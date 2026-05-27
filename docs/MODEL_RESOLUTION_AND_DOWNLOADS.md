# Model Resolution And Downloads

Status: current architecture/reference.

This doc describes how Noofy locates required workflow models on the user's
machine, resolves missing ones through external model providers, and downloads
them safely. It covers the model folder layout, the API key surface for
provider authentication, the staged workflow import preview, and the background
download job that drives the import progress UI.

## Model Folder Layout

The Noofy backend resolves a beginner-friendly default model folder and creates
ComfyUI-style category subfolders inside it.

- Default path: `~/Documents/Noofy Models`.
- Fallback: when `Documents` cannot be resolved, the backend uses
  `<data_dir>/Noofy Models`.
- The folder must not be inside `third_party/comfyui/` and is rejected when it
  resolves there.
- Subfolders are the standard ComfyUI categories such as `checkpoints`, `loras`,
  `vae`, `clip`, `controlnet`, `upscale_models`, etc. The full list lives in
  [backend/app/models/folders.py](../backend/app/models/folders.py).

Users can also connect an existing ComfyUI `models/` folder for read/reuse only.
Noofy treats that folder as a user-owned secondary root for availability checks.

Invariants:

- Noofy-owned downloads always go to the configured Noofy Models folder.
- User-selected imports are copied into the configured Noofy Models folder. The
  source file is not modified.
- Noofy never writes models into the external ComfyUI folder or into
  `third_party/comfyui/`.
- Delete actions are allowed only for files that resolve inside the configured
  Noofy Models folder and are recorded as Noofy-imported or Noofy-downloaded.
  Arbitrary user-owned files that the user placed in Noofy Models are reusable
  by Noofy but are not deletable from the Models page. External ComfyUI folder
  files and engine-visible references are never deleted by the Models page.
- The managed ComfyUI sidecar sees the Noofy Models folder (and the optional
  external ComfyUI folder) through a generated `extra-model-paths.yaml` under
  the runtime store. Noofy Models is registered as the default category root.

### Settings API

- `GET /api/settings/model-folders` — returns the active Noofy Models folder,
  optional external ComfyUI folder, the supported category list, and existence
  flags.
- `PUT /api/settings/model-folders` — updates either or both folders. Empty
  string for `external_comfyui_models_dir` clears the connected ComfyUI folder.
  Validation rejects locations inside `third_party/comfyui/`, non-folder paths,
  and unwritable Noofy Models targets. Path root changes return
  `restart_required: true` so the UI can prompt for a Noofy engine restart;
  Noofy's own model availability checks use the new roots immediately.

### Models Page Inventory API

The Models page is backed by Noofy API endpoints, not by direct ComfyUI calls.

- `GET /api/models` — returns a UI-ready inventory with summary counts, active
  model folders, persisted model tags, local files from the Noofy Models folder,
  read-only files from the optional external ComfyUI models folder, engine-visible
  fallback rows, and missing model requirements from installed workflows. Each
  row includes stable `model_key`, source, ownership (`noofy_downloaded`,
  `noofy_imported`, `noofy_local`, `external_reference`, `engine_reference`, or
  `workflow_requirement`), `can_delete`, workflow usage, download references,
  and persisted tag IDs. `can_delete` is true only for `noofy_downloaded` and
  `noofy_imported` files inside Noofy Models; `noofy_local` means user-owned
  local file in the Noofy Models folder and is intentionally not deletable by
  Noofy.
- `POST /api/models/import` — copies one or more local file paths into a selected
  Noofy Models category. The backend validates the category, rejects path escapes,
  rejects filename collisions unless `overwrite` is true, and never writes into
  the external ComfyUI folder. Large files are first copied under `.imports/`
  and then atomically moved into the selected category so interrupted imports do
  not appear as installed models.
- `DELETE /api/models/{model_key}` — deletes only an existing regular file that
  resolves inside the configured Noofy Models folder and has Noofy-owned
  provenance (`noofy_imported` or `noofy_downloaded`). It also clears local app
  tag and ownership metadata for that model key. It cannot delete arbitrary
  user-owned `noofy_local` files, external ComfyUI folder files,
  engine-visible references, or missing requirements.
- `POST /api/models/tags` and `PUT /api/models/{model_key}/tags` — persist
  local app tags and model/tag assignments under Noofy settings data. Tags are
  app-local organization metadata and are not exported in workflow packages.
