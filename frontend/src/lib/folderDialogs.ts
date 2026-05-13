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

export async function openFolder(path: string): Promise<void> {
  if (!path) return;
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("open_folder", { path });
    return;
  }
  console.info("[noofy] folder path:", path);
}
