import { useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, UploadCloud, X } from "lucide-react";

import { workflowDisplayName } from "../../lib/workflowNames";
import {
  isSupportedWorkflowImportFile,
  type WorkflowImportFlowController,
  unsupportedWorkflowImportMessage,
} from "./useWorkflowImportFlow";
import { importNeedsConfiguration } from "./workflowImportUtils";

const WORKFLOW_IMPORT_DROP_IGNORE_ATTR = "data-noofy-workflow-import-drop-ignore";

export function WorkflowGlobalDropImport({
  importFlow,
}: {
  importFlow: WorkflowImportFlowController;
}) {
  const [dragActive, setDragActive] = useState(false);
  const dragDepthRef = useRef(0);
  const { failImport, startWorkflowImport } = importFlow;

  useEffect(() => {
    function resetDragState() {
      dragDepthRef.current = 0;
      setDragActive(false);
    }

    function shouldHandleFileDrag(event: DragEvent) {
      if (event.defaultPrevented) return false;
      return hasFileTransfer(event.dataTransfer) && !isLocalFileDropTarget(event.target);
    }

    function handleDragEnter(event: DragEvent) {
      if (!shouldHandleFileDrag(event)) return;
      dragDepthRef.current += 1;
      setDragActive(true);
    }

    function handleDragOver(event: DragEvent) {
      if (!shouldHandleFileDrag(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      setDragActive(true);
    }

    function handleDragLeave(event: DragEvent) {
      if (!hasFileTransfer(event.dataTransfer)) return;
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setDragActive(false);
    }

    function handleDrop(event: DragEvent) {
      if (!shouldHandleFileDrag(event)) return;
      const files = filesFromDataTransfer(event.dataTransfer);
      if (files.length === 0) return;
      event.preventDefault();
      event.stopPropagation();
      resetDragState();

      const workflowFile = files.find(isSupportedWorkflowImportFile);
      if (workflowFile) {
        void startWorkflowImport(workflowFile);
        return;
      }

      failImport(unsupportedWorkflowImportMessage(files[0]?.name));
    }

    window.addEventListener("dragenter", handleDragEnter);
    window.addEventListener("dragover", handleDragOver);
    window.addEventListener("dragleave", handleDragLeave);
    window.addEventListener("drop", handleDrop);
    return () => {
      window.removeEventListener("dragenter", handleDragEnter);
      window.removeEventListener("dragover", handleDragOver);
      window.removeEventListener("dragleave", handleDragLeave);
      window.removeEventListener("drop", handleDrop);
    };
  }, [failImport, startWorkflowImport]);

  if (!dragActive) return null;

  return (
    <div className="workflow-global-drop-overlay" role="status" aria-live="polite">
      <div className="workflow-global-drop-overlay__panel">
        <UploadCloud size={30} aria-hidden="true" />
        <strong>Drop workflow package to import</strong>
        <span>.noofy files use the normal Noofy import review.</span>
      </div>
    </div>
  );
}

export function WorkflowImportStatusNotice({
  importFlow,
  hidden = false,
  onConfigureDashboard,
}: {
  importFlow: WorkflowImportFlowController;
  hidden?: boolean;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
}) {
  if (hidden) return null;
  const { state } = importFlow;

  if (state.importing && !state.pendingImport) {
    return (
      <div className="workflow-import-status workflow-import-status--busy" role="status" aria-live="polite">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <div>
          <strong>Importing workflow</strong>
          <span>Noofy is reviewing the workflow package.</span>
        </div>
      </div>
    );
  }

  if (state.importError) {
    return (
      <div className="workflow-import-status workflow-import-status--error" role="status" aria-live="polite">
        <AlertCircle size={18} aria-hidden="true" />
        <div>
          <strong>Workflow could not be imported</strong>
          <span>{state.importError}</span>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="Dismiss workflow import message"
          onClick={importFlow.dismissImportError}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    );
  }

  if (state.importResult) {
    const workflowName = workflowDisplayName(state.importResult.workflow);
    const needsConfiguration = importNeedsConfiguration(state.importResult);
    return (
      <div className="workflow-import-status workflow-import-status--success" role="status" aria-live="polite">
        <CheckCircle2 size={18} aria-hidden="true" />
        <div>
          <strong>{state.importResult.user_facing_message}</strong>
          <span>{workflowName} was added to your local workflows.</span>
        </div>
        {needsConfiguration && onConfigureDashboard ? (
          <button
            className="primary-button primary-button--compact"
            type="button"
            onClick={() => onConfigureDashboard(state.importResult?.workflow.id, workflowName)}
          >
            Configure dashboard
          </button>
        ) : null}
        <button
          className="icon-button"
          type="button"
          aria-label="Dismiss workflow import message"
          onClick={importFlow.dismissImportResult}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    );
  }

  return null;
}

function hasFileTransfer(dataTransfer: DataTransfer | null) {
  if (!dataTransfer) return false;
  const types = Array.from(dataTransfer.types ?? []);
  if (types.includes("Files")) return true;
  return Array.from(dataTransfer.items ?? []).some((item) => item.kind === "file");
}

function filesFromDataTransfer(dataTransfer: DataTransfer | null) {
  return Array.from(dataTransfer?.files ?? []);
}

function isLocalFileDropTarget(target: EventTarget | null) {
  const element = target instanceof Element ? target : null;
  if (!element) return false;
  return Boolean(
    element.closest(
      [
        `[${WORKFLOW_IMPORT_DROP_IGNORE_ATTR}]`,
        'input[type="file"]',
        ".dashboard-image-input",
        ".dashboard-audio-input",
        ".dashboard-video-input",
        ".dashboard-file-input",
        ".dashboard-three-d-input",
        ".builder-default-asset",
        ".workflow-export-icon-picker",
      ].join(", "),
    ),
  );
}
