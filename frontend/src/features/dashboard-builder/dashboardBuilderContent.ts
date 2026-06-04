import type { LucideIcon } from "lucide-react";
import {
  Cpu,
  File,
  FileAudio,
  Box,
  Image as ImageIcon,
  ImagePlus,
  Layers,
  Save,
  Shuffle,
  SlidersHorizontal,
  Sparkles,
  StickyNote,
  Type,
  Video,
} from "lucide-react";
import {
  minimumSizeForWidgetGroup,
  minimumSizeForWidgetType,
  withCurrentWidgetGroupMinimum,
  withCurrentWidgetMinimum,
} from "../../lib/widgetSizes";

export type WidgetType =
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
  | "seed_widget"
  | "lora_loader"
  | "select";

export type WorkflowValueKind =
  | "string"
  | "number"
  | "boolean"
  | "note"
  | "image_input"
  | "image_output"
  | "audio_input"
  | "audio_output"
  | "video_input"
  | "video_output"
  | "file_input"
  | "file_output"
  | "three_d_input"
  | "three_d_output"
  | "seed"
  | "lora"
  | "select";

export interface WorkflowNodeValue {
  id: string;
  nodeId: string;
  inputName: string;
  label: string;
  valueKind: WorkflowValueKind;
  rawValue: unknown;
  hint?: string;
  numberRange?: { min: number; max: number; step?: number };
  options?: string[];
  autoSelect?: boolean;
  technical?: boolean;
}

export type NodeIconKind = "text" | "note" | "sampler" | "image" | "image-input" | "audio" | "video" | "file" | "3d" | "lora" | "tune" | "output" | "save";

export interface WorkflowNode {
  id: string;
  classType: string;
  title: string;
  iconKind: NodeIconKind;
  values: WorkflowNodeValue[];
}

export interface MockWorkflow {
  id: string;
  name: string;
  source: "imported_noofy_package" | "raw_comfyui_json";
  nodes: WorkflowNode[];
}

export interface DashboardWidgetLayout {
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
}

export interface DashboardCanvasLayout {
  gridColumns: number;
  rowHeight: number;
  gridGap: number;
  responsive: boolean;
}

export interface DashboardActionBarPosition {
  x: number;
  y: number;
}

export interface DashboardPresentation {
  actionBar?: DashboardActionBarPosition;
}

export interface DashboardWidget {
  id: string;
  binding: { nodeId: string; inputName: string };
  valueId: string;
  widgetType: WidgetType;
  title: string;
  description: string;
  defaultValue: unknown;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  acceptedExtensions?: string[];
  acceptedMimeTypes?: string[];
  drawMask?: boolean;
  defaultPinned?: boolean;
  hasExecutableBinding?: boolean;
  layout?: DashboardWidgetLayout;
}

export interface DashboardWidgetGroup {
  id: string;
  title: string;
  description: string;
  widgetIds: string[];
  layout?: DashboardWidgetLayout;
}

export interface DashboardSchema {
  version: number;
  workflowId: string;
  workflowName: string;
  widgets: DashboardWidget[];
  hiddenWidgets?: DashboardWidget[];
  groups: DashboardWidgetGroup[];
  layout: DashboardCanvasLayout;
  presentation?: DashboardPresentation;
}

export type DashboardTopLevelItem =
  | { kind: "widget"; id: string; widget: DashboardWidget; layout?: DashboardWidgetLayout }
  | { kind: "group"; id: string; group: DashboardWidgetGroup; widgets: DashboardWidget[]; layout?: DashboardWidgetLayout };

export const DEFAULT_FILE_ACCEPTED_EXTENSIONS = [".txt", ".json", ".csv", ".srt", ".pdf", ".zip", ".npy", ".pt"];

export function dashboardDraftKey(workflowId: string) {
  return `noofy.builderDraft.${workflowId}`;
}

export function saveDashboardDraft(schema: DashboardSchema) {
  window.localStorage.setItem(
    dashboardDraftKey(schema.workflowId),
    JSON.stringify({ ...normalizeDashboardSchema(schema), status: "draft" }),
  );
}

