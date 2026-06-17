import { describe, expect, it } from "vitest";

import {
  NATIVE_WORKFLOW_ICON_OPTIONS,
  WORKFLOW_CATEGORY_OPTIONS,
  WORKFLOW_ICONS,
  workflowCategoryOption,
} from "./workflowMetadataOptions";

describe("workflow metadata category options", () => {
  it("includes modality workflow type categories for discovery filters", () => {
    expect(WORKFLOW_CATEGORY_OPTIONS).toEqual(expect.arrayContaining([
      "txt2audio",
      "audio2audio",
      "txt2vid",
      "img2vid",
      "imgTo3D",
      "txtTo3D",
      "txt2txt",
      "img2text",
      "audio2txt",
      "vid2vid",
    ]));
    expect(workflowCategoryOption("imgTo3D")).toBe("imgTo3D");
    expect(workflowCategoryOption("txt2txt")).toBe("txt2txt");
  });

  it("includes a native video workflow icon for video generation workflows", () => {
    expect(WORKFLOW_ICONS.video).toBeDefined();
    expect(NATIVE_WORKFLOW_ICON_OPTIONS).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "video", label: "Video" }),
    ]));
  });

  it("includes native workflow icons for additional workflow modalities", () => {
    for (const id of ["model3d", "audio", "text", "highDefinition", "upscale", "editing"]) {
      expect(WORKFLOW_ICONS[id]).toBeDefined();
    }
    expect(NATIVE_WORKFLOW_ICON_OPTIONS).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "model3d", label: "3D model" }),
      expect.objectContaining({ id: "audio", label: "Audio" }),
      expect.objectContaining({ id: "text", label: "Text" }),
      expect.objectContaining({ id: "maximize", label: "Outpainting" }),
      expect.objectContaining({ id: "highDefinition", label: "High definition" }),
      expect.objectContaining({ id: "upscale", label: "Upscale" }),
      expect.objectContaining({ id: "editing", label: "Editing" }),
    ]));
  });
});
