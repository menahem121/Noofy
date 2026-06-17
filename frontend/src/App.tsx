import { lazy, Suspense, useEffect, useRef, useState } from "react";

import type { AppNavigateOptions, AppRouteId } from "./features/app/AppLayout";
import { SidebarProvider } from "./features/app/AppLayout";
import { RuntimeStatusProvider } from "./features/app/RuntimeStatusProvider";
import { WorkflowTabsProvider, WorkflowTabsRouteProvider, useWorkflowTabs, type WorkflowTabRuntimeState } from "./features/app/WorkflowTabs";
import type { DashboardSchema } from "./features/dashboard-builder/dashboardBuilderContent";
import { removePendingImportedSetupReminder } from "./features/home/pendingSetupBanners";
import { HomePage } from "./features/home/HomePage";
import { WorkflowLibraryProvider, useWorkflowLibrary } from "./features/home/WorkflowLibraryProvider";
import { FirstLaunchOnboarding } from "./features/onboarding/FirstLaunchOnboarding";
import { WorkflowGlobalDropImport, WorkflowImportStatusNotice } from "./features/workflows/WorkflowGlobalImport";
import { WorkflowImportDialogs } from "./features/workflows/WorkflowImportModals";
import { useWorkflowImportFlow } from "./features/workflows/useWorkflowImportFlow";
import { workflowNeedsConfiguration } from "./features/workflows/workflowSearch";
import {
  cancelQueuedRunnerStart,
  cancelWorkflowActiveAndQueuedRuns,
  closeWorkflowRunnerLease,
  fetchJobProgress,
  fetchWorkflowActiveAndQueuedRuns,
  recordWorkflowOpened,
  type WorkflowSummary,
} from "./lib/api/noofyApi";
import {
  consumePendingNativeWorkflowFile,
  listenForNativeWorkflowOpen,
  readNativeWorkflowFile,
  type NativeWorkflowImportRequest,
  type NativeWorkflowOpenPayload,
} from "./lib/nativeWorkflowFiles";
import { workflowDisplayName } from "./lib/workflowNames";

const DashboardBuilderPage = lazy(() =>
  import("./features/dashboard-builder/DashboardBuilderPage").then((module) => ({ default: module.DashboardBuilderPage })),
);
const DashboardBuilderLayoutPage = lazy(() =>
  import("./features/dashboard-builder/DashboardBuilderLayoutPage").then((module) => ({ default: module.DashboardBuilderLayoutPage })),
);
const EngineSettingsPage = lazy(() =>
  import("./features/settings/EngineSettingsPage").then((module) => ({ default: module.EngineSettingsPage })),
);
const GalleryPage = lazy(() =>
  import("./features/gallery/GalleryPage").then((module) => ({ default: module.GalleryPage })),
);
const HistoryPage = lazy(() =>
  import("./features/history/HistoryPage").then((module) => ({ default: module.HistoryPage })),
);
const ModelsPage = lazy(() =>
  import("./features/models/ModelsPage").then((module) => ({ default: module.ModelsPage })),
);
const WorkflowRunPage = lazy(() =>
  import("./features/workflows/WorkflowRunPage").then((module) => ({ default: module.WorkflowRunPage })),
);
const WorkflowsPage = lazy(() =>
  import("./features/workflows/WorkflowsPage").then((module) => ({ default: module.WorkflowsPage })),
);

type AppRoute =
  | { name: "home" }
  | { name: "workflows"; search?: string }
  | { name: "gallery" }
  | { name: "history" }
  | { name: "models" }
  | { name: "settings" }
  | { name: "workflow"; workflowId: string }
  | {
      name: "dashboard-builder";
      workflowId?: string;
      workflowName?: string;
      initialSchema?: DashboardSchema;
      returnToRunOnCancel?: boolean;
    }
  | { name: "dashboard-builder-layout"; workflowId?: string; workflowName?: string; initialSchema?: DashboardSchema };

type PersistedAppRoute = Exclude<AppRoute, { name: "dashboard-builder" } | { name: "dashboard-builder-layout" }>;