- `POST /api/models/downloads`, `GET /api/models/downloads/active`,
  `GET /api/models/downloads/{job_id}`, and
  `POST /api/models/downloads/{job_id}/cancel` — start, resume/poll, and cancel
  standalone missing-model downloads selected from installed workflow
  requirements. These jobs reuse the same provider resolver, transaction,
  verification, and cleanup rules as staged workflow imports. Active jobs are
  in-process state with a short completed-job TTL; the active endpoint lets the
  Models page recover polling after a page refresh while the backend process is
  still running.

## API Keys (Hugging Face And Civitai)

External model-platform credentials are settings owned by the backend.

- Default storage: OS credential store via `KeyringCredentialStore`
  ([backend/app/settings/api_keys.py](../backend/app/settings/api_keys.py)).
  Plaintext/file-backed keyring fallbacks are explicitly blocked.
- Headless/source-server storage: use `NOOFY_API_KEY_STORE=encrypted-vault`.
  This stores encrypted ciphertext under the real Noofy app data directory, for
  example `~/.local/share/noofy/settings/api-key-vault.json` on Linux. It
  requires `NOOFY_API_KEY_VAULT_PASSPHRASE_FILE` to point to an operator-owned
  passphrase file outside the Noofy repo checkout.
- App data: only non-sensitive metadata (`configured`, `last_four`,
  `label`) is persisted in Noofy app data. In encrypted-vault mode the full
  keys are encrypted before they are written to the app data vault.
- Full keys must never appear in frontend responses, diagnostics, logs, runner
  environment variables, packaged runtime files, or test fixtures. Decrypted
  vault content, passphrase content, passphrase file content, and full
  sensitive storage paths must not appear in normal API/UI responses.
- Backend services read keys internally when calling providers.

On headless Linux, installing packages is not enough by itself for the default
OS keyring path. Secret Service also needs a D-Bus session, an unlocked
keyring daemon, and Noofy must run in that same session. If Python `keyring`
falls back to `keyring.backends.fail.Keyring`, the UI should keep saving
disabled and explain that no OS-backed credential store is available.

For EC2/source checkout use without Secret Service, the source checkout helper
automatically configures encrypted-vault storage when `NOOFY_API_KEY_STORE` is
not explicitly set. It creates a passphrase file under the user config
directory, for example `~/.config/noofy/api-key-vault.passphrase` on Linux, and
keeps full API keys out of normal API responses and diagnostics.

For direct backend launches without the source checkout helper, or when you want
to override the helper's repo-local default data directory, configure
encrypted-vault mode explicitly:

```bash
mkdir -p "$HOME/.config/noofy" "$HOME/.local/share/noofy"
openssl rand -base64 48 > "$HOME/.config/noofy/api-key-vault.passphrase"
chmod 600 "$HOME/.config/noofy/api-key-vault.passphrase"

export NOOFY_API_KEY_STORE=encrypted-vault
export NOOFY_DATA_DIR="$HOME/.local/share/noofy"
export NOOFY_API_KEY_VAULT_PASSPHRASE_FILE="$HOME/.config/noofy/api-key-vault.passphrase"
backend/.venv/bin/python -m app
```

Source-checkout helpers may default `NOOFY_DATA_DIR` to `.noofy-runtime/data`
inside the repo. That directory is gitignored, and the helper opts in to the
repo-local encrypted-vault development override only for this source checkout
case. Encrypted-vault mode still rejects repo-local Noofy data dirs, vault
files, and passphrase files by default outside the helper.

Endpoints:

- `GET /api/settings/apis` — provider list with `configured`/`last_four` and a
  `credential_store` status (`available`, `unavailable`) plus non-secret
  fields such as `kind`, `backend`, `display_path`, and `guidance`.
- `PUT /api/settings/apis/{provider}/key` — save a key. Returns `503` if the
  configured credential store is unusable.
- `DELETE /api/settings/apis/{provider}/key` — clear a saved key.

Provider slugs accept `hugging-face`/`hugging_face`/`hf` and `civitai`.

## Required-Model Availability

For each required model declared by a `.noofy` package, Noofy produces a
`RequiredModelAvailability` record summarising local presence, provider source
hints, and a user-safe status. The summary is also exposed for an installed
workflow.

Status values:

