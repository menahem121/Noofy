import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  Clock,
  ExternalLink,
  MoreHorizontal,
  Package,
  PackageMinus,
  PackagePlus,
  Play,
  RefreshCw,
  Search,
  ShieldAlert,
  X,
} from "lucide-react";

import {
  fetchHistory,
  fetchHistoryEvent,
  fetchRuntimeStatus,
  HISTORY_EVENT_STATUS_LABELS,
  HISTORY_EVENT_TYPE_LABELS,
  type HistoryEvent,
  type HistoryEventDetail,
  type HistoryEventStatus,
  type HistoryEventType,
  type RuntimeStatus,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";

type EventTypeFilter = HistoryEventType | "all";
type StatusFilter = HistoryEventStatus | "all";
type SortOption = "newest" | "oldest";
type DateRangeFilter = "all" | "today" | "week" | "month";

interface HistoryPageProps {
  onNavigate: (route: AppRouteId) => void;
}

interface PageState {
  loading: boolean;
  loadingMore: boolean;
  events: HistoryEvent[];
  total: number;
  nextCursor: string | null;
  hasMore: boolean;
  error: string | null;
}

interface DetailState {
  loading: boolean;
  event: HistoryEventDetail | null;
  error: string | null;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number): string {
  const rounded = Math.round(seconds);
  if (rounded < 60) return `${rounded}s`;
  const m = Math.floor(rounded / 60);
  const s = rounded % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function getDateGroupLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today.getTime() - 86400000);
  const eventDay = new Date(d);
  eventDay.setHours(0, 0, 0, 0);

  if (eventDay.getTime() === today.getTime()) return "Today";
  if (eventDay.getTime() === yesterday.getTime()) return "Yesterday";

  const daysAgo = Math.floor((today.getTime() - eventDay.getTime()) / 86400000);
  if (daysAgo < 30) {
    return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  }
  return "Older";
}

function dateGroupSortOrder(label: string): number {
  if (label === "Today") return 0;
  if (label === "Yesterday") return 1;
  if (label === "Older") return 9999;
  return 2;
}

function dateRangeBounds(range: DateRangeFilter): { createdAfter?: string; createdBefore?: string } {
  if (range === "all") return {};
  const now = new Date();
  const start = new Date(now);
  if (range === "today") {
    start.setHours(0, 0, 0, 0);
  } else if (range === "week") {
    start.setDate(start.getDate() - 7);
  } else {
    start.setDate(start.getDate() - 30);
  }
  return { createdAfter: start.toISOString(), createdBefore: now.toISOString() };
}

const EVENT_ICONS: Record<HistoryEventType, typeof Play> = {
  run: Play,
  run_blocked: ShieldAlert,
  workflow_imported: PackagePlus,
  workflow_removed: PackageMinus,
  import_failed: ShieldAlert,
};

const EVENT_ICON_TONE: Record<HistoryEventType, string> = {
  run: "run",
  run_blocked: "removed",
  workflow_imported: "installed",
  workflow_removed: "removed",
  import_failed: "removed",
};

const STATUS_TONES: Record<HistoryEventStatus, string> = {
  completed: "success",
  failed: "error",
  canceled: "muted",
  blocked: "warning",
  installed: "success",
  removed: "muted",
};

