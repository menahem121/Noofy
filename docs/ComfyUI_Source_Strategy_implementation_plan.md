# ComfyUI Source Strategy Implementation Plan

Implementation status: complete.

**Chosen ComfyUI Source Strategy**
Use a vendored upstream source snapshot at `third_party/comfyui`.

Rationale:
- The repo already contains a tracked ComfyUI source snapshot at `ComfyUI-official-repo/`.
- A git submodule would add clone/setup friction and make local managed mode easier to break.
- A bootstrap-cloned source would require network and create uncertainty for first-run development/product behavior.
- A vendored `third_party/comfyui` source is self-contained, packageable, and can be updated intentionally as a single vendor refresh.

Status: implemented. The tracked source strategy has moved from root-level `ComfyUI-official-repo/` to `third_party/comfyui`. Any ignored local data left under the old path is not used by Noofy defaults.

**Folder Structure**
Implemented structure:

```text
third_party/
  comfyui/                 # app-owned upstream ComfyUI source snapshot

<Noofy data dir>/
  runtime/                 # ComfyUI venv, pid file, runtime process state
  runtime-store/
    core-engines/          # future reproducible packaged ComfyUI source artifacts
    runner-workspaces/     # isolated workflow runner workspaces
  models/                  # app-owned model store/view
  input/                   # ComfyUI input/staging files
  outputs/                 # generated outputs
  logs/
  cache/
  temp/
  user-state/
    comfyui/               # ComfyUI user/db state
  dashboard-assets/
  workflow-store/
```

Existing ignored local folders under `ComfyUI-official-repo/` such as `models/`, `input/`, `output/`, or `custom_nodes/` are preserved as ignored local data and are no longer referenced by defaults. Only tracked ComfyUI source files were moved into `third_party/comfyui`.

**Backend Config Changes**
`backend/app/core/paths.py` now defaults `comfyui_repo_dir` to:

```text
<project_root>/third_party/comfyui
```

`COMFYUI_REPO_DIR` remains an override for dev/custom packaging flows.

Clear runtime-data path properties were added where useful, especially for ComfyUI input/user/db state, so code does not infer writable paths from the source checkout.

Status: implemented. `NoofyPaths` now resolves `input_dir`, `comfyui_custom_nodes_dir`, `comfyui_user_dir`, `comfyui_database_file`, and `python_cache_dir`.

**Managed Startup Command**
`RuntimeManager` managed mode startup now includes:

```bash
--disable-auto-launch
--dont-print-server
--base-directory <Noofy data dir>
--output-directory <Noofy outputs dir>
--input-directory <Noofy input dir>
--temp-directory <Noofy data dir>  # ComfyUI appends its temp child internally
--user-directory <Noofy user-state/comfyui dir>
--database-url sqlite:///<Noofy user-state/comfyui/comfyui.db>
```

Python/cache behavior is routed away from the source checkout where practical by setting a managed process cache/pycache env under Noofy’s cache dir.

Status: implemented. `RuntimeManager` adds the no-browser flags and accepts Noofy-managed writable path settings from the default engine service.

**Development Workflow Impact**
External mode stays unchanged:

```bash
COMFYUI_RUNTIME_MODE=external
COMFYUI_BASE_URL=http://127.0.0.1:8188
```

Managed mode becomes easier locally:

```bash
COMFYUI_RUNTIME_MODE=managed
```

That should use `third_party/comfyui` by default, bootstrap/use Noofy’s managed venv, and write runtime data into Noofy app-data paths.

**Production Direction**
Document that packaged builds should eventually materialize or bundle a clean reproducible ComfyUI source artifact under:

```text
runtime-store/core-engines/comfyui-core-<version>-<source-hash>/
```

The same `COMFYUI_REPO_DIR`/path contract can point to that artifact in production. `third_party/comfyui` remains the repo-owned source snapshot and packaging input, not a user’s external ComfyUI install.

**Docs To Update**
- `docs/ARCHITECTURE.md`
- `docs/MANAGED_COMFYUI_SIDECAR.md`
- `backend/README.md`
- `README.md` if needed
- `AGENTS.md` references from `ComfyUI-official-repo/` to `third_party/comfyui`
- `Makefile` defaults for source-based smoke tooling

Status: implemented for the listed docs and Makefile defaults.

**Tests / Smoke Checks**
Add or update tests for:

- Default ComfyUI source path resolves to `third_party/comfyui`.
- `COMFYUI_REPO_DIR` still overrides the source path.
- `ensure_directories()` creates writable runtime/data dirs but does not create or mutate the ComfyUI source dir.
- Managed startup command includes `--disable-auto-launch` and `--dont-print-server`.
- Managed startup command includes path isolation flags so input/output/temp/user/db do not default into the source checkout.
- External mode still does not spawn a managed process.

Status: implemented for targeted backend tests. Full verification is tracked in the final implementation summary.

**Architecture Review**
This plan matches the current architecture: FastAPI remains the only frontend target, `ComfyUIEngineAdapter` remains the backend-to-ComfyUI boundary, external mode remains a dev convenience, and managed mode becomes backed by an app-owned source without mutating that source at runtime.
