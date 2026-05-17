export async function openExternalUrl(url: string): Promise<void> {
  try {
    if (window.__TAURI_INTERNALS__) {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("open_external_url", { url });
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  } catch (error) {
    console.error("[noofy] failed to open external URL:", error);
  }
}
