import { useEffect, useRef, useState } from "react";

import type { AppNavigateOptions, AppRouteId } from "./features/app/AppLayout";
import { SidebarProvider } from "./features/app/AppLayout";
import { RuntimeStatusProvider } from "./features/app/RuntimeStatusProvider";
import { WorkflowTabsProvider, WorkflowTabsRouteProvider, useWorkflowTabs, type WorkflowTabRuntimeState } from "./features/app/WorkflowTabs";
import type { DashboardSchema } from "./features/dashboard-builder/dashboardBuilderContent";
import { DashboardBuilderPage } from "./features/dashboard-builder/DashboardBuilderPage";
import { DashboardBuilderLayoutPage } from "./features/dashboard-builder/DashboardBuilderLayoutPage";
import { EngineSettingsPage } from "./features/settings/EngineSettingsPage";
import { GalleryPage } from "./features/gallery/GalleryPage";
import { HistoryPage } from "./features/history/HistoryPage";
import { HomePage } from "./features/home/HomePage";
import { WorkflowLibraryProvider, useWorkflowLibrary } from "./features/home/WorkflowLibraryProvider";
import { ModelsPage } from "./features/models/ModelsPage";
import { FirstLaunchOnboarding } from "./features/onboarding/FirstLaunchOnboarding";
import { WorkflowRunPage } from "./features/workflows/WorkflowRunPage";
import { WorkflowsPage } from "./features/workflows/WorkflowsPage";
import {
  cancelJob,
  cancelQueuedRunnerStart,
  closeWorkflowRunnerLease,
  fetchJobProgress,
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

type AppRoute =
  | { name: "home" }
  | { name: "workflows"; search?: string }
  | { name: "gallery" }
  | { name: "history" }
  | { name: "models" }
  | { name: "settings" }
  | { name: "workflow"; workflowId: string }
  | { name: "dashboard-builder"; workflowId?: string; workflowName?: string; initialSchema?: DashboardSchema }
  | { name: "dashboard-builder-layout"; workflowId?: string; workflowName?: string; initialSchema?: DashboardSchema };

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
  const [route, setRoute] = useState<AppRoute>({ name: "home" });
  const [closeDialog, setCloseDialog] = useState<WorkflowCloseDialogState | null>(null);
  const [nativeWorkflowImport, setNativeWorkflowImport] = useState<NativeWorkflowImportRequest | null>(null);
  const nativeWorkflowImportIdRef = useRef(0);
  const workflowLibrary = useWorkflowLibrary();
  const workflowTabs = useWorkflowTabs();

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

  async function requestCloseWorkflowTab(workflowId: string) {
    const refreshed = await activeCloseState(workflowTabs.runtimeByWorkflowId[workflowId]);
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
      await cancelRuntimeHandle(closeDialog.runtime);
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
            })
          }
          onConfigureDashboard={(workflowId, workflowName) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: workflowId ?? undefined,
              workflowName: workflowName ?? undefined,
            })
          }
          onNavigate={navigate}
        />
      );
    }

    if (route.name === "dashboard-builder") {
      return (
        <DashboardBuilderPage
          workflowId={route.workflowId}
          workflowName={route.workflowName}
          initialSchema={route.initialSchema}
          onBack={() => setRoute({ name: "home" })}
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
          onConfigureDashboard={(workflowId, workflowName) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: workflowId ?? undefined,
              workflowName: workflowName ?? undefined,
            })
          }
          onEditWidgets={(schema) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: schema.workflowId,
              workflowName: schema.workflowName,
              initialSchema: schema,
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
        nativeImportRequest={nativeWorkflowImport}
        onConfigureDashboard={(workflowId, workflowName) =>
          setRoute({
            name: "dashboard-builder",
            workflowId: workflowId ?? undefined,
            workflowName: workflowName ?? undefined,
          })
        }
        onEditWidgets={(schema) =>
          setRoute({
            name: "dashboard-builder",
            workflowId: schema.workflowId,
            workflowName: schema.workflowName,
            initialSchema: schema,
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
      {renderPage()}
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

function workflowNeedsDashboardSetup(workflow: WorkflowSummary | null | undefined) {
  if (!workflow) return false;
  if (workflow.dashboard_ready === false) return true;
  if (workflow.dashboard_status && workflow.dashboard_status !== "configured") return true;
  if ((workflow.unresolved_input_count ?? 0) > 0) return true;
  return workflow.status === "needs_input_setup" || workflow.status === "prepared_needs_input_setup";
}

interface WorkflowCloseDialogState {
  workflowId: string;
  workflowName: string;
  runtime: WorkflowTabRuntimeState;
  busy: boolean;
  error: string | null;
}

const activeJobStatuses = new Set(["queued", "running", "queued_pending_memory"]);

async function activeCloseState(
  runtime: WorkflowTabRuntimeState | undefined,
): Promise<WorkflowTabRuntimeState | null> {
  if (!runtime?.handleSource) return null;

  if (runtime.handleSource === "runner_start_queue" && runtime.queueId) {
    return runtime.activeJobStatus && activeJobStatuses.has(runtime.activeJobStatus) ? runtime : null;
  }

  const jobId = runtime.activeJobId ?? runtime.queueId;
  if (!jobId) return null;
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

async function cancelRuntimeHandle(runtime: WorkflowTabRuntimeState) {
  if (runtime.handleSource === "runner_start_queue" && runtime.queueId) {
    await cancelQueuedRunnerStart(runtime.queueId);
    return;
  }
  const jobId = runtime.activeJobId ?? runtime.queueId;
  if (jobId) await cancelJob(jobId);
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
            {dialog.workflowName} is still working. Closing the tab now will stop the current generation.
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
