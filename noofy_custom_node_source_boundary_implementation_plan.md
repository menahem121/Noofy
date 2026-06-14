im# Noofy Custom-Node Source Boundary Implementation Plan

Status: implementation plan  
Scope: backend import/runtime preparation compatibility for real ComfyUI custom-node exports  
Primary goal: let Noofy run more exported ComfyUI workflows without weakening import safety or corrupting runtime boundaries.

## Problem Summary

A Noofy workflow failed before job creation because the custom-node source validator rejected a legitimate nested folder:

```text
custom_nodes/comfyui-rmbg/models/
```

The failure was not a memory error and the workflow did not start. The current validator treats any path whose first segment is `models` as shadowing a protected runtime path, even when that path is only an internal directory inside a custom-node package.

That behavior is too strict for Noofy’s product goal. Noofy should support real ComfyUI exports, where custom nodes commonly contain folders such as:

```text
models/
weights/
assets/
configs/
input/
output/
temp/
user/
scripts/
examples/
```

Those names are only dangerous when they are mounted as Noofy/ComfyUI runtime roots. They are not dangerous when they are normal internal files under a custom-node package.

## Product Goal

Noofy should maximize compatibility with workflows exported from ComfyUI while preserving strong boundaries.

The correct model is:

```text
Strict at Noofy-controlled boundaries.
Permissive inside an isolated custom-node package tree.
```

Noofy should not try to semantically judge every internal folder name. It should preserve package layout as exported, then rely on isolated runner preparation, dependency locks, source-policy checks, smoke tests, and runtime diagnostics.

## Non-Goals

Do not turn this into a one-off `ComfyUI-RMBG` fix.

Do not simply remove all protected-path checks.

Do not let custom-node archives write outside their package mount.

Do not import or execute community custom-node Python in the trusted backend.

Do not mutate `third_party/comfyui/`, the managed core runtime source, the Noofy Models folder, or arbitrary user folders during custom-node source materialization.

Do not pretend this makes unverified community Python safe. This remains dependency/runtime isolation, not a malicious-code sandbox.

Do not block the path-validator fix on the separate hidden model-download issue.

## Current Code Areas To Inspect

Read these files first:

```text
backend/app/runtime/dependencies/custom_nodes.py
backend/app/runtime/node_registry.py
backend/app/workflows/archive_validation.py
backend/app/workflows/importer.py
backend/app/runtime/storage/workspace_preparer.py
backend/app/runtime/capsule_installer.py
backend/tests/test_custom_node_workspace.py
docs/RUNTIME_ISOLATION_ARCHITECTURE.md
docs/WORKFLOW_PACKAGES.md
docs/MODEL_RESOLUTION_AND_DOWNLOADS.md
```

The likely faulty logic is in:

```text
backend/app/runtime/dependencies/custom_nodes.py
```

Specifically, the current `_safe_relative_posix_path(...)` mixes generic safe-path validation with protected runtime-folder shadowing.

## Core Design Rule

Introduce a custom-node source boundary model:

```text
.noofy archive / source cache
        |
        v
custom-node source package tree
        |
        v
runner_workspace/custom_nodes/<safe-package-folder>/
        |
        v
internal package files are preserved as-is, subject only to safety checks
```

Protected names are forbidden only when they would become a Noofy-controlled mount point or direct custom-node package folder.

Protected names are allowed inside a custom-node package.

## Target Path Validation Model

Replace the current single-purpose validator with role-specific validators.

### 1. Archive Member Path Validator

Purpose: safely inspect and extract archive members.

Use for:

```text
.noofy archive members
downloaded custom-node source archive members
cached source archive members
```

Rules:

- reject absolute paths
- reject `..`
- reject empty path segments
- reject backslashes
- reject symlinks and special files
- reject duplicate archive paths
- reject case-insensitive collisions when materializing to a filesystem
- enforce max file count and max uncompressed bytes
- ignore harmless platform junk such as `.DS_Store` and AppleDouble files where appropriate

Do not reject path segments merely because they are named:

```text
models
input
output
temp
user
assets
configs
weights
```

### 2. Custom-Node Package Folder Validator

Purpose: validate the folder that will be created directly under:

```text
runner_workspace/custom_nodes/
```

Rules:

