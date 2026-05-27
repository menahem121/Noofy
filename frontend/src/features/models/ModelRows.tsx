import { ChevronDown, ChevronRight, Download, Folder, MoreHorizontal, Plus, Trash2, X } from "lucide-react";

import type { ModelInventoryEntry, ModelTag } from "../../lib/api/noofyApi";
import {
  categoryLabel,
  folderNameLabel,
  formatBytes,
  hexAlpha,
  modelFolderPath,
  modelSourceLabel,
  MODEL_TYPE_LABELS,
  normalizeType,
  STATUS_LABELS,
  TYPE_ICONS,
} from "./modelUi";

export function ModelRow({
  model,
  tags,
  checked,
  selected,
  downloading,
  onCheck,
  onSelect,
  onDownload,
}: {
  model: ModelInventoryEntry;
  tags: ModelTag[];
  checked: boolean;
  selected: boolean;
  downloading: boolean;
  onCheck: (checked: boolean) => void;
  onSelect: () => void;
  onDownload: () => void;
}) {
  const type = normalizeType(model);
  const TypeIcon = TYPE_ICONS[type];
  const modelTags = tags.filter((tag) => model.tag_ids.includes(tag.id));
  const sourceLabel = modelSourceLabel(model);

  return (
    <article
      className={`model-row${selected ? " model-row--selected" : ""}${checked ? " model-row--checked" : ""}`}
      role="listitem"
      onClick={onSelect}
    >
      <div className="model-col model-col-check" onClick={(event) => event.stopPropagation()}>
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onCheck(event.target.checked)}
          aria-label={`Select ${model.filename}`}
        />
      </div>

      <div className="model-col model-col-main">
        <div className="model-type-icon" aria-hidden="true">
          <TypeIcon size={16} />
        </div>
        <div className="model-main-body">
          <div className="model-name-text">{model.filename}</div>
          <div className="model-type-text">
            {MODEL_TYPE_LABELS[type]} · {folderNameLabel(model.folder)}
          </div>
        </div>
      </div>

      <div className="model-col model-col-tags">
        <div className="model-tags-row">
          {modelTags.slice(0, 2).map((tag) => (
            <span
              key={tag.id}
              className="tag-pill"
              style={{
                backgroundColor: hexAlpha(tag.color, 0.12),
                borderColor: hexAlpha(tag.color, 0.3),
                color: tag.color,
              }}
            >
              {tag.name}
            </span>
          ))}
          {modelTags.length > 2 && <span className="tag-pill tag-pill--more">+{modelTags.length - 2}</span>}
        </div>
      </div>

      <div className="model-col model-col-size">{formatBytes(model.size_bytes)}</div>

      <div className="model-col model-col-status">
        <span className={`model-status model-status--${model.status}`}>
          <span className="model-status__dot" aria-hidden="true" />
          {model.status_label || STATUS_LABELS[model.status]}
        </span>
      </div>

      <div className="model-col model-col-source">
        <span>{sourceLabel}</span>
        <small>{model.ownership_label}</small>
      </div>

      <div className="model-col model-col-actions" onClick={(event) => event.stopPropagation()}>
        {model.downloadable_references.length > 0 ? (
          <button
            className="icon-button"
            type="button"
            aria-label={`Download ${model.filename}`}
            title="Download model"
            onClick={onDownload}
            disabled={downloading}
          >
            <Download size={16} aria-hidden="true" />
          </button>
        ) : (
          <button
            className="icon-button"
            type="button"
            aria-label={`Open details for ${model.filename}`}
            title="View details"
            onClick={onSelect}
          >
            <MoreHorizontal size={16} aria-hidden="true" />
          </button>
        )}
      </div>
    </article>
  );
}

