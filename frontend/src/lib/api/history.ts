import { getJson, resolveBackendUrl } from "./client";

export type HistoryEventType =
  | "run"
  | "run_blocked"
  | "workflow_imported"
  | "workflow_removed"
  | "import_failed";

export type HistoryEventStatus =
  | "completed"
  | "failed"
  | "canceled"
  | "blocked"
  | "installed"
  | "removed";

export interface HistoryEvent {
  id: string;
  type: HistoryEventType;
  status: HistoryEventStatus;
  title: string;
  workflowId: string | null;
  workflowName: string;
  createdAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  durationSeconds?: number | null;
  thumbnailUrl?: string | null;
  outputUrl?: string | null;
  galleryItemId?: string | null;
  galleryItemIds: string[];
  source?: string | null;
  trustLevel?: string | null;
  errorSummary?: string | null;
  canOpenWorkflow: boolean;
}

export interface HistoryEventDetail extends HistoryEvent {
  prompt?: string | null;
  usedSettings: Record<string, string | number | boolean | null>;
}

export interface HistoryQueryOptions {
  limit?: number;
  cursor?: string | null;
  type?: HistoryEventType | "all";
  status?: HistoryEventStatus | "all";
  workflowId?: string | null;
  q?: string;
  createdAfter?: string | null;
  createdBefore?: string | null;
  sort?: "newest" | "oldest";
}

export interface HistoryResponse {
  events: HistoryEvent[];
  total: number;
  nextCursor: string | null;
  hasMore: boolean;
}

export const HISTORY_EVENT_TYPE_LABELS: Record<HistoryEventType, string> = {
  run: "Workflow run",
  run_blocked: "Run blocked",
  workflow_imported: "Workflow imported",
  workflow_removed: "Workflow removed",
  import_failed: "Import failed",
};

export const HISTORY_EVENT_STATUS_LABELS: Record<HistoryEventStatus, string> = {
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
  blocked: "Blocked",
  installed: "Installed",
  removed: "Removed",
};

export async function fetchHistory(options: HistoryQueryOptions = {}): Promise<HistoryResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 50));
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.type && options.type !== "all") params.set("type", options.type);
  if (options.status && options.status !== "all") params.set("status", options.status);
  if (options.workflowId) params.set("workflow_id", options.workflowId);
  if (options.q?.trim()) params.set("q", options.q.trim());
  if (options.createdAfter) params.set("created_after", options.createdAfter);
  if (options.createdBefore) params.set("created_before", options.createdBefore);
  if (options.sort) params.set("sort", options.sort);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const raw = await getJson<Record<string, unknown>>(`/history${suffix}`);
  const events = Array.isArray(raw.events) ? raw.events.map(normalizeHistoryEvent) : [];
  return {
    events,
    total: typeof raw.total === "number" ? raw.total : events.length,
    nextCursor: typeof raw.next_cursor === "string" ? raw.next_cursor : null,
    hasMore: Boolean(raw.has_more),
  };
}

export async function fetchHistoryEvent(eventId: string): Promise<HistoryEventDetail> {
  return normalizeHistoryEventDetail(await getJson<unknown>(`/history/${encodeURIComponent(eventId)}`));
}

export function historyMediaUrl(value: string | null | undefined): string {
  return value ? resolveBackendUrl(value, { includeToken: true }) : "";
}

function normalizeHistoryEvent(raw: unknown): HistoryEvent {
  const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const thumbnailUrl = typeof item.thumbnail_url === "string" ? item.thumbnail_url : null;
  const outputUrl = typeof item.output_url === "string" ? item.output_url : null;
  return {
    id: String(item.id ?? ""),
    type: String(item.type ?? "run") as HistoryEventType,
    status: String(item.status ?? "completed") as HistoryEventStatus,
    title: String(item.title ?? ""),
    workflowId: typeof item.workflow_id === "string" ? item.workflow_id : null,
    workflowName: String(item.workflow_name ?? "Workflow"),
    createdAt: String(item.created_at ?? ""),
    startedAt: typeof item.started_at === "string" ? item.started_at : null,
    completedAt: typeof item.completed_at === "string" ? item.completed_at : null,
    durationSeconds: typeof item.duration_seconds === "number" ? item.duration_seconds : null,
    thumbnailUrl,
    outputUrl,
    galleryItemId: typeof item.gallery_item_id === "string" ? item.gallery_item_id : null,
    galleryItemIds: Array.isArray(item.gallery_item_ids) ? item.gallery_item_ids.filter((value): value is string => typeof value === "string") : [],
    source: typeof item.source === "string" ? item.source : null,
    trustLevel: typeof item.trust_level === "string" ? item.trust_level : null,
    errorSummary: typeof item.error_summary === "string" ? item.error_summary : null,
    canOpenWorkflow: Boolean(item.can_open_workflow),
  };
}

function normalizeHistoryEventDetail(raw: unknown): HistoryEventDetail {
  const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const usedSettings =
    item.used_settings && typeof item.used_settings === "object"
      ? item.used_settings as Record<string, string | number | boolean | null>
      : {};
  return {
    ...normalizeHistoryEvent(raw),
    prompt: typeof item.prompt === "string" ? item.prompt : null,
    usedSettings,
  };
}
