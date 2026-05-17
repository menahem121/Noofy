export interface NativeWorkflowOpenPayload {
  path: string;
  filename: string;
}

interface NativeWorkflowFile {
  path: string;
  filename: string;
  bytes: ArrayLike<number>;
}

export interface NativeWorkflowImportRequest {
  id: number;
  file?: File;
  filename?: string;
  error?: string;
}

const OPEN_WORKFLOW_FILE_EVENT = "noofy-open-workflow-file";

export async function consumePendingNativeWorkflowFile(): Promise<NativeWorkflowOpenPayload | null> {
  if (!window.__TAURI_INTERNALS__) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<NativeWorkflowOpenPayload | null>("pending_noofy_open_file");
}

export async function readNativeWorkflowFile(path: string): Promise<File> {
  if (!window.__TAURI_INTERNALS__) throw new Error("Native file access is unavailable.");
  const { invoke } = await import("@tauri-apps/api/core");
  const file = await invoke<NativeWorkflowFile>("read_noofy_file", { path });
  const source = file.bytes instanceof Uint8Array ? file.bytes : Uint8Array.from(file.bytes);
  const bytes = new Uint8Array(source.length);
  bytes.set(source);
  return new File(
    [bytes.buffer],
    file.filename || "workflow.noofy",
    { type: "application/x-noofy-workflow" },
  );
}

export async function listenForNativeWorkflowOpen(
  onOpen: (payload: NativeWorkflowOpenPayload) => void,
): Promise<() => void> {
  if (!window.__TAURI_INTERNALS__) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<NativeWorkflowOpenPayload>(OPEN_WORKFLOW_FILE_EVENT, (event) => {
    onOpen(event.payload);
  });
}
