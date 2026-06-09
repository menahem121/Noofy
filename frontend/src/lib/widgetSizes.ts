import type { GridItemLayout } from "./gridLayout";

export interface WidgetDimensions {
  w: number;
  h: number;
}

export interface WidgetSizingPolicy {
  default: WidgetDimensions;
  minimum: WidgetDimensions;
}

export type WidgetTypeKey =
  | "slider"
  | "int_field"
  | "string_field"
  | "textarea"
  | "note"
  | "toggle"
  | "load_image"
  | "load_image_mask"
  | "load_audio"
  | "load_video"
  | "load_file"
  | "load_3d"
  | "display_image"
  | "display_audio"
  | "display_video"
  | "display_file"
  | "display_3d"
  | "result_image"
  | "seed_widget"
  | "lora_loader"
  | "select"
  | "api_credential";

const FALLBACK_WIDGET_SIZING: WidgetSizingPolicy = {
  default: { w: 8, h: 6 },
  minimum: { w: 5, h: 4 },
};

export const WIDGET_SIZING_POLICY: Record<WidgetTypeKey, WidgetSizingPolicy> = {
  slider: { default: { w: 10, h: 4 }, minimum: { w: 6, h: 4 } },
  int_field: { default: { w: 6, h: 4 }, minimum: { w: 4, h: 3 } },
  string_field: { default: { w: 6, h: 4 }, minimum: { w: 4, h: 3 } },
  textarea: { default: { w: 8, h: 6 }, minimum: { w: 5, h: 4 } },
  note: { default: { w: 6, h: 4 }, minimum: { w: 4, h: 3 } },
  toggle: { default: { w: 6, h: 4 }, minimum: { w: 4, h: 3 } },
  load_image: { default: { w: 10, h: 10 }, minimum: { w: 6, h: 6 } },
  load_image_mask: { default: { w: 10, h: 10 }, minimum: { w: 6, h: 6 } },
  load_audio: { default: { w: 10, h: 4 }, minimum: { w: 6, h: 4 } },
  load_video: { default: { w: 14, h: 12 }, minimum: { w: 7, h: 6 } },
  load_file: { default: { w: 10, h: 6 }, minimum: { w: 5, h: 4 } },
  load_3d: { default: { w: 12, h: 10 }, minimum: { w: 7, h: 6 } },
  display_image: { default: { w: 14, h: 14 }, minimum: { w: 6, h: 6 } },
  display_audio: { default: { w: 12, h: 6 }, minimum: { w: 8, h: 5 } },
  display_video: { default: { w: 16, h: 14 }, minimum: { w: 8, h: 7 } },
  display_file: { default: { w: 10, h: 6 }, minimum: { w: 6, h: 5 } },
  display_3d: { default: { w: 16, h: 14 }, minimum: { w: 8, h: 8 } },
  result_image: { default: { w: 14, h: 14 }, minimum: { w: 6, h: 6 } },
  seed_widget: { default: { w: 6, h: 4 }, minimum: { w: 4, h: 3 } },
  lora_loader: { default: { w: 8, h: 6 }, minimum: { w: 6, h: 4 } },
  select: { default: { w: 8, h: 6 }, minimum: { w: 5, h: 3 } },
  api_credential: { default: { w: 8, h: 6 }, minimum: { w: 6, h: 4 } },
};

export function defaultSizeForWidgetType(widgetType: string): WidgetDimensions {
  return sizingPolicyForWidgetType(widgetType).default;
}

export function minimumSizeForWidgetType(widgetType: string): WidgetDimensions {
  return sizingPolicyForWidgetType(widgetType).minimum;
}

export function defaultLayoutForWidgetType(widgetType: string): GridItemLayout {
  const size = defaultSizeForWidgetType(widgetType);
  const minimum = minimumSizeForWidgetType(widgetType);
  return { x: 0, y: 0, w: size.w, h: size.h, minW: minimum.w, minH: minimum.h };
}

export function defaultSizeForWidgetGroup(widgetTypes: string[]): WidgetDimensions {
  const childSizes = widgetTypes.length > 0
    ? widgetTypes.map(defaultSizeForWidgetType)
    : [defaultSizeForWidgetType("slider")];
  return {
    w: Math.min(32, Math.max(12, ...childSizes.map((size) => size.w))),
    h: Math.max(6, childSizes.reduce((sum, size) => sum + Math.max(3, size.h), 0)),
  };
}

export function minimumSizeForWidgetGroup(widgetTypes: string[]): WidgetDimensions {
  const childMinimums = widgetTypes.length > 0
    ? widgetTypes.map(minimumSizeForWidgetType)
    : [minimumSizeForWidgetType("slider")];
  return {
    w: Math.max(6, ...childMinimums.map((size) => size.w)),
    h: 6,
  };
}

export function defaultLayoutForWidgetGroup(widgetTypes: string[]): GridItemLayout {
  const size = defaultSizeForWidgetGroup(widgetTypes);
  const minimum = minimumSizeForWidgetGroup(widgetTypes);
  return { x: 0, y: 0, w: size.w, h: size.h, minW: minimum.w, minH: minimum.h };
}

export function withCurrentWidgetMinimum(layout: GridItemLayout, widgetType: string): GridItemLayout {
  const minimum = minimumSizeForWidgetType(widgetType);
  return { ...layout, minW: minimum.w, minH: minimum.h };
}

export function withCurrentWidgetGroupMinimum(layout: GridItemLayout, widgetTypes: string[]): GridItemLayout {
  const minimum = minimumSizeForWidgetGroup(widgetTypes);
  return { ...layout, minW: minimum.w, minH: minimum.h };
}

export function isWidgetLayoutCompact(layout: GridItemLayout, widgetType: string): boolean {
  const size = defaultSizeForWidgetType(widgetType);
  return layout.w < size.w || layout.h < size.h;
}

export function isWidgetGroupLayoutCompact(layout: GridItemLayout, widgetTypes: string[]): boolean {
  const size = defaultSizeForWidgetGroup(widgetTypes);
  return layout.h < size.h;
}

function sizingPolicyForWidgetType(widgetType: string): WidgetSizingPolicy {
  return WIDGET_SIZING_POLICY[widgetType as WidgetTypeKey] ?? FALLBACK_WIDGET_SIZING;
}
