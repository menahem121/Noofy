import { useEffect, useRef, useState } from "react";

import {
  cancelModelDownload,
  fetchModelDownloadStatus,
  fetchWorkflowModelVerificationStatus,
  startModelDownload,
  startWorkflowModelVerification,
  type ModelDownloadJobStatus,
  type RequiredModelSummary,
  type WorkflowModelVerificationJobStatus,
} from "../../lib/api/noofyApi";
import { isModelDownloadActive } from "../../lib/modelDownloadProgress";
import {
  hasVerifiableLocalModels,
  requiredModelDownloadSelections,
} from "./workflowModelRequirements";

export function useWorkflowModelActions({
  workflowId,
  activeModelSummary,
  requiredModelsModalOpen,
  loadRequirements,
  onModelSummary,
}: {
  workflowId: string;
  activeModelSummary: RequiredModelSummary | null;
  requiredModelsModalOpen: boolean;
  loadRequirements: () => void | Promise<void>;
  onModelSummary: (summary: RequiredModelSummary) => void;
}) {
  const [modelDownloadJob, setModelDownloadJob] = useState<ModelDownloadJobStatus | null>(null);
  const [modelDownloadError, setModelDownloadError] = useState<string | null>(null);
  const [modelDownloadStarting, setModelDownloadStarting] = useState(false);
  const [modelVerificationJob, setModelVerificationJob] = useState<WorkflowModelVerificationJobStatus | null>(null);
  const [modelVerificationError, setModelVerificationError] = useState<string | null>(null);
  const modelVerificationStartInFlightRef = useRef(false);
  const loadRequirementsRef = useRef(loadRequirements);
  const onModelSummaryRef = useRef(onModelSummary);

  useEffect(() => {
    loadRequirementsRef.current = loadRequirements;
    onModelSummaryRef.current = onModelSummary;
  }, [loadRequirements, onModelSummary]);

  useEffect(() => {
    setModelDownloadJob(null);
    setModelDownloadError(null);
    setModelDownloadStarting(false);
    setModelVerificationJob(null);
    setModelVerificationError(null);
  }, [workflowId]);

  useEffect(() => {
    if (!modelDownloadJob || !isModelDownloadActive(modelDownloadJob.status)) return;
    const interval = window.setInterval(() => {
      fetchModelDownloadStatus(modelDownloadJob.job_id)
        .then((job) => {
          handleModelDownloadStatus(job);
        })
        .catch((error) => {
          setModelDownloadError(error instanceof Error ? error.message : "Could not check model download progress.");
        });
    }, 700);
    return () => window.clearInterval(interval);
  }, [modelDownloadJob?.job_id, modelDownloadJob?.status]);

  useEffect(() => {
    if (!requiredModelsModalOpen || !hasVerifiableLocalModels(activeModelSummary)) return;
    if (modelVerificationJob || modelVerificationError) return;
    let canceled = false;
    void startLocalModelVerification(() => canceled);
    return () => {
      canceled = true;
    };
  }, [activeModelSummary, modelVerificationError, modelVerificationJob, requiredModelsModalOpen, workflowId]);

  useEffect(() => {
    if (!modelVerificationJob || !["queued", "running"].includes(modelVerificationJob.status)) return;
    const interval = window.setInterval(() => {
      fetchWorkflowModelVerificationStatus(workflowId, modelVerificationJob.job_id)
        .then((job) => {
          setModelVerificationJob(job);
          setModelVerificationError(null);
          if (!["queued", "running"].includes(job.status)) {
            if (job.model_summary) {
              onModelSummaryRef.current(job.model_summary);
            }
            void loadRequirementsRef.current();
          }
        })
        .catch((error) => {
          setModelVerificationError(error instanceof Error ? error.message : "Could not check model verification progress.");
        });
    }, 800);
    return () => window.clearInterval(interval);
  }, [modelVerificationJob?.job_id, modelVerificationJob?.status, workflowId]);

  async function downloadRequiredModels() {
    const selections = requiredModelDownloadSelections(activeModelSummary, workflowId);
    if (selections.length === 0) return;
    setModelDownloadStarting(true);
    setModelDownloadError(null);
    setModelVerificationJob(null);
    setModelVerificationError(null);
    try {
      const started = await startModelDownload(selections);
      const status = await fetchModelDownloadStatus(started.job_id);
      handleModelDownloadStatus(status);
    } catch (error) {
      setModelDownloadError(error instanceof Error ? error.message : "Could not start the model download.");
    } finally {
      setModelDownloadStarting(false);
    }
  }

  async function cancelRequiredModelDownload() {
    if (!modelDownloadJob) return;
    try {
      setModelDownloadJob(await cancelModelDownload(modelDownloadJob.job_id));
    } catch (error) {
      setModelDownloadError(error instanceof Error ? error.message : "Could not cancel the model download.");
    }
  }

  function handleModelDownloadStatus(job: ModelDownloadJobStatus) {
    setModelDownloadJob(job);
    setModelDownloadError(null);
    if (job.model_summary) {
      onModelSummaryRef.current(job.model_summary);
    }
    if (!isModelDownloadActive(job.status)) {
      void loadRequirementsRef.current();
    }
  }

  async function startLocalModelVerification(isCanceled: () => boolean = () => false) {
    if (modelVerificationStartInFlightRef.current) return;
    modelVerificationStartInFlightRef.current = true;
    setModelVerificationJob(null);
    setModelVerificationError(null);
    try {
      const job = await startWorkflowModelVerification(workflowId);
      if (!isCanceled()) setModelVerificationJob(job);
    } catch (error) {
      if (!isCanceled()) {
        setModelVerificationError(error instanceof Error ? error.message : "Could not start local model verification.");
      }
    } finally {
      modelVerificationStartInFlightRef.current = false;
    }
  }

  return {
    modelDownloadJob,
    modelDownloadError,
    modelDownloadStarting,
    modelVerificationJob,
    modelVerificationError,
    downloadRequiredModels,
    cancelRequiredModelDownload,
    startLocalModelVerification,
  };
}
