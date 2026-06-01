import { describe, expect, it } from "vitest";

import { defaultLayoutForWidgetType } from "./widgetSizes";

describe("defaultLayoutForWidgetType", () => {
  it.each([
    ["toggle", 6, 4],
    ["int_field", 6, 4],
    ["string_field", 6, 4],
    ["seed_widget", 6, 4],
    ["textarea", 8, 6],
    ["note", 6, 4],
    ["select", 8, 6],
    ["lora_loader", 8, 6],
    ["slider", 10, 4],
    ["load_image", 10, 10],
    ["load_image_mask", 10, 10],
    ["load_audio", 10, 4],
    ["load_video", 14, 12],
    ["load_3d", 12, 10],
    ["load_file", 10, 6],
    ["display_mask", 10, 10],
    ["display_image", 14, 14],
    ["display_audio", 12, 6],
    ["display_video", 16, 14],
    ["display_3d", 16, 14],
    ["display_file", 10, 6],
    ["result_image", 14, 14],
  ])("uses the requested default and minimum size for %s", (widgetType, w, h) => {
    expect(defaultLayoutForWidgetType(widgetType)).toEqual({
      x: 0,
      y: 0,
      w,
      h,
      minW: w,
      minH: h,
    });
  });
});
