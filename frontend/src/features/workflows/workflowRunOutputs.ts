import {
  fetchAssetMetadata,
  resolveBackendUrl,
  type DashboardControlDef,
  type GallerySaveRequest,
  type JobResult,
  type WorkflowInputDef,
  type WorkflowOutputDef,
  type WorkflowPackageResponse,
} from "../../lib/api/noofyApi";
import {
  audioMetadataLabel,
  fileMetadataLabel,
  isGalleryMediaReference,
  isPackageAssetReference,
  isUploadedAssetValue,
  videoMetadataLabel,
  type OutputAudioMedia,
  type OutputFileMedia,
  type OutputThreeDMedia,
  type OutputVideoMedia,
} from "./media";
import { terminalStatuses } from "./workflowRunTracking";
import type { ComparisonImageSource } from "./workflowRunStateTypes";

const comparisonImageInputControlTypes = new Set(["load_image", "load_image_mask"]);

export function extractImageUrls(result: JobResult | null) {
  if (!result) return [];
  const urls: string[] = [];
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object" || !("images" in outputPayload)) continue;
    const images = outputPayload.images;
    if (!Array.isArray(images)) continue;
    for (const image of images) {
      if (mediaOutputKind(image, "image") === "image" && image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
        urls.push(resolveBackendUrl(image.view_url, { includeToken: true }));
      }
    }
  }
  return urls;
}

export function extractImageUrlsByNodeId(result: JobResult | null): Map<string, string[]> {
  const map = new Map<string, string[]>();
  if (!result) return map;
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object") continue;
    const nodeIdKey = Object.keys(output).find((key) => key !== "output");
    const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey;
    if (!nodeId) continue;
    const images = (outputPayload as Record<string, unknown>).images;
    if (!Array.isArray(images)) continue;
    const imageUrls: string[] = [];
    for (const image of images) {
      if (mediaOutputKind(image, "image") === "image" && image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
        imageUrls.push(resolveBackendUrl(image.view_url, { includeToken: true }));
      }
    }
    if (imageUrls.length > 0) {
      map.set(nodeId, [...(map.get(nodeId) ?? []), ...imageUrls]);
    }
  }
  return map;
}

export function extractAudioOutputs(result: JobResult | null): OutputAudioMedia[] {
  return extractMediaItems(result, "audio")
    .map(({ item }) => normalizeAudioOutput(item))
    .filter((item): item is OutputAudioMedia => Boolean(item));
}

export function extractAudioOutputsByNodeId(result: JobResult | null): Map<string, OutputAudioMedia[]> {
  const map = new Map<string, OutputAudioMedia[]>();
  if (!result) return map;
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object") continue;
    const nodeIdKey = Object.keys(output).find((key) => key !== "output");
    const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey;
    if (!nodeId) continue;
    const audios = (outputPayload as Record<string, unknown>).audio;
    if (!Array.isArray(audios)) continue;
    const audioOutputs = audios.map(normalizeAudioOutput).filter((item): item is OutputAudioMedia => Boolean(item));
    if (audioOutputs.length > 0) {
      map.set(nodeId, [...(map.get(nodeId) ?? []), ...audioOutputs]);
    }
  }
  return map;
}

export function extractTextOutputsByNodeId(result: JobResult | null): Map<string, string[]> {
  const map = new Map<string, string[]>();
  if (!result) return map;
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object") continue;
    const nodeIdKey = Object.keys(output).find((key) => key !== "output");
    const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey ?? null;
    if (!nodeId) continue;
    const rawText = (outputPayload as Record<string, unknown>).text;
    const values = Array.isArray(rawText) ? rawText : typeof rawText === "string" ? [rawText] : [];
    const texts = values.filter((value): value is string => typeof value === "string");
    if (texts.length > 0) map.set(nodeId, [...(map.get(nodeId) ?? []), ...texts]);
  }
  return map;
}

export function extractVideoOutputs(result: JobResult | null): OutputVideoMedia[] {
  return extractMediaItems(result, "video")
    .map(({ item }) => normalizeVideoOutput(item))
    .filter((item): item is OutputVideoMedia => Boolean(item));
}

export function extractFileOutputs(result: JobResult | null): OutputFileMedia[] {
  return extractMediaItems(result, "file")
    .map(({ item }) => normalizeFileOutput(item))
    .filter((item): item is OutputFileMedia => Boolean(item));
}

export function extractThreeDOutputs(result: JobResult | null): OutputThreeDMedia[] {
  return extractMediaItems(result, "3d")
    .map(({ item }) => normalizeThreeDOutput(item))
    .filter((item): item is OutputThreeDMedia => Boolean(item));
}