const APP_ROUTE_STORAGE_KEY = "noofy.appRoute.v1";

export default function App() {
  return (
    <RuntimeStatusProvider>
      <WorkflowTabsProvider>
        <WorkflowLibraryProvider>
          <SidebarProvider>
            <AppContent />
          </SidebarProvider>
        </WorkflowLibraryProvider>
      </WorkflowTabsProvider>
    </RuntimeStatusProvider>
  );
}

function AppContent() {
  const workflowLibrary = useWorkflowLibrary();
  const workflowTabs = useWorkflowTabs();
  const [route, setRoute] = useState<AppRoute>(() => loadStoredAppRoute(workflowTabs.tabs));
  const [closeDialog, setCloseDialog] = useState<WorkflowCloseDialogState | null>(null);
  const [nativeWorkflowImport, setNativeWorkflowImport] = useState<NativeWorkflowImportRequest | null>(null);
  const [handledNativeImportId, setHandledNativeImportId] = useState<number | null>(null);
  const nativeWorkflowImportIdRef = useRef(0);
  const workflowImportFlow = useWorkflowImportFlow({
    onOpenWorkflow: openWorkflow,
    onConfigureDashboard: configureDashboard,
    deferConfigurationAfterDownloadedImport: true,
  });
  const importStatusHidden = shouldHideImportStatusNotice(route, workflowImportFlow.state.importResult?.workflow.id);

  useEffect(() => {
    const persisted = persistedAppRoute(route);
    if (!persisted) return;
    try {
      window.localStorage.setItem(APP_ROUTE_STORAGE_KEY, JSON.stringify(persisted));
    } catch {
      // Route restoration is a convenience; navigation must remain usable without storage.
    }
  }, [route]);

  useEffect(() => {
    let active = true;
    let unlisten: (() => void) | null = null;

    async function openNativeWorkflow(payload: NativeWorkflowOpenPayload) {
      const id = nativeWorkflowImportIdRef.current + 1;
      nativeWorkflowImportIdRef.current = id;
      try {
        const file = await readNativeWorkflowFile(payload.path);
        if (!active || nativeWorkflowImportIdRef.current !== id) return;
        setNativeWorkflowImport({ id, file, filename: payload.filename });
        setRoute({ name: "home" });
      } catch (error) {
        if (!active || nativeWorkflowImportIdRef.current !== id) return;
        const message = error instanceof Error ? error.message : String(error);
        setNativeWorkflowImport({
          id,
          filename: payload.filename,
          error: `Noofy could not open ${payload.filename}. ${message}`,
        });
        setRoute({ name: "home" });
      }
    }

    async function openPendingNativeWorkflow(fallback?: NativeWorkflowOpenPayload) {
      try {
        const payload = await consumePendingNativeWorkflowFile();
        if (payload) {
          await openNativeWorkflow(payload);
          return;
        }
      } catch {
        // Browser/dev mode can run without the native desktop commands.
      }
      if (fallback) await openNativeWorkflow(fallback);
    }

    listenForNativeWorkflowOpen((payload) => {
      void openPendingNativeWorkflow(payload);
    })
      .then((handler) => {
        unlisten = handler;
        void openPendingNativeWorkflow();
      })
      .catch(() => {
        // Browser/dev mode can run without the native desktop event bridge.
        void openPendingNativeWorkflow();
      });

    return () => {
      active = false;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (!nativeWorkflowImport || handledNativeImportId === nativeWorkflowImport.id) return;
    setHandledNativeImportId(nativeWorkflowImport.id);
    if (nativeWorkflowImport.error || !nativeWorkflowImport.file) {
      workflowImportFlow.failImport(
        nativeWorkflowImport.error ??
          `Noofy could not open ${nativeWorkflowImport.filename ?? "the selected workflow package"}.`,
      );
      return;
    }
    void workflowImportFlow.startWorkflowImport(nativeWorkflowImport.file);
  }, [
    handledNativeImportId,
    nativeWorkflowImport,
    workflowImportFlow.failImport,
    workflowImportFlow.startWorkflowImport,
  ]);

  function workflowNameFor(workflowId: string, providedName?: string) {
    if (providedName) return providedName;
    if (workflowId === "text_to_image_v0") return "Text to Image";
    const workflow = workflowLibrary.workflows.find((item) => item.id === workflowId);
    return workflow ? workflowDisplayName(workflow) : "Workflow";
  }

  function openWorkflow(workflowId: string, workflowName?: string, options: { skipDashboardSetupGuard?: boolean } = {}) {
    const resolvedWorkflowName = workflowNameFor(workflowId, workflowName);
    const knownWorkflow = workflowLibrary.workflows.find((item) => item.id === workflowId);
    if (!options.skipDashboardSetupGuard && workflowNeedsDashboardSetup(knownWorkflow)) {
      setRoute({ name: "dashboard-builder", workflowId, workflowName: resolvedWorkflowName });
      return;
    }

    if (!options.skipDashboardSetupGuard && !knownWorkflow && !workflowLibrary.hasLoaded) {
      void workflowLibrary.refreshWorkflows().then((workflows) => {
        const refreshedWorkflow = workflows?.find((item) => item.id === workflowId);
        if (workflowNeedsDashboardSetup(refreshedWorkflow)) {
          setRoute({ name: "dashboard-builder", workflowId, workflowName: resolvedWorkflowName });
          return;
        }
        openRunnableWorkflow(workflowId, resolvedWorkflowName);
      });
      return;
    }

    openRunnableWorkflow(workflowId, resolvedWorkflowName);
  }

  function openRunnableWorkflow(workflowId: string, workflowName: string) {
    workflowTabs.openWorkflowTab(workflowId, workflowName);
    setRoute({ name: "workflow", workflowId });
    void recordWorkflowOpened(workflowId)
      .then((response) => workflowLibrary.updateWorkflowFromResponse(response.workflow))
      .catch(() => {
        // Opening a workflow should not be blocked by local history recording.
      });
  }

  function navigate(routeId: AppRouteId, options?: AppNavigateOptions) {
    if (routeId === "settings") {
      setRoute({ name: "settings" });
      return;
    }
    if (routeId === "models") {
      setRoute({ name: "models" });
      return;
    }
    if (routeId === "workflows") {
      setRoute({ name: "workflows", search: options?.workflowSearch });
      return;
    }
    if (routeId === "gallery") {
      setRoute({ name: "gallery" });
      return;
    }
    if (routeId === "history") {
      setRoute({ name: "history" });
      return;
    }
    setRoute({ name: "home" });
  }

  function configureDashboard(workflowId?: string, workflowName?: string) {
    setRoute({
      name: "dashboard-builder",
      workflowId: workflowId ?? undefined,
      workflowName: workflowName ?? undefined,
    });
  }

  async function requestCloseWorkflowTab(workflowId: string) {
    const refreshed = await activeCloseState(workflowId, workflowTabs.runtimeByWorkflowId[workflowId]);
    if (refreshed) {
      setCloseDialog({
        workflowId,
        workflowName: workflowNameFor(workflowId),
        runtime: refreshed,
        busy: false,
        error: null,
      });
      return;
    }
    await closeWorkflowTabNow(workflowId);
  }

  async function closeWorkflowTabNow(workflowId: string) {
    await closeWorkflowSessionLease(workflowId, workflowTabs.runtimeByWorkflowId[workflowId]);
    const nextRoute = fallbackRouteAfterClose(workflowId, workflowTabs.tabs, route);
    workflowTabs.closeWorkflowTab(workflowId);
    if (nextRoute) setRoute(nextRoute);
  }

  async function confirmStopAndClose() {
    if (!closeDialog) return;
    setCloseDialog((current) => (current ? { ...current, busy: true, error: null } : current));
    try {
      await cancelRuntimeHandle(closeDialog.workflowId, closeDialog.runtime);
      await closeWorkflowTabNow(closeDialog.workflowId);
      setCloseDialog(null);
    } catch (error) {
      setCloseDialog((current) =>
        current
          ? {
              ...current,
              busy: false,
              error: error instanceof Error ? error.message : String(error),
            }
          : current,
      );
    }
  }

  function renderPage() {
    if (route.name === "workflow") {
      return (
        <WorkflowRunPage
          workflowId={route.workflowId}
          onBack={() => setRoute({ name: "home" })}
          onWorkflowNameChange={(workflowName) => workflowTabs.updateWorkflowTabName(route.workflowId, workflowName)}
          onEditWidgets={(schema) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: schema.workflowId,
              workflowName: schema.workflowName,
              initialSchema: schema,
              returnToRunOnCancel: true,
            })
          }
          onConfigureDashboard={configureDashboard}
          onNavigate={navigate}
        />
      );
    }

    if (route.name === "dashboard-builder") {
      const cancelWorkflowId = route.returnToRunOnCancel ? route.workflowId : undefined;
      return (
        <DashboardBuilderPage
          workflowId={route.workflowId}
          workflowName={route.workflowName}
          initialSchema={route.initialSchema}
          onBack={() => setRoute({ name: "home" })}
          onCancelEdit={
            cancelWorkflowId
              ? () => openWorkflow(cancelWorkflowId, route.workflowName, { skipDashboardSetupGuard: true })
              : undefined
          }
          onContinue={(schema) =>
            setRoute({
              name: "dashboard-builder-layout",
              workflowId: route.workflowId,
              workflowName: route.workflowName,
              initialSchema: schema,
            })
          }
          onNavigate={navigate}
        />
      );
    }

    if (route.name === "dashboard-builder-layout") {
      return (
        <DashboardBuilderLayoutPage
          workflowId={route.workflowId}
          workflowName={route.workflowName}
          initialSchema={route.initialSchema}
          onBackToWidgets={(schema) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: route.workflowId,
              workflowName: route.workflowName,
              initialSchema: schema,
            })
          }
          onSaveComplete={(workflowId) => {
            removePendingImportedSetupReminder(workflowId);
            openWorkflow(workflowId, undefined, { skipDashboardSetupGuard: true });
            void workflowLibrary.refreshWorkflows();
          }}
          onNavigate={navigate}
        />
      );
    }

    if (route.name === "settings") {
      return <EngineSettingsPage onNavigate={navigate} />;
    }

    if (route.name === "models") {
      return <ModelsPage onNavigate={navigate} />;
    }

    if (route.name === "workflows") {
      return (
        <WorkflowsPage
          onNavigate={navigate}
          onOpenWorkflow={openWorkflow}
          workflowImportFlow={workflowImportFlow}
          onConfigureDashboard={configureDashboard}
          onEditWidgets={(schema) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: schema.workflowId,
              workflowName: schema.workflowName,
              initialSchema: schema,
              returnToRunOnCancel: true,
            })
          }
          onEditDashboard={(schema) =>
            setRoute({
              name: "dashboard-builder-layout",
              workflowId: schema.workflowId,
              workflowName: schema.workflowName,
              initialSchema: schema,
            })
          }
          initialSearchQuery={route.search}
        />
      );
    }

    if (route.name === "gallery") {
      return <GalleryPage onNavigate={navigate} />;
    }

    if (route.name === "history") {
      return <HistoryPage onNavigate={navigate} />;
    }

    return (
      <HomePage
        onOpenWorkflow={openWorkflow}
        workflowImportFlow={workflowImportFlow}
        onConfigureDashboard={configureDashboard}
        onEditWidgets={(schema) =>
          setRoute({
            name: "dashboard-builder",
            workflowId: schema.workflowId,
            workflowName: schema.workflowName,
            initialSchema: schema,
            returnToRunOnCancel: true,
          })
        }
        onEditDashboard={(schema) =>
          setRoute({
            name: "dashboard-builder-layout",
            workflowId: schema.workflowId,
            workflowName: schema.workflowName,
            initialSchema: schema,
          })
        }
        onNavigate={navigate}
      />
    );
  }

  return (
    <WorkflowTabsRouteProvider
      activeWorkflowId={route.name === "workflow" ? route.workflowId : null}
      onActivateWorkflowTab={openWorkflow}
      onRequestCloseWorkflowTab={(workflowId) => void requestCloseWorkflowTab(workflowId)}
    >
      <Suspense fallback={<main className="app-route-loading" aria-busy="true" />}>{renderPage()}</Suspense>
      <WorkflowGlobalDropImport importFlow={workflowImportFlow} />
      <WorkflowImportStatusNotice
        importFlow={workflowImportFlow}
        hidden={importStatusHidden}
        onConfigureDashboard={configureDashboard}
      />
      <WorkflowImportDialogs
        importFlow={workflowImportFlow}
        onViewModels={() => {
          void workflowImportFlow.cancelImport().then(() => navigate("models"));
        }}
      />
      {closeDialog ? (
        <WorkflowCloseDialog
          dialog={closeDialog}
          onCancel={() => setCloseDialog(null)}
          onConfirm={() => void confirmStopAndClose()}
        />
      ) : null}
      <FirstLaunchOnboarding
        workflows={workflowLibrary.workflows}
        hasLoadedWorkflows={workflowLibrary.hasLoaded}
        refreshWorkflows={workflowLibrary.refreshWorkflows}
        onOpenWorkflow={openWorkflow}
        onBrowseWorkflows={() => navigate("workflows")}
      />
    </WorkflowTabsRouteProvider>
  );
}

