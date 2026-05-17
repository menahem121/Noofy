import { useMemo, useState, type FormEvent } from "react";
import { Download, Loader2 } from "lucide-react";

import {
  saveWorkflowExportWithFilename,
  validateWorkflowExportFilename,
  workflowExportDownloadRequest,
  workflowExportFilename,
} from "../../lib/workflowExport";

interface WorkflowExportDialogProps {
  workflowName: string | null | undefined;
  exportUrl: string;
  extension?: ".noofy" | ".json";
  inputValues?: Record<string, unknown>;
  onClose: () => void;
}

export function WorkflowExportDialog({
  workflowName,
  exportUrl,
  extension = ".noofy",
  inputValues,
  onClose,
}: WorkflowExportDialogProps) {
  const [filenameInput, setFilenameInput] = useState(() => workflowExportFilename(workflowName, extension));
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const validation = useMemo(
    () => validateWorkflowExportFilename(filenameInput, extension),
    [extension, filenameInput],
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!validation.valid || exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      const exported = await saveWorkflowExportWithFilename(
        workflowExportDownloadRequest(exportUrl, inputValues),
        validation.filename,
      );
      if (exported) onClose();
      else setExporting(false);
    } catch (error) {
      setExporting(false);
      setExportError(error instanceof Error ? error.message : String(error));
    }
  }

  const helperText = validation.valid
    ? validation.message ?? `The workflow will export as ${validation.filename}.`
    : validation.message;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-export-title">
      <form className="workflow-export-modal" onSubmit={handleSubmit}>
        <header className="workflow-export-modal__header">
          <div className="workflow-export-modal__icon" aria-hidden="true">
            <Download size={18} />
          </div>
          <div>
            <h2 id="workflow-export-title">Export workflow</h2>
            <p>Choose the {extension} filename before saving.</p>
          </div>
        </header>

        <label className="workflow-export-modal__field">
          <span>Filename</span>
          <input
            autoFocus
            type="text"
            value={filenameInput}
            onChange={(event) => {
              setFilenameInput(event.target.value);
              setExportError(null);
            }}
            aria-invalid={!validation.valid}
            aria-describedby="workflow-export-filename-help"
          />
        </label>
        <p
          id="workflow-export-filename-help"
          className={`workflow-export-modal__help${validation.valid ? "" : " workflow-export-modal__help--error"}`}
        >
          {helperText}
        </p>
        {exportError ? <p className="workflow-export-modal__help workflow-export-modal__help--error">{exportError}</p> : null}

        <footer className="workflow-export-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose} disabled={exporting}>
            Cancel
          </button>
          <button className="primary-button primary-button--compact" type="submit" disabled={!validation.valid || exporting}>
            {exporting ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
            Export
          </button>
        </footer>
      </form>
    </div>
  );
}
