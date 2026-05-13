import { apiErrorMessage } from "./api/client";
import { saveBinaryFile, selectSaveFile } from "./folderDialogs";

export function isNativeWorkflowExportAvailable() {
  return Boolean(window.__TAURI_INTERNALS__);
}

export function workflowExportFilename(name: string | null | undefined, extension: ".noofy" | ".json") {
  const base = (name || "workflow")
    .trim()
    .replace(/\.[^.]+$/, "")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, " ")
    .slice(0, 80)
    .trim();
  return `${base || "workflow"}${extension}`;
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
