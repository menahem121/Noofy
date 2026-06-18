import { type ChangeEvent, type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Download,
  FileUp,
  PackagePlus,
  Plus,
  Search,
  Users,
  X,
} from "lucide-react";
import { openExternalUrl } from "../../lib/openExternalUrl";
import { resolveBackendUrl } from "../../lib/api/client";
import { workflowDisplayName } from "../../lib/workflowNames";
import type { NativeWorkflowImportRequest } from "../../lib/nativeWorkflowFiles";

// Replace with your real Reddit community URL when ready.
const REDDIT_URL = "https://www.reddit.com/r/noofy";
const REDDIT_ICON_URL = "/assets/reddit_icon.svg";

import {
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchWorkflowPackage,
  removeWorkflow,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import { useOptionalWorkflowTabs } from "../app/WorkflowTabs";
import {
  clearDashboardDraft,
  loadDashboardDraft,
  type DashboardSchema,
} from "../dashboard-builder/dashboardBuilderContent";
import type { WorkflowExportReviewModel } from "../../lib/workflowExport";
import { buildDashboardSchemaForEditing } from "../workflows/dashboardEditing";
import { WorkflowImportDialogs } from "../workflows/WorkflowImportModals";
import {
  WORKFLOW_IMPORT_ACCEPT,
  useWorkflowImportFlow,
  type WorkflowImportFlowController,
} from "../workflows/useWorkflowImportFlow";
import { importNeedsConfiguration } from "../workflows/workflowImportUtils";
import { WorkflowActionMenu } from "../workflows/WorkflowActionMenu";
import { WorkflowExportDialog } from "../workflows/WorkflowExportDialog";
import { hardwareWarningPillView } from "../workflows/hardwareWarning";
import { WORKFLOW_ICONS } from "../workflows/workflowMetadataOptions";
import {
  searchWorkflows,
  workflowNeedsConfiguration,
  workflowStatus as workflowSearchStatus,
  workflowStatusLabel as workflowSearchStatusLabel,
} from "../workflows/workflowSearch";
import {
  fallbackWorkflow,
  starterWorkflows,
  type WorkflowCard,
  type WorkflowCardVariant,
  type WorkflowStatus,
} from "./homeContent";
import {
  loadPendingImportedSetups,
  savePendingImportedSetups,
  type PendingImportedSetup,
} from "./pendingSetupBanners";
import { useWorkflowLibrary } from "./WorkflowLibraryProvider";
import { cleanupRemovedWorkflowFrontendState } from "../workflows/workflowRemoval";

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
    return "Generate new images from a simple text prompt.";
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
      title: workflowDisplayName(workflow),
      description: friendlyDescription(workflow),
      category: workflow.trust_level === "quarantined_community" ? "Imported" : "Installed",
      status,
      statusLabel:
        status === "needs_input_setup"
          ? workflowStatusLabel(status)
          : workflow.status === "imported"
          ? workflowStatusLabel(status)
          : workflow.status_label ?? workflowStatusLabel(status),
      trustLabel: workflow.trust?.label ?? trustLevelLabel(workflow.trust_level),
      trustTone: workflow.trust?.badge_tone ?? trustLevelTone(workflow.trust_level),
      trustSummary: workflow.trust?.summary,
      canRemove: Boolean(workflow.can_remove),
      canExportNoofy: Boolean(workflow.can_export_noofy),
      canExportComfyJson: workflow.can_export_comfyui_json !== false,
      hardwareWarning: workflow.hardware_warning ?? null,
      icon: workflow.icon,
      Icon: WORKFLOW_ICONS[(workflow.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? fallbackWorkflow.Icon,
      source: "backend",
    };
  });
}

function recentlyOpenedWorkflows(workflows: WorkflowSummary[]) {
  return [...workflows]
    .filter((workflow) => workflow.last_opened && !Number.isNaN(new Date(workflow.last_opened).getTime()))
    .sort(
      (left, right) =>
        new Date(right.last_opened ?? 0).getTime() - new Date(left.last_opened ?? 0).getTime(),
    )
    .slice(0, 3);
}

function recentWorkflowKind(workflow: WorkflowSummary) {
  if (workflow.source_label === "Imported") return "Imported workflow";
  if (workflow.source_label === "Created by me") return "Created workflow";
  return workflow.category ? `${workflow.category} workflow` : "Workflow";
}

