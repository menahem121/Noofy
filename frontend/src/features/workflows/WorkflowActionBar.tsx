import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type Ref } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  GripVertical,
  Loader2,
  Play,
  SlidersHorizontal,
  Square,
} from "lucide-react";

import { ViewportMenu } from "./ViewportMenu";

export interface WorkflowActionBarRunState {
  isRunning: boolean;
  canRun: boolean;
  canCancel: boolean;
  memoryLoaded?: boolean;
  cancelTitle?: string | null;
  showStatusNotice?: boolean;
  statusTitle?: string | null;
  statusMessage?: string | null;
  disabledReason?: string | null;
  disabledActionLabel?: string | null;
  developerDetails?: string | null;
}

interface WorkflowActionBarProps {
  runState: WorkflowActionBarRunState;
  batchCount: number;
  switchViewLabel: string;
  isEditingLayout?: boolean;
  className?: string;
  style?: CSSProperties;
  containerRef?: Ref<HTMLDivElement>;
  onDragStart?: (event: PointerEvent<HTMLButtonElement>) => void;
  onRun: () => void;
  onBatchCountChange: (value: number) => void;
  onCancel: () => void;
  onSwitchView: () => void;
  onExportNoofy: () => void;
  onExportComfyJson: () => void;
  onDisabledRunAction?: () => void;
  onRestoreDefaults: () => void;
  onEnterEditLayout: () => void;
  onSaveLayout: () => void;
  onCancelLayoutEdit: () => void;
  onEditWidgets?: () => void;
}

