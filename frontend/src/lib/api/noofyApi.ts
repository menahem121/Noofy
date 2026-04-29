export interface WorkflowSummary {
  id: string;
  name: string;
  version: string;
  description: string;
}

export interface RuntimeDependencyStatus {
  name: string;
  available: boolean;
  error: string | null;
}

export interface RuntimeEnvironmentStatus {
  prepared: boolean;
  dependencies?: RuntimeDependencyStatus[];
  error?: string | null;
}

export interface RuntimeStatus {
  mode: "external" | "managed";
  reachable: boolean;
  base_url: string;
  repo_dir: string;
  managed_process_running: boolean;
  pid: number | null;
  error: string | null;
  environment: RuntimeEnvironmentStatus | null;
}

const DEFAULT_API_BASE_URL = "/api";
const configuredApiBaseUrl = import.meta.env.VITE_NOOFY_API_BASE_URL as string | undefined;

export const apiBaseUrl = configuredApiBaseUrl ? configuredApiBaseUrl.replace(/\/$/, "") : DEFAULT_API_BASE_URL;

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`Noofy backend returned ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function fetchRuntimeStatus() {
  return getJson<RuntimeStatus>("/runtime");
}

export function fetchWorkflows() {
  return getJson<WorkflowSummary[]>("/workflows");
}