function formatOpenedAt(value: string | null | undefined) {
  if (!value) return "Never opened";
  const opened = new Date(value);
  if (Number.isNaN(opened.getTime())) return value;
  const diffMs = Math.max(Date.now() - opened.getTime(), 0);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diffMs < minute) return "Just now";
  if (diffMs < hour) {
    const minutes = Math.floor(diffMs / minute);
    return `${minutes} min ago`;
  }
  if (diffMs < day) {
    const hours = Math.floor(diffMs / hour);
    return `${hours} hr ago`;
  }
  if (diffMs < 2 * day) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(opened);
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
    description: "Generate new images from a simple text prompt.",
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
    if (!isNativeBundledWorkflow(workflow)) continue;
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

  const normalizedName = normalizeWorkflowName(workflowDisplayName(workflow));
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
  const displayName = workflowDisplayName(workflow);
  const rawLabel = displayName
    .replace(new RegExp(`^${group.title.replace(/\s+/g, "\\s+")}`, "i"), "")
    .replace(/^[\s:\u2014\u2013-]+/, "")
    .trim();
  const modelLabel = workflow.main_model?.name && workflow.main_model.name !== "No model detected"
    ? workflow.main_model.name.replace(/\.(safetensors|ckpt|gguf|pt|pth)$/i, "")
    : null;

  return {
    id: workflow.id,
    label: rawLabel || modelLabel || displayName,
    title: displayName,
  };
}

function workflowStatusFromSummary(workflow: WorkflowSummary): WorkflowStatus {
  if (workflow.status === "cannot_prepare_automatically") {
    return "cannot_prepare_automatically";
  }

  if (workflowSearchStatus(workflow) === "need_setup") {
    return "needs_input_setup";
  }

  return "installed";
}

