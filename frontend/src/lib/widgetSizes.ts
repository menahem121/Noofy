import type { GridItemLayout } from "./gridLayout";

export type WidgetSizePreset = "compact" | "standard" | "wide" | "media" | "media-large";

export interface WidgetPresetDef {
  name: string;
  w: number;
  h: number;
}

export const WIDGET_SIZE_PRESETS: Record<WidgetSizePreset, WidgetPresetDef> = {
  compact: { name: "Compact", w: 6, h: 4 },
  standard: { name: "Standard", w: 8, h: 6 },
  wide: { name: "Wide", w: 10, h: 4 },
  media: { name: "Media", w: 10, h: 10 },
  "media-large": { name: "Media Large", w: 14, h: 14 },
};

export type WidgetTypeKey =
  | "slider"
  | "int_field"
  | "string_field"
  | "textarea"
  | "note"
  | "toggle"
  | "load_image"
  | "load_image_mask"
  | "display_mask"
  | "display_image"
  | "result_image"
  | "seed_widget"
  | "lora_loader"
  | "select";

const DEFAULT_PRESETS: Record<WidgetTypeKey, WidgetSizePreset> = {
  slider: "wide",
  int_field: "compact",
  toggle: "compact",
  string_field: "compact",
  note: "compact",
  seed_widget: "compact",
  select: "standard",
  load_image: "media",
  load_image_mask: "media",
  display_mask: "media",
  lora_loader: "standard",
  textarea: "standard",
  display_image: "media-large",
  result_image: "media-large",
};

export function defaultLayoutForWidgetType(widgetType: string): GridItemLayout {
  const preset = DEFAULT_PRESETS[widgetType as WidgetTypeKey] ?? "standard";
  const def = WIDGET_SIZE_PRESETS[preset];
  return { x: 0, y: 0, w: def.w, h: def.h, minW: def.w, minH: def.h };
}

export function defaultLayoutForWidgetGroup(widgetTypes: string[]): GridItemLayout {
  const childLayouts = widgetTypes.length > 0
    ? widgetTypes.map((widgetType) => defaultLayoutForWidgetType(widgetType))
    : [defaultLayoutForWidgetType("slider")];
  const minW = Math.max(10, ...childLayouts.map((layout) => layout.minW ?? layout.w));
  const minH = Math.max(6, childLayouts.reduce((sum, layout) => sum + Math.max(3, layout.minH ?? layout.h), 0));
  return { x: 0, y: 0, w: Math.min(32, Math.max(minW, 12)), h: minH, minW, minH };
}

export function presetForWidgetType(widgetType: string): WidgetSizePreset {
  return DEFAULT_PRESETS[widgetType as WidgetTypeKey] ?? "standard";
}
