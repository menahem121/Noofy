import { Check, Loader2, RotateCcw, Save, X } from "lucide-react";

import type { GallerySaveRequest } from "../../lib/api/noofyApi";

export function GallerySaveAction({
  status,
  onSave,
  onCancel,
}: {
  status?: GallerySaveRequest;
  onSave: () => void;
  onCancel: () => void;
}) {
  if (status?.status === "queued" || status?.status === "saving") {
    const percent = status.total_bytes && status.total_bytes > 0
      ? Math.min(100, Math.round((status.bytes_copied / status.total_bytes) * 100))
      : null;
    return <div className="gallery-save-active"><span className="gallery-save-state"><Loader2 className="spin" size={13} />Saving{percent === null ? "..." : ` ${percent}%`}</span><button className="secondary-button secondary-button--small" type="button" aria-label="Cancel Gallery save" onClick={onCancel}><X size={13} />Cancel</button></div>;
  }
  if (status?.status === "saved") {
    return <span className="gallery-save-state gallery-save-state--saved"><Check size={13} />Saved</span>;
  }
  if (status?.status === "unavailable") {
    return <span className="gallery-save-state gallery-save-state--unavailable">Output unavailable</span>;
  }
  const retry = status && ["failed", "canceled", "interrupted", "saved_with_errors"].includes(status.status);
  return <button className="secondary-button secondary-button--small" type="button" onClick={onSave}>{retry ? <RotateCcw size={13} /> : <Save size={13} />}{retry ? "Retry save" : "Save to Gallery"}</button>;
}
