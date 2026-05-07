import { useMemo, useRef, useState, type CSSProperties, type DragEvent } from "react";
import {
  ChevronDown,
  Download,
  Image as ImageIcon,
  ImagePlus,
  Loader2,
  Move,
  Play,
  RotateCcw,
  Shuffle,
  SlidersHorizontal,
  Square,
  Sparkles,
  ToggleLeft,
  Type,
  UploadCloud,
} from "lucide-react";

import {
  type DashboardControlDef,
  type JobProgress,
  type WorkflowInputDef,
  type WorkflowOutputDef,
} from "../../lib/api/noofyApi";
import { findAvailableLayout, fitLayout, type GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import {
  DASHBOARD_CANVAS_COLUMNS,
  DASHBOARD_CANVAS_GRID_GAP,
  DASHBOARD_CANVAS_ROW_HEIGHT,
  DashboardCanvasFrame,
  DashboardCanvasSurface,
  DashboardCanvasWidgetShell,
  canvasRowsForItems,
  layoutFromCanvasPointer,
} from "../dashboard-canvas/DashboardCanvasPresentation";
import { DashboardInputControl } from "./DashboardInputControl";

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
  const canvasRef = useRef<HTMLDivElement | null>(null);
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

    const preview = findAvailableLayout(controlId, desired, canvasItems, DASHBOARD_CANVAS_COLUMNS);
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

    const resolved = findAvailableLayout(
      controlId,
      fitLayout(newLayout, DASHBOARD_CANVAS_COLUMNS),
      canvasItems,
      DASHBOARD_CANVAS_COLUMNS,
    );
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
    return layoutFromCanvasPointer(event, currentLayout, canvasRef.current);
  }

  const canvasRows = canvasRowsForItems(canvasItems);

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

      <DashboardCanvasFrame className="canvas-dashboard__canvas" aria-label="Workflow dashboard canvas">
        <DashboardCanvasSurface
          id="canvas-dashboard-surface"
          ref={canvasRef}
          className={isEditingLayout ? "canvas-dashboard__surface--editing" : ""}
          rows={canvasRows}
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
        </DashboardCanvasSurface>
      </DashboardCanvasFrame>

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
  const isOutput = control.type === "display_image" || control.type === "result_image";
  const Icon = iconForControlType(control.type);

  return (
    <DashboardCanvasWidgetShell
      className={`layout-canvas-widget--run${
        isEditingLayout ? " layout-canvas-widget--run-editing" : " layout-canvas-widget--readonly"
      }`}
      layout={layout}
      preview={isPreview}
      style={
        { height: `${layout.h * DASHBOARD_CANVAS_ROW_HEIGHT - DASHBOARD_CANVAS_GRID_GAP}px` } as CSSProperties
      }
      draggable={isEditingLayout}
      onDragStart={isEditingLayout ? (e) => onDragStart(e, control.id) : undefined}
      onDragEnd={isEditingLayout ? onDragEnd : undefined}
    >
      <header className="layout-canvas-widget__header">
        <div className="layout-canvas-widget__title">
          <span aria-hidden="true">
            <Icon size={16} />
          </span>
          <div>
            <h3>{control.label}</h3>
            {control.description ? <p>{control.description}</p> : null}
          </div>
        </div>
        {isEditingLayout ? (
          <div className="layout-canvas-widget__actions">
            <button
              className="icon-button icon-button--card"
              type="button"
              aria-label={`Move ${control.label}`}
              title="Drag to move"
            >
              <Move size={14} aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </header>

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
    </DashboardCanvasWidgetShell>
  );
}

function iconForControlType(type: string): typeof Type {
  if (type === "slider") return SlidersHorizontal;
  if (type === "toggle") return ToggleLeft;
  if (type === "load_image") return ImagePlus;
  if (type === "load_image_mask") return UploadCloud;
  if (type === "display_image" || type === "result_image") return Sparkles;
  if (type === "seed_widget") return Shuffle;
  if (type === "lora_loader") return Sparkles;
  if (type === "select") return ChevronDown;
  return Type;
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
