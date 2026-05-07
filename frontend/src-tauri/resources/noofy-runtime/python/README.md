# Packaged Python Runtime Placeholder

Final Noofy installers must replace this placeholder with a portable,
Noofy-owned Python runtime for the target platform.

The Tauri shell looks for:

- Windows: `python.exe` or `Scripts/python.exe`
- macOS/Linux: `bin/python3` or `bin/python`

The packaged runtime must include `venv`, `pip`, backend dependencies, and
Noofy's controlled `uv` binary. It must not point at system Python, Homebrew,
Conda, or a developer virtual environment.