export function loadDashboardDraft(workflowId: string): DashboardSchema | null {
  try {
    const raw = window.localStorage.getItem(dashboardDraftKey(workflowId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DashboardSchema>;
    if (
      parsed.workflowId !== workflowId ||
      typeof parsed.version !== "number" ||
      typeof parsed.workflowName !== "string" ||
      !Array.isArray(parsed.widgets) ||
      !parsed.layout
    ) {
      return null;
    }
    return normalizeDashboardSchema(parsed as DashboardSchema);
  } catch {
    return null;
  }
}

export function clearDashboardDraft(workflowId: string) {
  window.localStorage.removeItem(dashboardDraftKey(workflowId));
}

export const NODE_ICONS: Record<NodeIconKind, LucideIcon> = {
  text: Type,
  note: StickyNote,
  sampler: Shuffle,
  image: ImageIcon,
  "image-input": ImagePlus,
  audio: FileAudio,
  video: Video,
  file: File,
  "3d": Box,
  lora: Sparkles,
  tune: SlidersHorizontal,
  output: ImageIcon,
  save: Save,
};

export const VALUE_KIND_ICONS: Record<WorkflowValueKind, LucideIcon> = {
  string: Type,
  note: StickyNote,
  number: SlidersHorizontal,
  boolean: SlidersHorizontal,
  image_input: ImagePlus,
  image_output: ImageIcon,
  audio_input: FileAudio,
  audio_output: FileAudio,
  video_input: Video,
  video_output: Video,
  file_input: File,
  file_output: File,
  three_d_input: Box,
  three_d_output: Box,
  seed: Shuffle,
  lora: Sparkles,
  select: Layers,
};

export const WIDGET_TYPE_LABELS: Record<WidgetType, string> = {
  slider: "Slider",
  int_field: "Number field",
  string_field: "Single line text",
  textarea: "Multi-line text",
  note: "Note",
  toggle: "On / off",
  load_image: "Load image",
  load_image_mask: "Load image with mask",
  load_audio: "Load audio",
  load_video: "Load video",
  load_file: "Load file",
  load_3d: "Load 3D model",
  display_image: "Display image",
  display_audio: "Display audio",
  display_video: "Display video",
  display_file: "Display file",
  display_3d: "Display 3D model",
  seed_widget: "Variation ID (seed)",
  lora_loader: "LoRA loader",
  select: "Dropdown",
};

const INPUT_WIDGET_TYPES: WidgetType[] = [
  "slider",
  "int_field",
  "string_field",
  "textarea",
  "toggle",
  "load_image",
  "load_image_mask",
  "load_audio",
  "load_video",
  "load_file",
  "load_3d",
  "seed_widget",
  "lora_loader",
  "select",
];

export function isOutputWidgetType(widgetType: string): boolean {
  return widgetType === "display_image" || widgetType === "display_audio" || widgetType === "display_video" || widgetType === "display_file" || widgetType === "display_3d" || widgetType === "result_image";
}

export function widgetTypesForKind(kind: WorkflowValueKind): WidgetType[] {
  if (kind === "note") {
    return ["note"];
  }

  if (kind === "image_output") {
    return ["display_image"];
  }

  if (kind === "audio_output") {
    return ["display_audio"];
  }
  if (kind === "video_output") {
    return ["display_video"];
  }
  if (kind === "file_output") {
    return ["display_file"];
  }
  if (kind === "three_d_output") return ["display_3d"];

  if (kind === "image_input") {
    return ["load_image", "load_image_mask"];
  }

  if (kind === "audio_input") {
    return ["load_audio"];
  }
  if (kind === "video_input") {
    return ["load_video"];
  }
  if (kind === "file_input") {
    return ["load_file"];
  }
  if (kind === "three_d_input") return ["load_3d"];

  if (kind === "seed") {
    return ["seed_widget", "int_field", "slider"];
  }

  if (kind === "lora") {
    return ["lora_loader", "select"];
  }

  if (kind === "boolean") {
    return ["toggle", "select"];
  }

  if (kind === "number") {
    return ["slider", "int_field"];
  }

  if (kind === "string") {
    return ["string_field", "textarea", "select"];
  }

  if (kind === "select") {
    return ["select", "string_field"];
  }

  return INPUT_WIDGET_TYPES;
}

export function suggestWidgetType(value: WorkflowNodeValue): WidgetType {
  if (value.valueKind === "note") return "note";
  if (value.valueKind === "image_output") return "display_image";
  if (value.valueKind === "image_input") return "load_image";
  if (value.valueKind === "audio_output") return "display_audio";
  if (value.valueKind === "audio_input") return "load_audio";
  if (value.valueKind === "video_output") return "display_video";
  if (value.valueKind === "video_input") return "load_video";
  if (value.valueKind === "file_output") return "display_file";
  if (value.valueKind === "file_input") return "load_file";
  if (value.valueKind === "three_d_output") return "display_3d";
  if (value.valueKind === "three_d_input") return "load_3d";
  if (value.valueKind === "seed") return "seed_widget";
  if (value.valueKind === "lora") return "lora_loader";
  if (value.valueKind === "boolean") return "toggle";
  if (value.valueKind === "select") return "select";

  if (value.valueKind === "number") {
    return value.numberRange || isImageDimensionValue(value) ? "slider" : "int_field";
  }

  if (value.valueKind === "string") {
    if (typeof value.rawValue === "string" && value.rawValue.length > 32) {
      return "textarea";
    }
    return "string_field";
  }

  return "string_field";
}

export function defaultNumericRangeForValue(value: WorkflowNodeValue): { min: number; max: number; step: number } | undefined {
  if (value.valueKind !== "number" && value.valueKind !== "seed") return undefined;
  if (value.numberRange) {
    return { min: value.numberRange.min, max: value.numberRange.max, step: value.numberRange.step ?? 1 };
  }

  const raw = typeof value.rawValue === "number" && Number.isFinite(value.rawValue) ? value.rawValue : 0;
  if (isImageDimensionValue(value)) {
    const step = 64;
    return {
      min: 64,
      max: Math.max(2048, Math.ceil(raw / step) * step),
      step,
    };
  }

  if (!Number.isInteger(raw)) {
    return {
      min: 0,
      max: raw > 1 ? Math.max(10, Math.ceil(raw * 2)) : 1,
      step: raw > 1 ? 0.1 : 0.01,
    };
  }

  return {
    min: 0,
    max: Math.max(100, raw),
    step: 1,
  };
}

function isImageDimensionValue(value: Pick<WorkflowNodeValue, "inputName" | "label">): boolean {
  const identity = `${value.inputName} ${value.label}`.toLowerCase();
  return /\b(width|height)\b/.test(identity);
}

export function suggestTitle(value: WorkflowNodeValue, nodeTitle: string): string {
  if (value.valueKind === "note") return nodeTitle || "Note";
  if (value.valueKind === "three_d_input" || value.valueKind === "three_d_output") return "3D model";

  const labelMap: Record<string, string> = {
    text: nodeTitle.toLowerCase().includes("negative") ? "Negative prompt" : "Prompt",
    prompt: "Prompt",
    negative: "Negative prompt",
    width: "Width",
    height: "Height",
    seed: "Variation ID",
    denoise: "Transformation level",
    steps: "Detail passes",
    cfg: "Prompt strength",
    strength: "Strength",
    image: "Input image",
    audio: "Input audio",
    file: "Input file",
    output_audio: "Result",
    output_file: "Result",
    lora_name: "LoRA model",
    filename_prefix: "Save name",
    output_image: "Result",
  };

  const key = value.inputName.toLowerCase();
  if (labelMap[key]) {
    return labelMap[key];
  }

  return value.label.charAt(0).toUpperCase() + value.label.slice(1).replace(/_/g, " ");
}

export function suggestDescription(value: WorkflowNodeValue): string {
  if (value.valueKind === "note") {
    return typeof value.rawValue === "string" ? value.rawValue : "";
  }

  const hints: Record<string, string> = {
    text: "Describe what you want to create.",
    prompt: "Describe what you want to create.",
    negative: "Describe what to avoid in the result.",
    width: "Output width in pixels.",
    height: "Output height in pixels.",
    seed: "Lock or change to get a different variation.",
    denoise: "Lower keeps your input. Higher allows bigger changes.",
    steps: "More passes can add detail but take longer.",
    cfg: "How strongly the prompt guides the image.",
    image: "Choose an image from your computer.",
    audio: "Choose an audio file from your computer.",
    file: "Choose a file from your computer.",
    lora_name: "Pick a LoRA style to apply.",
    output_image: "Generated image will appear here.",
    output_audio: "Generated audio will appear here.",
    output_file: "Generated file will appear here.",
  };

  const key = value.inputName.toLowerCase();
  return hints[key] ?? value.hint ?? "";
}

export function createDashboardWidgetForValue(value: WorkflowNodeValue, node: WorkflowNode): DashboardWidget {
  const widgetType = suggestWidgetType(value);
  const numericRange = widgetType === "slider" ? defaultNumericRangeForValue(value) : value.numberRange;
  return {
    id: `ctrl-${value.id}`,
    valueId: value.id,
    binding: { nodeId: value.nodeId, inputName: value.inputName },
    widgetType,
    title: suggestTitle(value, node.title),
    description: suggestDescription(value),
    defaultValue: value.rawValue,
    options: value.options,
    acceptedExtensions: widgetType === "load_file" ? DEFAULT_FILE_ACCEPTED_EXTENSIONS : undefined,
    min: numericRange?.min,
    max: numericRange?.max,
    step: numericRange?.step,
    drawMask: widgetType === "load_image_mask" ? true : undefined,
  };
}

export const MOCK_WORKFLOW: MockWorkflow = {
  id: "imported_text_to_image_demo",
  name: "Imported text-to-image",
  source: "raw_comfyui_json",
  nodes: [
    {
      id: "6",
      classType: "CLIPTextEncode",
      title: "Positive prompt",
      iconKind: "text",
      values: [
        {
          id: "node-6-text",
          nodeId: "6",
          inputName: "text",
          label: "text",
          valueKind: "string",
          rawValue: "A high-quality photo of a cute dog in a park, 4k, detailed",
          hint: "What should appear in the image",
        },
      ],
    },
    {
      id: "7",
      classType: "CLIPTextEncode",
      title: "Negative prompt",
      iconKind: "text",
      values: [
        {
          id: "node-7-text",
          nodeId: "7",
          inputName: "text",
          label: "text",
          valueKind: "string",
          rawValue: "blurry, low quality, distorted",
          hint: "What to keep out of the image",
        },
      ],
    },
    {
      id: "5",
      classType: "EmptyLatentImage",
      title: "Image size",
      iconKind: "tune",
      values: [
        {
          id: "node-5-width",
          nodeId: "5",
          inputName: "width",
          label: "width",
          valueKind: "number",
          rawValue: 1024,
          numberRange: { min: 256, max: 2048, step: 64 },
        },
        {
          id: "node-5-height",
          nodeId: "5",
          inputName: "height",
          label: "height",
          valueKind: "number",
          rawValue: 1024,
          numberRange: { min: 256, max: 2048, step: 64 },
        },
        {
          id: "node-5-batch_size",
          nodeId: "5",
          inputName: "batch_size",
          label: "batch_size",
          valueKind: "number",
          rawValue: 1,
          numberRange: { min: 1, max: 8, step: 1 },
          technical: true,
        },
      ],
    },
    {
      id: "3",
      classType: "KSampler",
      title: "Sampler",
      iconKind: "sampler",
      values: [
        {
          id: "node-3-seed",
          nodeId: "3",
          inputName: "seed",
          label: "seed",
          valueKind: "seed",
          rawValue: 42,
        },
        {
          id: "node-3-denoise",
          nodeId: "3",
          inputName: "denoise",
          label: "denoise",
          valueKind: "number",
          rawValue: 0.75,
          numberRange: { min: 0, max: 1, step: 0.01 },
        },
        {
          id: "node-3-steps",
          nodeId: "3",
          inputName: "steps",
          label: "steps",
          valueKind: "number",
          rawValue: 20,
          numberRange: { min: 1, max: 60, step: 1 },
          technical: true,
        },
        {
          id: "node-3-cfg",
          nodeId: "3",
          inputName: "cfg",
          label: "cfg",
          valueKind: "number",
          rawValue: 7,
          numberRange: { min: 1, max: 15, step: 0.5 },
          technical: true,
        },
        {
          id: "node-3-sampler_name",
          nodeId: "3",
          inputName: "sampler_name",
          label: "sampler_name",
          valueKind: "select",
          rawValue: "euler",
          options: ["euler", "euler_ancestral", "heun", "dpm_2", "dpmpp_2m"],
          technical: true,
        },
      ],
    },
    {
      id: "10",
      classType: "LoadImage",
      title: "Load image",
      iconKind: "image-input",
      values: [
        {
          id: "node-10-image",
          nodeId: "10",
          inputName: "image",
          label: "image",
          valueKind: "image_input",
          rawValue: null,
          hint: "Reference image for the workflow",
        },
      ],
    },
    {
      id: "12",
      classType: "LoraLoader",
      title: "LoRA",
      iconKind: "lora",
      values: [
        {
          id: "node-12-lora_name",
          nodeId: "12",
          inputName: "lora_name",
          label: "lora_name",
          valueKind: "lora",
          rawValue: "none",
          options: ["none", "anime_v2.safetensors", "photoreal_v3.safetensors"],
        },
        {
          id: "node-12-strength_model",
          nodeId: "12",
          inputName: "strength_model",
          label: "strength_model",
          valueKind: "number",
          rawValue: 0.8,
          numberRange: { min: 0, max: 2, step: 0.05 },
          technical: true,
        },
      ],
    },
    {
      id: "8",
      classType: "VAEDecode",
      title: "VAE Decode",
      iconKind: "tune",
      values: [],
    },
    {
      id: "9",
      classType: "SaveImage",
      title: "Save image",
      iconKind: "save",
      values: [
        {
          id: "node-9-output",
          nodeId: "9",
          inputName: "output_image",
          label: "output_image",
          valueKind: "image_output",
          rawValue: null,
          hint: "Generated image saved to disk",
        },
        {
          id: "node-9-filename_prefix",
          nodeId: "9",
          inputName: "filename_prefix",
          label: "filename_prefix",
          valueKind: "string",
          rawValue: "Noofy",
          technical: true,
        },
      ],
    },
  ],
};

// ─── API ↔ Frontend conversion helpers ──────────────────────────────────────

export interface BackendWorkflowInput {
  id: string;
  label: string;
  control: string;
  binding: { node_id: string; input_name: string };
  default: unknown;
  default_pinned?: boolean;
  validation: Record<string, unknown>;
}

export interface BackendDashboardControl {
  id: string;
  type: string;
  label: string;
  input_id?: string;
  output_id?: string;
  description?: string;
  layout?: { x: number; y: number; w: number; h: number; min_w?: number; min_h?: number };
}

export interface BackendDashboardGroup {
  id: string;
  title: string;
  description?: string;
  control_ids: string[];
  layout?: { x: number; y: number; w: number; h: number; min_w?: number; min_h?: number };
}

export interface BackendDashboardSection {
  id: string;
  title: string;
  controls: BackendDashboardControl[];
  groups?: BackendDashboardGroup[];
}

export interface BackendDashboardPayload {
  version: string;
  status?: string;
  presentation?: { action_bar?: { x: number; y: number } };
  outputs?: Array<{ id: string; label: string; node_id: string; type: string; kind?: string }>;
  sections: BackendDashboardSection[];
}

export interface BackendSavePayload {
  inputs: BackendWorkflowInput[];
  dashboard: BackendDashboardPayload;
}

/** Convert a backend BindableInputsResponse node list into a MockWorkflow. */
export function workflowFromBindableInputs(
  workflowId: string,
  workflowName: string,
  nodes: Array<{
    node_id: string;
    node_type: string;
    node_title?: string;
    is_image_node: boolean;
    is_audio_node?: boolean;
    is_three_d_node?: boolean;
    is_lora_node: boolean;
    inputs: Array<{
      input_name: string;
      current_value: unknown;
      kind: string;
      suggested_widget_type: string;
      widget_types: string[];
      options?: string[];
      hint?: string;
      auto_select?: boolean;
    }>;
  }>
): MockWorkflow {
  function nodeIconKind(nodeType: string, isImageNode: boolean, isLoraNode: boolean): NodeIconKind {
    if (isImageNode) return "image-input";
    if (isLoraNode) return "lora";
    const t = nodeType.toLowerCase();
    if (t.includes("audio")) return "audio";
    if (t.includes("video")) return "video";
    if (t.includes("3d") || t.includes("mesh") || t.includes("glb")) return "3d";
    if (t.includes("file") || t.includes("document") || t.includes("archive")) return "file";
    if (t === "note") return "note";
    if (t.includes("clip") || t.includes("text")) return "text";
    if (t.includes("ksampler") || t.includes("sampler")) return "sampler";
    if (t.includes("save")) return "save";
    if (t.includes("vae") || t.includes("decode")) return "tune";
    if (t.includes("latent") || t.includes("image")) return "image";
    return "tune";
  }

  function valueKindFromString(kind: string): WorkflowValueKind {
    if (kind === "image_input") return "image_input";
    if (kind === "image_output") return "image_output";
    if (kind === "audio_input") return "audio_input";
    if (kind === "audio_output") return "audio_output";
    if (kind === "video_input") return "video_input";
    if (kind === "video_output") return "video_output";
    if (kind === "three_d_input") return "three_d_input";
    if (kind === "three_d_output") return "three_d_output";
    if (kind === "file_input") return "file_input";
    if (kind === "file_output") return "file_output";
    if (kind === "note") return "note";
    if (kind === "seed") return "seed";
    if (kind === "lora") return "lora";
    if (kind === "select") return "select";
    if (kind === "boolean") return "boolean";
    if (kind === "number") return "number";
    return "string";
  }

  const builtNodes: WorkflowNode[] = nodes.map((node) => ({
    id: node.node_id,
    classType: node.node_type,
    title: node.node_title ?? node.node_type,
    iconKind: node.is_three_d_node ? "3d" : node.is_audio_node ? "audio" : nodeIconKind(node.node_type, node.is_image_node, node.is_lora_node),
    values: node.inputs.map((inp) => ({
      id: `node-${node.node_id}-${inp.input_name}`,
      nodeId: node.node_id,
      inputName: inp.input_name,
      label: inp.input_name,
      valueKind: valueKindFromString(inp.kind),
      rawValue: inp.current_value,
      hint: inp.hint,
      options: inp.options,
      autoSelect: inp.auto_select,
      technical: ["steps", "cfg", "denoise", "batch_size", "scheduler", "sampler_name", "filename_prefix"].includes(
        inp.input_name
      ),
    })),
  }));

  return {
    id: workflowId,
    name: workflowName,
    source: "imported_noofy_package",
    nodes: builtNodes,
  };
}

/** Convert frontend DashboardSchema into a backend save payload. */
export function toBackendPayload(schema: DashboardSchema): BackendSavePayload {
  const normalized = normalizeDashboardSchema(schema);
  const groupedWidgetIds = groupedWidgetIdSet(normalized);
  const widgetById = new Map(normalized.widgets.map((widget) => [widget.id, widget]));
  const inputWidgets = [...normalized.widgets, ...(normalized.hiddenWidgets ?? [])];
  const inputs: BackendWorkflowInput[] = inputWidgets
    .filter((w) => !isOutputWidgetType(w.widgetType) && hasExecutableWorkflowBinding(w))
    .map((w) => ({
      id: w.id,
      label: w.title,
      control: w.widgetType,
      binding: { node_id: w.binding.nodeId, input_name: w.binding.inputName },
      default: w.defaultValue,
      default_pinned: w.defaultPinned === true,
      validation: {
        ...(w.min !== undefined && { min: w.min }),
        ...(w.max !== undefined && { max: w.max }),
        ...(w.step !== undefined && { step: w.step }),
        ...(w.options && w.options.length > 0 && { options: w.options }),
        ...(w.widgetType === "load_file" && {
          accepted_extensions:
            w.acceptedExtensions && w.acceptedExtensions.length > 0
              ? w.acceptedExtensions
              : DEFAULT_FILE_ACCEPTED_EXTENSIONS,
        }),
        ...(w.widgetType === "load_file" && w.acceptedMimeTypes && w.acceptedMimeTypes.length > 0 && {
          accepted_mime_types: w.acceptedMimeTypes,
        }),
      },
    }));

  const outputWidgets = normalized.widgets.filter((w) => isOutputWidgetType(w.widgetType));
  const outputIdForWidget = (widgetId: string) => {
    const widget = outputWidgets.find((item) => item.id === widgetId);
    const kind = mediaKindForOutputWidget(widget?.widgetType);
    const sameKindWidgets = outputWidgets.filter((item) => mediaKindForOutputWidget(item.widgetType) === kind);
    const index = sameKindWidgets.findIndex((item) => item.id === widgetId);
    return index <= 0 ? kind : `${kind}_${index + 1}`;
  };
  const outputs = outputWidgets.map((w) => ({
    id: outputIdForWidget(w.id),
    label: w.title,
    node_id: w.binding.nodeId,
    type: mediaKindForOutputWidget(w.widgetType),
    kind: mediaKindForOutputWidget(w.widgetType),
  }));

  const controls: BackendDashboardControl[] = normalized.widgets.map((widget, index) => {
    const minimum = minimumSizeForWidgetType(widget.widgetType);
    const sourceLayout = widget.layout ?? { x: 0, y: index * 4, w: 32, h: 4 };
    const layout = groupedWidgetIds.has(widget.id)
      ? undefined
      : {
          x: sourceLayout.x,
          y: sourceLayout.y,
          w: sourceLayout.w,
          h: sourceLayout.h,
          min_w: minimum.w,
          min_h: minimum.h,
        };
    return {
      id: widget.id,
      type: widget.widgetType,
      label: widget.title,
      ...(!isOutputWidgetType(widget.widgetType) && hasExecutableWorkflowBinding(widget) ? { input_id: widget.id } : {}),
      ...(isOutputWidgetType(widget.widgetType) ? { output_id: outputIdForWidget(widget.id) } : {}),
      description: widget.description,
      layout,
    };
  });
  const groups: BackendDashboardGroup[] = normalized.groups.map((group) => {
    const childTypes = group.widgetIds
      .map((widgetId) => widgetById.get(widgetId)?.widgetType)
      .filter((widgetType): widgetType is WidgetType => Boolean(widgetType));
    const minimum = minimumSizeForWidgetGroup(childTypes);
    return {
      id: group.id,
      title: group.title,
      description: group.description,
      control_ids: group.widgetIds,
      layout: group.layout
        ? {
            x: group.layout.x,
            y: group.layout.y,
            w: group.layout.w,
            h: group.layout.h,
            min_w: minimum.w,
            min_h: minimum.h,
          }
        : undefined,
    };
  });

  const dashboard: BackendDashboardPayload = {
    version: "0.1.0",
    status: "configured",
    ...(normalized.presentation?.actionBar
      ? {
          presentation: {
            action_bar: {
              x: Math.round(normalized.presentation.actionBar.x),
              y: Math.round(normalized.presentation.actionBar.y),
            },
          },
        }
      : {}),
    outputs,
    sections: [{ id: "main", title: "Main", controls, groups }],
  };

  return { inputs, dashboard };
}

function mediaKindForOutputWidget(widgetType?: string): "image" | "audio" | "video" | "3d" | "file" {
  if (widgetType === "display_audio") return "audio";
  if (widgetType === "display_video") return "video";
  if (widgetType === "display_file") return "file";
  if (widgetType === "display_3d") return "3d";
  return "image";
}

function mediaInputDefaultValue(value: WorkflowNodeValue): unknown {
  if (isPersistedMediaValue(value.rawValue)) return value.rawValue;
  return null;
}

function isUploadedAssetId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.[a-z0-9_-]+)+$/i.test(value)
  );
}

