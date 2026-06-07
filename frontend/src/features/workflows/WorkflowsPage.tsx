import { ChangeEvent, type MutableRefObject, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  FileUp,
  Loader2,
  PackageOpen,
  Play,
  Search,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  deleteWorkflowIcon,
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchWorkflowDetails,
  fetchWorkflowIcons,
  fetchWorkflowPackage,
  removeWorkflow,
  updateWorkflowMetadata,
  uploadWorkflowIcon,
  type WorkflowDetails,
  type WorkflowIconOption,
  type WorkflowMetadataUpdate,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import { resolveBackendUrl } from "../../lib/api/client";
import { workflowDisplayName } from "../../lib/workflowNames";
import type { WorkflowExportReviewModel } from "../../lib/workflowExport";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import { clearDashboardDraft, type DashboardSchema } from "../dashboard-builder/dashboardBuilderContent";
import { removePendingImportedSetupReminder } from "../home/pendingSetupBanners";
import { useWorkflowLibrary } from "../home/WorkflowLibraryProvider";
import { buildDashboardSchemaForEditing } from "./dashboardEditing";
import { WorkflowActionMenu } from "./WorkflowActionMenu";
import { WorkflowExportDialog } from "./WorkflowExportDialog";
import { DuplicateWorkflowModal, RequiredModelsModal } from "./WorkflowImportModals";
import { useWorkflowImportFlow } from "./useWorkflowImportFlow";
import {
  hardwareWarningBasis,
  hardwareWarningDeveloperDetailsText,
  hardwareWarningEstimateText,
  hardwareWarningExplanation,
  hardwareWarningMachineText,
  hardwareWarningPillView,
} from "./hardwareWarning";
import { NATIVE_WORKFLOW_ICON_OPTIONS, WORKFLOW_CATEGORY_OPTIONS, WORKFLOW_ICONS, workflowCategoryOption } from "./workflowMetadataOptions";
import { searchWorkflows, workflowStatus, workflowStatusLabel } from "./workflowSearch";

interface WorkflowsPageProps {
  onNavigate: (route: AppRouteId) => void;
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  onEditWidgets: (schema: DashboardSchema) => void;
  onEditDashboard: (schema: DashboardSchema) => void;
  initialSearchQuery?: string;
}

type SortDirection = "asc" | "desc";
type WorkflowSortKey = "name" | "tags" | "status" | "source" | "category" | "mainModel";

interface WorkflowSortState {
  key: WorkflowSortKey;
  direction: SortDirection;
}

const workflowStatusSortOrder: Record<string, number> = {
  missing_models: 0,
  need_setup: 1,
  ready: 2,
  failed: 3,
};

function formatDate(value: string | null | undefined) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function formatDuration(seconds: number | null | undefined) {
  if (seconds == null) return "Not recorded";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes}m ${remaining}s`;
}

function formatBytes(bytes: number | null | undefined) {
  if (bytes == null) return "Unknown size";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function WorkflowsPage({
  onNavigate,
  onOpenWorkflow,
  onConfigureDashboard,
  onEditWidgets,
  onEditDashboard,
  initialSearchQuery = "",
}: WorkflowsPageProps) {
  const runtimeStatus = useRuntimeStatus();
  const workflowLibrary = useWorkflowLibrary();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const detailsPanelFrameRef = useRef<number | null>(null);
  const detailsPanelCloseTimerRef = useRef<number | null>(null);
  const [activeCategory, setActiveCategory] = useState("All");
  const [search, setSearch] = useState(initialSearchQuery);
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("all");
  const [sort, setSort] = useState<WorkflowSortState | null>({ key: "name", direction: "asc" });
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [removeBusy, setRemoveBusy] = useState(false);
  const [detailsPanelOpen, setDetailsPanelOpen] = useState(false);
  const [details, setDetails] = useState<Record<string, WorkflowDetails>>({});
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
  const [showExportHelp, setShowExportHelp] = useState(false);
  const {
    state: importFlow,
    startWorkflowImport,
    downloadMissingModels,
    cancelModelDownload,
    continueImport,
    duplicateImport,
    readyImportAction,
    cancelImport,
  } = useWorkflowImportFlow({ onOpenWorkflow, onConfigureDashboard });
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [exportDialog, setExportDialog] = useState<{
    workflowName: string;
    exportUrl: string;
    extension: ".noofy" | ".json";
    review?: WorkflowExportReviewModel;
  } | null>(null);

  useEffect(() => {
    void runtimeStatus.refreshRuntime({ silent: true });
    void workflowLibrary.refreshWorkflows();
  }, [runtimeStatus.refreshRuntime, workflowLibrary.refreshWorkflows]);

  useEffect(() => {
    setSearch(initialSearchQuery);
  }, [initialSearchQuery]);

  useEffect(() => {
    return () => {
      if (detailsPanelFrameRef.current !== null) {
        window.cancelAnimationFrame(detailsPanelFrameRef.current);
      }
      if (detailsPanelCloseTimerRef.current !== null) {
        window.clearTimeout(detailsPanelCloseTimerRef.current);
      }
    };
  }, []);

  const workflows = workflowLibrary.workflows;
  const uniqueTags = useMemo(
    () => Array.from(new Set(workflows.flatMap((workflow) => workflow.tags ?? []))).sort(),
    [workflows],
  );
  const uniqueCategories = useMemo(
    () => Array.from(new Set(workflows.map((workflow) => workflow.category).filter(Boolean) as string[])).sort(),
    [workflows],
  );
  const categoryTabs = useMemo(() => ["All", ...uniqueCategories], [uniqueCategories]);

  useEffect(() => {
    if (!categoryTabs.includes(activeCategory)) {
      setActiveCategory("All");
    }
  }, [activeCategory, categoryTabs]);

  const filteredWorkflows = useMemo(
    () => {
      const filtered = searchWorkflows(workflows, {
        query: search,
        activeCategory,
        categoryFilter,
        sourceFilter,
        statusFilter,
        tagFilter,
      });
      if (!sort) return filtered;
      return stableSort(filtered, (a, b) => compareWorkflows(a, b, sort));
    },
    [activeCategory, categoryFilter, search, sourceFilter, statusFilter, tagFilter, workflows, sort],
  );

  const selectedSummary = selectedWorkflowId
    ? workflows.find((workflow) => workflow.id === selectedWorkflowId) ?? null
    : null;
  const selectedDetails = selectedWorkflowId ? details[selectedWorkflowId] ?? null : null;
  const selectedWorkflows = useMemo(
    () => workflows.filter((workflow) => checkedIds.has(workflow.id)),
    [checkedIds, workflows],
  );
  const selectedRemovableWorkflows = useMemo(
    () => selectedWorkflows.filter((workflow) => workflow.can_remove),
    [selectedWorkflows],
  );
  const selectedBlockedCount = selectedWorkflows.length - selectedRemovableWorkflows.length;
  const allFilteredWorkflowsSelected =
    filteredWorkflows.length > 0 && filteredWorkflows.every((workflow) => checkedIds.has(workflow.id));

  const readyCount = workflows.filter((workflow) => workflowStatus(workflow) === "ready").length;
  const needSetupCount = workflows.filter((workflow) => workflowStatus(workflow) === "need_setup").length;
  const missingModelsCount = workflows.reduce((total, workflow) => total + (workflow.missing_model_count ?? 0), 0);

  useEffect(() => {
    const workflowIds = new Set(workflows.map((workflow) => workflow.id));
    setCheckedIds((current) => {
      const next = new Set([...current].filter((id) => workflowIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [workflows]);

  async function handleViewModelsAfterImportDiskSpaceFailure() {
    await cancelImport();
    onNavigate("models");
  }

  async function openDetails(workflow: WorkflowSummary) {
    if (detailsPanelCloseTimerRef.current !== null) {
      window.clearTimeout(detailsPanelCloseTimerRef.current);
      detailsPanelCloseTimerRef.current = null;
    }
    if (detailsPanelFrameRef.current !== null) {
      window.cancelAnimationFrame(detailsPanelFrameRef.current);
      detailsPanelFrameRef.current = null;
    }
    const shouldSlideIn = !selectedWorkflowId || !detailsPanelOpen;
    setSelectedWorkflowId(workflow.id);
    if (shouldSlideIn) {
      setDetailsPanelOpen(false);
      detailsPanelFrameRef.current = window.requestAnimationFrame(() => {
        detailsPanelFrameRef.current = null;
        setDetailsPanelOpen(true);
      });
    } else {
      setDetailsPanelOpen(true);
    }
    setMenuOpenFor(null);
    if (details[workflow.id]) return;
    setDetailsLoading(true);
    setDetailsError(null);
    try {
      const loaded = await fetchWorkflowDetails(workflow.id);
      setDetails((current) => ({ ...current, [workflow.id]: loaded }));
    } catch (error) {
      setDetailsError(error instanceof Error ? error.message : String(error));
    } finally {
      setDetailsLoading(false);
    }
  }

  async function handleImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await startWorkflowImport(file);
  }

  async function handleRemove(workflow: WorkflowSummary) {
    if (!workflow.can_remove) return;
    const confirmed = window.confirm(`Remove "${workflowDisplayName(workflow)}" from Noofy?`);
    if (!confirmed) return;
    await removeWorkflow(workflow.id);
    // Drop the local builder draft so a future reimport (which reuses the same
    // deterministic workflow id) starts from the freshly imported dashboard
    // instead of resurrecting stale, possibly-duplicated widgets.
    clearDashboardDraft(workflow.id);
    removePendingImportedSetupReminder(workflow.id);
    if (selectedWorkflowId === workflow.id) {
      setDetailsPanelOpen(false);
      setSelectedWorkflowId(null);
    }
    setDetails((current) => {
      const next = { ...current };
      delete next[workflow.id];
      return next;
    });
    await workflowLibrary.refreshWorkflows();
  }

  function handleToggleCheck(id: string, checked: boolean) {
    setCheckedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function handleSelectAll() {
    setCheckedIds((current) => {
      const next = new Set(current);
      if (allFilteredWorkflowsSelected) {
        filteredWorkflows.forEach((workflow) => next.delete(workflow.id));
      } else {
        filteredWorkflows.forEach((workflow) => next.add(workflow.id));
      }
      return next;
    });
  }

  async function handleRemoveSelectedWorkflows() {
    if (selectedRemovableWorkflows.length === 0) {
      setActionError("None of the selected workflows can be removed from Noofy.");
      return;
    }

    const removeLabel =
      selectedRemovableWorkflows.length === 1
        ? `"${workflowDisplayName(selectedRemovableWorkflows[0])}"`
        : `${selectedRemovableWorkflows.length} workflows`;
    const skippedLabel =
      selectedBlockedCount > 0
        ? ` ${selectedBlockedCount} selected workflow${selectedBlockedCount === 1 ? "" : "s"} will be skipped because ${
            selectedBlockedCount === 1 ? "it cannot" : "they cannot"
          } be removed.`
        : "";
    if (!window.confirm(`Remove ${removeLabel} from Noofy?${skippedLabel}`)) return;

    setRemoveBusy(true);
    setActionError(null);
    setActionMessage(null);
    const failedIds = new Set<string>();
    const removedIds = new Set<string>();
    try {
      for (const workflow of selectedRemovableWorkflows) {
        try {
          await removeWorkflow(workflow.id);
          clearDashboardDraft(workflow.id);
          removePendingImportedSetupReminder(workflow.id);
          removedIds.add(workflow.id);
        } catch {
          failedIds.add(workflow.id);
        }
      }

      if (removedIds.has(selectedWorkflowId ?? "")) {
        setDetailsPanelOpen(false);
        setSelectedWorkflowId(null);
      }
      setDetails((current) => {
        const next = { ...current };
        removedIds.forEach((id) => delete next[id]);
        return next;
      });
      setCheckedIds(new Set(failedIds));
      await workflowLibrary.refreshWorkflows();

      if (failedIds.size > 0) {
        setActionError(
          `Removed ${removedIds.size} workflow${removedIds.size === 1 ? "" : "s"}. ${
            failedIds.size
          } selected workflow${failedIds.size === 1 ? "" : "s"} could not be removed.`,
        );
      } else {
        setActionMessage(`Removed ${removedIds.size} workflow${removedIds.size === 1 ? "" : "s"} from Noofy.`);
      }
    } finally {
      setRemoveBusy(false);
    }
  }

  function closeDetailsPanel() {
    if (detailsPanelFrameRef.current !== null) {
      window.cancelAnimationFrame(detailsPanelFrameRef.current);
      detailsPanelFrameRef.current = null;
    }
    if (detailsPanelCloseTimerRef.current !== null) {
      window.clearTimeout(detailsPanelCloseTimerRef.current);
    }
    setDetailsPanelOpen(false);
    detailsPanelCloseTimerRef.current = window.setTimeout(() => {
      detailsPanelCloseTimerRef.current = null;
      setSelectedWorkflowId(null);
    }, 260);
  }

  async function handleMetadataSave(workflowId: string, payload: WorkflowMetadataUpdate) {
    await updateWorkflowMetadata(workflowId, payload);
    const loaded = await fetchWorkflowDetails(workflowId);
    setDetails((current) => ({ ...current, [workflowId]: loaded }));
    await workflowLibrary.refreshWorkflows();
  }

  function handleSort(key: WorkflowSortKey) {
    setSort((current) => ({
      key,
      direction: current?.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  }

  async function handleEditDashboard(workflow: WorkflowSummary) {
    await openBuilder(workflow, onEditDashboard);
  }

  async function handleEditWidgets(workflow: WorkflowSummary) {
    await openBuilder(workflow, onEditWidgets);
  }

  async function openBuilder(workflow: WorkflowSummary, open: (schema: DashboardSchema) => void) {
    setMenuOpenFor(null);
    setActionError(null);
    try {
      const packageData = await fetchWorkflowPackage(workflow.id);
      open(buildDashboardSchemaForEditing(packageData));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <AppLayout
      activeRoute="workflows"
      onNavigate={onNavigate}
      mainClassName="main-workspace--workflows"
      contentClassName="workspace-content--workflows"
    >
      <div className={`workflows-layout${detailsPanelOpen ? " workflows-layout--drawer-open" : ""}`}>
        <div className="workflows-list-area">
          <section className="page-heading page-heading--compact page-heading--workflows" aria-labelledby="workflows-title">
            <div>
              <h1 id="workflows-title">Workflows</h1>
              <p>Manage native and imported workflows you can run in Noofy.</p>
            </div>
            <div className="button-row workflow-page-actions">
              <button className="secondary-button" type="button" onClick={() => setShowExportHelp((value) => !value)}>
                How to export workflows from ComfyUI
              </button>
              <button
                className="primary-button"
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={importFlow.importing || importFlow.downloadingModels}
              >
                <FileUp size={16} aria-hidden="true" />
                {importFlow.importing ? "Importing..." : "Import Workflow"}
              </button>
              <input ref={fileInputRef} className="sr-only" type="file" accept=".noofy" onChange={handleImport} />
            </div>
          </section>

          <div className="models-summary-bar" role="region" aria-label="Workflow statistics">
            <div className="models-stat-card">
              <div className="models-stat-card__value">{workflows.length}</div>
              <div className="models-stat-card__label">Total workflows</div>
            </div>
            <div className="models-stat-card">
              <div className="models-stat-card__value">{readyCount}</div>
              <div className="models-stat-card__label">Ready</div>
            </div>
            <div className={`models-stat-card${needSetupCount > 0 ? " models-stat-card--warning" : ""}`}>
              <div className="models-stat-card__value">{needSetupCount}</div>
              <div className="models-stat-card__label">Need setup</div>
            </div>
            <div className={`models-stat-card${missingModelsCount > 0 ? " models-stat-card--warning" : ""}`}>
              <div className="models-stat-card__value">{missingModelsCount}</div>
              <div className="models-stat-card__label">Missing models</div>
            </div>
          </div>

          {showExportHelp ? (
            <div className="workflow-help-panel" role="region" aria-label="How to export workflows from ComfyUI">
              <button className="icon-button" type="button" onClick={() => setShowExportHelp(false)} aria-label="Close export help">
                <X size={16} aria-hidden="true" />
              </button>
              <div>
                <strong>Build in ComfyUI, Run in Noofy</strong>
                <p>
                  Export your ComfyUI workflow as regular JSON ComfyUI workflow, or package it as a .noofy archive using Export2Noofy, a dedicated ComfyUI custom node made for exporting workflows to Noofy.<br />
Once imported, Noofy transforms your advanced node graph into a simple dashboard, with only the controls you decided to expose. This keeps the full power in the creator’s hands, while giving users a clean interface anyone can understand and use.<br />
Noofy hides the technical complexity, manages the workflow experience, and lets users run powerful AI workflows with a few clear controls and one Run button.
                </p>
              </div>
            </div>
          ) : null}

          {importFlow.importResult ? (
            <div className="notice notice--row" role="status">
              <CheckCircle2 size={18} aria-hidden="true" />
              <div>
                <strong>{importFlow.importResult.user_facing_message}</strong>
                <span>{workflowDisplayName(importFlow.importResult.workflow)} was added to your local workflows.</span>
              </div>
            </div>
          ) : null}
          {importFlow.importError ? <div className="notice notice--warning">{importFlow.importError}</div> : null}
          {actionError ? <div className="notice notice--warning">{actionError}</div> : null}
          {actionMessage ? <div className="notice">{actionMessage}</div> : null}

          <div className="models-toolbar models-toolbar--workflows">
            <label className="search-field search-field--models">
              <Search size={16} aria-hidden="true" />
              <span className="sr-only">Search workflows</span>
              <input type="search" placeholder="Search workflows..." value={search} onChange={(e) => setSearch(e.target.value)} />
            </label>

            <FilterSelect label="Filter by status" value={statusFilter} onChange={setStatusFilter}>
              <option value="all">All status</option>
              <option value="ready">Ready</option>
              <option value="need_setup">Need setup</option>
              <option value="missing_models">Missing models</option>
            </FilterSelect>
            <FilterSelect label="Filter by category" value={categoryFilter} onChange={setCategoryFilter}>
              <option value="all">All categories</option>
              {uniqueCategories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </FilterSelect>
            <FilterSelect label="Filter by source" value={sourceFilter} onChange={setSourceFilter}>
              <option value="all">All sources</option>
              <option value="Native Noofy">Native Noofy</option>
              <option value="Imported">Imported</option>
              <option value="Created by me">Created by me</option>
            </FilterSelect>
            <FilterSelect label="Filter by tags" value={tagFilter} onChange={setTagFilter}>
              <option value="all">All tags</option>
              {uniqueTags.map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </FilterSelect>
          </div>

          <div className="models-type-tabs" role="tablist" aria-label="Filter by workflow category">
            {categoryTabs.map((category) => (
              <button
                key={category}
                role="tab"
                aria-selected={activeCategory === category}
                className={`models-type-tab${activeCategory === category ? " models-type-tab--active" : ""}`}
                type="button"
                onClick={() => setActiveCategory(category)}
              >
                {category}
              </button>
            ))}
          </div>

          {checkedIds.size > 0 && (
            <div className="models-bulk-bar" role="region" aria-label="Bulk actions">
              <span className="models-bulk-bar__count">
                {checkedIds.size} selected
                {selectedBlockedCount > 0 ? `, ${selectedRemovableWorkflows.length} can be removed` : ""}
              </span>
              <div className="button-row">
                <button
                  className="secondary-button secondary-button--small secondary-button--danger"
                  type="button"
                  onClick={() => void handleRemoveSelectedWorkflows()}
                  disabled={removeBusy || selectedRemovableWorkflows.length === 0}
                  title={
                    selectedRemovableWorkflows.length === 0
                      ? "Selected workflows cannot be removed from Noofy"
                      : "Remove selected workflows"
                  }
                >
                  <Trash2 size={12} aria-hidden="true" />
                  Remove selected
                </button>
              </div>
              <button className="ghost-button" type="button" onClick={() => setCheckedIds(new Set())}>
                <X size={14} aria-hidden="true" />
                Clear
              </button>
            </div>
          )}

          <div className="workflows-table-head">
            <div className="workflow-col workflow-col-check">
              <input
                type="checkbox"
                checked={allFilteredWorkflowsSelected}
                onChange={handleSelectAll}
                aria-label="Select all workflows"
              />
            </div>
            <SortableHeader
              className="workflow-col workflow-col-main"
              label="Name"
              column="name"
              sort={sort}
              onSort={handleSort}
            />
            <SortableHeader
              className="workflow-col workflow-col-model"
              label="Main model"
              column="mainModel"
              sort={sort}
              onSort={handleSort}
            />
            <div className="workflow-col workflow-col-description">Description</div>
            <SortableHeader
              className="workflow-col workflow-col-status"
              label="Status"
              column="status"
              sort={sort}
              onSort={handleSort}
            />
            <SortableHeader
              className="workflow-col workflow-col-source"
              label="Source"
              column="source"
              sort={sort}
              onSort={handleSort}
            />
            <SortableHeader
              className="workflow-col workflow-col-category"
              label="Category"
              column="category"
              sort={sort}
              onSort={handleSort}
            />
            <SortableHeader
              className="workflow-col workflow-col-tags"
              label="Tags"
              column="tags"
              sort={sort}
              onSort={handleSort}
            />
            <div className="workflow-col workflow-col-actions">Actions</div>
          </div>

          {workflowLibrary.refreshing && !workflowLibrary.hasLoaded ? (
            <div className="models-empty">Loading workflows...</div>
          ) : filteredWorkflows.length === 0 ? (
            <div className="models-empty">
              <PackageOpen size={40} aria-hidden="true" />
              <h3>No workflows match your filters</h3>
              <p>Try adjusting search, status, category, source, or tag filters.</p>
            </div>
          ) : (
            <div className="workflows-table" role="list">
              {filteredWorkflows.map((workflow) => (
                <WorkflowRow
                  key={workflow.id}
                  workflow={workflow}
                  selected={selectedWorkflowId === workflow.id}
                  checked={checkedIds.has(workflow.id)}
                  menuOpen={menuOpenFor === workflow.id}
                  onCheck={(checked) => handleToggleCheck(workflow.id, checked)}
                  onOpen={() => onOpenWorkflow(workflow.id)}
                  onDetails={() => void openDetails(workflow)}
                  onToggleMenu={() => setMenuOpenFor((current) => (current === workflow.id ? null : workflow.id))}
                  onCloseMenu={() => setMenuOpenFor(null)}
                  onEditDashboard={() => void handleEditDashboard(workflow)}
                  onEditWidgets={() => void handleEditWidgets(workflow)}
                  onExportNoofy={() =>
                    setExportDialog({
                      workflowName: workflowDisplayName(workflow),
                      exportUrl: exportWorkflowUrl(workflow.id),
                      extension: ".noofy",
                      review: workflowSummaryExportReview(workflow),
                    })
                  }
                  onExportComfyJson={() =>
                    setExportDialog({ workflowName: workflowDisplayName(workflow), exportUrl: exportWorkflowComfyJsonUrl(workflow.id), extension: ".json" })
                  }
                  onRemove={() => void handleRemove(workflow)}
                />
              ))}
            </div>
          )}
        </div>

        {selectedSummary ? (
          <aside
            className={`workflow-detail-drawer${detailsPanelOpen ? " workflow-detail-drawer--open" : ""}`}
            aria-label={`Details for ${workflowDisplayName(selectedSummary)}`}
          >
            {detailsLoading && !selectedDetails ? (
              <WorkflowDetailsFallback
                workflow={selectedSummary}
                message="Loading details..."
                onClose={closeDetailsPanel}
              />
            ) : detailsError ? (
              <WorkflowDetailsFallback
                workflow={selectedSummary}
                message={detailsError}
                onClose={closeDetailsPanel}
              />
            ) : selectedDetails ? (
              <WorkflowDetailsDrawer
                workflow={selectedDetails}
                onClose={closeDetailsPanel}
                onOpen={() => onOpenWorkflow(selectedDetails.id)}
                onSave={(payload) => handleMetadataSave(selectedDetails.id, payload)}
                onExport={(workflowName, exportUrl, extension, review) => setExportDialog({ workflowName, exportUrl, extension, review })}
              />
            ) : null}
          </aside>
        ) : null}
      </div>
      {exportDialog ? (
        <WorkflowExportDialog
          workflowName={exportDialog.workflowName}
          exportUrl={exportDialog.exportUrl}
          extension={exportDialog.extension}
          review={exportDialog.review}
          onClose={() => setExportDialog(null)}
        />
      ) : null}
      {importFlow.pendingImport?.duplicate_identity && !importFlow.pendingImport.model_summary ? (
        <DuplicateWorkflowModal
          importResult={importFlow.pendingImport}
          busy={importFlow.importing}
          onReplace={() => void duplicateImport("replace")}
          onCopy={() => void duplicateImport("copy")}
          onCancel={() => void cancelImport()}
        />
      ) : null}
      {importFlow.pendingImport?.model_summary ? (
        <RequiredModelsModal
          importResult={importFlow.pendingImport}
          busy={importFlow.importing || importFlow.downloadingModels}
          importing={importFlow.importing}
          downloadJob={importFlow.downloadJob}
          verificationJob={importFlow.verificationJob}
          onDownload={() => void downloadMissingModels()}
          onCancelDownload={() => void cancelModelDownload()}
          onContinue={() => void continueImport()}
          onReplace={() => void duplicateImport("replace")}
          onCopy={() => void duplicateImport("copy")}
          onReadyAction={() => void readyImportAction()}
          onCancel={() => void cancelImport()}
          onViewModels={() => void handleViewModelsAfterImportDiskSpaceFailure()}
        />
      ) : null}
    </AppLayout>
  );
}

function WorkflowDetailsFallback({
  workflow,
  message,
  onClose,
}: {
  workflow: WorkflowSummary;
  message: string;
  onClose: () => void;
}) {
  const Icon = WORKFLOW_ICONS[(workflow.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? Sparkles;
  const displayName = workflowDisplayName(workflow);

  return (
    <>
      <div className="detail-panel__header">
        <div className="detail-panel__title-group">
          <div className="model-type-icon model-type-icon--lg" aria-hidden="true">
            <WorkflowIconVisual icon={workflow.icon} size={20} Icon={Icon} />
          </div>
          <div className="detail-panel__title-text">
            <h2 className="detail-panel__title">{displayName}</h2>
            <span className={`workflow-status workflow-status--${workflowStatus(workflow)}`}>
              {workflowStatusLabel(workflow)}
            </span>
          </div>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close workflow details">
          <X size={17} aria-hidden="true" />
        </button>
      </div>
      <div className="workflow-detail-drawer__loading">{message}</div>
    </>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="filter-select-wrap">
      <select className="filter-select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
      <ChevronDown size={13} aria-hidden="true" />
    </div>
  );
}

function WorkflowIconVisual({
  icon,
  size,
  Icon,
}: {
  icon?: string;
  size: number;
  Icon: LucideIcon;
}) {
  if (icon?.startsWith("asset:")) {
    const assetId = icon.slice("asset:".length);
    return (
      <img
        className="workflow-custom-icon"
        src={resolveBackendUrl(`/api/assets/${encodeURIComponent(assetId)}`, { includeToken: true })}
        alt=""
      />
    );
  }
  return <Icon size={size} />;
}

function SortableHeader({
  className,
  label,
  column,
  sort,
  onSort,
}: {
  className: string;
  label: string;
  column: WorkflowSortKey;
  sort: WorkflowSortState | null;
  onSort: (column: WorkflowSortKey) => void;
}) {
  const active = sort?.key === column;
  const nextDirection = active && sort?.direction === "asc" ? "descending" : "ascending";

  return (
    <div className={className}>
      <button
        className={`sortable-header${active ? " sortable-header--active" : ""}`}
        type="button"
        aria-label={`Sort by ${label} ${nextDirection}`}
        aria-pressed={active}
        onClick={() => onSort(column)}
      >
        <span>{label}</span>
        {active ? (
          sort?.direction === "asc" ? (
            <ChevronUp size={12} aria-hidden="true" />
          ) : (
            <ChevronDown size={12} aria-hidden="true" />
          )
        ) : (
          <span className="sortable-header__placeholder" aria-hidden="true" />
        )}
      </button>
    </div>
  );
}

function compareWorkflows(a: WorkflowSummary, b: WorkflowSummary, sort: WorkflowSortState) {
  const direction = sort.direction === "asc" ? 1 : -1;
  let result = 0;

  if (sort.key === "name") {
    result = compareText(workflowDisplayName(a), workflowDisplayName(b));
  } else if (sort.key === "tags") {
    result = compareText(workflowTagSortValue(a), workflowTagSortValue(b));
  } else if (sort.key === "status") {
    result = compareWorkflowStatus(a, b);
  } else if (sort.key === "source") {
    result = compareText(workflowSourceLabel(a), workflowSourceLabel(b));
  } else if (sort.key === "category") {
    result = compareText(workflowCategoryLabel(a), workflowCategoryLabel(b));
  } else if (sort.key === "mainModel") {
    result = compareText(workflowMainModelLabel(a), workflowMainModelLabel(b));
  }

  return result * direction;
}

function workflowTagSortValue(workflow: WorkflowSummary) {
  return [...(workflow.tags ?? [])].sort(compareText).join(" ");
}

function compareWorkflowStatus(a: WorkflowSummary, b: WorkflowSummary) {
  const statusA = workflowReadinessStatus(a);
  const statusB = workflowReadinessStatus(b);
  return workflowStatusSortOrder[statusA] - workflowStatusSortOrder[statusB] || compareText(statusA, statusB);
}

function workflowReadinessStatus(workflow: WorkflowSummary) {
  const backendStatus = `${workflow.status ?? ""} ${workflow.status_label ?? ""}`.toLowerCase();
  if (backendStatus.includes("fail") || backendStatus.includes("error")) return "failed";
  return workflowStatus(workflow);
}

function workflowReadinessLabel(workflow: WorkflowSummary) {
  return workflowReadinessStatus(workflow) === "failed" ? "Failed" : workflowStatusLabel(workflow);
}

function workflowSourceLabel(workflow: WorkflowSummary) {
  return workflow.source_label ?? workflow.trust?.label ?? workflow.trust_level ?? "Native Noofy";
}

function workflowCategoryLabel(workflow: WorkflowSummary) {
  return workflow.category ?? "Txt2img";
}

function workflowMainModelLabel(workflow: WorkflowSummary) {
  return workflow.main_model?.name ?? "";
}

function compareText(a: string | null | undefined, b: string | null | undefined) {
  return (a ?? "").localeCompare(b ?? "", undefined, { sensitivity: "base", numeric: true });
}

function stableSort<T>(items: T[], compare: (a: T, b: T) => number) {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => compare(a.item, b.item) || a.index - b.index)
    .map(({ item }) => item);
}

function workflowSummaryExportReview(workflow: WorkflowSummary): WorkflowExportReviewModel {
  return {
    name: workflowDisplayName(workflow),
    description: workflow.description ?? "",
    category: workflow.category ?? "",
    tags: workflow.tags ?? [],
    icon: workflow.icon ?? "",
    source: workflow.source_label ?? workflow.trust?.label ?? "Noofy workflow",
    requiredModels: workflow.main_model?.name ? [{
      name: workflow.main_model.name,
      type: workflow.main_model.type,
      size_bytes: workflow.main_model.size_bytes,
      status_label: workflow.missing_model_count && workflow.missing_model_count > 0 ? "Missing" : "Available",
    }] : [],
  };
}

function workflowDetailsExportReview(
  workflow: WorkflowDetails,
  draft?: WorkflowMetadataUpdate,
): WorkflowExportReviewModel {
  return {
    name: draft?.display_name ?? workflowDisplayName(workflow),
    description: draft?.description ?? workflow.overview.description,
    author: draft?.author ?? workflow.overview.author,
    website: draft?.website ?? workflow.overview.website,
    category: draft?.category ?? workflow.organization.category,
    tags: draft?.tags ?? workflow.organization.tags,
    icon: draft?.icon ?? workflow.organization.icon,
    source: workflow.overview.source,
    requiredModels: workflow.models_used,
  };
}

function WorkflowRow({
  workflow,
  selected,
  checked,
  menuOpen,
  onCheck,
  onOpen,
  onDetails,
  onToggleMenu,
  onCloseMenu,
  onEditDashboard,
  onEditWidgets,
  onExportNoofy,
  onExportComfyJson,
  onRemove,
}: {
  workflow: WorkflowSummary;
  selected: boolean;
  checked: boolean;
  menuOpen: boolean;
  onCheck: (checked: boolean) => void;
  onOpen: () => void;
  onDetails: () => void;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onEditDashboard: () => void;
  onEditWidgets: () => void;
  onExportNoofy: () => void;
  onExportComfyJson: () => void;
  onRemove: () => void;
}) {
  const Icon = WORKFLOW_ICONS[(workflow.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? Sparkles;
  const tags = workflow.tags ?? [];
  const readiness = workflowReadinessStatus(workflow);
  const displayName = workflowDisplayName(workflow);
  return (
    <article
      className={`workflow-row${selected ? " workflow-row--selected" : ""}${checked ? " workflow-row--checked" : ""}`}
      role="listitem"
      onClick={onDetails}
    >
      <div className="workflow-col workflow-col-check" onClick={(event) => event.stopPropagation()}>
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onCheck(event.target.checked)}
          aria-label={`Select ${displayName}`}
        />
      </div>
      <div className="workflow-col workflow-col-main">
        <div className="model-type-icon" aria-hidden="true">
          <WorkflowIconVisual icon={workflow.icon} size={16} Icon={Icon} />
        </div>
        <div className="model-main-body">
          <div className="model-name-text" title={displayName}>{displayName}</div>
          <div className="model-type-text" title={`Category: ${workflow.category ?? "Workflow"}`}>{workflow.category ?? "Workflow"}</div>
        </div>
      </div>
      <div className="workflow-col workflow-col-model" title={workflowMainModelLabel(workflow) || "No model detected"}>
        {workflowMainModelLabel(workflow) || "No model detected"}
      </div>
      <div className="workflow-col workflow-col-description" title={workflow.description || "No description yet"}>
        {workflow.description || "No description yet"}
      </div>
      <div className="workflow-col workflow-col-status">
        <div className="workflow-status-stack">
          <span className={`workflow-status workflow-status--${readiness}`}>
            {workflowReadinessLabel(workflow)}
          </span>
          {workflow.hardware_warning ? <HardwareWarningPill warning={workflow.hardware_warning} /> : null}
        </div>
      </div>
      <div className="workflow-col workflow-col-source" title={workflowSourceLabel(workflow)}>
        {workflowSourceLabel(workflow)}
      </div>
      <div className="workflow-col workflow-col-category">
        <span className="workflow-category-badge" title={workflowCategoryLabel(workflow)}>{workflowCategoryLabel(workflow)}</span>
      </div>
      <div className="workflow-col workflow-col-tags">
        <div className="model-tags-row">
          {tags.slice(0, 2).map((tag) => (
            <span key={tag} className="tag-pill tag-pill--more" title={tag}>
              {tag}
            </span>
          ))}
          {tags.length > 2 ? <span className="tag-pill tag-pill--more">+{tags.length - 2}</span> : null}
        </div>
      </div>
      <div className="workflow-col workflow-col-actions" onClick={(event) => event.stopPropagation()}>
        <button className="secondary-button secondary-button--small" type="button" onClick={onOpen}>
          <Play size={13} aria-hidden="true" />
          Open
        </button>
        <WorkflowActionMenu
          workflow={workflow}
          menuOpen={menuOpen}
          onOpen={onOpen}
          onDetails={onDetails}
          onToggleMenu={onToggleMenu}
          onCloseMenu={onCloseMenu}
          onEditDashboard={onEditDashboard}
          onEditWidgets={onEditWidgets}
          onExportNoofy={onExportNoofy}
          onExportComfyJson={onExportComfyJson}
          onRemove={onRemove}
        />
      </div>
    </article>
  );
}

function WorkflowDetailsDrawer({
  workflow,
  onClose,
  onOpen,
  onSave,
  onExport,
}: {
  workflow: WorkflowDetails;
  onClose: () => void;
  onOpen: () => void;
  onSave: (payload: WorkflowMetadataUpdate) => Promise<void> | void;
  onExport: (
    workflowName: string,
    exportUrl: string,
    extension: ".noofy" | ".json",
    review?: WorkflowExportReviewModel,
  ) => void;
}) {
  const workflowMetadataDraft = useMemo<WorkflowMetadataUpdate>(() => ({
    display_name: workflowDisplayName(workflow),
    description: workflow.overview.description,
    author: workflow.overview.author,
    website: workflow.overview.website,
    category: workflowCategoryOption(workflow.organization.category),
    tags: workflow.organization.tags,
    icon: workflow.organization.icon,
  }), [workflow]);
  const [draft, setDraft] = useState<WorkflowMetadataUpdate>(() => workflowMetadataDraft);
  const [savedDraft, setSavedDraft] = useState<WorkflowMetadataUpdate>(() => workflowMetadataDraft);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [customIcons, setCustomIcons] = useState<WorkflowIconOption[]>([]);
  const [iconError, setIconError] = useState<string | null>(null);
  const [importingIcon, setImportingIcon] = useState(false);
  const iconInputRef = useRef<HTMLInputElement | null>(null);
  const selectedIcon = draft.icon || workflow.organization.icon || "sparkles";
  const displayName = draft.display_name?.trim() || workflowDisplayName(workflow);
  const Icon = WORKFLOW_ICONS[(selectedIcon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? Sparkles;

  useEffect(() => {
    setDraft(workflowMetadataDraft);
    setSavedDraft(workflowMetadataDraft);
    setSaveError(null);
    setIconError(null);
  }, [workflowMetadataDraft]);

  useEffect(() => {
    let cancelled = false;
    fetchWorkflowIcons()
      .then((response) => {
        if (!cancelled) setCustomIcons(response.icons);
      })
      .catch((error) => {
        if (!cancelled) setIconError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveDraft(nextDraft: WorkflowMetadataUpdate = draft) {
    if (metadataDraftsEqual(nextDraft, savedDraft)) return true;
    setSaveError(null);
    try {
      await onSave(nextDraft);
      setSavedDraft(nextDraft);
      return true;
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }

  function selectIcon(icon: string) {
    const nextDraft = { ...draft, icon };
    setDraft(nextDraft);
    setIconError(null);
    void saveDraft(nextDraft);
  }

  async function handleIconImport(file: File | undefined) {
    if (!file) return;
    setIconError(null);
    if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) {
      setIconError("Use a PNG, JPEG, WebP, or GIF image for custom workflow icons.");
      return;
    }
    setImportingIcon(true);
    try {
      const icon = await uploadWorkflowIcon(file);
      setCustomIcons((current) => [...current.filter((item) => item.id !== icon.id), icon]);
      selectIcon(icon.id);
    } catch (error) {
      setIconError(error instanceof Error ? error.message : String(error));
    } finally {
      setImportingIcon(false);
      if (iconInputRef.current) iconInputRef.current.value = "";
    }
  }

  async function handleIconDelete(icon: WorkflowIconOption) {
    setIconError(null);
    try {
      await deleteWorkflowIcon(icon.id);
      setCustomIcons((current) => current.filter((item) => item.id !== icon.id));
      if (selectedIcon === icon.id) {
        selectIcon("sparkles");
      }
    } catch (error) {
      setIconError(error instanceof Error ? error.message : String(error));
    }
  }

  function handleExportClick(exportUrl: string, extension: ".noofy" | ".json") {
    void saveDraft().then((saved) => {
      if (!saved) return;
      onExport(
        displayName,
        exportUrl,
        extension,
        extension === ".noofy" ? workflowDetailsExportReview(workflow, draft) : undefined,
      );
    });
  }

  return (
    <>
      <div className="workflow-detail-sticky-top">
        <div className="detail-panel__header">
          <div className="detail-panel__title-group">
            <div className="model-type-icon model-type-icon--lg" aria-hidden="true">
              <WorkflowIconVisual icon={selectedIcon} size={20} Icon={Icon} />
            </div>
            <div className="detail-panel__title-text">
              <h2 className="detail-panel__title">{displayName}</h2>
              <span className={`workflow-status workflow-status--${workflowStatus(workflow)}`}>
                {workflowStatusLabel(workflow)}
              </span>
            </div>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close workflow details">
            <X size={17} aria-hidden="true" />
          </button>
        </div>

        <div className="workflow-detail-primary-actions">
          <button className="primary-button secondary-button--full workflow-open-cta" type="button" onClick={onOpen}>
            <Play size={15} aria-hidden="true" />
            Open Workflow
          </button>
        </div>
      </div>

      <DetailSection title="Overview">
        <EditableField
          label="Workflow name"
          value={draft.display_name ?? ""}
          onBlur={() => void saveDraft()}
          onChange={(display_name) => setDraft((current) => ({ ...current, display_name }))}
        />
        <EditableField
          label="Description"
          value={draft.description ?? ""}
          multiline
          onBlur={() => void saveDraft()}
          onChange={(description) => setDraft((current) => ({ ...current, description }))}
        />
        <EditableField
          label="Author"
          value={draft.author ?? ""}
          onBlur={() => void saveDraft()}
          onChange={(author) => setDraft((current) => ({ ...current, author }))}
        />
        <EditableField
          label="Website"
          value={draft.website ?? ""}
          onBlur={() => void saveDraft()}
          onChange={(website) => setDraft((current) => ({ ...current, website }))}
        />
        <dl className="detail-list detail-list--compact">
          <div><dt>Source</dt><dd>{workflow.overview.source}</dd></div>
          <div><dt>Version</dt><dd>{workflow.overview.version}</dd></div>
        </dl>
      </DetailSection>

      <DetailSection title="Models used">
        <div className="workflow-model-list">
          {workflow.models_used.length > 0 ? workflow.models_used.map((model) => (
            <div key={`${model.folder}-${model.name}`} className="workflow-model-item">
              <strong>{model.name}</strong>
              <span>{[model.type, formatBytes(model.size_bytes), model.status_label].filter(Boolean).join(" · ")}</span>
            </div>
          )) : <p className="detail-panel__tag-empty">No model detected.</p>}
        </div>
      </DetailSection>

      <DetailSection title="Run history">
        <dl className="detail-list detail-list--compact">
          <div><dt>Last run</dt><dd>{workflow.run_history.last_run_status ?? "Never"}</dd></div>
          <div><dt>Last duration</dt><dd>{formatDuration(workflow.run_history.last_duration_seconds)}</dd></div>
          <div><dt>Average duration</dt><dd>{formatDuration(workflow.run_history.average_duration_seconds)}</dd></div>
          <div><dt>Last error</dt><dd>{workflow.run_history.last_error ?? "None"}</dd></div>
        </dl>
      </DetailSection>

      {workflow.hardware_warning ? (
        <DetailSection title="Hardware compatibility">
          <HardwareWarningDetails warning={workflow.hardware_warning} />
        </DetailSection>
      ) : null}

      <DetailSection title="Organization">
        <EditableSelectField
          label="Category"
          value={workflowCategoryOption(draft.category)}
          options={WORKFLOW_CATEGORY_OPTIONS}
          onBlur={() => void saveDraft()}
          onChange={(category) => setDraft((current) => ({ ...current, category }))}
        />
        <EditableField
          label="Tags"
          value={(draft.tags ?? []).join(", ")}
          onBlur={() => void saveDraft()}
          onChange={(tags) => setDraft((current) => ({ ...current, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) }))}
        />
        <WorkflowMetadataIconPicker
          selectedIcon={selectedIcon}
          customIcons={customIcons}
          importingIcon={importingIcon}
          iconInputRef={iconInputRef}
          iconError={iconError}
          onImportIcon={handleIconImport}
          onDeleteIcon={handleIconDelete}
          onSelectIcon={selectIcon}
        />
        {saveError ? <p className="workflow-edit-error">{saveError}</p> : null}
      </DetailSection>

      <DetailSection title="Advanced">
        <dl className="detail-list detail-list--compact">
          <div><dt>Package ID</dt><dd>{workflow.advanced.package_id}</dd></div>
          <div><dt>Engine</dt><dd>{workflow.advanced.engine}</dd></div>
          <div><dt>Trust level</dt><dd>{workflow.advanced.trust_label}</dd></div>
        </dl>
      </DetailSection>

      <div className="workflow-detail-export-actions" aria-label="Workflow export actions">
        {workflow.advanced.can_export_noofy ? (
          <button
            className="primary-button secondary-button--full"
            type="button"
            onClick={() => handleExportClick(exportWorkflowUrl(workflow.id), ".noofy")}
          >
            <Download size={15} aria-hidden="true" />
            Export .Noofy
          </button>
        ) : null}
        <button
          className="secondary-button secondary-button--full"
          type="button"
          onClick={() => handleExportClick(exportWorkflowComfyJsonUrl(workflow.id), ".json")}
        >
          Export ComfyUI JSON
        </button>
      </div>
    </>
  );
}

function HardwareWarningPill({ warning }: { warning: NonNullable<WorkflowSummary["hardware_warning"]> }) {
  const view = hardwareWarningPillView(warning);
  return (
    <span
      className={`hardware-warning-pill hardware-warning-pill--${view.tone}`}
      title={view.tooltip}
    >
      {view.label}
    </span>
  );
}

function HardwareWarningDetails({ warning }: { warning: NonNullable<WorkflowSummary["hardware_warning"]> }) {
  return (
    <div className="hardware-warning-detail">
      <p>{hardwareWarningExplanation(warning)}</p>
      <dl className="detail-list detail-list--compact">
        <div><dt>Estimated need</dt><dd>{hardwareWarningEstimateText(warning)}</dd></div>
        <div><dt>Current machine</dt><dd>{hardwareWarningMachineText(warning)}</dd></div>
        <div><dt>Based on</dt><dd>{hardwareWarningBasis(warning)}</dd></div>
      </dl>
      <details className="hardware-warning-developer-details">
        <summary>Developer details</summary>
        <pre>{hardwareWarningDeveloperDetailsText(warning)}</pre>
      </details>
    </div>
  );
}

function WorkflowMetadataIconPicker({
  selectedIcon,
  customIcons,
  importingIcon,
  iconInputRef,
  iconError,
  onImportIcon,
  onDeleteIcon,
  onSelectIcon,
}: {
  selectedIcon: string;
  customIcons: WorkflowIconOption[];
  importingIcon: boolean;
  iconInputRef: MutableRefObject<HTMLInputElement | null>;
  iconError: string | null;
  onImportIcon: (file: File | undefined) => void;
  onDeleteIcon: (icon: WorkflowIconOption) => void;
  onSelectIcon: (icon: string) => void;
}) {
  return (
    <div className="workflow-export-icon-picker workflow-metadata-icon-picker">
      <div className="workflow-export-icon-picker__label">Icon</div>
      <div className="workflow-export-icon-grid" role="radiogroup" aria-label="Workflow icon">
        <button
          className="workflow-export-icon-tile workflow-export-icon-tile--import"
          type="button"
          onClick={() => iconInputRef.current?.click()}
          disabled={importingIcon}
        >
          {importingIcon ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <UploadCloud size={18} aria-hidden="true" />}
          <span>Import icon</span>
        </button>
        <input
          ref={(node) => {
            iconInputRef.current = node;
          }}
          className="visually-hidden"
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          onChange={(event) => onImportIcon(event.target.files?.[0])}
        />
        {NATIVE_WORKFLOW_ICON_OPTIONS.map(({ id, label, Icon }) => (
          <button
            key={id}
            className={`workflow-export-icon-tile${selectedIcon === id ? " workflow-export-icon-tile--selected" : ""}`}
            type="button"
            role="radio"
            aria-checked={selectedIcon === id}
            aria-label={label}
            onClick={() => onSelectIcon(id)}
          >
            <Icon size={20} aria-hidden="true" />
          </button>
        ))}
        {customIcons.map((icon) => (
          <div
            key={icon.id}
            className={`workflow-export-icon-tile workflow-export-icon-tile--custom${selectedIcon === icon.id ? " workflow-export-icon-tile--selected" : ""}`}
            role="radio"
            tabIndex={0}
            aria-checked={selectedIcon === icon.id}
            aria-label={icon.label}
            onClick={() => onSelectIcon(icon.id)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              onSelectIcon(icon.id);
            }}
          >
            <img src={resolveBackendUrl(icon.url, { includeToken: true })} alt="" />
            <span>{icon.label}</span>
            <button
              type="button"
              className="workflow-export-icon-tile__delete"
              aria-label={`Delete ${icon.label}`}
              onClick={(event) => {
                event.stopPropagation();
                onDeleteIcon(icon);
              }}
            >
              <Trash2 size={12} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
      {iconError ? <p className="workflow-export-modal__help workflow-export-modal__help--error">{iconError}</p> : null}
    </div>
  );
}

function metadataDraftsEqual(left: WorkflowMetadataUpdate, right: WorkflowMetadataUpdate) {
  return (
    (left.display_name ?? "") === (right.display_name ?? "") &&
    (left.description ?? "") === (right.description ?? "") &&
    (left.author ?? "") === (right.author ?? "") &&
    (left.website ?? "") === (right.website ?? "") &&
    (left.category ?? "") === (right.category ?? "") &&
    (left.icon ?? "") === (right.icon ?? "") &&
    (left.tags ?? []).join("\u0000") === (right.tags ?? []).join("\u0000")
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="detail-panel__section">
      <div className="detail-panel__section-label">{title}</div>
      {children}
    </section>
  );
}

function EditableField({
  label,
  value,
  multiline = false,
  onBlur,
  onChange,
}: {
  label: string;
  value: string;
  multiline?: boolean;
  onBlur?: () => void;
  onChange: (value: string) => void;
}) {
  return (
    <label className="workflow-edit-field">
      <span>{label}</span>
      {multiline ? (
        <textarea value={value} onBlur={onBlur} onChange={(event) => onChange(event.target.value)} rows={3} />
      ) : (
        <input value={value} onBlur={onBlur} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

function EditableSelectField({
  label,
  value,
  options,
  onBlur,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onBlur?: () => void;
  onChange: (value: string) => void;
}) {
  return (
    <label className="workflow-edit-field">
      <span>{label}</span>
      <select value={value} onBlur={onBlur} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}