function loadStoredAppRoute(tabs: Array<{ workflowId: string; lastActivatedAt: number }>): AppRoute {
  try {
    const raw = window.localStorage.getItem(APP_ROUTE_STORAGE_KEY);
    if (!raw) return { name: "home" };
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return { name: "home" };
    const route = parsed as Record<string, unknown>;
    if (route.name === "workflow" && typeof route.workflowId === "string" && route.workflowId.trim()) {
      if (tabs.some((tab) => tab.workflowId === route.workflowId)) {
        return { name: "workflow", workflowId: route.workflowId };
      }
      const fallback = [...tabs].sort((a, b) => b.lastActivatedAt - a.lastActivatedAt)[0];
      return fallback ? { name: "workflow", workflowId: fallback.workflowId } : { name: "home" };
    }
    if (route.name === "workflows") {
      return {
        name: "workflows",
        search: typeof route.search === "string" ? route.search : undefined,
      };
    }
    if (["home", "gallery", "history", "models", "settings"].includes(String(route.name))) {
      return { name: route.name } as PersistedAppRoute;
    }
  } catch {
    // Malformed stored routes fall back to Home.
  }
  return { name: "home" };
}

function persistedAppRoute(route: AppRoute): PersistedAppRoute | null {
  if (route.name === "dashboard-builder" || route.name === "dashboard-builder-layout") {
    return null;
  }
  return route;
}

