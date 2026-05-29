from __future__ import annotations

from app.models.source_auth import provider_auth_headers_for_url


def test_provider_auth_headers_for_model_source_urls() -> None:
    calls: list[str] = []

    def api_key_resolver(provider: str) -> str | None:
        calls.append(provider)
        return f"{provider}-token"

    assert provider_auth_headers_for_url(
        "https://huggingface.co/creator/repo/resolve/main/model.safetensors",
        api_key_resolver,
    ) == {"Authorization": "Bearer hugging_face-token"}
    assert provider_auth_headers_for_url(
        "https://civitai.com/api/download/models/2979642?fileId=2859181",
        api_key_resolver,
    ) == {"Authorization": "Bearer civitai-token"}
    assert provider_auth_headers_for_url(
        "https://example.invalid/model.safetensors",
        api_key_resolver,
    ) == {}
    assert provider_auth_headers_for_url(
        "https://civitai.com.evil.example/model.safetensors",
        api_key_resolver,
    ) == {}
    assert calls == ["hugging_face", "civitai"]