export function extractVideoOutputsByNodeId(result: JobResult | null): Map<string, OutputVideoMedia[]> {
  const map = new Map<string, OutputVideoMedia[]>();
  for (const { item, nodeId } of extractMediaItems(result, "video")) {
    if (!nodeId) continue;
    const normalized = normalizeVideoOutput(item);
    if (normalized) map.set(nodeId, [...(map.get(nodeId) ?? []), normalized]);
  }
  return map;
}

export function extractFileOutputsByNodeId(result: JobResult | null): Map<string, OutputFileMedia[]> {
  const map = new Map<string, OutputFileMedia[]>();
  for (const { item, nodeId } of extractMediaItems(result, "file")) {
    if (!nodeId) continue;
    const normalized = normalizeFileOutput(item);
    if (normalized) map.set(nodeId, [...(map.get(nodeId) ?? []), normalized]);
  }
  return map;
}

export function extractThreeDOutputsByNodeId(result: JobResult | null): Map<string, OutputThreeDMedia[]> {
  const map = new Map<string, OutputThreeDMedia[]>();
  for (const { item, nodeId } of extractMediaItems(result, "3d")) {
    if (!nodeId) continue;
    const normalized = normalizeThreeDOutput(item);
    if (normalized) map.set(nodeId, [...(map.get(nodeId) ?? []), normalized]);
  }
  return map;
}

export function mediaOutputKind(item: unknown, bucketName: string): "image" | "audio" | "video" | "3d" | "file" {
  if (item && typeof item === "object") {
    const record = item as Record<string, unknown>;
    if (record.kind === "image" || record.kind === "audio" || record.kind === "video" || record.kind === "3d" || record.kind === "file") return record.kind;
    if (record.type === "image" || record.type === "audio" || record.type === "video" || record.type === "3d" || record.type === "file") return record.type;
    const mimeType =
      typeof record.mime_type === "string"
        ? record.mime_type.toLowerCase()
        : typeof record.content_type === "string"
          ? record.content_type.toLowerCase()
          : "";
    if (mimeType.startsWith("image/")) return "image";
    if (mimeType.startsWith("audio/")) return "audio";
    if (mimeType.startsWith("video/")) return "video";
    if (mimeType.startsWith("model/")) return "3d";
    if (mimeType && mimeType !== "application/octet-stream") return "file";
    const filename = typeof record.filename === "string" ? record.filename.toLowerCase() : "";
    if (/\.(mp4|mov|webm|mkv)$/.test(filename)) return "video";
    if (/\.(wav|mp3|flac|ogg|m4a)$/.test(filename)) return "audio";
    if (/\.(glb|gltf|obj|stl|fbx|ply|usdz|dae|spz|splat|ksplat)$/.test(filename)) return "3d";
    if (/\.[a-z0-9][a-z0-9._-]*$/.test(filename)) return "file";
  }
  if (bucketName === "audio") return "audio";
  if (bucketName === "video" || bucketName === "videos") return "video";
  if (bucketName === "3d") return "3d";
  if (bucketName === "files" || bucketName === "text") return "file";
  return "image";
}

export function isReusableRecoveredResult(result: JobResult, waitForOutputPayload: boolean) {
  return terminalStatuses.has(result.status) && !shouldRetryRecoveredResult(result, waitForOutputPayload);
}

export function shouldRetryRecoveredResult(result: JobResult, waitForOutputPayload: boolean) {
  if (!terminalStatuses.has(result.status)) return true;
  return result.status === "completed" && waitForOutputPayload && !jobResultHasDisplayableOutput(result);
}

export function jobResultHasDisplayableOutput(result: JobResult) {
  return (
    extractImageUrls(result).length > 0 ||
    extractAudioOutputs(result).length > 0 ||
    extractVideoOutputs(result).length > 0 ||
    extractFileOutputs(result).length > 0 ||
    extractThreeDOutputs(result).length > 0 ||
    Array.from(extractTextOutputsByNodeId(result).values()).some((items) => items.length > 0)
  );
}

