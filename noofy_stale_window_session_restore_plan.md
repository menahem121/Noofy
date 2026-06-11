# Noofy Stale Window Recovery, Safe Session Restore, and Workflow Lease TTL

## Implementation Plan

This document is a standalone implementation plan for fixing stale Noofy frontend windows after close/relaunch, safely restoring user UI state, and preventing workflow-view lease leaks from keeping isolated runners warm indefinitely.

It is written for developers who may not have any prior conversation context.

---

## 1. Summary

Noofy currently has two related lifecycle problems:

1. In source/dev browser mode, closing or restarting Noofy can leave an old browser tab open. When Noofy is launched again, that old tab can reconnect to the new backend while still holding stale in-memory frontend state such as old job handles, old runner lease IDs, stale preparation UI, or stale memory blockers.
2. If a frontend window/tab disappears without explicitly closing its workflow-view lease, the backend can keep the workflow lease open forever inside that backend session. For isolated workflow runners, this can keep the runner warm indefinitely because closed-view release only happens after the backend sees the last workflow-view lease close.

The desired fix is not a full browser-session manager. The desired fix is:

- backend process exposes a unique session/boot identity;
- frontend detects backend session changes and performs a hard reload;
- frontend restores only safe UI state after reload;
- backend workflow-view leases have heartbeat and TTL;
- missing/unknown jobs after restart are shown as a calm restart recovery state, not as a failure or stale active run;
- packaged desktop close remains clean and verified;
- source/dev browser mode becomes self-healing when old tabs reconnect to a new backend.

Core rule:

> Restore safe UI state only. Never restore runtime truth unless the current backend confirms it.

---

## 2. Current Behavior and Verified Codebase Findings

These findings are based on the current Noofy repository structure and code paths. Developers should re-check exact line numbers because they may drift, but the architectural locations are accurate.

### 2.1 Packaged Tauri mode

Packaged Tauri mode is already mostly correct for app-owned windows and backend shutdown.

Relevant files:

- `frontend/src-tauri/src/main.rs`
- `backend/app/engine/factory.py`
- `backend/app/runtime/runners/runner_process.py`

Current behavior:

- Tauri creates one main webview window with ID `main`.
- The Tauri launcher starts the backend process and injects runtime config into the webview through `window.__NOOFY_RUNTIME_CONFIG__`.
- The injected config contains:
  - `apiBaseUrl`
  - `apiToken`
- The backend is launched with a fresh random API token and dynamic port (`--port 0`) per app launch.
- `CloseRequested` and `ExitRequested` both call `terminate_backend()`.
- On Unix, the backend is started in its own session/process group and shutdown sends SIGTERM, then SIGKILL after a timeout.
- On Windows, the launcher uses a Job Object with kill-on-close behavior for the backend process.
- Backend startup already performs stale runner PID cleanup through `RunnerProcessSupervisor.cleanup_stale_pid_files()`.

Conclusion:

- Packaged stale window recovery is generally not needed because the window is app-owned and should close with the app.
- A stale packaged webview would also hold a dead dynamic port and dead token, so it cannot safely reconnect to a new backend.
- Still verify packaged close behavior manually and keep tests/coverage around process ownership. Do not assume every child process always dies only because of the Tauri process group; backend shutdown and stale PID cleanup remain important safety nets.

### 2.2 Source/dev browser mode

Source/dev browser mode is the real stale-window scenario.

Relevant files:

- `scripts/noofy.py`
- `frontend/vite.config.ts`
- `backend/app/core/auth.py`
- `frontend/src/lib/api/client.ts`

Current behavior:

- `scripts/noofy.py run` starts:
  - backend on a fixed default port, currently `127.0.0.1:8765`;
  - Vite frontend on `127.0.0.1:5173`.
- The user opens the app in their own browser tab.
- Noofy does not own that browser tab and cannot close it when the launcher stops.
- Vite proxies `/api` requests to the backend port.
- In source/dev mode, unless `NOOFY_API_TOKEN` or another explicit token setting is configured, backend API auth is disabled.
- After relaunch, the old browser tab can silently reconnect to the new backend through the same Vite URL and backend port.
- The old tab keeps stale in-memory React state until it reloads.

Conclusion:

- Source/dev browser tabs need backend-session mismatch detection.
- When an old tab sees a different backend session, it should hard reload.
- After reload, it may restore safe UI state from localStorage/backend user-state, but all runtime state must be discarded.

### 2.3 Runtime status endpoint has no backend session identity

Relevant files:

- `backend/app/api/routes/runtime.py`
- `frontend/src/features/app/RuntimeStatusProvider.tsx`
- `frontend/src/lib/api/engine.ts`

Current behavior:

- `GET /api/runtime` returns the engine runtime status from `engine_service.runtime_status()`.
- The payload describes the ComfyUI/runtime engine state, not the backend process/session identity.
- The frontend polls runtime status in `RuntimeStatusProvider` every 2–10 seconds depending on state.
- There is no backend boot ID/session ID in the response.
- Therefore the frontend cannot distinguish:
  - same backend process recovered after a transient error;
  - completely new backend process after a restart.

Conclusion:

- Add backend session identity to the existing `/api/runtime` response.
- Do not add a new endpoint unless implementation proves `/runtime` cannot be safely extended.
- Do not modify the existing ComfyUI runtime status model if that model is reused elsewhere for engine-specific contracts. Prefer merging the backend session fields at the API route boundary.

### 2.4 Workflow-view leases have no heartbeat or TTL

Relevant files:

- `backend/app/api/routes/workflows.py`
- `backend/app/runtime/runners/supervisor.py`
- `backend/app/runtime/runners/lifecycle_service.py`
- `frontend/src/features/workflows/WorkflowRunPage.tsx`
- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/App.tsx`
- `frontend/src/lib/api/workflows.ts`

Current behavior:

- API supports:
  - `POST /api/workflows/{workflow_id}/runner/leases`
  - `DELETE /api/workflows/{workflow_id}/runner/leases/{lease_id}`
- `RunnerSupervisor` currently stores workflow leases as an in-memory mapping similar to:
  - `lease_id -> (workflow_id, runner_id)`
- There is no `opened_at` timestamp.
- There is no `last_heartbeat_at` timestamp.
- There is no TTL expiry.
- Closing a workflow tab calls backend lease close as a best effort.
- There is currently no reliable page-disappear handler such as `pagehide` to release leases when the browser tab/webview is killed or closed unexpectedly.
- Closed-view runner release only starts after the backend sees the last workflow-view lease close and the runner descriptor has `open_workflow_lease_count === 0`.

Conclusion:

- If a frontend disappears without sending DELETE, the backend session can keep the lease open indefinitely.
- For isolated runners, this can keep the runner warm forever within that backend process unless memory pressure cleanup or backend restart releases it.
- Add heartbeat and TTL on the backend. Frontend close events are best-effort only; backend TTL must be authoritative.

### 2.5 Jobs/progress/results are in-memory and become unknown after restart

Relevant files:

- `backend/app/engine/job_store.py`
- `backend/app/runs/job_service.py`
- `frontend/src/features/workflows/WorkflowRunPage.tsx`
- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/lib/api/jobs.ts`

Current behavior:

- JobStore is in-memory.
- Unknown job progress returns a `JobProgress` with `status: "unknown"`.
- Frontend `JobStatus` type includes `unknown`.
- Active tracked frontend run statuses are limited to statuses such as:
  - `queued`
  - `running`
  - `queued_pending_memory`
- Therefore, once an old job handle receives `unknown`, it should not remain active forever.
- However, the UI does not currently present this as a calm backend-restart recovery state.
- Users can still see confusing stale state before reload or after an old handle vanishes.

Important correction:

- Do not claim that unknown jobs poll forever after `unknown` is recorded. The real issue is not infinite polling; the real issue is unclear UX and stale runtime state surviving until reload.

Conclusion:

- Treat `unknown` progress for a previously active run as a vanished runtime handle.
- Clear live runtime state and show a calm message such as:
  - `The app restarted. Run this workflow again when ready.`
- Do not record it as a workflow failure.
- Do not open a scary failure modal.

### 2.6 Safe user state already exists in multiple places

Relevant files:

- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/lib/useWorkflowUserState.ts`
- backend workflow user-state APIs

Current behavior:

- Workflow tabs are already persisted in `localStorage` under a key like `noofy.workflowTabs.v1`.
- Runtime state for workflow tabs is not persisted; it lives in memory only.
- Workflow user values, layout overrides, presentation overrides, and output preferences are loaded/saved through workflow user-state APIs.

Conclusion:

- The app is already close to safe restore.
- Add route restoration and preserve the current existing user-state behavior.
- Do not persist runtime truth.

---

## 3. Product Goals

### 3.1 User-facing goals

When Noofy is closed and relaunched:

- Packaged app should close all app-owned windows and terminate owned backend/runtime processes.
- Source/dev old browser tabs should not remain stale or confusing.
- If an old page reconnects to a new backend session, it should reload automatically.
- After reload, the user should return to a safe, familiar UI state when possible:
  - same workflow tab open;
  - same selected workflow/page;
  - saved dashboard values restored;
  - saved layout/preferences restored.
- If an old run disappeared because the backend restarted, show a calm state:
  - `The app restarted. Run this workflow again when ready.`
- Avoid technical/scary popups for normal restart/reconnect cases.

### 3.2 Runtime goals

- Backend remains authoritative for runner lifecycle.
- Frontend may report view open/heartbeat/close, but frontend must not directly decide whether a runner process lives or dies.
- Workflow-view leases must expire automatically if the frontend disappears.
- Closed-view runner release should reuse the existing cooldown/release path.
- No stale lease should be able to keep an isolated runner warm forever.

### 3.3 Developer goals

- Keep the fix small and robust.
- Avoid building a full session manager.
- Avoid fragile reconciliation of old frontend runtime state with a new backend.
- Prefer hard reload on backend session mismatch.
- Add focused tests for each behavior.
- Keep packaged and source/dev behavior clear and documented.

---

## 4. Non-goals

This work should not:

- Restore old active jobs after backend restart unless the current backend confirms they still exist.
- Persist job truth to localStorage.
- Persist progress as authoritative state.
- Persist old backend token/session identity as usable runtime state.
- Persist runner lease IDs across page reloads.
- Preserve stale preparation dialogs or memory blockers across backend sessions.
- Build a full browser/session history manager.
- Attempt to close user-owned browser tabs in source/dev mode.
- Add a service worker or offline cache layer.
- Change frontend polling cadence except where needed for session mismatch handling.

---

## 5. Proposed Architecture

### 5.1 Backend session identity

Add a backend process/session identity generated once per backend process start.

Suggested new file:

- `backend/app/core/session.py`

Suggested contents:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime

BACKEND_SESSION_ID = f"bs-{uuid.uuid4().hex}"
BACKEND_SESSION_STARTED_AT = datetime.now(UTC).isoformat()


def backend_session_payload() -> dict[str, str]:
    return {
        "backend_session_id": BACKEND_SESSION_ID,
        "backend_session_started_at": BACKEND_SESSION_STARTED_AT,
    }
```

Design rules:

- Generate once per Python process.
- Do not persist it.
- A backend restart creates a new ID.
- This ID means all previous in-memory runtime state is void:
  - jobs;
  - queues;
  - runner lease ownership;
  - preparation state;
  - transient memory blockers.

Expose it through `GET /api/runtime`.

Current route:

- `backend/app/api/routes/runtime.py`

