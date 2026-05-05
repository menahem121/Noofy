import { useMemo, useState, type CSSProperties, type DragEvent } from "react";
import {
  Download,
  GripVertical,
  Image as ImageIcon,
  Loader2,
  Play,
  RotateCcw,
  Square,
} from "lucide-react";

import {
  type DashboardControlDef,
  type JobProgress,
  type WorkflowInputDef,
  type WorkflowOutputDef,
} from "../../lib/api/noofyApi";
import { findAvailableLayout, fitLayout, type GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import { DashboardInputControl } from "./DashboardInputControl";

const GRID_COLUMNS = 12;
const DRAG_MIME_TYPE = "application/noofy-dashboard-widget";
const DRAG_TEXT_PREFIX = "noofy-widget:";

export interface CanvasRunState {
  isRunning: boolean;
  canRun: boolean;
  canCancel: boolean;
  progress: JobProgress | null;
  progressPercent: number;
}

interface CanvasDashboardViewProps {
  controls: DashboardControlDef[];
  inputIndex: Map<string, WorkflowInputDef>;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
  inputValues: Record<string, unknown>;
  layoutOverrides: Record<string, GridItemLayout>;
  isEditingLayout: boolean;
  hasLayoutOverrides: boolean;
  runState: CanvasRunState;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onRun: () => void;
  onCancel: () => void;
  onRestoreDefaults: () => void;
  onToggleEditLayout: () => void;
  onResetLayout: () => void;
  onLayoutOverride: (controlId: string, layout: GridItemLayout) => void;
}

export function CanvasDashboardView({
  controls,
  inputIndex,
  outputIndex,
  outputImagesByNodeId,
  inputValues,
  layoutOverrides,
  isEditingLayout,
  hasLayoutOverrides,
  runState,
  onChange,
  onImageUpload,
  onRun,
  onCancel,
  onRestoreDefaults,
  onToggleEditLayout,
  onResetLayout,
  onLayoutOverride,
}: CanvasDashboardViewProps) {
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ controlId: string; layout: GridItemLayout } | null>(null);
  const canvasItems = useMemo(
    () => controls.map((control) => ({ id: control.id, layout: effectiveLayout(control) })),
    [controls, layoutOverrides],
  );

  function effectiveLayout(control: DashboardControlDef): GridItemLayout {
    if (layoutOverrides[control.id]) return layoutOverrides[control.id];
    if (control.layout) return fromBackendLayout(control.layout);
    return defaultLayoutForWidgetType(control.type);
  }

  function handleDragStart(event: DragEvent, controlId: string) {
    setActiveDragId(controlId);
    setDragPreview(null);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(DRAG_MIME_TYPE, JSON.stringify({ controlId }));
    event.dataTransfer.setData("text/plain", `${DRAG_TEXT_PREFIX}${controlId}`);
  }

  function handleDragOver(event: DragEvent) {
    if (!isEditingLayout) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const controlId = activeDragId ?? readDragControlId(event);
    if (!controlId) return;

    const control = controls.find((c) => c.id === controlId);
    if (!control) return;

    const layout = effectiveLayout(control);
    const desired = layoutFromPointer(event, layout);
    if (!desired) return;

    const preview = findAvailableLayout(controlId, desired, canvasItems, GRID_COLUMNS);
    setDragPreview((cur) => {
      if (
        cur?.controlId === controlId &&
        cur.layout.x === preview.x &&
        cur.layout.y === preview.y
      )
        return cur;
      return { controlId, layout: preview };
    });
  }

  function handleDrop(event: DragEvent) {
    if (!isEditingLayout) return;
    event.preventDefault();
    const controlId = dragPreview?.controlId ?? activeDragId ?? readDragControlId(event);
    if (!controlId) { clearDrag(); return; }

    const control = controls.find((c) => c.id === controlId);
    if (!control) { clearDrag(); return; }

    const newLayout = dragPreview?.layout ?? layoutFromPointer(event, effectiveLayout(control));
    if (!newLayout) { clearDrag(); return; }

    const resolved = findAvailableLayout(controlId, fitLayout(newLayout, GRID_COLUMNS), canvasItems, GRID_COLUMNS);
    onLayoutOverride(controlId, resolved);
    clearDrag();
  }

  function handleDragLeave(event: DragEvent) {
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    setDragPreview(null);
  }

  function clearDrag() {
    setActiveDragId(null);
    setDragPreview(null);
  }

  function layoutFromPointer(event: DragEvent, currentLayout: GridItemLayout): GridItemLayout | null {
    const canvas = document.getElementById("canvas-dashboard-surface");
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const ROW_HEIGHT = 64;
    const colW = rect.width / GRID_COLUMNS;
    const rawX = Math.floor((event.clientX - rect.left - (currentLayout.w * colW) / 2) / colW);
    const rawY = Math.floor((event.clientY - rect.top - (currentLayout.h * ROW_HEIGHT) / 2) / ROW_HEIGHT);
    return {
      ...currentLayout,
      x: Math.max(0, Math.min(rawX, GRID_COLUMNS - currentLayout.w)),
      y: Math.max(0, rawY),
    };
  }

  const maxRow = controls.reduce((max, c) => {
    const l = effectiveLayout(c);
    return Math.max(max, l.y + l.h);
  }, 6);

  return (
    <div className="canvas-dashboard">
      <div className="canvas-toolbar">
        <button className="secondary-button secondary-button--sm" type="button" onClick={onRestoreDefaults}>
          <RotateCcw size={14} aria-hidden="true" />
          Restore Default Values
        </button>

        <button className="secondary-button secondary-button--sm" type="button" onClick={onToggleEditLayout}>
          {isEditingLayout ? "Edit Variables" : "Edit Dashboard"}
        </button>

        {hasLayoutOverrides ? (
          <button className="ghost-button ghost-button--sm" type="button" onClick={onResetLayout}>
            Reset Layout
          </button>
        ) : null}
      </div>

      <div
        id="canvas-dashboard-surface"
        className={`canvas-dashboard__surface${isEditingLayout ? " canvas-dashboard__surface--editing" : ""}`}
        style={{ "--canvas-rows": maxRow + 2 } as CSSProperties}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onDragLeave={handleDragLeave}
      >
        {controls.map((control) => {
          const layout = effectiveLayout(control);
          const isPreview = dragPreview?.controlId === control.id;
          const previewLayout = isPreview ? dragPreview!.layout : null;
          const displayLayout = previewLayout ?? layout;

          return (
            <CanvasWidgetCell
              key={control.id}
              control={control}
              layout={displayLayout}
              isPreview={isPreview}
              isEditingLayout={isEditingLayout}
              inputIndex={inputIndex}
              outputIndex={outputIndex}
              outputImagesByNodeId={outputImagesByNodeId}
              inputValues={inputValues}
              onChange={onChange}
              onImageUpload={onImageUpload}
              onDragStart={handleDragStart}
              onDragEnd={clearDrag}
            />
          );
        })}
      </div>

      <div className="canvas-run-footer">
        <div className="canvas-run-footer__progress">
          <div className="canvas-run-footer__progress-labels">
            <span>{runState.progress?.status ?? "Not started"}</span>
            <span>{runState.progressPercent}%</span>
          </div>
          <div className="progress-bar" aria-label="Workflow progress">
            <span style={{ width: `${runState.progressPercent}%` }} />
          </div>
        </div>

        <div className="canvas-run-footer__actions">
          <button
            className="primary-button"
            type="button"
            disabled={!runState.canRun}
            onClick={onRun}
          >
            {runState.isRunning ? (
              <Loader2 className="spin" size={18} aria-hidden="true" />
            ) : (
              <Play size={18} aria-hidden="true" />
            )}
            Run Workflow
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!runState.canCancel}
            onClick={onCancel}
          >
            <Square size={16} aria-hidden="true" />
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Single widget cell ───────────────────────────────────────────────────────

function CanvasWidgetCell({
  control,
  layout,
  isPreview,
  isEditingLayout,
  inputIndex,
  outputIndex,
  outputImagesByNodeId,
  inputValues,
  onChange,
  onImageUpload,
  onDragStart,
  onDragEnd,
}: {
  control: DashboardControlDef;
  layout: GridItemLayout;
  isPreview: boolean;
  isEditingLayout: boolean;
  inputIndex: Map<string, WorkflowInputDef>;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
  inputValues: Record<string, unknown>;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onDragStart: (event: DragEvent, controlId: string) => void;
  onDragEnd: () => void;
}) {
  const style: CSSProperties = {
    gridColumn: `${layout.x + 1} / span ${layout.w}`,
    gridRow: `${layout.y + 1} / span ${layout.h}`,
  };

  const isOutput = control.type === "display_image" || control.type === "result_image";

  return (
    <article
      className={`widget-canvas-cell${isPreview ? " widget-canvas-cell--preview" : ""}${isEditingLayout ? " widget-canvas-cell--editing" : ""}`}
      style={style}
      draggable={isEditingLayout}
      onDragStart={isEditingLayout ? (e) => onDragStart(e, control.id) : undefined}
      onDragEnd={isEditingLayout ? onDragEnd : undefined}
    >
      {isEditingLayout ? (
        <div className="widget-canvas-cell__drag-handle" aria-hidden="true">
          <GripVertical size={15} />
        </div>
      ) : null}

      <div className="widget-canvas-cell__label">
        <span>{control.label}</span>
        {control.description ? <small>{control.description}</small> : null}
      </div>

      <div className="widget-canvas-cell__content">
        {isOutput ? (
          <OutputWidgetContent
            control={control}
            outputIndex={outputIndex}
            outputImagesByNodeId={outputImagesByNodeId}
          />
        ) : (
          <InputWidgetContent
            control={control}
            inputIndex={inputIndex}
            inputValues={inputValues}
            disabled={isEditingLayout}
            onChange={onChange}
            onImageUpload={onImageUpload}
          />
        )}
      </div>
    </article>
  );
}

// ─── Output widget ────────────────────────────────────────────────────────────

function OutputWidgetContent({
  control,
  outputIndex,
  outputImagesByNodeId,
}: {
  control: DashboardControlDef;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
}) {
  const output = control.output_id ? outputIndex.get(control.output_id) : null;
  const imageUrls = output ? outputImagesByNodeId.get(output.node_id) ?? [] : [];
  const firstImageUrl = imageUrls[0];

  if (imageUrls.length > 0) {
    return (
      <div className="widget-output-image">
        <div className={`widget-output-image__grid${imageUrls.length > 1 ? " widget-output-image__grid--multi" : ""}`}>
          {imageUrls.map((imageUrl, index) => (
            <img
              key={`${imageUrl}-${index}`}
              src={imageUrl}
              alt={imageUrls.length > 1 ? `Generated workflow output ${index + 1}` : "Generated workflow output"}
            />
          ))}
        </div>
        {control.show_download && firstImageUrl ? (
          <a className="widget-output-image__download" href={firstImageUrl} download aria-label="Download image">
            <Download size={14} aria-hidden="true" />
            Download
          </a>
        ) : null}
      </div>
    );
  }

  return (
    <div className="widget-output-placeholder">
      <ImageIcon size={36} aria-hidden="true" />
      <span>Your generated image will appear here.</span>
    </div>
  );
}

// ─── Input widget ─────────────────────────────────────────────────────────────

function InputWidgetContent({
  control,
  inputIndex,
  inputValues,
  disabled,
  onChange,
  onImageUpload,
}: {
  control: DashboardControlDef;
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  disabled: boolean;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
}) {
  if (!control.input_id) return null;
  const input = inputIndex.get(control.input_id);
  if (!input) return null;

  return (
    <DashboardInputControl
      control={control}
      input={input}
      value={inputValues[input.id]}
      disabled={disabled}
      variant="canvas"
      onChange={(value) => onChange(input.id, value)}
      onImageUpload={(file) => onImageUpload(input.id, file)}
    />
  );
}

// ─── Drag payload helper ──────────────────────────────────────────────────────

function readDragControlId(event: DragEvent): string | null {
  const raw = event.dataTransfer.getData(DRAG_MIME_TYPE);
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { controlId?: unknown };
      if (typeof parsed.controlId === "string") return parsed.controlId;
    } catch { /* ignore */ }
  }
  const text = event.dataTransfer.getData("text/plain");
  if (text.startsWith(DRAG_TEXT_PREFIX)) return text.slice(DRAG_TEXT_PREFIX.length);
  return null;
}

function fromBackendLayout(layout: DashboardControlDef["layout"]): GridItemLayout {
  return {
    x: layout?.x ?? 0,
    y: layout?.y ?? 0,
    w: layout?.w ?? 4,
    h: layout?.h ?? 2,
    minW: layout?.min_w,
    minH: layout?.min_h,
  };
}
