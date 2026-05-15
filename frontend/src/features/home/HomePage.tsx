import { type ChangeEvent, type KeyboardEvent, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Download,
  FileUp,
  Loader2,
  PackagePlus,
  Plus,
  Search,
  Users,
  X,
} from "lucide-react";
import { openExternalUrl } from "../../lib/openExternalUrl";

// Replace with your real Reddit community URL when ready.
const REDDIT_URL = "https://www.reddit.com/r/noofy";

import {
  fetchWorkflows,
  cancelImportModelDownload,
  cancelWorkflowImport,
  commitWorkflowImport,
  downloadImportMissingModels,
  fetchImportModelDownloadStatus,
  fetchImportModelVerificationStatus,
  fetchWorkflowPackage,
  previewWorkflowPackageImport,
  removeWorkflow,
  type ImportModelDownloadJobStatus,
  type ImportModelVerificationJobStatus,
  type RequiredModelAvailability,
  type WorkflowImportResponse,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import type { DashboardSchema } from "../dashboard-builder/dashboardBuilderContent";
import { buildDashboardSchemaForEditing } from "../workflows/dashboardEditing";
import { WorkflowActionMenu } from "../workflows/WorkflowActionMenu";
import { searchWorkflows, workflowStatusLabel as workflowSearchStatusLabel } from "../workflows/workflowSearch";
import {
  fallbackWorkflow,
  recentWorkflows,
  starterWorkflows,
  type WorkflowCard,
  type WorkflowCardVariant,
  type WorkflowStatus,
} from "./homeContent";
import { useWorkflowLibrary } from "./WorkflowLibraryProvider";

interface HomeDataState {
  importing: boolean;
  downloadingModels: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  verificationJob: ImportModelVerificationJobStatus | null;
  pendingImport: WorkflowImportResponse | null;
  allowCommunityPreparation: true;
  importResult: WorkflowImportResponse | null;
  importError: string | null;
}

const initialHomeState: HomeDataState = {
  importing: false,
  downloadingModels: false,
  downloadJob: null,
  verificationJob: null,
  pendingImport: null,
  allowCommunityPreparation: true,
  importResult: null,
  importError: null,
};

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debounced;
}

function friendlyDescription(workflow: WorkflowSummary) {
  if (workflow.id === "text_to_image_v0") {
    return "Generate a new image from a simple text prompt.";
  }

  return workflow.description.replace(/^Milestone \d+\s*/i, "");
}

function workflowIconStatus(status: WorkflowStatus) {
  if (status === "installed" || status === "ready") {
    return CheckCircle2;
  }

  if (status === "download") {
    return Download;
  }

  return AlertCircle;
}

function workflowCardsFromBackend(workflows: WorkflowSummary[]): WorkflowCard[] {
  return workflows.map((workflow) => {
    const status = workflowStatusFromSummary(workflow);

    return {
      id: workflow.id,
      title: workflow.name,
      description: friendlyDescription(workflow),
      category: workflow.trust_level === "quarantined_community" ? "Imported" : "Installed",
      status,
      statusLabel:
        workflow.status === "imported"
          ? workflowStatusLabel(status)
          : workflow.status_label ?? workflowStatusLabel(status),
      trustLabel: workflow.trust?.label ?? trustLevelLabel(workflow.trust_level),
      trustTone: workflow.trust?.badge_tone ?? trustLevelTone(workflow.trust_level),
      trustSummary: workflow.trust?.summary,
      canRemove: Boolean(workflow.can_remove),
      canExportNoofy: Boolean(workflow.can_export_noofy),
      canExportComfyJson: workflow.can_export_comfyui_json !== false,
      Icon: fallbackWorkflow.Icon,
      source: "backend",
    };
  });
}

type NativeHomeWorkflowKind = "text_to_image" | "image_to_image";

interface NativeHomeWorkflowGroup {
  id: NativeHomeWorkflowKind;
  title: "Text to Image" | "Image to Image";
  description: string;
}

const nativeHomeWorkflowGroups: Record<NativeHomeWorkflowKind, NativeHomeWorkflowGroup> = {
  text_to_image: {
    id: "text_to_image",
    title: "Text to Image",
    description: "Generate a new image from a simple text prompt.",
  },
  image_to_image: {
    id: "image_to_image",
    title: "Image to Image",
    description: "Use a reference image to guide a new generation.",
  },
};

function homeWorkflowCardsFromBackend(
  workflows: WorkflowSummary[],
  selectedVariants: Record<string, string | undefined>,
): WorkflowCard[] {
  const grouped = new Map<NativeHomeWorkflowKind, WorkflowSummary[]>();
  const ungrouped: WorkflowSummary[] = [];

  for (const workflow of workflows) {
    const groupKind = nativeHomeWorkflowKind(workflow);
    if (groupKind) {
      grouped.set(groupKind, [...(grouped.get(groupKind) ?? []), workflow]);
    } else {
      ungrouped.push(workflow);
    }
  }

  const cards: WorkflowCard[] = [];
  for (const kind of Object.keys(nativeHomeWorkflowGroups) as NativeHomeWorkflowKind[]) {
    const variants = grouped.get(kind);
    if (!variants?.length) continue;

    const selectedId = selectedVariants[kind];
    const selectedWorkflow = variants.find((variant) => variant.id === selectedId) ?? variants[0];
    const selectedCard = workflowCardsFromBackend([selectedWorkflow])[0];
    const group = nativeHomeWorkflowGroups[kind];

    cards.push({
      ...selectedCard,
      title: group.title,
      description: group.description,
      category: "Image Generation",
      variants: variants.map((variant) => workflowCardVariant(variant, group)),
    });
  }

  return [...cards, ...workflowCardsFromBackend(ungrouped)];
}

