import { useEffect, useRef, useState } from "react";
import { DownloadCloud, ExternalLink, Loader2, Search, SlidersHorizontal, X } from "lucide-react";

import {
  fetchModelDownloadStatus,
  searchCivitaiLoras,
  startCivitaiLoraDownload,
  resolveBackendUrl,
  type CivitaiLoraBaseModelDetection,
  type CivitaiLoraCard,
  type CivitaiLoraSearchResponse,
  type DashboardControlDef,
  type WorkflowInputDef,
} from "../../lib/api/noofyApi";
import { openExternalUrl } from "../../lib/openExternalUrl";

interface CivitaiLoraBrowserModalProps {
  workflowId: string;
  control: DashboardControlDef;
  input: WorkflowInputDef;
  inputValues: Record<string, unknown>;
  currentValue: unknown;
  onClose: () => void;
  onDownloadCompleted: (targetFilename: string, observedValue: string | null) => void;
}

interface DownloadState {
  jobId: string;
  cardKey: string;
  targetFilename: string;
  observedValue: string | null;
  status: "queued" | "running" | "completed" | "failed" | "canceled" | string;
  message: string;
}

type FilterOverride = string | null | undefined;

export function CivitaiLoraBrowserModal({
  workflowId,
  control,
  input,
  inputValues,
  currentValue,
  onClose,
  onDownloadCompleted,
}: CivitaiLoraBrowserModalProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [filterOverride, setFilterOverride] = useState<FilterOverride>(undefined);
  const [showFilters, setShowFilters] = useState(false);
  const [response, setResponse] = useState<CivitaiLoraSearchResponse | null>(null);
  const [items, setItems] = useState<CivitaiLoraCard[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [download, setDownload] = useState<DownloadState | null>(null);
  const requestSeqRef = useRef(0);
  const completedDownloadJobsRef = useRef(new Set<string>());

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    void runSearch({ append: false, cursor: null });
  }, [workflowId, input.id, debouncedQuery, filterOverride]);

  useEffect(() => {
    if (!download || !download.jobId || ["completed", "failed", "canceled"].includes(download.status)) return;
    let canceled = false;
    const timer = window.setInterval(async () => {
      try {
        const status = await fetchModelDownloadStatus(download.jobId);
        if (canceled) return;
        setDownload((current) =>
          current && current.jobId === download.jobId
            ? { ...current, status: status.status, message: status.user_facing_message }
            : current,
        );
        if (status.status === "completed" && !completedDownloadJobsRef.current.has(download.jobId)) {
          completedDownloadJobsRef.current.add(download.jobId);
          onDownloadCompleted(download.targetFilename, download.observedValue);
        }
      } catch (err) {
        if (!canceled) {
          setDownload((current) =>
            current && current.jobId === download.jobId
              ? { ...current, status: "failed", message: err instanceof Error ? err.message : String(err) }
              : current,
          );
        }
      }
    }, 900);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [download?.jobId, download?.status, onDownloadCompleted]);

  async function runSearch({ append, cursor }: { append: boolean; cursor: string | null }) {
    const requestId = ++requestSeqRef.current;
    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
      setItems([]);
      setNextCursor(null);
    }
    setError(null);
    try {
      const result = await searchCivitaiLoras({
        workflow_id: workflowId,
        lora_input_id: input.id,
        input_values: inputValues,
        query: debouncedQuery,
        base_model: typeof filterOverride === "string" ? filterOverride : null,
        clear_base_model_filter: filterOverride === null,
        cursor,
        sort: "Most Downloaded",
      });
      if (requestId !== requestSeqRef.current) return;
      setResponse(result);
      setItems((current) => (append ? [...current, ...result.items] : result.items));
      setNextCursor(result.next_cursor);
      if (result.status !== "ok") {
        setError(result.user_facing_message);
      }
    } catch (err) {
      if (requestId === requestSeqRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (requestId === requestSeqRef.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }

  async function handleDownload(card: CivitaiLoraCard) {
    const observedValue = typeof currentValue === "string" ? currentValue : currentValue == null ? null : String(currentValue);
    const cardKey = loraCardKey(card);
    setDownload({
      jobId: "",
      cardKey,
      targetFilename: card.file_name,
      observedValue,
      status: "queued",
      message: "CivitAI LoRA download is queued.",
    });
    try {
      const started = await startCivitaiLoraDownload({
        workflow_id: workflowId,
        lora_input_id: input.id,
        model_id: card.model_id,
        model_version_id: card.model_version_id,
        file_id: card.file_id,
        observed_lora_value: observedValue,
      });
      completedDownloadJobsRef.current.delete(started.job_id);
      setDownload({
        jobId: started.job_id,
        cardKey,
        targetFilename: started.target_filename,
        observedValue: started.observed_lora_value,
        status: started.status,
        message: started.user_facing_message,
      });
    } catch (err) {
      setDownload({
        jobId: "",
        cardKey,
        targetFilename: card.file_name,
        observedValue,
        status: "failed",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  const detection = response?.detection;
  const activeBaseModel = response?.base_model_filter ?? null;
  const selectedFilterValue = filterOverride === undefined ? activeBaseModel ?? "" : filterOverride ?? "";
  const filterOptions = detection?.available_base_models ?? [];
  const modalTitle = control.label || "LoRA browser";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="civitai-lora-browser-title">
      <section className="civitai-lora-modal">
        <header className="civitai-lora-modal__header">
          <div>
            <p className="eyebrow">CivitAI LoRA browser</p>
            <h2 id="civitai-lora-browser-title">{modalTitle}</h2>
            <p>Search LoRAs likely made for this AI model type and download them into Noofy Models.</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="civitai-lora-modal__toolbar">
          <label className="civitai-lora-search">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">Search LoRAs</span>
            <input
              value={query}
              placeholder="Search LoRAs by name or keyword"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="civitai-lora-modal__chips">
            <span className="filter-chip">
              Base model: {activeBaseModel ?? "Not detected"}
            </span>
            <button
              className="secondary-button secondary-button--small"
              type="button"
              onClick={() => setShowFilters((value) => !value)}
            >
              <SlidersHorizontal size={14} aria-hidden="true" />
              Filters
            </button>
          </div>
          {showFilters ? (
            <div className="civitai-lora-filters">
              {detection ? (
                <DetectionSummary
                  detection={detection}
                  onSelectBaseModel={(baseModel) => {
                    setFilterOverride(baseModel ?? undefined);
                  }}
                />
              ) : null}
              <label>
                <span>Base model filter</span>
                <select
                  value={selectedFilterValue}
                  onChange={(event) => {
                    const value = event.target.value;
                    setFilterOverride(value === "__clear" ? null : value || undefined);
                  }}
                >
                  <option value="">Use detected model</option>
                  <option value="__clear">Clear model filter</option>
                  {filterOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
        </div>

        <div className="civitai-lora-modal__body">
          {error ? (
            <div className="notice notice--warning notice--compact" role="status">
              <div>
                <strong>{response?.status === "api_key_required" ? "CivitAI API key required" : "CivitAI search"}</strong>
                <span>{error}</span>
              </div>
            </div>
          ) : null}

          {loading ? (
            <div className="civitai-lora-modal__loading">
              <Loader2 className="spin" size={18} aria-hidden="true" />
              <span>Searching CivitAI through Noofy...</span>
            </div>
          ) : items.length === 0 ? (
            <div className="civitai-lora-empty">
              No matching LoRAs found for this AI model. Try another search term or change the model filter.
            </div>
          ) : (
            <div className="civitai-lora-grid">
              {items.map((card) => (
                <LoraCard
                  key={loraCardKey(card)}
                  card={card}
                  download={download}
                  onDownload={() => void handleDownload(card)}
                />
              ))}
            </div>
          )}
        </div>

        <footer className="civitai-lora-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!nextCursor || loadingMore || loading}
            onClick={() => void runSearch({ append: true, cursor: nextCursor })}
          >
            {loadingMore ? <Loader2 className="spin" size={16} aria-hidden="true" /> : null}
            Load more
          </button>
        </footer>
      </section>
    </div>
  );
}

function DetectionSummary({
  detection,
  onSelectBaseModel,
}: {
  detection: CivitaiLoraBaseModelDetection;
  onSelectBaseModel: (baseModel: string | null) => void;
}) {
  return (
    <div className="civitai-lora-detection">
      <strong>{detection.status === "unknown" ? "We could not detect the AI model type automatically." : detection.message}</strong>
      {detection.candidates.length > 1 ? (
        <select
          aria-label="Detected model candidate"
          defaultValue={detection.candidates.find((candidate) => candidate.base_model === detection.base_model)?.id ?? detection.candidates[0]?.id}
          onChange={(event) => {
            const candidate = detection.candidates.find((item) => item.id === event.target.value);
            onSelectBaseModel(candidate?.base_model ?? null);
          }}
        >
          {detection.candidates.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.label}{candidate.base_model ? ` · ${candidate.base_model}` : ""}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

function LoraCard({
  card,
  download,
  onDownload,
}: {
  card: CivitaiLoraCard;
  download: DownloadState | null;
  onDownload: () => void;
}) {
  const cardKey = loraCardKey(card);
  const isDownloading = download?.cardKey === cardKey && !["completed", "failed", "canceled"].includes(download.status);
  const isDownloaded = card.already_downloaded || (download?.cardKey === cardKey && download.status === "completed");
  return (
    <article className="civitai-lora-card">
      <div className="civitai-lora-card__preview">
        {card.preview_image_url ? (
          <img src={resolveBackendUrl(card.preview_image_url, { includeToken: true })} alt={`${card.name} preview`} />
        ) : (
          <span>No preview</span>
        )}
      </div>
      <div className="civitai-lora-card__body">
        <h3>{card.name}</h3>
        <p>
          {card.creator ? `by ${card.creator}` : "Unknown creator"}
          {card.base_model ? ` · ${card.base_model}` : ""}
        </p>
        <div className="civitai-lora-card__meta">
          <span>{formatFileSize(card.file_size_bytes)}</span>
          <span>{formatCount(card.download_count)} downloads</span>
          <span>{formatCount(card.thumbs_up_count)} likes</span>
        </div>
        {card.trigger_words.length > 0 ? (
          <div className="civitai-lora-card__triggers">
            {card.trigger_words.slice(0, 6).map((word) => (
              <span key={word}>{word}</span>
            ))}
          </div>
        ) : null}
        {download?.cardKey === cardKey && download.message ? (
          <small className={download.status === "failed" ? "field-error" : undefined}>{download.message}</small>
        ) : null}
      </div>
      <div className="civitai-lora-card__actions">
        <button className="secondary-button secondary-button--small" type="button" disabled={isDownloading || isDownloaded} onClick={onDownload}>
          {isDownloading ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <DownloadCloud size={14} aria-hidden="true" />}
          {isDownloaded ? "Downloaded" : "Download"}
        </button>
        <button
          className="secondary-button secondary-button--small"
          type="button"
          onClick={() => void openExternalUrl(card.model_page_url)}
        >
          <ExternalLink size={14} aria-hidden="true" />
          Open on CivitAI
        </button>
      </div>
    </article>
  );
}

function loraCardKey(card: CivitaiLoraCard): string {
  return `${card.model_id}:${card.model_version_id}:${card.file_id ?? "primary"}`;
}

function formatFileSize(bytes: number | null): string {
  if (!bytes || bytes <= 0) return "Unknown size";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

function formatCount(value: number | null): string {
  if (value == null) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}