export function HistoryPage({ onNavigate }: HistoryPageProps) {
  const [runtimeState, setRuntimeState] = useState<{ loading: boolean; runtime: RuntimeStatus | null }>({
    loading: true,
    runtime: null,
  });
  const [pageState, setPageState] = useState<PageState>({
    loading: true,
    loadingMore: false,
    events: [],
    total: 0,
    nextCursor: null,
    hasMore: false,
    error: null,
  });
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<EventTypeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [dateRange, setDateRange] = useState<DateRangeFilter>("all");
  const [sortOption, setSortOption] = useState<SortOption>("newest");
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [detailState, setDetailState] = useState<DetailState>({ loading: false, event: null, error: null });
  const historyRequestId = useRef(0);

  useEffect(() => {
    let mounted = true;
    fetchRuntimeStatus()
      .then((runtime) => {
        if (mounted) setRuntimeState({ loading: false, runtime });
      })
      .catch(() => {
        if (mounted) setRuntimeState({ loading: false, runtime: null });
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 220);
    return () => window.clearTimeout(timer);
  }, [search]);

  const loadHistory = useCallback(
    async (options: { cursor?: string | null; append?: boolean } = {}) => {
      const requestId = historyRequestId.current + 1;
      historyRequestId.current = requestId;
      const append = Boolean(options.append);
      setPageState((prev) => ({
        ...prev,
        loading: !append,
        loadingMore: append,
        error: null,
        events: append ? prev.events : [],
      }));
      try {
        const bounds = dateRangeBounds(dateRange);
        const data = await fetchHistory({
          limit: 50,
          cursor: options.cursor,
          type: typeFilter,
          status: statusFilter,
          q: debouncedSearch,
          sort: sortOption,
          ...bounds,
        });
        if (requestId !== historyRequestId.current) return;
        setPageState((prev) => ({
          loading: false,
          loadingMore: false,
          events: append ? [...prev.events, ...data.events] : data.events,
          total: data.total,
          nextCursor: data.nextCursor,
          hasMore: data.hasMore,
          error: null,
        }));
      } catch (error) {
        if (requestId !== historyRequestId.current) return;
        setPageState((prev) => ({
          ...prev,
          loading: false,
          loadingMore: false,
          error: error instanceof Error ? error.message : String(error),
        }));
      }
    },
    [dateRange, debouncedSearch, sortOption, statusFilter, typeFilter],
  );

  useEffect(() => {
    void loadHistory();
    setSelectedEventId(null);
    setDetailState({ loading: false, event: null, error: null });
  }, [loadHistory]);

  useEffect(() => {
    if (!selectedEventId) {
      setDetailState({ loading: false, event: null, error: null });
      return;
    }
    let canceled = false;
    setDetailState({ loading: true, event: null, error: null });
    fetchHistoryEvent(selectedEventId)
      .then((event) => {
        if (!canceled) setDetailState({ loading: false, event, error: null });
      })
      .catch((error) => {
        if (!canceled) {
          setDetailState({
            loading: false,
            event: null,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      });
    return () => {
      canceled = true;
    };
  }, [selectedEventId]);

  const appStatus = runtimeStatusCopy(runtimeState);

  const selectedListEvent = selectedEventId
    ? (pageState.events.find((e) => e.id === selectedEventId) ?? null)
    : null;
  const selectedEvent = detailState.event ?? selectedListEvent;

  const groupedEvents = useMemo(() => {
    const groups = new Map<string, HistoryEvent[]>();
    for (const evt of pageState.events) {
      const label = getDateGroupLabel(evt.createdAt);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label)!.push(evt);
    }
    return [...groups.entries()].sort(([a], [b]) => {
      const diff = dateGroupSortOrder(a) - dateGroupSortOrder(b);
      if (diff !== 0) return diff;
      const aTop = groups.get(a)![0].createdAt;
      const bTop = groups.get(b)![0].createdAt;
      return new Date(bTop).getTime() - new Date(aTop).getTime();
    });
  }, [pageState.events]);

  const hasActiveFilters =
    typeFilter !== "all" || statusFilter !== "all" || dateRange !== "all" || search.trim() !== "";
  const panelOpen = selectedEvent !== null;

  function handleSelectEvent(id: string) {
    setSelectedEventId((prev) => (prev === id ? null : id));
  }

  function clearFilters() {
    setSearch("");
    setDebouncedSearch("");
    setTypeFilter("all");
    setStatusFilter("all");
    setDateRange("all");
  }

  return (
    <AppLayout activeRoute="history" status={appStatus} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="history-title">
        <div>
          <p className="eyebrow">Activity log</p>
          <h1 id="history-title">History</h1>
          <p>Review your workflow runs, imports, and changes.</p>
        </div>
      </section>

      <div className="history-type-tabs" role="tablist" aria-label="Filter by event type">
        {(
          [
            { id: "all", label: "All" },
            { id: "run", label: "Runs" },
            { id: "run_blocked", label: "Blocked" },
            { id: "workflow_imported", label: "Imports" },
            { id: "workflow_removed", label: "Removals" },
            { id: "import_failed", label: "Failed imports" },
          ] as Array<{ id: EventTypeFilter; label: string }>
        ).map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={typeFilter === id}
            className={`history-type-tab${typeFilter === id ? " history-type-tab--active" : ""}`}
            type="button"
            onClick={() => setTypeFilter(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="history-toolbar">
        <label className="search-field history-search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search history</span>
          <input
            type="search"
            placeholder="Search history..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>

        <div className="filter-select-wrap">
          <select
            className="filter-select"
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          >
            <option value="all">All status</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="canceled">Canceled</option>
            <option value="blocked">Blocked</option>
            <option value="installed">Installed</option>
            <option value="removed">Removed</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>

        <div className="filter-select-wrap">
          <select
            className="filter-select"
            aria-label="Date range"
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value as DateRangeFilter)}
          >
            <option value="all">All time</option>
            <option value="today">Today</option>
            <option value="week">Last 7 days</option>
            <option value="month">Last 30 days</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>

        <div className="history-toolbar__spacer" />

        <div className="filter-select-wrap">
          <select
            className="filter-select"
            aria-label="Sort order"
            value={sortOption}
            onChange={(e) => setSortOption(e.target.value as SortOption)}
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>
      </div>

      {pageState.loading ? (
        <HistoryLoading />
      ) : pageState.error ? (
        <HistoryError error={pageState.error} onRetry={() => void loadHistory()} />
      ) : pageState.events.length === 0 && !hasActiveFilters ? (
        <HistoryEmpty onNavigate={onNavigate} />
      ) : (
        <div className={`history-layout${panelOpen ? " history-layout--panel-open" : ""}`}>
          <div className="history-list-area">
            {pageState.events.length === 0 ? (
              <div className="history-no-results">
                <Search size={36} aria-hidden="true" />
                <h3>No events match your filters</h3>
                <p>Try adjusting your search or filters above.</p>
                {hasActiveFilters && (
                  <button className="ghost-button" type="button" onClick={clearFilters}>
                    Clear all filters
                  </button>
                )}
              </div>
            ) : (
              <div className="history-groups">
                {groupedEvents.map(([label, events]) => (
                  <div key={label} className="history-date-group">
                    <div className="history-date-label">
                      <span>{label}</span>
                      <span className="history-date-count">{events.length}</span>
                    </div>
                    <div className="history-events" role="list">
                      {events.map((evt) => (
                        <HistoryEventRow
                          key={evt.id}
                          event={evt}
                          selected={selectedEventId === evt.id}
                          panelOpen={panelOpen}
                          onSelect={() => handleSelectEvent(evt.id)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
                {pageState.hasMore && (
                  <button
                    className="secondary-button secondary-button--full"
                    type="button"
                    disabled={pageState.loadingMore}
                    onClick={() => void loadHistory({ cursor: pageState.nextCursor, append: true })}
                  >
                    {pageState.loadingMore ? "Loading..." : "Load more"}
                  </button>
                )}
              </div>
            )}
          </div>

          {selectedEvent && (
            <aside className="history-detail-panel" aria-label={`Details for ${selectedEvent.title}`}>
              <EventDetailPanel
                event={selectedEvent}
                loading={detailState.loading}
                error={detailState.error}
                onClose={() => setSelectedEventId(null)}
                onNavigate={onNavigate}
              />
            </aside>
          )}
        </div>
      )}
    </AppLayout>
  );
}

function HistoryEventRow({
  event,
  selected,
  panelOpen,
  onSelect,
}: {
  event: HistoryEvent;
  selected: boolean;
  panelOpen: boolean;
  onSelect: () => void;
}) {
  const EventIcon = EVENT_ICONS[event.type];
  const summaryParts = buildSummaryParts(event);

  return (
    <article
      className={`history-event-row${selected ? " history-event-row--selected" : ""}`}
      role="listitem"
      onClick={onSelect}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      aria-selected={selected}
    >
      <div className={`history-event-icon history-event-icon--${EVENT_ICON_TONE[event.type]}`} aria-hidden="true">
        <EventIcon size={16} />
      </div>

      <div className="history-event-body">
        <div className="history-event-title">{event.title}</div>
        <div className="history-event-summary">
          {summaryParts.map((part, i) => (
            <span key={part} className="history-event-summary__part">
              {i > 0 && <span className="history-event-summary__dot" aria-hidden="true" />}
              {part}
            </span>
          ))}
        </div>
      </div>

      {event.thumbnailUrl && !panelOpen && (
        <div className="history-event-thumb" aria-hidden="true">
          <img src={event.thumbnailUrl} alt="" loading="lazy" />
        </div>
      )}

      <div className="history-event-meta">
        <span className="history-event-time">{formatTime(event.createdAt)}</span>
        <HistoryStatusBadge status={event.status} />
      </div>

      <div className="history-event-actions" onClick={(e) => e.stopPropagation()}>
        <button
          className="icon-button"
          type="button"
          aria-label={`Open details for ${event.title}`}
          title="View details"
          onClick={onSelect}
        >
          <MoreHorizontal size={16} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}

function buildSummaryParts(event: HistoryEvent): string[] {
  const parts: string[] = [`Workflow: ${event.workflowName}`];
  if (event.durationSeconds !== undefined && event.durationSeconds !== null) {
    parts.push(`Duration: ${formatDuration(event.durationSeconds)}`);
  }
  if (event.source) parts.push(`Source: ${event.source}`);
  if (event.errorSummary) parts.push(event.errorSummary);
  return parts;
}

function HistoryStatusBadge({ status }: { status: HistoryEventStatus }) {
  return (
    <span className={`history-status history-status--${STATUS_TONES[status]}`}>
      <span className="history-status__dot" aria-hidden="true" />
      {HISTORY_EVENT_STATUS_LABELS[status]}
    </span>
  );
}

function EventDetailPanel({
  event,
  loading,
  error,
  onClose,
  onNavigate,
}: {
  event: HistoryEvent | HistoryEventDetail;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onNavigate: (route: AppRouteId) => void;
}) {
  const EventIcon = EVENT_ICONS[event.type];
  const isDetail = "usedSettings" in event;
  const usedSettings = isDetail ? event.usedSettings : {};
  const prompt = isDetail ? event.prompt : null;

  return (
    <>
      <div className="detail-panel__header">
        <div className="detail-panel__title-group">
          <div
            className={`history-event-icon history-event-icon--lg history-event-icon--${EVENT_ICON_TONE[event.type]}`}
            aria-hidden="true"
          >
            <EventIcon size={19} />
          </div>
          <div className="detail-panel__title-text">
            <h2 className="detail-panel__title">{event.title}</h2>
            <span className="detail-panel__type">{HISTORY_EVENT_TYPE_LABELS[event.type]}</span>
          </div>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close details panel">
          <X size={17} aria-hidden="true" />
        </button>
      </div>

      {event.thumbnailUrl && (
        <div className="detail-panel__section">
          <div className="history-detail-thumb">
            <img src={event.thumbnailUrl} alt="Generated output" />
          </div>
        </div>
      )}

      <div className="detail-panel__section">
        <HistoryStatusBadge status={event.status} />
      </div>

      {loading && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">Details</div>
          <p className="history-detail-prompt">Loading details...</p>
        </div>
      )}

      {error && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">Details unavailable</div>
          <p className="history-detail-error">{error}</p>
        </div>
      )}

      {prompt && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">Prompt</div>
          <p className="history-detail-prompt">{prompt}</p>
        </div>
      )}

      <div className="detail-panel__section">
        <dl className="detail-list detail-list--compact">
          <div>
            <dt>Workflow</dt>
            <dd>{event.workflowName}</dd>
          </div>
          {event.startedAt && (
            <div>
              <dt>Started</dt>
              <dd>{formatDateTime(event.startedAt)}</dd>
            </div>
          )}
          {event.completedAt && (
            <div>
              <dt>Completed</dt>
              <dd>{formatDateTime(event.completedAt)}</dd>
            </div>
          )}
          {event.durationSeconds !== undefined && event.durationSeconds !== null && (
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(event.durationSeconds)}</dd>
            </div>
          )}
          {event.source && (
            <div>
              <dt>Source</dt>
              <dd>{event.source}</dd>
            </div>
          )}
          {event.trustLevel && (
            <div>
              <dt>Trust level</dt>
              <dd style={{ textTransform: "capitalize" }}>{event.trustLevel}</dd>
            </div>
          )}
        </dl>
      </div>

      {Object.keys(usedSettings).length > 0 && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">Settings used</div>
          <dl className="history-settings-list">
            {Object.entries(usedSettings).map(([key, val]) => (
              <div key={key} className="history-setting-row">
                <dt>{key}</dt>
                <dd>{String(val)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {event.errorSummary && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">What happened</div>
          <p className="history-detail-error">{event.errorSummary}</p>
        </div>
      )}

      <div className="detail-panel__section">
        <div className="detail-panel__section-label">Actions</div>
        <div className="detail-panel__actions">
          {event.outputUrl && (
            <a className="secondary-button secondary-button--full" href={event.outputUrl} target="_blank" rel="noreferrer">
              <ExternalLink size={14} aria-hidden="true" />
              Open result
            </a>
          )}
          {event.canOpenWorkflow && (
            <button
              className="secondary-button secondary-button--full"
              type="button"
              onClick={() => onNavigate("workflows")}
            >
              <Package size={14} aria-hidden="true" />
              Open workflow
            </button>
          )}
        </div>
      </div>
    </>
  );
}

function HistoryLoading() {
  return (
    <div className="history-loading" aria-label="Loading history" aria-live="polite">
      {[...Array(7)].map((_, i) => (
        <div key={i} className="history-skeleton" />
      ))}
    </div>
  );
}

function HistoryEmpty({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  return (
    <div className="history-empty">
      <div className="history-empty__icon" aria-hidden="true">
        <Clock size={36} />
      </div>
      <h2>No history yet</h2>
      <p>Run or import a workflow to start building your history.</p>
      <button className="primary-button" type="button" onClick={() => onNavigate("workflows")}>
        Open Workflows
      </button>
    </div>
  );
}

function HistoryError({ error, onRetry }: { error: string; onRetry: () => void }) {
  const [showDetail, setShowDetail] = useState(false);

  return (
    <div className="history-error">
      <AlertCircle size={40} aria-hidden="true" />
      <h2>History could not be loaded.</h2>
      <p>Try again, or open details if the problem continues.</p>
      <div className="history-error__actions">
        <button className="primary-button" type="button" onClick={onRetry}>
          <RefreshCw size={16} aria-hidden="true" />
          Retry
        </button>
        <button className="ghost-button" type="button" onClick={() => setShowDetail((v) => !v)}>
          {showDetail ? "Hide details" : "Show details"}
        </button>
      </div>
      {showDetail && <div className="history-error__detail">{error}</div>}
    </div>
  );
}