function shouldHideImportStatusNotice(route: AppRoute, importedWorkflowId?: string) {
  if (route.name === "home" || route.name === "workflows") return true;
  if (
    importedWorkflowId &&
    (route.name === "dashboard-builder" || route.name === "dashboard-builder-layout") &&
    route.workflowId === importedWorkflowId
  ) {
    return true;
  }
  return false;
}

function workflowNeedsDashboardSetup(workflow: WorkflowSummary | null | undefined) {
  return workflow ? workflowNeedsConfiguration(workflow) : false;
}

interface WorkflowCloseDialogState {
  workflowId: string;
  workflowName: string;
  runtime: WorkflowTabRuntimeState;
  busy: boolean;
  error: string | null;
}

const activeJobStatuses = new Set(["queued", "running", "queued_pending_memory"]);

const idleTabRuntime: WorkflowTabRuntimeState = {
  activeJobId: null,
  activeJobStatus: null,
  activeJobProgress: null,
  activeJobUpdatedAt: null,
  handleSource: null,
  queueId: null,
  runnerLeaseId: null,
  runnerId: null,
};

async function activeCloseState(
  workflowId: string,
  runtime: WorkflowTabRuntimeState | undefined,
): Promise<WorkflowTabRuntimeState | null> {
  if (runtime?.handleSource === "runner_start_queue" && runtime.queueId) {
    if (runtime.activeJobStatus && activeJobStatuses.has(runtime.activeJobStatus)) return runtime;
  }

  try {
    // The workflow-scoped summary covers every active and queued generation,
    // including batch runs beyond the single handle the tab tracks.
    const summary = await fetchWorkflowActiveAndQueuedRuns(workflowId);
    return summary.total_count > 0 ? runtime ?? idleTabRuntime : null;
  } catch {
    // Summary unavailable; fall back to the tracked handle below.
  }

  if (runtime?.activeJobStatus && activeJobStatuses.has(runtime.activeJobStatus)) {
    return runtime;
  }
  const jobId = runtime?.activeJobId ?? runtime?.queueId;
  if (!runtime || !jobId) return null;
  try {
    const progress = await fetchJobProgress(jobId);
    if (activeJobStatuses.has(progress.status)) {
      return {
        ...runtime,
        activeJobId: progress.job_id,
        activeJobStatus: progress.status,
        activeJobProgress: progress,
        activeJobUpdatedAt: Date.now(),
      };
    }
  } catch {
    return null;
  }
  return null;
}

