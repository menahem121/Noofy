# OS Sandboxing Feasibility

Status: feasibility reference.

Noofy's runtime isolation architecture isolates dependencies, runner workspaces, model views, install transactions, and runner processes. It does not currently provide a hard security sandbox for arbitrary community Python code.

## Product Claim

Noofy may claim:

- community workflow installs do not mutate the trusted core runtime
- dependency conflicts are isolated per resolved runtime capsule
- failed installs roll back or quarantine staged artifacts
- custom-node Python imports happen only in runner processes

Noofy must not claim:

- arbitrary community Python code is safe
- unverified workflows are trustworthy
- virtual environments prevent file access, network access, or malicious behavior
- current runner processes are OS-sandboxed

## macOS

Potential mechanisms:

- App Sandbox
- hardened runtime
- child-process entitlement restrictions
- read/write scoped bookmarks
- network entitlement restrictions

Feasibility:

- App Sandbox can restrict file and network access, but ComfyUI runners need Python execution, model access, GPU/Metal access, temp/cache directories, and dynamically materialized custom-node files.
- Tauri app signing/notarization and managed Python runners need careful entitlement design.
- Per-runner filesystem allowlists may be possible through app-container paths and scoped access, but this requires product packaging work and real-device testing.

Decision:

- macOS sandboxing is feasible only as dedicated hardening work after product packaging is stable.
- Runtime isolation completion does not require macOS App Sandbox implementation.

## Windows

Potential mechanisms:

- restricted process tokens
- Windows Job Objects
- AppContainer
- Controlled Folder Access guidance
- firewall/network rules

Feasibility:

- Job Objects are useful for process-tree cleanup, resource accounting, and kill behavior.
- Restricted tokens can reduce privileges but may break Python, GPU, file linking, model access, and custom-node subprocesses.
- AppContainer offers stronger boundaries but requires significant packaging and filesystem capability work.

Decision:

- Windows process supervision and cleanup are necessary product reliability work.
- Windows AppContainer-style sandboxing is future security hardening, not required for this runtime isolation plan.

## Linux

Potential mechanisms:

- namespaces
- seccomp
- cgroups
- Landlock
- Flatpak portals
- container-like runner wrappers
- network namespace restrictions

Feasibility:

- Linux provides strong primitives, but availability varies by distribution, packaging format, kernel version, GPU backend, and user permissions.
- GPU access, model-store mounts, cache directories, and Python runner startup complicate a portable default sandbox.
- Flatpak could provide a packaging-level sandbox, but AppImage/deb/rpm distributions would need separate handling.

Decision:

- Linux sandboxing is feasible for a future controlled packaging target or optional runner wrapper.
- It is not required to complete dependency/runtime isolation.

## Cross-Platform Future Requirements

Future OS sandboxing work should define:

- runner filesystem allowlists
- network policy per trust level
- GPU/device access policy
- model-store access mode
- temp/cache cleanup behavior
- process-tree cleanup behavior
- diagnostics for sandbox denial without exposing technical noise by default
- compatibility tests per OS/package format/GPU backend

## Conclusion

OS-level sandboxing is not required for the current runtime isolation architecture, because the accepted architecture is dependency/runtime isolation rather than malicious-code containment.

Docs and UI must make that boundary explicit and must not imply unverified community code is safe or OS-sandboxed.