Proposed behavior:

```python
@router.get("/runtime")
async def runtime_status(engine_service: EngineServiceDep, response: Response):
    _set_dynamic_response_headers(response)
    status = await engine_service.runtime_status()
    return {**status.model_dump(mode="json"), **backend_session_payload()}
```

Adjust the implementation depending on the actual return type of `engine_service.runtime_status()`.

Important:

- Do not mutate the ComfyUI-specific runtime status model unless that is already the established API pattern.
- If `runtime_status()` already returns a plain dict in the current code, merge directly.
- Keep existing runtime fields unchanged.
- Keep no-store headers.

Frontend type update:

- `frontend/src/lib/api/engine.ts`

Add optional fields to `RuntimeStatus`:

```ts
backend_session_id?: string | null;
backend_session_started_at?: string | null;
```

Use optional fields so older backends or tests without the field do not break.

---

### 5.2 Frontend backend-session mismatch detection

Primary file:

- `frontend/src/features/app/RuntimeStatusProvider.tsx`

Use the existing runtime polling flow.

Behavior:

1. On first successful runtime response with `backend_session_id`, store it in an in-memory ref.
2. If a later successful runtime response has a different `backend_session_id`, treat it as a backend process restart.
3. Before reloading:
   - write a session restart marker to `sessionStorage`;
   - persist the new backend session ID for diagnostics/convenience;
   - hard reload the page.
4. The reload clears all in-memory runtime state.
5. Existing localStorage/backend user-state restores safe UI state.

Suggested keys:

- `localStorage["noofy.backendSession.v1"]`
- `sessionStorage["noofy.sessionRestart.v1"]`

Example restart marker:

```json
{
  "backendSessionId": "bs-...",
  "detectedAt": 1710000000000
}
```

Implementation details:

- Add an internal helper, for example `observeBackendSession(runtime)`.
- Call this helper from every successful runtime adoption path:
  - normal poll success;
  - `setRuntimeFromResponse` if it receives runtime payloads from actions.
- Add an injectable `reloadPage?: () => void` prop to `RuntimeStatusProvider` for tests.
- Default implementation: `window.location.reload()`.
- Use a ref guard so reload is triggered only once per page lifetime.

Reload conditions:

Only reload when all are true:

- runtime fetch succeeded;
- response contains a non-empty `backend_session_id`;
- this page has already confirmed a previous session ID in memory;
- the new session ID differs from the previous confirmed session ID;
- reload has not already been requested.

Do not reload when:

- first runtime response arrives after page load;
- runtime request fails or times out;
- backend is temporarily unreachable;
- backend does not include `backend_session_id`;
- same session ID repeats.

Why hard reload instead of in-place reconciliation:

- It is simpler and safer.
- It drops stale React state.
- It drops old job handles and runner lease IDs.
- It drops stale preparation/memory UI.
- It avoids a complex and fragile partial reset of many components.

---

### 5.3 Safe route restoration

Primary file:

- `frontend/src/App.tsx`

Current behavior:

- Route state is in-memory only.
- Workflow tabs are persisted separately.

Add route persistence for safe route shapes only.

Suggested key:

- `localStorage["noofy.appRoute.v1"]`

Safe route shapes:

```ts
type PersistedAppRoute =
  | { name: "home" }
  | { name: "workflows"; search?: string }
  | { name: "gallery" }
  | { name: "history" }
  | { name: "models" }
  | { name: "settings" }
  | { name: "workflow"; workflowId: string };
```

Do not persist:

- `dashboard-builder`
- `dashboard-builder-layout`
- any route carrying `initialSchema`
- modal/dialog state
- native workflow import state
- close confirmation state

Hydration behavior:

- On App initialization, read persisted route.
- If route is invalid, fall back to `{ name: "home" }`.
- If route is `workflow`, validate that the workflow ID exists in persisted workflow tabs.
- If workflow route points to a missing/closed tab, fall back to home or the most recently activated tab.

Save behavior:

- Persist route whenever route changes to a safe route.
- If route changes to an unsafe route, either keep the last safe route or store home. Prefer keeping the last safe route unless that causes confusing back-navigation behavior.

User result:

- After backend session mismatch and hard reload, the user lands back on the same safe page/workflow when possible.

---

### 5.4 Safe UI state restoration

Already safe to restore:

- Open workflow tabs from `localStorage["noofy.workflowTabs.v1"]`.
- Workflow user values from backend user-state.
- Layout overrides from backend user-state.
- Presentation overrides from backend user-state.
- Output preferences from backend user-state.
- App route from the new route-persistence work.

Never restore:

- active job truth;
- job progress as truth;
- queue IDs as active handles;
- backend token/session identity as runtime truth;
- runner lease IDs;
- runner IDs;
- preparation blockers/dialogs;
- memory blockers/dialogs;
- active run submission state;
- live preview state.

Implementation rule:

- Keep `WorkflowTabRuntimeState` memory-only.
- Do not add it to localStorage.
- Keep the existing `runtimeByWorkflowId` initialization empty after reload.

---

### 5.5 Calm restart recovery notice

Primary files:

- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/features/workflows/WorkflowRunPage.tsx`
- `frontend/src/features/app/RuntimeStatusProvider.tsx`

Goal:

When a backend restart causes a previously active workflow run to vanish, the user should see a calm inline message, not a scary failure.

Suggested user text:

> The app restarted. Run this workflow again when ready.

Suggested keys:

- `sessionStorage["noofy.sessionRestart.v1"]`
- `sessionStorage["noofy.activeRunWorkflows.v1"]`

Active-run workflow marker:

- In `WorkflowTabsProvider`, mirror workflow IDs with active runtime handles into sessionStorage.
- Only store workflow IDs, not job IDs or queue IDs.
- Example:

```json
{
  "workflowIds": ["workflow-a", "workflow-b"],
  "updatedAt": 1710000000000
}
```

Notice display behavior:

- After reload, `WorkflowRunPage` checks:
  - there is a recent session restart marker;
  - this workflow ID was listed in active-run workflow marker;
  - notice has not been dismissed for this workflow/session.
- Display an inline compact notice near the run panel/top notices.
- Do not show a modal/toast.
- Allow dismissal.

Unknown progress behavior:

If a tracked run polls and receives `status: "unknown"`:

- treat it as a vanished runtime handle;
- clear active runtime state for that workflow;
- clear live preview for that handle;
- do not call `recordTrackedFailure`;
- do not open failure dialog;
- optionally mark the tracked run as terminal/vanished internally so queue advancement can continue;
- show the same calm restart/recovery notice.

Important:

- Unknown progress does not necessarily mean a backend restart in every possible case, but from the user's perspective it means the previous run handle no longer exists. The message should be calm and action-oriented.

Potential copy options:

Preferred:

> The app restarted. Run this workflow again when ready.

Alternative if not sure it was a full app restart:

> This run is no longer active. Run this workflow again when ready.

Use the more precise message only when a session restart marker exists. Otherwise use the more general message.

---

### 5.6 Workflow-view lease heartbeat and TTL

Primary backend files:

- `backend/app/runtime/runners/supervisor.py`
- `backend/app/runtime/runners/lifecycle_service.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/core/config.py`

Primary frontend files:

- `frontend/src/lib/api/workflows.ts`
- `frontend/src/lib/api/client.ts`
- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/features/workflows/WorkflowRunPage.tsx`

#### 5.6.1 Backend lease data model

Replace the current tuple lease record with an explicit record.

Suggested model:

```python
class WorkflowLeaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    runner_id: str
    opened_at: datetime
    last_heartbeat_at: datetime
```

Current mapping:

```python
self._workflow_leases: dict[str, tuple[str, str]]
```

New mapping:

```python
self._workflow_leases: dict[str, WorkflowLeaseRecord]
```

Rules:

- `opened_at` and `last_heartbeat_at` use the supervisor's injected clock (`self._now`) to keep tests deterministic.
- Opening a lease sets both timestamps to now.
- Heartbeat updates only `last_heartbeat_at`.
- Closing removes the lease.
- Expiring stale leases removes old records using the same state transition as close.

Audit all `_workflow_leases` usages.

Known categories to update:

- lease open;
- lease close;
- runner unbind cleanup;
- descriptor lease count updates;
- `open_workflow_lease_ids` recomputation;
- tests constructing supervisor lease internals directly.

#### 5.6.2 Supervisor methods

Add methods to `RunnerSupervisor`.

Suggested signatures:

```python
def heartbeat_workflow_lease(
    self,
    lease_id: str,
    *,
    workflow_id: str | None = None,
) -> RunnerDescriptor | None:
    ...
```

```python
def expire_stale_workflow_leases(
    self,
    ttl_seconds: float,
) -> list[tuple[str, RunnerDescriptor]]:
    ...
```

Heartbeat behavior:

- If lease does not exist, return `None`.
- If `workflow_id` is provided and does not match the lease record, return `None`.
- If valid, update `last_heartbeat_at` to now and return the current runner descriptor.
- Do not change `open_workflow_lease_count` on heartbeat.
- Do not change cooldown on heartbeat.

Expire behavior:

- Calculate cutoff: `now - ttl_seconds`.
- Find all leases with `last_heartbeat_at < cutoff`.
- For each expired lease, remove it using shared close/update logic.
- Return the affected lease IDs and updated descriptors.
- If the last lease for a runner expires, descriptor should transition exactly like explicit close:
  - `open_workflow_lease_count` becomes 0;
  - `open_workflow_lease_ids` becomes empty;
  - `closed_view_cooldown_expires_at` is set;
  - if status was `idle_warm`, it can become `idle` according to current close behavior.

Implementation recommendation:

- Avoid duplicating close logic. Factor a private locked helper if useful.
- Do not call async lifecycle code while holding the supervisor lock.

#### 5.6.3 Backend heartbeat API

Add route beside existing open/close lease routes:

```http
PUT /api/workflows/{workflow_id}/runner/leases/{lease_id}/heartbeat
```

Suggested response shape:

Active:

```json
{
  "workflow_id": "...",
  "status": "active",
  "lease_id": "...",
  "runner": { ... }
}
```

Not found:

```json
{
  "workflow_id": "...",
  "status": "lease_not_found",
  "lease_id": "...",
  "runner": null
}
```

HTTP status:

- Return HTTP 200 for `lease_not_found`.
- This lets stale frontend state self-heal without surfacing as a hard API error.
- Keep normal workflow ID validation behavior consistent with existing lease close/open routes.

Auth:

- Use normal Bearer auth through existing API middleware.
- Do not modify auth middleware for heartbeat.

Logging:

- Active heartbeat should not log every request at info level; that would be noisy.
- Unknown heartbeat should be logged at most debug/info with dedup/rate-limit, or not logged unless diagnostics require it.
- Avoid a warning every 25 seconds from stale tabs.

#### 5.6.4 Lifecycle sweeper

Primary file:

- `backend/app/runtime/runners/lifecycle_service.py`

Add an async sweeper task that expires stale workflow-view leases.

Suggested config:

- `NOOFY_WORKFLOW_LEASE_TTL_SECONDS`, default `120`
- `NOOFY_WORKFLOW_LEASE_SWEEP_INTERVAL_SECONDS`, default `20`

Primary settings file:

- `backend/app/core/config.py`

Suggested behavior:

- The sweeper starts lazily when the first workflow lease is opened.
- Every sweep interval:
  - call `runner_supervisor.expire_stale_workflow_leases(ttl_seconds)`;
  - for each expired lease, log a concise diagnostic event;
  - call `_maybe_schedule_closed_view_release(updated_descriptor)` for affected runners.
