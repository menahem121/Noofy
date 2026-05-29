import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  CheckCircle2,
  Download,
  Folder,
  Loader2,
  Package,
  Plus,
  RefreshCw,
  Search,
  Tag,
  Trash2,
  X,
} from "lucide-react";

import {
  createModelTag,
  fetchRuntimeStatus,
  importModelFiles,
  deleteModelFile,
  updateModelTags,
  type ModelDownloadSelection,
  type ModelImportResponse,
  type ModelInventoryEntry,
  type ModelInventorySource,
  type ModelInventoryStatus,
  type ModelTag,
  type RuntimeStatus,
} from "../../lib/api/noofyApi";
import { openFolder } from "../../lib/folderDialogs";
import { failedModelMessage, isModelDownloadActive, isModelDownloadFailure } from "../../lib/modelDownloadProgress";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import { ModelImportPanel } from "./ModelImportPanel";
import { DetailPanel, ModelRow } from "./ModelRows";
import { uniqueDownloadSelections } from "./modelDownloads";
import { TAG_COLOR_PRESETS } from "./modelsContent";
import {
  formatBytes,
  hexAlpha,
  MODEL_TYPE_FILTERS,
  modelFolderPath,
  normalizeType,
  SOURCE_FILTERS,
  type ModelTypeFilter,
} from "./modelUi";
import { useModelDownloadJob } from "./useModelDownloadJob";
import { useModelInventory } from "./useModelInventory";

interface ModelsPageProps {
  onNavigate: (route: AppRouteId) => void;
}