- must be one path segment only
- must not be empty, `.`, or `..`
- must not contain path separators
- must not be a protected runtime name
- must avoid case-insensitive collision with another materialized custom-node package
- must be stable/deterministic for the same source package

Protected direct package folder names should include at least:

```text
models
input
output
temp
user
.git
__pycache__
```

This protection applies only to the direct package mount name, not to internal package paths.

### 3. Custom-Node Internal Path Validator

Purpose: validate files inside one custom-node package.

Use for paths relative to the package root, for example:

```text
models/birefnet.py
assets/icon.png
configs/default.yaml
input/defaults/example.png
```

Rules:

- allow nested folders
- reject traversal
- reject absolute paths
- reject backslashes
- reject symlinks and special files
- reject case-insensitive collisions
- enforce file-count and byte-size limits

Do not reject normal internal folder names like `models`, `input`, `output`, `temp`, or `user`.

### 4. Runner Workspace Destination Validator

Purpose: guarantee copied source files land only under:

```text
runner_workspace/custom_nodes/<safe-package-folder>/
```

Rules:

- compute destination from the safe package folder and internal relative path
- resolve/containment-check destination before writing
- never write into:
  - `runner_workspace/models`
  - `runner_workspace/input`
  - `runner_workspace/output`
  - `runner_workspace/temp`
  - `runner_workspace/user`
  - `third_party/comfyui`
  - managed core runtime source
  - Noofy Models folder
  - external ComfyUI models folder
  - arbitrary user paths

## Package Folder Mapping Policy

Preserve the exported custom-node folder name whenever it is safe.

If the exported folder name is unsafe or protected, but package metadata has a safe `package_id`, mount under the safe package ID.

If neither the folder name nor package ID is safe, block preparation with a clear user-facing message and developer details.

Suggested deterministic fallback shape when remapping is safe and needed:

```text
custom_nodes/noofy_<normalized_package_id>_<short_source_hash>/
```

Be cautious: changing the folder name can break custom nodes that rely on absolute imports based on their folder name. Therefore:

1. Preserve original folder name when safe.
2. Use package ID only when needed.
3. Remap with hash only as a last resort.
4. Record the remap in the custom-node workspace manifest and diagnostics.

## Required Implementation Changes

### Step 1 — Split Validators

In `backend/app/runtime/dependencies/custom_nodes.py`:

Create separate helpers similar to:

```python
def _safe_relative_posix_path(value: str, *, allow_nested: bool) -> str:
    """Path shape only: no absolute paths, no traversal, no backslashes."""
    ...

def _safe_custom_node_package_folder_name(value: str) -> str:
    """Single custom-node package folder under runner_workspace/custom_nodes."""
    value = _safe_relative_posix_path(value, allow_nested=False)
    if value.casefold() in _PROTECTED_CUSTOM_NODE_FOLDER_NAMES:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PROTECTED_PATH_SHADOWING,
            f"Custom-node source shadows a protected runtime path: {value}",
        )
    return value

def _safe_custom_node_internal_relative_path(value: str) -> str:
    """Internal package path. Allows nested models/input/output/etc."""
    return _safe_relative_posix_path(value, allow_nested=True)
```

Then update call sites so protected-name checks are used only for direct package folder names.

### Step 2 — Fix Internal Source Tree Validation

Update `_validate_materialized_source_path(...)`.

It should validate the source path with the internal-path validator, not the package-folder validator.

Expected behavior:

```text
models/foo.py                allowed inside package
input/example.png            allowed inside package
output_templates/foo.json    allowed inside package
../escape.py                 rejected
/absolute.py                 rejected
a\b.py                      rejected
symlink                      rejected
```

### Step 3 — Tighten `CustomNodeWorkspaceEntry.materialized_relative_path`

The manifest field should not accept arbitrary nested paths just because `allow_nested=True`.

It should accept only:

```text
custom_nodes/<safe-package-folder>
```

Reject:

```text
models
custom_nodes
custom_nodes/foo/bar
../custom_nodes/foo
custom_nodes/models
custom_nodes/input
```

This makes the manifest safer and easier to reason about.

### Step 4 — Preserve Source Content Hash Semantics

Do not change source tree hashing in a way that hides files.

The source content hash should still include all materialized source files that Noofy will copy.