export function WorkflowActionBar({
  runState,
  batchCount,
  switchViewLabel,
  isEditingLayout = false,
  className = "",
  style,
  containerRef,
  onDragStart,
  onRun,
  onBatchCountChange,
  onCancel,
  onSwitchView,
  onExportNoofy,
  onExportComfyJson,
  onDisabledRunAction,
  onRestoreDefaults,
  onEnterEditLayout,
  onSaveLayout,
  onCancelLayoutEdit,
  onEditWidgets,
}: WorkflowActionBarProps) {
  const [optionsOpen, setOptionsOpen] = useState(false);
  const optionsRef = useRef<HTMLDivElement | null>(null);
  const optionsTriggerRef = useRef<HTMLButtonElement | null>(null);
  const optionsMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!optionsOpen) return;

    function handlePointerDown(event: globalThis.PointerEvent) {
      const target = event.target;
      if (
        target instanceof Node
        && (optionsRef.current?.contains(target) || optionsMenuRef.current?.contains(target))
      ) return;
      setOptionsOpen(false);
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOptionsOpen(false);
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [optionsOpen]);

  function runMenuAction(action: () => void) {
    setOptionsOpen(false);
    action();
  }

  return (
    <div
      ref={containerRef}
      className={`canvas-action-cluster${className ? ` ${className}` : ""}`}
      style={style}
      aria-label={isEditingLayout ? "Dashboard layout actions" : "Workflow actions"}
    >
      {onDragStart ? (
        <button
          className="canvas-action-cluster__drag-handle"
          type="button"
          aria-label="Move workflow action bar"
          title="Move action bar"
          onPointerDown={onDragStart}
        >
          <GripVertical size={14} aria-hidden="true" />
        </button>
      ) : null}
      {isEditingLayout ? (
        <>
          <button className="secondary-button canvas-action-cluster__cancel" type="button" onClick={onCancelLayoutEdit}>
            Cancel
          </button>
          <button className="primary-button canvas-action-cluster__run" type="button" onClick={onSaveLayout}>
            Save Dashboard
          </button>
        </>
      ) : (
        <>
          <WorkflowBatchCountStepper value={batchCount} onChange={onBatchCountChange} />
          <button
            className="primary-button canvas-action-cluster__run workflow-run-action-button"
            type="button"
            disabled={!runState.canRun}
            aria-label="Run workflow"
            title={
              !runState.canRun && runState.disabledReason
                ? runState.disabledReason
                : runState.canRun && runState.isRunning
                  ? "Queue another run behind the current one"
                  : "Run workflow"
            }
            aria-describedby={!runState.canRun && runState.disabledReason ? "workflow-run-disabled-reason" : undefined}
            onClick={onRun}
          >
            {runState.isRunning ? (
              <Loader2 className="spin" size={16} aria-hidden="true" />
            ) : (
              <Play size={16} aria-hidden="true" />
            )}
          </button>
          <button
            className="secondary-button canvas-action-cluster__cancel workflow-run-action-button"
            type="button"
            disabled={!runState.canCancel}
            aria-label="Cancel run"
            title={runState.cancelTitle ?? undefined}
            onClick={onCancel}
          >
            <Square size={14} aria-hidden="true" />
          </button>
          <div className="canvas-options-menu" ref={optionsRef}>
            <button
              ref={optionsTriggerRef}
              className="icon-button canvas-options-menu__trigger"
              type="button"
              aria-label="Workflow options"
              aria-haspopup="menu"
              aria-expanded={optionsOpen}
              title="Workflow options"
              onClick={() => setOptionsOpen((open) => !open)}
            >
              <SlidersHorizontal size={16} aria-hidden="true" />
            </button>

            <ViewportMenu open={optionsOpen} triggerRef={optionsTriggerRef} menuRef={optionsMenuRef}>
              <button className="canvas-options-menu__item" role="menuitem" type="button" onClick={() => runMenuAction(onSwitchView)}>
                {switchViewLabel}
              </button>
              <button className="canvas-options-menu__item" role="menuitem" type="button" onClick={() => runMenuAction(onExportNoofy)}>
                Export the Noofy workflow
              </button>
              <button className="canvas-options-menu__item" role="menuitem" type="button" onClick={() => runMenuAction(onExportComfyJson)}>
                Export ComfyUI JSON
              </button>
              <button className="canvas-options-menu__item" role="menuitem" type="button" onClick={() => runMenuAction(onEnterEditLayout)}>
                Edit dashboard layout
              </button>
              <button
                className="canvas-options-menu__item"
                role="menuitem"
                type="button"
                disabled={!onEditWidgets}
                onClick={() => onEditWidgets && runMenuAction(onEditWidgets)}
              >
                Edit widgets
              </button>
              <button className="canvas-options-menu__item" role="menuitem" type="button" onClick={() => runMenuAction(onRestoreDefaults)}>
                Restore dashboard defaults
              </button>
            </ViewportMenu>
          </div>
          {runState.memoryLoaded ? <WorkflowMemoryLoadedPill /> : null}
          {runState.showStatusNotice || (!runState.canRun && runState.disabledReason) ? (
            <div className="canvas-action-cluster__reason" id="workflow-run-disabled-reason" role="status">
              <AlertCircle size={14} aria-hidden="true" />
              <div className="canvas-action-cluster__reason-content">
                {runState.statusTitle ? <strong>{runState.statusTitle}</strong> : null}
                <span>{runState.statusMessage ?? runState.disabledReason}</span>
                {runState.developerDetails ? (
                  <details className="memory-status-developer-details">
                    <summary>Developer details</summary>
                    <pre>{runState.developerDetails}</pre>
                  </details>
                ) : null}
              </div>
            </div>
          ) : null}
          {!runState.canRun && runState.disabledActionLabel && onDisabledRunAction ? (
            <button className="secondary-button canvas-action-cluster__download" type="button" onClick={onDisabledRunAction}>
              <Download size={14} aria-hidden="true" />
              {runState.disabledActionLabel}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}

function WorkflowMemoryLoadedPill() {
  return (
    <div
      className="canvas-memory-loaded-pill"
      title="The required models are already loaded, so the next run should start faster."
      role="status"
    >
      <CheckCircle2 size={12} aria-hidden="true" />
      <span>Models loaded</span>
    </div>
  );
}

function WorkflowBatchCountStepper({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const normalized = clampNumber(Number.isFinite(value) ? Math.round(value) : 1, 1, 99);
  const clampBatch = (next: number) => clampNumber(Number.isFinite(next) ? Math.round(next) : 1, 1, 99);
  return (
    <div className="canvas-batch-count-stepper" aria-label="Batch count">
      <input
        type="number"
        min={1}
        max={99}
        aria-label="Batch count"
        value={normalized}
        onChange={(event) => onChange(clampBatch(Number(event.target.value)))}
      />
      <div className="canvas-batch-count-stepper__buttons">
        <button type="button" aria-label="Increase batch count" onClick={() => onChange(clampBatch(normalized + 1))}>
          <ChevronUp size={11} aria-hidden="true" />
        </button>
        <button type="button" aria-label="Decrease batch count" onClick={() => onChange(clampBatch(normalized - 1))}>
          <ChevronDown size={11} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
