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

export function getApiBaseUrl() {
  const runtimeBaseUrl = window.__NOOFY_RUNTIME_CONFIG__?.apiBaseUrl;
  const baseUrl = runtimeBaseUrl || configuredApiBaseUrl || DEFAULT_API_BASE_URL;
  return baseUrl.replace(/\/$/, "");
}

export function getApiToken() {
  return window.__NOOFY_RUNTIME_CONFIG__?.apiToken || configuredApiToken || null;
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

export function createJobEventsUrl(jobId: string) {
  const url = `${getApiBaseUrl()}/jobs/${encodeURIComponent(jobId)}/events`;
  const token = getApiToken();
  if (!token) return url;
  return `${url}?token=${encodeURIComponent(token)}`;
}

export function resolveBackendUrl(pathOrUrl: string, options: { includeToken?: boolean } = {}) {
  const apiBaseUrl = getApiBaseUrl();
  const isAbsoluteUrl = /^[a-z][a-z0-9+.-]*:/i.test(pathOrUrl) || pathOrUrl.startsWith("//");
  let resolved = pathOrUrl;
  let tokenEligible = !isAbsoluteUrl;

  if (!isAbsoluteUrl && pathOrUrl.startsWith(DEFAULT_API_BASE_URL)) {
    resolved =
      apiBaseUrl === DEFAULT_API_BASE_URL
        ? pathOrUrl
        : `${apiBaseUrl}${pathOrUrl.slice(DEFAULT_API_BASE_URL.length)}`;
  } else if (!isAbsoluteUrl && pathOrUrl.startsWith("/")) {
    resolved = `${apiBaseUrl}${pathOrUrl}`;
  } else if (!isAbsoluteUrl) {
    resolved = `${apiBaseUrl}/${pathOrUrl}`;
  } else if (apiBaseUrl !== DEFAULT_API_BASE_URL) {
    tokenEligible = pathOrUrl === apiBaseUrl || pathOrUrl.startsWith(`${apiBaseUrl}/`);
  }

  const token = options.includeToken ? getApiToken() : null;
  if (!token || !tokenEligible) return resolved;
  const separator = resolved.includes("?") ? "&" : "?";
  return `${resolved}${separator}token=${encodeURIComponent(token)}`;
}

export async function apiErrorMessage(response: Response): Promise<string> {
  const fallback = `Noofy backend returned ${response.status}`;
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
  } catch {
    // Use the stable fallback below.
  }
  return fallback;
}

export async function getJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers: apiHeaders(),
  });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<T>;
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: apiHeaders("application/json"),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<T>;
}

export async function postBytes<T>(path: string, body: ArrayBuffer): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: apiHeaders("application/octet-stream"),
    body,
  });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<T>;
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "PUT",
    headers: apiHeaders("application/json"),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<T>;
}

export async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<T>;
}
