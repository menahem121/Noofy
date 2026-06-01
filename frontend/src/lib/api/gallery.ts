import { deleteJson, getJson, putJson, resolveBackendUrl } from "./client";

export type GalleryKind = "image" | "video" | "audio" | "3d" | "file";
export type GalleryGenerationSettings = Record<string, unknown>;
export type GalleryUsedSettings = Record<string, string | number | boolean>;

export interface GalleryItem {
  id: string;
  kind: GalleryKind;
  type: GalleryKind;
  contentUrl: string;
  thumbnailUrl: string | null;
  fileState: "available" | "missing" | "degraded" | string;
  workflowId: string;
  workflowName: string;
  jobId: string;
  controlId: string;
  outputId: string;
  widgetTitle: string;
  filename: string;
  mimeType: string | null;
  extension: string | null;
  sizeBytes: number | null;
  prompt: string;
  createdAt: string;
  width: number | null;
  height: number | null;
  durationSeconds: number | null;
  fps: number | null;
  favorite: boolean;
  usedSettings: GalleryUsedSettings;
  generationSettings: GalleryGenerationSettings;
}

export interface GalleryResponse {
  items: GalleryItem[];
  total: number;
}

export function galleryContentUrl(item: GalleryItem, options: { download?: boolean } = {}): string {
  if (!item.contentUrl) return "";
  const url = resolveBackendUrl(item.contentUrl, { includeToken: true });
  if (!options.download) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}download=true`;
}

export function galleryThumbnailUrl(item: GalleryItem): string {
  if (!item.thumbnailUrl) return "";
  return resolveBackendUrl(item.thumbnailUrl, { includeToken: true });
}

export function galleryPreviewUrl(item: GalleryItem): string {
  if (item.fileState === "missing") return "";
  if (item.fileState !== "degraded" && item.thumbnailUrl) return galleryThumbnailUrl(item);
  return galleryContentUrl(item);
}

function galleryKind(value: unknown): GalleryKind {
  return value === "video" || value === "audio" || value === "3d" || value === "file" ? value : "image";
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeGalleryItem(raw: unknown): GalleryItem {
  const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const generationSettings =
    item.generation_settings && typeof item.generation_settings === "object"
      ? item.generation_settings as Record<string, unknown>
      : item.generationSettings && typeof item.generationSettings === "object"
        ? item.generationSettings as Record<string, unknown>
        : {};
  const usedSettings =
    generationSettings.settings && typeof generationSettings.settings === "object"
      ? generationSettings.settings as GalleryUsedSettings
      : {};
  const prompt = typeof usedSettings.Prompt === "string"
    ? usedSettings.Prompt
    : typeof usedSettings.prompt === "string"
      ? usedSettings.prompt
      : "";
  const kind = galleryKind(item.kind ?? item.type);
  return {
    id: String(item.id ?? ""),
    kind,
    type: kind,
    contentUrl: String(item.content_url ?? item.url ?? ""),
    thumbnailUrl: optionalString(item.thumbnail_url),
    fileState: String(item.file_state ?? "available"),
    workflowId: String(item.workflow_id ?? ""),
    workflowName: String(item.workflow_title ?? "Workflow"),
    jobId: String(item.job_id ?? ""),
    controlId: String(item.control_id ?? ""),
    outputId: String(item.output_id ?? ""),
    widgetTitle: String(item.widget_title ?? ""),
    filename: String(item.filename ?? "Generated output"),
    mimeType: optionalString(item.mime_type),
    extension: optionalString(item.extension),
    sizeBytes: optionalNumber(item.size_bytes),
    prompt,
    createdAt: String(item.created_at ?? ""),
    width: optionalNumber(item.width),
    height: optionalNumber(item.height),
    durationSeconds: optionalNumber(item.duration_seconds),
    fps: optionalNumber(item.fps),
    favorite: Boolean(item.favorite),
    usedSettings,
    generationSettings,
  };
}

export async function fetchGallery(): Promise<GalleryResponse> {
  const data = await getJson<{ items?: unknown[]; total: number }>("/gallery");
  const items = Array.isArray(data.items) ? data.items : [];
  return { items: items.map(normalizeGalleryItem), total: data.total };
}

export async function fetchGalleryItem(itemId: string): Promise<GalleryItem> {
  return normalizeGalleryItem(await getJson<unknown>(`/gallery/${encodeURIComponent(itemId)}`));
}

export async function updateGalleryFavorite(itemId: string, favorite: boolean): Promise<GalleryItem> {
  return normalizeGalleryItem(await putJson<unknown>(`/gallery/${encodeURIComponent(itemId)}/favorite`, { favorite }));
}

export function deleteGalleryItem(itemId: string): Promise<{ id: string; deleted: boolean }> {
  return deleteJson<{ id: string; deleted: boolean }>(`/gallery/${encodeURIComponent(itemId)}`);
}