export function selectClassicPreviewMedia(
  controls: DashboardControlDef[],
  outputs: WorkflowOutputDef[],
  imagesByNodeId: Map<string, string[]>,
  audiosByNodeId: Map<string, OutputAudioMedia[]>,
  videosByNodeId: Map<string, OutputVideoMedia[]>,
  threeDsByNodeId: Map<string, OutputThreeDMedia[]>,
  filesByNodeId: Map<string, OutputFileMedia[]>,
  fallbackImages: string[],
  fallbackAudios: OutputAudioMedia[],
  fallbackVideos: OutputVideoMedia[],
  fallbackThreeDs: OutputThreeDMedia[],
  fallbackFiles: OutputFileMedia[],
) {
  const outputsById = new Map(outputs.map((output) => [output.id, output]));
  const declaredOutputs = controls
    .filter((control) => control.output_id)
    .map((control) => ({ controlId: control.id, output: outputsById.get(control.output_id!) }))
    .filter((item): item is { controlId: string; output: WorkflowOutputDef } => Boolean(item.output));
  for (const { controlId, output } of declaredOutputs) {
    const kind = output.kind ?? output.type;
    if (kind === "video" && videosByNodeId.get(output.node_id)?.[0]) return { kind: "video" as const, media: videosByNodeId.get(output.node_id)![0], controlId };
    if (kind === "image" && imagesByNodeId.get(output.node_id)?.[0]) return { kind: "image" as const, url: imagesByNodeId.get(output.node_id)![0], controlId };
    if (kind === "audio" && audiosByNodeId.get(output.node_id)?.[0]) return { kind: "audio" as const, media: audiosByNodeId.get(output.node_id)![0], controlId };
    if (kind === "3d" && threeDsByNodeId.get(output.node_id)?.[0]) return { kind: "3d" as const, media: threeDsByNodeId.get(output.node_id)![0], controlId };
    if (kind === "file" && filesByNodeId.get(output.node_id)?.[0]) return { kind: "file" as const, media: filesByNodeId.get(output.node_id)![0], controlId };
  }
  for (const output of outputs) {
    const kind = output.kind ?? output.type;
    if (kind === "video" && videosByNodeId.get(output.node_id)?.[0]) return { kind: "video" as const, media: videosByNodeId.get(output.node_id)![0] };
    if (kind === "image" && imagesByNodeId.get(output.node_id)?.[0]) return { kind: "image" as const, url: imagesByNodeId.get(output.node_id)![0] };
    if (kind === "audio" && audiosByNodeId.get(output.node_id)?.[0]) return { kind: "audio" as const, media: audiosByNodeId.get(output.node_id)![0] };
    if (kind === "3d" && threeDsByNodeId.get(output.node_id)?.[0]) return { kind: "3d" as const, media: threeDsByNodeId.get(output.node_id)![0] };
    if (kind === "file" && filesByNodeId.get(output.node_id)?.[0]) return { kind: "file" as const, media: filesByNodeId.get(output.node_id)![0] };
  }
  if (fallbackVideos[0]) return { kind: "video" as const, media: fallbackVideos[0] };
  if (fallbackImages[0]) return { kind: "image" as const, url: fallbackImages[0] };
  if (fallbackAudios[0]) return { kind: "audio" as const, media: fallbackAudios[0] };
  if (fallbackThreeDs[0]) return { kind: "3d" as const, media: fallbackThreeDs[0] };
  if (fallbackFiles[0]) return { kind: "file" as const, media: fallbackFiles[0] };
  return null;
}

export function videoOutputMetaLabel(video: OutputVideoMedia): string {
  return videoMetadataLabel(null, video.mimeType, video.size, video.durationSeconds, video.width, video.height, video.fps, "Video output");
}

export function audioOutputMetaLabel(audio: OutputAudioMedia): string {
  return audioMetadataLabel(null, audio.mimeType, audio.size, audio.durationSeconds, "Audio output");
}

export function fileOutputMetaLabel(file: OutputFileMedia): string {
  return fileMetadataLabel(file.extension, file.mimeType, file.size, "File output");
}