function isGalleryMediaReference(value: unknown): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { source?: unknown }).source === "gallery" &&
    typeof (value as { gallery_item_id?: unknown }).gallery_item_id === "string" &&
    ((value as { gallery_item_id: string }).gallery_item_id).trim() !== "" &&
    ["image", "audio", "video", "3d"].includes(String((value as { kind?: unknown }).kind))
  );
}

function isPackageAssetReference(value: unknown): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { source?: unknown }).source === "package_asset" &&
    typeof (value as { asset_id?: unknown }).asset_id === "string" &&
    ((value as { asset_id: string }).asset_id).trim() !== "" &&
    ["image", "audio", "video", "3d", "file"].includes(String((value as { kind?: unknown }).kind))
  );
}

function isPersistedMediaValue(value: unknown): boolean {
  return isUploadedAssetId(value) || isGalleryMediaReference(value) || isPackageAssetReference(value);
}

function isMediaInputWidgetType(widgetType: WidgetType): boolean {
  return ["load_image", "load_image_mask", "load_audio", "load_video", "load_file", "load_3d"].includes(widgetType);
}

function hasExecutableWorkflowBinding(widget: DashboardWidget): boolean {
  return widget.widgetType !== "note" || widget.hasExecutableBinding === true;
}

export function canPreserveWidgetAsHiddenInput(widget: DashboardWidget): boolean {
  if (isOutputWidgetType(widget.widgetType) || !hasExecutableWorkflowBinding(widget)) return false;
  if (!widget.binding.nodeId || !widget.binding.inputName) return false;
  if (isMediaInputWidgetType(widget.widgetType)) return isPersistedMediaValue(widget.defaultValue);
  return widget.defaultPinned === true;
}

