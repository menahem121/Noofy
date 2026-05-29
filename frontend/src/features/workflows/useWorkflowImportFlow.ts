import { useCallback, useEffect, useState } from "react";

import {
  cancelImportModelDownload,
  cancelWorkflowImport,
  commitWorkflowImport,
  downloadImportMissingModels,
  fetchImportModelDownloadStatus,
  fetchImportModelVerificationStatus,
  fetchWorkflows,
  previewWorkflowPackageImport,
  type ImportModelDownloadJobStatus,
  type ImportModelVerificationJobStatus,
  type WorkflowImportResponse,
} from "../../lib/api/noofyApi";
import { isModelDownloadActive } from "../../lib/modelDownloadProgress";
import { workflowDisplayName } from "../../lib/workflowNames";
import { useWorkflowLibrary } from "../home/WorkflowLibraryProvider";
import { importNeedsConfiguration } from "./workflowImportUtils";

interface WorkflowImportFlowState {
  importing: boolean;
  downloadingModels: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  verificationJob: ImportModelVerificationJobStatus | null;
  pendingImport: WorkflowImportResponse | null;
  importResult: WorkflowImportResponse | null;
  importError: string | null;
}

const initialWorkflowImportFlowState: WorkflowImportFlowState = {
  importing: false,
  downloadingModels: false,
  downloadJob: null,
  verificationJob: null,
  pendingImport: null,
  importResult: null,
  importError: null,
};

