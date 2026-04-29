import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Download,
  FileUp,
  Loader2,
  Menu,
  PackagePlus,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";

import {
  fetchRuntimeStatus,
  fetchWorkflows,
  type RuntimeStatus,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import {
  fallbackWorkflow,
  recentWorkflows,
  sidebarItems,
  starterWorkflows,
  type WorkflowCard,
  type WorkflowStatus,
} from "./homeContent";

interface HomeDataState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  workflows: WorkflowSummary[];
  error: string | null;
}

const initialHomeState: HomeDataState = {
  loading: true,
  runtime: null,
  workflows: [],
  error: null,
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

function runtimeCopy(state: HomeDataState) {
  if (state.loading) {
    return {
      label: "Checking backend",
      description: "Looking for the local app service",
      tone: "info",
    };
  }

  if (!state.runtime) {
    return {
      label: "Backend offline",
      description: "Start the Noofy backend to load live workflows",
      tone: "error",
    };
  }

  if (state.runtime.reachable) {
    return {
      label: "Engine ready",
      description: "Local workflow engine is reachable",
      tone: "success",
    };
  }

  if (state.runtime.managed_process_running) {
    return {
      label: "Engine starting",
      description: "The local engine process is still warming up",
      tone: "info",
    };
  }

  return {
    label: "Engine offline",
    description: "Open settings to start or repair the local engine",
    tone: "warning",
  };
}

function workflowCardsFromBackend(workflows: WorkflowSummary[]): WorkflowCard[] {
  return workflows.map((workflow) => ({
    id: workflow.id,
    title: workflow.name,
    description: friendlyDescription(workflow),
    category: "Installed",
    status: "installed",
    statusLabel: "Installed",
    Icon: fallbackWorkflow.Icon,
    source: "backend",
  }));
}

export function HomePage() {
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
      });
    }

    void loadHomeData();

    return () => {
      mounted = false;
    };
  }, []);

  const status = runtimeCopy(homeData);

  const workflowCards = useMemo(() => {
    const backendCards = workflowCardsFromBackend(homeData.workflows);
    const fallbackCards = backendCards.length > 0 ? backendCards : [fallbackWorkflow];
    const starterWithoutDuplicates = starterWorkflows.filter(
      (starter) => !fallbackCards.some((card) => card.id === starter.id),
    );

    return [...fallbackCards, ...starterWithoutDuplicates].slice(0, 8);
  }, [homeData.workflows]);

  const installedCount = homeData.workflows.length;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__brand">
          <button className="icon-button topbar__menu" type="button" aria-label="Open navigation" title="Open navigation">
            <Menu size={20} aria-hidden="true" />
          </button>
          <div className="brand-mark" aria-hidden="true">
            <ShieldCheck size={17} />
          </div>
          <span className="brand-name">Noofy</span>
        </div>

        <label className="search-field">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">Search workflows</span>
          <input type="search" placeholder="Search workflows..." />
        </label>

        <div className="topbar__actions">
          <div className={`status-pill status-pill--${status.tone}`}>
            {homeData.loading ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <span />}
            <span>{status.label}</span>
          </div>
          <button className="icon-button" type="button" aria-label="Open settings" title="Open settings">
            <Settings size={19} aria-hidden="true" />
          </button>
        </div>
      </header>

      <aside className="sidebar">
        <div className="workspace-card">
          <div className="workspace-card__avatar" aria-hidden="true">
            <ShieldCheck size={19} />
          </div>
          <div>
            <p>AI Workspace</p>
            <span>{status.label}</span>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="Main navigation">
          {sidebarItems.map(({ label, Icon, active }) => (
            <button
              className={active ? "sidebar-nav__item sidebar-nav__item--active" : "sidebar-nav__item"}
              type="button"
              key={label}
            >
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar__spacer" />

        <div className="engine-card">
          <div className="engine-card__header">
            <span className={`engine-dot engine-dot--${status.tone}`} />
            <span>Local engine</span>
          </div>
          <p>{status.description}</p>
          <button className="secondary-button secondary-button--full" type="button">
            <SlidersHorizontal size={16} aria-hidden="true" />
            Engine Settings
          </button>
        </div>
      </aside>

      <main className="main-workspace">
        <div className="workspace-content">
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
                <input className="sr-only" type="file" accept=".json,.noofy" />
                <FileUp size={16} aria-hidden="true" />
                Choose File
              </label>
            </article>

            <article className="action-card action-card--accent">
              <div className="action-card__icon action-card__icon--accent">
                <PackagePlus size={26} aria-hidden="true" />
              </div>
              <div>
                <h2>Create Workflow Interface</h2>
                <p>Pick the controls beginners should see and keep the complex parts hidden.</p>
              </div>
              <button className="primary-button primary-button--compact" type="button">
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
              <WorkflowCardView key={workflow.id} workflow={workflow} />
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
                  <button className="secondary-button secondary-button--small" type="button">
                    Open
                  </button>
                </article>
              ))}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

function WorkflowCardView({ workflow }: { workflow: WorkflowCard }) {
  const StatusIcon = workflowIconStatus(workflow.status);

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
        <button className="icon-button icon-button--card" type="button" aria-label={`Open ${workflow.title}`}>
          <ArrowRight size={17} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}