export function buildInitialDashboard(workflow: MockWorkflow): DashboardSchema {
  const promptValue = workflow.nodes
    .flatMap((node) => node.values)
    .find((value) => value.inputName === "text" && !value.label.toLowerCase().includes("negative"));

  const widgets: DashboardWidget[] = [];

  if (promptValue) {
    const node = workflow.nodes.find((n) => n.id === promptValue.nodeId)!;
    widgets.push({
      id: `ctrl-${promptValue.id}`,
      valueId: promptValue.id,
      binding: { nodeId: promptValue.nodeId, inputName: promptValue.inputName },
      widgetType: "textarea",
      title: suggestTitle(promptValue, node.title),
      description: suggestDescription(promptValue),
      defaultValue: promptValue.rawValue,
    });
  }

  return addAutomaticDashboardWidgets(
    {
      version: 1,
      workflowId: workflow.id,
      workflowName: workflow.name,
      widgets,
      groups: [],
      layout: {
        gridColumns: 32,
        rowHeight: 32,
        gridGap: 14,
        responsive: true,
      },
    },
    workflow,
  );
}

/**
 * Whether the schema already exposes an output widget for one of the given
 * output records. A display/output widget is identified by its source node: a
 * SaveVideo node yields exactly one video output, so a second display widget on
 * the same node is always a duplicate. Matching on node id (in addition to
 * value id and binding) is important because output widgets are created through
 * different paths — the builder's auto-add uses a synthetic value id like
 * `node-75-output_video`, while a widget rebuilt from a saved dashboard uses the
 * backend output id (e.g. `video`) with an empty input name. Without node-id
 * matching those look unrelated and the output widget gets duplicated.
 */
