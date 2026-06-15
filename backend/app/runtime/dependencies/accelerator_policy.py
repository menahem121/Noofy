"""Policy for accelerator and core-runtime packages in custom-node dependencies.

The stable Noofy runtime does not install accelerator attention packages
requested by community custom nodes: they change ComfyUI's attention path
(and therefore same-seed outputs), usually need machine-specific binaries,
and are not part of the validated runtime profile. They are stripped from
custom-node dependency resolution and recorded in developer diagnostics;
dependency import, custom-node registration, and workflow smoke stages then
decide whether the workflow still works without them.

Core runtime packages (torch and friends) are provided by the managed runtime
environment. Custom-node requirements for them are treated as satisfied by the
core runtime and never re-resolved, so a community pin can never replace or
shadow the validated torch build.
"""

from __future__ import annotations

import re

# Normalized distribution names (PEP 503) of accelerator packages that are not
# part of the stable runtime unless a trusted runtime profile explicitly
# allows them.
UNSUPPORTED_ACCELERATOR_PACKAGES = frozenset(
    {
        "xformers",
        "flash-attn",
        "sageattention",
        "sageattn3",
        "triton",
    }
)

# Top-level import names of the packages above, used to translate import
# failures into a beginner-friendly unsupported-runtime message.
UNSUPPORTED_ACCELERATOR_IMPORT_NAMES = frozenset(
    {
        "xformers",
        "flash_attn",
        "sageattention",
        "sageattn3",
        "triton",
    }
)

# Normalized names of packages pinned by the managed core runtime; custom
# nodes may not introduce or replace them.
CORE_RUNTIME_PACKAGES = frozenset(
    {
        "torch",
        "torchvision",
        "torchaudio",
        "numpy",
    }
)

RUNTIME_EXCLUDED_PACKAGES = frozenset(
    CORE_RUNTIME_PACKAGES | UNSUPPORTED_ACCELERATOR_PACKAGES
)

BUILD_BLOCKED_PACKAGES = frozenset(
    {
        "torch",
        "torchvision",
        "torchaudio",
    }
    | UNSUPPORTED_ACCELERATOR_PACKAGES
)

IGNORED_REASON_UNSUPPORTED_ACCELERATOR = "unsupported_accelerator"
IGNORED_REASON_PROVIDED_BY_CORE_RUNTIME = "provided_by_core_runtime"

_MISSING_MODULE_PATTERN = re.compile(r"No module named ['\"]([A-Za-z0-9_.]+)['\"]")


def ignored_dependency_reason(
    normalized_name: str,
    *,
    allowed_accelerator_packages: frozenset[str] = frozenset(),
) -> str | None:
    """Return why a custom-node dependency is ignored, or None if it is kept."""
    if normalized_name in CORE_RUNTIME_PACKAGES:
        return IGNORED_REASON_PROVIDED_BY_CORE_RUNTIME
    if (
        normalized_name in UNSUPPORTED_ACCELERATOR_PACKAGES
        and normalized_name not in allowed_accelerator_packages
    ):
        return IGNORED_REASON_UNSUPPORTED_ACCELERATOR
    return None


def runtime_excluded_packages(
    *,
    allowed_accelerator_packages: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    return tuple(
        sorted(
            CORE_RUNTIME_PACKAGES
            | (
                UNSUPPORTED_ACCELERATOR_PACKAGES
                - allowed_accelerator_packages
            )
        )
    )


def unsupported_accelerator_module(output: str) -> str | None:
    """Return the unsupported accelerator module named in an import failure."""
    for match in _MISSING_MODULE_PATTERN.finditer(output):
        top_level = match.group(1).split(".")[0]
        if top_level in UNSUPPORTED_ACCELERATOR_IMPORT_NAMES:
            return top_level
    return None


def unsupported_accelerator_message(module: str) -> str:
    return (
        "This workflow includes an add-on that needs the accelerator "
        f"package '{module}'. That accelerator is not supported by Noofy's "
        "stable runtime on this computer, so the workflow cannot run here yet."
    )