- `available` — local file found with strong-enough identity for the model's
  verification level (`sha256_size`, `filename_size`, or `filename_only`).
- `possible_match` — a same-name file exists but identity is too weak to
  trust automatically.
- `missing` — not present locally; can be downloaded if a provider source is
  resolvable.
- `needs_manual_download` — missing with no provider source Noofy can act on.
- `download_failed`, `authentication_required`, `rate_limited`,
  `hash_mismatch`, `not_enough_disk_space`, `canceled` — terminal/failure
  states for download attempts.

The summary includes counts and `ready_to_run` (true only when every required
model resolves to `available`).

Endpoint:

- `GET /api/workflows/{workflow_id}/model-summary` — current availability for
  an installed workflow.

`WorkflowPackageValidator` runs this summary for imported packages so workflow
validation reflects Noofy-verified availability (not just raw engine
`object_info`).

## Provider Resolver

When a required model has no usable `source_urls` from the package, Noofy can
search providers for a reliable match. See
[backend/app/workflows/model_availability.py](../backend/app/workflows/model_availability.py).

Resolution order:

1. Explicit package `source_urls` (preferred when present).
2. Hugging Face bounded repo search plus file-metadata inspection
   (`GET https://huggingface.co/api/models`, then selected
   `GET https://huggingface.co/api/models/{repo_id}` requests with blob
   metadata).
3. Civitai by-hash lookup (`GET https://civitai.com/api/v1/model-versions/by-hash/{sha256}`)
   when the package declares a SHA-256.
4. Civitai query fallback (`GET https://civitai.com/api/v1/models`).

Authentication and rate limits:

- Public access is allowed without keys when providers permit it.
- When the corresponding key is configured, requests send
  `Authorization: Bearer <token>`.
- `401` and `403` raise `ProviderAuthenticationRequired` and surface a
  user-safe message. `429` raises `ProviderRateLimited`.

Matching is intentionally conservative:

- A candidate is only accepted as reliable when the filename matches exactly
  **and** either the SHA-256 matches or the byte size matches.
- If a provider exposes SHA-256 metadata and it does not match the package
  SHA-256, Noofy rejects that file even when filename and size match.
- Hugging Face inspection is bounded by a small number of search terms and
  unique repos. It does not scan Hugging Face globally.
- Hugging Face files with exact basename and exact size but no provider
  SHA-256 can be downloaded only as metadata-limited candidates; the final
  local file must still pass package size/SHA verification before becoming
  ready.
- If multiple reliable provider candidates remain, Noofy tries them in
  reliability order and only marks the model ready after strict local
  verification succeeds. A failed candidate is cleaned up before trying the
  next one.
- Fuzzy name matches and "first search result" picks are never used for
  automatic downloads.
- Secret tokens and any leaked URL credentials are redacted from diagnostic
  messages before they reach logs or the UI.

## Staged `.noofy` Import Flow

Imports that contain required model records now go through a preview/commit
flow so the user can review missing models before any state is persisted.

Endpoints:

- `POST /api/workflows/import/preview` — parse the archive and return a
  `StagedWorkflowImportResponse` with an `import_session_id`, a workflow
  summary, and a `model_summary`. Archives with no required models commit
  immediately and return `import_session_id: null`.
- `POST /api/workflows/import/{session}/download-models` — start a background
  download job for the `missing` models in the session. Returns
  `ImportModelDownloadJobStart` with a `job_id`.
- `GET /api/workflows/import/{session}/download-models/{job_id}` — poll job
  progress.
- `POST /api/workflows/import/{session}/download-models/{job_id}/cancel` —
  request cancellation.
- `POST /api/workflows/import/{session}/commit` — finalize the import. Fails
  with `409` while a download job is still active.
- `DELETE /api/workflows/import/{session}` — cancel the import. Active
  downloads are signaled to stop.

Frontend behavior:

- The import modal shows the staged model list (filename, type/folder,
  identity level, size when known, source availability, status) before
  committing.
- User actions: **Download missing models**, **Continue without downloading**,
  **Cancel import**.
- Continuing without downloading commits the workflow but leaves it not ready
  while any required model remains unavailable. Running stays blocked until
  the model summary reports `ready_to_run: true`.

### Session TTL

Pending import sessions live for **1 hour** of inactivity
(`IMPORT_SESSION_TTL` in [backend/app/workflows/import_orchestrator.py](../backend/app/workflows/import_orchestrator.py)).