export function downloadMediaDirect(mediaUrl: string, filename: string) {
  const link = document.createElement("a");
  const url = new URL(mediaUrl, window.location.href);
  url.searchParams.set("download", "true");
  link.href = url.toString();
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function failedGallerySaveRequest(jobId: string, controlId: string, error: unknown): GallerySaveRequest {
  const message = error instanceof Error ? error.message : "Gallery save failed. Retry this output.";
  const unavailable = /(?:not|no longer) available|unavailable/i.test(message);
  return {
    job_id: jobId,
    control_id: controlId,
    status: unavailable ? "unavailable" : "failed",
    message,
    bytes_copied: 0,
    total_bytes: null,
    item_ids: [],
    updated_at: new Date().toISOString(),
  };
}

export function isDashboardOutputControl(control: DashboardControlDef) {
  return Boolean(control.output_id) || control.type === "result_image" || control.type.startsWith("display_");
}

export function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export async function comparisonImageSourceForRun(
  workflowId: string,
  packageData: WorkflowPackageResponse | null,
  controls: DashboardControlDef[],
  inputValues: Record<string, unknown>,
): Promise<ComparisonImageSource | null> {
  for (const control of controls) {
    if (!comparisonImageInputControlTypes.has(control.type) || !control.input_id) continue;
    const value = inputValues[control.input_id];
    const source = await comparisonImageSourceFromValue(workflowId, control.input_id, value);
    if (source) return source;
  }

  for (const input of packageData?.inputs ?? []) {
    if (!comparisonImageInputControlTypes.has(input.control)) continue;
    const value = inputValues[input.id];
    const source = await comparisonImageSourceFromValue(workflowId, input.id, value);
    if (source) return source;
  }

  return null;
}

export function defaultValueForWorkflowInput(input: WorkflowInputDef): unknown {
  if (input.control === "lora_loader" && isEmptyWorkflowValue(input.default)) return "None";
  return input.default;
}

export function extensionFromFilename(filename: string): string | null {
  const parts = filename.split(".");
  return parts.length > 1 ? `.${parts[parts.length - 1].toLowerCase()}` : null;
}

function extractMediaItems(result: JobResult | null, kind: "audio" | "video" | "3d" | "file") {
  const outputs: Array<{ item: unknown; nodeId: string | null }> = [];
  if (!result) return outputs;
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object") continue;
    const nodeIdKey = Object.keys(output).find((key) => key !== "output");
    const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey ?? null;
    for (const bucketName of ["images", "audio", "video", "videos", "gifs", "3d", "files", "text"]) {
      const items = (outputPayload as Record<string, unknown>)[bucketName];
      if (!Array.isArray(items)) continue;
      for (const item of items) {
        if (mediaOutputKind(item, bucketName) === kind) outputs.push({ item, nodeId });
      }
    }
  }
  return outputs;
}

function normalizeVideoOutput(video: unknown): OutputVideoMedia | null {
  if (!video || typeof video !== "object") return null;
  const item = video as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-video");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    thumbnailUrl: typeof item.thumbnail_url === "string" ? resolveBackendUrl(item.thumbnail_url, { includeToken: true }) : null,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    durationSeconds: typeof item.duration_seconds === "number" ? item.duration_seconds : null,
    width: typeof item.width === "number" ? item.width : null,
    height: typeof item.height === "number" ? item.height : null,
    fps: typeof item.fps === "number" ? item.fps : null,
    size: typeof item.size === "number" ? item.size : null,
  };
}

function normalizeFileOutput(file: unknown): OutputFileMedia | null {
  if (!file || typeof file !== "object") return null;
  const item = file as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-file");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    extension: typeof item.extension === "string" ? item.extension : extensionFromFilename(filename),
    size: typeof item.size === "number" ? item.size : null,
  };
}

function normalizeThreeDOutput(model: unknown): OutputThreeDMedia | null {
  if (!model || typeof model !== "object") return null;
  const item = model as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-model.glb");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    thumbnailUrl: typeof item.thumbnail_url === "string" ? resolveBackendUrl(item.thumbnail_url, { includeToken: true }) : null,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    extension: typeof item.extension === "string" ? item.extension : extensionFromFilename(filename),
    size: typeof item.size === "number" ? item.size : null,
  };
}

function normalizeAudioOutput(audio: unknown): OutputAudioMedia | null {
  if (!audio || typeof audio !== "object") return null;
  const item = audio as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-audio");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    durationSeconds: typeof item.duration_seconds === "number" ? item.duration_seconds : null,
    size: typeof item.size === "number" ? item.size : null,
  };
}

function filenameFromMediaUrl(rawUrl: string, fallback: string): string {
  try {
    const url = new URL(rawUrl, window.location.href);
    return url.searchParams.get("filename") || fallback;
  } catch {
    return fallback;
  }
}

async function comparisonImageSourceFromValue(
  workflowId: string,
  inputId: string,
  value: unknown,
): Promise<ComparisonImageSource | null> {
  if (isUploadedAssetValue(value)) {
    try {
      const metadata = await fetchAssetMetadata(value);
      if (metadata.has_mask && metadata.source_asset_id && isUploadedAssetValue(metadata.source_asset_id)) {
        return {
          kind: "masked_source_asset",
          workflowId,
          inputId,
          maskedAssetId: value,
          sourceAssetId: metadata.source_asset_id,
        };
      }
    } catch {
      // If metadata is unavailable, fall back to the selected asset. The image
      // fetch below still decides whether comparison can be shown.
    }
    return { kind: "uploaded_asset", workflowId, inputId, assetId: value };
  }
  if (isPackageAssetReference(value) && value.kind === "image") {
    return { kind: "package_asset", workflowId, inputId, assetId: value.asset_id };
  }
  if (isGalleryMediaReference(value) && value.kind === "image") {
    return { kind: "gallery_reference", workflowId, inputId, galleryItemId: value.gallery_item_id };
  }
  return null;
}

function isEmptyWorkflowValue(value: unknown): boolean {
  return value == null || (typeof value === "string" && value.trim() === "");
}
