import { type ChangeEvent, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Download, FileUp, PackagePlus, Plus } from "lucide-react";

import {
  fetchRuntimeStatus,
  fetchWorkflows,
  importWorkflowPackage,
  type RuntimeStatus,
  type WorkflowImportResponse,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import {
  fallbackWorkflow,
  recentWorkflows,
  starterWorkflows,
  type WorkflowCard,
  type WorkflowStatus,
} from "./homeContent";

interface HomeDataState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  workflows: WorkflowSummary[];
  error: string | null;
  importing: boolean;
  importResult: WorkflowImportResponse | null;
  importError: string | null;
}

const initialHomeState: HomeDataState = {
  loading: true,
  runtime: null,
  workflows: [],
  error: null,
  importing: false,
  importResult: null,
  importError: null,
};

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
  return workflows.map((workflow) => ({
    id: workflow.id,
    title: workflow.name,
    description: friendlyDescription(workflow),
    category: workflow.trust_level === "quarantined_community" ? "Imported" : "Installed",
    status: workflowStatusFromSummary(workflow),
    statusLabel: workflow.status_label ?? workflowStatusLabel(workflowStatusFromSummary(workflow)),
    Icon: fallbackWorkflow.Icon,
    source: "backend",
  }));
}

function workflowStatusFromSummary(workflow: WorkflowSummary): WorkflowStatus {
  if (workflow.status === "needs_input_setup") {
    return "needs_input_setup";
  }

  if (workflow.status === "cannot_prepare_automatically") {
    return "cannot_prepare_automatically";
  }

  if (workflow.status === "imported") {
    return "imported";
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

interface HomePageProps {
  onOpenWorkflow: (workflowId: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  onNavigate: (route: AppRouteId) => void;
}

export function HomePage({ onOpenWorkflow, onConfigureDashboard, onNavigate }: HomePageProps) {
  const [homeData, setHomeData] = useState<HomeDataState>(initialHomeState);

  useEffect(() => {
    let mounted = true;

    async function loadHomeData() {
      const [runtimeResult, workflowsResult] = await Promise.allSettled([
        fetchRuntimeStatus(),
        fetchWorkflows(),
      ]);

      if (!mounted) {
        return;
      }

      const runtime = runtimeResult.status === "fulfilled" ? runtimeResult.value : null;
      const workflows = workflowsResult.status === "fulfilled" ? workflowsResult.value : [];
      const firstError =
        runtimeResult.status === "rejected"
          ? runtimeResult.reason
          : workflowsResult.status === "rejected"
            ? workflowsResult.reason
            : null;

      setHomeData({
        loading: false,
        runtime,
        workflows,
        error: firstError instanceof Error ? firstError.message : firstError ? String(firstError) : null,
        importing: false,
        importResult: null,
        importError: null,
      });
    }

    void loadHomeData();

    return () => {
      mounted = false;
    };
  }, []);

  const status = runtimeStatusCopy(homeData);

  const workflowCards = useMemo(() => {
    const backendCards = workflowCardsFromBackend(homeData.workflows);
    const fallbackCards = backendCards.length > 0 ? backendCards : [fallbackWorkflow];
    const starterWithoutDuplicates = starterWorkflows.filter(
      (starter) => !fallbackCards.some((card) => card.id === starter.id),
    );

    return [...fallbackCards, ...starterWithoutDuplicates].slice(0, 8);
  }, [homeData.workflows]);

  const installedCount = homeData.workflows.length;

  async function handleWorkflowFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) {
      return;
    }

    setHomeData((current) => ({
      ...current,
      importing: true,
      importResult: null,
      importError: null,
    }));

    try {
      const importResult = await importWorkflowPackage(file);
      const workflows = await fetchWorkflows();
      setHomeData((current) => ({
        ...current,
        workflows,
        importing: false,
        importResult,
        importError: null,
      }));
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        importing: false,
        importResult: null,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  return (
    <AppLayout activeRoute="home" status={status} onNavigate={onNavigate}>
          <section className="page-heading" aria-labelledby="home-title">
            <div>
              <p className="eyebrow">Private local workflows</p>
              <h1 id="home-title">Choose a workflow</h1>
              <p>
                Start with a ready-made workflow, open one from your computer, or create a simple
                interface for a workflow you already use.
              </p>
            </div>
            <button className="primary-button" type="button">
              <Plus size={18} aria-hidden="true" />
              New Workflow
            </button>
          </section>

          {homeData.error ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Backend is not reachable</strong>
                <span>The page is showing starter content until the local backend is running.</span>
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
                <PackagePlus size={26} aria-hidden="true" />
              </div>
              <div>
                <h2>Create Workflow Interface</h2>
                <p>Pick the widgets beginners should see and keep the complex parts hidden.</p>
              </div>
              <button
                className="primary-button primary-button--compact"
                type="button"
                onClick={() => onConfigureDashboard?.()}
              >
                <PackagePlus size={16} aria-hidden="true" />
                Create Interface
              </button>
            </article>
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
                onOpenWorkflow={onOpenWorkflow}
                onConfigureDashboard={onConfigureDashboard}
              />
            ))}
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
    </AppLayout>
  );
}

function WorkflowCardView({
  workflow,
  onOpenWorkflow,
  onConfigureDashboard,
}: {
  workflow: WorkflowCard;
  onOpenWorkflow: (workflowId: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
}) {
  const StatusIcon = workflowIconStatus(workflow.status);
  const needsSetup =
    workflow.status === "needs_input_setup" || workflow.status === "cannot_prepare_automatically";
  const canOpen = workflow.source === "backend" || workflow.id === "text_to_image_v0";

  function handleClick() {
    if (needsSetup) {
      onConfigureDashboard?.(workflow.id, workflow.title);
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
        <span className="category-badge">{workflow.category}</span>
      </div>
      <h3>{workflow.title}</h3>
      <p>{workflow.description}</p>
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