- Active download jobs keep their session alive across the polling window.
- Expired sessions are removed opportunistically and return `410` from any
  staged endpoint, with a message telling the user to import the workflow
  again.

## Download Transactions

Every download runs as a transaction under the Noofy Models folder:

```text
<Noofy Models>/
  .downloads/
    <download_id>/
      <filename>.part
      download-state.json
```

Behavior:

- Bytes stream into `<filename>.part`. `download-state.json` records the
  redacted source URL, provider, target folder/filename, expected size and
  SHA-256, current status, and timestamps.
- Size and SHA-256 are verified when known. Mismatches fail the transaction.
- On success, Noofy moves the file atomically into the configured Noofy Models
  category folder. Final validated models must never remain under
  `.downloads/`.
- On failure or cancellation, the `.part` file and the transaction folder are
  cleaned up safely.
- Path containment and symlink-escape protections enforce that final files
  land inside the configured Noofy Models folder.

### Startup Cleanup

On backend startup the `ModelAvailabilityService` removes any transaction
folder whose `download-state.json` is in an active status
(`downloading`, `verifying`, `placing`). Completed-but-not-yet-moved
transactions are left for the normal flow to finalize on retry.

## Background Download Job

`POST /api/workflows/import/{session}/download-models` schedules an
`asyncio` task per session. Progress events update:

- `current_model_filename`, `current_model_index`, `total_models`
- `bytes_downloaded`, `total_bytes`, `percent`
- `speed_bytes_per_second`
- per-model `status`/`status_label` and optional `message`
- a refreshed `model_summary` once the job ends

Status transitions: `queued` → `running` → `completed` | `failed` | `canceled`.

Cancellation rules:

- Models that already finished before cancel are kept on disk and reflected in
  the final summary as `available`.
- The in-flight model's partial download is removed via the transaction
  cleanup path.
- Retries start a fresh transaction and a new `job_id`.

## Safety Rules (Quick Reference)

- Frontend must never call Hugging Face or Civitai directly.
- Backend never writes downloads outside the configured Noofy Models folder.
- Backend never writes downloads into the external (user-owned) ComfyUI folder
  or `third_party/comfyui/`.
- API keys live only in the configured credential store: OS keyring by default,
  or explicit encrypted-vault mode for headless/source-server use. Only
  `configured`/`last_four` and non-secret credential-store status fields ever
  appear in JSON responses.
- Provider responses with `401`/`403`/`429` are surfaced with user-safe
  messages; secrets and credential-bearing URL fragments are redacted from
  diagnostics.
- Auto-download requires exact filename plus exact size or matching SHA-256.

## Code Map

- Model availability/resolver/downloads: [backend/app/workflows/model_availability.py](../backend/app/workflows/model_availability.py)
- Model folder settings: [backend/app/models/folders.py](../backend/app/models/folders.py)
- API key settings: [backend/app/settings/api_keys.py](../backend/app/settings/api_keys.py)
- Staged import / background job orchestration: [backend/app/workflows/import_orchestrator.py](../backend/app/workflows/import_orchestrator.py)
- API routes: [backend/app/api/routes/](../backend/app/api/routes/)
- Frontend API client: [frontend/src/lib/api/noofyApi.ts](../frontend/src/lib/api/noofyApi.ts)
- Import preview modal / progress UI: [frontend/src/features/home/HomePage.tsx](../frontend/src/features/home/HomePage.tsx)
- Settings screen (model folder + APIs cards): [frontend/src/features/settings/EngineSettingsPage.tsx](../frontend/src/features/settings/EngineSettingsPage.tsx)

## Focused Tests

- Provider resolver, availability summary, transaction safety, startup
  cleanup: [backend/tests/test_model_availability.py](../backend/tests/test_model_availability.py)
- Staged import preview, TTL, download job, cancel/commit endpoints:
  [backend/tests/test_api_workflow_import.py](../backend/tests/test_api_workflow_import.py)
- API key endpoints and credential store handling:
  [backend/tests/test_api_keys.py](../backend/tests/test_api_keys.py)
- Frontend import preview/progress/cancel:
  [frontend/src/features/home/HomePage.test.tsx](../frontend/src/features/home/HomePage.test.tsx)

Default tests must use mocked/offline provider responses. No default test may
hit live Hugging Face or Civitai endpoints.