function workflowStatusLabel(status: WorkflowStatus) {
  if (status === "needs_input_setup") {
    return "Configure";
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

interface PendingSetupBanner {
  kind: "import" | "draft";
  workflowId: string;
  workflowName: string;
  title: string;
  message: string;
}

interface PendingWorkflowRemoval {
  workflowId: string;
  workflowName: string;
  busy: boolean;
  error: string | null;
}

interface HomePageProps {
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  nativeImportRequest?: NativeWorkflowImportRequest | null;
  workflowImportFlow?: WorkflowImportFlowController;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  onEditWidgets?: (schema: DashboardSchema) => void;
  onEditDashboard?: (schema: DashboardSchema) => void;
  onNavigate: (route: AppRouteId, options?: { workflowSearch?: string }) => void;
}

export function HomePage({
  onOpenWorkflow,
  nativeImportRequest,
  workflowImportFlow,
  onConfigureDashboard,
  onEditWidgets,
  onEditDashboard,
  onNavigate,
}: HomePageProps) {
  const workflowTabs = useOptionalWorkflowTabs();
  const localImportFlow = useWorkflowImportFlow({
    onOpenWorkflow,
    onConfigureDashboard,
    deferConfigurationAfterDownloadedImport: true,
  });
  const importFlowController = workflowImportFlow ?? localImportFlow;
  const rendersOwnImportDialogs = !workflowImportFlow;
  const {
    state: homeData,
    startWorkflowImport,
    failImport,
    cancelImport,
    dismissImportResult,
  } = importFlowController;
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
  const [cardActionError, setCardActionError] = useState<string | null>(null);
  const [pendingRemoval, setPendingRemoval] = useState<PendingWorkflowRemoval | null>(null);
  const [draftDismissTick, setDraftDismissTick] = useState(0);
  const [pendingImportedSetups, setPendingImportedSetups] = useState<PendingImportedSetup[]>(
    loadPendingImportedSetups,
  );
  const startupPendingImportedWorkflowIds = useRef<Set<string> | null>(null);
  if (startupPendingImportedWorkflowIds.current === null) {
    startupPendingImportedWorkflowIds.current = new Set(pendingImportedSetups.map((item) => item.workflowId));
  }
  const [exportDialog, setExportDialog] = useState<{
    workflowName: string;
    exportUrl: string;
    extension: ".noofy" | ".json";
    review?: WorkflowExportReviewModel;
  } | null>(null);
  const [selectedNativeVariants, setSelectedNativeVariants] = useState<Record<string, string | undefined>>({});
  const [homeSearch, setHomeSearch] = useState("");
  const [searchDropdownOpen, setSearchDropdownOpen] = useState(false);
  const [highlightedSearchIndex, setHighlightedSearchIndex] = useState(-1);
  const [handledNativeImportId, setHandledNativeImportId] = useState<number | null>(null);
  const debouncedHomeSearch = useDebouncedValue(homeSearch, 160);
  const runtimeStatus = useRuntimeStatus();
  const workflowLibrary = useWorkflowLibrary();
  const { refreshRuntime } = runtimeStatus;
  const { refreshWorkflows } = workflowLibrary;

  useEffect(() => {
    void refreshRuntime({ silent: true });
    const initialWorkflowIds = startupPendingImportedWorkflowIds.current ?? new Set<string>();
    void refreshWorkflows().then((workflows) => {
      if (!workflows) return;
      setPendingImportedSetups((current) => (
        reconcilePendingImportedSetups(current, workflows, initialWorkflowIds)
      ));
    });
  }, [refreshRuntime, refreshWorkflows]);

  useEffect(() => {
    savePendingImportedSetups(pendingImportedSetups);
  }, [pendingImportedSetups]);

  useEffect(() => {
    const result = homeData.importResult;
    if (!result || !importNeedsConfiguration(result)) return;

    const pendingSetup = {
      workflowId: result.workflow.id,
      workflowName: workflowDisplayName(result.workflow),
      dismissed: false,
    };
    startupPendingImportedWorkflowIds.current?.delete(pendingSetup.workflowId);
    setPendingImportedSetups((current) => {
      return [pendingSetup, ...current.filter((item) => item.workflowId !== pendingSetup.workflowId)];
    });
  }, [homeData.importResult]);

  useEffect(() => {
    if (!workflowLibrary.hasLoaded) return;

    setPendingImportedSetups((current) => {
      return reconcilePendingImportedSetups(current, workflowLibrary.workflows);
    });
  }, [workflowLibrary.hasLoaded, workflowLibrary.workflows]);

  const workflowCards = useMemo(() => {
    const backendCards = homeWorkflowCardsFromBackend(workflowLibrary.workflows, selectedNativeVariants);
    const fallbackCards = backendCards.length > 0 ? backendCards : [fallbackWorkflow];
    const starterWithoutDuplicates = starterWorkflows.filter(
      (starter) => !fallbackCards.some((card) => card.id === starter.id || card.title === starter.title),
    );

    return [...fallbackCards, ...starterWithoutDuplicates].slice(0, 8);
  }, [selectedNativeVariants, workflowLibrary.workflows]);
  const builtInCount = useMemo(
    () => workflowLibrary.workflows.filter(isNativeBundledWorkflow).length,
    [workflowLibrary.workflows],
  );
  const recentlyOpened = useMemo(
    () => recentlyOpenedWorkflows(workflowLibrary.workflows),
    [workflowLibrary.workflows],
  );

  // Imported setup reminders persist independently, while saved builder drafts
  // also surface reminders. Import reminders win when both exist for one workflow.
  const pendingSetupBanners = useMemo<PendingSetupBanner[]>(() => {
    const workflowsById = new Map(workflowLibrary.workflows.map((workflow) => [workflow.id, workflow]));
    const importedWorkflowIds = new Set(pendingImportedSetups.map((item) => item.workflowId));
    const importedBanners: PendingSetupBanner[] = pendingImportedSetups.flatMap((item) => {
      const workflow = workflowsById.get(item.workflowId);
      if (item.dismissed || (workflow && !workflowNeedsConfiguration(workflow))) return [];
      const workflowName = workflow ? workflowDisplayName(workflow) : item.workflowName;
      return [{
        kind: "import",
        workflowId: item.workflowId,
        workflowName,
        title: "The workflow needs setup",
        message: `${workflowName} was added to your local workflows.`,
      }];
    });
    const draftWorkflows = workflowLibrary.workflows.filter(
      (workflow) =>
        !importedWorkflowIds.has(workflow.id) &&
        workflowNeedsConfiguration(workflow) &&
        loadDashboardDraft(workflow.id) !== null,
    );
    const draftBanners: PendingSetupBanner[] = draftWorkflows.map((workflow) => {
      const name = workflowDisplayName(workflow);
      return {
        kind: "draft",
        workflowId: workflow.id,
        workflowName: name,
        title: "Needs input setup",
        message: `Resume setting up ${name} to finish its dashboard.`,
      };
    });

    return [...importedBanners, ...draftBanners];
    // draftDismissTick forces a re-read after a draft is discarded locally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowLibrary.workflows, pendingImportedSetups, draftDismissTick]);

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
    runtimeStatus.backendStatus === "unreachable"
      ? {
          title: "Noofy is reconnecting",
          message: "The page is keeping the last loaded workflows visible while Noofy reconnects.",
        }
      : workflowLibrary.error
        ? {
            title: "Workflow library could not load",
            message: "Noofy will keep trying in the background. Restart Noofy if this keeps happening.",
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

  useEffect(() => {
    if (!nativeImportRequest || handledNativeImportId === nativeImportRequest.id) return;
    setHandledNativeImportId(nativeImportRequest.id);
    if (nativeImportRequest.error || !nativeImportRequest.file) {
      failImport(
        nativeImportRequest.error ??
          `Noofy could not open ${nativeImportRequest.filename ?? "the selected workflow package"}.`,
      );
      return;
    }
    void startWorkflowImport(nativeImportRequest.file);
  }, [failImport, handledNativeImportId, nativeImportRequest, startWorkflowImport]);

  async function handleWorkflowFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) {
      return;
    }
    await startWorkflowImport(file);
  }

  async function handleViewModelsAfterImportDiskSpaceFailure() {
    await cancelImport();
    onNavigate("models");
  }

  function requestRemoveWorkflowCard(workflow: WorkflowCard) {
    const workflowId = activeWorkflowId(workflow);
    const workflowTitle = activeWorkflowTitle(workflow);
    if (!workflow.canRemove) return;
    setMenuOpenFor(null);
    setCardActionError(null);
    setPendingRemoval({
      workflowId,
      workflowName: workflowTitle,
      busy: false,
      error: null,
    });
  }

  function requestRemoveRecentWorkflow(workflow: WorkflowSummary) {
    if (!workflow.can_remove) return;
    setMenuOpenFor(null);
    setCardActionError(null);
    setPendingRemoval({
      workflowId: workflow.id,
      workflowName: workflowDisplayName(workflow),
      busy: false,
      error: null,
    });
  }

  async function confirmWorkflowRemoval() {
    if (!pendingRemoval || pendingRemoval.busy) return;
    const { workflowId } = pendingRemoval;
    setPendingRemoval((current) => current ? { ...current, busy: true, error: null } : current);
    try {
      await removeWorkflow(workflowId);
      cleanupRemovedWorkflowFrontendState(workflowId, workflowTabs);
      setPendingImportedSetups((current) => {
        return current.filter((item) => item.workflowId !== workflowId);
      });
      await refreshWorkflows();
      setPendingRemoval(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setPendingRemoval((current) => current ? { ...current, busy: false, error: message } : current);
    }
  }

  function dismissPendingSetup(banner: PendingSetupBanner) {
    if (banner.kind === "import") {
      setPendingImportedSetups((current) => {
        return current.map((item) =>
          item.workflowId === banner.workflowId ? { ...item, dismissed: true } : item,
        );
      });
      dismissImportResult();
    } else {
      // Draft-only reminders predate imported setup banners and intentionally
      // discard the local draft when dismissed.
      clearDashboardDraft(banner.workflowId);
    }
    setDraftDismissTick((tick) => tick + 1);
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

  async function handleEditRecentWorkflow(
    workflow: WorkflowSummary,
    open?: (schema: DashboardSchema) => void,
  ) {
    setMenuOpenFor(null);
    setCardActionError(null);
    if (!open) {
      onConfigureDashboard?.(workflow.id, workflowDisplayName(workflow));
      return;
    }
    try {
      const packageData = await fetchWorkflowPackage(workflow.id);
      open(buildDashboardSchemaForEditing(packageData));
    } catch (error) {
      setCardActionError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <AppLayout activeRoute="home" onNavigate={onNavigate}>
          <section className="page-heading" aria-labelledby="home-title">
            <div>
              <p className="eyebrow">PRIVATE LOCAL AI STUDIO</p>
              <h1 id="home-title">Powerful AI workflows without the complexity</h1>
              <p>
                Noofy turns advanced image workflows into simple creative tools that run privately on your machine.
              </p>
            </div>
            <button className="primary-button" type="button" onClick={() => onConfigureDashboard?.()}>
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

          {homeData.importResult && !importNeedsConfiguration(homeData.importResult) ? (
            <div className="notice notice--row" role="status">
              <CheckCircle2 size={18} aria-hidden="true" />
              <div>
                <strong>{homeData.importResult.user_facing_message}</strong>
                <span>{workflowDisplayName(homeData.importResult.workflow)} was added to your local workflows.</span>
              </div>
            </div>
          ) : null}

          {pendingSetupBanners.map((banner) => (
            <div className="notice notice--row" role="status" key={`${banner.kind}:${banner.workflowId}`}>
              <CheckCircle2 size={18} aria-hidden="true" />
              <div>
                <strong>{banner.title}</strong>
                <span>{banner.message}</span>
              </div>
              {onConfigureDashboard ? (
                <button
                  className="primary-button primary-button--compact"
                  style={{ marginLeft: "auto" }}
                  type="button"
                  onClick={() => onConfigureDashboard(banner.workflowId, banner.workflowName)}
                >
                  <PackagePlus size={14} aria-hidden="true" />
                  Configure dashboard
                </button>
              ) : null}
              <button
                className="icon-button"
                style={onConfigureDashboard ? undefined : { marginLeft: "auto" }}
                type="button"
                aria-label={`Dismiss setup for ${banner.workflowName}`}
                title={banner.kind === "draft" ? "Discard draft and dismiss" : "Dismiss"}
                onClick={() => dismissPendingSetup(banner)}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
          ))}

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

          {rendersOwnImportDialogs ? (
            <WorkflowImportDialogs
              importFlow={importFlowController}
              onViewModels={() => void handleViewModelsAfterImportDiskSpaceFailure()}
            />
          ) : null}

          <section className="action-grid" aria-label="Workflow actions">
            <article className="action-card">
              <div className="action-card__icon">
                <FileUp size={26} aria-hidden="true" />
              </div>
              <div>
                <h2>Import any ComfyUI Workflow</h2>
                <p>
                  Import a <span className="workflow-extension workflow-extension--json">.json</span> or{" "}
                  <span className="workflow-extension workflow-extension--noofy">.noofy</span> workflow to run it through Noofy.
                </p>
              </div>
              <label
                className={`secondary-button action-card__button${homeData.importing ? " is-disabled" : ""}`}
                aria-disabled={homeData.importing}
              >
                <input
                  className="sr-only"
                  type="file"
                  accept={WORKFLOW_IMPORT_ACCEPT}
                  disabled={homeData.importing}
                  onChange={(event) => void handleWorkflowFileSelected(event)}
                />
                <FileUp size={16} aria-hidden="true" />
                {homeData.importing ? "Importing..." : "Choose File"}
              </label>
            </article>

            <article className="action-card action-card--accent action-card--reddit">
              <div className="action-card__icon action-card__icon--accent action-card__icon--reddit">
                <Users size={26} aria-hidden="true" />
              </div>
              <div>
                <h2>Join the Reddit Community</h2>
                <p>Share workflows, ask questions, and follow Noofy's progress with other AI creators.</p>
              </div>
              <button
                className="primary-button primary-button--compact primary-button--reddit"
                type="button"
                onClick={() => void openExternalUrl(REDDIT_URL)}
              >
                <img className="reddit-icon" src={REDDIT_ICON_URL} alt="" aria-hidden="true" />
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
                              <span>{workflowDisplayName(workflow)}</span>
                              <small>{workflow.description || workflow.status_label || workflowSearchStatusLabel(workflow)}</small>
                            </span>
                            <span className="mini-status">{workflowSearchStatusLabel(workflow)}</span>
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
              <button className="ghost-button" type="button" onClick={() => onNavigate("workflows")}>
                View workflows
                <ArrowRight size={16} aria-hidden="true" />
              </button>
            </div>

            <div className="recent-list">
              {recentlyOpened.length > 0 ? (
                recentlyOpened.map((recent) => {
                  const Icon =
                    WORKFLOW_ICONS[(recent.icon as keyof typeof WORKFLOW_ICONS) ?? "sparkles"] ?? fallbackWorkflow.Icon;
                  return (
                    <article className="recent-row" key={recent.id}>
                      <div className="recent-row__icon" aria-hidden="true">
                        {recent.icon?.startsWith("asset:") ? (
                          <img
                            className="workflow-custom-icon"
                            src={resolveBackendUrl(
                              `/api/assets/${encodeURIComponent(recent.icon.slice("asset:".length))}`,
                              { includeToken: true },
                            )}
                            alt=""
                          />
                        ) : (
                          <Icon size={20} />
                        )}
                      </div>
                      <div className="recent-row__body">
                        <div className="recent-row__title">
                          <h3>{recent.name}</h3>
                          <span className="mini-status">{workflowSearchStatusLabel(recent)}</span>
                        </div>
                        <p>
                          {recentWorkflowKind(recent)}
                          <span aria-hidden="true" />
                          {formatOpenedAt(recent.last_opened)}
                        </p>
                      </div>
                      <button
                        className="secondary-button secondary-button--small"
                        type="button"
                        onClick={() => onOpenWorkflow(recent.id)}
                      >
                        Open
                      </button>
                      <WorkflowActionMenu
                        workflow={recent}
                        menuOpen={menuOpenFor === `recent:${recent.id}`}
                        mode="settings"
                        buttonClassName="icon-button icon-button--recent"
                        menuClassName="workflow-action-menu--recent"
                        onOpen={() => onOpenWorkflow(recent.id)}
                        onDetails={() => {
                          setMenuOpenFor(null);
                          onNavigate("workflows", { workflowSearch: workflowDisplayName(recent) });
                        }}
                        onToggleMenu={() =>
                          setMenuOpenFor((current) =>
                            current === `recent:${recent.id}` ? null : `recent:${recent.id}`,
                          )
                        }
                        onCloseMenu={() => setMenuOpenFor(null)}
                        onEditDashboard={() => void handleEditRecentWorkflow(recent, onEditDashboard)}
                        onEditWidgets={() => void handleEditRecentWorkflow(recent, onEditWidgets)}
                        onExportNoofy={() =>
                          setExportDialog({
                            workflowName: workflowDisplayName(recent),
                            exportUrl: exportWorkflowUrl(recent.id),
                            extension: ".noofy",
                            review: workflowSummaryExportReview(recent),
                          })
                        }
                        onExportComfyJson={() =>
                          setExportDialog({
                            workflowName: workflowDisplayName(recent),
                            exportUrl: exportWorkflowComfyJsonUrl(recent.id),
                            extension: ".json",
                          })
                        }
                        onRemove={() => requestRemoveRecentWorkflow(recent)}
                      />
                    </article>
                  );
                })
              ) : (
                <div className="recent-row recent-row--empty" role="status">
                  <div className="recent-row__body">
                    <h3>No recently opened workflows yet.</h3>
                    <p>Open a workflow and it will appear here.</p>
                  </div>
                </div>
              )}
            </div>
          </section>

          <section className="section-heading" aria-labelledby="built-in-workflows-title">
            <div>
              <h2 id="built-in-workflows-title">Built-in Workflows</h2>
              <p>
                {builtInCount > 0
                  ? `${builtInCount} built-in workflow${builtInCount === 1 ? "" : "s"} available.`
                  : "Starter workflows will appear here as packages are added."}
              </p>
            </div>
            <button className="ghost-button" type="button" onClick={() => onNavigate("workflows")}>
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
                onExportNoofy={() =>
                  setExportDialog({
                    workflowName: activeWorkflowTitle(workflow),
                    exportUrl: exportWorkflowUrl(activeWorkflowId(workflow)),
                    extension: ".noofy",
                    review: workflowCardExportReview(workflow),
                  })
                }
                onExportComfyJson={() =>
                  setExportDialog({
                    workflowName: activeWorkflowTitle(workflow),
                    exportUrl: exportWorkflowComfyJsonUrl(activeWorkflowId(workflow)),
                    extension: ".json",
                  })
                }
                onRemove={() => requestRemoveWorkflowCard(workflow)}
              />
            ))}
          </section>
          {pendingRemoval ? (
            <WorkflowRemovalDialog
              removal={pendingRemoval}
              onCancel={() => setPendingRemoval(null)}
              onConfirm={() => void confirmWorkflowRemoval()}
            />
          ) : null}
          {exportDialog ? (
            <WorkflowExportDialog
              workflowName={exportDialog.workflowName}
              exportUrl={exportDialog.exportUrl}
              extension={exportDialog.extension}
              review={exportDialog.review}
              onClose={() => setExportDialog(null)}
            />
          ) : null}
    </AppLayout>
  );
}

function reconcilePendingImportedSetups(
  current: PendingImportedSetup[],
  workflows: WorkflowSummary[],
  removeMissingWorkflowIds = new Set<string>(),
) {
  const workflowsById = new Map(workflows.map((workflow) => [workflow.id, workflow]));
  const next = current.flatMap((item) => {
    const workflow = workflowsById.get(item.workflowId);
    if (!workflow) return removeMissingWorkflowIds.has(item.workflowId) ? [] : [item];
    if (!workflowNeedsConfiguration(workflow)) return [];
    return [{ ...item, workflowName: workflowDisplayName(workflow) }];
  });

  return importedSetupsEqual(current, next) ? current : next;
}

function importedSetupsEqual(left: PendingImportedSetup[], right: PendingImportedSetup[]) {
  return (
    left.length === right.length &&
    left.every(
      (item, index) =>
        item.workflowId === right[index]?.workflowId &&
        item.workflowName === right[index]?.workflowName &&
        item.dismissed === right[index]?.dismissed,
    )
  );
}

function activeWorkflowId(workflow: WorkflowCard) {
  return workflow.id;
}

function activeWorkflowTitle(workflow: WorkflowCard) {
  return workflow.variants?.find((variant) => variant.id === workflow.id)?.title ?? workflow.title;
}

function workflowCardExportReview(workflow: WorkflowCard): WorkflowExportReviewModel {
  return {
    name: activeWorkflowTitle(workflow),
    description: workflow.description,
    category: workflow.category,
    source: workflow.source === "backend" ? workflow.category : "Starter workflow",
    requiredModels: [],
  };
}

function workflowSummaryExportReview(workflow: WorkflowSummary): WorkflowExportReviewModel {
  return {
    name: workflowDisplayName(workflow),
    description: workflow.description,
    category: workflow.category ?? "",
    tags: workflow.tags ?? [],
    icon: workflow.icon ?? "",
    source: workflow.source_label ?? workflow.trust?.label ?? "Noofy workflow",
    requiredModels: workflow.main_model?.name
      ? [{
          name: workflow.main_model.name,
          type: workflow.main_model.type,
          size_bytes: workflow.main_model.size_bytes,
          status_label: workflow.missing_model_count && workflow.missing_model_count > 0 ? "Missing" : "Available",
        }]
      : [],
  };
}

function WorkflowRemovalDialog({
  removal,
  onCancel,
  onConfirm,
}: {
  removal: PendingWorkflowRemoval;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !removal.busy) {
        onCancel();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, removal.busy]);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="workflow-remove-title"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !removal.busy) onCancel();
      }}
    >
      <section className="workflow-close-modal" aria-busy={removal.busy}>
        <header className="workflow-close-modal__header">
          <h2 id="workflow-remove-title">Remove workflow?</h2>
          <p>
            Remove <strong>{removal.workflowName}</strong> from Noofy? This removes its local workflow files and saved
            setup.
          </p>
        </header>
        {removal.error ? (
          <div className="notice notice--error" role="status">
            <AlertCircle size={18} aria-hidden="true" />
            <div>
              <strong>Workflow could not be removed</strong>
              <span>{removal.error}</span>
            </div>
          </div>
        ) : null}
        <footer className="workflow-close-modal__footer">
          <button
            className="secondary-button"
            type="button"
            autoFocus
            disabled={removal.busy}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button className="danger-button" type="button" disabled={removal.busy} onClick={onConfirm}>
            {removal.busy ? "Removing..." : "Remove workflow"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function nativeVariantSelectionKey(workflow: WorkflowCard) {
  if (workflow.title === "Text to Image") return "text_to_image";
  if (workflow.title === "Image to Image") return "image_to_image";
  return workflow.id;
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
  onExportNoofy,
  onExportComfyJson,
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
  onExportNoofy: () => void;
  onExportComfyJson: () => void;
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
          <WorkflowCardIcon workflow={workflow} />
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
            {workflow.hardwareWarning ? <HardwareWarningPill warning={workflow.hardwareWarning} /> : null}
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
              onExportNoofy={onExportNoofy}
              onExportComfyJson={onExportComfyJson}
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

function HardwareWarningPill({ warning }: { warning: NonNullable<WorkflowCard["hardwareWarning"]> }) {
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

function WorkflowCardIcon({ workflow }: { workflow: WorkflowCard }) {
  if (workflow.icon?.startsWith("asset:")) {
    const assetId = workflow.icon.slice("asset:".length);
    return (
      <img
        className="workflow-custom-icon"
        src={resolveBackendUrl(`/api/assets/${encodeURIComponent(assetId)}`, { includeToken: true })}
        alt=""
      />
    );
  }
  return <workflow.Icon size={22} />;
}