- The sweeper can exit when no leases remain, or it can remain alive until service shutdown. Prefer exit-when-empty if simple and testable.
- Cancel sweeper task in existing `WorkflowRunnerLifecycleService.shutdown()`.

Diagnostic event:

Suggested message:

> Workflow view lease expired without heartbeat

Suggested details:

```json
{
  "workflow_id": "...",
  "runner_id": "...",
  "lease_id": "...",
  "ttl_seconds": 120,
  "open_workflow_lease_count": 0,
  "closed_view_cooldown_expires_at": "..."
}
```

Important:

- Lease expiry does not immediately kill the runner.
- It should start the existing closed-view cooldown/release path.
- Backend remains authoritative over whether/when the runner can be released.

#### 5.6.5 Frontend heartbeat

Primary file:

- `frontend/src/features/app/WorkflowTabs.tsx`

Why provider-level heartbeat:

- A workflow lease is stored in workflow tab runtime state.
- It should outlive the `WorkflowRunPage` component as long as the workflow tab remains open.
- Therefore heartbeat belongs in `WorkflowTabsProvider`, not inside `WorkflowRunPage`.

Add API client:

- `frontend/src/lib/api/workflows.ts`

```ts
export function heartbeatWorkflowRunnerLease(workflowId: string, leaseId: string) {
  return putJson<WorkflowRunnerLeaseResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/runner/leases/${encodeURIComponent(leaseId)}/heartbeat`,
    {},
  );
}
```

Heartbeat behavior:

- Every 25 seconds, send heartbeat for every workflow runtime entry with `runnerLeaseId`.
- If response status is `active`, optionally refresh `runnerId` from response.
- If response status is `lease_not_found`, clear `runnerLeaseId` and `runnerId` for that workflow.
- Ignore transient network errors. TTL is the authority.
- Do not aggressively reopen leases from the heartbeat loop.
- Let existing WorkflowRunPage reacquire logic open a new lease when appropriate.

Suggested constants:

- frontend heartbeat interval: `25_000ms`
- backend TTL: `120s`
- backend sweep: `20s`

Rationale:

- Allows roughly four missed heartbeats before expiry.
- Laptop sleep or short frontend pauses should not immediately release runners.
- Worst-case over-retention after a tab dies is roughly TTL + sweep + closed-view cooldown.

#### 5.6.6 Best-effort pagehide lease release

Add best-effort frontend release on page disappearance.

Primary files:

- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/lib/api/workflows.ts`
- `frontend/src/lib/api/client.ts`

Use `pagehide`, not only `beforeunload`.

Why:

- `pagehide` is more reliable for browser/webview teardown and bfcache-related navigation.
- `beforeunload` is more restricted and can be unreliable.

Use `fetch` with `keepalive: true`, not `sendBeacon`.

Why:

- `sendBeacon` cannot set the Bearer Authorization header.
- Current backend query-token whitelist is GET-only for selected asset/job routes.
- DELETE lease close needs normal auth in packaged mode.

Suggested helper:

```ts
export function closeWorkflowRunnerLeaseKeepalive(workflowId: string, leaseId: string) {
  void fetch(
    `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/runner/leases/${encodeURIComponent(leaseId)}`,
    {
      method: "DELETE",
      headers: apiHeaders(),
      keepalive: true,
    },
  ).catch(() => undefined);
}
```

Rules:

- Best effort only.
- Do not block unload.
- TTL remains the real safety net.
- Do not show UI errors from pagehide cleanup.

---

## 6. File-by-file Implementation Guide

This section gives likely locations. Developers should inspect current code before editing.

### 6.1 Backend session identity

Add:

- `backend/app/core/session.py`

Modify:

- `backend/app/api/routes/runtime.py`

Potential tests:

- `backend/tests/api/test_runtime.py`
- or existing runtime API test file.

### 6.2 Frontend runtime session detection

Modify:

- `frontend/src/lib/api/engine.ts`
- `frontend/src/features/app/RuntimeStatusProvider.tsx`

Potential tests:

- `frontend/src/features/app/RuntimeStatusProvider.test.tsx`

Add test-only injection:

- `reloadPage?: () => void`

### 6.3 Route persistence

Modify:

- `frontend/src/App.tsx`

Potential tests:

- `frontend/src/App.test.tsx`

### 6.4 Active-run restart notice

Modify:

- `frontend/src/features/app/WorkflowTabs.tsx`
- `frontend/src/features/workflows/WorkflowRunPage.tsx`

Potential tests:

- `frontend/src/features/app/WorkflowTabs.test.tsx`
- `frontend/src/features/workflows/WorkflowRunPage.test.tsx`

### 6.5 Lease heartbeat/TTL backend

Modify:

- `backend/app/runtime/runners/supervisor.py`
- `backend/app/runtime/runners/lifecycle_service.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/core/config.py`

Potential tests:

- `backend/tests/runtime/runners/test_runner_supervisor.py`
- `backend/tests/runtime/runners/test_closed_view_release.py`
- `backend/tests/api/test_workflows.py`

### 6.6 Lease heartbeat/pagehide frontend

Modify:

- `frontend/src/lib/api/workflows.ts`
- `frontend/src/lib/api/client.ts` if shared keepalive helper is desired
- `frontend/src/features/app/WorkflowTabs.tsx`

Potential tests:

- `frontend/src/features/app/WorkflowTabs.test.tsx`

---

## 7. Implementation Phases

Each phase should be independently reviewable. Avoid bundling all changes into one untestable patch.

### Phase 1 — Backend session identity

Scope:

- Add backend session module.
- Add `backend_session_id` and `backend_session_started_at` to `/api/runtime`.
- Add frontend `RuntimeStatus` optional fields.
- Add backend API tests.