export function useWorkflowImportFlow({
  onOpenWorkflow,
  onConfigureDashboard,
  allowUnverifiedCommunityPreparation = true,
}: {
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  allowUnverifiedCommunityPreparation?: boolean;
}) {
  const workflowLibrary = useWorkflowLibrary();
  const [state, setState] = useState<WorkflowImportFlowState>(initialWorkflowImportFlowState);

  const failImport = useCallback((message: string) => {
    setState((current) => ({
      ...current,
      importing: false,
      downloadingModels: false,
      downloadJob: null,
      verificationJob: null,
      pendingImport: null,
      importResult: null,
      importError: message,
    }));
  }, []);

  const startWorkflowImport = useCallback(async (file: File) => {
    setState((current) => ({
      ...current,
      importing: true,
      downloadingModels: false,
      downloadJob: null,
      verificationJob: null,
      pendingImport: null,
      importResult: null,
      importError: null,
    }));

    try {
      const importResult = await previewWorkflowPackageImport(file, allowUnverifiedCommunityPreparation);
      if (
        importResult.import_session_id &&
        (
          importResult.duplicate_identity ||
          (importResult.model_summary && importResult.model_summary.total_count > 0)
        )
      ) {
        setState((current) => ({
          ...current,
          importing: false,
          verificationJob: null,
          pendingImport: importResult,
          importResult: null,
          importError: null,
        }));
        return;
      }
      const workflows = await fetchWorkflows();
      workflowLibrary.setWorkflowsFromResponse(workflows);
      setState((current) => ({
        ...current,
        importing: false,
        pendingImport: null,
        verificationJob: null,
        importResult,
        importError: null,
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        pendingImport: null,
        verificationJob: null,
        importResult: null,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [allowUnverifiedCommunityPreparation, workflowLibrary.setWorkflowsFromResponse]);

  const downloadMissingModels = useCallback(async () => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, downloadingModels: true, downloadJob: null, importError: null }));
    try {
      const job = await downloadImportMissingModels(sessionId);
      setState((current) => ({
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
      setState((current) => ({
        ...current,
        downloadingModels: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [state.pendingImport?.import_session_id]);

  const cancelModelDownload = useCallback(async () => {
    const sessionId = state.pendingImport?.import_session_id;
    const jobId = state.downloadJob?.job_id;
    if (!sessionId || !jobId) return;
    try {
      const status = await cancelImportModelDownload(sessionId, jobId);
      setState((current) => ({
        ...current,
        downloadingModels: isModelDownloadActive(status.status),
        downloadJob: status,
        importError: status.user_facing_message,
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [state.downloadJob?.job_id, state.pendingImport?.import_session_id]);

  const finishImport = useCallback(async (importResult: WorkflowImportResponse, openAfterImport: boolean) => {
    const workflows = await fetchWorkflows();
    workflowLibrary.setWorkflowsFromResponse(workflows);
    setState((current) => ({
      ...current,
      importing: false,
      downloadingModels: false,
      pendingImport: null,
      downloadJob: null,
      verificationJob: null,
      importResult,
      importError: null,
    }));
    if (openAfterImport) {
      if (importNeedsConfiguration(importResult) && onConfigureDashboard) {
        onConfigureDashboard(importResult.workflow.id, workflowDisplayName(importResult.workflow));
        return;
      }
      onOpenWorkflow(importResult.workflow.id);
    }
  }, [onConfigureDashboard, onOpenWorkflow, workflowLibrary.setWorkflowsFromResponse]);

  const continueImport = useCallback(async () => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, importing: true, importError: null }));
    try {
      const importResult = await commitWorkflowImport(sessionId);
      await finishImport(importResult, false);
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [finishImport, state.pendingImport?.import_session_id]);

  const duplicateImport = useCallback(async (action: "replace" | "copy") => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, importing: true, importError: null }));
    try {
      const importResult = await commitWorkflowImport(sessionId, action);
      await finishImport(importResult, true);
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [finishImport, state.pendingImport?.import_session_id]);

  const readyImportAction = useCallback(async () => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, importing: true, downloadingModels: false, importError: null }));
    try {
      const importResult = await commitWorkflowImport(sessionId);
      await finishImport(importResult, true);
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [finishImport, state.pendingImport?.import_session_id]);

  const cancelImport = useCallback(async () => {
    const sessionId = state.pendingImport?.import_session_id;
    if (sessionId) {
      try {
        await cancelWorkflowImport(sessionId);
      } catch {
        // Pending import sessions can expire; the modal can still close locally.
      }
    }
    setState((current) => ({
      ...current,
      pendingImport: null,
      downloadJob: null,
      verificationJob: null,
      importError: null,
    }));
  }, [state.pendingImport?.import_session_id]);

  useEffect(() => {
    const sessionId = state.pendingImport?.import_session_id;
    const verifying =
      state.verificationJob?.status === "queued" ||
      state.verificationJob?.status === "running" ||
      state.pendingImport?.model_summary?.models.some((model) => model.status === "checking");
    if (!sessionId || !verifying) return;

    let stopped = false;
    let inFlight = false;
    let interval: number | null = null;
    const stopPolling = () => {
      stopped = true;
      if (interval !== null) {
        window.clearInterval(interval);
      }
    };
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        const status = await fetchImportModelVerificationStatus(sessionId);
        if (stopped) return;
        const finished = ["completed", "failed"].includes(status.status);
        setState((current) => ({
          ...current,
          verificationJob: status,
          pendingImport:
            status.model_summary && current.pendingImport
              ? { ...current.pendingImport, model_summary: status.model_summary }
              : current.pendingImport,
          importError: finished && status.status !== "completed" ? status.user_facing_message : current.importError,
        }));
        if (finished) stopPolling();
      } catch (error) {
        if (stopped) return;
        stopPolling();
        setState((current) => ({
          ...current,
          importError: error instanceof Error ? error.message : String(error),
        }));
      } finally {
        inFlight = false;
      }
    };

    void poll();
    interval = window.setInterval(() => void poll(), 800);
    return stopPolling;
  }, [
    state.pendingImport?.import_session_id,
    state.pendingImport?.model_summary,
    state.verificationJob?.status,
  ]);

  useEffect(() => {
    const sessionId = state.pendingImport?.import_session_id;
    const jobId = state.downloadJob?.job_id;
    const active = isModelDownloadActive(state.downloadJob?.status);
    if (!sessionId || !jobId || !active) return;

    let stopped = false;
    let inFlight = false;
    let interval: number | null = null;
    const stopPolling = () => {
      stopped = true;
      if (interval !== null) {
        window.clearInterval(interval);
      }
    };
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        const status = await fetchImportModelDownloadStatus(sessionId, jobId);
        if (stopped) return;
        const finished = ["completed", "completed_with_errors", "failed", "canceled"].includes(status.status);
        setState((current) => ({
          ...current,
          downloadingModels: !finished,
          downloadJob: status,
          pendingImport:
            status.model_summary && current.pendingImport
              ? { ...current.pendingImport, model_summary: status.model_summary }
              : current.pendingImport,
          importError: finished && status.status !== "completed" ? status.user_facing_message : current.importError,
        }));
        if (finished) stopPolling();
      } catch (error) {
        if (stopped) return;
        stopPolling();
        setState((current) => ({
          ...current,
          downloadingModels: false,
          importError: error instanceof Error ? error.message : String(error),
        }));
      } finally {
        inFlight = false;
      }
    };

    void poll();
    interval = window.setInterval(() => void poll(), 1000);
    return stopPolling;
  }, [
    state.pendingImport?.import_session_id,
    state.downloadJob?.job_id,
    state.downloadJob?.status,
  ]);

  return {
    state,
    startWorkflowImport,
    failImport,
    downloadMissingModels,
    cancelModelDownload,
    continueImport,
    duplicateImport,
    readyImportAction,
    cancelImport,
  };
}