If Noofy chooses to ignore platform junk such as `.DS_Store`, it must do so consistently at:

- validation
- hashing
- copying

Do not hash a file and then skip copying it, or copy a file that was not included in the hash.

### Step 5 — Ensure Cached Registry Sources Follow The Same Boundary Model

Review `backend/app/runtime/node_registry.py`.

The registry/source-cache extractor already uses a more generic safe-relative-path check. Keep it permissive for internal archive paths, but make sure:

- archive extraction cannot escape the transaction/source directory
- `archive_subdir` cannot escape
- cached source content hash remains tied to the verified archive bytes
- materialization into the runner workspace still goes through the safe package-folder mapping

Do not add protected-folder rejection to internal downloaded archive paths.

### Step 6 — Add Diagnostics

When Noofy rejects a custom node source, developer details should say which boundary failed:

```text
archive_member_path
custom_node_package_folder
custom_node_internal_path
runner_workspace_destination
```

This avoids confusing messages like:

```text
Custom-node source shadows a protected runtime path: models
```

when the actual path is a nested internal folder.

Beginner-facing message should stay simple:

```text
Noofy could not prepare one workflow extension safely.
```

Developer details should include:

```json
{
  "boundary": "custom_node_internal_path",
  "relative_path": "models/birefnet.py",
  "reason": "path_traversal | symlink | collision | protected_package_folder | oversized"
}
```

### Step 7 — Keep Import Flow Behavior Stable

The path fix should not change:

- `.noofy` package schema
- dashboard schema
- model-summary API shape
- run API shape
- trust policy semantics
- source-policy enforcement
- dependency resolver behavior
- runner selection / Memory Governor behavior
- frontend API calls

This should be a backend compatibility fix.

## Hidden Runtime Model Downloads

The `ComfyUI-RMBG` / `BiRefNet-HR` case also exposes a separate issue: some custom nodes download model weights internally at runtime instead of declaring them as required workflow models.

Do not solve that by weakening path validation.

Handle it as a second, general workflow-compatibility capability.

### Short-Term Behavior

For this implementation:

- allow the custom-node package to prepare if its source tree is safe
- do not require the package to declare hidden weights before the validator fix can land
- let smoke/custom-node registration/runtime execution reveal whether the node can run
- surface clear diagnostics if the node downloads or fails because weights are missing

### Medium-Term Direction

Add optional custom-node model requirement metadata.

Possible sources:

```text
.noofy package metadata
Noofy registry metadata
known custom-node compatibility metadata
export report hints
runtime smoke diagnostics
```

This metadata should eventually produce normal Noofy required-model records where possible.

For known custom nodes, Noofy can map:

```text
custom-node package/version + node type/input
        -> required model file/source/hash/size/folder
```

Then Noofy can use the existing model-resolution flow:

- show missing model before run
- download through Noofy
- verify size/hash
- install into Noofy Models
- track ownership
- reuse across workflows
- clean up only when unreferenced

### Unknown Dynamic Downloads

For unknown custom-node downloads, Noofy should be honest:

```text
This workflow extension may download additional files the first time it runs.
```

Those files should ideally be routed to a Noofy-controlled app-data cache, not random global cache locations.

Longer-term, define a custom-node cache policy:

```text
runtime-store/custom-node-runtime-cache/<runner-or-package-id>/
```

or model-cache integration if the file is a reusable model weight.

Do not claim Noofy verified hidden downloads unless Noofy actually downloaded and verified them.

## Test Plan

Add or update tests in:

```text
backend/tests/test_custom_node_workspace.py
```

### Required Unit Tests

```text
test_materializer_rejects_protected_custom_node_folder_name
```

Keep existing behavior: top-level custom node package named `models` is rejected.

```text
test_materializer_allows_nested_models_directory_inside_custom_node
```

Fixture:

```text
custom_nodes/comfyui-rmbg/__init__.py
custom_nodes/comfyui-rmbg/models/__init__.py
custom_nodes/comfyui-rmbg/models/birefnet.py
```

Expected:

```text
runner-workspace/custom_nodes/comfyui-rmbg/models/birefnet.py
```

exists after materialization.

```text
test_materializer_allows_common_internal_runtime_like_folder_names
```

Internal folders to allow:

```text
models/
input/
output/
temp/
user/
assets/
configs/
weights/
```

