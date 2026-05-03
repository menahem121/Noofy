export type HistoryEventType = "run" | "workflow_installed" | "workflow_removed";
export type HistoryEventStatus =
  | "completed"
  | "failed"
  | "canceled"
  | "installed"
  | "removed"
  | "preparing"
  | "ready";

export interface HistoryEvent {
  id: string;
  type: HistoryEventType;
  workflowId: string;
  workflowName: string;
  status: HistoryEventStatus;
  title: string;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  durationSeconds?: number;
  thumbnailUrl?: string;
  outputRef?: string;
  prompt?: string;
  usedSettings?: Record<string, string | number | boolean>;
  peakRamMb?: number;
  peakVramMb?: number;
  source?: string;
  trustLevel?: string;
  errorSummary?: string;
  developerDetails?: Record<string, string>;
}

export const EVENT_TYPE_LABELS: Record<HistoryEventType, string> = {
  run: "Workflow run",
  workflow_installed: "Workflow installed",
  workflow_removed: "Workflow removed",
};

export const EVENT_STATUS_LABELS: Record<HistoryEventStatus, string> = {
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
  installed: "Installed",
  removed: "Removed",
  preparing: "Preparing",
  ready: "Ready",
};

const now = new Date();

function ago(minutes: number): string {
  return new Date(now.getTime() - minutes * 60 * 1000).toISOString();
}

function daysAgo(days: number, hoursOffset = 0): string {
  return new Date(now.getTime() - (days * 24 * 60 + hoursOffset * 60) * 60 * 1000).toISOString();
}

export const MOCK_HISTORY: HistoryEvent[] = [
  {
    id: "evt_001",
    type: "run",
    workflowId: "wf_portrait",
    workflowName: "Realistic Portrait",
    status: "completed",
    title: "Image generated",
    createdAt: ago(14),
    startedAt: ago(15),
    completedAt: ago(14),
    durationSeconds: 42,
    thumbnailUrl: "https://picsum.photos/seed/portrait1/320/320",
    outputRef: "output/portrait_001.png",
    prompt: "A photorealistic portrait of a woman with short dark hair, soft studio lighting, shallow depth of field",
    usedSettings: { steps: 30, cfg: 7.5, width: 512, height: 768, sampler: "DPM++ 2M Karras" },
    peakRamMb: 5324,
    peakVramMb: 7982,
  },
  {
    id: "evt_002",
    type: "run",
    workflowId: "wf_portrait",
    workflowName: "Realistic Portrait",
    status: "completed",
    title: "Image generated",
    createdAt: ago(47),
    startedAt: ago(48),
    completedAt: ago(47),
    durationSeconds: 38,
    thumbnailUrl: "https://picsum.photos/seed/portrait2/320/320",
    outputRef: "output/portrait_002.png",
    prompt: "An elderly man with weathered features, dramatic side lighting, black and white",
    usedSettings: { steps: 25, cfg: 8, width: 512, height: 768, sampler: "Euler a" },
    peakRamMb: 5100,
    peakVramMb: 7750,
  },
  {
    id: "evt_003",
    type: "run",
    workflowId: "wf_landscape",
    workflowName: "Fantasy Landscape",
    status: "failed",
    title: "Generation failed",
    createdAt: ago(95),
    startedAt: ago(96),
    completedAt: ago(95),
    durationSeconds: 8,
    errorSummary: "VRAM out of memory. Try lowering the resolution or reducing the step count.",
    usedSettings: { steps: 40, cfg: 9, width: 1024, height: 1024 },
    peakRamMb: 4900,
    peakVramMb: 12288,
    developerDetails: {
      job_id: "job_abc123",
      error_code: "OOM",
      failing_node: "KSampler",
      engine_version: "ComfyUI 0.3.2",
    },
  },
  {
    id: "evt_004",
    type: "workflow_installed",
    workflowId: "wf_bg_remover",
    workflowName: "Background Remover",
    status: "installed",
    title: "Workflow installed",
    createdAt: daysAgo(1, 2),
    source: "Noofy Marketplace",
    trustLevel: "verified",
  },
  {
    id: "evt_005",
    type: "run",
    workflowId: "wf_upscale",
    workflowName: "Upscale Image",
    status: "completed",
    title: "Image upscaled",
    createdAt: daysAgo(1, 5),
    startedAt: daysAgo(1, 5),
    completedAt: daysAgo(1, 5),
    durationSeconds: 21,
    thumbnailUrl: "https://picsum.photos/seed/upscale1/320/320",
    outputRef: "output/upscaled_001.png",
    usedSettings: { scale: 2, model: "RealESRGAN_x4plus", tile_size: 512 },
    peakRamMb: 3200,
    peakVramMb: 4100,
  },
  {
    id: "evt_006",
    type: "run",
    workflowId: "wf_portrait",
    workflowName: "Realistic Portrait",
    status: "canceled",
    title: "Generation canceled",
    createdAt: daysAgo(1, 8),
    startedAt: daysAgo(1, 8),
    durationSeconds: 5,
    usedSettings: { steps: 30, cfg: 7.5, width: 512, height: 768 },
  },
  {
    id: "evt_007",
    type: "workflow_installed",
    workflowId: "wf_landscape",
    workflowName: "Fantasy Landscape",
    status: "installed",
    title: "Workflow installed",
    createdAt: daysAgo(3),
    source: "Noofy Marketplace",
    trustLevel: "verified",
  },
  {
    id: "evt_008",
    type: "run",
    workflowId: "wf_bg_remover",
    workflowName: "Background Remover",
    status: "completed",
    title: "Background removed",
    createdAt: daysAgo(4),
    startedAt: daysAgo(4),
    completedAt: daysAgo(4),
    durationSeconds: 14,
    thumbnailUrl: "https://picsum.photos/seed/bgremover1/320/320",
    outputRef: "output/bg_removed_001.png",
    peakRamMb: 2800,
    peakVramMb: 3500,
  },
  {
    id: "evt_009",
    type: "workflow_removed",
    workflowId: "wf_old_upscaler",
    workflowName: "Old Upscaler v1",
    status: "removed",
    title: "Workflow removed",
    createdAt: daysAgo(7),
  },
  {
    id: "evt_010",
    type: "run",
    workflowId: "wf_portrait",
    workflowName: "Realistic Portrait",
    status: "completed",
    title: "Image generated",
    createdAt: daysAgo(14),
    startedAt: daysAgo(14),
    completedAt: daysAgo(14),
    durationSeconds: 55,
    thumbnailUrl: "https://picsum.photos/seed/portrait3/320/320",
    outputRef: "output/portrait_003.png",
    prompt: "A young man with curly auburn hair, golden hour sunlight, cinematic color grading",
    usedSettings: { steps: 35, cfg: 7, width: 512, height: 768, sampler: "DPM++ SDE" },
    peakRamMb: 5500,
    peakVramMb: 8200,
  },
];