export function DetailPanel({
  model,
  tags,
  showDevDetails,
  showAddTag,
  downloadBusy,
  deleteBusy,
  onClose,
  onToggleDevDetails,
  onToggleAddTag,
  onTagToggle,
  onReveal,
  onDownload,
  onDelete,
}: {
  model: ModelInventoryEntry;
  tags: ModelTag[];
  showDevDetails: boolean;
  showAddTag: boolean;
  downloadBusy: boolean;
  deleteBusy: boolean;
  onClose: () => void;
  onToggleDevDetails: () => void;
  onToggleAddTag: () => void;
  onTagToggle: (tagId: string) => void;
  onReveal: () => void;
  onDownload: () => void;
  onDelete: () => void;
}) {
  const type = normalizeType(model);
  const TypeIcon = TYPE_ICONS[type];
  const modelTags = tags.filter((tag) => model.tag_ids.includes(tag.id));
  const availableTags = tags.filter((tag) => !model.tag_ids.includes(tag.id));
  const folderPath = modelFolderPath(model);
  const sourceLabel = modelSourceLabel(model);
  const deleteLabel = model.source === "external_comfyui" ? "Delete from ComfyUI folder" : "Delete from Noofy Models";

  return (
    <>
      <div className="detail-panel__header">
        <div className="detail-panel__title-group">
          <div className="model-type-icon model-type-icon--lg" aria-hidden="true">
            <TypeIcon size={20} />
          </div>
          <div className="detail-panel__title-text">
            <h2 className="detail-panel__title">{model.filename}</h2>
            <span className="detail-panel__type">{MODEL_TYPE_LABELS[type]}</span>
          </div>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close details panel">
          <X size={17} aria-hidden="true" />
        </button>
      </div>

      <div className="detail-panel__section">
        <span className={`model-status model-status--${model.status}`}>
          <span className="model-status__dot" aria-hidden="true" />
          {model.status_label || STATUS_LABELS[model.status]}
        </span>
        {model.message && <p className="detail-panel__tag-empty">{model.message}</p>}
      </div>

      <div className="detail-panel__section">
        <div className="detail-panel__section-label">Tags</div>
        <div className="detail-panel__tags">
          {modelTags.map((tag) => (
            <span
              key={tag.id}
              className="tag-pill"
              style={{
                backgroundColor: hexAlpha(tag.color, 0.12),
                borderColor: hexAlpha(tag.color, 0.3),
                color: tag.color,
              }}
            >
              {tag.name}
              <button
                type="button"
                className="tag-pill__remove"
                onClick={() => onTagToggle(tag.id)}
                aria-label={`Remove tag ${tag.name}`}
              >
                <X size={10} aria-hidden="true" />
              </button>
            </span>
          ))}
          <button className="tag-pill tag-pill--add" type="button" onClick={onToggleAddTag} aria-expanded={showAddTag}>
            <Plus size={11} aria-hidden="true" />
            Add tag
          </button>
        </div>
        {showAddTag && (
          <div className="detail-panel__tag-dropdown">
            {availableTags.length > 0 ? (
              availableTags.map((tag) => (
                <button
                  key={tag.id}
                  type="button"
                  className="detail-panel__tag-option"
                  onClick={() => {
                    onTagToggle(tag.id);
                    onToggleAddTag();
                  }}
                >
                  <span className="detail-panel__tag-dot" style={{ background: tag.color }} aria-hidden="true" />
                  {tag.name}
                </button>
              ))
            ) : (
              <p className="detail-panel__tag-empty">No tags available. Create one above.</p>
            )}
          </div>
        )}
      </div>

      <div className="detail-panel__section">
        <dl className="detail-list detail-list--compact">
          <div>
            <dt>Size</dt>
            <dd>{formatBytes(model.size_bytes)}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{sourceLabel}</dd>
          </div>
          <div>
            <dt>Ownership</dt>
            <dd>{model.ownership_label}</dd>
          </div>
          <div>
            <dt>Folder</dt>
            <dd>{categoryLabel(model.folder)}</dd>
          </div>
          {model.workflow_usage.length > 0 && (
            <div>
              <dt>Used by</dt>
              <dd>{model.workflow_usage.map((workflow) => workflow.workflow_name).join(", ")}</dd>
            </div>
          )}
        </dl>
      </div>

      <div className="detail-panel__section">
        <div className="detail-panel__section-label">Actions</div>
        <div className="detail-panel__actions">
          {model.downloadable_references.length > 0 && (
            <button className="secondary-button secondary-button--full" type="button" onClick={onDownload} disabled={downloadBusy}>
              <Download size={14} aria-hidden="true" />
              Download model
            </button>
          )}
          <button className="secondary-button secondary-button--full" type="button" onClick={onReveal} disabled={!folderPath}>
            <Folder size={14} aria-hidden="true" />
            Reveal location
          </button>
          {model.can_delete && (
            <button className="secondary-button secondary-button--full secondary-button--danger" type="button" onClick={onDelete} disabled={deleteBusy}>
              <Trash2 size={14} aria-hidden="true" />
              {deleteLabel}
            </button>
          )}
        </div>
        {!model.can_delete && model.delete_unavailable_reason && (
          <p className="detail-panel__tag-empty">{model.delete_unavailable_reason}</p>
        )}
      </div>

      <div className="detail-developer">
        <button className="detail-developer__toggle" type="button" onClick={onToggleDevDetails} aria-expanded={showDevDetails}>
          {showDevDetails ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          Developer details
        </button>
        {showDevDetails && (
          <dl className="detail-list detail-list--compact detail-developer__content">
            {model.path && (
              <div>
                <dt>File path</dt>
                <dd className="detail-dev-value">{model.path}</dd>
              </div>
            )}
            <div>
              <dt>Model key</dt>
              <dd className="detail-dev-value">{model.model_key}</dd>
            </div>
            {model.matched_sha256 && (
              <div>
                <dt>SHA-256</dt>
                <dd className="detail-dev-value">{model.matched_sha256.slice(0, 16)}...</dd>
              </div>
            )}
            <div>
              <dt>Verification</dt>
              <dd>{model.verification_level ?? "Not verified"}</dd>
            </div>
          </dl>
        )}
      </div>
    </>
  );
}
