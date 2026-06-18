import { useCallback, useEffect, useRef, useState } from "react";

import {
  approveImportCustomNodeCandidate,
  cancelImportModelDownload,
  cancelWorkflowImport,
  commitWorkflowImport,
  downloadImportMissingModels,
  fetchImportModelDownloadStatus,
  fetchImportModelVerificationStatus,
  fetchWorkflows,
  markImportHasNoCustomNodes,
  previewWorkflowPackageImport,
  resolveImportCustomNodesFromUrls,
  type ImportModelDownloadJobStatus,
  type ImportModelVerificationJobStatus,
  type WorkflowImportResponse,
} from "../../lib/api/noofyApi";
import { isModelDownloadActive } from "../../lib/modelDownloadProgress";
import { workflowDisplayName } from "../../lib/workflowNames";
import { addPendingImportedSetupReminder } from "../home/pendingSetupBanners";
import { useWorkflowLibrary } from "../home/WorkflowLibraryProvider";
import { importNeedsConfiguration } from "./workflowImportUtils";

export interface WorkflowImportFlowState {
  importing: boolean;
  downloadingModels: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  verificationJob: ImportModelVerificationJobStatus | null;
  pendingImport: WorkflowImportResponse | null;
  importResult: WorkflowImportResponse | null;
  importError: string | null;
}

export interface WorkflowImportFlowController {
  state: WorkflowImportFlowState;
  startWorkflowImport: (file: File) => Promise<void>;
  failImport: (message: string) => void;
  downloadMissingModels: () => Promise<void>;
  cancelModelDownload: () => Promise<void>;
  continueImport: () => Promise<void>;
  duplicateImport: (action: "replace" | "copy") => Promise<void>;
  resolveCustomNodesFromUrls: (urlsByNodeType: Record<string, string>) => Promise<void>;
  approveCustomNodeCandidate: (candidateId: string) => Promise<void>;
  markWorkflowHasNoCustomNodes: () => Promise<void>;
  readyImportAction: () => Promise<void>;
  cancelImport: () => Promise<void>;
  dismissImportResult: () => void;
  dismissImportError: () => void;
}

export const RAW_COMFY_JSON_IMPORT_UNSUPPORTED_MESSAGE =
  "Noofy can import raw ComfyUI .json workflow files here.";

export const SUPPORTED_WORKFLOW_IMPORT_EXTENSIONS = [".noofy", ".json"] as const;
export const WORKFLOW_IMPORT_ACCEPT = SUPPORTED_WORKFLOW_IMPORT_EXTENSIONS.join(",");

export function workflowImportExtension(filename: string) {
  const match = /\.[^.\\/]+$/.exec(filename.trim().toLowerCase());
  return match?.[0] ?? "";
}

export function isSupportedWorkflowImportFile(file: { name: string }) {
  return SUPPORTED_WORKFLOW_IMPORT_EXTENSIONS.includes(
    workflowImportExtension(file.name) as (typeof SUPPORTED_WORKFLOW_IMPORT_EXTENSIONS)[number],
  );
}

