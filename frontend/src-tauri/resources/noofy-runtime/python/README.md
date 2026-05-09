# Packaged Python Runtime Placeholder

Final Noofy installers must replace this placeholder with a portable,
Noofy-owned Python runtime for the target platform.

Prepare the runtime through:

```bash
cd frontend
npm run tauri:prepare-runtime -- \
  --source /path/to/noofy-runtime-artifact \
  --python-build-id cpython-3.13-noofy-v1
```

For a local release-smoke artifact, use:

```bash
cd frontend
npm run tauri:download-runtime -- --target macos-arm64
```

The source artifact must contain a `python/` directory. The preparation script
copies that directory here and writes `../runtime-manifest.json` with exact
Python and uv versions plus SHA-256 checksums.

The Noofy backend itself is bundled separately by Tauri into
`noofy-runtime/backend/app` with `noofy-runtime/backend/pyproject.toml`.
Packaged mode launches `python -m app` from that packaged backend directory,
not from the repository checkout. The manifest also records a backend artifact
hash for the files Tauri packages into `noofy-runtime/backend`.

The Tauri shell and verifier look for:

- Windows: `python.exe` or `Scripts/python.exe`
- macOS/Linux: `bin/python3` or `bin/python`
- `uv`: `Scripts/uv.exe`, `uv.exe`, `bin/uv`, or `tools/uv`

The packaged runtime must include `venv`, `pip`, backend dependencies, and
Noofy's controlled `uv` binary. It must not point at system Python, Homebrew,
Conda, or a developer virtual environment.
