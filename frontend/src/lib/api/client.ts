declare global {
  interface Window {
    __NOOFY_RUNTIME_CONFIG__?: {
      apiBaseUrl?: string;
      apiToken?: string;
    };
  }
}

const DEFAULT_API_BASE_URL = "/api";
const configuredApiBaseUrl = import.meta.env.VITE_NOOFY_API_BASE_URL as string | undefined;
const configuredApiToken = import.meta.env.VITE_NOOFY_API_TOKEN as string | undefined;

function isDesktopRuntime() {
  return Boolean(window.__TAURI_INTERNALS__);
}

function desktopRuntimeConfig() {
  const config = window.__NOOFY_RUNTIME_CONFIG__;
  if (!isDesktopRuntime()) return config;
  if (!config?.apiBaseUrl || !config?.apiToken) {
    throw new Error("Noofy desktop is missing its startup connection settings.");
  }
  return config;
}

export function getApiBaseUrl() {
  const runtimeBaseUrl = desktopRuntimeConfig()?.apiBaseUrl;
  const baseUrl = runtimeBaseUrl || configuredApiBaseUrl || DEFAULT_API_BASE_URL;
  return baseUrl.replace(/\/$/, "");
}

export function getApiToken() {
  const runtimeToken = desktopRuntimeConfig()?.apiToken;
  return runtimeToken || (isDesktopRuntime() ? null : configuredApiToken) || null;
}

export function apiHeaders(contentType?: string) {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  const token = getApiToken();
  if (contentType) headers["Content-Type"] = contentType;
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly url: string;

  constructor(message: string, response: Response) {
    super(message);
    this.name = "ApiError";
    this.status = response.status;
    this.statusText = response.statusText;
    this.url = response.url;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function createJobEventsUrl(jobId: string) {
  const url = `${getApiBaseUrl()}/jobs/${encodeURIComponent(jobId)}/events`;
  const token = getApiToken();
  if (!token) return url;
  return `${url}?token=${encodeURIComponent(token)}`;
}

export function resolveBackendUrl(pathOrUrl: string, options: { includeToken?: boolean } = {}) {
  const apiBaseUrl = getApiBaseUrl();
  const backendApiPath = localhostApiPath(pathOrUrl);
  const source = backendApiPath ?? pathOrUrl;
  const isAbsoluteUrl = /^[a-z][a-z0-9+.-]*:/i.test(source) || source.startsWith("//");
  let resolved = source;
  let tokenEligible = !isAbsoluteUrl;

  if (!isAbsoluteUrl && source.startsWith(DEFAULT_API_BASE_URL)) {
    resolved =
      apiBaseUrl === DEFAULT_API_BASE_URL
        ? source
        : `${apiBaseUrl}${source.slice(DEFAULT_API_BASE_URL.length)}`;
  } else if (!isAbsoluteUrl && source.startsWith("/")) {
    resolved = `${apiBaseUrl}${source}`;
  } else if (!isAbsoluteUrl) {
    resolved = `${apiBaseUrl}/${source}`;
  } else if (apiBaseUrl !== DEFAULT_API_BASE_URL) {
    tokenEligible = source === apiBaseUrl || source.startsWith(`${apiBaseUrl}/`);
  }

  const token = options.includeToken ? getApiToken() : null;
  if (!token || !tokenEligible) return resolved;
  const separator = resolved.includes("?") ? "&" : "?";
  return `${resolved}${separator}token=${encodeURIComponent(token)}`;
}

function localhostApiPath(value: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(value.startsWith("//") ? `http:${value}` : value);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    return null;
  }
  if (!["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsed.hostname.toLowerCase())) {
    return null;
  }
  if (parsed.pathname !== DEFAULT_API_BASE_URL && !parsed.pathname.startsWith(`${DEFAULT_API_BASE_URL}/`)) {
    return null;
  }
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

export async function apiErrorMessage(response: Response): Promise<string> {
  const fallback = `Noofy reported an error while loading this data (${response.status}).`;
  try {
    const payload = (await response.clone().json()) as unknown;
    if (payload && typeof payload === "object" && "detail" in payload) {
      const detail = (payload as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
      if (detail && typeof detail === "object" && "message" in detail) {
        const message = (detail as { message?: unknown }).message;
        if (typeof message === "string" && message.trim()) return message;
      }
    }
    if (payload && typeof payload === "object" && "errors" in payload) {
      const errors = (payload as { errors?: unknown }).errors;
      if (Array.isArray(errors)) {
        const first = errors.find((item) => item && typeof item === "object" && "msg" in item);
        const message = first && typeof (first as { msg?: unknown }).msg === "string" ? (first as { msg: string }).msg : null;
        if (message?.trim()) return message;
      }
    }
  } catch {
    // Use the stable fallback below.
  }
  return fallback;
}

async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  return new ApiError(await apiErrorMessage(response), response);
}

export async function getJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers: apiHeaders(),
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<T>;
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: apiHeaders("application/json"),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<T>;
}

export async function postBytes<T>(path: string, body: ArrayBuffer): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: apiHeaders("application/octet-stream"),
    body,
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<T>;
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "PUT",
    headers: apiHeaders("application/json"),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<T>;
}

export async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<T>;
}
