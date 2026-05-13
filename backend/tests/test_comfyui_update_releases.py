from __future__ import annotations

from app.runtime.comfyui.comfyui_update_releases import (
    UpstreamComfyUIRelease,
    stable_sorted_releases,
    version_sort_key,
)


def _release(tag: str, *, prerelease: bool = False, draft: bool = False):
    return UpstreamComfyUIRelease(
        tag_name=tag,
        prerelease=prerelease,
        draft=draft,
    )


def test_stable_sorted_releases_filters_prerelease_and_draft_versions() -> None:
    releases = stable_sorted_releases(
        [
            _release("v0.19.0"),
            _release("v0.21.0-rc1", prerelease=True),
            _release("v0.20.1"),
            _release("v0.22.0", draft=True),
        ]
    )

    assert [release.tag_name for release in releases] == ["v0.20.1", "v0.19.0"]


def test_version_sort_key_uses_numeric_components() -> None:
    assert version_sort_key("v0.20.10") > version_sort_key("v0.20.2")
    assert version_sort_key("latest") == (0,)
