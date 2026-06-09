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
      "img2text",
      "audio2txt",
      "vid2vid",
    ]));
    expect(workflowCategoryOption("imgTo3D")).toBe("imgTo3D");
  });

  it("includes a native video workflow icon for video generation workflows", () => {
    expect(WORKFLOW_ICONS.video).toBeDefined();
    expect(NATIVE_WORKFLOW_ICON_OPTIONS).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "video", label: "Video" }),
    ]));
  });
});
