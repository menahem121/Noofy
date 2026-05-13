import { ChevronDown, Loader2, Plus, Upload } from "lucide-react";

import type { ModelImportResponse } from "../../lib/api/noofyApi";
import { selectModelFiles } from "../../lib/folderDialogs";
import { categoryLabel } from "./modelUi";

interface ModelImportPanelProps {
  categories: string[];
  folder: string;
  paths: string[];
  overwrite: boolean;
  busy: boolean;
  result: ModelImportResponse | null;
  onFolderChange: (folder: string) => void;
  onPathsChange: (paths: string[]) => void;
  onOverwriteChange: (overwrite: boolean) => void;
  onImport: () => void;
}

export function ModelImportPanel({
  categories,
  folder,
  paths,
  overwrite,
  busy,
  result,
  onFolderChange,
  onPathsChange,
  onOverwriteChange,
  onImport,
}: ModelImportPanelProps) {
  async function chooseFiles() {
    const selected = await selectModelFiles();
    if (selected.length > 0) onPathsChange(selected);
  }

  return (
    <div className="tag-create-form" role="region" aria-label="Add model files">
      <div className="filter-select-wrap">
        <select
          className="filter-select"
          aria-label="Model folder"
          value={folder}
          onChange={(event) => onFolderChange(event.target.value)}
        >
          {categories.map((category) => (
            <option key={category} value={category}>
              {categoryLabel(category)}
            </option>
          ))}
        </select>
        <ChevronDown size={13} aria-hidden="true" />
      </div>
      <button className="secondary-button secondary-button--small" type="button" onClick={() => void chooseFiles()}>
        <Upload size={14} aria-hidden="true" />
        Choose files
      </button>
      <label className="checkbox-row" title="This only replaces files in Noofy Models, never external model folders.">
        <input
          type="checkbox"
          checked={overwrite}
          onChange={(event) => onOverwriteChange(event.target.checked)}
        />
        Replace same-name Noofy files
      </label>
      <span className="tag-pill tag-pill--more">
        {paths.length ? `${paths.length} selected` : "No files selected"}
      </span>
      <button
        className="primary-button primary-button--compact"
        type="button"
        onClick={onImport}
        disabled={busy || paths.length === 0}
      >
        {busy ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
        Import
      </button>
      {result && (
        <span className="tag-pill tag-pill--more">
          {result.imported_count} imported, {result.failed_count} failed
        </span>
      )}
    </div>
  );
}