Acceptance for phase:

- `/api/runtime` includes a stable backend session ID within a single backend process.
- Restarting backend produces a different session ID.
- Existing runtime fields are unchanged.
- Existing runtime consumers still pass.

### Phase 2 — Frontend session mismatch reload

Scope:

- Add session observer to `RuntimeStatusProvider`.
- On changed backend session ID, write session restart marker and hard reload.
- Add reload injection for tests.
- Add tests for adoption, same session, changed session, missing field, failures, and double-fire protection.

Acceptance for phase:

- First runtime response never reloads.
- Same session never reloads.
- Changed session reloads exactly once.
- Missing session field is ignored.
- Transient unreachable backend does not reload.
- No reload loop occurs.

### Phase 3 — Safe route restoration

Scope:

- Persist safe route shapes.
- Hydrate initial route from localStorage.
- Validate workflow route against persisted workflow tabs.
- Avoid persisting dashboard-builder routes and schema-carrying routes.
- Add App tests.

Acceptance for phase:

- Relaunch/reload restores home/workflows/gallery/history/models/settings routes.
- Workflow route restores only when matching workflow tab exists.
- Invalid route falls back safely.
- Dashboard builder routes do not restore with stale component state.

### Phase 4 — Calm vanished-run recovery

Scope:

- Mirror active-run workflow IDs to sessionStorage.
- Use restart marker + active-run marker to show inline notice after reload.
- Treat `unknown` progress for previously active tracked runs as vanished runtime handle.
- Clear active runtime state and live preview without failure modal.
- Add WorkflowRunPage tests.

Acceptance for phase:

- After backend session reload, a workflow that had an active run shows a calm notice.
- Unknown progress does not open failure dialog.
- Unknown progress clears active job state.
- Unknown progress does not remain a stale active run.
- Batch/queued runs advance or clear cleanly.

### Phase 5 — Backend lease heartbeat and TTL

Scope:

- Replace tuple lease records with explicit `WorkflowLeaseRecord`.
- Add heartbeat method.
- Add expiry method.
- Add heartbeat API route.
- Add TTL/sweep settings.
- Add lifecycle sweeper that calls expiry and then existing closed-view release scheduling.
- Add backend tests.

Acceptance for phase:

- Opening a lease records timestamps.
- Heartbeat refreshes `last_heartbeat_at`.
- Stale leases expire after TTL.
- Fresh leases do not expire.
- Expiring last lease starts closed-view cooldown.
- Closed-view release path remains backend-authoritative.
- Sweeper is cancelled on shutdown.
- Unknown heartbeat returns `lease_not_found` without hard API failure.

### Phase 6 — Frontend lease heartbeat and pagehide release

Scope:

- Add heartbeat API client.
- Add provider-level heartbeat loop in `WorkflowTabsProvider`.
- Clear lease state on `lease_not_found`.
- Add `pagehide` keepalive DELETE for held leases.
- Add frontend tests with fake timers and pagehide dispatch.

Acceptance for phase:

- Heartbeat is sent every 25 seconds for each held lease.
- No heartbeat is sent for workflows without lease IDs.
- `lease_not_found` clears stale lease state.
- Network errors do not thrash UI.
- Pagehide sends keepalive DELETE for every held lease.
- TTL still handles missed pagehide cleanup.

### Phase 7 — Polish, diagnostics, and manual verification

Scope:

- Ensure restart notice copy is calm and non-technical.
- Ensure diagnostics are useful but not noisy.
- Verify packaged app close behavior.
- Verify dev/browser stale-tab recovery.
- Run full test suites.

Acceptance for phase:

- No scary popup for normal restart recovery.
- Diagnostics show lease expiry when TTL cleanup happens.
- No heartbeat log spam.
- Packaged close/relaunch works.
- Dev old tab auto-reloads and restores safe UI state.

---

## 8. Test Plan

### 8.1 Backend tests

#### Runtime session API

Test cases:

1. `GET /api/runtime` includes:
   - `backend_session_id`
   - `backend_session_started_at`
2. Session ID is non-empty and stable across multiple runtime calls within one process.
3. Existing runtime fields remain present and unchanged.
4. Response still has no-store dynamic headers.

#### RunnerSupervisor lease records

Test cases:

1. Opening a workflow lease:
   - creates a lease record;
   - sets `opened_at`;
   - sets `last_heartbeat_at`;
   - updates descriptor lease count and IDs.
2. Heartbeat:
   - refreshes `last_heartbeat_at`;
   - returns descriptor;
   - does not alter lease count.
3. Heartbeat with wrong workflow ID:
   - returns None or `lease_not_found` at service layer;
   - does not update timestamp.
4. Heartbeat with unknown lease:
   - returns None.
5. Expiry:
   - expires only leases older than TTL;
   - preserves fresh/heartbeated leases;
   - updates descriptor lease counts.
6. Expiring last lease:
   - sets `closed_view_cooldown_expires_at`;
   - allows existing closed-view release scheduling.
7. Unbind runner:
   - removes lease records for that runner with the new record shape.

Use injected fake clock (`self._now`) where current supervisor tests already do.

#### Lifecycle sweeper

Test cases:

1. Sweeper expires stale lease and schedules closed-view release.
2. Heartbeat prevents expiry.
3. Multiple leases on one runner:
   - expiring one lease does not start cooldown if another lease remains.
4. Last lease expiry starts cooldown.
5. Sweeper task is cancelled during service shutdown.
6. Expiry diagnostic is logged once per expired lease, not repeatedly.

#### Heartbeat API route

Test cases:

1. Active lease heartbeat returns HTTP 200 and status `active`.
2. Unknown lease heartbeat returns HTTP 200 and status `lease_not_found`.
3. Wrong workflow/lease combination returns `lease_not_found`.
4. Unknown workflow still follows existing workflow validation behavior.
5. Auth behavior remains consistent with other workflow API routes.

