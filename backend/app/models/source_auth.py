from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from urllib.parse import urlparse

ModelSourceProvider = Literal["hugging_face", "civitai"]
ApiKeyResolver = Callable[[ModelSourceProvider], str | None]


def provider_auth_headers_for_url(
    url: str, api_key_resolver: ApiKeyResolver
) -> dict[str, str]:
    provider = provider_from_model_source_url(url)
    if provider is None:
        return {}
    token = api_key_resolver(provider)
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def provider_from_model_source_url(url: str) -> ModelSourceProvider | None:
    host = (urlparse(url).hostname or "").casefold()
    if _host_matches_domain(host, "huggingface.co"):
        return "hugging_face"
    if _host_matches_domain(host, "civitai.com"):
        return "civitai"
    return None


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")
