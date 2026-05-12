import { ChangeEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  Download,
  Edit3,
  FileJson,
  FileUp,
  Image,
  Maximize2,
  MoreHorizontal,
  PackageOpen,
  Play,
  Search,
  SlidersHorizontal,
  Sparkles,
  Trash2,
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
  type WorkflowInputDef,
  type WorkflowMetadataUpdate,
  type WorkflowOutputDef,
  type WorkflowPackageResponse,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import type {
  DashboardSchema,
  DashboardWidget,
  WidgetGroup,
  WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import { useWorkflowLibrary } from "../home/WorkflowLibraryProvider";

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
  const [activeCategory, setActiveCategory] = useState<WorkflowCategory>("All");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("all");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
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
    setSelectedWorkflowId(workflow.id);
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
    setSelectedWorkflowId((current) => (current === workflow.id ? null : current));
    setDetails((current) => {
      const next = { ...current };
      delete next[workflow.id];
      return next;
    });
    await workflowLibrary.refreshWorkflows();
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
    <AppLayout activeRoute="workflows" status={runtimeStatus.statusView} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="workflows-title">
        <div>
          <h1 id="workflows-title">Workflows</h1>
          <p>Manage native and imported workflows you can run in Noofy.</p>
        </div>
        <div className="button-row">
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

      <div className="models-toolbar">
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

      <div className={`workflows-layout${selectedWorkflowId ? " workflows-layout--drawer-open" : ""}`}>
        <div className="workflows-list-area">
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
          <aside className="workflow-detail-drawer" aria-label={`Details for ${selectedSummary.name}`}>
            {detailsLoading && !selectedDetails ? (
              <div className="workflow-detail-drawer__loading">Loading details...</div>
            ) : detailsError ? (
              <div className="workflow-detail-drawer__loading">{detailsError}</div>
            ) : selectedDetails ? (
              <WorkflowDetailsDrawer
                workflow={selectedDetails}
                onClose={() => setSelectedWorkflowId(null)}
                onOpen={() => onOpenWorkflow(selectedDetails.id)}
                onSave={(payload) => void handleMetadataSave(selectedDetails.id, payload)}
              />
            ) : null}
          </aside>
        ) : null}
      </div>
    </AppLayout>
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
          <div className="model-name-text">{workflow.name}</div>
          <div className="model-type-text">{workflow.source_label ?? "Native Noofy"}</div>
        </div>
      </div>
      <div className="workflow-col workflow-col-model">{workflow.main_model?.name ?? "No model detected"}</div>
      <div className="workflow-col workflow-col-description">{workflow.description || "No description yet"}</div>
      <div className="workflow-col workflow-col-category">
        <span className="workflow-category-badge">{workflow.category ?? "Txt2img"}</span>
      </div>
      <div className="workflow-col workflow-col-opened">{formatDate(workflow.last_opened)}</div>
      <div className="workflow-col workflow-col-tags">
        <div className="model-tags-row">
          {tags.slice(0, 2).map((tag) => (
            <span key={tag} className="tag-pill tag-pill--more">
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
        <div className="workflow-action-menu">
          <button
            className="icon-button"
            type="button"
            aria-label={`Actions for ${workflow.name}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={onToggleMenu}
          >
            <MoreHorizontal size={16} aria-hidden="true" />
          </button>
          {menuOpen ? (
            <div className="workflow-action-menu__content" role="menu">
              <button role="menuitem" type="button" onClick={onOpen}>
                <Play size={14} aria-hidden="true" />
                Open
              </button>
              <button role="menuitem" type="button" onClick={onDetails}>
                <PackageOpen size={14} aria-hidden="true" />
                View details
              </button>
              <button role="menuitem" type="button" onClick={onEditDashboard}>
                <Edit3 size={14} aria-hidden="true" />
                Edit dashboard
              </button>
              <button role="menuitem" type="button" onClick={onEditWidgets}>
                <SlidersHorizontal size={14} aria-hidden="true" />
                Edit Widgets
              </button>
              {workflow.can_export_noofy ? (
                <a role="menuitem" href={exportWorkflowUrl(workflow.id)} download onClick={onCloseMenu}>
                  <Download size={14} aria-hidden="true" />
                  Export as .noofy
                </a>
              ) : null}
              <a role="menuitem" href={exportWorkflowComfyJsonUrl(workflow.id)} download onClick={onCloseMenu}>
                <FileJson size={14} aria-hidden="true" />
                Export ComfyUI JSON
              </a>
              {workflow.can_remove ? (
                <button className="workflow-action-menu__danger" role="menuitem" type="button" onClick={onRemove}>
                  <Trash2 size={14} aria-hidden="true" />
                  Remove workflow
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
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
  onSave: (payload: WorkflowMetadataUpdate) => void;
}) {
  const [draft, setDraft] = useState<WorkflowMetadataUpdate>(() => ({
    description: workflow.overview.description,
    author: workflow.overview.author,
    website: workflow.overview.website,
    category: workflow.organization.category,
    tags: workflow.organization.tags,
    icon: workflow.organization.icon,
  }));
  const Icon = WORKFLOW_ICONS[(workflow.organization.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? Sparkles;

  useEffect(() => {
    setDraft({
      description: workflow.overview.description,
      author: workflow.overview.author,
      website: workflow.overview.website,
      category: workflow.organization.category,
      tags: workflow.organization.tags,
      icon: workflow.organization.icon,
    });
  }, [workflow]);

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

      <button className="primary-button secondary-button--full workflow-open-cta" type="button" onClick={onOpen}>
        <Play size={15} aria-hidden="true" />
        Open Workflow
      </button>

      <DetailSection title="Overview">
        <EditableField label="Description" value={draft.description ?? ""} multiline onChange={(description) => setDraft((current) => ({ ...current, description }))} />
        <EditableField label="Author" value={draft.author ?? ""} onChange={(author) => setDraft((current) => ({ ...current, author }))} />
        <EditableField label="Website" value={draft.website ?? ""} onChange={(website) => setDraft((current) => ({ ...current, website }))} />
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
        <EditableField label="Category" value={draft.category ?? ""} onChange={(category) => setDraft((current) => ({ ...current, category }))} />
        <EditableField
          label="Tags"
          value={(draft.tags ?? []).join(", ")}
          onChange={(tags) => setDraft((current) => ({ ...current, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) }))}
        />
        <EditableField label="Icon" value={draft.icon ?? ""} onChange={(icon) => setDraft((current) => ({ ...current, icon }))} />
        <button className="secondary-button secondary-button--full" type="button" onClick={() => onSave(draft)}>
          Save details
        </button>
      </DetailSection>

      <DetailSection title="Advanced">
        <dl className="detail-list detail-list--compact">
          <div><dt>Package ID</dt><dd>{workflow.advanced.package_id}</dd></div>
          <div><dt>Engine</dt><dd>{workflow.advanced.engine}</dd></div>
          <div><dt>Trust level</dt><dd>{workflow.advanced.trust_label}</dd></div>
        </dl>
        <div className="detail-panel__actions">
          {workflow.advanced.can_export_noofy ? (
            <a className="secondary-button secondary-button--full" href={exportWorkflowUrl(workflow.id)} download>Export as .noofy</a>
          ) : null}
          <a className="secondary-button secondary-button--full" href={exportWorkflowComfyJsonUrl(workflow.id)} download>Export ComfyUI JSON</a>
        </div>
      </DetailSection>
    </>
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
  onChange,
}: {
  label: string;
  value: string;
  multiline?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="workflow-edit-field">
      <span>{label}</span>
      {multiline ? (
        <textarea value={value} onChange={(event) => onChange(event.target.value)} rows={3} />
      ) : (
        <input value={value} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

function buildDashboardSchemaForEditing(packageData: WorkflowPackageResponse): DashboardSchema {
  const inputIndex = new Map<string, WorkflowInputDef>();
  for (const input of packageData.inputs) inputIndex.set(input.id, input);
  const outputIndex = new Map<string, WorkflowOutputDef>();
  for (const output of packageData.outputs) outputIndex.set(output.id, output);

  const widgets: DashboardWidget[] = [];
  for (const section of packageData.dashboard.sections) {
    for (const control of section.controls) {
      const layout = control.layout
        ? {
            x: control.layout.x,
            y: control.layout.y,
            w: control.layout.w,
            h: control.layout.h,
            minW: control.layout.min_w,
            minH: control.layout.min_h,
          }
        : undefined;

      if (control.input_id) {
        const input = inputIndex.get(control.input_id);
        if (!input) continue;
        widgets.push({
          id: control.id,
          valueId: input.id,
          binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
          widgetType: toBuilderWidgetType(control.type),
          title: control.label,
          description: control.description ?? "",
          orientation: "vertical",
          group: toBuilderWidgetGroup(control.group),
          defaultValue: input.default,
          min: numberValidation(input.validation.min),
          max: numberValidation(input.validation.max),
          step: numberValidation(input.validation.step),
          options: stringArrayValidation(input.validation.options),
          layout,
        });
      } else if (control.output_id) {
        const output = outputIndex.get(control.output_id);
        if (!output) continue;
        widgets.push({
          id: control.id,
          valueId: output.id,
          binding: { nodeId: output.node_id, inputName: "" },
          widgetType: "display_image",
          title: control.label,
          description: control.description ?? "",
          orientation: "vertical",
          group: toBuilderWidgetGroup(control.group),
          defaultValue: null,
          showDownload: Boolean(control.show_download),
          layout,
        });
      }
    }
  }

  return {
    version: 1,
    workflowId: packageData.metadata.id,
    workflowName: packageData.metadata.name,
    widgets,
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
  };
}

function toBuilderWidgetType(type: string): WidgetType {
  if (type === "result_image") return "display_image";
  const knownTypes = new Set<WidgetType>([
    "slider",
    "int_field",
    "string_field",
    "textarea",
    "toggle",
    "load_image",
    "load_image_mask",
    "display_image",
    "seed_widget",
    "lora_loader",
    "select",
  ]);
  return knownTypes.has(type as WidgetType) ? (type as WidgetType) : "string_field";
}

function toBuilderWidgetGroup(group: string | undefined): WidgetGroup {
  return group === "advanced" ? "advanced" : "simple";
}

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : undefined;
}
