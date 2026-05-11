import { type ChangeEvent, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Download, FileUp, PackagePlus, Plus, Search, X, Users } from "lucide-react";
import { openExternalUrl } from "../../lib/openExternalUrl";

// Replace with your real Reddit community URL when ready.
const REDDIT_URL = "https://www.reddit.com/r/noofy";

import {
  fetchRuntimeStatus,
  fetchWorkflows,
  cancelImportModelDownload,
  cancelWorkflowImport,
  commitWorkflowImport,
  downloadImportMissingModels,
  fetchImportModelDownloadStatus,
  previewWorkflowPackageImport,
  type ImportModelDownloadJobStatus,
  type RequiredModelAvailability,
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
  downloadingModels: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  pendingImport: WorkflowImportResponse | null;
  allowCommunityPreparation: true;
  importResult: WorkflowImportResponse | null;
  importError: string | null;
}

const initialHomeState: HomeDataState = {
  loading: true,
  runtime: null,
  workflows: [],
  error: null,
  importing: false,
  downloadingModels: false,
  downloadJob: null,
  pendingImport: null,
  allowCommunityPreparation: true,
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
      Icon: fallbackWorkflow.Icon,
      source: "backend",
    };
  });
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
    return "Quarantined Community";
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
        downloadingModels: false,
        downloadJob: null,
        pendingImport: null,
        allowCommunityPreparation: true,
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
      downloadingModels: false,
      downloadJob: null,
      pendingImport: null,
      importResult: null,
      importError: null,
    }));

    try {
      const importResult = await previewWorkflowPackageImport(file, homeData.allowCommunityPreparation);
      if (importResult.import_session_id && importResult.model_summary && importResult.model_summary.total_count > 0) {
        if (importResult.model_summary.ready_to_run) {
          const committedImport = await commitWorkflowImport(importResult.import_session_id);
          const workflows = await fetchWorkflows();
          setHomeData((current) => ({
            ...current,
            workflows,
            importing: false,
            pendingImport: null,
            downloadJob: null,
            importResult: committedImport,
            importError: null,
          }));
          return;
        }
        setHomeData((current) => ({
          ...current,
          importing: false,
          pendingImport: importResult,
          importResult: null,
          importError: null,
        }));
        return;
      }
      const workflows = await fetchWorkflows();
      setHomeData((current) => ({
        ...current,
        workflows,
        importing: false,
        pendingImport: null,
        importResult,
        importError: null,
      }));
    } catch (error) {
      setHomeData((current) => ({
        ...current,
        importing: false,
        pendingImport: null,
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
      setHomeData((current) => ({
        ...current,
        workflows,
        importing: false,
        pendingImport: null,
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

  async function handleCancelImport() {
    const sessionId = homeData.pendingImport?.import_session_id;
    if (sessionId) {
      try {
        await cancelWorkflowImport(sessionId);
      } catch {
        // The pending import is in-memory; if the backend already forgot it, the UI can still close.
      }
    }
    setHomeData((current) => ({ ...current, pendingImport: null, downloadJob: null, importError: null }));
  }

  useEffect(() => {
    const sessionId = homeData.pendingImport?.import_session_id;
    const jobId = homeData.downloadJob?.job_id;
    const active = homeData.downloadJob?.status === "queued" || homeData.downloadJob?.status === "running";
    if (!sessionId || !jobId || !active) return;

    let stopped = false;
    let committingReadyImport = false;
    const poll = async () => {
      try {
        const status = await fetchImportModelDownloadStatus(sessionId, jobId);
        if (stopped) return;
        const finished = ["completed", "failed", "canceled"].includes(status.status);
        if (finished && status.status === "completed" && status.model_summary?.ready_to_run && !committingReadyImport) {
          committingReadyImport = true;
          const importResult = await commitWorkflowImport(sessionId);
          const workflows = await fetchWorkflows();
          if (stopped) return;
          setHomeData((current) => ({
            ...current,
            workflows,
            importing: false,
            downloadingModels: false,
            pendingImport: null,
            downloadJob: null,
            importResult,
            importError: null,
          }));
          return;
        }
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
      } catch (error) {
        if (stopped) return;
        setHomeData((current) => ({
          ...current,
          downloadingModels: false,
          importError: error instanceof Error ? error.message : String(error),
        }));
      }
    };

    void poll();
    const interval = window.setInterval(() => void poll(), 1000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [homeData.pendingImport?.import_session_id, homeData.downloadJob?.job_id, homeData.downloadJob?.status]);

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

          {homeData.pendingImport?.model_summary ? (
            <RequiredModelsModal
              importResult={homeData.pendingImport}
              busy={homeData.importing || homeData.downloadingModels}
              downloadJob={homeData.downloadJob}
              onDownload={() => void handleDownloadMissingModels()}
              onCancelDownload={() => void handleCancelModelDownload()}
              onContinue={() => void handleContinueImport()}
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
                <label className="search-field find-workflow-card__input">
                  <Search size={16} aria-hidden="true" />
                  <span className="sr-only">Search workflows</span>
                  <input type="search" placeholder="Search workflows..." />
                </label>
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
                onOpenWorkflow={onOpenWorkflow}
                onConfigureDashboard={onConfigureDashboard}
              />
            ))}
          </section>
    </AppLayout>
  );
}

function RequiredModelsModal({
  importResult,
  busy,
  downloadJob,
  onDownload,
  onCancelDownload,
  onContinue,
  onCancel,
}: {
  importResult: WorkflowImportResponse;
  busy: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  onDownload: () => void;
  onCancelDownload: () => void;
  onContinue: () => void;
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
  const jobModels = new Map(activeDownload ? downloadJob?.models.map((model) => [model.requirement_id, model]) ?? [] : []);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="required-models-title">
      <section className="required-models-modal">
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Workflow models</p>
            <h2 id="required-models-title">{importResult.workflow.name}</h2>
            <p>
              This workflow needs the following AI models. Some are already available on your computer.
              Missing models must be downloaded or selected before the workflow can run. If a download fails,
              Noofy cleans up the partial file safely; you can retry, continue importing, or cancel.
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

        <footer className="required-models-modal__footer">
          <button className="secondary-button" type="button" disabled={busy || !hasDownloadable} onClick={onDownload}>
            <Download size={16} aria-hidden="true" />
            {activeDownload ? "Downloading..." : "Download Missing Models"}
          </button>
          {activeDownload ? (
            <button className="secondary-button" type="button" onClick={onCancelDownload}>
              Cancel Download
            </button>
          ) : null}
          <button className="secondary-button" type="button" disabled={busy} onClick={onContinue}>
            Continue Without Downloading
          </button>
          <button className="ghost-button" type="button" disabled={busy} onClick={onCancel}>
            Cancel Import
          </button>
        </footer>
      </section>
    </div>
  );
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
  const percent = job.percent ?? (
    job.bytes_downloaded !== null && job.total_bytes
      ? Math.round((job.bytes_downloaded / job.total_bytes) * 100)
      : null
  );

  return (
    <div className="model-download-progress" role="status">
      <div className="model-download-progress__header">
        <strong>{label}</strong>
        <span>{percent !== null ? `${percent}%` : job.status}</span>
      </div>
      {percent !== null ? (
        <div className="model-download-progress__bar" aria-hidden="true">
          <span style={{ width: `${Math.max(0, Math.min(percent, 100))}%` }} />
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
