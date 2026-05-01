import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Box,
  ChevronDown,
  ChevronRight,
  Cpu,
  Folder,
  Hash,
  Maximize2,
  MoreHorizontal,
  Package,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Tag,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";

import { fetchRuntimeStatus, type RuntimeStatus } from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import {
  INITIAL_TAGS,
  MOCK_MODELS,
  MODEL_SOURCE_LABELS,
  MODEL_STATUS_LABELS,
  MODEL_TYPE_LABELS,
  TAG_COLOR_PRESETS,
  type ModelEntry,
  type ModelSource,
  type ModelStatus,
  type ModelTag,
  type ModelType,
} from "./modelsContent";

type ModelTypeFilter = ModelType | "all";

interface ModelsPageProps {
  onNavigate: (route: AppRouteId) => void;
}

const MODEL_TYPE_FILTERS: Array<{ id: ModelTypeFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "checkpoint", label: "Checkpoints" },
  { id: "lora", label: "LoRAs" },
  { id: "controlnet", label: "ControlNet" },
  { id: "upscaler", label: "Upscalers" },
  { id: "vae", label: "VAE" },
  { id: "embedding", label: "Embeddings" },
  { id: "other", label: "Other" },
];

const TYPE_ICONS: Record<ModelType, typeof Box> = {
  checkpoint: Box,
  lora: Zap,
  controlnet: SlidersHorizontal,
  upscaler: Maximize2,
  vae: Cpu,
  embedding: Hash,
  other: Package,
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function hexAlpha(hex: string, opacity: number): string {
  const alpha = Math.round(opacity * 255)
    .toString(16)
    .padStart(2, "0");
  return hex + alpha;
}

export function ModelsPage({ onNavigate }: ModelsPageProps) {
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

  const [models, setModels] = useState<ModelEntry[]>(() => MOCK_MODELS.map((m) => ({ ...m })));
  const [tags, setTags] = useState<ModelTag[]>(INITIAL_TAGS);

  const [activeType, setActiveType] = useState<ModelTypeFilter>("all");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | ModelStatus>("all");
  const [sourceFilter, setSourceFilter] = useState<"all" | ModelSource>("all");
  const [tagFilter, setTagFilter] = useState<string>("all");

  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());

  const [showTagCreate, setShowTagCreate] = useState(false);
  const [newTagName, setNewTagName] = useState("");
  const [newTagColor, setNewTagColor] = useState(TAG_COLOR_PRESETS[0]);

  const [showDevDetails, setShowDevDetails] = useState(false);
  const [showAddTag, setShowAddTag] = useState(false);

  const filteredModels = useMemo(() => {
    return models.filter((m) => {
      if (activeType !== "all" && m.type !== activeType) return false;
      if (search && !m.name.toLowerCase().includes(search.toLowerCase())) return false;
      if (statusFilter !== "all" && m.status !== statusFilter) return false;
      if (sourceFilter !== "all" && m.source !== sourceFilter) return false;
      if (tagFilter !== "all" && !m.tagIds.includes(tagFilter)) return false;
      return true;
    });
  }, [models, activeType, search, statusFilter, sourceFilter, tagFilter]);

  const selectedModel = selectedModelId ? (models.find((m) => m.id === selectedModelId) ?? null) : null;

  const totalSizeBytes = useMemo(() => models.reduce((acc, m) => acc + m.sizeBytes, 0), [models]);
  const missingCount = useMemo(() => models.filter((m) => m.status === "missing").length, [models]);
  const linkedCount = useMemo(() => models.filter((m) => m.source === "linked").length, [models]);

  function handleSelectModel(id: string) {
    setSelectedModelId((prev) => (prev === id ? null : id));
    setShowDevDetails(false);
    setShowAddTag(false);
  }

  function handleToggleCheck(id: string, checked: boolean) {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function handleSelectAll() {
    if (checkedIds.size === filteredModels.length && filteredModels.length > 0) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(filteredModels.map((m) => m.id)));
    }
  }

  function handleModelTagToggle(modelId: string, tagId: string) {
    setModels((prev) =>
      prev.map((m) => {
        if (m.id !== modelId) return m;
        const hasTag = m.tagIds.includes(tagId);
        return { ...m, tagIds: hasTag ? m.tagIds.filter((t) => t !== tagId) : [...m.tagIds, tagId] };
      }),
    );
  }

  function handleCreateTag() {
    if (!newTagName.trim()) return;
    const id = `tag_${Date.now()}`;
    setTags((prev) => [...prev, { id, name: newTagName.trim(), color: newTagColor }]);
    setNewTagName("");
    setShowTagCreate(false);
  }

  function handleBulkAddTag(tagId: string) {
    setModels((prev) =>
      prev.map((m) =>
        checkedIds.has(m.id) && !m.tagIds.includes(tagId) ? { ...m, tagIds: [...m.tagIds, tagId] } : m,
      ),
    );
  }

  function handleBulkRemove() {
    setModels((prev) => prev.filter((m) => !checkedIds.has(m.id)));
    if (selectedModelId && checkedIds.has(selectedModelId)) setSelectedModelId(null);
    setCheckedIds(new Set());
  }

  const panelOpen = selectedModel !== null;

  return (
    <AppLayout activeRoute="models" status={appStatus} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="models-title">
        <div>
          <p className="eyebrow">Local model files</p>
          <h1 id="models-title">Models</h1>
          <p>Manage the AI models Noofy can use for your workflows.</p>
        </div>
        <div className="button-row">
          <button className="secondary-button" type="button">
            <RefreshCw size={15} aria-hidden="true" />
            Scan Folders
          </button>
          <button className="primary-button" type="button">
            <Plus size={16} aria-hidden="true" />
            Add Model
          </button>
        </div>
      </section>

      <div className="models-summary-bar" role="region" aria-label="Model statistics">
        <div className="models-stat-card">
          <div className="models-stat-card__value">{models.length}</div>
          <div className="models-stat-card__label">Total models</div>
        </div>
        <div className="models-stat-card">
          <div className="models-stat-card__value">{formatBytes(totalSizeBytes)}</div>
          <div className="models-stat-card__label">Installed size</div>
        </div>
        <div className={`models-stat-card${missingCount > 0 ? " models-stat-card--warning" : ""}`}>
          <div className="models-stat-card__value">{missingCount}</div>
          <div className="models-stat-card__label">Missing from workflows</div>
        </div>
        <div className="models-stat-card">
          <div className="models-stat-card__value">{linkedCount}</div>
          <div className="models-stat-card__label">Linked from your computer</div>
        </div>
      </div>

      {missingCount > 0 && (
        <div className="notice notice--warning notice--row" role="status">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <strong>
              {missingCount} model{missingCount !== 1 ? "s are" : " is"} needed by installed workflows.
            </strong>
            <span>Some workflows may not run until these models are available.</span>
          </div>
          <div className="button-row" style={{ marginLeft: "auto", flexShrink: 0 }}>
            <button className="secondary-button secondary-button--small" type="button">
              Download missing
            </button>
            <button className="secondary-button secondary-button--small" type="button">
              Choose files
            </button>
          </div>
        </div>
      )}

      <div className="models-type-tabs" role="tablist" aria-label="Filter by model type">
        {MODEL_TYPE_FILTERS.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={activeType === id}
            className={`models-type-tab${activeType === id ? " models-type-tab--active" : ""}`}
            type="button"
            onClick={() => {
              setActiveType(id);
              setSelectedModelId(null);
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="models-toolbar">
        <label className="search-field search-field--models">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search models</span>
          <input
            type="search"
            placeholder="Search models..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>

        <div className="filter-select-wrap">
          <select
            className="filter-select"
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as "all" | ModelStatus)}
          >
            <option value="all">All status</option>
            <option value="ready">Ready</option>
            <option value="missing">Missing</option>
            <option value="needs_attention">Needs attention</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>

        <div className="filter-select-wrap">
          <select
            className="filter-select"
            aria-label="Filter by source"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value as "all" | ModelSource)}
          >
            <option value="all">All sources</option>
            <option value="downloaded">Downloaded by Noofy</option>
            <option value="imported">Imported</option>
            <option value="linked">Linked from computer</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>

        <div className="filter-select-wrap">
          <select
            className="filter-select"
            aria-label="Filter by tag"
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
          >
            <option value="all">All tags</option>
            {tags.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>

        <div className="models-toolbar__spacer" />

        <button
          className="ghost-button"
          type="button"
          onClick={() => setShowTagCreate((v) => !v)}
          aria-expanded={showTagCreate}
        >
          <Tag size={15} aria-hidden="true" />
          {showTagCreate ? "Cancel" : "Create Tag"}
        </button>
      </div>

      {showTagCreate && (
        <div className="tag-create-form" role="region" aria-label="Create a new tag">
          <input
            type="text"
            className="tag-create-form__input"
            placeholder="Tag name..."
            value={newTagName}
            onChange={(e) => setNewTagName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateTag();
            }}
            autoFocus
            maxLength={24}
          />
          <div className="tag-color-presets" role="group" aria-label="Pick a color">
            {TAG_COLOR_PRESETS.map((color) => (
              <button
                key={color}
                type="button"
                className={`tag-color-preset${newTagColor === color ? " tag-color-preset--active" : ""}`}
                style={{ background: color }}
                aria-label={`Color ${color}`}
                aria-pressed={newTagColor === color}
                onClick={() => setNewTagColor(color)}
              />
            ))}
          </div>
          <span
            className="tag-pill"
            style={{
              backgroundColor: hexAlpha(newTagColor, 0.12),
              borderColor: hexAlpha(newTagColor, 0.3),
              color: newTagColor,
            }}
          >
            {newTagName || "Preview"}
          </span>
          <button
            className="primary-button primary-button--compact"
            type="button"
            onClick={handleCreateTag}
            disabled={!newTagName.trim()}
          >
            Create
          </button>
        </div>
      )}

      {checkedIds.size > 0 && (
        <div className="models-bulk-bar" role="region" aria-label="Bulk actions">
          <span className="models-bulk-bar__count">{checkedIds.size} selected</span>
          <div className="button-row">
            {tags.map((tag) => (
              <button
                key={tag.id}
                className="secondary-button secondary-button--small"
                type="button"
                onClick={() => handleBulkAddTag(tag.id)}
              >
                <Tag size={12} aria-hidden="true" />
                Add "{tag.name}"
              </button>
            ))}
          </div>
          <button className="secondary-button secondary-button--small" type="button" onClick={handleBulkRemove}>
            <Trash2 size={13} aria-hidden="true" />
            Remove from Noofy
          </button>
          <button className="ghost-button" type="button" onClick={() => setCheckedIds(new Set())}>
            <X size={14} aria-hidden="true" />
            Clear
          </button>
        </div>
      )}

      <div className={`models-layout${panelOpen ? " models-layout--panel-open" : ""}`}>
        <div className="models-list-area">
          {filteredModels.length > 0 && (
            <div className="models-table-head" aria-hidden="true">
              <div className="model-col model-col-check">
                <input
                  type="checkbox"
                  checked={checkedIds.size === filteredModels.length && filteredModels.length > 0}
                  onChange={handleSelectAll}
                  aria-label="Select all models"
                />
              </div>
              <div className="model-col model-col-main">Name</div>
              <div className="model-col model-col-tags">Tags</div>
              <div className="model-col model-col-size">Size</div>
              <div className="model-col model-col-status">Status</div>
              <div className="model-col model-col-source">Source</div>
              <div className="model-col model-col-actions" />
            </div>
          )}

          {filteredModels.length === 0 ? (
            <div className="models-empty">
              {models.length === 0 ? (
                <>
                  <Package size={44} aria-hidden="true" />
                  <h3>No models yet</h3>
                  <p>Add a model or open a workflow that needs one.</p>
                  <div className="models-empty__actions">
                    <button className="primary-button" type="button">
                      <Plus size={16} aria-hidden="true" />
                      Add Model
                    </button>
                    <button className="secondary-button" type="button" onClick={() => onNavigate("home")}>
                      Open Workflow
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <Search size={36} aria-hidden="true" />
                  <h3>No models match your filters</h3>
                  <p>Try adjusting the search or filters above.</p>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => {
                      setSearch("");
                      setStatusFilter("all");
                      setSourceFilter("all");
                      setTagFilter("all");
                      setActiveType("all");
                    }}
                  >
                    Clear all filters
                  </button>
                </>
              )}
            </div>
          ) : (
            <div className="models-table" role="list">
              {filteredModels.map((model) => (
                <ModelRow
                  key={model.id}
                  model={model}
                  tags={tags}
                  checked={checkedIds.has(model.id)}
                  selected={selectedModelId === model.id}
                  onCheck={(checked) => handleToggleCheck(model.id, checked)}
                  onSelect={() => handleSelectModel(model.id)}
                />
              ))}
            </div>
          )}
        </div>

        {selectedModel && (
          <aside className="models-detail-panel" aria-label={`Details for ${selectedModel.name}`}>
            <DetailPanel
              model={selectedModel}
              tags={tags}
              showDevDetails={showDevDetails}
              showAddTag={showAddTag}
              onClose={() => setSelectedModelId(null)}
              onToggleDevDetails={() => setShowDevDetails((v) => !v)}
              onToggleAddTag={() => setShowAddTag((v) => !v)}
              onTagToggle={(tagId) => handleModelTagToggle(selectedModel.id, tagId)}
            />
          </aside>
        )}
      </div>
    </AppLayout>
  );
}

