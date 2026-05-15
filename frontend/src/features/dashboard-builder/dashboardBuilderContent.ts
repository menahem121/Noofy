import type { LucideIcon } from "lucide-react";
import {
  Cpu,
  Image as ImageIcon,
  ImagePlus,
  Layers,
  Save,
  Shuffle,
  SlidersHorizontal,
  Sparkles,
  Type,
} from "lucide-react";

export type WidgetType =
  | "slider"
  | "int_field"
  | "string_field"
  | "textarea"
  | "toggle"
  | "load_image"
  | "load_image_mask"
  | "display_image"
  | "seed_widget"
  | "lora_loader"
  | "select";

export type WorkflowValueKind =
  | "string"
  | "number"
  | "boolean"
  | "image_input"
  | "image_output"
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
  technical?: boolean;
}

export type NodeIconKind = "text" | "sampler" | "image" | "image-input" | "lora" | "tune" | "output" | "save";

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

export type WidgetGroup = "simple" | "advanced";

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

export interface DashboardWidget {
  id: string;
  binding: { nodeId: string; inputName: string };
  valueId: string;
  widgetType: WidgetType;
  title: string;
  description: string;
  orientation: "vertical" | "horizontal";
  group: WidgetGroup;
  defaultValue: unknown;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  showDownload?: boolean;
  drawMask?: boolean;
  layout?: DashboardWidgetLayout;
}

export interface DashboardSchema {
  version: number;
  workflowId: string;
  workflowName: string;
  widgets: DashboardWidget[];
  layout: DashboardCanvasLayout;
}

export type DashboardDraftStatus = "draft" | "configured";

export function dashboardDraftKey(workflowId: string) {
  return `noofy.builderDraft.${workflowId}`;
}

export function saveDashboardDraft(schema: DashboardSchema, status: DashboardDraftStatus = "draft") {
  window.localStorage.setItem(dashboardDraftKey(schema.workflowId), JSON.stringify({ ...schema, status }));
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
    return parsed as DashboardSchema;
  } catch {
    return null;
  }
}

export function clearDashboardDraft(workflowId: string) {
  window.localStorage.removeItem(dashboardDraftKey(workflowId));
}

export const NODE_ICONS: Record<NodeIconKind, LucideIcon> = {
  text: Type,
  sampler: Shuffle,
  image: ImageIcon,
  "image-input": ImagePlus,
  lora: Sparkles,
  tune: SlidersHorizontal,
  output: ImageIcon,
  save: Save,
};

export const VALUE_KIND_ICONS: Record<WorkflowValueKind, LucideIcon> = {
  string: Type,
  number: SlidersHorizontal,
  boolean: SlidersHorizontal,
  image_input: ImagePlus,
  image_output: ImageIcon,
  seed: Shuffle,
  lora: Sparkles,
  select: Layers,
};

export const WIDGET_TYPE_LABELS: Record<WidgetType, string> = {
  slider: "Slider",
  int_field: "Number field",
  string_field: "Single line text",
  textarea: "Multi-line text",
  toggle: "On / off",
  load_image: "Load image",
  load_image_mask: "Load image with mask",
  display_image: "Display image",
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
  "seed_widget",
  "lora_loader",
  "select",
];

const OUTPUT_WIDGET_TYPES: WidgetType[] = ["display_image"];

export function widgetTypesForKind(kind: WorkflowValueKind): WidgetType[] {
  if (kind === "image_output") {
    return OUTPUT_WIDGET_TYPES;
  }

  if (kind === "image_input") {
    return ["load_image", "load_image_mask"];
  }

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
  if (value.valueKind === "image_output") return "display_image";
  if (value.valueKind === "image_input") return "load_image";
  if (value.valueKind === "seed") return "seed_widget";
  if (value.valueKind === "lora") return "lora_loader";
  if (value.valueKind === "boolean") return "toggle";
  if (value.valueKind === "select") return "select";

  if (value.valueKind === "number") {
    return value.numberRange ? "slider" : "int_field";
  }

  if (value.valueKind === "string") {
    if (typeof value.rawValue === "string" && value.rawValue.length > 32) {
      return "textarea";
    }
    return "string_field";
  }

  return "string_field";
}

export function suggestTitle(value: WorkflowNodeValue, nodeTitle: string): string {
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
    lora_name: "LoRA model",
    filename_prefix: "Save name",
  };

  const key = value.inputName.toLowerCase();
  if (labelMap[key]) {
    return labelMap[key];
  }

  return value.label.charAt(0).toUpperCase() + value.label.slice(1).replace(/_/g, " ");
}

export function suggestDescription(value: WorkflowNodeValue): string {
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
    lora_name: "Pick a LoRA style to apply.",
  };

  const key = value.inputName.toLowerCase();
  return hints[key] ?? value.hint ?? "";
}

