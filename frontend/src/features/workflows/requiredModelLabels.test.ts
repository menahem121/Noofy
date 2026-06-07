import { describe, expect, it } from "vitest";

import { requiredModelTypeLabel } from "./requiredModelLabels";

describe("requiredModelTypeLabel", () => {
  it("uses the download destination folder as the precise model type", () => {
    expect(requiredModelTypeLabel("checkpoints", "AI model")).toBe("Checkpoint");
    expect(requiredModelTypeLabel("diffusion_models", "Checkpoint")).toBe("Diffusion model");
    expect(requiredModelTypeLabel("vae", "Image helper")).toBe("VAE");
  });

  it("humanizes new destination folders instead of falling back to a generic model name", () => {
    expect(requiredModelTypeLabel("custom_audio_models", null)).toBe("Custom audio models");
  });

  it("preserves established model acronyms", () => {
    expect(requiredModelTypeLabel("LLM", null)).toBe("LLM");
    expect(requiredModelTypeLabel("clip_vision", null)).toBe("CLIP Vision");
  });

  it("uses model metadata only when destination-folder metadata is unavailable", () => {
    expect(requiredModelTypeLabel("", "text_encoder")).toBe("Text encoder");
    expect(requiredModelTypeLabel("", null)).toBe("Model");
  });
});
