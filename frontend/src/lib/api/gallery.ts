import { deleteJson, getJson, putJson, resolveBackendUrl } from "./client";

/**
 * User-facing settings used when an image was generated.
 * Keys are beginner labels (e.g. "Prompt", "Style", "Aspect ratio").
 * Raw ComfyUI node data must never appear here.
 */
export type GalleryImageSettings = Record<string, string | number | boolean>;
export type GalleryGenerationSettings = Record<string, unknown>;

export interface GalleryImage {
  id: string;
  /** URL or relative path for the grid thumbnail (may be same as imageUrl) */
  thumbnailUrl: string;
  /** URL or relative path for the full-resolution image */
  imageUrl: string;
  fileState: "available" | "missing" | "degraded" | string;
  workflowId: string;
  workflowName: string;
  /** The text prompt the user typed, if applicable */
  prompt: string;
  /** ISO-8601 timestamp */
  createdAt: string;
  width: number | null;
  height: number | null;
  favorite: boolean;
  widgetTitle?: string;
  mimeType?: string | null;
  /**
   * User-facing workflow widget values at the time of generation.
   * Only values the user could edit in the Noofy workflow UI.
   */
  usedSettings: GalleryImageSettings;
  generationSettings?: GalleryGenerationSettings;
  /** Backend file reference (path or output ref) — not shown in default UI */
  fileRef: string;
}

export interface GalleryResponse {
  images: GalleryImage[];
  total: number;
}

function normalizeGalleryImage(raw: unknown): GalleryImage {
  const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const generationSettings =
    item.generation_settings && typeof item.generation_settings === "object"
      ? item.generation_settings as Record<string, unknown>
      : item.generationSettings && typeof item.generationSettings === "object"
        ? item.generationSettings as Record<string, unknown>
        : {};
  const usedSettings =
    generationSettings.settings && typeof generationSettings.settings === "object"
      ? generationSettings.settings as GalleryImageSettings
      : item.usedSettings && typeof item.usedSettings === "object"
        ? item.usedSettings as GalleryImageSettings
        : {};
  const prompt = typeof usedSettings.Prompt === "string"
    ? usedSettings.Prompt
    : typeof usedSettings.prompt === "string"
      ? usedSettings.prompt
      : typeof item.prompt === "string"
        ? item.prompt
        : "";
  const imageUrl = String(item.image_url ?? item.imageUrl ?? "");
  const fileState = String(item.file_state ?? item.fileState ?? "available");
  const thumbnailUrl = String(item.thumbnail_url ?? item.thumbnailUrl ?? imageUrl);
  return {
    id: String(item.id ?? ""),
    thumbnailUrl: (fileState === "degraded" ? imageUrl : thumbnailUrl)
      ? resolveBackendUrl(fileState === "degraded" ? imageUrl : thumbnailUrl, { includeToken: true })
      : "",
    imageUrl: imageUrl ? resolveBackendUrl(imageUrl, { includeToken: true }) : "",
    fileState,
    workflowId: String(item.workflow_id ?? item.workflowId ?? ""),
    workflowName: String(item.workflow_title ?? item.workflowName ?? ""),
    prompt,
    createdAt: String(item.created_at ?? item.createdAt ?? ""),
    width: typeof item.width === "number" ? item.width : null,
    height: typeof item.height === "number" ? item.height : null,
    favorite: Boolean(item.favorite),
    widgetTitle: typeof item.widget_title === "string" ? item.widget_title : undefined,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    usedSettings,
    generationSettings,
    fileRef: String(item.image_rel_path ?? item.fileRef ?? ""),
  };
}

export async function fetchGallery(): Promise<GalleryResponse> {
  const data = await getJson<{ images: unknown[]; total: number }>("/gallery");
  return {
    images: Array.isArray(data.images) ? data.images.map(normalizeGalleryImage) : [],
    total: data.total,
  };
}

export async function fetchGalleryItem(itemId: string): Promise<GalleryImage> {
  return normalizeGalleryImage(await getJson<unknown>(`/gallery/${encodeURIComponent(itemId)}`));
}

export async function updateGalleryFavorite(itemId: string, favorite: boolean): Promise<GalleryImage> {
  return normalizeGalleryImage(await putJson<unknown>(`/gallery/${encodeURIComponent(itemId)}/favorite`, { favorite }));
}

export function deleteGalleryItem(itemId: string): Promise<{ id: string; deleted: boolean }> {
  return deleteJson<{ id: string; deleted: boolean }>(`/gallery/${encodeURIComponent(itemId)}`);
}
