import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({
    position: "fixed",
    visibility: "hidden",
  });

  useLayoutEffect(() => {
    if (!menuOpen) return;

    function updateMenuPosition() {
      const button = buttonRef.current;
      const content = contentRef.current;
      if (!button || !content) return;

      const viewportPadding = 12;
      const menuGap = 8;
      const buttonRect = button.getBoundingClientRect();
      const menuWidth = content.offsetWidth || 210;
      const menuHeight = content.offsetHeight || 260;
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const maxTop = Math.max(viewportPadding, viewportHeight - viewportPadding - menuHeight);
      const left = Math.min(
        Math.max(viewportPadding, buttonRect.right - menuWidth),
        Math.max(viewportPadding, viewportWidth - viewportPadding - menuWidth),
      );
      const belowTop = buttonRect.bottom + menuGap;
      const aboveTop = buttonRect.top - menuGap - menuHeight;
      const top = belowTop + menuHeight <= viewportHeight - viewportPadding || aboveTop < viewportPadding
        ? Math.min(Math.max(viewportPadding, belowTop), maxTop)
        : Math.max(viewportPadding, aboveTop);

      setMenuStyle({
        position: "fixed",
        top: Math.round(top),
        left: Math.round(left),
        visibility: "visible",
      });
    }

    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);

    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (buttonRef.current?.contains(target) || contentRef.current?.contains(target)) return;
      onCloseMenu();
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onCloseMenu();
        buttonRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen, onCloseMenu]);

  return (
    <div className={menuClasses}>
      <button
        ref={buttonRef}
        className={buttonClassName}
        type="button"
        aria-label={`Actions for ${displayName}`}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={onToggleMenu}
      >
        <MoreHorizontal size={16} aria-hidden="true" />
      </button>
      {menuOpen
        ? createPortal(
            <div ref={contentRef} className="workflow-action-menu__content" role="menu" style={menuStyle}>
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
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
