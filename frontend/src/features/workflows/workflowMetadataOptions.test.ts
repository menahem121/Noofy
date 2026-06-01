import { describe, expect, it } from "vitest";

import { WORKFLOW_CATEGORY_OPTIONS, workflowCategoryOption } from "./workflowMetadataOptions";

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
});
