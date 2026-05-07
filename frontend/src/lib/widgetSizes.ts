import type { GridItemLayout } from "./gridLayout";

export type WidgetSizePreset = "compact" | "standard" | "wide" | "media" | "media-large";

export interface WidgetPresetDef {
  name: string;
  w: number;
  h: number;
}

export const WIDGET_SIZE_PRESETS: Record<WidgetSizePreset, WidgetPresetDef> = {
  compact: { name: "Compact", w: 8, h: 4 },
  standard: { name: "Standard", w: 11, h: 4 },
  wide: { name: "Wide", w: 16, h: 6 },
  media: { name: "Media", w: 13, h: 10 },
  "media-large": { name: "Media Large", w: 19, h: 14 },
};

export type WidgetTypeKey =
  | "slider"
  | "int_field"
  | "string_field"
  | "textarea"
  | "toggle"
  | "load_image"
  | "load_image_mask"
  | "display_image"
  | "result_image"
  | "seed_widget"
  | "lora_loader"
  | "select";

const DEFAULT_PRESETS: Record<WidgetTypeKey, WidgetSizePreset> = {
  slider: "compact",
  int_field: "compact",
  toggle: "compact",
  string_field: "standard",
  seed_widget: "standard",
  select: "standard",
  load_image: "standard",
  load_image_mask: "standard",
  lora_loader: "standard",
  textarea: "wide",
  display_image: "media-large",
  result_image: "media-large",
};

export function defaultLayoutForWidgetType(widgetType: string): GridItemLayout {
  const preset = DEFAULT_PRESETS[widgetType as WidgetTypeKey] ?? "standard";
  const def = WIDGET_SIZE_PRESETS[preset];
  return { x: 0, y: 0, w: def.w, h: def.h, minW: def.w, minH: def.h };
}

export function presetForWidgetType(widgetType: string): WidgetSizePreset {
  return DEFAULT_PRESETS[widgetType as WidgetTypeKey] ?? "standard";
}
