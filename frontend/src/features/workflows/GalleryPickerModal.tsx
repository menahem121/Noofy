import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, Box, Check, FileAudio, Image as ImageIcon, Loader2, Search, Video, X } from "lucide-react";

import {
  fetchGallery,
  galleryPreviewUrl,
  type GalleryItem,
  type GalleryKind,
} from "../../lib/api/noofyApi";
import { audioMetadataLabel, fileMetadataLabel, formatMediaDuration, type GalleryMediaReference } from "./media";

type PickerKind = Extract<GalleryKind, "image" | "video" | "audio" | "3d">;

interface GalleryPickerModalProps {
  kind: PickerKind;
  acceptedExtensions?: string[];
  acceptedMimeTypes?: string[];
  onClose: () => void;
  onSelect: (reference: GalleryMediaReference) => void;
}

const PAGE_SIZE = 50;

export function GalleryPickerModal({
  kind,
  acceptedExtensions = [],
  acceptedMimeTypes = [],
  onClose,
  onSelect,
}: GalleryPickerModalProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  useEffect(() => {
    void loadPage({ append: false, cursor: null });
  }, [kind, debouncedQuery, acceptedExtensions.join(","), acceptedMimeTypes.join(",")]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  async function loadPage({ append, cursor }: { append: boolean; cursor: string | null }) {
    const requestId = ++requestSeq.current;
    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
      setItems([]);
      setSelectedId(null);
      setNextCursor(null);
    }
    setError(null);
    try {
      const response = await fetchGallery({
        kind,
        search: debouncedQuery,
        limit: PAGE_SIZE,
        cursor,
        acceptedExtensions,
        acceptedMimeTypes,
      });
      if (requestId !== requestSeq.current) return;
      setItems((current) => (append ? [...current, ...response.items] : response.items));
      setNextCursor(response.nextCursor);
    } catch (err) {
      if (requestId === requestSeq.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (requestId === requestSeq.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }

  function selectCurrent() {
    if (!selected || selected.kind !== kind) return;
    onSelect({
      source: "gallery",
      gallery_item_id: selected.id,
      kind,
      filename: selected.filename,
      extension: selected.extension,
      mime_type: selected.mimeType,
      size_bytes: selected.sizeBytes,
      width: selected.width,
      height: selected.height,
      duration_seconds: selected.durationSeconds,
      fps: selected.fps,
    });
    onClose();
  }

  return createPortal(
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="gallery-picker-title" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="gallery-picker-modal">
        <header className="gallery-picker-modal__header">
          <div>
            <p className="eyebrow">Gallery</p>
            <h2 id="gallery-picker-title">Choose {kindLabel(kind)}</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="gallery-picker-modal__toolbar">
          <label className="gallery-picker-search">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">Search Gallery</span>
            <input autoFocus value={query} placeholder="Search by filename, workflow, or prompt" onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>

        <div className="gallery-picker-modal__body">
          {error && items.length === 0 ? (
            <div className="gallery-picker-message gallery-picker-message--error" role="status">
              <AlertCircle size={20} aria-hidden="true" />
              <span>{error}</span>
              <button className="secondary-button secondary-button--small" type="button" onClick={() => void loadPage({ append: false, cursor: null })}>
                Try again
              </button>
            </div>
          ) : loading ? (
            <div className="gallery-picker-message" aria-busy="true">
              <Loader2 className="spin" size={18} aria-hidden="true" />
              <span>Loading Gallery items...</span>
            </div>
          ) : items.length === 0 ? (
            <div className="gallery-picker-message">
              <MediaKindIcon kind={kind} size={24} />
              <span>No compatible {kindLabel(kind).toLowerCase()} items found.</span>
            </div>
          ) : (
            <>
              {error ? <p className="gallery-picker-inline-error" role="status">{error}</p> : null}
              <div className="gallery-picker-grid" aria-label={`Compatible ${kindLabel(kind)} Gallery items`}>
                {items.map((item) => (
                  <button
                    key={item.id}
                    className={selectedId === item.id ? "gallery-picker-card gallery-picker-card--selected" : "gallery-picker-card"}
                    type="button"
                    aria-pressed={selectedId === item.id}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <GalleryPickerVisual item={item} />
                    <span className="gallery-picker-card__meta">
                      <strong>{item.filename}</strong>
                      <span>{mediaMetaLabel(item)}</span>
                    </span>
                    {selectedId === item.id ? <span className="gallery-picker-card__check"><Check size={14} aria-hidden="true" /></span> : null}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        <footer className="gallery-picker-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose}>Cancel</button>
          <button className="secondary-button" type="button" disabled={!nextCursor || loading || loadingMore} onClick={() => void loadPage({ append: true, cursor: nextCursor })}>
            {loadingMore ? <Loader2 className="spin" size={16} aria-hidden="true" /> : null}
            Load more
          </button>
          <button className="primary-button primary-button--compact" type="button" disabled={!selected} onClick={selectCurrent}>Select</button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

function GalleryPickerVisual({ item }: { item: GalleryItem }) {
  const preview = galleryPreviewUrl(item);
  if ((item.kind === "image" || (item.kind === "3d" && item.thumbnailUrl)) && preview) {
    return <img className="gallery-picker-card__img" src={preview} alt={item.prompt || item.filename} draggable={false} />;
  }
  return (
    <span className={`gallery-picker-card__placeholder gallery-picker-card__placeholder--${item.kind}`}>
      <MediaKindIcon kind={item.kind as PickerKind} size={30} />
      {item.durationSeconds ? <em>{formatMediaDuration(item.durationSeconds)}</em> : item.extension ? <em>{item.extension.replace(".", "").toUpperCase()}</em> : null}
    </span>
  );
}

function MediaKindIcon({ kind, size }: { kind: PickerKind; size: number }) {
  const Icon = kind === "image" ? ImageIcon : kind === "video" ? Video : kind === "audio" ? FileAudio : Box;
  return <Icon size={size} aria-hidden="true" />;
}

function kindLabel(kind: PickerKind) {
  return kind === "3d" ? "3D model" : `${kind.charAt(0).toUpperCase()}${kind.slice(1)}`;
}

function mediaMetaLabel(item: GalleryItem): string {
  if (item.kind === "audio") return audioMetadataLabel(item.extension?.replace(".", ""), item.mimeType, item.sizeBytes, item.durationSeconds, "Audio");
  if (item.kind === "video") return [item.extension?.replace(".", "").toUpperCase(), item.durationSeconds ? formatMediaDuration(item.durationSeconds) : null].filter(Boolean).join(" · ") || "Video";
  return fileMetadataLabel(item.extension, item.mimeType, item.sizeBytes, item.kind === "3d" ? "3D model" : "Image");
}