### 8.2 Frontend tests

#### RuntimeStatusProvider

Test cases:

1. First runtime response with session ID adopts without reload.
2. Same session repeated does not reload.
3. Different session after first adoption triggers reload exactly once.
4. Changed session writes restart marker to sessionStorage.
5. Changed session persists new backend session ID to localStorage.
6. Missing `backend_session_id` no-ops.
7. Runtime fetch failure does not reload.
8. `setRuntimeFromResponse` path also observes session identity.
9. Double successful responses with changed session do not double reload.

#### App route persistence

Test cases:

1. Navigating to safe route persists route.
2. Initial render hydrates safe persisted route.
3. Workflow route restores only if workflow tab exists.
4. Workflow route falls back when tab is missing.
5. Dashboard builder routes are not persisted/restored.
6. Malformed localStorage falls back to home.

#### WorkflowTabsProvider heartbeat

Use fake timers.

Test cases:

1. One held lease sends heartbeat every 25 seconds.
2. Multiple held leases each send heartbeat.
3. No heartbeat for workflow runtime entries without `runnerLeaseId`.
4. `lease_not_found` clears `runnerLeaseId` and `runnerId`.
5. Heartbeat network error does not clear lease immediately.
6. `pagehide` event sends keepalive DELETE for every held lease.
7. Provider records active-run workflow IDs in sessionStorage without job IDs.

#### WorkflowRunPage restart/unknown recovery

Test cases:

1. Restart marker + active-run marker shows calm notice on mount for that workflow.
2. Notice is not shown for unrelated workflow.
3. Notice can be dismissed.
4. `unknown` progress for active tracked run:
   - clears active runtime handle;
   - clears live preview for that handle;
   - does not open failure dialog;
   - does not record failed tracked run;
   - shows calm vanished/restart notice.
5. Batch run queue advances or clears cleanly after vanished handle.

### 8.3 Manual verification

#### Source/dev mode

Steps:

1. Run Noofy through source/dev launcher.
2. Open browser tab at `http://127.0.0.1:5173`.
3. Open a workflow tab.
4. Start a run or get into a visible run/preparation state.
5. Stop Noofy launcher/backend.
6. Restart Noofy launcher/backend.
7. Return to the old browser tab.

Expected:

- Old tab detects the new backend session within one runtime poll interval.
- Page hard reloads automatically.
- Same safe route/workflow tab is restored if possible.
- Dashboard values/preferences remain intact.
- Old active run is not shown as still running.
- Calm restart notice appears if the workflow had active work.
- No stale preparation or memory blocker remains visible.

#### Lease expiry after killed tab

Steps:

1. Run Noofy.
2. Open workflow tab that acquires a workflow runner lease.
3. Kill the browser tab/window without clicking close.
4. Keep backend alive.
5. Wait for TTL + sweep interval.
6. Observe diagnostics and runner state.

Expected:

- Lease expires.
- Diagnostic shows lease expired without heartbeat.
- Runner descriptor lease count drops.
- Existing closed-view cooldown path starts.
- Isolated runner releases when safe after cooldown.

#### Graceful close

Steps:

1. Open workflow tab with lease.
2. Close workflow tab normally.

Expected:

- DELETE lease close is sent.
- Lease count drops immediately.
- Closed-view cooldown starts if it was the last lease.

#### Pagehide best effort

Steps:

1. Open workflow tab with lease.
2. Close browser tab/window or reload page.
3. Observe network/backend logs where practical.

Expected:

- keepalive DELETE is attempted.
- If it succeeds, lease closes immediately.
- If it fails, TTL expiry still cleans up later.

#### Packaged mode

Steps:

1. Build/run packaged app.
2. Open workflow tab.
3. Start/prepare workflow if needed.
4. Close app window.
5. Check backend, runner, and ComfyUI processes.
6. Relaunch app.
7. Repeat 3 times.

Expected:

- App-owned webview closes.
- Backend process tree terminates.
- No stale runner/ComfyUI processes accumulate.
- Relaunch opens clean app state.
- Safe tabs/route/user-state restore as intended.

---

## 9. Acceptance Criteria

The implementation is complete only when all of these are true.

### Session/reload

- Backend exposes a unique `backend_session_id` per backend process.
- Frontend adopts first session ID without reload.
- Frontend hard reloads exactly once when a different backend session is detected.
- No reload occurs on transient backend unavailability.
- No reload loop is possible.
- Source/dev stale browser tab auto-recovers after backend restart.

### Safe restore

- Open workflow tabs restore after reload/relaunch.
- Last safe route restores after reload/relaunch.
- Workflow route restores only when the workflow tab exists.
- Dashboard values/layout/preferences restore through existing user-state.
- Dashboard builder routes are not restored with stale transient state.

### Runtime state safety

- Active job truth is not restored from localStorage/sessionStorage.
- Progress is not restored as authoritative state.
- Runner lease IDs are not persisted across reload.
- Old backend token/session identity is not reused as runtime truth.
- Stale preparation/memory blockers disappear after reload.

### Vanished run UX

- Old/unknown job handles do not show as active after backend restart.
- Unknown progress for a previously active handle is not treated as workflow failure.
- User sees a calm inline recovery notice.
- No scary modal/toast appears for normal restart recovery.

### Lease lifecycle

- Held workflow leases receive heartbeat while frontend tab is alive.
- Stale leases expire after TTL.
- Expiring the last lease starts existing closed-view cooldown/release flow.
- Graceful close releases immediately.
- Pagehide attempts best-effort keepalive release.
- TTL cleanup works even when pagehide release fails.
- No stale lease can keep an isolated runner warm forever within one backend session.

