import { apiErrorMessage } from "./api/client";
import { saveBinaryFile, selectSaveFile } from "./folderDialogs";

export function isNativeWorkflowExportAvailable() {
  return Boolean(window.__TAURI_INTERNALS__);
}

export function workflowExportFilename(name: string | null | undefined, extension: ".noofy" | ".json") {
  const base = cleanDefaultExportName(name || "workflow", extension)
    .trim()
    .replace(/\.[^.]+$/, "")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/[\x00-\x1f]+/g, "")
    .replace(/\s+/g, " ")
    .slice(0, 80)
    .trim();
  return `${base || "workflow"}${extension}`;
}

export interface WorkflowExportFilenameValidation {
  filename: string;
  valid: boolean;
  message: string | null;
  sanitized: boolean;
}

function cleanDefaultExportName(name: string, extension: ".noofy" | ".json") {
  if (extension !== ".noofy") return name;
  const trimmed = name.trim();
  const internalParts = trimmed.split("__");
  const lastPart = internalParts[internalParts.length - 1] ?? "";
  if (internalParts.length >= 3 && /^v?\d+\.\d+\.\d+(?:[-+].*)?$/i.test(lastPart)) {
    return internalParts.slice(1, -1).join("__") || trimmed;
  }
  return trimmed;
}

export function validateNoofyExportFilename(input: string): WorkflowExportFilenameValidation {
  const trimmed = input.trim();
  if (!trimmed) {
    return { filename: "", valid: false, message: "Enter a filename.", sanitized: false };
  }

  const withExtension = /\.noofy$/i.test(trimmed) ? trimmed : `${trimmed}.noofy`;
  const withoutPathSeparators = withExtension.replace(/[\\/]+/g, "-");
  const sanitized = withoutPathSeparators
    .replace(/[:*?"<>|]+/g, "-")
    .replace(/[\x00-\x1f]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const stem = sanitized.replace(/\.noofy$/i, "").replace(/[.\s-]+/g, "");

  if (!sanitized || !stem) {
    return {
      filename: sanitized,
      valid: false,
      message: "Use at least one letter or number before .noofy.",
      sanitized: sanitized !== withExtension,
    };
  }

  return {
    filename: sanitized,
    valid: true,
    message: sanitized !== withExtension ? `Invalid filename characters will be saved as ${sanitized}.` : null,
    sanitized: sanitized !== withExtension,
  };
}

export async function saveWorkflowExportToNativeFile(url: string, defaultFilename: string): Promise<string | null> {
  const targetPath = await selectSaveFile(defaultFilename);
  if (!targetPath) return null;

  const response = await fetch(url);
  if (!response.ok) throw new Error(await apiErrorMessage(response));

  const bytes = Array.from(new Uint8Array(await response.arrayBuffer()));
  return saveBinaryFile(targetPath, bytes);
}

export async function saveWorkflowExportToNativeFileWithAlert(url: string, defaultFilename: string): Promise<boolean> {
  try {
    return Boolean(await saveWorkflowExportToNativeFile(url, defaultFilename));
  } catch (error) {
    window.alert(error instanceof Error ? error.message : String(error));
    return false;
  }
}

export async function saveWorkflowExportWithFilename(url: string, filename: string): Promise<boolean> {
  if (isNativeWorkflowExportAvailable()) {
    return Boolean(await saveWorkflowExportToNativeFile(url, filename));
  }

  const response = await fetch(url);
  if (!response.ok) throw new Error(await apiErrorMessage(response));

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
  return true;
}

export function handleNativeWorkflowExportClick(
  event: { preventDefault: () => void },
  url: string,
  defaultFilename: string,
) {
  if (!isNativeWorkflowExportAvailable()) return false;
  event.preventDefault();
  void saveWorkflowExportToNativeFileWithAlert(url, defaultFilename);
  return true;
}
