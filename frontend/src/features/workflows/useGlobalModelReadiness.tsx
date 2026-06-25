import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchWorkflowModelSummary,
  fetchWorkflowModelVerificationStatus,
  startWorkflowModelVerification,
  type RequiredModelSummary,
  type WorkflowModelVerificationJobStatus,
} from "../../lib/api/noofyApi";
import { useWorkflowModelActions } from "./useWorkflowModelActions";
import {
  requiredModelSummaryHasNonReadyModels,
  requiredModelSummaryHasPendingVerification,
  WorkflowRequiredModelsModal,
} from "./workflowModelRequirements";

export interface ModelReadinessRequest {
  workflowId: string;
  workflowName: string;
  summary: RequiredModelSummary;
}

interface ModelReadinessWatch extends ModelReadinessRequest {
  job: WorkflowModelVerificationJobStatus | null;
  starting: boolean;
  error: string | null;
}

type ActiveMissingModelsModal = ModelReadinessRequest;

const emptySummary: RequiredModelSummary = {
  workflow_id: "__no-active-workflow__",
  total_count: 0,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 0,
  needs_manual_download_count: 0,
  ready_to_run: true,
  models: [],
};

export function useGlobalModelReadiness() {
  const [watches, setWatches] = useState<Record<string, ModelReadinessWatch>>({});
  const [activeModal, setActiveModal] = useState<ActiveMissingModelsModal | null>(null);

  const removeWatch = useCallback((workflowId: string) => {
    setWatches((current) => {
      const next = { ...current };
      delete next[workflowId];
      return next;
    });
  }, []);

  const openMissingModels = useCallback((request: ModelReadinessRequest) => {
    setActiveModal(request);
  }, []);

  const registerBackgroundVerification = useCallback((request: ModelReadinessRequest) => {
    if (requiredModelSummaryHasNonReadyModels(request.summary)) {
      openMissingModels(request);
      return;
    }
    if (!requiredModelSummaryHasPendingVerification(request.summary)) return;
    setWatches((current) => ({
      ...current,
      [request.workflowId]: {
        ...request,
        job: current[request.workflowId]?.job ?? null,
        starting: current[request.workflowId]?.starting ?? false,
        error: null,
      },
    }));
  }, [openMissingModels]);

  useEffect(() => {
    const entries = Object.values(watches);
    for (const watch of entries) {
      if (watch.job || watch.starting || watch.error) continue;
      setWatches((current) => ({
        ...current,
        [watch.workflowId]: { ...watch, starting: true },
      }));
      void startWorkflowModelVerification(watch.workflowId)
        .then((job) => {
          const summary = job.model_summary ?? watch.summary;
          if (requiredModelSummaryHasNonReadyModels(summary)) {
            openMissingModels({
              workflowId: watch.workflowId,
              workflowName: watch.workflowName,
              summary,
            });
            removeWatch(watch.workflowId);
            return;
          }
          if (!["queued", "running"].includes(job.status)) {
            removeWatch(watch.workflowId);
            return;
          }
          setWatches((current) => {
            const existing = current[watch.workflowId];
            if (!existing) return current;
            return {
              ...current,
              [watch.workflowId]: {
                ...existing,
                job,
                summary,
                starting: false,
                error: null,
              },
            };
          });
        })
        .catch((error) => {
          setWatches((current) => {
            const existing = current[watch.workflowId];
            if (!existing) return current;
            return {
              ...current,
              [watch.workflowId]: {
                ...existing,
                starting: false,
                error: error instanceof Error ? error.message : "Could not start local model verification.",
              },
            };
          });
        });
    }
  }, [openMissingModels, removeWatch, watches]);

  useEffect(() => {
    const activeWatches = Object.values(watches).filter((watch) => watch.job && ["queued", "running"].includes(watch.job.status));
    if (activeWatches.length === 0) return undefined;
    let stopped = false;
    let inFlight = false;
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        await Promise.all(
          activeWatches.map(async (watch) => {
            try {
              const job = watch.job;
              if (!job) return;
              const nextJob = await fetchWorkflowModelVerificationStatus(watch.workflowId, job.job_id);
              if (stopped) return;
              const nextSummary = nextJob.model_summary ?? watch.summary;
              const finished = !["queued", "running"].includes(nextJob.status);
              if (requiredModelSummaryHasNonReadyModels(nextSummary)) {
                openMissingModels({
                  workflowId: watch.workflowId,
                  workflowName: watch.workflowName,
                  summary: nextSummary,
                });
                removeWatch(watch.workflowId);
                return;
              }
              if (finished) {
                removeWatch(watch.workflowId);
                return;
              }
              setWatches((current) => {
                const existing = current[watch.workflowId];
                if (!existing) return current;
                return {
                  ...current,
                  [watch.workflowId]: {
                    ...existing,
                    job: nextJob,
                    summary: nextSummary,
                    error: null,
                  },
                };
              });
            } catch (error) {
              if (stopped) return;
              setWatches((current) => {
                const existing = current[watch.workflowId];
                if (!existing) return current;
                return {
                  ...current,
                  [watch.workflowId]: {
                    ...existing,
                    error: error instanceof Error ? error.message : "Could not check model verification progress.",
                  },
                };
              });
            }
          }),
        );
      } finally {
        inFlight = false;
      }
    };
    void poll();
    const interval = window.setInterval(() => void poll(), 800);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [openMissingModels, removeWatch, watches]);

  const activeWorkflowId = activeModal?.workflowId ?? "__no-active-workflow__";
  const activeSummary = activeModal?.summary ?? emptySummary;
  const refreshActiveSummary = useCallback(async () => {
    if (!activeModal) return;
    const summary = await fetchWorkflowModelSummary(activeModal.workflowId);
    setActiveModal((current) => current && current.workflowId === activeModal.workflowId ? { ...current, summary } : current);
  }, [activeModal]);
  const handleModelSummary = useCallback((summary: RequiredModelSummary) => {
    setActiveModal((current) => current ? { ...current, summary } : current);
  }, []);
  const {
    modelDownloadJob,
    modelDownloadError,
    modelDownloadStarting,
    modelVerificationJob,
    modelVerificationError,
    downloadRequiredModels,
    cancelRequiredModelDownload,
    startLocalModelVerification,
  } = useWorkflowModelActions({
    workflowId: activeWorkflowId,
    activeModelSummary: activeModal?.summary ?? null,
    requiredModelsModalOpen: Boolean(activeModal),
    loadRequirements: refreshActiveSummary,
    onModelSummary: handleModelSummary,
  });

  const element = useMemo(() => {
    if (!activeModal) return null;
    return (
      <WorkflowRequiredModelsModal
        workflowName={activeModal.workflowName}
        summary={activeSummary}
        downloadJob={modelDownloadJob}
        downloadError={modelDownloadError}
        downloadBusy={modelDownloadStarting}
        verificationJob={modelVerificationJob}
        verificationError={modelVerificationError}
        onDownload={() => void downloadRequiredModels()}
        onCancelDownload={() => void cancelRequiredModelDownload()}
        onRetryVerification={() => void startLocalModelVerification()}
        onClose={() => setActiveModal(null)}
      />
    );
  }, [
    activeModal,
    activeSummary,
    cancelRequiredModelDownload,
    downloadRequiredModels,
    modelDownloadError,
    modelDownloadJob,
    modelDownloadStarting,
    modelVerificationError,
    modelVerificationJob,
    startLocalModelVerification,
  ]);

  return {
    registerBackgroundVerification,
    openMissingModels,
    element,
  };
}
