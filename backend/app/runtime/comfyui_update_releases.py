from __future__ import annotations

import re
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

UPSTREAM_REPO = "Comfy-Org/ComfyUI"
UPSTREAM_RELEASES_API = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases"


class UpstreamComfyUIRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tag_name: str = Field(min_length=1)
    name: str | None = None
    draft: bool = False
    prerelease: bool = False
    published_at: str | None = None
    zipball_url: str | None = None
    tarball_url: str | None = None
    html_url: str | None = None
    target_commitish: str | None = None


async def fetch_upstream_releases() -> list[UpstreamComfyUIRelease]:
    payload: list[object] = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for page in range(1, 6):
            response = await client.get(
                UPSTREAM_RELEASES_API,
                headers={"Accept": "application/vnd.github+json"},
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            page_payload = response.json()
            if not isinstance(page_payload, list):
                raise RuntimeError("GitHub releases response was not a list.")
            payload.extend(page_payload)
            if len(page_payload) < 100:
                break
    releases = [UpstreamComfyUIRelease.model_validate(item) for item in payload]
    return stable_sorted_releases(releases)


async def download_archive(url: str, dest: Path) -> int:
    bytes_written = 0
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)
                    bytes_written += len(chunk)
    return bytes_written


def stable_sorted_releases(
    releases: list[UpstreamComfyUIRelease],
) -> list[UpstreamComfyUIRelease]:
    stable = [
        release for release in releases if not release.draft and not release.prerelease
    ]
    return sorted(
        stable, key=lambda release: version_sort_key(release.tag_name), reverse=True
    )


def version_sort_key(tag: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", tag)]
    return tuple(numbers or [0])
