# Plan — Find Compatible Models Button for the model Selector Widget

## 1. Purpose

Add a button inside the **LoRA model selector widget** that helps users find and download LoRAs compatible with the currently selected base model.

The goal is to hide technical model-family complexity from normal users. A user should not need to know whether their current model is **SD1.5**, **SDXL**, **Pony**, **Flux**, **SD3**, **Qwen Image**, or another architecture before downloading a LoRA.

The button should answer:

> “Show me LoRAs that are likely to work with the model I am currently using.”

This fits Noofy’s direction because model handling already relies on model identity, checksum, file size, source URL, and verification level, and missing-model download can later use the same metadata path.  The Noofy export extension also already records model references for checkpoints, LoRAs, VAEs, ControlNets, CLIP models, diffusion models, style models, and upscale models, including SHA-256 and file size when available. 

---

## 2. User-Facing Behavior

In a LoRA selector widget, add a secondary action:

```text
[Find compatible LoRAs]
```

or more beginner-friendly:

```text
[Find styles for this model]
```

Recommended product wording:

```text
Find compatible LoRAs
```

When clicked:

1. Noofy checks the currently selected base model.
2. Noofy detects the model family.
3. Noofy searches available LoRAs from configured sources, starting with Civitai or Noofy’s own registry later.
4. Noofy shows LoRAs grouped by compatibility confidence.
5. The user can download one or more LoRAs.
6. Downloaded LoRAs become available in the same LoRA selector widget.

Example UI result:

```text
Compatible with your current model

Best match
- LoRA A — SDXL / Pony
- LoRA B — SDXL

Likely compatible
- LoRA C — Generic SDXL

Uncertain
- LoRA D — Missing metadata

Hidden by default
- SD1.5 LoRAs
- Flux LoRAs
- SD3 LoRAs
```

Important: do **not** present compatibility as perfect. Use confidence labels.

---

## 3. Core Concept

Create a backend service that understands model identity and compatibility.

Suggested internal names:

```text
ModelIdentityService
ModelCompatibilityService
LoraCompatibilityResolver
```

This should not live only in the frontend. The frontend should ask the Noofy backend, because Noofy’s architecture already says the frontend should depend on the app-owned engine contract and not on ComfyUI routes directly. 

---

## 4. What Noofy Should Detect

For the selected base model, detect:

```json
{
  "asset_type": "checkpoint",
  "architecture_family": "sdxl",
  "base_model_tag": "pony",
  "model_format": "safetensors",
  "sha256": "...",
  "size_bytes": 6938045234,
  "confidence": "high",
  "detection_sources": [
    "safetensors_metadata",
    "tensor_signature",
    "civitai_hash_lookup"
  ]
}
```

For LoRAs, detect:

```json
{
  "asset_type": "lora",
  "architecture_family": "sdxl",
  "base_model_tag": "pony",
  "trigger_words": ["..."],
  "sha256": "...",
  "size_bytes": 144703488,
  "confidence": "medium",
  "detection_sources": [
    "civitai_metadata",
    "safetensors_metadata"
  ]
}
```

Supported `architecture_family` values should be extensible:

```text
sd15
sdxl
pony_sdxl
flux
sd3
qwen_image
wan
hunyuan
unknown
```

Do not hardcode the system in a way that only supports Stable Diffusion. The market is moving quickly, so this should be registry-driven or easy to extend.

---

## 5. Detection Strategy

Use several signals, ranked from strongest to weakest.

### 5.1 File hash lookup

Compute the model file SHA-256 and use it to identify the model/version from online registries.

Civitai’s API documentation lists:

```text
GET /api/v1/model-versions/by-hash/:hash
```

for model-version lookup by hash. ([GitHub][1])

Use this when internet access is available.

Expected result:

```text
hash -> known model version -> base model family -> model type -> download metadata
```

This is the strongest path when it works.

### 5.2 Safetensors metadata

For `.safetensors` files, parse the header/metadata without loading the full model into memory. Hugging Face documents that safetensors metadata can expose tensor names, tensor types, shapes, and metadata efficiently. ([Hugging Face][2])

Useful fields may include:

```text
__metadata__
ss_base_model_version
ss_sd_model_name
ss_network_module
ss_network_dim
ss_resolution
modelspec.architecture
modelspec.implementation
modelspec.title
```

But metadata is not guaranteed. Many community files have incomplete metadata.

### 5.3 Tensor-key signature

When metadata is missing or vague, inspect tensor names and shapes.

Example idea:

```text
SD1.5-style keys -> sd15
SDXL-style keys -> sdxl
Flux transformer block keys -> flux
SD3/MMDiT-style keys -> sd3
Qwen Image-style keys -> qwen_image
```

This should be implemented as a signature table that can evolve over time.

### 5.4 ComfyUI runtime metadata

When a model is already visible to the active runner, Noofy can ask the running ComfyUI instance for available models and metadata through the backend adapter. ComfyUI documents routes such as `/models`, `/models/{folder}`, `/view_metadata`, and `/object_info`. ([ComfyUI Documentation][3])

Important: use this through `ComfyUIEngineAdapter`, not directly from the frontend.

---

## 6. Compatibility Rules

Compatibility should be calculated as a confidence score.

Suggested enum:

```text
compatible
likely_compatible
uncertain
not_compatible
```

Example rules:

| Selected base model | LoRA family  | Result                         |
| ------------------- | ------------ | ------------------------------ |
| SD1.5               | SD1.5        | compatible                     |
| SDXL                | SDXL         | compatible                     |
| Pony SDXL           | Pony SDXL    | compatible                     |
| Pony SDXL           | generic SDXL | likely_compatible              |
| SDXL                | Pony SDXL    | likely_compatible or uncertain |
| Flux                | Flux         | compatible                     |
| SD3                 | SD3          | compatible                     |
| Qwen Image          | Qwen Image   | compatible                     |
| Flux                | SDXL         | not_compatible                 |
| SDXL                | SD1.5        | not_compatible                 |
| Unknown             | Known LoRA   | uncertain                      |
| Known model         | Unknown LoRA | uncertain                      |

Do not block uncertain LoRAs completely. Hide them by default or show them under an “Uncertain” section with a warning.

Suggested warning:

```text
Noofy could not confirm this LoRA matches your current model. It may not work correctly.
```

---

## 7. Backend API Proposal

Add backend endpoints like:

```text
GET /api/models/{model_id}/identity
GET /api/models/{model_id}/compatible-loras
POST /api/models/scan
POST /api/lora-sources/search-compatible
POST /api/lora-sources/download
```

Example response:

```json
{
  "selected_model": {
    "id": "model_sdxl_pony_001",
    "display_name": "Pony Realism XL",
    "architecture_family": "pony_sdxl",
    "confidence": "high"
  },
  "results": [
    {
      "id": "civitai:12345:67890",
      "name": "Example Pony Style",
      "source": "civitai",
      "architecture_family": "pony_sdxl",
      "compatibility": "compatible",
      "compatibility_reason": "LoRA metadata and registry base model match the selected model family.",
      "download_size_bytes": 144703488,
      "sha256": "...",
      "trigger_words": ["example_style"],
      "installed": false
    },
    {
      "id": "civitai:22222:33333",
      "name": "Generic SDXL Detail Enhancer",
      "source": "civitai",
      "architecture_family": "sdxl",
      "compatibility": "likely_compatible",
      "compatibility_reason": "This is an SDXL LoRA. Your selected model appears to be Pony/SDXL-derived.",
      "download_size_bytes": 98304000,
      "sha256": "...",
      "trigger_words": [],
      "installed": false
    }
  ]
}
```

---

## 8. Data Model Additions

Add a persistent local model identity record:

```json
{
  "model_id": "local:sha256:...",
  "asset_type": "checkpoint",
  "display_name": "Pony Realism XL",
  "filename": "pony_realism_xl.safetensors",
  "sha256": "...",
  "size_bytes": 6938045234,
  "format": "safetensors",
  "architecture_family": "pony_sdxl",
  "base_model_tag": "pony",
  "source_registry": "civitai",
  "source_model_id": 123,
  "source_model_version_id": 456,
  "identity_confidence": "high",
  "identity_sources": [
    "sha256_size",
    "civitai_hash_lookup",
    "tensor_signature"
  ],
  "last_scanned_at": "2026-05-04T00:00:00Z"
}
```

For LoRAs:

```json
{
  "model_id": "local:sha256:...",
  "asset_type": "lora",
  "display_name": "Example Style LoRA",
  "filename": "example_lora.safetensors",
  "sha256": "...",
  "size_bytes": 144703488,
  "format": "safetensors",
  "architecture_family": "sdxl",
  "base_model_tag": "pony",
  "trigger_words": ["example_style"],
  "source_registry": "civitai",
  "source_model_id": 789,
  "source_model_version_id": 101112,
  "identity_confidence": "medium",
  "identity_sources": [
    "safetensors_metadata",
    "civitai_metadata"
  ]
}
```