function ModelRow({
  model,
  tags,
  checked,
  selected,
  onCheck,
  onSelect,
}: {
  model: ModelEntry;
  tags: ModelTag[];
  checked: boolean;
  selected: boolean;
  onCheck: (checked: boolean) => void;
  onSelect: () => void;
}) {
  const TypeIcon = TYPE_ICONS[model.type];
  const modelTags = tags.filter((t) => model.tagIds.includes(t.id));

  return (
    <article
      className={`model-row${selected ? " model-row--selected" : ""}${checked ? " model-row--checked" : ""}`}
      role="listitem"
      onClick={onSelect}
    >
      <div
        className="model-col model-col-check"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onCheck(e.target.checked)}
          aria-label={`Select ${model.name}`}
        />
      </div>

      <div className="model-col model-col-main">
        <div className="model-type-icon" aria-hidden="true">
          <TypeIcon size={16} />
        </div>
        <div className="model-main-body">
          <div className="model-name-text">{model.name}</div>
          <div className="model-type-text">{MODEL_TYPE_LABELS[model.type]}</div>
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
          {modelTags.length > 2 && (
            <span className="tag-pill tag-pill--more">+{modelTags.length - 2}</span>
          )}
        </div>
      </div>

      <div className="model-col model-col-size">{formatBytes(model.sizeBytes)}</div>

      <div className="model-col model-col-status">
        <span className={`model-status model-status--${model.status}`}>
          <span className="model-status__dot" aria-hidden="true" />
          {MODEL_STATUS_LABELS[model.status]}
        </span>
      </div>

      <div className="model-col model-col-source">{MODEL_SOURCE_LABELS[model.source]}</div>

      <div
        className="model-col model-col-actions"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="icon-button"
          type="button"
          aria-label={`Open details for ${model.name}`}
          title="View details"
          onClick={onSelect}
        >
          <MoreHorizontal size={16} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}

