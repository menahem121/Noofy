import { describe, expect, it } from "vitest";

import {
  WIDGET_SIZING_POLICY,
  defaultLayoutForWidgetGroup,
  defaultLayoutForWidgetType,
  isWidgetGroupLayoutCompact,
  isWidgetLayoutCompact,
  withCurrentWidgetGroupMinimum,
  withCurrentWidgetMinimum,
} from "./widgetSizes";

describe("widget sizing policy", () => {
  it("contains exactly the widget types supported by the current runtime", () => {
    expect(Object.keys(WIDGET_SIZING_POLICY).sort()).toEqual([
      "api_credential",
      "display_3d",
      "display_audio",
      "display_file",
      "display_image",
      "display_video",
      "int_field",
      "load_3d",
      "load_audio",
      "load_file",
      "load_image",
      "load_image_mask",
      "load_video",
      "lora_loader",
      "note",
      "result_image",
      "seed_widget",
      "select",
      "slider",
      "string_field",
      "textarea",
      "toggle",
    ]);
  });

  it.each([
    ["slider", 10, 4, 6, 4],
    ["int_field", 6, 4, 4, 3],
    ["string_field", 6, 4, 4, 3],
    ["textarea", 8, 6, 5, 4],
    ["note", 6, 4, 4, 3],
    ["toggle", 6, 4, 4, 3],
    ["load_image", 10, 10, 6, 6],
    ["load_image_mask", 10, 10, 6, 6],
    ["load_audio", 10, 4, 6, 4],
    ["load_video", 14, 12, 7, 6],
    ["load_file", 10, 6, 5, 4],
    ["load_3d", 12, 10, 7, 6],
    ["display_image", 14, 14, 6, 6],
    ["display_audio", 12, 6, 8, 5],
    ["display_video", 16, 14, 8, 7],
    ["display_file", 10, 6, 6, 5],
    ["display_3d", 16, 14, 8, 8],
    ["result_image", 14, 14, 6, 6],
    ["seed_widget", 6, 4, 4, 3],
    ["lora_loader", 8, 6, 6, 4],
    ["select", 8, 6, 5, 3],
    ["api_credential", 8, 6, 6, 4],
  ])("keeps the comfortable default and compact minimum for %s", (widgetType, w, h, minW, minH) => {
    expect(defaultLayoutForWidgetType(widgetType)).toEqual({
      x: 0,
      y: 0,
      w,
      h,
      minW,
      minH,
    });
  });

  it("gives every supported widget a minimum smaller than its default", () => {
    for (const sizing of Object.values(WIDGET_SIZING_POLICY)) {
      expect(sizing.minimum.w <= sizing.default.w).toBe(true);
      expect(sizing.minimum.h <= sizing.default.h).toBe(true);
      expect(sizing.minimum.w < sizing.default.w || sizing.minimum.h < sizing.default.h).toBe(true);
    }
  });

  it("uses the current widget minimum without changing loaded dimensions", () => {
    expect(withCurrentWidgetMinimum(
      { x: 7, y: 8, w: 3, h: 2, minW: 99, minH: 99 },
      "textarea",
    )).toEqual({ x: 7, y: 8, w: 3, h: 2, minW: 5, minH: 4 });
  });

  it("uses the explicit fallback policy for unknown widget types", () => {
    expect(defaultLayoutForWidgetType("future_widget")).toEqual({
      x: 0,
      y: 0,
      w: 8,
      h: 6,
      minW: 5,
      minH: 4,
    });
  });

  it("separates comfortable group defaults from compact group minimums", () => {
    expect(defaultLayoutForWidgetGroup(["textarea", "display_image"])).toEqual({
      x: 0,
      y: 0,
      w: 14,
      h: 20,
      minW: 6,
      minH: 6,
    });
    expect(withCurrentWidgetGroupMinimum(
      { x: 3, y: 4, w: 5, h: 4, minW: 99, minH: 99 },
      ["textarea", "display_image"],
    )).toEqual({ x: 3, y: 4, w: 5, h: 4, minW: 6, minH: 6 });
  });

  it("marks only layouts below their default size as compact", () => {
    expect(isWidgetLayoutCompact({ x: 0, y: 0, w: 8, h: 6 }, "textarea")).toBe(false);
    expect(isWidgetLayoutCompact({ x: 0, y: 0, w: 8, h: 5 }, "textarea")).toBe(true);
    expect(isWidgetGroupLayoutCompact({ x: 0, y: 0, w: 14, h: 20 }, ["textarea", "display_image"])).toBe(false);
    expect(isWidgetGroupLayoutCompact({ x: 0, y: 0, w: 14, h: 19 }, ["textarea", "display_image"])).toBe(true);
  });
});