function schemaHasOutputWidgetForRecords(
  schema: DashboardSchema,
  records: Array<{ value: WorkflowNodeValue }>,
  matchesType: (widgetType: WidgetType) => boolean,
): boolean {
  const valueIds = new Set(records.map((record) => record.value.id));
  const bindings = new Set(records.map((record) => `${record.value.nodeId}:${record.value.inputName}`));
  const nodeIds = new Set(records.map((record) => record.value.nodeId));
  return schema.widgets.some(
    (widget) =>
      matchesType(widget.widgetType) &&
      (valueIds.has(widget.valueId) ||
        bindings.has(`${widget.binding.nodeId}:${widget.binding.inputName}`) ||
        nodeIds.has(widget.binding.nodeId)),
  );
}

export function addAutomaticDashboardWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  let next = addAutomaticNoteWidgets(schema, workflow);
  next = addAutomaticImageInputWidgets(next, workflow);
  next = addAutomaticAudioInputWidgets(next, workflow);
  next = addAutomaticVideoInputWidgets(next, workflow);
  next = addAutomaticThreeDInputWidgets(next, workflow);
  next = addAutomaticFileInputWidgets(next, workflow);
  next = addAutomaticImageOutputWidget(next, workflow);
  next = addAutomaticAudioOutputWidget(next, workflow);
  next = addAutomaticVideoOutputWidget(next, workflow);
  next = addAutomaticThreeDOutputWidget(next, workflow);
  return addAutomaticFileOutputWidget(next, workflow);
}

export function addAutomaticThreeDInputWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const existingValueIds = new Set(schema.widgets.map((widget) => widget.valueId));
  const existingBindings = new Set(schema.widgets.map((widget) => `${widget.binding.nodeId}:${widget.binding.inputName}`));
  const widgets = [...schema.widgets];

  for (const node of workflow.nodes) {
    for (const value of node.values) {
      if (value.valueKind !== "three_d_input" || existingValueIds.has(value.id)) continue;
      const bindingKey = `${value.nodeId}:${value.inputName}`;
      if (existingBindings.has(bindingKey)) continue;
      widgets.push({ ...createDashboardWidgetForValue(value, node), widgetType: "load_3d", defaultValue: mediaInputDefaultValue(value) });
      existingValueIds.add(value.id);
      existingBindings.add(bindingKey);
    }
  }

  return widgets.length === schema.widgets.length ? schema : { ...schema, widgets };
}

export function addAutomaticFileInputWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const existingValueIds = new Set(schema.widgets.map((widget) => widget.valueId));
  const existingBindings = new Set(schema.widgets.map((widget) => `${widget.binding.nodeId}:${widget.binding.inputName}`));
  const widgets = [...schema.widgets];

  for (const node of workflow.nodes) {
    for (const value of node.values) {
      if (value.valueKind !== "file_input" || existingValueIds.has(value.id)) continue;
      const bindingKey = `${value.nodeId}:${value.inputName}`;
      if (existingBindings.has(bindingKey)) continue;
      widgets.push({ ...createDashboardWidgetForValue(value, node), widgetType: "load_file", defaultValue: mediaInputDefaultValue(value) });
      existingValueIds.add(value.id);
      existingBindings.add(bindingKey);
    }
  }

  return widgets.length === schema.widgets.length ? schema : { ...schema, widgets };
}

export function addAutomaticVideoInputWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const existingValueIds = new Set(schema.widgets.map((widget) => widget.valueId));
  const existingBindings = new Set(schema.widgets.map((widget) => `${widget.binding.nodeId}:${widget.binding.inputName}`));
  const widgets = [...schema.widgets];

  for (const node of workflow.nodes) {
    for (const value of node.values) {
      if (value.valueKind !== "video_input" || existingValueIds.has(value.id)) continue;
      const bindingKey = `${value.nodeId}:${value.inputName}`;
      if (existingBindings.has(bindingKey)) continue;
      widgets.push({ ...createDashboardWidgetForValue(value, node), widgetType: "load_video", defaultValue: mediaInputDefaultValue(value) });
      existingValueIds.add(value.id);
      existingBindings.add(bindingKey);
    }
  }

  return widgets.length === schema.widgets.length ? schema : { ...schema, widgets };
}

export function addAutomaticNoteWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const existingValueIds = new Set(schema.widgets.map((widget) => widget.valueId));
  const existingWidgetIds = new Set(schema.widgets.map((widget) => widget.id));
  const widgets = [...schema.widgets];

  for (const node of workflow.nodes) {
    for (const value of node.values) {
      if (
        value.valueKind !== "note" ||
        existingValueIds.has(value.id) ||
        existingWidgetIds.has(`ctrl-${value.id}`)
      ) {
        continue;
      }
      widgets.push(createDashboardWidgetForValue(value, node));
      existingValueIds.add(value.id);
      existingWidgetIds.add(`ctrl-${value.id}`);
    }
  }

  return widgets.length === schema.widgets.length ? schema : { ...schema, widgets };
}

export function addAutomaticImageInputWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const existingValueIds = new Set(schema.widgets.map((widget) => widget.valueId));
  const existingBindings = new Set(
    schema.widgets.map((widget) => `${widget.binding.nodeId}:${widget.binding.inputName}`),
  );
  const widgets = [...schema.widgets];

  for (const node of workflow.nodes) {
    for (const value of node.values) {
      if (value.valueKind !== "image_input") continue;
      if (existingValueIds.has(value.id)) continue;
      const bindingKey = `${value.nodeId}:${value.inputName}`;
      if (existingBindings.has(bindingKey)) continue;

      const widget = createDashboardWidgetForValue(value, node);
      widgets.push({
        ...widget,
        widgetType: "load_image",
        defaultValue: mediaInputDefaultValue(value),
        drawMask: undefined,
      });
      existingValueIds.add(value.id);
      existingBindings.add(bindingKey);
    }
  }

  return widgets.length === schema.widgets.length ? schema : { ...schema, widgets };
}