function DetailPanel({
  model,
  tags,
  showDevDetails,
  showAddTag,
  onClose,
  onToggleDevDetails,
  onToggleAddTag,
  onTagToggle,
}: {
  model: ModelEntry;
  tags: ModelTag[];
  showDevDetails: boolean;
  showAddTag: boolean;
  onClose: () => void;
  onToggleDevDetails: () => void;
  onToggleAddTag: () => void;
  onTagToggle: (tagId: string) => void;
}) {
  const TypeIcon = TYPE_ICONS[model.type];
  const modelTags = tags.filter((t) => model.tagIds.includes(t.id));
  const availableTags = tags.filter((t) => !model.tagIds.includes(t.id));

  return (
    <>
      <div className="detail-panel__header">
        <div className="detail-panel__title-group">
          <div className="model-type-icon model-type-icon--lg" aria-hidden="true">
            <TypeIcon size={20} />
          </div>
          <div className="detail-panel__title-text">
            <h2 className="detail-panel__title">{model.name}</h2>
            <span className="detail-panel__type">{MODEL_TYPE_LABELS[model.type]}</span>
          </div>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close details panel">
          <X size={17} aria-hidden="true" />
        </button>
      </div>

      <div className="detail-panel__section">
        <span className={`model-status model-status--${model.status}`}>
          <span className="model-status__dot" aria-hidden="true" />
          {MODEL_STATUS_LABELS[model.status]}
        </span>
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
          <button
            className="tag-pill tag-pill--add"
            type="button"
            onClick={onToggleAddTag}
            aria-expanded={showAddTag}
          >
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
                  <span
                    className="detail-panel__tag-dot"
                    style={{ background: tag.color }}
                    aria-hidden="true"
                  />
                  {tag.name}
                </button>
              ))
            ) : (
              <p className="detail-panel__tag-empty">No more tags available. Create one above.</p>
            )}
          </div>
        )}
      </div>

      <div className="detail-panel__section">
        <dl className="detail-list detail-list--compact">
          <div>
            <dt>Size</dt>
            <dd>{formatBytes(model.sizeBytes)}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{MODEL_SOURCE_LABELS[model.source]}</dd>
          </div>
          <div>
            <dt>Last used</dt>
            <dd>{model.lastUsed ?? "Never"}</dd>
          </div>
          {model.usedByWorkflows.length > 0 && (
            <div>
              <dt>Used by</dt>
              <dd>{model.usedByWorkflows.join(", ")}</dd>
            </div>
          )}
        </dl>
      </div>

      <div className="detail-panel__section">
        <div className="detail-panel__section-label">Actions</div>
        <div className="detail-panel__actions">
          <button className="secondary-button secondary-button--full" type="button">
            <Upload size={14} aria-hidden="true" />
            Replace file
          </button>
          <button className="secondary-button secondary-button--full" type="button">
            <Folder size={14} aria-hidden="true" />
            Reveal location
          </button>
          <button className="secondary-button secondary-button--full secondary-button--danger" type="button">
            <Trash2 size={14} aria-hidden="true" />
            Remove from Noofy
          </button>
        </div>
      </div>

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
            <div>
              <dt>File path</dt>
              <dd className="detail-dev-value">{model.filePath}</dd>
            </div>
            {model.hash && (
              <div>
                <dt>Hash</dt>
                <dd className="detail-dev-value">{model.hash.slice(0, 16)}…</dd>
              </div>
            )}
            <div>
              <dt>ComfyUI folder</dt>
              <dd>{model.comfyFolder}</dd>
            </div>
            <div>
              <dt>Verification</dt>
              <dd>{model.hash ? "Hash stored" : "Not verified"}</dd>
            </div>
          </dl>
        )}
      </div>
    </>
  );
}
