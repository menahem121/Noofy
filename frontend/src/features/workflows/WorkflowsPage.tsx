import { ChangeEvent, type MouseEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  Download,
  FileUp,
  Image,
  Maximize2,
  PackageOpen,
  Play,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";

import {
  commitWorkflowImport,
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchWorkflowDetails,
  fetchWorkflowPackage,
  previewWorkflowPackageImport,
  removeWorkflow,
  updateWorkflowMetadata,
  type WorkflowDetails,
  type WorkflowMetadataUpdate,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import {
  isNativeWorkflowExportAvailable,
  saveWorkflowExportToNativeFileWithAlert,
  workflowExportFilename,
} from "../../lib/workflowExport";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import type { DashboardSchema } from "../dashboard-builder/dashboardBuilderContent";
import { useWorkflowLibrary } from "../home/WorkflowLibraryProvider";
import { buildDashboardSchemaForEditing } from "./dashboardEditing";
import { WorkflowActionMenu } from "./WorkflowActionMenu";

interface WorkflowsPageProps {
  onNavigate: (route: AppRouteId) => void;
  onOpenWorkflow: (workflowId: string) => void;
  onEditWidgets: (schema: DashboardSchema) => void;
  onEditDashboard: (schema: DashboardSchema) => void;
}

type WorkflowCategory =
  | "All"
  | "Txt2img"
  | "Img2img"
  | "Inpainting"
  | "Outpainting"
  | "Upscaling"
  | "Style Transfer"
  | "Swapping"
  | "Character Consistency"
  | "Pose Control"
  | "Depth Control"
  | "Canny / Line Control"
  | "Background Replacement"
  | "Background Removal"
  | "Restoration"
  | "All-in-one";

const CATEGORY_FILTERS: WorkflowCategory[] = [
  "All",
  "Txt2img",
  "Img2img",
  "Inpainting",
  "Outpainting",
  "Upscaling",
  "Style Transfer",
  "Swapping",
  "Character Consistency",
  "Pose Control",
  "Depth Control",
  "Canny / Line Control",
  "Background Replacement",
  "Background Removal",
  "Restoration",
  "All-in-one",
];

const WORKFLOW_ICONS = {
  sparkles: Sparkles,
  image: Image,
  maximize: Maximize2,
  sliders: SlidersHorizontal,
  package: PackageOpen,
};

function workflowStatus(summary: WorkflowSummary) {
  if ((summary.missing_model_count ?? 0) > 0) return "missing_models";
  if (summary.needs_setup) return "need_setup";
  return "ready";
}

function workflowStatusLabel(summary: WorkflowSummary) {
  const status = workflowStatus(summary);
  if (status === "need_setup") return "Need setup";
  if (status === "missing_models") return "Missing models";
  return "Ready";
}

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
  onEditWidgets,
  onEditDashboard,
}: WorkflowsPageProps) {
  const runtimeStatus = useRuntimeStatus();
  const workflowLibrary = useWorkflowLibrary();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const detailsPanelFrameRef = useRef<number | null>(null);
  const detailsPanelCloseTimerRef = useRef<number | null>(null);
  const [activeCategory, setActiveCategory] = useState<WorkflowCategory>("All");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("all");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [detailsPanelOpen, setDetailsPanelOpen] = useState(false);
  const [details, setDetails] = useState<Record<string, WorkflowDetails>>({});
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
  const [showExportHelp, setShowExportHelp] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    void runtimeStatus.refreshRuntime({ silent: true });
    void workflowLibrary.refreshWorkflows();
  }, [runtimeStatus.refreshRuntime, workflowLibrary.refreshWorkflows]);

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

  const filteredWorkflows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return workflows.filter((workflow) => {
      const category = workflow.category ?? "Txt2img";
      const tags = workflow.tags ?? [];
      if (activeCategory !== "All" && category !== activeCategory) return false;
      if (categoryFilter !== "all" && category !== categoryFilter) return false;
      if (sourceFilter !== "all" && workflow.source_label !== sourceFilter) return false;
      if (tagFilter !== "all" && !tags.includes(tagFilter)) return false;
      if (statusFilter !== "all" && workflowStatus(workflow) !== statusFilter) return false;
      if (query) {
        const haystack = [
          workflow.name,
          workflow.description,
          workflow.main_model?.name,
          category,
          workflow.source_label,
          ...tags,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  }, [activeCategory, categoryFilter, search, sourceFilter, statusFilter, tagFilter, workflows]);

  const selectedSummary = selectedWorkflowId
    ? workflows.find((workflow) => workflow.id === selectedWorkflowId) ?? null
    : null;
  const selectedDetails = selectedWorkflowId ? details[selectedWorkflowId] ?? null : null;

  const readyCount = workflows.filter((workflow) => workflowStatus(workflow) === "ready").length;
  const needSetupCount = workflows.filter((workflow) => workflowStatus(workflow) === "need_setup").length;
  const missingModelsCount = workflows.reduce((total, workflow) => total + (workflow.missing_model_count ?? 0), 0);

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
    setImporting(true);
    setImportError(null);
    try {
      const preview = await previewWorkflowPackageImport(file, true);
      if (preview.import_session_id && (!preview.model_summary || preview.model_summary.ready_to_run)) {
        await commitWorkflowImport(preview.import_session_id);
      } else if (preview.import_session_id) {
        setImportError("This workflow needs models before it can be imported from this page. Use Home to review and download missing models.");
        return;
      }
      await workflowLibrary.refreshWorkflows();
    } catch (error) {
      setImportError(error instanceof Error ? error.message : String(error));
    } finally {
      setImporting(false);
    }
  }

  async function handleRemove(workflow: WorkflowSummary) {
    if (!workflow.can_remove) return;
    const confirmed = window.confirm(`Remove "${workflow.name}" from Noofy?`);
    if (!confirmed) return;
    await removeWorkflow(workflow.id);
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
      status={runtimeStatus.statusView}
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
              <button className="primary-button" type="button" onClick={() => fileInputRef.current?.click()} disabled={importing}>
                <FileUp size={16} aria-hidden="true" />
                {importing ? "Importing..." : "Import Workflow"}
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
                <strong>Export from ComfyUI, then import into Noofy.</strong>
                <p>
                  Save the workflow from ComfyUI as JSON or package it as a `.noofy` archive. Noofy will import the file,
                  keep its own editable copy, and run it through the backend engine adapter.
                </p>
              </div>
            </div>
          ) : null}

          {importError ? <div className="notice notice--warning">{importError}</div> : null}
          {actionError ? <div className="notice notice--warning">{actionError}</div> : null}

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
            {CATEGORY_FILTERS.map((category) => (
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

          <div className="workflows-table-head" aria-hidden="true">
            <div className="workflow-col workflow-col-main">Workflow</div>
            <div className="workflow-col workflow-col-model">Main model</div>
            <div className="workflow-col workflow-col-description">Description</div>
            <div className="workflow-col workflow-col-category">Category</div>
            <div className="workflow-col workflow-col-opened">Last opened</div>
            <div className="workflow-col workflow-col-tags">Tags</div>
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
                  menuOpen={menuOpenFor === workflow.id}
                  onOpen={() => onOpenWorkflow(workflow.id)}
                  onDetails={() => void openDetails(workflow)}
                  onToggleMenu={() => setMenuOpenFor((current) => (current === workflow.id ? null : workflow.id))}
                  onCloseMenu={() => setMenuOpenFor(null)}
                  onEditDashboard={() => void handleEditDashboard(workflow)}
                  onEditWidgets={() => void handleEditWidgets(workflow)}
                  onRemove={() => void handleRemove(workflow)}
                />
              ))}
            </div>
          )}
        </div>

        {selectedSummary ? (
          <aside
            className={`workflow-detail-drawer${detailsPanelOpen ? " workflow-detail-drawer--open" : ""}`}
            aria-label={`Details for ${selectedSummary.name}`}
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
              />
            ) : null}
          </aside>
        ) : null}
      </div>
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

  return (
    <>
      <div className="detail-panel__header">
        <div className="detail-panel__title-group">
          <div className="model-type-icon model-type-icon--lg" aria-hidden="true">
            <Icon size={20} />
          </div>
          <div className="detail-panel__title-text">
            <h2 className="detail-panel__title">{workflow.name}</h2>
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

function WorkflowRow({
  workflow,
  selected,
  menuOpen,
  onOpen,
  onDetails,
  onToggleMenu,
  onCloseMenu,
  onEditDashboard,
  onEditWidgets,
  onRemove,
}: {
  workflow: WorkflowSummary;
  selected: boolean;
  menuOpen: boolean;
  onOpen: () => void;
  onDetails: () => void;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onEditDashboard: () => void;
  onEditWidgets: () => void;
  onRemove: () => void;
}) {
  const Icon = WORKFLOW_ICONS[(workflow.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? Sparkles;
  const tags = workflow.tags ?? [];
  return (
    <article
      className={`workflow-row${selected ? " workflow-row--selected" : ""}`}
      role="listitem"
      onClick={onDetails}
    >
      <div className="workflow-col workflow-col-main">
        <div className="model-type-icon" aria-hidden="true">
          <Icon size={16} />
        </div>
        <div className="model-main-body">
          <div className="model-name-text" title={workflow.name}>{workflow.name}</div>
          <div className="model-type-text" title={workflow.source_label ?? "Native Noofy"}>{workflow.source_label ?? "Native Noofy"}</div>
        </div>
      </div>
      <div className="workflow-col workflow-col-model" title={workflow.main_model?.name ?? "No model detected"}>
        {workflow.main_model?.name ?? "No model detected"}
      </div>
      <div className="workflow-col workflow-col-description" title={workflow.description || "No description yet"}>
        {workflow.description || "No description yet"}
      </div>
      <div className="workflow-col workflow-col-category">
        <span className="workflow-category-badge" title={workflow.category ?? "Txt2img"}>{workflow.category ?? "Txt2img"}</span>
      </div>
      <div className="workflow-col workflow-col-opened" title={formatDate(workflow.last_opened)}>{formatDate(workflow.last_opened)}</div>
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
}: {
  workflow: WorkflowDetails;
  onClose: () => void;
  onOpen: () => void;
  onSave: (payload: WorkflowMetadataUpdate) => Promise<void> | void;
}) {
  const workflowMetadataDraft = useMemo<WorkflowMetadataUpdate>(() => ({
    description: workflow.overview.description,
    author: workflow.overview.author,
    website: workflow.overview.website,
    category: workflow.organization.category,
    tags: workflow.organization.tags,
    icon: workflow.organization.icon,
  }), [workflow]);
  const [draft, setDraft] = useState<WorkflowMetadataUpdate>(() => workflowMetadataDraft);
  const [savedDraft, setSavedDraft] = useState<WorkflowMetadataUpdate>(() => workflowMetadataDraft);
  const [saveError, setSaveError] = useState<string | null>(null);
  const Icon = WORKFLOW_ICONS[(workflow.organization.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? Sparkles;

  useEffect(() => {
    setDraft(workflowMetadataDraft);
    setSavedDraft(workflowMetadataDraft);
    setSaveError(null);
  }, [workflowMetadataDraft]);

  async function saveDraft() {
    if (metadataDraftsEqual(draft, savedDraft)) return true;
    setSaveError(null);
    try {
      await onSave(draft);
      setSavedDraft(draft);
      return true;
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }

  function handleExportClick(event: MouseEvent<HTMLAnchorElement>, url: string, defaultFilename: string) {
    const needsNativeExport = isNativeWorkflowExportAvailable();
    if (metadataDraftsEqual(draft, savedDraft) && !needsNativeExport) return;
    event.preventDefault();
    void saveDraft().then((saved) => {
      if (!saved) return;
      if (needsNativeExport) {
        void saveWorkflowExportToNativeFileWithAlert(url, defaultFilename);
        return;
      }
      window.location.href = url;
    });
  }

  return (
    <>
      <div className="workflow-detail-sticky-top">
        <div className="detail-panel__header">
          <div className="detail-panel__title-group">
            <div className="model-type-icon model-type-icon--lg" aria-hidden="true">
              <Icon size={20} />
            </div>
            <div className="detail-panel__title-text">
              <h2 className="detail-panel__title">{workflow.name}</h2>
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

      <DetailSection title="Organization">
        <EditableField
          label="Category"
          value={draft.category ?? ""}
          onBlur={() => void saveDraft()}
          onChange={(category) => setDraft((current) => ({ ...current, category }))}
        />
        <EditableField
          label="Tags"
          value={(draft.tags ?? []).join(", ")}
          onBlur={() => void saveDraft()}
          onChange={(tags) => setDraft((current) => ({ ...current, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) }))}
        />
        <EditableField
          label="Icon"
          value={draft.icon ?? ""}
          onBlur={() => void saveDraft()}
          onChange={(icon) => setDraft((current) => ({ ...current, icon }))}
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
          <a
            className="primary-button secondary-button--full"
            href={exportWorkflowUrl(workflow.id)}
            download
            onClick={(event) => handleExportClick(
              event,
              exportWorkflowUrl(workflow.id),
              workflowExportFilename(workflow.name, ".noofy"),
            )}
          >
            <Download size={15} aria-hidden="true" />
            Export .Noofy
          </a>
        ) : null}
        <a
          className="secondary-button secondary-button--full"
          href={exportWorkflowComfyJsonUrl(workflow.id)}
          download
          onClick={(event) => handleExportClick(
            event,
            exportWorkflowComfyJsonUrl(workflow.id),
            workflowExportFilename(workflow.name, ".json"),
          )}
        >
          Export ComfyUI JSON
        </a>
      </div>
    </>
  );
}

function metadataDraftsEqual(left: WorkflowMetadataUpdate, right: WorkflowMetadataUpdate) {
  return (
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
