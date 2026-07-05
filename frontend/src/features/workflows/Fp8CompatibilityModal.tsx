import { AlertCircle, Download, Loader2, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  cancelFp8Conversion,
  cancelModelDownload,
  dismissFp8Compatibility,
  fetchFp8ConversionStatus,
  fetchModelDownloadStatus,
  isValidFp8AlternativeUrl,
  startFp8AlternativeDownload,
  startFp8Conversion,
  type Fp8ConversionJobStatus,
  type Fp8IncompatibleModel,
  type ModelDownloadJobStatus,
  type RequiredModelSummary,
} from "../../lib/api/noofyApi";

const POLL_INTERVAL_MS = 700;

type Fp8ModalPhase = "idle" | "converting" | "downloading";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export function Fp8CompatibilityModal({
  workflowId,
  models,
  onResolved,
  onClose,
}: {
  workflowId: string;
  models: Fp8IncompatibleModel[];
  onResolved: (summary: RequiredModelSummary | null) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [phase, setPhase] = useState<Fp8ModalPhase>("idle");
  const [conversionJob, setConversionJob] = useState<Fp8ConversionJobStatus | null>(null);
  const [downloadJob, setDownloadJob] = useState<ModelDownloadJobStatus | null>(null);
  const [conversionQueue, setConversionQueue] = useState<Fp8IncompatibleModel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);
  const downloadJobIdRef = useRef<string | null>(null);
  const conversionJobIdRef = useRef<string | null>(null);
  const cancelPendingRef = useRef<Fp8ModalPhase | null>(null);
  const operationIdRef = useRef(0);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const busy = phase !== "idle";
  const selectedModel = models[Math.min(selectedIndex, models.length - 1)] ?? models[0];
  const urlValid = isValidFp8AlternativeUrl(url);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, []);

  function stopPolling() {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  function setModalPhase(nextPhase: Fp8ModalPhase) {
    busyRef.current = nextPhase !== "idle";
    setPhase(nextPhase);
  }

  async function convertNext(queue: Fp8IncompatibleModel[], operationId: number) {
    if (operationId !== operationIdRef.current) {
      return;
    }
    const [next, ...rest] = queue;
    if (!next) {
      setModalPhase("idle");
      onResolved(conversionJob?.model_summary ?? null);
      return;
    }
    setConversionQueue(rest);
    try {
      const started = await startFp8Conversion(workflowId, {
        folder: next.folder,
        filename: next.filename,
      });
      if (
        !mountedRef.current ||
        operationId !== operationIdRef.current ||
        cancelPendingRef.current === "converting"
      ) {
        if (cancelPendingRef.current === "converting") {
          cancelPendingRef.current = null;
        }
        void cancelFp8Conversion(workflowId, started.job_id).catch(() => undefined);
        if (mountedRef.current && operationId === operationIdRef.current) {
          setModalPhase("idle");
        }
        return;
      }
      conversionJobIdRef.current = started.job_id;
      pollConversion(started.job_id, rest, operationId);
    } catch (requestError) {
      if (!mountedRef.current) {
        return;
      }
      if (operationId !== operationIdRef.current) {
        return;
      }
      if (cancelPendingRef.current === "converting") {
        cancelPendingRef.current = null;
        setModalPhase("idle");
        return;
      }
      setModalPhase("idle");
      setError(errorMessage(requestError, "The conversion could not be started."));
    }
  }

  function pollConversion(jobId: string, remaining: Fp8IncompatibleModel[], operationId: number) {
    stopPolling();
    pollTimer.current = window.setInterval(async () => {
      let status: Fp8ConversionJobStatus;
      try {
        status = await fetchFp8ConversionStatus(workflowId, jobId);
      } catch {
        return;
      }
      if (!mountedRef.current || operationId !== operationIdRef.current) {
        return;
      }
      setConversionJob(status);
      if (status.status === "completed") {
        stopPolling();
        conversionJobIdRef.current = null;
        if (remaining.length > 0) {
          void convertNext(remaining, operationId);
        } else {
          setModalPhase("idle");
          onResolved(status.model_summary ?? null);
        }
      } else if (status.status === "failed") {
        stopPolling();
        conversionJobIdRef.current = null;
        setModalPhase("idle");
        setError(status.user_facing_message ?? "The model could not be converted.");
      } else if (status.status === "canceled") {
        stopPolling();
        conversionJobIdRef.current = null;
        setModalPhase("idle");
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleConvert() {
    if (busyRef.current) {
      return;
    }
    setError(null);
    cancelPendingRef.current = null;
    const operationId = operationIdRef.current + 1;
    operationIdRef.current = operationId;
    setModalPhase("converting");
    await convertNext(models, operationId);
  }

  async function handleDownload() {
    if (busyRef.current || !urlValid || !selectedModel) {
      return;
    }
    setError(null);
    setModalPhase("downloading");
    cancelPendingRef.current = null;
    const operationId = operationIdRef.current + 1;
    operationIdRef.current = operationId;
    try {
      const started = await startFp8AlternativeDownload(workflowId, {
        folder: selectedModel.folder,
        filename: selectedModel.filename,
        url: url.trim(),
      });
      if (
        !mountedRef.current ||
        operationId !== operationIdRef.current ||
        cancelPendingRef.current === "downloading"
      ) {
        if (cancelPendingRef.current === "downloading") {
          cancelPendingRef.current = null;
        }
        void cancelModelDownload(started.job_id).catch(() => undefined);
        if (mountedRef.current && operationId === operationIdRef.current) {
          setModalPhase("idle");
        }
        return;
      }
      downloadJobIdRef.current = started.job_id;
      pollDownload(started.job_id, operationId);
    } catch (requestError) {
      if (!mountedRef.current) {
        return;
      }
      if (operationId !== operationIdRef.current) {
        return;
      }
      if (cancelPendingRef.current === "downloading") {
        cancelPendingRef.current = null;
        setModalPhase("idle");
        return;
      }
      setModalPhase("idle");
      setError(errorMessage(requestError, "The download could not be started."));
    }
  }

  function pollDownload(jobId: string, operationId: number) {
    stopPolling();
    pollTimer.current = window.setInterval(async () => {
      let status: ModelDownloadJobStatus;
      try {
        status = await fetchModelDownloadStatus(jobId);
      } catch {
        return;
      }
      if (!mountedRef.current || operationId !== operationIdRef.current) {
        return;
      }
      setDownloadJob(status);
      if (status.status === "completed" || status.status === "succeeded") {
        stopPolling();
        downloadJobIdRef.current = null;
        setModalPhase("idle");
        onResolved(status.model_summary ?? null);
      } else if (["failed", "completed_with_errors", "canceled"].includes(status.status)) {
        stopPolling();
        downloadJobIdRef.current = null;
        setModalPhase("idle");
        if (status.status !== "canceled") {
          setError(status.user_facing_message ?? "The model could not be downloaded.");
        }
      }
    }, POLL_INTERVAL_MS);
  }

  function handleCancel() {
    if (phase === "converting") {
      // Use the ref, not polled state: the user may cancel before the first
      // status poll, or before the start request has returned a job id at all.
      const jobId = conversionJobIdRef.current;
      cancelPendingRef.current = "converting";
      operationIdRef.current += 1;
      if (jobId) {
        void cancelFp8Conversion(workflowId, jobId).catch(() => undefined);
        conversionJobIdRef.current = null;
        cancelPendingRef.current = null;
      }
      stopPolling();
      setModalPhase("idle");
      return;
    }
    if (phase === "downloading") {
      const jobId = downloadJobIdRef.current;
      cancelPendingRef.current = "downloading";
      operationIdRef.current += 1;
      if (jobId) {
        void cancelModelDownload(jobId).catch(() => undefined);
        downloadJobIdRef.current = null;
        cancelPendingRef.current = null;
      }
      stopPolling();
      setModalPhase("idle");
      return;
    }
    if (selectedModel) {
      void dismissFp8Compatibility(workflowId, {
        folder: selectedModel.folder,
        filename: selectedModel.filename,
      }).catch(() => undefined);
    }
    stopPolling();
    onClose();
  }

  const modelNames = models.map((model) => model.filename).join(", ");
  const conversionPercent =
    conversionJob?.percent !== null && conversionJob?.percent !== undefined
      ? Math.max(0, Math.min(conversionJob.percent, 100))
      : null;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="fp8-compatibility-title">
      <section className="required-models-modal" aria-busy={busy}>
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Apple Silicon</p>
            <h2 id="fp8-compatibility-title">Model not supported on Apple Silicon</h2>
            <p>
              {models.length === 1
                ? `${modelNames} is an FP8 model. Macs with Apple Silicon can't run FP8 models.`
                : `This workflow uses FP8 models (${modelNames}). Macs with Apple Silicon can't run FP8 models.`}
            </p>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Close Apple Silicon compatibility dialog"
            disabled={busy}
            onClick={handleCancel}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="required-models-modal__body">
          <p>
            Noofy can convert {models.length === 1 ? "it" : "them"} into a Mac-compatible version.
            The quality stays the same and the conversion only happens once, but the converted copy
            uses more disk space.
          </p>

          {phase === "converting" && conversionJob ? (
            <div className="model-download-progress model-download-progress--active" role="status">
              <div className="model-download-progress__header">
                <strong>{`Converting ${conversionJob.filename}`}</strong>
                <span className="model-download-progress__status">
                  {conversionPercent !== null ? `${Math.round(conversionPercent)}%` : "Working..."}
                </span>
              </div>
              {conversionPercent !== null ? (
                <div
                  className="model-download-progress__bar"
                  role="progressbar"
                  aria-label="Model conversion progress"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={conversionPercent}
                >
                  <div
                    className="model-download-progress__bar-fill"
                    style={{ width: `${conversionPercent}%` }}
                  />
                </div>
              ) : null}
              <span>{conversionJob.user_facing_message}</span>
            </div>
          ) : null}

          {phase === "downloading" && downloadJob ? (
            <div className="model-download-progress model-download-progress--active" role="status">
              <div className="model-download-progress__header">
                <strong>Downloading compatible model</strong>
                <span className="model-download-progress__status">
                  {downloadJob.percent !== null ? `${Math.round(downloadJob.percent)}%` : "Working..."}
                </span>
              </div>
              {downloadJob.percent !== null ? (
                <div
                  className="model-download-progress__bar"
                  role="progressbar"
                  aria-label="Model download progress"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.max(0, Math.min(downloadJob.percent, 100))}
                >
                  <div
                    className="model-download-progress__bar-fill"
                    style={{ width: `${Math.max(0, Math.min(downloadJob.percent, 100))}%` }}
                  />
                </div>
              ) : null}
              <span>{downloadJob.user_facing_message}</span>
            </div>
          ) : null}

          {error ? (
            <div className="notice notice--error notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Something went wrong</strong>
                <span>{error}</span>
              </div>
            </div>
          ) : null}

          <article className="custom-node-row custom-node-row--repository">
            <div className="custom-node-row__title">
              <strong>Already have a compatible version?</strong>
            </div>
            <p>
              Paste a download link to an FP16, BF16, or FP4 variant instead
              {models.length === 1 ? ` of ${selectedModel?.filename}.` : "."}
            </p>
            {models.length > 1 ? (
              <label className="custom-node-row__url">
                <span>Replace model</span>
                <select
                  value={selectedIndex}
                  disabled={busy}
                  onChange={(event) => setSelectedIndex(Number(event.target.value))}
                >
                  {models.map((model, index) => (
                    <option key={`${model.folder}/${model.filename}`} value={index}>
                      {model.filename}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="custom-node-row__url">
              <span>Model download link</span>
              <input
                type="url"
                value={url}
                placeholder="https://huggingface.co/.../model-bf16.safetensors"
                disabled={busy}
                onChange={(event) => setUrl(event.target.value)}
              />
            </label>
            {url.trim().length > 0 && !urlValid ? (
              <p className="custom-node-trust-note">
                Enter a direct https link to a .safetensors model file.
              </p>
            ) : null}
            <button
              className="secondary-button"
              type="button"
              disabled={busy || !urlValid}
              onClick={handleDownload}
            >
              {phase === "downloading" ? (
                <Loader2 className="spin" size={16} aria-hidden="true" />
              ) : (
                <Download size={16} aria-hidden="true" />
              )}
              Download
            </button>
          </article>

          <details className="custom-node-details">
            <summary>Developer details</summary>
            <pre>{JSON.stringify({ workflow_id: workflowId, fp8_models: models }, null, 2)}</pre>
          </details>
        </div>

        <footer className="required-models-modal__footer">
          <button
            className="primary-button"
            type="button"
            disabled={busy}
            onClick={() => void handleConvert()}
          >
            {phase === "converting" ? (
              <Loader2 className="spin" size={16} aria-hidden="true" />
            ) : (
              <Sparkles size={16} aria-hidden="true" />
            )}
            {phase === "converting" ? "Converting..." : "Convert"}
          </button>
          <button className="secondary-button" type="button" onClick={handleCancel}>
            {phase === "converting" ? "Cancel Conversion" : phase === "downloading" ? "Cancel Download" : "Cancel"}
          </button>
        </footer>
      </section>
    </div>
  );
}
