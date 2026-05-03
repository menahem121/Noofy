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

export type ControlType =
  | "slider"
  | "int_field"
  | "string_field"
  | "textarea"
  | "toggle"
  | "load_image"
  | "load_image_mask"
  | "display_image"
  | "seed_control"
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

export type ControlGroup = "simple" | "advanced";

export interface DashboardControl {
  id: string;
  binding: { nodeId: string; inputName: string };
  valueId: string;
  controlType: ControlType;
  title: string;
  description: string;
  orientation: "vertical" | "horizontal";
  group: ControlGroup;
  defaultValue: unknown;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  showDownload?: boolean;
  drawMask?: boolean;
}

export interface DashboardSchema {
  version: number;
  workflowId: string;
  workflowName: string;
  controls: DashboardControl[];
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

export const CONTROL_TYPE_LABELS: Record<ControlType, string> = {
  slider: "Slider",
  int_field: "Number field",
  string_field: "Single line text",
  textarea: "Multi-line text",
  toggle: "On / off",
  load_image: "Load image",
  load_image_mask: "Load image with mask",
  display_image: "Display image",
  seed_control: "Variation ID (seed)",
  lora_loader: "LoRA loader",
  select: "Dropdown",
};

const INPUT_CONTROL_TYPES: ControlType[] = [
  "slider",
  "int_field",
  "string_field",
  "textarea",
  "toggle",
  "load_image",
  "load_image_mask",
  "seed_control",
  "lora_loader",
  "select",
];

const OUTPUT_CONTROL_TYPES: ControlType[] = ["display_image"];

export function controlTypesForKind(kind: WorkflowValueKind): ControlType[] {
  if (kind === "image_output") {
    return OUTPUT_CONTROL_TYPES;
  }

  if (kind === "image_input") {
    return ["load_image", "load_image_mask"];
  }

  if (kind === "seed") {
    return ["seed_control", "int_field", "slider"];
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

  return INPUT_CONTROL_TYPES;
}

export function suggestControlType(value: WorkflowNodeValue): ControlType {
  if (value.valueKind === "image_output") return "display_image";
  if (value.valueKind === "image_input") return "load_image";
  if (value.valueKind === "seed") return "seed_control";
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
    text: "Describe what you want to see.",
    prompt: "Describe what you want to see.",
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

export function defaultGroupFor(value: WorkflowNodeValue): ControlGroup {
  if (value.technical) return "advanced";
  if (value.valueKind === "seed") return "advanced";
  if (["denoise", "cfg", "steps", "sampler", "scheduler"].includes(value.inputName)) {
    return "advanced";
  }
  return "simple";
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

export function buildInitialDashboard(workflow: MockWorkflow): DashboardSchema {
  const promptValue = workflow.nodes
    .flatMap((node) => node.values)
    .find((value) => value.inputName === "text" && !value.label.toLowerCase().includes("negative"));

  const controls: DashboardControl[] = [];

  if (promptValue) {
    const node = workflow.nodes.find((n) => n.id === promptValue.nodeId)!;
    controls.push({
      id: `ctrl-${promptValue.id}`,
      valueId: promptValue.id,
      binding: { nodeId: promptValue.nodeId, inputName: promptValue.inputName },
      controlType: "textarea",
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
    controls,
  };
}