export function addAutomaticAudioInputWidgets(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const existingValueIds = new Set(schema.widgets.map((widget) => widget.valueId));
  const existingBindings = new Set(
    schema.widgets.map((widget) => `${widget.binding.nodeId}:${widget.binding.inputName}`),
  );
  const widgets = [...schema.widgets];

  for (const node of workflow.nodes) {
    for (const value of node.values) {
      if (value.valueKind !== "audio_input") continue;
      if (existingValueIds.has(value.id)) continue;
      const bindingKey = `${value.nodeId}:${value.inputName}`;
      if (existingBindings.has(bindingKey)) continue;

      widgets.push({
        ...createDashboardWidgetForValue(value, node),
        widgetType: "load_audio",
        defaultValue: mediaInputDefaultValue(value),
      });
      existingValueIds.add(value.id);
      existingBindings.add(bindingKey);
    }
  }

  return widgets.length === schema.widgets.length ? schema : { ...schema, widgets };
}

export function addAutomaticImageOutputWidget(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const imageOutputRecords = workflow.nodes.flatMap((node) =>
    node.values
      .filter((value) => value.valueKind === "image_output")
      .map((value) => ({ node, value })),
  );
  const selected =
    imageOutputRecords.find((record) => record.value.autoSelect) ??
    imageOutputRecords[imageOutputRecords.length - 1];
  if (!selected) return schema;
  if (schemaHasOutputWidgetForRecords(schema, imageOutputRecords, isOutputWidgetType)) {
    return schema;
  }

  const widget = createDashboardWidgetForValue(selected.value, selected.node);
  return {
    ...schema,
    widgets: [
      ...schema.widgets,
      {
        ...widget,
        widgetType: "display_image",
        defaultValue: null,
      },
    ],
  };
}

export function addAutomaticAudioOutputWidget(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const audioOutputRecords = workflow.nodes.flatMap((node) =>
    node.values
      .filter((value) => value.valueKind === "audio_output")
      .map((value) => ({ node, value })),
  );
  const selected =
    audioOutputRecords.find((record) => record.value.autoSelect) ??
    audioOutputRecords[audioOutputRecords.length - 1];
  if (!selected) return schema;
  if (schemaHasOutputWidgetForRecords(schema, audioOutputRecords, (type) => type === "display_audio")) {
    return schema;
  }

  return {
    ...schema,
    widgets: [
      ...schema.widgets,
      {
        ...createDashboardWidgetForValue(selected.value, selected.node),
        widgetType: "display_audio",
        defaultValue: null,
      },
    ],
  };
}

export function addAutomaticVideoOutputWidget(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const outputRecords = workflow.nodes.flatMap((node) =>
    node.values.filter((value) => value.valueKind === "video_output").map((value) => ({ node, value })),
  );
  const selected = outputRecords.find((record) => record.value.autoSelect) ?? outputRecords[outputRecords.length - 1];
  if (!selected) return schema;
  if (schemaHasOutputWidgetForRecords(schema, outputRecords, (type) => type === "display_video")) {
    return schema;
  }

  return {
    ...schema,
    widgets: [...schema.widgets, { ...createDashboardWidgetForValue(selected.value, selected.node), widgetType: "display_video", defaultValue: null }],
  };
}

export function addAutomaticFileOutputWidget(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const outputRecords = workflow.nodes.flatMap((node) =>
    node.values.filter((value) => value.valueKind === "file_output").map((value) => ({ node, value })),
  );
  const selected = outputRecords.find((record) => record.value.autoSelect) ?? outputRecords[outputRecords.length - 1];
  if (!selected) return schema;
  if (schemaHasOutputWidgetForRecords(schema, outputRecords, (type) => type === "display_file")) {
    return schema;
  }

  return {
    ...schema,
    widgets: [...schema.widgets, { ...createDashboardWidgetForValue(selected.value, selected.node), widgetType: "display_file", defaultValue: null }],
  };
}

export function addAutomaticThreeDOutputWidget(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  const outputRecords = workflow.nodes.flatMap((node) =>
    node.values.filter((value) => value.valueKind === "three_d_output").map((value) => ({ node, value })),
  );
  const selected = outputRecords.find((record) => record.value.autoSelect) ?? outputRecords[outputRecords.length - 1];
  if (!selected) return schema;
  if (schemaHasOutputWidgetForRecords(schema, outputRecords, (type) => type === "display_3d")) {
    return schema;
  }

  return {
    ...schema,
    widgets: [...schema.widgets, { ...createDashboardWidgetForValue(selected.value, selected.node), widgetType: "display_3d", defaultValue: null }],
  };
}

/**
 * Collapse duplicate widgets that can accumulate in stale state. Two output
 * widgets that target the same node and media kind are always redundant (a
 * SaveVideo node produces a single video output), so only the first — or the
 * placed one, if any — is kept. This makes the schema self-healing: a draft or
 * saved dashboard authored before the output dedup fix is corrected the moment
 * it is loaded, instead of resurfacing the duplicate after a reimport. Widgets
 * sharing an id are also collapsed so a corrupt schema cannot break React keys.
 */
function dedupeWidgets(widgets: DashboardWidget[]): DashboardWidget[] {
  const outputKeyOf = (widget: DashboardWidget) =>
    `${mediaKindForOutputWidget(widget.widgetType)}:${widget.binding.nodeId}`;
  const chosenOutputByKey = new Map<string, DashboardWidget>();
  for (const widget of widgets) {
    if (!isOutputWidgetType(widget.widgetType)) continue;
    const existing = chosenOutputByKey.get(outputKeyOf(widget));
    if (!existing || (!existing.layout && widget.layout)) {
      chosenOutputByKey.set(outputKeyOf(widget), widget);
    }
  }

  const seenIds = new Set<string>();
  const result: DashboardWidget[] = [];
  for (const widget of widgets) {
    if (seenIds.has(widget.id)) continue;
    if (isOutputWidgetType(widget.widgetType) && chosenOutputByKey.get(outputKeyOf(widget)) !== widget) {
      continue;
    }
    seenIds.add(widget.id);
    result.push(widget);
  }
  return result;
}

function widgetBindingKey(widget: DashboardWidget): string {
  return `${widget.binding.nodeId}:${widget.binding.inputName}`;
}

