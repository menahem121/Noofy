# Model Compatibility Plan

Status: active future plan, partially overlapping with shipped behavior.

The first user-facing feature is a LoRA selector action such as **Find compatible LoRAs**. The durable architecture goal is broader: a backend model identity and compatibility system that can later help with LoRAs, checkpoints, VAEs, ControlNets, upscalers, missing-model resolution, and marketplace/package model requirements.

Current repo verification: Noofy has model requirement identity levels, a model store, model-view materialization, and model validation through the active `EngineAdapter`. Required-model resolution and verified downloads from Hugging Face and Civitai (by-hash and query) are already implemented for the staged `.noofy` import flow and Noofy Models folder — see [MODEL_RESOLUTION_AND_DOWNLOADS.md](MODEL_RESOLUTION_AND_DOWNLOADS.md). What is still missing for this plan: a generic local model identity scanner, the LoRA compatibility resolver, the compatible-LoRA API, registry search beyond required-model resolution, and the frontend compatible-LoRA picker.

## Product Behavior

The LoRA selector should let a user ask for LoRAs that are likely to work with the currently selected base model. The UI must present confidence, not certainty.

Suggested result groups:

- Compatible
- Likely compatible
- Uncertain
- Hidden by default / not compatible

The user chooses what to download. Do not provide a bulk "download all compatible LoRAs" action.

## Backend Ownership

This feature belongs behind the Noofy backend API. The frontend must not call ComfyUI, Civitai, Hugging Face, or any model registry directly.

Likely backend concepts:

- `ModelIdentityService`
- `ModelCompatibilityService`
- `LoraCompatibilityResolver`
- model registry source adapters
- verified model download/install path using the existing model store

The implementation must respect existing source policy, trust, model ownership, diagnostics, and runner-visible model-view rules.

## Identity Record Direction

Persist local model identity records with:

- asset type: checkpoint, LoRA, VAE, ControlNet, text encoder, diffusion model, upscaler, etc.
- display name and filename
- SHA-256 and byte size when available
- format, e.g. `safetensors`
- architecture family, e.g. `sd15`, `sdxl`, `pony_sdxl`, `flux`, `sd3`, `qwen_image`, `wan`, `hunyuan`, `unknown`
- base model tag when useful
- trigger words for LoRAs when available
- source registry and registry IDs when known
- identity confidence and detection sources
- last scanned timestamp

Keep the architecture-family rules table versioned and easy to extend. Do not scatter model-family hardcoding through frontend components.

## Detection Signals

Use multiple signals, strongest first:

1. File hash lookup against configured registries when online lookup is enabled and policy permits it.
2. Local `safetensors` metadata parsing without loading full model tensors into memory.
3. Tensor-key and shape signatures for known architecture families.
4. Active engine metadata through `EngineAdapter` when a model is visible to the runner.

Registry APIs and metadata formats change. Before implementation, verify current upstream API contracts and rate/auth requirements from primary sources.

External model-platform API keys are user settings and must be handled by the backend only. Store Hugging Face and Civitai keys in the operating system credential store, with only non-sensitive metadata such as `configured` and `last_four` in Noofy app-data settings. Do not place full keys in workflow packages, dashboards, diagnostics, logs, packaged runtime files, or runner environment variables by default.

Civitai provides a model-version lookup endpoint by file hash:

- `GET https://civitai.com/api/v1/model-versions/by-hash/{hash}`

This lookup returns a model version, not only the top-level model. The response can provide fields such as model ID, model name, version name, file information, and download URL. Older official REST reference material says the fields match the normal model-version endpoint and that supported hashes include AutoV1, AutoV2, SHA256, CRC32, and Blake3. Re-verify the current endpoint behavior before implementation.

## Compatibility Rules

Compatibility should produce a confidence classification and explanation.

Examples:

- SD1.5 checkpoint + SD1.5 LoRA: compatible.
- SDXL checkpoint + SDXL LoRA: compatible.
- Pony/SDXL checkpoint + Pony/SDXL LoRA: compatible.
- Pony/SDXL checkpoint + generic SDXL LoRA: likely compatible.
- Known base model + unknown LoRA: uncertain.
- Unknown base model + known LoRA: uncertain.
- Flux checkpoint + SDXL LoRA: not compatible.

Uncertain LoRAs should remain available behind a warning instead of being hard-blocked.

## Proposed API Shape

Names may change, but the backend needs these capabilities:

- `GET /api/models/{model_id}/identity`
- `GET /api/models/{model_id}/compatible-loras`
- `POST /api/models/scan`
- `POST /api/model-sources/search-compatible`
- `POST /api/model-sources/download`

Downloads must check disk space, write to Noofy-owned model storage, verify hash/size when available, register local identity, materialize the file into the runner-visible model view, refresh relevant selectors, and emit diagnostics.

## Implementation Order

1. Local checkpoint identity scanner: SHA-256, size, safetensors metadata, tensor signatures, persisted identity records.
2. Local LoRA identity scanner: LoRA detection, target architecture, trigger words, base-model metadata.
3. Compatibility resolver: selected checkpoint identity plus LoRA identity to confidence/result groups.
4. Optional online registry lookup: hash lookup, search normalization, response caching, source-policy enforcement.
5. Download and verification: install selected LoRA into Noofy model storage and model views.
6. Frontend picker: show compatible/likely/uncertain LoRAs and download status inside the LoRA selector widget.

## Acceptance Criteria

- The frontend uses only Noofy backend APIs.
- Common SD1.5, SDXL, Pony/SDXL, Flux, SD3, and unknown local files classify with confidence labels.
- Installed LoRAs are grouped by compatibility with the selected base model.
- Online lookup is optional, policy-aware, and cached.
- Downloaded LoRAs are verified before registration and become visible in the selector.
- Hash or size mismatch fails closed with a clear user-facing state and diagnostic event.
