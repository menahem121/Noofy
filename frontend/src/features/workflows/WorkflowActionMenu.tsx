import {
  Download,
  Edit3,
  FileJson,
  MoreHorizontal,
  PackageOpen,
  Play,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";

import { workflowDisplayName } from "../../lib/workflowNames";

export interface WorkflowActionMenuWorkflow {
  id: string;
  name: string;
  display_name?: string;
  can_export_noofy?: boolean;
  can_export_comfyui_json?: boolean;
  can_remove?: boolean;
}

interface WorkflowActionMenuProps {
  workflow: WorkflowActionMenuWorkflow;
  menuOpen: boolean;
  buttonClassName?: string;
  menuClassName?: string;
  onOpen: () => void;
  onDetails: () => void;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onEditDashboard: () => void;
  onEditWidgets: () => void;
  onExportNoofy: () => void;
  onExportComfyJson: () => void;
  onRemove: () => void;
}

export function WorkflowActionMenu({
  workflow,
  menuOpen,
  buttonClassName = "icon-button",
  menuClassName = "",
  onOpen,
  onDetails,
  onToggleMenu,
  onCloseMenu,
  onEditDashboard,
  onEditWidgets,
  onExportNoofy,
  onExportComfyJson,
  onRemove,
}: WorkflowActionMenuProps) {
  const menuClasses = ["workflow-action-menu", menuClassName].filter(Boolean).join(" ");
  const canExportComfyJson = workflow.can_export_comfyui_json !== false;
  const displayName = workflowDisplayName(workflow);

  return (
    <div className={menuClasses}>
      <button
        className={buttonClassName}
        type="button"
        aria-label={`Actions for ${displayName}`}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={onToggleMenu}
      >
        <MoreHorizontal size={16} aria-hidden="true" />
      </button>
      {menuOpen ? (
        <div className="workflow-action-menu__content" role="menu">
          <button role="menuitem" type="button" onClick={onOpen}>
            <Play size={14} aria-hidden="true" />
            Open
          </button>
          <button role="menuitem" type="button" onClick={onDetails}>
            <PackageOpen size={14} aria-hidden="true" />
            View details
          </button>
          <button role="menuitem" type="button" onClick={onEditDashboard}>
            <Edit3 size={14} aria-hidden="true" />
            Edit dashboard
          </button>
          <button role="menuitem" type="button" onClick={onEditWidgets}>
            <SlidersHorizontal size={14} aria-hidden="true" />
            Edit Widgets
          </button>
          {workflow.can_export_noofy ? (
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                onCloseMenu();
                onExportNoofy();
              }}
            >
              <Download size={14} aria-hidden="true" />
              Export .Noofy
            </button>
          ) : null}
          {canExportComfyJson ? (
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                onCloseMenu();
                onExportComfyJson();
              }}
            >
              <FileJson size={14} aria-hidden="true" />
              Export ComfyUI JSON
            </button>
          ) : null}
          {workflow.can_remove ? (
            <button className="workflow-action-menu__danger" role="menuitem" type="button" onClick={onRemove}>
              <Trash2 size={14} aria-hidden="true" />
              Remove workflow
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