This matches the existing Noofy model identity direction: strongest identity is SHA-256 plus size, filename plus size is weaker, and filename-only should not be trusted. 

---

## 9. Download Flow

When the user chooses a LoRA to download:

1. Confirm download size.
2. Check disk space.
3. Download into Noofy-owned model storage.
4. Verify hash and file size if available.
5. Register model identity locally.
6. Materialize the LoRA into the runner-visible model view.
7. Refresh the LoRA selector widget.

Suggested UI text:

```text
Downloading LoRA...
Checking file...
Ready to use
```

If hash verification fails:

```text
The downloaded file does not match the expected identity. Noofy did not install it.
```

---

## 10. Frontend Behavior in the LoRA Selector Widget

The widget should support:

```text
- current selected LoRA
- installed compatible LoRAs
- installed uncertain LoRAs
- Find compatible LoRAs button
- download status
- trigger word helper
- compatibility badge
```

Example:

```text
Style LoRA

[ Select LoRA...                  v ]

Compatible with current model:
✓ Detail Enhancer XL
✓ Cinematic Pony Style

[Find compatible LoRAs]
```

After download:

```text
Downloaded and ready.

Trigger words:
cinematic_style, soft_light
```

---

## 11. Important Product Rule

The button should not say:

```text
Download all compatible LoRAs
```

That sounds dangerous and may download a lot of huge files.

Better:

```text
Find compatible LoRAs
```

Then the user chooses what to download.

---

## 12. Risks and How to Handle Them

### Risk: metadata is missing

Handle with tensor signatures and registry lookup. If still unclear, mark as `uncertain`.

### Risk: Civitai result is incomplete or unavailable

Use local detection fallback. Cache successful registry responses.

### Risk: same architecture but poor practical result

Show “likely compatible,” not “guaranteed.”

### Risk: model families evolve

Keep architecture detection as a versioned rules table, not scattered hardcoded checks.

### Risk: frontend bypasses backend

Do not let the widget call Civitai or ComfyUI directly. The backend owns model identity, downloads, verification, and runner model views.

---

## 13. Implementation Phases

### Phase 1 — Local Model Identity Scanner

Implement local scanning for `.safetensors` files:

```text
- compute SHA-256
- record file size
- parse safetensors metadata
- inspect tensor keys/shapes
- classify model family
- persist identity record
```

Acceptance:

```text
Noofy can classify common SD1.5, SDXL, Flux, SD3, and unknown files with confidence labels.
```

### Phase 2 — LoRA Identity Scanner

Extend the scanner to LoRAs:

```text
- detect LoRA vs checkpoint
- detect target architecture family
- extract trigger words when available
- extract base model metadata when available
```

Acceptance:

```text
Noofy can classify installed LoRAs and group them by compatibility with the selected base model.
```

### Phase 3 — Compatibility Resolver

Add compatibility logic:

```text
selected checkpoint identity + LoRA identity -> compatibility result
```

Acceptance:

```text
The LoRA selector can show compatible, likely compatible, uncertain, and incompatible LoRAs.
```

### Phase 4 — Civitai Lookup

Add optional online lookup:

```text
- hash lookup for known local files
- LoRA search/filter by base model
- result normalization into Noofy model records
- response caching
```

Acceptance:

```text
Clicking the button returns a list of downloadable LoRAs filtered by the current selected model family.
```

### Phase 5 — Download and Verification

Add download support:

```text
- user selects LoRA
- Noofy downloads it
- verifies hash/size when available
- registers it locally
- makes it visible in the LoRA selector
```

Acceptance:

```text
Downloaded LoRAs appear in the widget and are marked with compatibility confidence.
```

---

## 14. Final Direction

This should become a general Noofy capability:

```text
Model Identity + Compatibility
```

not only a single LoRA button.

The LoRA selector button is the first user-facing use case, but the same system will later help with:

```text
- compatible checkpoints
- compatible VAEs
- compatible ControlNets
- compatible upscalers
- workflow missing-model resolution
- marketplace/package model requirements
```

Best product name for the user-facing button:

```text
Find compatible LoRAs
```

Best technical name for the backend work:

```text
ModelCompatibilityService
```

[1]: https://github.com/civitai/civitai/wiki/REST-API-Reference/dff336bf9450cb11e80fb5a42327221ce3f09b45 "REST API Reference · civitai/civitai Wiki · GitHub"
[2]: https://huggingface.co/docs/safetensors/metadata_parsing "Metadata Parsing · Hugging Face"
[3]: https://docs.comfy.org/development/comfyui-server/comms_routes "Routes - ComfyUI"
