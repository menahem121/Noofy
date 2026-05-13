"""
Packaging compatibility guard.

Asserts that the package name, entry point, and Tauri/script resource
mappings that depend on `backend/app` and `python -m app` are still intact.

These must never change without a coordinated update to:
  - frontend/src-tauri/src/main.rs  (bundle resources + source launch spec)
  - frontend/src-tauri/tauri.conf.json  (bundle resources)
  - frontend/scripts/packagedRuntime.mjs  (backendAppPackagedPath)
  - backend/pyproject.toml  (includes = ["app*"])
  - scripts/noofy.py  (imports app.engine.factory)

See docs/NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md §2 for the
decision to keep `backend/app` as the permanent package name.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_backend_app_package_dir_exists():
    assert (REPO_ROOT / "backend" / "app").is_dir(), (
        "backend/app must exist — Tauri, Makefile, and packaging all depend on this path"
    )


def test_backend_app_has_main_entry_point():
    assert (REPO_ROOT / "backend" / "app" / "__main__.py").is_file(), (
        "backend/app/__main__.py must exist — `python -m app` is how Tauri and scripts launch the backend"
    )


def test_backend_app_is_python_package():
    assert (REPO_ROOT / "backend" / "app" / "__init__.py").is_file(), (
        "backend/app/__init__.py must exist — app must remain a proper Python package"
    )


def test_pyproject_includes_app_package():
    """pyproject.toml must still list app* in its package includes."""
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text()
    assert 'include = ["app*"]' in pyproject or "app*" in pyproject, (
        "backend/pyproject.toml must include 'app*' so the app package is packaged correctly"
    )


def test_tauri_main_rs_uses_backend_dir():
    """Tauri main.rs must resolve the backend directory from the project root as 'backend'."""
    main_rs = REPO_ROOT / "frontend" / "src-tauri" / "src" / "main.rs"
    if not main_rs.exists():
        return  # Tauri source not present (e.g. CI without Rust toolchain) — skip
    content = main_rs.read_text()
    # main.rs builds paths like root.join("backend") — verify "backend" is still the dir name
    assert '"backend"' in content, (
        'frontend/src-tauri/src/main.rs must reference "backend" as the backend directory name'
    )
    assert "noofy-runtime" in content, (
        "frontend/src-tauri/src/main.rs must reference noofy-runtime as the packaged runtime dir"
    )


def test_tauri_conf_maps_backend_app():
    """tauri.conf.json bundle resources must map backend/app into noofy-runtime/backend/app."""
    tauri_conf = REPO_ROOT / "frontend" / "src-tauri" / "tauri.conf.json"
    if not tauri_conf.exists():
        return
    content = tauri_conf.read_text()
    assert "backend/app" in content, (
        "frontend/src-tauri/tauri.conf.json must contain a bundle.resources entry for backend/app"
    )
    assert "noofy-runtime" in content, (
        "frontend/src-tauri/tauri.conf.json must reference noofy-runtime"
    )


def test_packaged_runtime_script_references_backend_app():
    """packagedRuntime.mjs must set backendAppPackagedPath to 'backend/app'."""
    script = REPO_ROOT / "frontend" / "scripts" / "packagedRuntime.mjs"
    if not script.exists():
        return
    content = script.read_text()
    # Should contain something like: backendAppPackagedPath = "backend/app"
    assert re.search(r'backendAppPackagedPath\s*=\s*["\']backend/app["\']', content), (
        "frontend/scripts/packagedRuntime.mjs must set backendAppPackagedPath = \"backend/app\""
    )


def test_scripts_noofy_imports_from_app():
    """scripts/noofy.py must import from app.* (not a renamed package)."""
    noofy_script = REPO_ROOT / "scripts" / "noofy.py"
    if not noofy_script.exists():
        return
    content = noofy_script.read_text()
    assert re.search(r"from app\.", content) or re.search(r"import app\.", content), (
        "scripts/noofy.py must import from app.* — package name must remain 'app'"
    )
