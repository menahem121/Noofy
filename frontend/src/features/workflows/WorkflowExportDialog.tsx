import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Download, Loader2, Trash2, UploadCloud } from "lucide-react";

import {
  deleteWorkflowIcon,
  fetchWorkflowIcons,
  uploadWorkflowIcon,
  resolveBackendUrl,
  type WorkflowIconOption,
} from "../../lib/api/noofyApi";
import {
  saveWorkflowExportWithFilename,
  validateWorkflowExportFilename,
  workflowExportDownloadRequest,
  workflowExportFilename,
  type WorkflowExportMetadata,
  type WorkflowExportReviewModel,
} from "../../lib/workflowExport";
import { NATIVE_WORKFLOW_ICON_OPTIONS, WORKFLOW_CATEGORY_OPTIONS } from "./workflowMetadataOptions";

const WORKFLOW_EXPORT_CREATOR_STORAGE_KEY = "noofy.workflowExport.creator.v1";

interface WorkflowExportDialogProps {
  workflowName: string | null | undefined;
  exportUrl: string;
  extension?: ".noofy" | ".json";
  inputValues?: Record<string, unknown>;
  review?: WorkflowExportReviewModel;
  onClose: () => void;
}

export function WorkflowExportDialog({
  workflowName,
  exportUrl,
  extension = ".noofy",
  inputValues,
  review,
  onClose,
}: WorkflowExportDialogProps) {
  const [filenameInput, setFilenameInput] = useState(() => workflowExportFilename(workflowName, extension));
  const rememberedCreator = useMemo(() => readRememberedWorkflowExportCreator(), []);
  const [metadataDraft, setMetadataDraft] = useState<WorkflowExportMetadata>(() => ({
    name: review?.name ?? workflowName ?? "",
    description: review?.description ?? "",
    author: cleanExportText(review?.author) || rememberedCreator.author,
    website: cleanExportText(review?.website) || rememberedCreator.website,
    category: review?.category ?? "",
    tags: review?.tags ?? [],
    icon: review?.icon ?? "",
  }));
  const [tagInput, setTagInput] = useState(() => (review?.tags ?? []).join(", "));
  const [customIcons, setCustomIcons] = useState<WorkflowIconOption[]>([]);
  const [iconError, setIconError] = useState<string | null>(null);
  const [importingIcon, setImportingIcon] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const iconInputRef = useRef<HTMLInputElement | null>(null);
  const validation = useMemo(
    () => validateWorkflowExportFilename(filenameInput, extension),
    [extension, filenameInput],
  );
  const isNoofyExport = extension === ".noofy";
  const metadataValid = !isNoofyExport || Boolean((metadataDraft.name ?? "").trim());
  const canExport = validation.valid && metadataValid && !exporting;

  useEffect(() => {
    if (!isNoofyExport) return;
    let cancelled = false;
    fetchWorkflowIcons()
      .then((response) => {
        if (!cancelled) setCustomIcons(response.icons);
      })
      .catch((error) => {
        if (!cancelled) setIconError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [isNoofyExport]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canExport) return;
    setExporting(true);
    setExportError(null);
    try {
      const metadata = isNoofyExport ? normalizedMetadata(metadataDraft, tagInput, selectedCategory) : undefined;
      if (metadata) rememberWorkflowExportCreator(metadata);
      const exported = await saveWorkflowExportWithFilename(
        workflowExportDownloadRequest(
          exportUrl,
          inputValues,
          metadata,
        ),
        validation.filename,
      );
      if (exported) onClose();
      else setExporting(false);
    } catch (error) {
      setExporting(false);
      setExportError(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleIconImport(file: File | undefined) {
    if (!file) return;
    setIconError(null);
    if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) {
      setIconError("Use a PNG, JPEG, WebP, or GIF image for custom workflow icons.");
      return;
    }
    setImportingIcon(true);
    try {
      const icon = await uploadWorkflowIcon(file);
      setCustomIcons((current) => [...current.filter((item) => item.id !== icon.id), icon]);
      setMetadataDraft((current) => ({ ...current, icon: icon.id }));
    } catch (error) {
      setIconError(error instanceof Error ? error.message : String(error));
    } finally {
      setImportingIcon(false);
      if (iconInputRef.current) iconInputRef.current.value = "";
    }
  }

  async function handleIconDelete(icon: WorkflowIconOption) {
    setIconError(null);
    try {
      await deleteWorkflowIcon(icon.id);
      setCustomIcons((current) => current.filter((item) => item.id !== icon.id));
      if (metadataDraft.icon === icon.id) {
        setMetadataDraft((current) => ({ ...current, icon: "sparkles" }));
      }
    } catch (error) {
      setIconError(error instanceof Error ? error.message : String(error));
    }
  }

  const helperText = validation.valid
    ? validation.message ?? `The workflow will export as ${validation.filename}.`
    : validation.message;
  const requiredModels = review?.requiredModels ?? [];
  const primaryLabel = isNoofyExport ? "Export .noofy" : "Export";
  const draftCategory = metadataDraft.category ?? "";
  const selectedCategory = WORKFLOW_CATEGORY_OPTIONS.includes(draftCategory as (typeof WORKFLOW_CATEGORY_OPTIONS)[number])
    ? draftCategory
    : "";
  const selectedIcon = metadataDraft.icon || "sparkles";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-export-title">
      <form className={`workflow-export-modal${isNoofyExport ? " workflow-export-modal--review" : ""}`} onSubmit={handleSubmit}>
        <header className="workflow-export-modal__header">
          <div className="workflow-export-modal__icon" aria-hidden="true">
            <Download size={18} />
          </div>
          <div>
            <h2 id="workflow-export-title">Export workflow</h2>
            <p>{isNoofyExport ? "Review the details that will be included in this package." : `Choose the ${extension} filename before saving.`}</p>
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

        {isNoofyExport ? (
          <div className="workflow-export-review">
            <section className="workflow-export-review__section">
              <h3>Editable package details</h3>
              <label className="workflow-edit-field">
                <span>Workflow name</span>
                <input
                  value={metadataDraft.name ?? ""}
                  onChange={(event) => setMetadataDraft((current) => ({ ...current, name: event.target.value }))}
                  aria-invalid={!metadataValid}
                />
              </label>
              {!metadataValid ? (
                <p className="workflow-export-modal__help workflow-export-modal__help--error">Enter a workflow name before exporting.</p>
              ) : null}
              <label className="workflow-edit-field">
                <span>Description</span>
                <textarea
                  rows={3}
                  value={metadataDraft.description ?? ""}
                  onChange={(event) => setMetadataDraft((current) => ({ ...current, description: event.target.value }))}
                />
              </label>
              <div className="workflow-export-review__grid">
                <label className="workflow-edit-field">
                  <span>Author</span>
                  <input
                    value={metadataDraft.author ?? ""}
                    onChange={(event) => setMetadataDraft((current) => ({ ...current, author: event.target.value }))}
                  />
                </label>
                <label className="workflow-edit-field">
                  <span>Website</span>
                  <input
                    value={metadataDraft.website ?? ""}
                    onChange={(event) => setMetadataDraft((current) => ({ ...current, website: event.target.value }))}
                  />
                </label>
                <label className="workflow-edit-field">
                  <span>Category</span>
                  <select
                    value={selectedCategory}
                    onChange={(event) => setMetadataDraft((current) => ({ ...current, category: event.target.value }))}
                  >
                    <option value="">No category</option>
                    {WORKFLOW_CATEGORY_OPTIONS.map((category) => (
                      <option key={category} value={category}>{category}</option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="workflow-edit-field">
                <span>Tags</span>
                <input
                  value={tagInput}
                  onChange={(event) => setTagInput(event.target.value)}
                  placeholder="portrait, cleanup, starter"
                />
              </label>
              <div className="workflow-export-icon-picker" data-noofy-workflow-import-drop-ignore>
                <div className="workflow-export-icon-picker__label">Icon</div>
                <div className="workflow-export-icon-grid" role="radiogroup" aria-label="Workflow icon">
                  <button
                    className="workflow-export-icon-tile workflow-export-icon-tile--import"
                    type="button"
                    onClick={() => iconInputRef.current?.click()}
                    disabled={importingIcon}
                  >
                    {importingIcon ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <UploadCloud size={18} aria-hidden="true" />}
                    <span>Import icon</span>
                  </button>
                  <input
                    ref={iconInputRef}
                    className="visually-hidden"
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    onChange={(event) => void handleIconImport(event.target.files?.[0])}
                  />
                  {NATIVE_WORKFLOW_ICON_OPTIONS.map(({ id, label, Icon }) => (
                    <button
                      key={id}
                      className={`workflow-export-icon-tile${selectedIcon === id ? " workflow-export-icon-tile--selected" : ""}`}
                      type="button"
                      role="radio"
                      aria-checked={selectedIcon === id}
                      aria-label={label}
                      onClick={() => setMetadataDraft((current) => ({ ...current, icon: id }))}
                    >
                      <Icon size={20} aria-hidden="true" />
                    </button>
                  ))}
                  {customIcons.map((icon) => (
                    <div
                      key={icon.id}
                      className={`workflow-export-icon-tile workflow-export-icon-tile--custom${selectedIcon === icon.id ? " workflow-export-icon-tile--selected" : ""}`}
                      role="radio"
                      tabIndex={0}
                      aria-checked={selectedIcon === icon.id}
                      aria-label={icon.label}
                      onClick={() => setMetadataDraft((current) => ({ ...current, icon: icon.id }))}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        event.preventDefault();
                        setMetadataDraft((current) => ({ ...current, icon: icon.id }));
                      }}
                    >
                      <img src={resolveBackendUrl(icon.url, { includeToken: true })} alt="" />
                      <span>{icon.label}</span>
                      <button
                        type="button"
                        className="workflow-export-icon-tile__delete"
                        aria-label={`Delete ${icon.label}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleIconDelete(icon);
                        }}
                      >
                        <Trash2 size={12} aria-hidden="true" />
                      </button>
                    </div>
                  ))}
                </div>
                {iconError ? <p className="workflow-export-modal__help workflow-export-modal__help--error">{iconError}</p> : null}
              </div>
            </section>

            <section className="workflow-export-review__section">
              <h3>Included package details</h3>
              <dl className="detail-list detail-list--compact workflow-export-review__details">
                <div><dt>Source</dt><dd>{review?.source || "Noofy workflow"}</dd></div>
              </dl>
              <div className="workflow-model-list">
                {requiredModels.length > 0 ? requiredModels.map((model) => (
                  <div key={`${model.folder ?? ""}-${model.name}`} className="workflow-model-item">
                    <strong>{model.name}</strong>
                    <span>{[model.type, model.status_label, model.folder].filter(Boolean).join(" · ")}</span>
                  </div>
                )) : <p className="detail-panel__tag-empty">No required models detected.</p>}
              </div>
            </section>
          </div>
        ) : null}

        <footer className="workflow-export-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose} disabled={exporting}>
            Cancel
          </button>
          <button className="primary-button primary-button--compact" type="submit" disabled={!canExport}>
            {exporting ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
            {primaryLabel}
          </button>
        </footer>
      </form>
    </div>
  );
}

function normalizedMetadata(
  metadata: WorkflowExportMetadata,
  tagInput: string,
  selectedCategory: string,
): WorkflowExportMetadata {
  return {
    name: cleanExportText(metadata.name),
    description: cleanExportText(metadata.description),
    author: cleanExportText(metadata.author),
    website: cleanExportText(metadata.website),
    category: selectedCategory,
    tags: tagInput.split(",").map((tag) => tag.trim()).filter(Boolean),
    icon: cleanExportText(metadata.icon),
  };
}

function cleanExportText(value: string | null | undefined) {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function readRememberedWorkflowExportCreator() {
  try {
    const raw = window.localStorage.getItem(WORKFLOW_EXPORT_CREATOR_STORAGE_KEY);
    if (!raw) return { author: "", website: "" };
    const parsed = JSON.parse(raw) as { author?: unknown; website?: unknown } | null;
    if (!parsed || typeof parsed !== "object") return { author: "", website: "" };
    return {
      author: typeof parsed.author === "string" ? cleanExportText(parsed.author) : "",
      website: typeof parsed.website === "string" ? cleanExportText(parsed.website) : "",
    };
  } catch {
    return { author: "", website: "" };
  }
}

function rememberWorkflowExportCreator(metadata: WorkflowExportMetadata) {
  try {
    window.localStorage.setItem(
      WORKFLOW_EXPORT_CREATOR_STORAGE_KEY,
      JSON.stringify({
        author: cleanExportText(metadata.author),
        website: cleanExportText(metadata.website),
      }),
    );
  } catch {
    // Export should not fail because browser storage is unavailable.
  }
}
