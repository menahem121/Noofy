export interface OutputAudioMedia {
  url: string;
  filename: string;
  mimeType?: string | null;
  durationSeconds?: number | null;
  size?: number | null;
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

function audioFormatFromMime(mimeType: string | null | undefined): string | null {
  if (!mimeType) return null;
  if (mimeType.includes("mpeg")) return "MP3";
  if (mimeType.includes("wav")) return "WAV";
  if (mimeType.includes("flac")) return "FLAC";
  if (mimeType.includes("ogg")) return "OGG";
  if (mimeType.includes("mp4") || mimeType.includes("m4a")) return "M4A";
  return mimeType.replace(/^audio\//, "").toUpperCase();
}

function formatMediaBytes(bytes: number): string | null {
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

function formatMediaDuration(seconds: number): string | null {
  if (!Number.isFinite(seconds)) return null;
  const rounded = Math.max(0, Math.round(seconds));
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}
