import type { LucideIcon } from "lucide-react";
import {
  Box,
  Brush,
  Eraser,
  Expand,
  FileAudio,
  FileText,
  FolderClock,
  ImagePlus,
  Images,
  Library,
  LucideWandSparkles,
  PackageOpen,
  Video,
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
  variantGroupId?: string;
  variants?: WorkflowCardVariant[];
}

export interface WorkflowCardVariant {
  id: string;
  label: string;
  title: string;
}

export const fallbackWorkflow: WorkflowCard = {
  id: "workflow-library-offline",
  title: "Built-in Workflows",
  description: "Noofy is reconnecting to your local workflow library.",
  category: "Workflows",
  status: "offline",
  statusLabel: "Reconnect",
  Icon: ImagePlus,
  source: "starter",
};

export const starterWorkflows: WorkflowCard[] = [
  {
    id: "text-to-image",
    title: "Text to Image",
    description: "Generate new images from a simple prompt.",
    category: "Image Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: ImagePlus,
    source: "starter",
  },
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
    id: "outpainting",
    title: "Outpainting",
    description: "Extend an image beyond its original frame.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: Expand,
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
    id: "inpainting",
    title: "Inpainting",
    description: "Edit selected parts of an image while keeping the rest intact.",
    category: "Image Editing",
    status: "planned",
    statusLabel: "Planned",
    Icon: Brush,
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
    id: "image-to-3d",
    title: "Image to 3D",
    description: "Turn a reference image into a 3D asset.",
    category: "3D Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: Box,
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
  {
    id: "text-to-audio",
    title: "Text to Audio",
    description: "Create audio from written text.",
    category: "Audio Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: FileAudio,
    source: "starter",
  },
  {
    id: "text-to-video",
    title: "Text to Video",
    description: "Generate a video from a written prompt.",
    category: "Video Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: Video,
    source: "starter",
  },
  {
    id: "text-to-text",
    title: "Text to Text",
    description: "Generate or transform text with a local language workflow.",
    category: "Text Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: FileText,
    source: "starter",
  },
  {
    id: "image-to-video",
    title: "Image to Video",
    description: "Animate a still image into a short video.",
    category: "Video Generation",
    status: "planned",
    statusLabel: "Planned",
    Icon: Video,
    source: "starter",
  },
];

export const sidebarItems = [
  { label: "Home", Icon: Library, active: true },
  { label: "Workflows", Icon: PackageOpen, active: false },
  { label: "Projects", Icon: Images, active: false },
  { label: "History", Icon: FolderClock, active: false },
];
