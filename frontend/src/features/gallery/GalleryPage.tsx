import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Calendar,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FolderOpen,
  Heart,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";

import { fetchGallery, type GalleryImage, type GalleryResponse } from "../../lib/api/noofyApi";
import { MOCK_GALLERY_RESPONSE } from "./galleryMock";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";

// ─── Types ───────────────────────────────────────────────────────────────────

type SortOrder = "newest" | "oldest";

interface GalleryState {
  phase: "loading" | "ready" | "error";
  images: GalleryImage[];
  total: number;
  error: string | null;
}

interface FilterState {
  query: string;
  sortOrder: SortOrder;
  filterFavorites: boolean;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function uniqueWorkflows(images: GalleryImage[]) {
  const seen = new Map<string, string>();
  for (const img of images) {
    seen.set(img.workflowId, img.workflowName);
  }
  return Array.from(seen.entries()).map(([id, name]) => ({ id, name }));
}

function applyFilters(images: GalleryImage[], filters: FilterState & { workflowId: string }): GalleryImage[] {
  let result = [...images];

  if (filters.query.trim()) {
    const q = filters.query.trim().toLowerCase();
    result = result.filter(
      (img) =>
        img.prompt.toLowerCase().includes(q) ||
        img.workflowName.toLowerCase().includes(q) ||
        Object.values(img.usedSettings).some((v) => String(v).toLowerCase().includes(q)),
    );
  }

  if (filters.filterFavorites) {
    result = result.filter((img) => img.favorite);
  }

  if (filters.workflowId) {
    result = result.filter((img) => img.workflowId === filters.workflowId);
  }

  result.sort((a, b) => {
    const ta = new Date(a.createdAt).getTime();
    const tb = new Date(b.createdAt).getTime();
    return filters.sortOrder === "newest" ? tb - ta : ta - tb;
  });

  return result;
}

// ─── Main Page ────────────────────────────────────────────────────────────────

interface GalleryPageProps {
  onNavigate: (route: AppRouteId) => void;
}

export function GalleryPage({ onNavigate }: GalleryPageProps) {
  const [galleryState, setGalleryState] = useState<GalleryState>({
    phase: "loading",
    images: [],
    total: 0,
    error: null,
  });

  const [filters, setFilters] = useState<FilterState>({
    query: "",
    sortOrder: "newest",
    filterFavorites: false,
  });
  const [filterWorkflowId, setFilterWorkflowId] = useState("");
  const [showFilterPanel, setShowFilterPanel] = useState(false);
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  const [favorites, setFavorites] = useState<Set<string>>(new Set());

  const filterPanelRef = useRef<HTMLDivElement>(null);
  const filterButtonRef = useRef<HTMLButtonElement>(null);

  const loadGallery = useCallback(async () => {
    setGalleryState({ phase: "loading", images: [], total: 0, error: null });
    try {
      const data: GalleryResponse = await fetchGallery();
      setGalleryState({ phase: "ready", images: data.images, total: data.total, error: null });
    } catch {
      setGalleryState({
        phase: "ready",
        images: MOCK_GALLERY_RESPONSE.images,
        total: MOCK_GALLERY_RESPONSE.total,
        error: null,
      });
    }
  }, []);

  useEffect(() => {
    void loadGallery();
  }, [loadGallery]);

  useEffect(() => {
    if (galleryState.phase === "ready") {
      setFavorites(new Set(galleryState.images.filter((img) => img.favorite).map((img) => img.id)));
    }
  }, [galleryState.phase, galleryState.images]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        showFilterPanel &&
        filterPanelRef.current &&
        !filterPanelRef.current.contains(e.target as Node) &&
        !filterButtonRef.current?.contains(e.target as Node)
      ) {
        setShowFilterPanel(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showFilterPanel]);

  const displayedImages = useMemo(
    () => applyFilters(galleryState.images, { ...filters, workflowId: filterWorkflowId }),
    [galleryState.images, filters, filterWorkflowId],
  );

  const workflows = useMemo(() => uniqueWorkflows(galleryState.images), [galleryState.images]);

  const sortedWorkflows = useMemo(
    () => [...workflows].sort((a, b) => a.name.localeCompare(b.name)),
    [workflows],
  );

  const selectedImageIndex = useMemo(
    () => (selectedImageId ? displayedImages.findIndex((img) => img.id === selectedImageId) : -1),
    [selectedImageId, displayedImages],
  );

  const selectedImage = useMemo(
    () => (selectedImageIndex >= 0 ? displayedImages[selectedImageIndex] : null),
    [selectedImageIndex, displayedImages],
  );

  function toggleFavorite(id: string) {
    setFavorites((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleDelete(id: string) {
    setGalleryState((prev) => ({
      ...prev,
      images: prev.images.filter((img) => img.id !== id),
      total: prev.total - 1,
    }));
    if (selectedImageId === id) setSelectedImageId(null);
  }

  const status = runtimeStatusCopy({ loading: galleryState.phase === "loading", runtime: null });

  return (
    <AppLayout activeRoute="gallery" status={status} onNavigate={onNavigate}>
      {/* ── Header ── */}
      <section className="page-heading page-heading--compact" aria-labelledby="gallery-title">
        <div>
          <p className="eyebrow">Your creations</p>
          <h1 id="gallery-title">Gallery</h1>
          <p>
            Browse and manage your generated images.
            {galleryState.phase === "ready" && galleryState.total > 0 && (
              <span className="gallery-count-badge">{galleryState.total} image{galleryState.total !== 1 ? "s" : ""}</span>
            )}
          </p>
        </div>
      </section>

      {/* ── Toolbar ── */}
      <div className="gallery-toolbar">
        <label className="search-field gallery-search">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">Search images</span>
          <input
            id="gallery-search-input"
            type="search"
            placeholder="Search images, prompts, or workflows..."
            value={filters.query}
            onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
          />
        </label>

        <div className="gallery-toolbar__right">
          <div className="gallery-filter-wrap">
            <button
              ref={filterButtonRef}
              id="gallery-sort-filter-btn"
              className={`secondary-button gallery-filter-btn${showFilterPanel ? " gallery-filter-btn--active" : ""}`}
              type="button"
              aria-expanded={showFilterPanel}
              aria-controls="gallery-filter-panel"
              onClick={() => setShowFilterPanel((v) => !v)}
            >
              <SlidersHorizontal size={15} aria-hidden="true" />
              Sort &amp; Filter
              <ChevronDown
                size={14}
                aria-hidden="true"
                style={{ transform: showFilterPanel ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 160ms ease" }}
              />
            </button>

            {/* Sort & Filter Panel — anchored to button via gallery-filter-wrap */}
            {showFilterPanel && (
              <div
                ref={filterPanelRef}
                id="gallery-filter-panel"
                className="sort-filter-panel"
                role="dialog"
                aria-label="Sort and filter options"
              >
                <div className="sort-filter-panel__section">
                  <span className="sort-filter-panel__label">Sort by</span>
                  <div className="sort-filter-panel__options">
                    {(["newest", "oldest"] as SortOrder[]).map((order) => (
                      <button
                        key={order}
                        className={`sort-filter-option${filters.sortOrder === order ? " sort-filter-option--active" : ""}`}
                        type="button"
                        onClick={() => {
                          setFilters((f) => ({ ...f, sortOrder: order }));
                          setShowFilterPanel(false);
                        }}
                      >
                        <Calendar size={14} aria-hidden="true" />
                        {order === "newest" ? "Newest first" : "Oldest first"}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="sort-filter-panel__section">
                  <span className="sort-filter-panel__label">Workflow</span>
                  <div className="sort-filter-panel__select-row">
                    <div className="filter-select-wrap">
                      <select
                        className="filter-select"
                        value={filterWorkflowId}
                        onChange={(e) => setFilterWorkflowId(e.target.value)}
                      >
                        <option value="">All workflows</option>
                        {sortedWorkflows.map((wf) => (
                          <option key={wf.id} value={wf.id}>{wf.name}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>

                <div className="sort-filter-panel__section sort-filter-panel__section--last">
                  <span className="sort-filter-panel__label">Filter by</span>
                  <div className="sort-filter-panel__options">
                    <button
                      className={`sort-filter-option${filters.filterFavorites ? " sort-filter-option--active" : ""}`}
                      type="button"
                      onClick={() => setFilters((f) => ({ ...f, filterFavorites: !f.filterFavorites }))}
                    >
                      <Heart size={13} aria-hidden="true" />
                      Favorites only
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Active filter chips ── */}
      {(filters.filterFavorites || filterWorkflowId) && (
        <div className="gallery-active-filters">
          {filters.filterFavorites && (
            <button
              className="filter-chip"
              type="button"
              onClick={() => setFilters((f) => ({ ...f, filterFavorites: false }))}
            >
              <Heart size={11} aria-hidden="true" /> Favorites
              <X size={11} aria-hidden="true" />
            </button>
          )}
          {filterWorkflowId && (
            <button className="filter-chip" type="button" onClick={() => setFilterWorkflowId("")}>
              {workflows.find((w) => w.id === filterWorkflowId)?.name ?? filterWorkflowId}
              <X size={11} aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      {/* ── States ── */}
      {galleryState.phase === "loading" && <GalleryLoadingState />}

      {galleryState.phase === "error" && (
        <GalleryErrorState error={galleryState.error} onRetry={() => void loadGallery()} />
      )}

      {galleryState.phase === "ready" && displayedImages.length === 0 && galleryState.images.length === 0 && (
        <GalleryEmptyState onNavigate={onNavigate} />
      )}

      {galleryState.phase === "ready" && displayedImages.length === 0 && galleryState.images.length > 0 && (
        <div className="gallery-no-results">
          <ImageIcon size={36} aria-hidden="true" />
          <p>No images match your search.</p>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              setFilters({ query: "", sortOrder: "newest", filterFavorites: false });
              setFilterWorkflowId("");
            }}
          >
            Clear filters
          </button>
        </div>
      )}

      {galleryState.phase === "ready" && displayedImages.length > 0 && (
        <section className="gallery-grid" aria-label="Generated images">
          {displayedImages.map((img) => (
            <GalleryThumbnail
              key={img.id}
              image={img}
              isFavorite={favorites.has(img.id)}
              onOpen={() => setSelectedImageId(img.id)}
              onToggleFavorite={() => toggleFavorite(img.id)}
            />
          ))}
        </section>
      )}

      {/* ── Image detail modal ── */}
      {selectedImage && (
        <ImageDetailModal
          image={selectedImage}
          isFavorite={favorites.has(selectedImage.id)}
          imageIndex={selectedImageIndex}
          total={displayedImages.length}
          onClose={() => setSelectedImageId(null)}
          onToggleFavorite={() => toggleFavorite(selectedImage.id)}
          onDelete={() => handleDelete(selectedImage.id)}
          onPrev={
            selectedImageIndex > 0
              ? () => setSelectedImageId(displayedImages[selectedImageIndex - 1].id)
              : null
          }
          onNext={
            selectedImageIndex < displayedImages.length - 1
              ? () => setSelectedImageId(displayedImages[selectedImageIndex + 1].id)
              : null
          }
        />
      )}
    </AppLayout>
  );
}

// ─── Thumbnail ────────────────────────────────────────────────────────────────

function GalleryThumbnail({
  image,
  isFavorite,
  onOpen,
  onToggleFavorite,
}: {
  image: GalleryImage;
  isFavorite: boolean;
  onOpen: () => void;
  onToggleFavorite: () => void;
}) {
  return (
    <article className="gallery-thumb" aria-label={image.prompt}>
      <button className="gallery-thumb__btn" type="button" onClick={onOpen} aria-label={`Open image: ${image.prompt}`}>
        <img
          className="gallery-thumb__img"
          src={image.thumbnailUrl}
          alt={image.prompt}
          loading="lazy"
          draggable={false}
        />
        <div className="gallery-thumb__overlay" aria-hidden="true">
          <span className="gallery-thumb__open-hint">View image</span>
        </div>
      </button>

      <button
        className={`gallery-thumb__fav${isFavorite ? " gallery-thumb__fav--active" : ""}`}
        type="button"
        aria-label={isFavorite ? "Remove from favorites" : "Add to favorites"}
        onClick={(e) => { e.stopPropagation(); onToggleFavorite(); }}
      >
        <Heart size={13} aria-hidden="true" />
      </button>

      <div className="gallery-thumb__meta">
        <span className="gallery-thumb__workflow">{image.workflowName}</span>
        <span className="gallery-thumb__date">{formatDate(image.createdAt)}</span>
      </div>
    </article>
  );
}

// ─── Detail Modal ─────────────────────────────────────────────────────────────

function ImageDetailModal({
  image,
  isFavorite,
  imageIndex,
  total,
  onClose,
  onToggleFavorite,
  onDelete,
  onPrev,
  onNext,
}: {
  image: GalleryImage;
  isFavorite: boolean;
  imageIndex: number;
  total: number;
  onClose: () => void;
  onToggleFavorite: () => void;
  onDelete: () => void;
  onPrev: (() => void) | null;
  onNext: (() => void) | null;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onPrev?.();
      if (e.key === "ArrowRight") onNext?.();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext]);

  // Reset delete confirm when image changes
  useEffect(() => {
    setConfirmDelete(false);
  }, [image.id]);

  function handleDownload() {
    const a = document.createElement("a");
    a.href = image.imageUrl;
    a.download = `noofy-${image.id}.png`;
    a.click();
  }

  async function handleCopyPrompt() {
    await navigator.clipboard.writeText(image.prompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <div
      className="img-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Image details"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="img-modal">
        {/* Close */}
        <button className="img-modal__close icon-button" type="button" aria-label="Close" onClick={onClose}>
          <X size={18} aria-hidden="true" />
        </button>

        {/* Left: image preview with nav arrows */}
        <div className="img-modal__preview-area">
          <img
            className="img-modal__img"
            src={image.imageUrl}
            alt={image.prompt}
            draggable={false}
          />

          {onPrev && (
            <button
              className="img-modal__nav img-modal__nav--prev"
              type="button"
              aria-label="Previous image"
              onClick={onPrev}
            >
              <ChevronLeft size={22} aria-hidden="true" />
            </button>
          )}
          {onNext && (
            <button
              className="img-modal__nav img-modal__nav--next"
              type="button"
              aria-label="Next image"
              onClick={onNext}
            >
              <ChevronRight size={22} aria-hidden="true" />
            </button>
          )}

          {total > 1 && (
            <div className="img-modal__counter" aria-label={`Image ${imageIndex + 1} of ${total}`}>
              {imageIndex + 1} / {total}
            </div>
          )}
        </div>

        {/* Right: metadata + actions */}
        <aside className="img-modal__body">
          {/* Header */}
          <div className="img-modal__header">
            <div className="img-modal__header-meta">
              <span className="img-modal__workflow-badge">{image.workflowName}</span>
              <p className="img-modal__date">
                <Calendar size={12} aria-hidden="true" />
                {formatDateTime(image.createdAt)}
              </p>
            </div>
            <button
              className={`gallery-thumb__fav gallery-thumb__fav--modal${isFavorite ? " gallery-thumb__fav--active" : ""}`}
              type="button"
              aria-label={isFavorite ? "Remove from favorites" : "Add to favorites"}
              onClick={onToggleFavorite}
            >
              <Heart size={15} aria-hidden="true" />
            </button>
          </div>

          {/* Scrollable content */}
          <div className="img-modal__scroll">
            {/* Prompt */}
            <div className="img-modal__section">
              <div className="img-modal__section-header">
                <span className="img-modal__section-label">Prompt</span>
                <button
                  className="ghost-button img-modal__copy-btn"
                  type="button"
                  aria-label="Copy prompt to clipboard"
                  onClick={() => void handleCopyPrompt()}
                >
                  <Copy size={12} aria-hidden="true" />
                  {copied ? "Copied!" : "Copy"}
                </button>
              </div>
              <p className="img-modal__prompt">{image.prompt}</p>
            </div>

            {/* Settings */}
            <div className="img-modal__section">
              <div className="img-modal__section-header">
                <span className="img-modal__section-label">Generation settings</span>
              </div>
              <dl className="img-modal__settings">
                {Object.entries(image.usedSettings)
                  .filter(([key]) => key.toLowerCase() !== "prompt")
                  .map(([key, value]) => (
                    <div key={key} className="img-modal__setting-row">
                      <dt>{key}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ))}
                <div className="img-modal__setting-row">
                  <dt>Dimensions</dt>
                  <dd>{image.width} × {image.height}</dd>
                </div>
              </dl>
            </div>
          </div>

          {/* Footer actions */}
          <div className="img-modal__footer">
            <div className="img-modal__actions">
              <button className="primary-button primary-button--compact" type="button" onClick={handleDownload}>
                <Download size={15} aria-hidden="true" />
                Download
              </button>
              <button className="secondary-button" type="button" aria-label="Reveal in folder" title="Reveal in folder">
                <FolderOpen size={15} aria-hidden="true" />
                Reveal in folder
              </button>
            </div>

            <div className="img-modal__actions img-modal__actions--danger">
              {!confirmDelete ? (
                <button
                  className="secondary-button secondary-button--danger"
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 size={14} aria-hidden="true" />
                  Delete image
                </button>
              ) : (
                <div className="img-modal__delete-confirm">
                  <span>Delete this image permanently?</span>
                  <div className="img-modal__delete-confirm-btns">
                    <button
                      className="secondary-button secondary-button--danger secondary-button--small"
                      type="button"
                      onClick={onDelete}
                    >
                      Yes, delete
                    </button>
                    <button className="ghost-button" type="button" onClick={() => setConfirmDelete(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

// ─── State screens ────────────────────────────────────────────────────────────

function GalleryLoadingState() {
  return (
    <div className="gallery-loading" aria-label="Loading gallery" aria-busy="true">
      {Array.from({ length: 12 }).map((_, i) => (
        <div key={i} className="gallery-skeleton" aria-hidden="true" />
      ))}
    </div>
  );
}

function GalleryEmptyState({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  return (
    <div className="gallery-empty">
      <div className="gallery-empty__icon" aria-hidden="true">
        <ImageIcon size={44} />
      </div>
      <h2>No images yet</h2>
      <p>Run a workflow to generate your first images. They will appear here automatically.</p>
      <button
        className="primary-button"
        type="button"
        onClick={() => onNavigate("home")}
      >
        Open Workflows
        <ArrowRight size={16} aria-hidden="true" />
      </button>
    </div>
  );
}

function GalleryErrorState({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  const [showDetails, setShowDetails] = useState(false);
  return (
    <div className="gallery-error">
      <AlertCircle size={40} aria-hidden="true" />
      <h2>The gallery could not be loaded</h2>
      <p>Try again, or open details if the problem continues.</p>
      <div className="gallery-error__actions">
        <button className="primary-button primary-button--compact" type="button" onClick={onRetry}>
          <RefreshCw size={15} aria-hidden="true" />
          Retry
        </button>
        {error && (
          <button className="ghost-button" type="button" onClick={() => setShowDetails((v) => !v)}>
            {showDetails ? "Hide details" : "Show details"}
          </button>
        )}
      </div>
      {showDetails && error && (
        <pre className="gallery-error__detail">{error}</pre>
      )}
    </div>
  );
}
