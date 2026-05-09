# User-Managed ComfyUI Updates

Noofy can update its managed ComfyUI sidecar independently of Noofy app releases.

The updater queries stable upstream releases from `https://github.com/Comfy-Org/ComfyUI`
only after the user presses `Check for Updates` or starts a manual update. The
settings page does not contact GitHub during passive app or settings startup.
After an upstream check, the settings dropdown defaults to `Latest version`,
which means the newest stable upstream release. Older choices also come directly
from upstream GitHub releases.

Noofy does not label an upstream release as compatible before local validation.
Each selected version is installed into Noofy-managed runtime storage, smoke
tested, and activated only if validation passes.

Status: the managed update and automatic repair implementation is complete for
the current Noofy architecture and covered by the repo test suite.

## Storage Contract

- `third_party/comfyui/` remains the bundled fallback and packaging input.
- Self-updated ComfyUI sources live under `runtime-store/core-engines/`.
- Per-version ComfyUI environments live under `runtime-store/core-envs/`.
- Active version metadata is stored in
  `runtime-store/core-engines/active-comfyui.json`.
- Local validation metadata is stored in
  `runtime-store/core-engines/local-validation.json`.
- Failed update transactions remain quarantined under `runtime-store/transactions/`.

The updater must never mutate `third_party/comfyui/`.

## Activation Policy

Updates are available only in managed mode. They are disabled when developer
overrides such as `COMFYUI_REPO_DIR` or `COMFYUI_PYTHON_EXECUTABLE` are active.

Activation is transactional:

1. Resolve the selected upstream release.
2. Download and extract the release archive into staging.
3. Hash the source tree and copy it into `runtime-store/core-engines/`.
4. Create a fresh per-version venv under `runtime-store/core-envs/`.
5. Run startup, API route, workflow, WebSocket, and path-isolation smoke checks.
6. Stop the current managed sidecar if needed.
7. Atomically update active metadata.

If validation fails, Noofy keeps the current working ComfyUI unchanged and records
the selected version as `Failed validation`.

## Automatic Repair

Noofy can automatically repair an installed managed ComfyUI runtime when an
explicit engine start fails because the source or Python environment is missing,
incomplete, or broken. Repair does not run during passive status refreshes.

Repair follows the same safety policy as updates:

1. Detect a real managed start failure such as `environment_not_ready`,
   `repo_missing`, or an environment-like `startup_failed`.
2. Check the bounded repair policy. Noofy tries at most two automatic repairs for
   the same version/source hash within 24 hours.
3. Reuse the existing source only if it still matches the recorded source hash.
4. If the source is missing or corrupt, redownload the recorded upstream
   artifact, preferring the recorded commit/hash metadata when available.
5. Rebuild a fresh environment in transaction staging.
6. Run the required Noofy smoke checks.
7. Activate only after validation passes.

Noofy never runs pip repair inside the existing active environment. It builds a
fresh staged environment first. A known-working active environment is not deleted
before validation. If the active environment is already unusable, Noofy may
replace that broken environment after the staged rebuild passes validation, while
preserving `previous_active` or bundled fallback recovery.

Repair failures are classified separately from compatibility failures:

- `repair_failed`: download, extraction, hash verification, dependency install,
  or environment creation failed.
- `validation_failed`: smoke validation could not complete for a non-API reason.
- `startup_failed`: the repaired runtime still could not start.
- `incompatible`: source/env repair succeeded, but required Noofy behavior such
  as routes, WebSocket progress, prompt execution, or path isolation failed.

When repair fails, Noofy keeps the current active metadata unchanged where
possible. If a previous locally verified runtime is recorded, Noofy tries to
start that fallback. If not, it attempts the bundled `third_party/comfyui`
runtime. The bundled source remains immutable and is never self-updated.

## Manual Environment Rebuild

Settings exposes `Rebuild Environment` for managed ComfyUI versions. This is a
manual repair action for dependency drift or local environment corruption when a
full ComfyUI version update is not needed.

The rebuild action:

1. Selects the current installed version by default.
2. Reuses the same source verification and redownload rules as automatic repair.
3. Builds a fresh staged environment.
4. Runs the same Noofy smoke validation.
5. Activates only after validation passes.
6. Clears the automatic repair block for the version after a successful rebuild.

The backend endpoint is `POST /api/engine/comfyui/rebuild`. Progress is reported
through the existing `GET /api/engine/comfyui/update/status` job status surface
with `operation: "rebuild"`.
