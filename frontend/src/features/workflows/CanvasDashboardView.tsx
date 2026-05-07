import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
} from "react";
import {
  ChevronDown,
  Download,
  Image as ImageIcon,
  ImagePlus,
  Loader2,
  Play,
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
import { fitLayout, layoutsOverlap, type GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import {
  DASHBOARD_CANVAS_COLUMNS,
  DASHBOARD_CANVAS_GRID_GAP,
  DASHBOARD_CANVAS_ROW_HEIGHT,
  DashboardCanvasFrame,
  DashboardCanvasResizeHandles,
  DashboardCanvasSurface,
  DashboardCanvasWidgetShell,
  type DashboardResizeHandle,
  canvasRowsForItems,
  resizeLayoutFromPointerDelta,
} from "../dashboard-canvas/DashboardCanvasPresentation";
import { DashboardInputControl } from "./DashboardInputControl";

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
  runState: CanvasRunState;
  exportNoofyUrl: string;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onRun: () => void;
  onCancel: () => void;
  onRestoreDefaults: () => void;
  onEnterEditLayout: () => void;
  onSaveLayout: () => void;
  onCancelLayoutEdit: () => void;
  onEditWidgets?: () => void;
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
  runState,
  exportNoofyUrl,
  onChange,
  onImageUpload,
  onRun,
  onCancel,
  onRestoreDefaults,
  onEnterEditLayout,
  onSaveLayout,
  onCancelLayoutEdit,
  onEditWidgets,
  onLayoutOverride,
}: CanvasDashboardViewProps) {
  const [movingControlId, setMovingControlId] = useState<string | null>(null);
  const [movePreview, setMovePreview] = useState<{ controlId: string; layout: GridItemLayout } | null>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const optionsRef = useRef<HTMLDivElement | null>(null);
  const resizeStateRef = useRef<{
    controlId: string;
    handle: DashboardResizeHandle;
    startLayout: GridItemLayout;
    startClientX: number;
    startClientY: number;
  } | null>(null);
  const moveStateRef = useRef<{
    controlId: string;
    startLayout: GridItemLayout;
    startClientX: number;
    startClientY: number;
    columnWidth: number;
    lastLayout: GridItemLayout;
  } | null>(null);
  const canvasItems = useMemo(
    () => controls.map((control) => ({ id: control.id, layout: effectiveLayout(control) })),
    [controls, layoutOverrides],
  );

  useEffect(() => {
    if (!optionsOpen) return;

    function handlePointerDown(event: globalThis.PointerEvent) {
      const target = event.target;
      if (target instanceof Node && optionsRef.current?.contains(target)) return;
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

  function effectiveLayout(control: DashboardControlDef): GridItemLayout {
    if (layoutOverrides[control.id]) return withLayoutMinimums(layoutOverrides[control.id], control);
    if (control.layout) return fromBackendLayout(control);
    return defaultLayoutForWidgetType(control.type);
  }

  function resolveLayout(controlId: string, candidate: GridItemLayout): GridItemLayout {
    const fitted = fitLayout(candidate, DASHBOARD_CANVAS_COLUMNS);
    const collides = canvasItems.some((item) => {
      if (item.id === controlId || !item.layout) return false;
      return layoutsOverlap(fitted, item.layout);
    });
    return collides ? effectiveLayout(controls.find((control) => control.id === controlId)!) : fitted;
  }

  function handleMoveStart(
    event: PointerEvent<HTMLElement>,
    controlId: string,
    layout: GridItemLayout,
  ) {
    if (!isEditingLayout || shouldIgnoreWidgetMove(event.target)) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const columnWidth = rect.width / DASHBOARD_CANVAS_COLUMNS;
    moveStateRef.current = {
      controlId,
      startLayout: layout,
      startClientX: event.clientX,
      startClientY: event.clientY,
      columnWidth,
      lastLayout: layout,
    };
    setMovingControlId(controlId);
    setMovePreview({ controlId, layout });

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      const moveState = moveStateRef.current;
      if (!moveState) return;
      const deltaColumns = Math.round((pointerEvent.clientX - moveState.startClientX) / moveState.columnWidth);
      const deltaRows = Math.round((pointerEvent.clientY - moveState.startClientY) / DASHBOARD_CANVAS_ROW_HEIGHT);
      const candidate = fitLayout(
        {
          ...moveState.startLayout,
          x: Math.max(0, Math.min(moveState.startLayout.x + deltaColumns, DASHBOARD_CANVAS_COLUMNS - moveState.startLayout.w)),
          y: Math.max(0, moveState.startLayout.y + deltaRows),
        },
        DASHBOARD_CANVAS_COLUMNS,
      );
      if (
        candidate.x === moveState.lastLayout.x &&
        candidate.y === moveState.lastLayout.y &&
        candidate.w === moveState.lastLayout.w &&
        candidate.h === moveState.lastLayout.h
      ) {
        return;
      }
      const resolved = resolveLayout(moveState.controlId, candidate);
      if (resolved.x !== candidate.x || resolved.y !== candidate.y) return;
      moveState.lastLayout = resolved;
      setMovePreview({ controlId: moveState.controlId, layout: resolved });
    }

    function handlePointerUp() {
      const finalLayout = moveStateRef.current?.lastLayout;
      if (finalLayout) onLayoutOverride(controlId, finalLayout);
      moveStateRef.current = null;
      setMovingControlId(null);
      setMovePreview(null);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  function handleResizeStart(
    event: PointerEvent<HTMLButtonElement>,
    controlId: string,
    layout: GridItemLayout,
    handle: DashboardResizeHandle,
  ) {
    if (!isEditingLayout) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    resizeStateRef.current = {
      controlId,
      handle,
      startLayout: layout,
      startClientX: event.clientX,
      startClientY: event.clientY,
    };

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      const resizeState = resizeStateRef.current;
      if (!resizeState) return;
      const candidate = resizeLayoutFromPointerDelta({
        startLayout: resizeState.startLayout,
        startClientX: resizeState.startClientX,
        startClientY: resizeState.startClientY,
        clientX: pointerEvent.clientX,
        clientY: pointerEvent.clientY,
        canvas: canvasRef.current,
        handle: resizeState.handle,
      });
      onLayoutOverride(resizeState.controlId, resolveLayout(resizeState.controlId, candidate));
    }

    function handlePointerUp() {
      resizeStateRef.current = null;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  const canvasRows = canvasRowsForItems(canvasItems);

  return (
    <div className="canvas-dashboard">
      <DashboardCanvasFrame className="canvas-dashboard__canvas" aria-label="Workflow dashboard canvas">
        <div className="canvas-action-cluster" aria-label={isEditingLayout ? "Dashboard layout actions" : "Workflow actions"}>
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
              <div className="canvas-options-menu" ref={optionsRef}>
                <button
                  className="icon-button canvas-options-menu__trigger"
                  type="button"
                  aria-label="Workflow options"
                  aria-haspopup="menu"
                  aria-expanded={optionsOpen}
                  title="Workflow options"
                  onClick={() => setOptionsOpen((open) => !open)}
                >
                  <SlidersHorizontal size={18} aria-hidden="true" />
                </button>

                {optionsOpen ? (
                  <div className="canvas-options-menu__content" role="menu" aria-label="Workflow options">
                    <a
                      className="canvas-options-menu__item"
                      role="menuitem"
                      href={exportNoofyUrl}
                      download
                      onClick={() => setOptionsOpen(false)}
                    >
                      Export as Noofy
                    </a>
                    <button className="canvas-options-menu__item" role="menuitem" type="button" disabled>
                      Export as JSON
                    </button>
                    <button
                      className="canvas-options-menu__item"
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        onEnterEditLayout();
                        setOptionsOpen(false);
                      }}
                    >
                      Edit dashboard layout
                    </button>
                    <button
                      className="canvas-options-menu__item"
                      role="menuitem"
                      type="button"
                      disabled={!onEditWidgets}
                      onClick={() => {
                        onEditWidgets?.();
                        setOptionsOpen(false);
                      }}
                    >
                      Edit widgets
                    </button>
                    <button
                      className="canvas-options-menu__item"
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        onRestoreDefaults();
                        setOptionsOpen(false);
                      }}
                    >
                      Restore dashboard to the workflow default values
                    </button>
                  </div>
                ) : null}
              </div>

              <button
                className="secondary-button canvas-action-cluster__cancel"
                type="button"
                disabled={!runState.canCancel}
                onClick={onCancel}
              >
                <Square size={16} aria-hidden="true" />
                Cancel Run
              </button>
              <button
                className="primary-button canvas-action-cluster__run"
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
            </>
          )}
        </div>

        {!isEditingLayout ? (
        <div className="canvas-progress-overlay">
          <div className="canvas-progress-overlay__labels">
            <span>{runState.progress?.status ?? "Not started"}</span>
            <span>{runState.progressPercent}%</span>
          </div>
          <div className="progress-bar" aria-label="Workflow progress">
            <span style={{ width: `${runState.progressPercent}%` }} />
          </div>
        </div>
        ) : null}

        <DashboardCanvasSurface
          id="canvas-dashboard-surface"
          ref={canvasRef}
          className={isEditingLayout ? "canvas-dashboard__surface--editing" : ""}
          rows={canvasRows}
        >
          {controls.map((control) => {
            const layout = effectiveLayout(control);
            const previewLayout = movePreview?.controlId === control.id ? movePreview.layout : null;
            const displayLayout = previewLayout ?? layout;
            const isPreview = movingControlId === control.id;

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
                onMoveStart={(event) => handleMoveStart(event, control.id, displayLayout)}
                onResizeStart={(event, handle) => handleResizeStart(event, control.id, displayLayout, handle)}
              />
            );
          })}
        </DashboardCanvasSurface>
      </DashboardCanvasFrame>
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
  onMoveStart,
  onResizeStart,
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
  onMoveStart: (event: PointerEvent<HTMLElement>) => void;
  onResizeStart: (event: PointerEvent<HTMLButtonElement>, handle: DashboardResizeHandle) => void;
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
      onPointerDown={isEditingLayout ? onMoveStart : undefined}
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
      {isEditingLayout ? <DashboardCanvasResizeHandles label={control.label} onResizeStart={(handle, event) => onResizeStart(event, handle)} /> : null}
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

function shouldIgnoreWidgetMove(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      "button, input, textarea, select, a, [role='button'], .layout-canvas-resize-handle, .layout-canvas-resize-handles",
    ),
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

function fromBackendLayout(control: DashboardControlDef): GridItemLayout {
  const fallback = defaultLayoutForWidgetType(control.type);
  const layout = control.layout;
  return withLayoutMinimums({
    x: layout?.x ?? 0,
    y: layout?.y ?? 0,
    w: layout?.w ?? 4,
    h: layout?.h ?? 2,
    minW: layout?.min_w ?? fallback.minW,
    minH: layout?.min_h ?? fallback.minH,
  }, control);
}

function withLayoutMinimums(layout: GridItemLayout, control: DashboardControlDef): GridItemLayout {
  const fallback = defaultLayoutForWidgetType(control.type);
  return {
    ...layout,
    minW: layout.minW ?? control.layout?.min_w ?? fallback.minW,
    minH: layout.minH ?? control.layout?.min_h ?? fallback.minH,
  };
}