export function ModelsPage({ onNavigate }: ModelsPageProps) {
  const [runtimeState, setRuntimeState] = useState<{ loading: boolean; runtime: RuntimeStatus | null }>({
    loading: true,
    runtime: null,
  });
  const { inventoryState, refreshInventory: loadInventory } = useModelInventory();

  const [activeType, setActiveType] = useState<ModelTypeFilter>("all");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | ModelInventoryStatus>("all");
  const [sourceFilter, setSourceFilter] = useState<"all" | ModelInventorySource>("all");
  const [tagFilter, setTagFilter] = useState<string>("all");
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());

  const [showTagCreate, setShowTagCreate] = useState(false);
  const [newTagName, setNewTagName] = useState("");
  const [newTagColor, setNewTagColor] = useState(TAG_COLOR_PRESETS[0]);
  const [tagAction, setTagAction] = useState<string | null>(null);

  const [showDevDetails, setShowDevDetails] = useState(false);
  const [showAddTag, setShowAddTag] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importFolder, setImportFolder] = useState("checkpoints");
  const [importPaths, setImportPaths] = useState<string[]>([]);
  const [importOverwrite, setImportOverwrite] = useState(false);
  const [importResult, setImportResult] = useState<ModelImportResponse | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [pageMessage, setPageMessage] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const refreshInventory = useCallback(async (options: { silent?: boolean } = {}) => {
    const inventory = await loadInventory(options);
    if (inventory) {
      if (!inventory.models.some((model) => model.model_key === selectedModelId)) {
        setSelectedModelId(null);
      }
      if (!inventory.folders.categories.includes(importFolder)) {
        setImportFolder(inventory.folders.categories[0] ?? "checkpoints");
      }
    }
    return inventory;
  }, [importFolder, loadInventory, selectedModelId]);

  const { downloadJob, downloadError, downloadBusy, startDownload, cancelDownload } = useModelDownloadJob(() => {
    void refreshInventory({ silent: true });
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
    void refreshInventory();
    return () => {
      mounted = false;
    };
    // Initial page load only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const appStatus = runtimeStatusCopy(runtimeState);
  const inventory = inventoryState.inventory;
  const models = inventory?.models ?? [];
  const tags = inventory?.tags ?? [];

  const filteredModels = useMemo(() => {
    return models.filter((model) => {
      if (activeType !== "all" && normalizeType(model) !== activeType) return false;
      if (search) {
        const haystack = `${model.filename} ${model.folder} ${model.source_label} ${model.workflow_usage
          .map((workflow) => workflow.workflow_name)
          .join(" ")}`.toLowerCase();
        if (!haystack.includes(search.toLowerCase())) return false;
      }
      if (statusFilter !== "all" && model.status !== statusFilter) return false;
      if (sourceFilter !== "all" && model.source !== sourceFilter) return false;
      if (tagFilter !== "all" && !model.tag_ids.includes(tagFilter)) return false;
      return true;
    });
  }, [models, activeType, search, statusFilter, sourceFilter, tagFilter]);

  const selectedModel = selectedModelId ? (models.find((model) => model.model_key === selectedModelId) ?? null) : null;
  const selectedModels = useMemo(
    () => models.filter((model) => checkedIds.has(model.model_key)),
    [checkedIds, models],
  );
  const selectedDeletableModels = useMemo(
    () => selectedModels.filter((model) => model.can_delete),
    [selectedModels],
  );
  const selectedBlockedCount = selectedModels.length - selectedDeletableModels.length;
  const allFilteredModelsSelected =
    filteredModels.length > 0 && filteredModels.every((model) => checkedIds.has(model.model_key));
  const missingDownloadRefs = useMemo(
    () => uniqueDownloadSelections(models.flatMap((model) => model.downloadable_references)),
    [models],
  );

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
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (allFilteredModelsSelected) {
        filteredModels.forEach((model) => next.delete(model.model_key));
      } else {
        filteredModels.forEach((model) => next.add(model.model_key));
      }
      return next;
    });
  }

  async function handleCreateTag() {
    if (!newTagName.trim()) return;
    setTagAction("Creating tag...");
    setPageError(null);
    try {
      await createModelTag({ name: newTagName.trim(), color: newTagColor });
      setNewTagName("");
      setShowTagCreate(false);
      await refreshInventory({ silent: true });
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Could not create the tag.");
    } finally {
      setTagAction(null);
    }
  }

  async function handleModelTagToggle(model: ModelInventoryEntry, tagId: string) {
    const hasTag = model.tag_ids.includes(tagId);
    const next = hasTag ? model.tag_ids.filter((id) => id !== tagId) : [...model.tag_ids, tagId];
    setTagAction("Saving tags...");
    setPageError(null);
    try {
      await updateModelTags(model.model_key, next);
      await refreshInventory({ silent: true });
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Could not save tags.");
    } finally {
      setTagAction(null);
    }
  }

  async function handleBulkAddTag(tagId: string) {
    const selected = models.filter((model) => checkedIds.has(model.model_key));
    setTagAction("Saving tags...");
    setPageError(null);
    try {
      await Promise.all(
        selected.map((model) =>
          updateModelTags(model.model_key, model.tag_ids.includes(tagId) ? model.tag_ids : [...model.tag_ids, tagId]),
        ),
      );
      await refreshInventory({ silent: true });
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Could not save tags.");
    } finally {
      setTagAction(null);
    }
  }

  async function handleImportModels() {
    if (importPaths.length === 0) return;
    setImportBusy(true);
    setImportResult(null);
    setPageError(null);
    try {
      const result = await importModelFiles({ source_paths: importPaths, folder: importFolder, overwrite: importOverwrite });
      setImportResult(result);
      await refreshInventory({ silent: true });
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Could not import those model files.");
    } finally {
      setImportBusy(false);
    }
  }

  async function handleDownload(selections: ModelDownloadSelection[]) {
    await startDownload(selections);
  }

  async function handleDeleteModel(model: ModelInventoryEntry) {
    if (!model.can_delete) return;
    const locationLabel = model.source === "external_comfyui" ? "the ComfyUI models folder" : "Noofy Models";
    const confirmed = window.confirm(
      `Delete ${model.filename} from ${locationLabel}? This removes the file from computer storage and cannot be undone.`,
    );
    if (!confirmed) return;
    setDeleteBusy(true);
    setPageError(null);
    try {
      const result = await deleteModelFile(model.model_key);
      setPageMessage(result.message);
      setSelectedModelId(null);
      await refreshInventory({ silent: true });
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Could not delete this model.");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function handleDeleteSelectedModels() {
    if (selectedDeletableModels.length === 0) {
      setPageError("None of the selected models can be deleted from a configured model folder.");
      return;
    }

    const deleteLabel =
      selectedDeletableModels.length === 1
        ? selectedDeletableModels[0].filename
        : `${selectedDeletableModels.length} model files`;
    const selectedSources = new Set(selectedDeletableModels.map((model) => model.source));
    const locationLabel =
      selectedSources.size === 1 && selectedSources.has("external_comfyui")
        ? "the ComfyUI models folder"
        : selectedSources.size === 1
          ? "Noofy Models"
          : "the selected model folders";
    const skippedLabel =
      selectedBlockedCount > 0
        ? [
            `${selectedBlockedCount} selected item${selectedBlockedCount === 1 ? "" : "s"} will be skipped`,
            `because ${selectedBlockedCount === 1 ? "it is" : "they are"} not deletable by Noofy.`,
          ].join(" ")
        : "";
    const confirmed = window.confirm(
      [
        `Delete ${deleteLabel} from ${locationLabel}?`,
        `This removes the file${selectedDeletableModels.length === 1 ? "" : "s"} from computer storage and cannot be undone.`,
        skippedLabel,
      ]
        .filter(Boolean)
        .join(" "),
    );
    if (!confirmed) return;

    setDeleteBusy(true);
    setPageError(null);
    setPageMessage(null);
    const failedKeys = new Set<string>();
    const deletedKeys = new Set<string>();
    try {
      for (const model of selectedDeletableModels) {
        try {
          await deleteModelFile(model.model_key);
          deletedKeys.add(model.model_key);
        } catch {
          failedKeys.add(model.model_key);
        }
      }

      if (deletedKeys.has(selectedModelId ?? "")) {
        setSelectedModelId(null);
      }
      setCheckedIds(new Set(failedKeys));
      await refreshInventory({ silent: true });

      if (failedKeys.size > 0) {
        setPageError(
          [
            `Deleted ${deletedKeys.size} model${deletedKeys.size === 1 ? "" : "s"}.`,
            `${failedKeys.size} selected model${failedKeys.size === 1 ? "" : "s"} could not be deleted.`,
          ].join(" "),
        );
      } else {
        setPageMessage(`Deleted ${deletedKeys.size} model${deletedKeys.size === 1 ? "" : "s"} from ${locationLabel}.`);
      }
    } finally {
      setDeleteBusy(false);
    }
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
          <button className="secondary-button" type="button" onClick={() => void refreshInventory()}>
            {inventoryState.loading ? <Loader2 className="spin" size={15} aria-hidden="true" /> : <RefreshCw size={15} aria-hidden="true" />}
            Scan Folders
          </button>
          {inventory?.folders.noofy_models_dir && (
            <button className="secondary-button" type="button" onClick={() => void openFolder(inventory.folders.noofy_models_dir)}>
              <Folder size={15} aria-hidden="true" />
              Open Noofy Models
            </button>
          )}
          <button className="primary-button" type="button" onClick={() => setShowImport((value) => !value)}>
            <Plus size={16} aria-hidden="true" />
            Add Model
          </button>
        </div>
      </section>

      {inventoryState.error && (
        <div className="notice notice--warning notice--row" role="status">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <strong>Models could not be loaded.</strong>
            <span>{inventoryState.error}</span>
          </div>
        </div>
      )}

      {(pageError || downloadError) && (
        <div className="notice notice--warning notice--row" role="status">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <strong>That did not work yet.</strong>
            <span>{pageError ?? downloadError}</span>
          </div>
        </div>
      )}

      {pageMessage && (
        <div className="notice notice--row" role="status">
          <CheckCircle2 size={18} aria-hidden="true" />
          <div>
            <strong>{pageMessage}</strong>
            <span>The model list has been refreshed.</span>
          </div>
        </div>
      )}

      <div className="models-summary-bar" role="region" aria-label="Model statistics">
        <div className="models-stat-card">
          <div className="models-stat-card__value">{inventory?.summary.total_count ?? "..."}</div>
          <div className="models-stat-card__label">Total models</div>
        </div>
        <div className="models-stat-card">
          <div className="models-stat-card__value">{formatBytes(inventory?.summary.total_known_size_bytes)}</div>
          <div className="models-stat-card__label">Known size</div>
        </div>
        <div className={`models-stat-card${(inventory?.summary.missing_count ?? 0) > 0 ? " models-stat-card--warning" : ""}`}>
          <div className="models-stat-card__value">{inventory?.summary.missing_count ?? "..."}</div>
          <div className="models-stat-card__label">Missing from workflows</div>
        </div>
        <div className="models-stat-card">
          <div className="models-stat-card__value">{inventory?.summary.external_comfyui_count ?? "..."}</div>
          <div className="models-stat-card__label">From ComfyUI folder</div>
        </div>
      </div>

      {showImport && inventory && (
        <ModelImportPanel
          categories={inventory.folders.categories}
          folder={importFolder}
          paths={importPaths}
          overwrite={importOverwrite}
          busy={importBusy}
          result={importResult}
          onFolderChange={setImportFolder}
          onPathsChange={setImportPaths}
          onOverwriteChange={setImportOverwrite}
          onImport={() => void handleImportModels()}
        />
      )}

      {importResult?.failed_count ? (
        <div className="notice notice--warning notice--row" role="status">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <strong>Some files were not imported.</strong>
            <span>{importResult.models.filter((item) => item.status === "failed").map((item) => `${item.filename ?? item.source_path}: ${item.message}`).join(" ")}</span>
          </div>
        </div>
      ) : null}

      {(inventory?.summary.missing_count ?? 0) > 0 && (
        <div className="notice notice--warning notice--row" role="status">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <strong>
              {inventory?.summary.missing_count} model{inventory?.summary.missing_count !== 1 ? "s are" : " is"} needed by installed workflows.
            </strong>
            <span>Some workflows may not run until these models are available.</span>
          </div>
          <div className="button-row" style={{ marginLeft: "auto", flexShrink: 0 }}>
            <button
              className="secondary-button secondary-button--small"
              type="button"
              onClick={() => void handleDownload(missingDownloadRefs)}
              disabled={missingDownloadRefs.length === 0 || downloadBusy}
            >
              <Download size={13} aria-hidden="true" />
              Download missing
            </button>
          </div>
        </div>
      )}

      {downloadJob && (
        <div className={`notice notice--row ${isModelDownloadFailure(downloadJob.status) ? "notice--error" : ""}`} role="status">
          {isModelDownloadActive(downloadJob.status) ? (
            <Loader2 className="spin" size={18} aria-hidden="true" />
          ) : isModelDownloadFailure(downloadJob.status) ? (
            <AlertTriangle size={18} aria-hidden="true" />
          ) : (
            <Download size={18} aria-hidden="true" />
          )}
          <div>
            <strong>{isModelDownloadFailure(downloadJob.status) ? "Some downloads failed" : downloadJob.user_facing_message}</strong>
            <span>
              {downloadJob.current_model_filename ?? "Model download"}{" "}
              {!isModelDownloadFailure(downloadJob.status) && downloadJob.percent !== null ? `${downloadJob.percent}%` : ""}
            </span>
            {failedModelMessage(downloadJob) ? <span>{failedModelMessage(downloadJob)}</span> : null}
          </div>
          {isModelDownloadActive(downloadJob.status) ? (
            <button className="secondary-button secondary-button--small" type="button" onClick={() => void cancelDownload()}>
              Cancel
            </button>
          ) : isModelDownloadFailure(downloadJob.status) ? (
            <button className="secondary-button secondary-button--small" type="button" onClick={() => void handleDownload(missingDownloadRefs)}>
              Retry
            </button>
          ) : null}
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
            onChange={(e) => setStatusFilter(e.target.value as "all" | ModelInventoryStatus)}
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
            onChange={(e) => setSourceFilter(e.target.value as "all" | ModelInventorySource)}
          >
            {SOURCE_FILTERS.map((source) => (
              <option key={source.id} value={source.id}>
                {source.label}
              </option>
            ))}
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
            {tags.map((tag) => (
              <option key={tag.id} value={tag.id}>
                {tag.name}
              </option>
            ))}
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </div>

        <div className="models-toolbar__spacer" />

        <button
          className="ghost-button"
          type="button"
          onClick={() => setShowTagCreate((value) => !value)}
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
              if (e.key === "Enter") void handleCreateTag();
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
            onClick={() => void handleCreateTag()}
            disabled={!newTagName.trim() || tagAction !== null}
          >
            Create
          </button>
        </div>
      )}

      {checkedIds.size > 0 && (
        <div className="models-bulk-bar" role="region" aria-label="Bulk actions">
          <span className="models-bulk-bar__count">
            {checkedIds.size} selected
            {selectedBlockedCount > 0 ? `, ${selectedDeletableModels.length} can be deleted` : ""}
          </span>
          <div className="button-row">
            {tags.map((tag) => (
              <button
                key={tag.id}
                className="secondary-button secondary-button--small"
                type="button"
                onClick={() => void handleBulkAddTag(tag.id)}
                disabled={tagAction !== null}
              >
                <Tag size={12} aria-hidden="true" />
                Add "{tag.name}"
              </button>
            ))}
            <button
              className="secondary-button secondary-button--small secondary-button--danger"
              type="button"
              onClick={() => void handleDeleteSelectedModels()}
              disabled={deleteBusy || selectedDeletableModels.length === 0}
              title={
                selectedDeletableModels.length === 0
                  ? "Selected models cannot be deleted from a configured model folder"
                  : "Delete selected model files"
              }
            >
              <Trash2 size={12} aria-hidden="true" />
              Delete selected
            </button>
          </div>
          <button className="ghost-button" type="button" onClick={() => setCheckedIds(new Set())}>
            <X size={14} aria-hidden="true" />
            Clear
          </button>
        </div>
      )}

      <div className={`models-layout${panelOpen ? " models-layout--panel-open" : ""}`}>
        <div className="models-list-area">
          {filteredModels.length > 0 && (
            <div className="models-table-head">
              <div className="model-col model-col-check">
                <input
                  type="checkbox"
                  checked={allFilteredModelsSelected}
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

          {inventoryState.loading && models.length === 0 ? (
            <div className="models-empty">Loading models...</div>
          ) : filteredModels.length === 0 ? (
            <div className="models-empty">
              {models.length === 0 ? (
                <>
                  <Package size={44} aria-hidden="true" />
                  <h3>No models found</h3>
                  <p>Add a model or import a workflow that needs one.</p>
                  <div className="models-empty__actions">
                    <button className="primary-button" type="button" onClick={() => setShowImport(true)}>
                      <Plus size={16} aria-hidden="true" />
                      Add Model
                    </button>
                    <button className="secondary-button" type="button" onClick={() => onNavigate("workflows")}>
                      Open Workflows
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
                  key={model.model_key}
                  model={model}
                  tags={tags}
                  checked={checkedIds.has(model.model_key)}
                  selected={selectedModelId === model.model_key}
                  downloading={downloadBusy}
                  onCheck={(checked) => handleToggleCheck(model.model_key, checked)}
                  onSelect={() => handleSelectModel(model.model_key)}
                  onDownload={() => void handleDownload(uniqueDownloadSelections(model.downloadable_references))}
                />
              ))}
            </div>
          )}
        </div>

        {selectedModel && (
          <aside className="models-detail-panel" aria-label={`Details for ${selectedModel.filename}`}>
            <DetailPanel
              model={selectedModel}
              tags={tags}
              showDevDetails={showDevDetails}
              showAddTag={showAddTag}
              downloadBusy={downloadBusy}
              deleteBusy={deleteBusy}
              onClose={() => setSelectedModelId(null)}
              onToggleDevDetails={() => setShowDevDetails((value) => !value)}
              onToggleAddTag={() => setShowAddTag((value) => !value)}
              onTagToggle={(tagId) => void handleModelTagToggle(selectedModel, tagId)}
              onReveal={() => {
                const path = modelFolderPath(selectedModel);
                if (path) void openFolder(path);
              }}
              onDownload={() => void handleDownload(uniqueDownloadSelections(selectedModel.downloadable_references))}
              onDelete={() => void handleDeleteModel(selectedModel)}
            />
          </aside>
        )}
      </div>
    </AppLayout>
  );
}
