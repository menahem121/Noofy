import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Download,
  Image,
  Loader2,
  Play,
  RotateCcw,
  Share2,
  Square,
} from "lucide-react";

import {
  cancelJob,
  createJobEventsUrl,
  exportWorkflowUrl,
  fetchJobProgress,
  fetchJobResult,
  fetchRuntimeStatus,
  fetchWorkflowPackage,
  fetchWorkflowStatus,
  isEngineJob,
  runWorkflow,
  uploadDashboardAsset,
  validateWorkflow,
  type DashboardControlDef,
  type EngineJob,
  type JobProgress,
  type JobResult,
  type MemoryStatus,
  type RuntimeStatus,
  type WorkflowInputDef,
  type WorkflowOutputDef,
  type WorkflowPackageResponse,
  type WorkflowStatusResponse,
  type WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import type { GridItemLayout } from "../../lib/gridLayout";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { useWorkflowUserState } from "../../lib/useWorkflowUserState";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import { CanvasDashboardView } from "./CanvasDashboardView";
import { DashboardInputControl } from "./DashboardInputControl";

interface WorkflowRunPageProps {
  workflowId: string;
  onBack: () => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RunPageState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  workflowStatus: WorkflowStatusResponse | null;
  packageData: WorkflowPackageResponse | null;
  validation: WorkflowValidationResult | null;
  job: EngineJob | null;
  progress: JobProgress | null;
  result: JobResult | null;
  error: string | null;
}

const initialState: RunPageState = {
  loading: true,
  runtime: null,
  workflowStatus: null,
  packageData: null,
  validation: null,
  job: null,
  progress: null,
  result: null,
  error: null,
};

const terminalStatuses = new Set(["completed", "failed", "canceled"]);
const watchableJobStatuses = new Set(["queued", "running"]);

export function WorkflowRunPage({ workflowId, onBack, onNavigate }: WorkflowRunPageProps) {
  const [state, setState] = useState<RunPageState>(initialState);
  const [isEditingLayout, setIsEditingLayout] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);

  const { viewMode } = useAppPreferences();
  const isRunning = state.progress?.status === "queued" || state.progress?.status === "running";
  const isWaitingForMemory = state.job?.status === "queued_pending_memory";
  const isBlockedByMemory = state.job?.status === "blocked_by_memory";
  const status = runtimeStatusCopy({ loading: state.loading, runtime: state.runtime });

  const outputImages = useMemo(() => extractImageUrls(state.result), [state.result]);

  // Build input index from package data.
  const inputIndex = useMemo<Map<string, WorkflowInputDef>>(() => {
    const map = new Map<string, WorkflowInputDef>();
    for (const input of state.packageData?.inputs ?? []) {
      map.set(input.id, input);
    }
    return map;
  }, [state.packageData]);

  // Build output index.
  const outputIndex = useMemo<Map<string, WorkflowOutputDef>>(() => {
    const map = new Map<string, WorkflowOutputDef>();
    for (const output of state.packageData?.outputs ?? []) {
      map.set(output.id, output);
    }
    return map;
  }, [state.packageData]);

  // Collect creator defaults from package data.
  const packageDefaults = useMemo<Record<string, unknown>>(() => {
    const defaults: Record<string, unknown> = {};
    for (const input of state.packageData?.inputs ?? []) {
      defaults[input.id] = input.default;
    }
    return defaults;
  }, [state.packageData]);

  const dashboardVersion = state.packageData?.dashboard?.version ?? "";
  const allControls = useMemo(
    () => state.packageData?.dashboard?.sections.flatMap((section) => section.controls) ?? [],
    [state.packageData],
  );
  const dashboardControlIds = useMemo(() => allControls.map((control) => control.id), [allControls]);

  const {
    values: inputValues,
    setValue: setInputValue,
    restoreDefaults,
    layoutOverrides,
    setLayoutOverride,
    resetLayout,
    hasLayoutOverrides,
  } = useWorkflowUserState(workflowId, packageDefaults, dashboardVersion, inputIndex, dashboardControlIds);

  // Build output-images-by-node-id map for canvas output widgets.
  const outputImagesByNodeId = useMemo<Map<string, string[]>>(() => {
    const map = new Map<string, string[]>();
    if (!state.result) return map;
    for (const output of state.result.outputs) {
      const outputPayload = output.output;
      if (!outputPayload || typeof outputPayload !== "object") continue;
      const nodeIdKey = Object.keys(output).find((k) => k !== "output");
      const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey;
      if (!nodeId) continue;
      const images = (outputPayload as Record<string, unknown>).images;
      if (!Array.isArray(images)) continue;
      const imageUrls: string[] = [];
      for (const image of images) {
        if (image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
          imageUrls.push(image.view_url);
        }
      }
      if (imageUrls.length > 0) {
        map.set(nodeId, [...(map.get(nodeId) ?? []), ...imageUrls]);
      }
    }
    return map;
  }, [state.result]);

  async function loadRequirements() {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [runtime, workflowStatus, packageData] = await Promise.all([
        fetchRuntimeStatus(),
        fetchWorkflowStatus(workflowId).catch(() => null),
        fetchWorkflowPackage(workflowId).catch(() => null),
      ]);
      if (!runtime.reachable) {
        setState((current) => ({ ...current, loading: false, runtime, workflowStatus, packageData, validation: null }));
        return;
      }

      const validation = await validateWorkflow(workflowId);
      setState((current) => ({ ...current, loading: false, runtime, workflowStatus, packageData, validation }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        runtime: null,
        workflowStatus: null,
        packageData: null,
        validation: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  useEffect(() => {
    void loadRequirements();
    return () => {
      cleanupJobWatchers();
    };
  }, [workflowId]);

  async function handleRun() {
    if (!state.validation?.valid || !state.runtime?.reachable || isRunning) {
      return;
    }

    cleanupJobWatchers();
    setState((current) => ({ ...current, job: null, progress: null, result: null, error: null }));

    try {
      const response = await runWorkflow(workflowId, {
        inputs: inputValues as Record<string, unknown>,
        options: {},
      });

      if (!isEngineJob(response)) {
        setState((current) => ({ ...current, validation: response }));
        return;
      }

      setSubmittedJob(response);
      if (isWatchableJob(response)) {
        watchJob(response.job_id);
        await pollJobOnce(response.job_id);
      }
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleCancel() {
    if (!state.job) return;
    try {
      const progress = await cancelJob(state.job.job_id);
      cleanupJobWatchers();
      setState((current) => ({ ...current, progress }));
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleImageUpload(inputId: string, file: File) {
    try {
      const { asset_id } = await uploadDashboardAsset(workflowId, file);
      setInputValue(inputId, asset_id);
    } catch {
      // ignore — user will see the input is still empty
    }
  }

  function watchJob(jobId: string) {
    if (typeof EventSource === "undefined") {
      pollTimerRef.current = window.setInterval(() => {
        void pollJobOnce(jobId);
      }, 1000);
      return;
    }

    const source = new EventSource(createJobEventsUrl(jobId));
    eventSourceRef.current = source;
    source.addEventListener("progress", (event) => {
      setState((current) => ({ ...current, progress: JSON.parse(event.data) as JobProgress }));
    });
    source.addEventListener("result", (event) => {
      source.close();
      eventSourceRef.current = null;
      const result = JSON.parse(event.data) as JobResult | EngineJob;
      if (isEngineJob(result)) {
        setSubmittedJob(result);
        if (isWatchableJob(result)) {
          watchJob(result.job_id);
          void pollJobOnce(result.job_id);
        }
        return;
      }
      setState((current) => ({ ...current, result }));
    });
    source.onerror = () => {
      source.close();
      eventSourceRef.current = null;
      pollTimerRef.current = window.setInterval(() => {
        void pollJobOnce(jobId);
      }, 1000);
    };
  }

  function setSubmittedJob(job: EngineJob) {
    setState((current) => ({
      ...current,
      job,
      progress: {
        job_id: job.job_id,
        status: job.status,
        value: null,
        max: null,
        current_node: null,
        message: job.memory_status?.message ?? job.message ?? "Preparing workflow...",
      },
      result: null,
    }));
  }

  async function pollJobOnce(jobId: string) {
    const progress = await fetchJobProgress(jobId);
    setState((current) => ({ ...current, progress }));

    if (terminalStatuses.has(progress.status)) {
      cleanupJobWatchers();
      const result = await fetchJobResult(jobId);
      if (isEngineJob(result)) {
        setSubmittedJob(result);
        if (isWatchableJob(result)) {
          watchJob(result.job_id);
          await pollJobOnce(result.job_id);
        }
        return;
      }
      setState((current) => ({ ...current, result }));
    }
  }

  function cleanupJobWatchers() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  const missingModels = state.validation?.missing_models ?? [];
  const workflowSummary = state.workflowStatus?.workflow;
  const trust = workflowSummary?.trust;
  const memoryStatus = state.result ? null : state.job?.memory_status ?? null;
  const canRun = Boolean(
    state.validation?.valid && state.runtime?.reachable && !isRunning && !isWaitingForMemory && !isBlockedByMemory,
  );
  const canCancel = Boolean(isRunning && state.job && !isBlockedByMemory);
  const progressPercent =
    state.progress?.value !== null && state.progress?.value !== undefined && state.progress.max
      ? Math.min(100, Math.round((state.progress.value / state.progress.max) * 100))
      : state.progress?.status === "completed"
        ? 100
        : 0;

  const inputControls = allControls.filter(
    (c) => c.type !== "result_image" && c.type !== "display_image" && c.input_id,
  );

  const hasDashboard = Boolean(
    state.packageData?.dashboard?.status === "configured" && allControls.length > 0,
  );

  const pageHeader = (
    <section className="page-heading page-heading--compact" aria-labelledby="workflow-title">
      <div>
        <button className="ghost-button ghost-button--back" type="button" onClick={onBack}>
          <ArrowLeft size={16} aria-hidden="true" />
          Back to Home
        </button>
        <div className="detail-eyebrow-row">
          <p className="eyebrow">{workflowSummary?.publisher_id ?? "Starter"} workflow</p>
          {trust ? (
            <span className={`trust-badge trust-badge--${trust.badge_tone}`} title={trust.summary}>
              {trust.label}
            </span>
          ) : null}
        </div>
        <h1 id="workflow-title">{workflowSummary?.name ?? state.packageData?.metadata?.name ?? "Workflow"}</h1>
        <p>
          {workflowSummary?.description
            ? workflowSummary.description.replace(/^Milestone \d+\s*/i, "")
            : "Describe the image you want, then let Noofy run the local workflow in the background."}
        </p>
      </div>
      <div className="button-row">
        <a
          className="secondary-button"
          href={exportWorkflowUrl(workflowId)}
          download
          aria-label="Share / Save as .noofy"
        >
          <Share2 size={15} aria-hidden="true" />
          Share
        </a>
        <button className="secondary-button" type="button" onClick={() => void loadRequirements()}>
          <RotateCcw size={16} aria-hidden="true" />
          Check Again
        </button>
      </div>
    </section>
  );

  const notices = (
    <>
      {state.error ? (
        <div className="notice notice--error" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The workflow is not ready</strong>
            <span>Start the backend and engine, then try again.</span>
          </div>
        </div>
      ) : null}
      {state.runtime && !state.runtime.reachable ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The local AI engine is offline</strong>
            <span>Open Engine Settings to prepare or start the engine before running this workflow.</span>
          </div>
        </div>
      ) : null}
      {missingModels.length > 0 ? (
        <div className="notice notice--warning" role="status">
          <Download size={18} aria-hidden="true" />
          <div>
            <strong>This workflow needs one missing model</strong>
            <span>{missingModels.map((model) => model.filename).join(", ")}</span>
          </div>
        </div>
      ) : null}
      {memoryStatus ? (
        <div className={`notice ${memoryNoticeClass(memoryStatus)} notice--compact`} role="status">
          <AlertCircle size={16} aria-hidden="true" />
          <div>
            <strong>{memoryStatusTitle(memoryStatus.state)}</strong>
            <span>{memoryStatus.message}</span>
          </div>
        </div>
      ) : null}
    </>
  );

  if (hasDashboard && viewMode === "canvas") {
    return (
      <AppLayout
        activeRoute="workflows"
        status={status}
        onNavigate={onNavigate}
        mainClassName="main-workspace--canvas-run"
        contentClassName="workspace-content--canvas-run"
      >
        <CanvasDashboardView
          controls={allControls}
          inputIndex={inputIndex}
          outputIndex={outputIndex}
          outputImagesByNodeId={outputImagesByNodeId}
          inputValues={inputValues}
          layoutOverrides={layoutOverrides}
          isEditingLayout={isEditingLayout}
          hasLayoutOverrides={hasLayoutOverrides}
          runState={{
            isRunning,
            canRun,
            canCancel,
            progress: state.progress,
            progressPercent,
          }}
          onChange={(inputId, value) => setInputValue(inputId, value)}
          onImageUpload={handleImageUpload}
          onRun={() => void handleRun()}
          onCancel={() => void handleCancel()}
          onRestoreDefaults={() => void restoreDefaults()}
          onToggleEditLayout={() => setIsEditingLayout((v) => !v)}
          onResetLayout={() => void resetLayout()}
          onLayoutOverride={(controlId: string, layout: GridItemLayout) => void setLayoutOverride(controlId, layout)}
        />
      </AppLayout>
    );
  }

  return (
    <AppLayout activeRoute="workflows" status={status} onNavigate={onNavigate}>
      {pageHeader}
      {notices}

      <section className="run-workspace">
        <form className="run-panel" onSubmit={(event) => event.preventDefault()}>
          <div className="panel-heading">
            <div>
              <h2>Inputs</h2>
              <p>
                {hasDashboard
                  ? "Fill in the controls below, then click Run."
                  : "Keep it simple. Advanced workflow widgets can come later."}
              </p>
            </div>
          </div>

          {hasDashboard ? (
            <DashboardInputControls
              controls={inputControls}
              inputIndex={inputIndex}
              inputValues={inputValues}
              onChange={(id, value) => setInputValue(id, value)}
              onImageUpload={handleImageUpload}
            />
          ) : (
            <FallbackInputs
              inputValues={inputValues}
              inputs={state.packageData?.inputs ?? []}
              onChange={(id, value) => setInputValue(id, value)}
            />
          )}

          <div className="button-row">
            <button className="primary-button" type="button" disabled={!canRun} onClick={() => void handleRun()}>
              {isRunning ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <Play size={18} aria-hidden="true" />}
              Run Workflow
            </button>
            <button className="secondary-button" type="button" disabled={!canCancel} onClick={() => void handleCancel()}>
              <Square size={16} aria-hidden="true" />
              Cancel
            </button>
          </div>
        </form>

        <aside className="preview-panel">
          <div className="panel-heading">
            <div>
              <h2>Preview</h2>
              <p>{progressMessage(state.progress, state.result)}</p>
            </div>
            {state.validation?.valid ? (
              <span className="mini-status">
                <CheckCircle2 size={13} aria-hidden="true" />
                Ready
              </span>
            ) : null}
          </div>

          <div className="preview-stage">
            {outputImages[0] ? (
              <img src={outputImages[0]} alt="Generated workflow output" />
            ) : (
              <div className="preview-empty">
                <Image size={48} aria-hidden="true" />
                <span>Your generated image will appear here.</span>
              </div>
            )}
          </div>

          <div className="progress-block">
            <div className="progress-block__topline">
              <span>{state.progress?.status ?? "Not started"}</span>
              <span>{progressPercent}%</span>
            </div>
            <div className="progress-bar" aria-label="Workflow progress">
              <span style={{ width: `${progressPercent}%` }} />
            </div>
          </div>

          {state.result?.status === "failed" ? (
            <div className="notice notice--error notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Workflow failed</strong>
                <span>{state.result.error ?? "Open details for the technical error."}</span>
              </div>
            </div>
          ) : null}
        </aside>
      </section>
    </AppLayout>
  );
}

// ─── Schema-driven input controls ───────────────────────────────────────────

function DashboardInputControls({
  controls,
  inputIndex,
  inputValues,
  onChange,
  onImageUpload,
}: {
  controls: DashboardControlDef[];
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
}) {
  return (
    <>
      {controls.map((control) => {
        if (!control.input_id) return null;
        const input = inputIndex.get(control.input_id);
        if (!input) return null;
        const value = inputValues[input.id];

        return (
          <DashboardInputControl
            key={control.id}
            control={control}
            input={input}
            value={value}
            onChange={(v) => onChange(input.id, v)}
            onImageUpload={(file) => onImageUpload(input.id, file)}
          />
        );
      })}
    </>
  );
}

// ─── Fallback inputs (no configured dashboard) ──────────────────────────────

function FallbackInputs({
  inputs,
  inputValues,
  onChange,
}: {
  inputs: WorkflowInputDef[];
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
}) {
  if (inputs.length === 0) {
    return (
      <>
        <label className="field-group">
          <span>Prompt</span>
          <textarea
            value={typeof inputValues["prompt"] === "string" ? inputValues["prompt"] : ""}
            onChange={(e) => onChange("prompt", e.target.value)}
            rows={7}
          />
        </label>
        <div className="input-grid">
          <label className="field-group">
            <span>Variation ID</span>
            <input
              type="number"
              min={0}
              value={typeof inputValues["seed"] === "number" ? inputValues["seed"] : 5}
              onChange={(e) => onChange("seed", Number(e.target.value))}
            />
          </label>
          <label className="field-group">
            <span>Width</span>
            <input
              type="range"
              min={256}
              max={1024}
              step={64}
              value={typeof inputValues["width"] === "number" ? inputValues["width"] : 512}
              onChange={(e) => onChange("width", Number(e.target.value))}
            />
            <small>{typeof inputValues["width"] === "number" ? inputValues["width"] : 512}px</small>
          </label>
          <label className="field-group">
            <span>Height</span>
            <input
              type="range"
              min={256}
              max={1024}
              step={64}
              value={typeof inputValues["height"] === "number" ? inputValues["height"] : 512}
              onChange={(e) => onChange("height", Number(e.target.value))}
            />
            <small>{typeof inputValues["height"] === "number" ? inputValues["height"] : 512}px</small>
          </label>
        </div>
      </>
    );
  }

  return (
    <>
      {inputs.map((input) => {
        const value = inputValues[input.id];
        return (
          <label key={input.id} className="field-group">
            <span>{input.label}</span>
            <input
              type="text"
              value={typeof value === "string" || typeof value === "number" ? String(value) : ""}
              onChange={(e) => onChange(input.id, e.target.value)}
            />
          </label>
        );
      })}
    </>
  );
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function isWatchableJob(job: EngineJob) {
  return watchableJobStatuses.has(job.status);
}

function progressMessage(progress: JobProgress | null, result: JobResult | null) {
  if (result?.status === "completed") return "Result saved by the local workflow.";
  if (result?.status === "failed") return "The local engine could not finish this run.";
  if (progress?.status === "canceled") return "Run canceled.";
  if (progress?.status === "running") return progress.message ?? "Generating image...";
  if (progress?.status === "queued") return "Preparing workflow...";
  if (progress?.status === "queued_pending_memory") return progress.message ?? "Waiting for memory.";
  if (progress?.status === "blocked_by_memory") return progress.message ?? "This workflow needs more memory.";
  return "Run the workflow to create your first result.";
}

function memoryStatusTitle(state: string) {
  if (state === "waiting_for_gpu") return "Waiting for the GPU";
  if (state === "freeing_memory" || state === "waiting_for_memory_release") return "Freeing memory";
  if (state === "retrying_after_memory_cleanup") return "Trying again";
  if (state === "blocked_by_memory" || state === "memory_cleanup_failed") return "Not enough memory";
  if (state === "ready_warm_co_resident" || state === "ready_reusing_runner") return "Ready to relaunch";
  return "Memory status";
}

function memoryNoticeClass(status: MemoryStatus) {
  if (status.state === "blocked_by_memory" || status.state === "memory_cleanup_failed") return "notice--error";
  return "notice--warning";
}

function extractImageUrls(result: JobResult | null) {
  if (!result) return [];
  const urls: string[] = [];
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object" || !("images" in outputPayload)) continue;
    const images = outputPayload.images;
    if (!Array.isArray(images)) continue;
    for (const image of images) {
      if (image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
        urls.push(image.view_url);
      }
    }
  }
  return urls;
}
