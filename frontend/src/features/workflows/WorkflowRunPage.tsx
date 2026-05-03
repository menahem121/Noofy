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
  Square,
} from "lucide-react";

import {
  cancelJob,
  createJobEventsUrl,
  fetchJobProgress,
  fetchJobResult,
  fetchRuntimeStatus,
  isEngineJob,
  runWorkflow,
  validateWorkflow,
  type EngineJob,
  type JobProgress,
  type JobResult,
  type MemoryStatus,
  type RuntimeStatus,
  type WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";

interface WorkflowRunPageProps {
  workflowId: string;
  onBack: () => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RunPageState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  validation: WorkflowValidationResult | null;
  job: EngineJob | null;
  progress: JobProgress | null;
  result: JobResult | null;
  error: string | null;
}

const initialState: RunPageState = {
  loading: true,
  runtime: null,
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
  const [prompt, setPrompt] = useState("a cinematic photo of a mountain lake");
  const [variationId, setVariationId] = useState(5);
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);

  const isRunning = state.progress?.status === "queued" || state.progress?.status === "running";
  const isWaitingForMemory = state.job?.status === "queued_pending_memory";
  const isBlockedByMemory = state.job?.status === "blocked_by_memory";
  const status = runtimeStatusCopy({ loading: state.loading, runtime: state.runtime });

  const outputImages = useMemo(() => extractImageUrls(state.result), [state.result]);

  async function loadRequirements() {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const runtime = await fetchRuntimeStatus();
      if (!runtime.reachable) {
        setState((current) => ({ ...current, loading: false, runtime, validation: null }));
        return;
      }

      const validation = await validateWorkflow(workflowId);
      setState((current) => ({ ...current, loading: false, runtime, validation }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        runtime: null,
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
        inputs: {
          prompt,
          seed: variationId,
          width,
          height,
        },
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
    if (!state.job) {
      return;
    }

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

  return (
    <AppLayout activeRoute="workflows" status={status} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="workflow-title">
        <div>
          <button className="ghost-button ghost-button--back" type="button" onClick={onBack}>
            <ArrowLeft size={16} aria-hidden="true" />
            Back to Home
          </button>
          <p className="eyebrow">Starter workflow</p>
          <h1 id="workflow-title">Text to Image</h1>
          <p>Describe the image you want, then let Noofy run the local workflow in the background.</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => void loadRequirements()}>
          <RotateCcw size={16} aria-hidden="true" />
          Check Again
        </button>
      </section>

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

      <section className="run-workspace">
        <form className="run-panel" onSubmit={(event) => event.preventDefault()}>
          <div className="panel-heading">
            <div>
              <h2>Inputs</h2>
              <p>Keep it simple. Advanced workflow controls can come later.</p>
            </div>
          </div>

          <label className="field-group">
            <span>Prompt</span>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={7} />
          </label>

          <div className="input-grid">
            <label className="field-group">
              <span>Variation ID</span>
              <input
                type="number"
                min={0}
                value={variationId}
                onChange={(event) => setVariationId(Number(event.target.value))}
              />
            </label>
            <label className="field-group">
              <span>Width</span>
              <input
                type="range"
                min={256}
                max={1024}
                step={64}
                value={width}
                onChange={(event) => setWidth(Number(event.target.value))}
              />
              <small>{width}px</small>
            </label>
            <label className="field-group">
              <span>Height</span>
              <input
                type="range"
                min={256}
                max={1024}
                step={64}
                value={height}
                onChange={(event) => setHeight(Number(event.target.value))}
              />
              <small>{height}px</small>
            </label>
          </div>

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

          {memoryStatus ? (
            <div className={`notice ${memoryNoticeClass(memoryStatus)} notice--compact`} role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>{memoryStatusTitle(memoryStatus.state)}</strong>
                <span>{memoryStatus.message}</span>
              </div>
            </div>
          ) : null}
        </aside>
      </section>
    </AppLayout>
  );
}

function isWatchableJob(job: EngineJob) {
  return watchableJobStatuses.has(job.status);
}

function progressMessage(progress: JobProgress | null, result: JobResult | null) {
  if (result?.status === "completed") {
    return "Result saved by the local workflow.";
  }

  if (result?.status === "failed") {
    return "The local engine could not finish this run.";
  }

  if (progress?.status === "canceled") {
    return "Run canceled.";
  }

  if (progress?.status === "running") {
    return progress.message ?? "Generating image...";
  }

  if (progress?.status === "queued") {
    return "Preparing workflow...";
  }

  if (progress?.status === "queued_pending_memory") {
    return progress.message ?? "Waiting for memory.";
  }

  if (progress?.status === "blocked_by_memory") {
    return progress.message ?? "This workflow needs more memory.";
  }

  return "Run the workflow to create your first result.";
}

function memoryStatusTitle(state: string) {
  if (state === "waiting_for_gpu") {
    return "Waiting for the GPU";
  }
  if (state === "freeing_memory" || state === "waiting_for_memory_release") {
    return "Freeing memory";
  }
  if (state === "retrying_after_memory_cleanup") {
    return "Trying again";
  }
  if (state === "blocked_by_memory" || state === "memory_cleanup_failed") {
    return "Not enough memory";
  }
  if (state === "ready_warm_co_resident" || state === "ready_reusing_runner") {
    return "Ready to relaunch";
  }
  return "Memory status";
}

function memoryNoticeClass(status: MemoryStatus) {
  if (status.state === "blocked_by_memory" || status.state === "memory_cleanup_failed") {
    return "notice--error";
  }
  return "notice--warning";
}

function extractImageUrls(result: JobResult | null) {
  if (!result) {
    return [];
  }

  const urls: string[] = [];
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object" || !("images" in outputPayload)) {
      continue;
    }

    const images = outputPayload.images;
    if (!Array.isArray(images)) {
      continue;
    }

    for (const image of images) {
      if (image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
        urls.push(image.view_url);
      }
    }
  }
  return urls;
}