export function defaultGroupFor(value: WorkflowNodeValue): WidgetGroup {
  if (value.technical) return "advanced";
  if (value.valueKind === "seed") return "advanced";
  if (["denoise", "cfg", "steps", "sampler", "scheduler"].includes(value.inputName)) {
    return "advanced";
  }
  return "simple";
}

export function createDashboardWidgetForValue(value: WorkflowNodeValue, node: WorkflowNode): DashboardWidget {
  const widgetType = suggestWidgetType(value);
  return {
    id: `ctrl-${value.id}`,
    valueId: value.id,
    binding: { nodeId: value.nodeId, inputName: value.inputName },
    widgetType,
    title: suggestTitle(value, node.title),
    description: suggestDescription(value),
    orientation: "vertical",
    group: defaultGroupFor(value),
    defaultValue: value.rawValue,
    options: value.options,
    min: value.numberRange?.min,
    max: value.numberRange?.max,
    step: value.numberRange?.step,
    showDownload: widgetType === "display_image" ? true : undefined,
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
  validation: Record<string, unknown>;
}

export interface BackendDashboardControl {
  id: string;
  type: string;
  label: string;
  input_id?: string;
  output_id?: string;
  description?: string;
  group?: string;
  show_download?: boolean;
  layout?: { x: number; y: number; w: number; h: number; min_w?: number; min_h?: number };
}

export interface BackendDashboardSection {
  id: string;
  title: string;
  controls: BackendDashboardControl[];
}

export interface BackendDashboardPayload {
  version: string;
  status?: string;
  outputs?: Array<{ id: string; label: string; node_id: string; type: string }>;
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
    is_image_node: boolean;
    is_lora_node: boolean;
    inputs: Array<{
      input_name: string;
      current_value: unknown;
      kind: string;
      suggested_widget_type: string;
      widget_types: string[];
      options?: string[];
      hint?: string;
    }>;
  }>
): MockWorkflow {
  function nodeIconKind(nodeType: string, isImageNode: boolean, isLoraNode: boolean): NodeIconKind {
    if (isImageNode) return "image-input";
    if (isLoraNode) return "lora";
    const t = nodeType.toLowerCase();
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
    title: node.node_type,
    iconKind: nodeIconKind(node.node_type, node.is_image_node, node.is_lora_node),
    values: node.inputs.map((inp) => ({
      id: `node-${node.node_id}-${inp.input_name}`,
      nodeId: node.node_id,
      inputName: inp.input_name,
      label: inp.input_name,
      valueKind: valueKindFromString(inp.kind),
      rawValue: inp.current_value,
      hint: inp.hint,
      options: inp.options,
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
  const isOutputWidget = (widgetType: string) => widgetType === "display_image" || widgetType === "result_image";
  const inputs: BackendWorkflowInput[] = schema.widgets
    .filter((w) => !isOutputWidget(w.widgetType))
    .map((w) => ({
      id: w.id,
      label: w.title,
      control: w.widgetType,
      binding: { node_id: w.binding.nodeId, input_name: w.binding.inputName },
      default: w.defaultValue,
      validation: {
        ...(w.min !== undefined && { min: w.min }),
        ...(w.max !== undefined && { max: w.max }),
        ...(w.step !== undefined && { step: w.step }),
        ...(w.options && w.options.length > 0 && { options: w.options }),
      },
    }));

  const outputWidgets = schema.widgets.filter((w) => isOutputWidget(w.widgetType));
  const outputIdForWidget = (widgetId: string) => {
    const index = outputWidgets.findIndex((widget) => widget.id === widgetId);
    return index <= 0 ? "image" : `image_${index + 1}`;
  };
  const outputs = outputWidgets.map((w) => ({
    id: outputIdForWidget(w.id),
    label: w.title,
    node_id: w.binding.nodeId,
    type: "image",
  }));

  const controls: BackendDashboardControl[] = schema.widgets.map((w, i) => ({
    id: w.id,
    type: w.widgetType,
    label: w.title,
    input_id: !isOutputWidget(w.widgetType) ? w.id : undefined,
    output_id: isOutputWidget(w.widgetType) ? outputIdForWidget(w.id) : undefined,
    description: w.description,
    group: w.group,
    show_download: Boolean(w.showDownload),
    layout: w.layout
      ? { x: w.layout.x, y: w.layout.y, w: w.layout.w, h: w.layout.h, min_w: w.layout.minW, min_h: w.layout.minH }
      : { x: 0, y: i * 4, w: 32, h: 4 },
  }));

  const dashboard: BackendDashboardPayload = {
    version: "0.1.0",
    status: "configured",
    outputs,
    sections: [{ id: "main", title: "Main", controls }],
  };

  return { inputs, dashboard };
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
      orientation: "vertical",
      group: "simple",
      defaultValue: promptValue.rawValue,
    });
  }

  return {
    version: 1,
    workflowId: workflow.id,
    workflowName: workflow.name,
    widgets,
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
  };
}