### Process lifecycle

- Packaged close still terminates app-owned backend/runtime process tree.
- Existing stale runner PID cleanup still works.
- Repeated launch/close cycles do not accumulate stale runners, leases, or backend processes.

### Tests/verification

- Focused backend tests pass.
- Focused frontend tests pass.
- Full relevant backend test suite passes.
- Full relevant frontend test suite/typecheck passes.
- Manual dev stale-tab scenario is verified.
- Manual packaged close/relaunch scenario is verified.

---

## 10. Risks and Tradeoffs

### 10.1 Laptop sleep / suspended tabs

Risk:

- Browser timers may pause during laptop sleep.
- Heartbeats may stop long enough for the backend TTL to expire a lease.
- Runner may release after cooldown.

Decision:

- Accept this.
- Backend memory authority is more important than preserving warm runners forever.
- On wake, frontend can reacquire lease when the workflow page is active again.
- Next run may need to warm models again.

### 10.2 Multiple dev browser tabs

Risk:

- Multiple tabs can be open for the same app.
- Route persistence can be last-writer-wins.
- Multiple tabs may hold independent leases.

Decision:

- Accept independent leases.
- Last-writer-wins route persistence is acceptable for dev/source mode.
- Backend TTL prevents permanent leaks.

### 10.3 Backend crash loops

Risk:

- If backend restarts repeatedly while a tab is open, frontend may reload once per successful new session detection.

Mitigation:

- Reload guard prevents multiple reloads per page lifetime.
- Optionally rate-limit reloads with restart marker timestamp if crash loops become annoying.
- Do not add rate limiting unless tests/manual verification show a real problem.

### 10.4 Heartbeat noise

Risk:

- Logging every heartbeat or every unknown stale heartbeat can spam diagnostics.

Decision:

- Do not log successful heartbeats at info level.
- Log expiry events.
- Unknown heartbeat should be low-noise or rate-limited.

### 10.5 Keepalive limitations

Risk:

- `fetch(..., keepalive: true)` is best-effort and can be dropped by the browser/webview.

Decision:

- Accept this.
- TTL expiry is the authoritative cleanup.
- keepalive is only an optimization for graceful page disappearance.

### 10.6 Packaged vs source/dev behavior

Risk:

- Packaged app and source/dev browser mode have different ownership models.

Decision:

- Document the difference clearly.
- Do not try to make source/dev browser tabs app-owned.
- Do not add packaged-specific stale webview recovery unless a real packaged stale-window bug is reproduced.

### 10.7 Overengineering

Risk:

- This can grow into a complex session restore system.

Decision:

- Keep solution scoped:
  - backend session ID;
  - hard reload on mismatch;
  - safe route/tabs restore;
  - lease heartbeat/TTL;
  - calm vanished-run UX.
- Do not build a full browser session manager.

---

## 11. Open Questions

These should be decided during implementation if not already obvious from current code.

1. Exact TTL values:
   - Proposed: heartbeat every 25s, TTL 120s, sweep every 20s.
   - Confirm whether this should align with `closed_view_cooldown_seconds`.
2. Restart notice wording:
   - Use `The app restarted. Run this workflow again when ready.` when session restart is confirmed.
   - Use `This run is no longer active. Run this workflow again when ready.` for generic unknown progress without restart marker.
3. Route fallback:
   - If persisted workflow route points to a missing tab, should fallback be home or most recent valid tab?
   - Recommendation: most recent valid tab if available, otherwise home.
4. Dashboard builder route:
   - Recommendation: do not restore builder routes for now because they carry transient schema/component state.
5. Unknown heartbeat logging:
   - Recommendation: avoid warning spam; return `lease_not_found` and let frontend self-heal.
6. Session ID naming:
   - Use `backend_session_id` in API payload.
   - Use `bs-<uuid>` as readable format.

---

## 12. Developer Handoff Checklist

Before coding:

- Re-check current file paths and exact code structure.
- Confirm existing tests and naming conventions.
- Confirm whether `engine_service.runtime_status()` returns a Pydantic model or dict.
- Confirm current frontend test patterns for `RuntimeStatusProvider`, `WorkflowTabs`, `WorkflowRunPage`, and `App`.

During coding:

- Keep phases small.
- Add tests with each phase.
- Avoid broad refactors unrelated to lifecycle/session/lease behavior.
- Avoid persisting runtime truth.
- Avoid noisy logs.

Before commit:

- Run backend focused tests.
- Run frontend focused tests.
- Run backend suite or relevant large subset.
- Run frontend typecheck.
- Run frontend test suite or relevant large subset.
- Run formatting/lint checks used by the repo.
- Manually verify source/dev stale-tab recovery.
- Manually verify packaged close/relaunch behavior if packaging environment is available.

Final implementation report should include:

- Root cause confirmed.
- Files changed.
- API/data model changes.
- Tests added/updated.
- Verification commands run.
- Manual verification performed.
- Remaining risks.
- Whether it is ready to commit.

---

## 13. Suggested Final Dev Prompt

Use this if assigning the task to an implementation agent:

```text
Please implement the Noofy stale window recovery, safe session restore, and workflow-view lease TTL plan from this Markdown file.

Follow the phases in order:
1. Backend session identity in /api/runtime.
2. Frontend backend-session mismatch detection with hard reload.
3. Safe route restoration.
4. Calm vanished-run/restart recovery UX.
5. Backend workflow-view lease heartbeat + TTL expiry.
6. Frontend lease heartbeat + pagehide keepalive release.
7. Tests, diagnostics, and verification.

Keep the core rule: restore safe UI state only; never restore runtime truth unless the current backend confirms it.

Do not overbuild a full browser-session manager. Keep the backend authoritative for runner lifetime.

After implementation, report what changed, tests added, verification run, remaining risks, and whether it is ready to commit.
```
