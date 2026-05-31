import type { LucideIcon } from "lucide-react";
import {
  Brush,
  Eraser,
  Expand,
  FolderClock,
  ImagePlus,
  Images,
  Library,
  LucideWandSparkles,
  PackageOpen,
  Wand2,
} from "lucide-react";
import type { WorkflowHardwareWarning } from "../../lib/api/noofyApi";

export type WorkflowStatus =
  | "installed"
  | "ready"
  | "download"
  | "planned"
  | "offline"
  | "imported"
  | "needs_input_setup"
  | "cannot_prepare_automatically";

export interface WorkflowCard {
  id: string;
  title: string;
  description: string;
  category: string;
  status: WorkflowStatus;
  statusLabel: string;
  trustLabel?: string;
  trustTone?: string;
  trustSummary?: string;
  canRemove?: boolean;
  canExportNoofy?: boolean;
  canExportComfyJson?: boolean;
  hardwareWarning?: WorkflowHardwareWarning | null;
  icon?: string;
  Icon: LucideIcon;
  source: "backend" | "starter";
  variants?: WorkflowCardVariant[];
}

export interface WorkflowCardVariant {
  id: string;
  label: string;
  title: string;
}

export const fallbackWorkflow: WorkflowCard = {
  id: "text_to_image_v0",
  title: "Text to Image",
  description: "Generate a new image from a simple text prompt.",
  category: "Image Generation",
  status: "offline",
  statusLabel: "Reconnect",
  Icon: ImagePlus,
  source: "starter",
};

export const starterWorkflows: WorkflowCard[] = [
  {
    id: "remove-background",
    title: "Remove Background",
    description: "Remove the background from an image automatically.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: Eraser,
    source: "starter",
  },
  {
    id: "erase-object",
    title: "Erase Object",
    description: "Brush over something unwanted and fill the scene naturally.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: Brush,
    source: "starter",
  },
  {
    id: "upscale-image",
    title: "Upscale Image",
    description: "Increase image size while preserving important detail.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: Expand,
    source: "starter",
  },
  {
    id: "enhance-image",
    title: "Enhance Image",
    description: "Improve lighting, color balance, and clarity in one pass.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: LucideWandSparkles,
    source: "starter",
  },
  {
    id: "image-to-image",
    title: "Image to Image",
    description: "Use a reference image to guide a new generation.",
    category: "Image Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: Images,
    source: "starter",
  },
  {
    id: "replace-background",
    title: "Replace Background",
    description: "Keep the subject and create a new background scene.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: Wand2,
    source: "starter",
  },
];

export const sidebarItems = [
  { label: "Home", Icon: Library, active: true },
  { label: "Workflows", Icon: PackageOpen, active: false },
  { label: "Projects", Icon: Images, active: false },
  { label: "History", Icon: FolderClock, active: false },
];
