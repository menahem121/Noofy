import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Box,
  Calendar,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileAudio,
  FileText,
  Film,
  Heart,
  Image as ImageIcon,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";

import {
  deleteGalleryItem,
  fetchGallery,
  galleryContentUrl,
  galleryPreviewUrl,
  updateGalleryFavorite,
  type GalleryItem,
  type GalleryKind,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { ThreeDViewer } from "../three-d/ThreeDViewer";

type KindFilter = "all" | GalleryKind;
type SortOrder = "newest" | "oldest";

interface PendingGalleryDeletion {
  ids: string[];
  label: string | null;
  busy: boolean;
  error: string | null;
}

interface GalleryPageProps {
  onNavigate: (route: AppRouteId) => void;
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function formatBytes(value: number | null): string {
  if (value === null) return "Unknown";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDuration(value: number | null): string | null {
  if (value === null) return null;
  const total = Math.max(0, Math.round(value));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes ? `${minutes}:${String(seconds).padStart(2, "0")}` : `0:${String(seconds).padStart(2, "0")}`;
}

function dimensions(item: GalleryItem): string | null {
  return item.width && item.height ? `${item.width} x ${item.height}` : null;
}

function kindLabel(kind: GalleryKind): string {
  return kind === "image" ? "Image" : kind === "video" ? "Video" : kind === "audio" ? "Audio" : kind === "3d" ? "3D model" : "File";
}

function itemLabel(item: GalleryItem): string {
  return item.filename || item.widgetTitle || `${kindLabel(item.kind)} output`;
}

function directDownload(item: GalleryItem) {
  if (item.fileState === "missing") return;
  const anchor = document.createElement("a");
  anchor.href = galleryContentUrl(item, { download: true });
  anchor.download = item.filename;
  anchor.click();
}

export function GalleryPage({ onNavigate }: GalleryPageProps) {
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [query, setQuery] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [pendingDeletion, setPendingDeletion] = useState<PendingGalleryDeletion | null>(null);

  const loadGallery = useCallback(async () => {
    setPhase("loading");
    setError(null);
    try {
      const response = await fetchGallery();
      setItems(response.items);
      setPhase("ready");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase("error");
    }
  }, []);

  useEffect(() => { void loadGallery(); }, [loadGallery]);

  const displayedItems = useMemo(() => {
    const search = query.trim().toLowerCase();
    return items
      .filter((item) => kindFilter === "all" || item.kind === kindFilter)
      .filter((item) => !favoritesOnly || item.favorite)
      .filter((item) => !search || [
        item.filename,
        item.workflowName,
        item.widgetTitle,
        item.prompt,
        ...Object.values(item.usedSettings).map(String),
      ].some((value) => value.toLowerCase().includes(search)))
      .sort((left, right) => {
        const delta = new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime();
        return sortOrder === "newest" ? delta : -delta;
      });
  }, [favoritesOnly, items, kindFilter, query, sortOrder]);

  const checkedItems = useMemo(
    () => displayedItems.filter((item) => checkedIds.has(item.id)),
    [checkedIds, displayedItems],
  );
  const downloadableCheckedItems = useMemo(
    () => checkedItems.filter((item) => item.fileState !== "missing" && Boolean(item.contentUrl)),
    [checkedItems],
  );
  const selectedIndex = selectedId ? displayedItems.findIndex((item) => item.id === selectedId) : -1;
  const selected = selectedIndex >= 0 ? displayedItems[selectedIndex] : null;
  const hasActiveFilters = kindFilter !== "all" || Boolean(query.trim()) || favoritesOnly;

  function clearFilters() {
    setKindFilter("all");
    setQuery("");
    setFavoritesOnly(false);
  }

  useEffect(() => {
    const visible = new Set(displayedItems.map((item) => item.id));
    setCheckedIds((current) => new Set([...current].filter((id) => visible.has(id))));
  }, [displayedItems]);

  async function toggleFavorite(item: GalleryItem) {
    const next = !item.favorite;
    setItems((current) => current.map((value) => value.id === item.id ? { ...value, favorite: next } : value));
    try {
      const saved = await updateGalleryFavorite(item.id, next);
      setItems((current) => current.map((value) => value.id === item.id ? saved : value));
    } catch {
      setItems((current) => current.map((value) => value.id === item.id ? { ...value, favorite: item.favorite } : value));
    }
  }

  function requestDeleteItems(itemsToDelete: GalleryItem[]) {
    if (itemsToDelete.length === 0) return;
    setPendingDeletion({
      ids: itemsToDelete.map((item) => item.id),
      label: itemsToDelete.length === 1 ? itemLabel(itemsToDelete[0]) : null,
      busy: false,
      error: null,
    });
  }

  async function confirmDeleteItems() {
    if (!pendingDeletion || pendingDeletion.busy) return;
    const ids = pendingDeletion.ids;
    setPendingDeletion((current) => current ? { ...current, busy: true, error: null } : current);
    const results = await Promise.allSettled(ids.map(deleteGalleryItem));
    const removed = new Set(ids.filter((_, index) => results[index].status === "fulfilled"));
    const failedIds = ids.filter((id) => !removed.has(id));
    setItems((current) => current.filter((item) => !removed.has(item.id)));
    setCheckedIds((current) => new Set([...current].filter((id) => !removed.has(id))));
    if (selectedId && removed.has(selectedId)) setSelectedId(null);
    if (failedIds.length === 0) {
      setPendingDeletion(null);
      return;
    }
    const failedItem = failedIds.length === 1
      ? items.find((item) => item.id === failedIds[0])
      : null;
    setPendingDeletion({
      ids: failedIds,
      label: failedItem ? itemLabel(failedItem) : "Gallery item",
      busy: false,
      error: failedIds.length === 1
        ? "This Gallery item could not be deleted. Try again."
        : "Some selected Gallery items could not be deleted. Try again.",
    });
  }

  return (
    <AppLayout activeRoute="gallery" onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="gallery-title">
        <div>
          <p className="eyebrow">Your creations</p>
          <h1 id="gallery-title">Gallery</h1>
          <p>Browse and manage your generated media.<span className="gallery-count-badge">{items.length} item{items.length === 1 ? "" : "s"}</span></p>
        </div>
      </section>

      <nav className="models-type-tabs gallery-kind-tabs" aria-label="Gallery media types">
        {([
          ["all", "All"], ["image", "Images"], ["video", "Videos"], ["audio", "Audio"], ["3d", "3D Models"], ["file", "Files"],
        ] as Array<[KindFilter, string]>).map(([value, label]) => (
          <button
            key={value}
            className={kindFilter === value ? "models-type-tab models-type-tab--active" : "models-type-tab"}
            type="button"
            aria-pressed={kindFilter === value}
            onClick={() => setKindFilter(value)}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="models-toolbar gallery-toolbar">
        <div className="search-field gallery-search">
          <Search size={17} aria-hidden="true" />
          <input aria-label="Search generated media" type="search" placeholder="Search items, prompts, or workflows..." value={query} onChange={(event) => setQuery(event.target.value)} />
          {query ? <button className="gallery-search__clear" type="button" aria-label="Clear Gallery search" onClick={() => setQuery("")}><X size={15} /></button> : null}
        </div>
        <div className="gallery-toolbar__right">
          <div className="filter-select-wrap">
            <select className="filter-select" aria-label="Sort Gallery items" value={sortOrder} onChange={(event) => setSortOrder(event.target.value as SortOrder)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
            <ChevronDown size={13} aria-hidden="true" />
          </div>
          <button className={favoritesOnly ? "secondary-button secondary-button--selected gallery-filter-btn" : "secondary-button gallery-filter-btn"} type="button" aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly((value) => !value)}>
            <Heart size={14} aria-hidden="true" /> Favorites
          </button>
        </div>
      </div>

      {checkedItems.length > 0 && (
        <div className="gallery-bulk-bar" role="region" aria-label="Selected item actions">
          <div className="gallery-bulk-bar__summary">
            <strong>{checkedItems.length} selected</strong>
            <span>{downloadableCheckedItems.length === checkedItems.length ? "Ready for bulk actions" : `${downloadableCheckedItems.length} available to download`}</span>
          </div>
          <div className="gallery-bulk-bar__actions">
            <button className="secondary-button" type="button" onClick={() => downloadableCheckedItems.forEach(directDownload)} disabled={downloadableCheckedItems.length === 0}><Download size={14} /> Download selected</button>
            <button className="secondary-button secondary-button--danger" type="button" onClick={() => requestDeleteItems(checkedItems)}>
              <Trash2 size={14} /> Delete selected
            </button>
            <button className="ghost-button" type="button" onClick={() => setCheckedIds(new Set())}>Clear selection</button>
          </div>
        </div>
      )}

      {phase === "loading" ? <GalleryLoadingState /> : null}
      {phase === "error" ? <GalleryErrorState error={error} onRetry={() => void loadGallery()} /> : null}
      {phase === "ready" && items.length === 0 ? <GalleryEmptyState onNavigate={onNavigate} /> : null}
      {phase === "ready" && items.length > 0 && displayedItems.length === 0 ? (
        <div className="gallery-no-results">
          <Search size={34} aria-hidden="true" />
          <h2>No media found</h2>
          <p>No generated media matches your current search and filters.</p>
          {hasActiveFilters ? <button className="secondary-button" type="button" onClick={clearFilters}>Clear filters</button> : null}
        </div>
      ) : null}
      {phase === "ready" && displayedItems.length > 0 ? (
        <section className="gallery-grid" aria-label="Generated media">
          {displayedItems.map((item) => (
            <GalleryCard
              key={item.id}
              item={item}
              checked={checkedIds.has(item.id)}
              onOpen={() => setSelectedId(item.id)}
              onCheck={() => setCheckedIds((current) => {
                const next = new Set(current);
                if (next.has(item.id)) next.delete(item.id); else next.add(item.id);
                return next;
              })}
              onFavorite={() => void toggleFavorite(item)}
            />
          ))}
        </section>
      ) : null}

      {selected ? (
        <MediaDetail
          item={selected}
          index={selectedIndex}
          total={displayedItems.length}
          onClose={() => setSelectedId(null)}
          onFavorite={() => void toggleFavorite(selected)}
          onDelete={() => requestDeleteItems([selected])}
          deleteDialogOpen={Boolean(pendingDeletion)}
          onPrev={selectedIndex > 0 ? () => setSelectedId(displayedItems[selectedIndex - 1].id) : null}
          onNext={selectedIndex < displayedItems.length - 1 ? () => setSelectedId(displayedItems[selectedIndex + 1].id) : null}
        />
      ) : null}
      {pendingDeletion ? (
        <GalleryDeletionDialog
          deletion={pendingDeletion}
          onCancel={() => setPendingDeletion(null)}
          onConfirm={() => void confirmDeleteItems()}
        />
      ) : null}
    </AppLayout>
  );
}

function GalleryCard({ item, checked, onOpen, onCheck, onFavorite }: { item: GalleryItem; checked: boolean; onOpen: () => void; onCheck: () => void; onFavorite: () => void }) {
  const label = item.workflowName || `${kindLabel(item.kind)} workflow`;
  return (
    <article className={checked ? "gallery-thumb gallery-thumb--selected" : "gallery-thumb"} aria-label={label}>
      <button className="gallery-thumb__btn" type="button" onClick={onOpen} aria-label={`Open ${kindLabel(item.kind).toLowerCase()}: ${label}`}>
        <CardVisual item={item} />
        <div className="gallery-thumb__overlay"><span className="gallery-thumb__open-hint">Open {kindLabel(item.kind).toLowerCase()}</span></div>
      </button>
      <label className="gallery-thumb__select"><input type="checkbox" checked={checked} aria-label={`Select ${label}`} onChange={onCheck} /><span className="gallery-thumb__checkbox" /></label>
      <button className={item.favorite ? "gallery-thumb__fav gallery-thumb__fav--active" : "gallery-thumb__fav"} type="button" aria-pressed={item.favorite} aria-label={item.favorite ? "Remove from favorites" : "Add to favorites"} onClick={onFavorite}><Heart size={13} /></button>
      <div className="gallery-thumb__meta">
        <strong className="gallery-thumb__label">{label}</strong>
      </div>
    </article>
  );
}

function CardVisual({ item }: { item: GalleryItem }) {
  if (item.fileState === "missing") {
    return <div className="gallery-thumb__missing"><AlertCircle size={34} /><span>Output unavailable</span></div>;
  }
  const badge = formatDuration(item.durationSeconds) ?? (item.kind === "file" || item.kind === "3d" ? item.extension?.replace(".", "").toUpperCase() : dimensions(item));
  const previewUrl = galleryPreviewUrl(item);
  if ((item.kind === "image" || (item.kind === "3d" && item.thumbnailUrl)) && previewUrl) {
    return <img className="gallery-thumb__img" src={previewUrl} alt={item.prompt || item.filename} loading="lazy" draggable={false} />;
  }
  const Icon = item.kind === "video" ? Film : item.kind === "audio" ? FileAudio : item.kind === "3d" ? Box : item.kind === "file" ? FileText : ImageIcon;
  return (
    <div className={`gallery-media-placeholder gallery-media-placeholder--${item.kind}`}>
      <Icon size={38} aria-hidden="true" />
      <strong>{item.kind === "file" ? (item.extension?.replace(".", "").toUpperCase() || "FILE") : kindLabel(item.kind)}</strong>
      {item.kind === "audio" ? <div className="gallery-waveform" aria-hidden="true">{[12, 24, 18, 32, 22, 28, 16, 26, 14].map((height, index) => <span key={index} style={{ height }} />)}</div> : null}
      {badge ? <span className="gallery-media-badge">{badge}</span> : null}
    </div>
  );
}

function MediaDetail({ item, index, total, onClose, onFavorite, onDelete, deleteDialogOpen, onPrev, onNext }: { item: GalleryItem; index: number; total: number; onClose: () => void; onFavorite: () => void; onDelete: () => void; deleteDialogOpen: boolean; onPrev: (() => void) | null; onNext: (() => void) | null }) {
  const contentUrl = galleryContentUrl(item);
  const isMissing = item.fileState === "missing";
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (deleteDialogOpen) return;
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft") onPrev?.();
      if (event.key === "ArrowRight") onNext?.();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [deleteDialogOpen, onClose, onNext, onPrev]);

  return (
    <div className="img-modal-backdrop" role="dialog" aria-modal="true" aria-label={`${kindLabel(item.kind)} details`} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={item.kind === "3d" ? "img-modal img-modal--three-d" : "img-modal"}>
        <button className="img-modal__close icon-button" type="button" aria-label="Close" onClick={onClose}><X size={18} /></button>
        <div className={item.kind === "3d" ? "img-modal__preview-area img-modal__preview-area--three-d" : "img-modal__preview-area"}>
          {isMissing ? <div className="img-modal__missing"><AlertCircle size={42} /><span>Output file unavailable</span></div> : <DetailPreview item={item} contentUrl={contentUrl} />}
          {onPrev ? <button className="img-modal__nav img-modal__nav--prev" type="button" aria-label="Previous item" onClick={onPrev}><ChevronLeft size={22} /></button> : null}
          {onNext ? <button className="img-modal__nav img-modal__nav--next" type="button" aria-label="Next item" onClick={onNext}><ChevronRight size={22} /></button> : null}
          {total > 1 ? <div className="img-modal__counter">{index + 1} / {total}</div> : null}
        </div>
        <aside className="img-modal__body">
          <div className="img-modal__header">
            <div className="img-modal__header-meta"><span className="img-modal__workflow-badge">{kindLabel(item.kind)}</span><strong className="gallery-detail-title">{item.filename}</strong><p className="img-modal__date"><Calendar size={12} />{formatDateTime(item.createdAt)}</p></div>
            <button className={item.favorite ? "gallery-thumb__fav gallery-thumb__fav--modal gallery-thumb__fav--active" : "gallery-thumb__fav gallery-thumb__fav--modal"} type="button" aria-pressed={item.favorite} aria-label={item.favorite ? "Remove from favorites" : "Add to favorites"} onClick={onFavorite}><Heart size={15} /></button>
          </div>
          <div className="img-modal__scroll">
            <div className="img-modal__section"><span className="img-modal__section-label">Generated with</span><p className="img-modal__prompt">{item.widgetTitle || item.workflowName}</p></div>
            {item.prompt ? <div className="img-modal__section"><span className="img-modal__section-label">Prompt</span><p className="img-modal__prompt">{item.prompt}</p></div> : null}
            <Metadata item={item} />
          </div>
          <div className="img-modal__footer">
            <div className="img-modal__actions">
              <button className="primary-button primary-button--compact" type="button" disabled={isMissing} onClick={() => directDownload(item)}><Download size={15} />Download</button>
              <button className="secondary-button" type="button" disabled={isMissing} onClick={() => window.open(contentUrl, "_blank", "noopener,noreferrer")}><ExternalLink size={14} />Open</button>
            </div>
            <button className="secondary-button secondary-button--danger" type="button" onClick={onDelete}><Trash2 size={14} />Delete item</button>
          </div>
        </aside>
      </div>
    </div>
  );
}

function GalleryDeletionDialog({
  deletion,
  onCancel,
  onConfirm,
}: {
  deletion: PendingGalleryDeletion;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const count = deletion.ids.length;
  const title = count === 1 ? "Delete Gallery item?" : `Delete ${count} Gallery items?`;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !deletion.busy) onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [deletion.busy, onCancel]);

  return (
    <div
      className="modal-backdrop gallery-delete-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="gallery-delete-title"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !deletion.busy) onCancel();
      }}
    >
      <section className="workflow-close-modal" aria-busy={deletion.busy}>
        <header className="workflow-close-modal__header">
          <h2 id="gallery-delete-title">{title}</h2>
          <p>
            {count === 1 && deletion.label ? (
              <>Delete <strong>{deletion.label}</strong> permanently? This cannot be undone.</>
            ) : (
              <>Delete these {count} selected Gallery items permanently? This cannot be undone.</>
            )}
          </p>
        </header>
        {deletion.error ? (
          <div className="notice notice--error" role="status">
            <AlertCircle size={18} aria-hidden="true" />
            <div>
              <strong>Gallery deletion failed</strong>
              <span>{deletion.error}</span>
            </div>
          </div>
        ) : null}
        <footer className="workflow-close-modal__footer">
          <button className="secondary-button" type="button" autoFocus disabled={deletion.busy} onClick={onCancel}>
            Cancel
          </button>
          <button className="danger-button" type="button" disabled={deletion.busy} onClick={onConfirm}>
            {deletion.busy ? "Deleting..." : count === 1 ? "Delete item" : "Delete items"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function DetailPreview({ item, contentUrl }: { item: GalleryItem; contentUrl: string }) {
  if (item.kind === "image") return <img className="img-modal__img" src={contentUrl} alt={item.prompt || item.filename} />;
  if (item.kind === "video") return <video className="gallery-detail-video" src={contentUrl} controls preload="metadata" />;
  if (item.kind === "audio") return <div className="gallery-detail-audio"><FileAudio size={64} /><strong>{item.filename}</strong><audio src={contentUrl} controls preload="metadata" /></div>;
  if (item.kind === "3d") return <ThreeDViewer className="gallery-detail-three-d" url={contentUrl} filename={item.filename} size={item.sizeBytes} autoPreviewUnknownSize />;
  return <div className="gallery-detail-file"><FileText size={72} /><strong>{item.extension?.replace(".", "").toUpperCase() || "FILE"}</strong><span>{item.filename}</span></div>;
}

function Metadata({ item }: { item: GalleryItem }) {
  return <dl className="img-modal__settings">
    <div className="img-modal__setting-row"><dt>Workflow</dt><dd>{item.workflowName}</dd></div>
    <div className="img-modal__setting-row"><dt>Type</dt><dd>{item.mimeType || item.extension || kindLabel(item.kind)}</dd></div>
    <div className="img-modal__setting-row"><dt>Size</dt><dd>{formatBytes(item.sizeBytes)}</dd></div>
    {dimensions(item) ? <div className="img-modal__setting-row"><dt>Dimensions</dt><dd>{dimensions(item)}</dd></div> : null}
    {formatDuration(item.durationSeconds) ? <div className="img-modal__setting-row"><dt>Duration</dt><dd>{formatDuration(item.durationSeconds)}</dd></div> : null}
    {item.fps !== null ? <div className="img-modal__setting-row"><dt>Frame rate</dt><dd>{item.fps} fps</dd></div> : null}
  </dl>;
}

function GalleryLoadingState() {
  return <div className="gallery-loading" aria-label="Loading gallery" aria-busy="true">{Array.from({ length: 12 }).map((_, index) => <div key={index} className="gallery-skeleton" />)}</div>;
}

function GalleryEmptyState({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  return <div className="gallery-empty"><div className="gallery-empty__icon"><Film size={42} /></div><h2>No saved media yet</h2><p>Generated images, videos, audio, 3D models, and files you save will appear here.</p><button className="primary-button" type="button" onClick={() => onNavigate("home")}>Open Workflows<ArrowRight size={16} /></button></div>;
}

function GalleryErrorState({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return <div className="gallery-error"><AlertCircle size={40} /><h2>The Gallery could not be loaded</h2><p>{error || "Try again in a moment."}</p><button className="primary-button primary-button--compact" type="button" onClick={onRetry}><RefreshCw size={15} />Retry</button></div>;
}
