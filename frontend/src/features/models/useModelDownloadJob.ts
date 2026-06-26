import { useCallback, useEffect, useState } from "react";

import {
  cancelModelDownload,
  fetchActiveModelDownload,
  fetchModelDownloadStatus,
  isApiError,
  startModelDownload,
  type ModelDownloadJobStatus,
  type ModelDownloadSelection,
} from "../../lib/api/noofyApi";
import { isModelDownloadActive } from "../../lib/modelDownloadProgress";

const ACTIVE_JOB_STORAGE_KEY = "noofy.models.activeDownloadJobId";

function clearStoredDownloadJob() {
  window.localStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
}

function isMissingDownloadJobError(error: unknown) {
  return isApiError(error) && error.status === 404;
}

export function useModelDownloadJob(onFinished: () => void) {
  const [downloadJob, setDownloadJob] = useState<ModelDownloadJobStatus | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let mounted = true;
    fetchActiveModelDownload()
      .then((response) => response.job)
      .then((job) => {
        if (!mounted) return;
        if (!job) {
          clearStoredDownloadJob();
          return;
        }
        setDownloadJob(job);
        if (isModelDownloadActive(job.status)) {
          window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, job.job_id);
        } else {
          clearStoredDownloadJob();
        }
      })
      .catch(() => {
        if (mounted) {
          setDownloadError("Could not check the current model download yet.");
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!downloadJob || !isModelDownloadActive(downloadJob.status)) return;
    window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, downloadJob.job_id);
    const interval = window.setInterval(() => {
      fetchModelDownloadStatus(downloadJob.job_id)
        .then((job) => {
          setDownloadJob(job);
          setDownloadError(null);
          if (!isModelDownloadActive(job.status)) {
            clearStoredDownloadJob();
            onFinished();
          }
        })
        .catch((error) => {
          if (isMissingDownloadJobError(error)) {
            setDownloadJob(null);
            setDownloadError(null);
            clearStoredDownloadJob();
            return;
          }
          setDownloadError(error instanceof Error ? error.message : "Could not check model download progress.");
        });
    }, 700);
    return () => window.clearInterval(interval);
  }, [downloadJob?.job_id, downloadJob?.status, onFinished]);

  const startDownload = useCallback(async (selections: ModelDownloadSelection[]) => {
    if (selections.length === 0) return;
    setStarting(true);
    setDownloadError(null);
    try {
      const started = await startModelDownload(selections);
      window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, started.job_id);
      const status = await fetchModelDownloadStatus(started.job_id);
      setDownloadJob(status);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Could not start the model download.");
    } finally {
      setStarting(false);
    }
  }, []);

  const cancelDownload = useCallback(async () => {
    if (!downloadJob) return;
    try {
      setDownloadJob(await cancelModelDownload(downloadJob.job_id));
      clearStoredDownloadJob();
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Could not cancel the model download.");
    }
  }, [downloadJob]);

  return {
    downloadJob,
    downloadError,
    downloadBusy: starting || Boolean(downloadJob && isModelDownloadActive(downloadJob.status)),
    startDownload,
    cancelDownload,
  };
}