function nativeHomeWorkflowKind(workflow: WorkflowSummary): NativeHomeWorkflowKind | null {
  if (!isNativeBundledWorkflow(workflow)) return null;

  const normalizedName = normalizeWorkflowName(workflow.name);
  const category = workflow.category?.toLowerCase();
  if (matchesNativeWorkflowName(normalizedName, "text to image") || category === "txt2img") {
    return "text_to_image";
  }
  if (matchesNativeWorkflowName(normalizedName, "image to image") || category === "img2img") {
    return "image_to_image";
  }
  return null;
}

function isNativeBundledWorkflow(workflow: WorkflowSummary) {
  if (workflow.source_label === "Imported" || workflow.trust_level === "quarantined_community" || workflow.can_remove) {
    return false;
  }
  if (workflow.status === "imported") return false;
  return workflow.source_label === undefined || workflow.source_label === "Native Noofy";
}

function normalizeWorkflowName(value: string) {
  return value
    .replace(/[\u2014\u2013]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function matchesNativeWorkflowName(normalizedName: string, baseName: string) {
  return normalizedName === baseName || normalizedName.startsWith(`${baseName} - `) || normalizedName.startsWith(`${baseName}: `);
}

function workflowCardVariant(workflow: WorkflowSummary, group: NativeHomeWorkflowGroup): WorkflowCardVariant {
  const rawLabel = workflow.name
    .replace(new RegExp(`^${group.title.replace(/\s+/g, "\\s+")}`, "i"), "")
    .replace(/^[\s:\u2014\u2013-]+/, "")
    .trim();
  const modelLabel = workflow.main_model?.name && workflow.main_model.name !== "No model detected"
    ? workflow.main_model.name.replace(/\.(safetensors|ckpt|gguf|pt|pth)$/i, "")
    : null;

  return {
    id: workflow.id,
    label: rawLabel || modelLabel || workflow.name,
    title: workflow.name,
  };
}

function workflowStatusFromSummary(workflow: WorkflowSummary): WorkflowStatus {
  if (workflow.status === "needs_input_setup") {
    return "needs_input_setup";
  }

  if (workflow.status === "cannot_prepare_automatically") {
    return "cannot_prepare_automatically";
  }

  return "installed";
}

function workflowStatusLabel(status: WorkflowStatus) {
  if (status === "needs_input_setup") {
    return "Needs input setup";
  }

  if (status === "cannot_prepare_automatically") {
    return "Cannot prepare";
  }

  if (status === "imported") {
    return "Imported";
  }

  return "Installed";
}

function trustLevelLabel(level?: string) {
  if (level === "registry_locked") {
    return "Registry Locked";
  }
  if (level === "quarantined_community") {
    return "Community";
  }
  if (level === "unsupported") {
    return "Unsupported";
  }
  return "Noofy Verified";
}

function trustLevelTone(level?: string) {
  if (level === "registry_locked") {
    return "locked";
  }
  if (level === "quarantined_community") {
    return "community";
  }
  if (level === "unsupported") {
    return "unsupported";
  }
  return "verified";
}

interface HomePageProps {
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  onEditWidgets?: (schema: DashboardSchema) => void;
  onEditDashboard?: (schema: DashboardSchema) => void;
  onNavigate: (route: AppRouteId, options?: { workflowSearch?: string }) => void;
}

export function HomePage({
  onOpenWorkflow,
  onConfigureDashboard,
  onEditWidgets,
  onEditDashboard,
  onNavigate,
}: HomePageProps) {
  const [homeData, setHomeData] = useState<HomeDataState>(initialHomeState);
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
  const [cardActionError, setCardActionError] = useState<string | null>(null);
  const [selectedNativeVariants, setSelectedNativeVariants] = useState<Record<string, string | undefined>>({});
  const [homeSearch, setHomeSearch] = useState("");
  const [searchDropdownOpen, setSearchDropdownOpen] = useState(false);
  const [highlightedSearchIndex, setHighlightedSearchIndex] = useState(-1);
  const debouncedHomeSearch = useDebouncedValue(homeSearch, 160);
  const runtimeStatus = useRuntimeStatus();
  const workflowLibrary = useWorkflowLibrary();
  const { refreshRuntime } = runtimeStatus;
  const { refreshWorkflows, setWorkflowsFromResponse } = workflowLibrary;

  useEffect(() => {
    void refreshRuntime({ silent: true });
    void refreshWorkflows();
  }, [refreshRuntime, refreshWorkflows]);

  const status = runtimeStatus.statusView;

  const workflowCards = useMemo(() => {
    const backendCards = homeWorkflowCardsFromBackend(workflowLibrary.workflows, selectedNativeVariants);
    const fallbackCards = backendCards.length > 0 ? backendCards : [fallbackWorkflow];
    const starterWithoutDuplicates = starterWorkflows.filter(
      (starter) => !fallbackCards.some((card) => card.id === starter.id || card.title === starter.title),
    );

    return [...fallbackCards, ...starterWithoutDuplicates].slice(0, 8);
  }, [selectedNativeVariants, workflowLibrary.workflows]);

  const installedCount = workflowLibrary.workflows.length;
  const homeSearchResults = useMemo(
    () =>
      debouncedHomeSearch.trim()
        ? searchWorkflows(workflowLibrary.workflows, { query: debouncedHomeSearch }).slice(0, 6)
        : [],
    [debouncedHomeSearch, workflowLibrary.workflows],
  );
  const searchQueryActive = debouncedHomeSearch.trim().length > 0;
  const showSearchDropdown = searchDropdownOpen && searchQueryActive;
  const homeWarning =
    workflowLibrary.error
      ? {
          title: "Workflow library could not refresh",
          message: "Noofy is keeping your last loaded workflows visible while it retries in the background.",
        }
      : runtimeStatus.backendStatus === "unreachable"
        ? {
            title: "Backend is not reachable",
            message: "The page is keeping the last loaded workflows visible until the local backend returns.",
          }
        : null;

  useEffect(() => {
    setHighlightedSearchIndex(-1);
  }, [homeSearchResults]);

  function navigateToWorkflowSearch() {
    setSearchDropdownOpen(false);
    onNavigate("workflows", { workflowSearch: homeSearch.trim() });
  }

  function openSearchResult(workflow: WorkflowSummary) {
    setSearchDropdownOpen(false);
    onOpenWorkflow(workflow.id);
  }

  function handleHomeSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setSearchDropdownOpen(false);
      setHighlightedSearchIndex(-1);
      return;
    }

    if (event.key === "ArrowDown" && showSearchDropdown && homeSearchResults.length > 0) {
      event.preventDefault();
      setHighlightedSearchIndex((current) => (current < 0 ? 0 : (current + 1) % homeSearchResults.length));
      return;
    }

    if (event.key === "ArrowUp" && showSearchDropdown && homeSearchResults.length > 0) {
      event.preventDefault();
      setHighlightedSearchIndex((current) =>
        current < 0 ? homeSearchResults.length - 1 : (current - 1 + homeSearchResults.length) % homeSearchResults.length,
      );
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      const highlightedWorkflow = showSearchDropdown ? homeSearchResults[highlightedSearchIndex] : undefined;
      if (highlightedWorkflow) {
        openSearchResult(highlightedWorkflow);
        return;
      }
      navigateToWorkflowSearch();
    }
  }

  async function handleWorkflowFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) {
      return;
    }

    setHomeData((current) => ({
      ...current,
      importing: true,
      downloadingModels: false,
      downloadJob: null,
      verificationJob: null,
      pendingImport: null,
      importResult: null,
      importError: null,
    }));

    try {
      const importResult = await previewWorkflowPackageImport(file, homeData.allowCommunityPreparation);
      if (importResult.import_session_id && importResult.model_summary && importResult.model_summary.total_count > 0) {
        setHomeData((current) => ({
          ...current,
          importing: false,
          verificationJob: null,
          pendingImport: importResult,
          importResult: null,
          importError: null,
        }));
        return;
      }
      const workflows = await fetchWorkflows();
      setWorkflowsFromResponse(workflows);
      setHomeData((current) => ({
        ...current,
        importing: false,
        pendingImport: null,
        verificationJob: null,
        importResult,
        importError: null,
      }));
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        importing: false,
        pendingImport: null,
        verificationJob: null,
        importResult: null,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleDownloadMissingModels() {
    const sessionId = homeData.pendingImport?.import_session_id;
    if (!sessionId) return;
    setHomeData((current) => ({ ...current, downloadingModels: true, downloadJob: null, importError: null }));
    try {
      const job = await downloadImportMissingModels(sessionId);
      setHomeData((current) => ({
        ...current,
        downloadingModels: true,
        downloadJob: {
          ...job,
          current_model_filename: null,
          current_model_index: null,
          total_models: current.pendingImport?.model_summary?.missing_count ?? 0,
          bytes_downloaded: null,
          total_bytes: null,
          percent: null,
          speed_bytes_per_second: null,
          models: [],
          model_summary: current.pendingImport?.model_summary ?? null,
        },
        importError: null,
      }));
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        downloadingModels: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleCancelModelDownload() {
    const sessionId = homeData.pendingImport?.import_session_id;
    const jobId = homeData.downloadJob?.job_id;
    if (!sessionId || !jobId) return;
    try {
      const status = await cancelImportModelDownload(sessionId, jobId);
      setHomeData((current) => ({
        ...current,
        downloadingModels: status.status === "queued" || status.status === "running",
        downloadJob: status,
        importError: status.user_facing_message,
      }));
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleContinueImport() {
    const sessionId = homeData.pendingImport?.import_session_id;
    if (!sessionId) return;
    setHomeData((current) => ({ ...current, importing: true, importError: null }));
    try {
      const importResult = await commitWorkflowImport(sessionId);
      const workflows = await fetchWorkflows();
      setWorkflowsFromResponse(workflows);
      setHomeData((current) => ({
        ...current,
        importing: false,
        pendingImport: null,
        verificationJob: null,
        importResult,
        importError: null,
      }));
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleReadyImportAction() {
    const sessionId = homeData.pendingImport?.import_session_id;
    if (!sessionId) return;
    setHomeData((current) => ({ ...current, importing: true, downloadingModels: false, importError: null }));
    try {
      const importResult = await commitWorkflowImport(sessionId);
      const workflows = await fetchWorkflows();
      setWorkflowsFromResponse(workflows);
      setHomeData((current) => ({
        ...current,
        importing: false,
        downloadingModels: false,
        pendingImport: null,
        downloadJob: null,
        verificationJob: null,
        importResult,
        importError: null,
      }));
      if (importResult.status === "needs_input_setup" && onConfigureDashboard) {
        onConfigureDashboard(importResult.workflow.id, importResult.workflow.name);
        return;
      }
      onOpenWorkflow(importResult.workflow.id);
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleCancelImport() {
    const sessionId = homeData.pendingImport?.import_session_id;
    if (sessionId) {
      try {
        await cancelWorkflowImport(sessionId);
      } catch {
        // The pending import is in-memory; if the backend already forgot it, the UI can still close.
      }
    }
    setHomeData((current) => ({ ...current, pendingImport: null, downloadJob: null, verificationJob: null, importError: null }));
  }

  async function handleRemoveWorkflowCard(workflow: WorkflowCard) {
    const workflowId = activeWorkflowId(workflow);
    const workflowTitle = activeWorkflowTitle(workflow);
    if (!workflow.canRemove) return;
    const confirmed = window.confirm(`Remove "${workflowTitle}" from Noofy?`);
    if (!confirmed) return;
    setMenuOpenFor(null);
    setCardActionError(null);
    try {
      await removeWorkflow(workflowId);
      await refreshWorkflows();
    } catch (error) {
      setCardActionError(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleEditWorkflowCard(workflow: WorkflowCard, open?: (schema: DashboardSchema) => void) {
    const workflowId = activeWorkflowId(workflow);
    const workflowTitle = activeWorkflowTitle(workflow);
    setMenuOpenFor(null);
    setCardActionError(null);
    if (!open) {
      onConfigureDashboard?.(workflowId, workflowTitle);
      return;
    }
    try {
      const packageData = await fetchWorkflowPackage(workflowId);
      open(buildDashboardSchemaForEditing(packageData));
    } catch (error) {
      setCardActionError(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    const sessionId = homeData.pendingImport?.import_session_id;
    const verifying =
      homeData.verificationJob?.status === "queued" ||
      homeData.verificationJob?.status === "running" ||
      homeData.pendingImport?.model_summary?.models.some((model) => model.status === "checking");
    if (!sessionId || !verifying) return;

    let stopped = false;
    let inFlight = false;
    let interval: number | null = null;
    const stopPolling = () => {
      stopped = true;
      if (interval !== null) {
        window.clearInterval(interval);
      }
    };
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        const status = await fetchImportModelVerificationStatus(sessionId);
        if (stopped) return;
        const finished = ["completed", "failed"].includes(status.status);
        setHomeData((current) => ({
          ...current,
          verificationJob: status,
          pendingImport:
            status.model_summary && current.pendingImport
              ? { ...current.pendingImport, model_summary: status.model_summary }
              : current.pendingImport,
          importError: finished && status.status !== "completed" ? status.user_facing_message : current.importError,
        }));
        if (finished) stopPolling();
      } catch (error) {
        if (stopped) return;
        stopPolling();
        setHomeData((current) => ({
          ...current,
          importError: error instanceof Error ? error.message : String(error),
        }));
      } finally {
        inFlight = false;
      }
    };

    void poll();
    interval = window.setInterval(() => void poll(), 800);
    return stopPolling;
  }, [
    homeData.pendingImport?.import_session_id,
    homeData.pendingImport?.model_summary,
    homeData.verificationJob?.status,
  ]);

  useEffect(() => {
    const sessionId = homeData.pendingImport?.import_session_id;
    const jobId = homeData.downloadJob?.job_id;
    const active = homeData.downloadJob?.status === "queued" || homeData.downloadJob?.status === "running";
    if (!sessionId || !jobId || !active) return;

    let stopped = false;
    let inFlight = false;
    let interval: number | null = null;
    const stopPolling = () => {
      stopped = true;
      if (interval !== null) {
        window.clearInterval(interval);
      }
    };
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        const status = await fetchImportModelDownloadStatus(sessionId, jobId);
        if (stopped) return;
        const finished = ["completed", "failed", "canceled"].includes(status.status);
        setHomeData((current) => ({
          ...current,
          downloadingModels: !finished,
          downloadJob: status,
          pendingImport:
            status.model_summary && current.pendingImport
              ? { ...current.pendingImport, model_summary: status.model_summary }
              : current.pendingImport,
          importError: finished && status.status !== "completed" ? status.user_facing_message : current.importError,
        }));
        if (finished) stopPolling();
      } catch (error) {
        if (stopped) return;
        stopPolling();
        setHomeData((current) => ({
          ...current,
          downloadingModels: false,
          importError: error instanceof Error ? error.message : String(error),
        }));
      } finally {
        inFlight = false;
      }
    };

    void poll();
    interval = window.setInterval(() => void poll(), 1000);
    return stopPolling;
  }, [
    homeData.pendingImport?.import_session_id,
    homeData.downloadJob?.job_id,
    homeData.downloadJob?.status,
  ]);

  return (
    <AppLayout activeRoute="home" status={status} onNavigate={onNavigate}>
          <section className="page-heading" aria-labelledby="home-title">
            <div>
              <p className="eyebrow">PRIVATE LOCAL AI STUDIO</p>
              <h1 id="home-title">Powerful AI workflows without the complexity</h1>
              <p>
                Noofy turns advanced image workflows into simple creative tools that run privately on your machine.
              </p>
            </div>
            <button className="primary-button" type="button">
              <Plus size={18} aria-hidden="true" />
              New Workflow
            </button>
          </section>

          {homeWarning ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>{homeWarning.title}</strong>
                <span>{homeWarning.message}</span>
              </div>
            </div>
          ) : null}

          {homeData.importResult ? (
            <div className="notice notice--row" role="status">
              <CheckCircle2 size={18} aria-hidden="true" />
              <div>
                <strong>{homeData.importResult.user_facing_message}</strong>
                <span>{homeData.importResult.workflow.name} was added to your local workflows.</span>
              </div>
              {homeData.importResult.status === "needs_input_setup" && onConfigureDashboard ? (
                <button
                  className="primary-button primary-button--compact"
                  style={{ marginLeft: "auto" }}
                  type="button"
                  onClick={() =>
                    onConfigureDashboard(
                      homeData.importResult!.workflow.id,
                      homeData.importResult!.workflow.name,
                    )
                  }
                >
                  <PackagePlus size={14} aria-hidden="true" />
                  Configure dashboard
                </button>
              ) : null}
            </div>
          ) : null}

          {homeData.importError ? (
            <div className="notice notice--error" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Workflow could not be imported</strong>
                <span>{homeData.importError}</span>
              </div>
            </div>
          ) : null}

          {cardActionError ? (
            <div className="notice notice--error" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Workflow action failed</strong>
                <span>{cardActionError}</span>
              </div>
            </div>
          ) : null}

          {homeData.pendingImport?.model_summary ? (
            <RequiredModelsModal
              importResult={homeData.pendingImport}
              busy={homeData.importing || homeData.downloadingModels}
              importing={homeData.importing}
              downloadJob={homeData.downloadJob}
              verificationJob={homeData.verificationJob}
              onDownload={() => void handleDownloadMissingModels()}
              onCancelDownload={() => void handleCancelModelDownload()}
              onContinue={() => void handleContinueImport()}
              onReadyAction={() => void handleReadyImportAction()}
              onCancel={() => void handleCancelImport()}
            />
          ) : null}

          <section className="action-grid" aria-label="Workflow actions">
            <article className="action-card">
              <div className="action-card__icon">
                <FileUp size={26} aria-hidden="true" />
              </div>
              <div>
                <h2>Open Workflow File</h2>
                <p>Choose a saved workflow package and run it through Noofy.</p>
              </div>
              <label className="secondary-button action-card__button">
                <input
                  className="sr-only"
                  type="file"
                  accept=".noofy"
                  disabled={homeData.importing}
                  onChange={(event) => void handleWorkflowFileSelected(event)}
                />
                <FileUp size={16} aria-hidden="true" />
                {homeData.importing ? "Importing..." : "Choose File"}
              </label>
            </article>

            <article className="action-card action-card--accent">
              <div className="action-card__icon action-card__icon--accent">
                <Users size={26} aria-hidden="true" />
              </div>
              <div>
                <h2>Join the Reddit Community</h2>
                <p>Share workflows, ask questions, and follow Noofy's progress with other local AI builders.</p>
              </div>
              <button
                className="primary-button primary-button--compact"
                type="button"
                onClick={() => void openExternalUrl(REDDIT_URL)}
              >
                <Users size={16} aria-hidden="true" />
                Open Reddit
              </button>
            </article>
          </section>

          <section className="find-workflow-section" aria-labelledby="find-workflow-title">
            <div className="find-workflow-card">
              <div className="find-workflow-card__icon" aria-hidden="true">
                <Search size={24} />
              </div>
              <div className="find-workflow-card__body">
                <h2 id="find-workflow-title">Find a Workflow</h2>
                <p>Search by name, tag, or category.</p>
                <div className="home-workflow-search">
                  <label className="search-field find-workflow-card__input">
                    <Search size={16} aria-hidden="true" />
                    <span className="sr-only">Search workflows</span>
                    <input
                      type="search"
                      placeholder="Search workflows..."
                      value={homeSearch}
                      aria-expanded={showSearchDropdown}
                      aria-controls="home-workflow-search-results"
                      aria-activedescendant={
                        showSearchDropdown && highlightedSearchIndex >= 0
                          ? `home-workflow-search-result-${homeSearchResults[highlightedSearchIndex]?.id}`
                          : undefined
                      }
                      onChange={(event) => {
                        setHomeSearch(event.target.value);
                        setSearchDropdownOpen(event.target.value.trim().length > 0);
                      }}
                      onFocus={() => setSearchDropdownOpen(homeSearch.trim().length > 0)}
                      onKeyDown={handleHomeSearchKeyDown}
                    />
                  </label>
                  {showSearchDropdown ? (
                    <div className="home-workflow-search__panel" id="home-workflow-search-results" role="listbox">
                      {homeSearchResults.length > 0 ? (
                        homeSearchResults.map((workflow, index) => (
                          <button
                            key={workflow.id}
                            id={`home-workflow-search-result-${workflow.id}`}
                            className={
                              index === highlightedSearchIndex
                                ? "home-workflow-search__result home-workflow-search__result--active"
                                : "home-workflow-search__result"
                            }
                            type="button"
                            role="option"
                            aria-selected={index === highlightedSearchIndex}
                            onMouseEnter={() => setHighlightedSearchIndex(index)}
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => openSearchResult(workflow)}
                          >
                            <span className="home-workflow-search__result-main">
                              <span>{workflow.name}</span>
                              <small>{workflow.description || workflow.status_label || workflowSearchStatusLabel(workflow)}</small>
                            </span>
                            <span className="mini-status">{workflow.status_label ?? workflowSearchStatusLabel(workflow)}</span>
                          </button>
                        ))
                      ) : (
                        <button
                          className="home-workflow-search__empty"
                          type="button"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={navigateToWorkflowSearch}
                        >
                          Go to Workflows page
                        </button>
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </section>

          <section className="recent-section" aria-labelledby="recent-title">
            <div className="section-heading section-heading--tight">
              <div>
                <h2 id="recent-title">Recently Opened</h2>
                <p>Continue from a workflow you opened before.</p>
              </div>
              <button className="ghost-button" type="button">
                View all
                <ArrowRight size={16} aria-hidden="true" />
              </button>
            </div>

            <div className="recent-list">
              {recentWorkflows.map((recent) => (
                <article className="recent-row" key={recent.title}>
                  <div className="recent-row__icon" aria-hidden="true">
                    <recent.Icon size={20} />
                  </div>
                  <div className="recent-row__body">
                    <h3>{recent.title}</h3>
                    <p>
                      {recent.kind}
                      <span aria-hidden="true" />
                      {recent.openedAt}
                    </p>
                  </div>
                  <span className="mini-status">{recent.statusLabel}</span>
                  <button
                    className="secondary-button secondary-button--small"
                    type="button"
                    onClick={() => onOpenWorkflow("text_to_image_v0")}
                  >
                    Open
                  </button>
                </article>
              ))}
            </div>
          </section>

          <section className="section-heading" aria-labelledby="built-in-workflows-title">
            <div>
              <h2 id="built-in-workflows-title">Built-in Workflows</h2>
              <p>
                {installedCount > 0
                  ? `${installedCount} workflow${installedCount === 1 ? "" : "s"} loaded locally.`
                  : "Starter workflows will appear here as packages are added."}
              </p>
            </div>
            <button className="ghost-button" type="button">
              View all
              <ArrowRight size={16} aria-hidden="true" />
            </button>
          </section>

          <section className="workflow-grid" aria-label="Built-in workflows">
            {workflowCards.map((workflow) => (
              <WorkflowCardView
                key={workflow.id}
                workflow={workflow}
                menuOpen={menuOpenFor === workflow.id}
                onOpenWorkflow={onOpenWorkflow}
                onConfigureDashboard={onConfigureDashboard}
                onViewDetails={() => onNavigate("workflows")}
                onToggleMenu={() => setMenuOpenFor((current) => current === workflow.id ? null : workflow.id)}
                onCloseMenu={() => setMenuOpenFor(null)}
                onVariantChange={(variantId) =>
                  setSelectedNativeVariants((current) => ({ ...current, [nativeVariantSelectionKey(workflow)]: variantId }))
                }
                onEditDashboard={() => void handleEditWorkflowCard(workflow, onEditDashboard)}
                onEditWidgets={() => void handleEditWorkflowCard(workflow, onEditWidgets)}
                onRemove={() => void handleRemoveWorkflowCard(workflow)}
              />
            ))}
          </section>
    </AppLayout>
  );
}

function activeWorkflowId(workflow: WorkflowCard) {
  return workflow.id;
}

function activeWorkflowTitle(workflow: WorkflowCard) {
  return workflow.variants?.find((variant) => variant.id === workflow.id)?.title ?? workflow.title;
}

function nativeVariantSelectionKey(workflow: WorkflowCard) {
  if (workflow.title === "Text to Image") return "text_to_image";
  if (workflow.title === "Image to Image") return "image_to_image";
  return workflow.id;
}

function RequiredModelsModal({
  importResult,
  busy,
  importing,
  downloadJob,
  verificationJob,
  onDownload,
  onCancelDownload,
  onContinue,
  onReadyAction,
  onCancel,
}: {
  importResult: WorkflowImportResponse;
  busy: boolean;
  importing: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  verificationJob: ImportModelVerificationJobStatus | null;
  onDownload: () => void;
  onCancelDownload: () => void;
  onContinue: () => void;
  onReadyAction: () => void;
  onCancel: () => void;
}) {
  const summary = importResult.model_summary;
  if (!summary) return null;
  const retryableStatuses = new Set([
    "missing",
    "download_failed",
    "authentication_required",
    "rate_limited",
    "hash_mismatch",
    "not_enough_disk_space",
  ]);
  const hasDownloadable = summary.models.some((model) => retryableStatuses.has(model.status));
  const activeDownload = downloadJob?.status === "queued" || downloadJob?.status === "running";
  const activeVerification =
    verificationJob?.status === "queued" ||
    verificationJob?.status === "running" ||
    summary.models.some((model) => model.status === "checking");
  const jobModels = new Map(activeDownload ? downloadJob?.models.map((model) => [model.requirement_id, model]) ?? [] : []);
  const readyToRun = summary.ready_to_run && !activeDownload && !activeVerification;
  const needsWorkflowConfiguration = importNeedsConfiguration(importResult);
  const readyActionLabel = needsWorkflowConfiguration ? "Configure Workflow" : "Open Workflow";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="required-models-title">
      <section className="required-models-modal" aria-busy={importing}>
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Workflow models</p>
            <h2 id="required-models-title">{importResult.workflow.name}</h2>
            <p>
              Noofy is checking your local models first. Missing models can be downloaded or selected before the
              workflow runs. If a download fails, Noofy cleans up the partial file safely.
            </p>
          </div>
          <button className="icon-button" type="button" aria-label="Cancel import" disabled={busy} onClick={onCancel}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="required-models-list">
          {summary.models.map((model) => (
            <RequiredModelRow key={model.requirement_id} model={model} progress={jobModels.get(model.requirement_id)} />
          ))}
        </div>

        {downloadJob && shouldShowDownloadProgress(downloadJob) ? <ModelDownloadProgressPanel job={downloadJob} /> : null}
        {activeVerification ? <ModelVerificationProgressPanel job={verificationJob} /> : null}

        {importing ? (
          <div className="required-models-modal__processing" role="status" aria-live="polite">
            <Loader2 className="spin" size={16} aria-hidden="true" />
            <span>Preparing workflow import...</span>
          </div>
        ) : null}

        <footer className={`required-models-modal__footer${readyToRun ? " required-models-modal__footer--ready" : ""}`}>
          {readyToRun ? (
            <button className="primary-button" type="button" disabled={busy} onClick={onReadyAction}>
              {importing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <ArrowRight size={16} aria-hidden="true" />}
              {importing ? "Preparing..." : readyActionLabel}
            </button>
          ) : (
            <>
              <button className="secondary-button" type="button" disabled={busy || activeVerification || !hasDownloadable} onClick={onDownload}>
                <Download size={16} aria-hidden="true" />
                {activeDownload ? "Downloading..." : "Download Missing Models"}
              </button>
              {activeDownload ? (
                <button className="secondary-button" type="button" onClick={onCancelDownload}>
                  Cancel Download
                </button>
              ) : null}
              <button className="secondary-button" type="button" disabled={busy || activeVerification} onClick={onContinue}>
                {importing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : null}
                {importing ? "Importing..." : "Continue Without Downloading"}
              </button>
              <button className="ghost-button" type="button" disabled={busy} onClick={onCancel}>
                Cancel Import
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}

function importNeedsConfiguration(importResult: WorkflowImportResponse) {
  return importResult.status === "needs_input_setup" || importResult.unresolved_input_count > 0;
}

function RequiredModelRow({
  model,
  progress,
}: {
  model: RequiredModelAvailability;
  progress?: ImportModelDownloadJobStatus["models"][number];
}) {
  const status = progress?.status ?? model.status;
  const statusLabel = progress?.status_label ?? model.status_label;
  const message = progress?.message ?? model.message;
  return (
    <article className="required-model-row">
      <div className="required-model-row__main">
        <h3>{model.filename}</h3>
        <p>
          {[model.model_type ?? "AI model", model.folder, formatModelSize(model.size_bytes)]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {message ? <span className="required-model-row__message">{message}</span> : null}
      </div>
      <div className="required-model-row__meta">
        <span className="model-identity">{model.verification_level.replace(/_/g, " + ")}</span>
        <span className={`model-status-pill model-status-pill--${status}`}>{statusLabel}</span>
        <span className="model-source">{modelSourceLabel(model)}</span>
      </div>
    </article>
  );
}

function ModelDownloadProgressPanel({ job }: { job: ImportModelDownloadJobStatus }) {
  const label = job.current_model_filename
    ? `Model ${job.current_model_index ?? 1} of ${job.total_models}: ${job.current_model_filename}`
    : job.user_facing_message;
  const rawPercent = job.percent ?? (
    job.bytes_downloaded !== null && job.total_bytes
      ? Math.round((job.bytes_downloaded / job.total_bytes) * 100)
      : null
  );
  const percent = rawPercent !== null && Number.isFinite(Number(rawPercent))
    ? Math.max(0, Math.min(Number(rawPercent), 100))
    : null;
  const percentLabel = percent !== null
    ? `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`
    : job.status;

  return (
    <div className="model-download-progress" role="status">
      <div className="model-download-progress__header">
        <strong>{label}</strong>
        <span>{percentLabel}</span>
      </div>
      {percent !== null ? (
        <div
          className="model-download-progress__bar"
          role="progressbar"
          aria-label="Model download progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div
            className="model-download-progress__bar-fill"
            style={{ width: `${percent}%` }}
          />
        </div>
      ) : null}
      <p>
        {[formatModelSize(job.bytes_downloaded), job.total_bytes ? formatModelSize(job.total_bytes) : null]
          .filter(Boolean)
          .join(" / ")}
        {job.speed_bytes_per_second ? ` · ${formatModelSpeed(job.speed_bytes_per_second)}` : ""}
      </p>
      <span>{job.user_facing_message}</span>
    </div>
  );
}

function ModelVerificationProgressPanel({ job }: { job: ImportModelVerificationJobStatus | null }) {
  const percent = job?.percent !== null && job?.percent !== undefined
    ? Math.max(0, Math.min(Number(job.percent), 100))
    : null;
  const label = job?.current_model_filename
    ? `Model ${job.current_model_index ?? 1} of ${job.total_models}: ${job.current_model_filename}`
    : "Checking local models";
  const percentLabel = percent !== null
    ? `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`
    : "Checking";

  return (
    <div className="model-download-progress" role="status">
      <div className="model-download-progress__header">
        <strong>{label}</strong>
        <span>{percentLabel}</span>
      </div>
      <div
        className="model-download-progress__bar"
        role="progressbar"
        aria-label="Model verification progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? 0}
      >
        <div
          className="model-download-progress__bar-fill"
          style={{ width: `${percent ?? 0}%` }}
        />
      </div>
      <span>{job?.user_facing_message ?? "Verifying local model files..."}</span>
    </div>
  );
}

function shouldShowDownloadProgress(job: ImportModelDownloadJobStatus) {
  if (
    job.status === "queued" ||
    job.status === "running" ||
    job.status === "completed" ||
    job.status === "failed" ||
    job.status === "canceled"
  ) {
    return true;
  }
  return job.percent !== null || job.bytes_downloaded !== null;
}

function formatModelSize(size: number | null) {
  if (!size) return null;
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(1)} GB`;
  if (size >= 1024 ** 2) return `${Math.round(size / 1024 ** 2)} MB`;
  return `${Math.round(size / 1024)} KB`;
}

function formatModelSpeed(bytesPerSecond: number) {
  const size = formatModelSize(bytesPerSecond);
  return size ? `${size}/s` : null;
}

function modelSourceLabel(model: RequiredModelAvailability) {
  if (model.source_urls.length > 0) return "Download source known";
  if (model.source_availability === "resolvable") return "Can search Hugging Face and Civitai";
  return "No download source";
}

function WorkflowVariantSelect({
  title,
  value,
  variants,
  onChange,
}: {
  title: string;
  value: string;
  variants: WorkflowCardVariant[];
  onChange: (workflowId: string) => void;
}) {
  return (
    <label className="workflow-variant-select">
      <span>Model</span>
      <div className="workflow-variant-select__control">
        <select
          aria-label={`${title} model workflow`}
          value={value}
          disabled={variants.length < 2}
          onChange={(event) => onChange(event.target.value)}
        >
          {variants.map((variant) => (
            <option key={variant.id} value={variant.id}>
              {variant.label}
            </option>
          ))}
        </select>
        <ChevronDown size={15} aria-hidden="true" />
      </div>
    </label>
  );
}

function WorkflowCardView({
  workflow,
  menuOpen,
  onOpenWorkflow,
  onConfigureDashboard,
  onViewDetails,
  onToggleMenu,
  onCloseMenu,
  onVariantChange,
  onEditDashboard,
  onEditWidgets,
  onRemove,
}: {
  workflow: WorkflowCard;
  menuOpen: boolean;
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  onViewDetails: () => void;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onVariantChange: (workflowId: string) => void;
  onEditDashboard: () => void;
  onEditWidgets: () => void;
  onRemove: () => void;
}) {
  const StatusIcon = workflowIconStatus(workflow.status);
  const needsSetup =
    workflow.status === "needs_input_setup" || workflow.status === "cannot_prepare_automatically";
  const canOpen = workflow.source === "backend" || workflow.id === "text_to_image_v0";
  const canShowActions = workflow.source === "backend";
  const selectedWorkflowTitle = activeWorkflowTitle(workflow);

  function handleClick() {
    onCloseMenu();
    if (needsSetup) {
      onConfigureDashboard?.(workflow.id, selectedWorkflowTitle);
      return;
    }
    if (canOpen) {
      onOpenWorkflow(workflow.id);
    }
  }

  return (
    <article className={`workflow-card workflow-card--${workflow.status}`}>
      <div className="workflow-card__topline">
        <div className="workflow-card__icon" aria-hidden="true">
          <workflow.Icon size={22} />
        </div>
        <div className="workflow-card__meta">
          <div className="workflow-card__badges">
            <span className="category-badge">{workflow.category}</span>
            {workflow.trustLabel ? (
              <span
                className={`trust-badge trust-badge--${workflow.trustTone ?? "verified"}`}
                title={workflow.trustSummary}
              >
                {workflow.trustLabel}
              </span>
            ) : null}
          </div>
          {canShowActions ? (
            <WorkflowActionMenu
              workflow={{
                id: workflow.id,
                name: workflow.title,
                can_export_noofy: workflow.canExportNoofy,
                can_export_comfyui_json: workflow.canExportComfyJson,
                can_remove: workflow.canRemove,
              }}
              menuOpen={menuOpen}
              buttonClassName="icon-button icon-button--card"
              menuClassName="workflow-action-menu--card"
              onOpen={handleClick}
              onDetails={onViewDetails}
              onToggleMenu={onToggleMenu}
              onCloseMenu={onCloseMenu}
              onEditDashboard={onEditDashboard}
              onEditWidgets={onEditWidgets}
              onRemove={onRemove}
            />
          ) : null}
        </div>
      </div>
      <h3>{workflow.title}</h3>
      <p>{workflow.description}</p>
      {workflow.variants ? (
        <WorkflowVariantSelect
          title={workflow.title}
          value={workflow.id}
          variants={workflow.variants}
          onChange={onVariantChange}
        />
      ) : null}
      <div className="workflow-card__footer">
        <span className={`workflow-status workflow-status--${workflow.status}`}>
          <StatusIcon size={14} aria-hidden="true" />
          {workflow.statusLabel}
        </span>
        <button
          className="icon-button icon-button--card"
          type="button"
          aria-label={needsSetup ? `Configure dashboard for ${workflow.title}` : `Open ${workflow.title}`}
          title={needsSetup ? "Configure dashboard" : undefined}
          disabled={!needsSetup && !canOpen}
          onClick={handleClick}
        >
          <ArrowRight size={17} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}
