// Shared formatting contracts for dashboard media inputs and outputs.
export interface OutputAudioMedia {
  url: string;
  filename: string;
  mimeType?: string | null;
  durationSeconds?: number | null;
  size?: number | null;
}

export interface OutputVideoMedia {
  url: string;
  filename: string;
  thumbnailUrl?: string | null;
  mimeType?: string | null;
  durationSeconds?: number | null;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  size?: number | null;
}

export interface OutputFileMedia {
  url: string;
  filename: string;
  mimeType?: string | null;
  extension?: string | null;
  size?: number | null;
}

export interface OutputThreeDMedia {
  url: string;
  filename: string;
  thumbnailUrl?: string | null;
  mimeType?: string | null;
  extension?: string | null;
  size?: number | null;
}

export interface GalleryMediaReference {
  source: "gallery";
  gallery_item_id: string;
  kind: "image" | "audio" | "video" | "3d";
  filename: string;
  extension?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  fps?: number | null;
}

const uploadedAssetPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.[A-Za-z0-9_-]+)+$/i;

export function isUploadedAssetValue(value: unknown): value is string {
  return typeof value === "string" && uploadedAssetPattern.test(value);
}

export function isGalleryMediaReference(value: unknown): value is GalleryMediaReference {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    record.source === "gallery" &&
    typeof record.gallery_item_id === "string" &&
    record.gallery_item_id.trim().length > 0 &&
    (record.kind === "image" || record.kind === "audio" || record.kind === "video" || record.kind === "3d") &&
    typeof record.filename === "string"
  );
}

export function audioMetadataLabel(
  format: string | null | undefined,
  mimeType: string | null | undefined,
  size: number | null | undefined,
  durationSeconds: number | null | undefined,
  fallback: string,
): string {
  const parts = [
    format?.toUpperCase() ?? audioFormatFromMime(mimeType),
    typeof size === "number" ? formatMediaBytes(size) : null,
    typeof durationSeconds === "number" ? formatMediaDuration(durationSeconds) : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : fallback;
}

export function videoMetadataLabel(
  format: string | null | undefined,
  mimeType: string | null | undefined,
  size: number | null | undefined,
  durationSeconds: number | null | undefined,
  width: number | null | undefined,
  height: number | null | undefined,
  fps: number | null | undefined,
  fallback: string,
): string {
  const parts = [
    format?.toUpperCase() ?? videoFormatFromMime(mimeType),
    typeof width === "number" && typeof height === "number" ? `${width} × ${height}` : null,
    typeof fps === "number" && Number.isFinite(fps) ? `${Number.isInteger(fps) ? fps : fps.toFixed(2)} fps` : null,
    typeof size === "number" ? formatMediaBytes(size) : null,
    typeof durationSeconds === "number" ? formatMediaDuration(durationSeconds) : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : fallback;
}

export function fileMetadataLabel(
  extension: string | null | undefined,
  mimeType: string | null | undefined,
  size: number | null | undefined,
  fallback: string,
): string {
  const parts = [
    extension ? extension.replace(/^\./, "").toUpperCase() : fileFormatFromMime(mimeType),
    typeof size === "number" ? formatMediaBytes(size) : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : fallback;
}

function audioFormatFromMime(mimeType: string | null | undefined): string | null {
  if (!mimeType) return null;
  if (mimeType.includes("mpeg")) return "MP3";
  if (mimeType.includes("wav")) return "WAV";
  if (mimeType.includes("flac")) return "FLAC";
  if (mimeType.includes("ogg")) return "OGG";
  if (mimeType.includes("mp4") || mimeType.includes("m4a")) return "M4A";
  return mimeType.replace(/^audio\//, "").toUpperCase();
}

function videoFormatFromMime(mimeType: string | null | undefined): string | null {
  if (!mimeType) return null;
  if (mimeType.includes("quicktime")) return "MOV";
  if (mimeType.includes("webm")) return "WEBM";
  if (mimeType.includes("matroska")) return "MKV";
  if (mimeType.includes("mp4")) return "MP4";
  return mimeType.replace(/^video\//, "").toUpperCase();
}

function fileFormatFromMime(mimeType: string | null | undefined): string | null {
  if (!mimeType || mimeType === "application/octet-stream") return null;
  const subtype = mimeType.split("/")[1] ?? mimeType;
  return subtype.replace(/^x-/, "").toUpperCase();
}

export function formatMediaBytes(bytes: number): string | null {
  if (!Number.isFinite(bytes) || bytes < 0) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(Number.isInteger(value) || value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export function formatMediaDuration(seconds: number): string | null {
  if (!Number.isFinite(seconds)) return null;
  const rounded = Math.max(0, Math.round(seconds));
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}
