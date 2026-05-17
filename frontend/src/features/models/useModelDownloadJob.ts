import { useCallback, useEffect, useState } from "react";

import {
  cancelModelDownload,
  fetchActiveModelDownload,
  fetchModelDownloadStatus,
  startModelDownload,
  type ModelDownloadJobStatus,
  type ModelDownloadSelection,
} from "../../lib/api/noofyApi";

const ACTIVE_JOB_STORAGE_KEY = "noofy.models.activeDownloadJobId";

export function useModelDownloadJob(onFinished: () => void) {
  const [downloadJob, setDownloadJob] = useState<ModelDownloadJobStatus | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let mounted = true;
    const storedJobId = window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY);
    const load = storedJobId
      ? fetchModelDownloadStatus(storedJobId).catch(() => fetchActiveModelDownload().then((response) => response.job))
      : fetchActiveModelDownload().then((response) => response.job);
    load
      .then((job) => {
        if (!mounted || !job) return;
        setDownloadJob(job);
        if (["queued", "running"].includes(job.status)) {
          window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, job.job_id);
        }
      })
      .catch(() => {
        if (mounted) {
          setDownloadError("Could not check the active model download yet.");
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!downloadJob || !["queued", "running"].includes(downloadJob.status)) return;
    window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, downloadJob.job_id);
    const interval = window.setInterval(() => {
      fetchModelDownloadStatus(downloadJob.job_id)
        .then((job) => {
          setDownloadJob(job);
          setDownloadError(null);
          if (!["queued", "running"].includes(job.status)) {
            window.localStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
            onFinished();
          }
        })
        .catch((error) => {
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
      window.localStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Could not cancel the model download.");
    }
  }, [downloadJob]);

  return {
    downloadJob,
    downloadError,
    downloadBusy: starting || Boolean(downloadJob && ["queued", "running"].includes(downloadJob.status)),
    startDownload,
    cancelDownload,
  };
}