export function unsupportedWorkflowImportMessage(filename?: string | null) {
  const name = filename?.trim();
  if (name && workflowImportExtension(name) === ".json") {
    return RAW_COMFY_JSON_IMPORT_UNSUPPORTED_MESSAGE;
  }
  return name
    ? `Noofy can import .noofy workflow packages and raw ComfyUI .json files. ${name} is not a supported workflow import file.`
    : "Noofy can import .noofy workflow packages and raw ComfyUI .json files here.";
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

function rememberImportedSetup(importResult: WorkflowImportResponse) {
  if (!importNeedsConfiguration(importResult)) return;
  addPendingImportedSetupReminder(
    importResult.workflow.id,
    workflowDisplayName(importResult.workflow),
  );
}

export function importNeedsCustomNodeResolution(importResult: WorkflowImportResponse) {
  const resolution = importResult.custom_node_resolution;
  if (!resolution) return false;
  if (resolution.mode === "manual_url" || resolution.mode === "candidate_approval") return true;
  if (["missing_custom_nodes", "needs_comfyui_update"].includes(resolution.status)) return true;
  if (resolution.candidate) return true;
  if (resolution.package_id || resolution.missing_custom_node) return true;
  return Boolean(
    (resolution.unresolved_node_types?.length ?? 0) > 0 ||
    (resolution.ambiguous_node_types?.length ?? 0) > 0 ||
    (resolution.github_url_fields?.length ?? 0) > 0,
  );
}

export function useWorkflowImportFlow({
  onOpenWorkflow,
  onConfigureDashboard,
  allowUnverifiedCommunityPreparation = true,
  deferConfigurationAfterDownloadedImport = false,
}: {
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  allowUnverifiedCommunityPreparation?: boolean;
  deferConfigurationAfterDownloadedImport?: boolean;
}): WorkflowImportFlowController {
  const workflowLibrary = useWorkflowLibrary();
  const [state, setState] = useState<WorkflowImportFlowState>(initialWorkflowImportFlowState);
  const autoCommittedDownloadJobs = useRef(new Set<string>());

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
    if (!isSupportedWorkflowImportFile(file)) {
      failImport(unsupportedWorkflowImportMessage(file.name));
      return;
    }
    autoCommittedDownloadJobs.current.clear();
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
          importNeedsCustomNodeResolution(importResult) ||
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
      rememberImportedSetup(importResult);
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
  }, [allowUnverifiedCommunityPreparation, failImport, workflowLibrary.setWorkflowsFromResponse]);

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
    rememberImportedSetup(importResult);
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

  const resolveCustomNodesFromUrls = useCallback(async (urlsByNodeType: Record<string, string>) => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, importing: true, importError: null }));
    try {
      const importResult = await resolveImportCustomNodesFromUrls(sessionId, urlsByNodeType);
      if (
        importResult.import_session_id &&
        (
          importResult.duplicate_identity ||
          importNeedsCustomNodeResolution(importResult) ||
          (importResult.model_summary && importResult.model_summary.total_count > 0)
        )
      ) {
        setState((current) => ({
          ...current,
          importing: false,
          pendingImport: importResult,
          importError: null,
        }));
        return;
      }
      const committed = await commitWorkflowImport(sessionId);
      await finishImport(committed, false);
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [finishImport, state.pendingImport?.import_session_id]);

  const approveCustomNodeCandidate = useCallback(async (candidateId: string) => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, importing: true, importError: null }));
    try {
      const importResult = await approveImportCustomNodeCandidate(sessionId, candidateId);
      if (
        importResult.import_session_id &&
        (
          importResult.duplicate_identity ||
          importNeedsCustomNodeResolution(importResult) ||
          (importResult.model_summary && importResult.model_summary.total_count > 0)
        )
      ) {
        setState((current) => ({
          ...current,
          importing: false,
          pendingImport: importResult,
          importError: null,
        }));
        return;
      }
      const committed = await commitWorkflowImport(sessionId);
      await finishImport(committed, false);
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [finishImport, state.pendingImport?.import_session_id]);

  const markWorkflowHasNoCustomNodes = useCallback(async () => {
    const sessionId = state.pendingImport?.import_session_id;
    if (!sessionId) return;
    setState((current) => ({ ...current, importing: true, importError: null }));
    try {
      const importResult = await markImportHasNoCustomNodes(sessionId);
      setState((current) => ({
        ...current,
        importing: false,
        pendingImport: importResult,
        importError: null,
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        importing: false,
        importError: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [state.pendingImport?.import_session_id]);

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
      importing: false,
      downloadingModels: false,
      pendingImport: null,
      downloadJob: null,
      verificationJob: null,
      importError: null,
    }));
  }, [state.pendingImport?.import_session_id]);

  const dismissImportResult = useCallback(() => {
    setState((current) => (current.importResult ? { ...current, importResult: null } : current));
  }, []);

  const dismissImportError = useCallback(() => {
    setState((current) => (current.importError ? { ...current, importError: null } : current));
  }, []);

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

  useEffect(() => {
    const pendingImport = state.pendingImport;
    const sessionId = pendingImport?.import_session_id;
    const jobId = state.downloadJob?.job_id;
    if (!sessionId || !jobId) return;
    if (state.importing || state.downloadingModels) return;
    if (pendingImport?.duplicate_identity) return;
    if (state.downloadJob?.status !== "completed") return;

    const summary = state.downloadJob.model_summary ?? pendingImport?.model_summary;
    if (!summary?.models.length || !summary.models.every((model) => model.status === "available")) return;

    // Use the final filesystem availability, not terminal download progress or
    // a potentially stale aggregate flag, as the commit boundary.
    const jobKey = `${sessionId}:${jobId}`;
    if (autoCommittedDownloadJobs.current.has(jobKey)) return;

    autoCommittedDownloadJobs.current.add(jobKey);
    if (deferConfigurationAfterDownloadedImport && pendingImport && importNeedsConfiguration(pendingImport)) {
      void continueImport();
      return;
    }
    void readyImportAction();
  }, [
    continueImport,
    deferConfigurationAfterDownloadedImport,
    readyImportAction,
    state.downloadJob?.job_id,
    state.downloadJob?.model_summary,
    state.downloadJob?.status,
    state.downloadingModels,
    state.importing,
    state.pendingImport,
  ]);

  return {
    state,
    startWorkflowImport,
    failImport,
    downloadMissingModels,
    cancelModelDownload,
    continueImport,
    duplicateImport,
    resolveCustomNodesFromUrls,
    approveCustomNodeCandidate,
    markWorkflowHasNoCustomNodes,
    readyImportAction,
    cancelImport,
    dismissImportResult,
    dismissImportError,
  };
}
