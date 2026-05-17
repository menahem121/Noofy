export async function selectFolder(): Promise<string | null> {
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string | null>("select_folder");
  }
  return window.prompt("Enter the folder path")?.trim() || null;
}

export async function selectModelFiles(): Promise<string[]> {
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string[]>("select_model_files");
  }
  const value = window.prompt("Enter model file paths separated by commas");
  return value
    ? value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    : [];
}

export async function selectSaveFile(defaultFilename: string): Promise<string | null> {
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string | null>("select_save_file", { defaultFilename });
  }
  return null;
}

export async function saveBinaryFile(path: string, bytes: number[]): Promise<string> {
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string>("save_binary_file", { path, bytes });
  }
  throw new Error("Native file saving is only available in the desktop app.");
}

export async function openFolder(path: string): Promise<void> {
  if (!path) return;
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("open_folder", { path });
    return;
  }
  window.alert("Folder opening is only available in the desktop app.");
}