```text
test_workspace_entry_rejects_arbitrary_nested_materialized_path
```

Reject:

```text
custom_nodes/foo/bar
custom_nodes/models
models/foo
../custom_nodes/foo
```

```text
test_internal_source_path_still_rejects_traversal
```

Reject archive/source path escapes.

```text
test_internal_source_path_still_rejects_symlink
```

Symlinks remain unsupported.

```text
test_internal_source_path_still_rejects_case_insensitive_collision
```

Example:

```text
models/Foo.py
models/foo.py
```

should collide on case-insensitive filesystems.

```text
test_materializer_never_writes_outside_runner_custom_nodes_package
```

Assert no file is created outside:

```text
runner_workspace/custom_nodes/<package-folder>/
```

### Regression Fixture

Add a small fake custom node shaped like `ComfyUI-RMBG`:

```text
custom_nodes/comfyui-rmbg/
  __init__.py
  nodes.py
  models/
    __init__.py
    birefnet.py
```

It does not need real RMBG code or model weights. It only proves Noofy accepts realistic package structure.

### Integration-Level Test

If there is already a capsule installer / workspace preparer test path, add a fixture package that includes:

```text
custom_nodes/comfyui-rmbg/models/birefnet.py
```

and verify:

- import succeeds
- capsule lock refresh succeeds
- runner workspace materialization succeeds
- custom-node source content hash is deterministic
- trusted runtime source is unchanged

## Manual Validation

After implementation, run focused tests first:

```bash
cd backend
.venv/bin/python -m pytest backend/tests/test_custom_node_workspace.py
```

Then run relevant import/runtime tests:

```bash
cd backend
.venv/bin/python -m pytest backend/tests -k "custom_node or capsule or import"
```

Then run the full backend suite if practical:

```bash
make test
```

Finally test with the real failing workflow package:

1. Import the remove-background workflow.
2. Confirm import succeeds.
3. Confirm runner preparation does not reject `custom_nodes/comfyui-rmbg/models`.
4. Confirm the workflow either:
   - runs successfully, or
   - fails later with an honest missing model/runtime/dependency error.
5. Confirm the failure is no longer reported as protected runtime path shadowing.

## Acceptance Criteria

The implementation is ready when all of these are true:

- Noofy accepts custom-node packages containing nested internal folders named `models`, `input`, `output`, `temp`, or `user`.
- Noofy still rejects a custom-node package mounted directly as `custom_nodes/models`.
- Noofy still rejects traversal, absolute paths, backslashes, symlinks, oversized trees, and case-insensitive collisions.
- Noofy copies custom-node source only into isolated runner workspaces.
- Noofy never writes custom-node source into the managed core runtime, `third_party/comfyui`, Noofy Models, external ComfyUI models, or arbitrary user paths.
- Existing package/dashboard/run API shapes remain unchanged.
- Existing trust/source-policy behavior remains unchanged.
- The old RMBG failure is gone.
- Any remaining RMBG/BiRefNet issue is reported as a separate model/runtime/download problem, not as a custom-node path validation problem.
- Tests prove both compatibility and safety boundaries.

## Suggested Agent Task

```text
Please implement the custom-node source boundary refactor.

Goal:
Noofy should support real ComfyUI custom-node packages that contain nested folders named models, input, output, temp, user, assets, configs, weights, etc., while still protecting Noofy-controlled runtime roots.

Do not make a one-off ComfyUI-RMBG fix. Split path validation by boundary:
- archive/source member path
- direct custom-node package folder under runner_workspace/custom_nodes
- internal custom-node package path
- final runner workspace destination

Protected names like models/input/output/temp/user must remain forbidden only as direct custom-node package folder names, not as internal package directories.

Keep the trusted backend data-only: do not import or execute community custom-node code. Keep all materialization inside isolated runner workspaces. Do not change package schemas or frontend API shapes.

Add tests proving:
- custom_nodes/models is still rejected as a package folder
- custom_nodes/comfyui-rmbg/models/... is allowed
- internal folders named input/output/temp/user are allowed
- traversal, symlink, case-insensitive collision, oversize, and destination escape are still rejected
- materialization never mutates trusted runtime files

After this, separately report on the hidden BiRefNet/runtime model download issue. Do not block the path fix on that second issue.
```