async function cancelRuntimeHandle(workflowId: string, runtime: WorkflowTabRuntimeState) {
  if (runtime.handleSource === "runner_start_queue" && runtime.queueId) {
    await cancelQueuedRunnerStart(runtime.queueId);
  }
  // Cancel every remaining active and queued generation for this workflow,
  // not just the single handle the tab tracked.
  const summary = await cancelWorkflowActiveAndQueuedRuns(workflowId);
  if (summary.failed_to_cancel_count > 0) {
    throw new Error(
      "Noofy could not stop every active or queued generation. The tab is still open so you can try again.",
    );
  }
  const remaining = await fetchWorkflowActiveAndQueuedRuns(workflowId);
  if (remaining.total_count > 0) {
    throw new Error(
      "This workflow still has active or queued generations. The tab is still open so you can try again.",
    );
  }
}

async function closeWorkflowSessionLease(workflowId: string, runtime: WorkflowTabRuntimeState | undefined) {
  if (!runtime?.runnerLeaseId) return;
  try {
    await closeWorkflowRunnerLease(workflowId, runtime.runnerLeaseId);
  } catch {
    // The lease is session runtime state; stale or already-closed leases should not block tab close.
  }
}

function fallbackRouteAfterClose(
  workflowId: string,
  tabs: Array<{ workflowId: string }>,
  route: AppRoute,
): AppRoute | null {
  if (route.name !== "workflow" || route.workflowId !== workflowId) return null;
  const index = tabs.findIndex((tab) => tab.workflowId === workflowId);
  const next = index >= 0 ? tabs[index + 1] ?? tabs[index - 1] : tabs.find((tab) => tab.workflowId !== workflowId);
  return next ? { name: "workflow", workflowId: next.workflowId } : { name: "home" };
}

function WorkflowCloseDialog({
  dialog,
  onCancel,
  onConfirm,
}: {
  dialog: WorkflowCloseDialogState;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-close-title">
      <section className="workflow-close-modal">
        <header className="workflow-close-modal__header">
          <h2 id="workflow-close-title">Stop this workflow?</h2>
          <p>
            {dialog.workflowName} still has work in progress. Closing this tab will stop its active
            generations and cancel any generations waiting in its queue.
          </p>
        </header>
        {dialog.error ? (
          <div className="notice notice--error" role="status">
            <span>{dialog.error}</span>
          </div>
        ) : null}
        <footer className="workflow-close-modal__footer">
          <button className="secondary-button" type="button" onClick={onCancel} disabled={dialog.busy}>
            Cancel
          </button>
          <button className="danger-button" type="button" onClick={onConfirm} disabled={dialog.busy}>
            {dialog.busy ? "Stopping..." : "Stop and close"}
          </button>
        </footer>
      </section>
    </div>
  );
}