export function normalizeDashboardSchema(schema: DashboardSchema): DashboardSchema {
  const widgets = dedupeWidgets(Array.isArray(schema.widgets) ? schema.widgets : [])
    .map((widget) => widget.layout
      ? { ...widget, layout: withCurrentWidgetMinimum(widget.layout, widget.widgetType) }
      : widget);
  const visibleBindings = new Set(widgets.map(widgetBindingKey));
  const visibleIds = new Set(widgets.map((widget) => widget.id));
  const hiddenWidgets = dedupeWidgets(Array.isArray(schema.hiddenWidgets) ? schema.hiddenWidgets : [])
    .filter(canPreserveWidgetAsHiddenInput)
    .filter((widget) => !visibleIds.has(widget.id) && !visibleBindings.has(widgetBindingKey(widget)))
    .map((widget) => widget.layout
      ? { ...widget, layout: withCurrentWidgetMinimum(widget.layout, widget.widgetType) }
      : widget);
  const widgetIds = new Set(widgets.map((widget) => widget.id));
  const widgetById = new Map(widgets.map((widget) => [widget.id, widget]));
  const usedWidgetIds = new Set<string>();
  const usedGroupIds = new Set<string>();
  const groups: DashboardWidgetGroup[] = [];

  for (const rawGroup of Array.isArray(schema.groups) ? schema.groups : []) {
    if (!rawGroup || typeof rawGroup.id !== "string" || usedGroupIds.has(rawGroup.id)) continue;
    const widgetIdsForGroup: string[] = [];
    for (const widgetId of Array.isArray(rawGroup.widgetIds) ? rawGroup.widgetIds : []) {
      if (typeof widgetId !== "string") continue;
      if (!widgetIds.has(widgetId) || usedWidgetIds.has(widgetId) || widgetIdsForGroup.includes(widgetId)) continue;
      widgetIdsForGroup.push(widgetId);
    }
    if (widgetIdsForGroup.length < 2) continue;
    widgetIdsForGroup.forEach((widgetId) => usedWidgetIds.add(widgetId));
    usedGroupIds.add(rawGroup.id);
    const childTypes = widgetIdsForGroup
      .map((widgetId) => widgetById.get(widgetId)?.widgetType)
      .filter((widgetType): widgetType is WidgetType => Boolean(widgetType));
    groups.push({
      id: rawGroup.id,
      title: typeof rawGroup.title === "string" && rawGroup.title.trim() ? rawGroup.title : "Widget group",
      description: typeof rawGroup.description === "string" ? rawGroup.description : "",
      widgetIds: widgetIdsForGroup,
      layout: rawGroup.layout
        ? withCurrentWidgetGroupMinimum(rawGroup.layout, childTypes)
        : undefined,
    });
  }

  const nextSchema: DashboardSchema = {
    ...schema,
    widgets,
    groups,
  };
  if (hiddenWidgets.length > 0) {
    nextSchema.hiddenWidgets = hiddenWidgets;
  } else {
    delete nextSchema.hiddenWidgets;
  }
  return nextSchema;
}

export function removeDashboardWidgetsFromSchema(
  schema: DashboardSchema,
  widgetIds: Iterable<string>,
  keepHiddenDefaults = false,
): DashboardSchema {
  const removedIds = new Set(widgetIds);
  const removedWidgets = schema.widgets.filter((widget) => removedIds.has(widget.id));
  let widgets = schema.widgets.filter((widget) => !removedIds.has(widget.id));
  let hiddenWidgets = schema.hiddenWidgets ?? [];

  if (keepHiddenDefaults) {
    for (const removedWidget of removedWidgets) {
      if (!canPreserveWidgetAsHiddenInput(removedWidget)) continue;
      hiddenWidgets = [
        ...hiddenWidgets.filter(
          (widget) =>
            widget.id !== removedWidget.id &&
            widgetBindingKey(widget) !== widgetBindingKey(removedWidget),
        ),
        withoutWidgetLayout({ ...removedWidget, defaultPinned: true }),
      ];
    }
  }

  const groups: DashboardWidgetGroup[] = [];
  for (const group of schema.groups) {
    const nextWidgetIds = group.widgetIds.filter((id) => !removedIds.has(id));
    if (nextWidgetIds.length >= 2) {
      groups.push({ ...group, widgetIds: nextWidgetIds });
      continue;
    }
    if (nextWidgetIds.length === 1 && group.layout) {
      widgets = widgets.map((widget) =>
        widget.id === nextWidgetIds[0] ? { ...widget, layout: widget.layout ?? group.layout } : widget,
      );
    }
  }

  return normalizeDashboardSchema({ ...schema, widgets, hiddenWidgets, groups });
}

export function groupedWidgetIdSet(schema: DashboardSchema): Set<string> {
  const grouped = new Set<string>();
  for (const group of schema.groups ?? []) {
    for (const widgetId of group.widgetIds) {
      grouped.add(widgetId);
    }
  }
  return grouped;
}

export function widgetGroupIdMap(schema: DashboardSchema): Map<string, string> {
  const map = new Map<string, string>();
  for (const group of schema.groups ?? []) {
    for (const widgetId of group.widgetIds) {
      map.set(widgetId, group.id);
    }
  }
  return map;
}

export function topLevelDashboardItems(schema: DashboardSchema): DashboardTopLevelItem[] {
  const normalized = normalizeDashboardSchema(schema);
  const widgetById = new Map(normalized.widgets.map((widget) => [widget.id, widget]));
  const groupByWidgetId = widgetGroupIdMap(normalized);
  const groupById = new Map(normalized.groups.map((group) => [group.id, group]));
  const emittedGroups = new Set<string>();
  const items: DashboardTopLevelItem[] = [];

  for (const widget of normalized.widgets) {
    const groupId = groupByWidgetId.get(widget.id);
    if (!groupId) {
      items.push({ kind: "widget", id: widget.id, widget, layout: widget.layout });
      continue;
    }

    if (emittedGroups.has(groupId)) continue;
    const group = groupById.get(groupId);
    if (!group) continue;
    const widgets = group.widgetIds.map((widgetId) => widgetById.get(widgetId)).filter((item): item is DashboardWidget => Boolean(item));
    if (widgets.length < 2) continue;
    items.push({ kind: "group", id: group.id, group, widgets, layout: group.layout });
    emittedGroups.add(groupId);
  }

  return items;
}

function withoutWidgetLayout(widget: DashboardWidget): DashboardWidget {
  const { layout: _layout, ...withoutLayout } = widget;
  return withoutLayout;
}
