import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  ExternalLink,
  MoreHorizontal,
  Package,
  PackageMinus,
  PackagePlus,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  X,
} from "lucide-react";

import { fetchRuntimeStatus, type RuntimeStatus } from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import {
  EVENT_STATUS_LABELS,
  EVENT_TYPE_LABELS,
  MOCK_HISTORY,
  type HistoryEvent,
  type HistoryEventStatus,
  type HistoryEventType,
} from "./historyMock";

type EventTypeFilter = HistoryEventType | "all";
type StatusFilter = HistoryEventStatus | "all";
type SortOption = "newest" | "oldest" | "longest" | "most_memory";
type DateRangeFilter = "all" | "today" | "week" | "month";

interface HistoryPageProps {
  onNavigate: (route: AppRouteId) => void;
}

interface PageState {
  loading: boolean;
  events: HistoryEvent[];
  error: string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

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
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatMemory(mb: number): string {
  if (mb < 1024) return `${mb} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
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

const EVENT_ICONS: Record<HistoryEventType, typeof Play> = {
  run: Play,
  workflow_installed: PackagePlus,
  workflow_removed: PackageMinus,
};

const EVENT_ICON_TONE: Record<HistoryEventType, string> = {
  run: "run",
  workflow_installed: "installed",
  workflow_removed: "removed",
};

const STATUS_TONES: Record<HistoryEventStatus, string> = {
  completed: "success",
  failed: "error",
  canceled: "muted",
  installed: "success",
  removed: "muted",
  preparing: "warning",
  ready: "success",
};

// ── Page ─────────────────────────────────────────────────────────────────────

export function HistoryPage({ onNavigate }: HistoryPageProps) {
  const [runtimeState, setRuntimeState] = useState<{ loading: boolean; runtime: RuntimeStatus | null }>({
    loading: true,
    runtime: null,
  });

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

  const appStatus = runtimeStatusCopy(runtimeState);

  const [pageState, setPageState] = useState<PageState>({ loading: true, events: [], error: null });

  useEffect(() => {
    const timer = setTimeout(() => {
      setPageState({ loading: false, events: MOCK_HISTORY, error: null });
    }, 520);
    return () => clearTimeout(timer);
  }, []);

  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<EventTypeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [dateRange, setDateRange] = useState<DateRangeFilter>("all");
  const [sortOption, setSortOption] = useState<SortOption>("newest");

  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [showDevDetails, setShowDevDetails] = useState(false);

  const selectedEvent = selectedEventId
    ? (pageState.events.find((e) => e.id === selectedEventId) ?? null)
    : null;

  const filteredEvents = useMemo(() => {
    const now = new Date();
    let result = pageState.events.filter((evt) => {
      if (typeFilter !== "all" && evt.type !== typeFilter) return false;
      if (statusFilter !== "all" && evt.status !== statusFilter) return false;

      if (dateRange !== "all") {
        const diffDays = (now.getTime() - new Date(evt.createdAt).getTime()) / 86400000;
        if (dateRange === "today" && diffDays >= 1) return false;
        if (dateRange === "week" && diffDays >= 7) return false;
        if (dateRange === "month" && diffDays >= 30) return false;
      }

      if (search.trim()) {
        const q = search.toLowerCase();
        const haystack = [
          evt.workflowName,
          evt.prompt ?? "",
          evt.title,
          EVENT_STATUS_LABELS[evt.status],
          EVENT_TYPE_LABELS[evt.type],
          evt.outputRef ?? "",
        ].join(" ").toLowerCase();
        if (!haystack.includes(q)) return false;
      }

      return true;
    });

    result = [...result].sort((a, b) => {
      if (sortOption === "newest") return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
      if (sortOption === "oldest") return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
      if (sortOption === "longest") return (b.durationSeconds ?? 0) - (a.durationSeconds ?? 0);
      if (sortOption === "most_memory") return (b.peakVramMb ?? 0) - (a.peakVramMb ?? 0);
      return 0;
    });

    return result;
  }, [pageState.events, typeFilter, statusFilter, dateRange, search, sortOption]);

  const groupedEvents = useMemo(() => {
    const groups = new Map<string, HistoryEvent[]>();
    for (const evt of filteredEvents) {
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
  }, [filteredEvents]);

  const hasActiveFilters =
    typeFilter !== "all" || statusFilter !== "all" || dateRange !== "all" || search.trim() !== "";

  function handleRetry() {
    setPageState({ loading: true, events: [], error: null });
    setTimeout(() => {
      setPageState({ loading: false, events: MOCK_HISTORY, error: null });
    }, 520);
  }

  function handleSelectEvent(id: string) {
    setSelectedEventId((prev) => (prev === id ? null : id));
    setShowDevDetails(false);
  }

  const panelOpen = selectedEvent !== null;

  return (
    <AppLayout activeRoute="history" status={appStatus} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="history-title">
        <div>
          <p className="eyebrow">Activity log</p>
          <h1 id="history-title">History</h1>
          <p>Review your workflow runs, installs, and changes.</p>
        </div>
      </section>

      <div className="history-type-tabs" role="tablist" aria-label="Filter by event type">
        {(
          [
            { id: "all", label: "All" },
            { id: "run", label: "Runs" },
            { id: "workflow_installed", label: "Installs" },
            { id: "workflow_removed", label: "Removals" },
          ] as Array<{ id: EventTypeFilter; label: string }>
        ).map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={typeFilter === id}
            className={`history-type-tab${typeFilter === id ? " history-type-tab--active" : ""}`}
            type="button"
            onClick={() => {
              setTypeFilter(id);
              setSelectedEventId(null);
            }}
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
            <option value="longest">Longest duration</option>
            <option value="most_memory">Highest memory</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>
      </div>

      {pageState.loading ? (
        <HistoryLoading />
      ) : pageState.error ? (
        <HistoryError error={pageState.error} onRetry={handleRetry} />
      ) : pageState.events.length === 0 ? (
        <HistoryEmpty onNavigate={onNavigate} />
      ) : (
        <div className={`history-layout${panelOpen ? " history-layout--panel-open" : ""}`}>
          <div className="history-list-area">
            {filteredEvents.length === 0 ? (
              <div className="history-no-results">
                <Search size={36} aria-hidden="true" />
                <h3>No events match your filters</h3>
                <p>Try adjusting your search or filters above.</p>
                {hasActiveFilters && (
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => {
                      setSearch("");
                      setTypeFilter("all");
                      setStatusFilter("all");
                      setDateRange("all");
                    }}
                  >
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
              </div>
            )}
          </div>

          {selectedEvent && (
            <aside className="history-detail-panel" aria-label={`Details for ${selectedEvent.title}`}>
              <EventDetailPanel
                event={selectedEvent}
                showDevDetails={showDevDetails}
                onClose={() => setSelectedEventId(null)}
                onToggleDevDetails={() => setShowDevDetails((v) => !v)}
                onNavigate={onNavigate}
              />
            </aside>
          )}
        </div>
      )}
    </AppLayout>
  );
}

// ── Event row ─────────────────────────────────────────────────────────────────

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
            <span key={i} className="history-event-summary__part">
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

      <div
        className="history-event-actions"
        onClick={(e) => e.stopPropagation()}
      >
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
  if (event.durationSeconds !== undefined) parts.push(`Duration: ${formatDuration(event.durationSeconds)}`);
  if (event.peakRamMb !== undefined) parts.push(`RAM: ${formatMemory(event.peakRamMb)}`);
  if (event.peakVramMb !== undefined) parts.push(`VRAM: ${formatMemory(event.peakVramMb)}`);
  if (event.errorSummary) parts.push(event.errorSummary);
  return parts;
}

// ── Status badge ──────────────────────────────────────────────────────────────

function HistoryStatusBadge({ status }: { status: HistoryEventStatus }) {
  return (
    <span className={`history-status history-status--${STATUS_TONES[status]}`}>
      <span className="history-status__dot" aria-hidden="true" />
      {EVENT_STATUS_LABELS[status]}
    </span>
  );
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function EventDetailPanel({
  event,
  showDevDetails,
  onClose,
  onToggleDevDetails,
  onNavigate,
}: {
  event: HistoryEvent;
  showDevDetails: boolean;
  onClose: () => void;
  onToggleDevDetails: () => void;
  onNavigate: (route: AppRouteId) => void;
}) {
  const EventIcon = EVENT_ICONS[event.type];
  const isRun = event.type === "run";
  const isRemoval = event.type === "workflow_removed";

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
            <span className="detail-panel__type">{EVENT_TYPE_LABELS[event.type]}</span>
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

      {event.prompt && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">Prompt</div>
          <p className="history-detail-prompt">{event.prompt}</p>
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
          {event.durationSeconds !== undefined && (
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(event.durationSeconds)}</dd>
            </div>
          )}
          {event.peakRamMb !== undefined && (
            <div>
              <dt>Peak RAM</dt>
              <dd>{formatMemory(event.peakRamMb)}</dd>
            </div>
          )}
          {event.peakVramMb !== undefined && (
            <div>
              <dt>Peak VRAM</dt>
              <dd>{formatMemory(event.peakVramMb)}</dd>
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

      {event.usedSettings && Object.keys(event.usedSettings).length > 0 && (
        <div className="detail-panel__section">
          <div className="detail-panel__section-label">Settings used</div>
          <dl className="history-settings-list">
            {Object.entries(event.usedSettings).map(([key, val]) => (
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
          <div className="detail-panel__section-label">What went wrong</div>
          <p className="history-detail-error">{event.errorSummary}</p>
        </div>
      )}

      <div className="detail-panel__section">
        <div className="detail-panel__section-label">Actions</div>
        <div className="detail-panel__actions">
          {isRun && event.outputRef && (
            <button className="secondary-button secondary-button--full" type="button">
              <ExternalLink size={14} aria-hidden="true" />
              Open result
            </button>
          )}
          {!isRemoval && (
            <button
              className="secondary-button secondary-button--full"
              type="button"
              onClick={() => onNavigate("home")}
            >
              <Package size={14} aria-hidden="true" />
              Open workflow
            </button>
          )}
          {isRun && event.usedSettings && (
            <button className="secondary-button secondary-button--full" type="button">
              <RotateCcw size={14} aria-hidden="true" />
              Reuse settings
            </button>
          )}
          {isRemoval && (
            <button className="secondary-button secondary-button--full" type="button">
              <Download size={14} aria-hidden="true" />
              Reinstall
            </button>
          )}
        </div>
      </div>

      {event.developerDetails && (
        <div className="detail-developer">
          <button
            className="detail-developer__toggle"
            type="button"
            onClick={onToggleDevDetails}
            aria-expanded={showDevDetails}
          >
            {showDevDetails ? (
              <ChevronDown size={14} aria-hidden="true" />
            ) : (
              <ChevronRight size={14} aria-hidden="true" />
            )}
            Developer details
          </button>
          {showDevDetails && (
            <dl className="detail-list detail-list--compact detail-developer__content">
              {Object.entries(event.developerDetails).map(([key, val]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd className="detail-dev-value">{val}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
    </>
  );
}

// ── Loading state ─────────────────────────────────────────────────────────────

function HistoryLoading() {
  return (
    <div className="history-loading" aria-label="Loading history" aria-live="polite">
      {[...Array(7)].map((_, i) => (
        <div key={i} className="history-skeleton" />
      ))}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function HistoryEmpty({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  return (
    <div className="history-empty">
      <div className="history-empty__icon" aria-hidden="true">
        <Clock size={36} />
      </div>
      <h2>No history yet</h2>
      <p>Run a workflow or install one to start building your history.</p>
      <button className="primary-button" type="button" onClick={() => onNavigate("home")}>
        Open Workflows
      </button>
    </div>
  );
}

// ── Error state ───────────────────────────────────────────────────────────────

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
