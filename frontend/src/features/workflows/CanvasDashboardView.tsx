import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
  type SyntheticEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  ChevronDown,
  Download,
  ExternalLink,
  FileAudio,
  GripVertical,
  Image as ImageIcon,
  ImagePlus,
  LayoutGrid,
  Loader2,
  Maximize,
  Play,
  Shuffle,
  SlidersHorizontal,
  Square,
  Sparkles,
  StickyNote,
  ToggleLeft,
  Type,
  UploadCloud,
  Video,
  X,
} from "lucide-react";

import {
  type DashboardControlDef,
  type DashboardControlGroupDef,
  type OutputPreferences,
  type UploadProgress,
  type WorkflowInputDef,
  type WorkflowOutputDef,
} from "../../lib/api/noofyApi";
import { findNearestAvailableLayout, fitLayout, layoutsOverlap, type GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetGroup, defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import {
  DASHBOARD_CANVAS_COLUMNS,
  DASHBOARD_CANVAS_ROW_HEIGHT,
  DashboardCanvasFrame,
  DashboardCanvasResizeHandles,
  DashboardCanvasSurface,
  DashboardCanvasWidgetShell,
  type DashboardResizeHandle,
  canvasRowsForItems,
  fitMovedLayoutPosition,
  resizeLayoutFromPointerDelta,
  sameGridLayout,
} from "../dashboard-canvas/DashboardCanvasPresentation";
import { DashboardInputControl } from "./DashboardInputControl";
import type { LoraBrowserControlProps } from "./DashboardInputControl";
import { ImageComparisonSlider } from "./ImageComparisonSlider";
import { WorkflowExportDialog } from "./WorkflowExportDialog";
import { audioMetadataLabel, videoMetadataLabel, type OutputAudioMedia, type OutputVideoMedia } from "./media";
import type { WorkflowExportReviewModel } from "../../lib/workflowExport";
import { topLevelDashboardControlItems, type DashboardTopLevelControlItem } from "./dashboardTopLevelItems";

export interface CanvasRunState {
  isRunning: boolean;
  canRun: boolean;
  canCancel: boolean;
  disabledReason?: string | null;
  disabledActionLabel?: string | null;
}

export interface CanvasActionBarPosition {
  x: number;
  y: number;
}

const ACTION_BAR_BOUNDARY_PADDING = 10;

interface CanvasDashboardViewProps {
  controls: DashboardControlDef[];
  groups: DashboardControlGroupDef[];
  inputIndex: Map<string, WorkflowInputDef>;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
  outputAudiosByNodeId: Map<string, OutputAudioMedia[]>;
  outputVideosByNodeId: Map<string, OutputVideoMedia[]>;
  comparisonBeforeImageUrl?: string | null;
  inputValues: Record<string, unknown>;
  outputPreferences: OutputPreferences;
  layoutOverrides: Record<string, GridItemLayout>;
  actionBarPosition?: CanvasActionBarPosition | null;
  isEditingLayout: boolean;
  runState: CanvasRunState;
  exportNoofyUrl: string;
  exportComfyJsonUrl: string;
  exportWorkflowName?: string | null;
  exportReview?: WorkflowExportReviewModel;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
  onOutputPreferenceChange: (controlId: string, autoSave: boolean) => void;
  onRun: () => void;
  onCancel: () => void;
  onDisabledRunAction?: () => void;
  onRestoreDefaults: () => void;
  onEnterEditLayout: () => void;
  onSaveLayout: () => void;
  onCancelLayoutEdit: () => void;
  onEditWidgets?: () => void;
  onLayoutOverride: (controlId: string, layout: GridItemLayout) => void;
  onActionBarPositionChange: (position: CanvasActionBarPosition) => void;
}

export function CanvasDashboardView({
  controls,
  groups,
  inputIndex,
  outputIndex,
  outputImagesByNodeId,
  outputAudiosByNodeId,
  outputVideosByNodeId,
  comparisonBeforeImageUrl,
  inputValues,
  outputPreferences,
  layoutOverrides,
  actionBarPosition,
  isEditingLayout,
  runState,
  exportNoofyUrl,
  exportComfyJsonUrl,
  exportWorkflowName,
  exportReview,
  onChange,
  onImageUpload,
  onAudioUpload,
  onVideoUpload,
  loraBrowserFor,
  onOutputPreferenceChange,
  onRun,
  onCancel,
  onDisabledRunAction,
  onRestoreDefaults,
  onEnterEditLayout,
  onSaveLayout,
  onCancelLayoutEdit,
  onEditWidgets,
  onLayoutOverride,
  onActionBarPositionChange,
}: CanvasDashboardViewProps) {
  const [movingControlId, setMovingControlId] = useState<string | null>(null);
  const [movePreview, setMovePreview] = useState<{ controlId: string; layout: GridItemLayout } | null>(null);
  const [dropPreview, setDropPreview] = useState<{ controlId: string; layout: GridItemLayout } | null>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [exportDialog, setExportDialog] = useState<{ extension: ".noofy" | ".json"; url: string } | null>(null);
  const [draggingActionBarPosition, setDraggingActionBarPosition] = useState<CanvasActionBarPosition | null>(null);
  const [boundedActionBarPosition, setBoundedActionBarPosition] = useState<CanvasActionBarPosition | null>(null);
  const [actionBarBoundsVersion, setActionBarBoundsVersion] = useState(0);
  const frameRef = useRef<HTMLElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const actionBarRef = useRef<HTMLDivElement | null>(null);
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
    currentLayout: GridItemLayout;
    dropLayout: GridItemLayout;
  } | null>(null);
  const actionBarDragStateRef = useRef<{
    startPosition: CanvasActionBarPosition;
    startClientX: number;
    startClientY: number;
    currentPosition: CanvasActionBarPosition;
  } | null>(null);
  const topLevelItems = useMemo(
    () => topLevelDashboardControlItems(controls, groups),
    [controls, groups],
  );
  const canvasItems = useMemo(
    () => topLevelItems.map((item) => ({ id: item.id, layout: effectiveLayout(item) })),
    [topLevelItems, layoutOverrides],
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

  const requestedActionBarPosition = draggingActionBarPosition ?? actionBarPosition ?? null;

  useEffect(() => {
    function handleResize() {
      setActionBarBoundsVersion((version) => version + 1);
    }

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  useLayoutEffect(() => {
    if (!requestedActionBarPosition) {
      setBoundedActionBarPosition(null);
      return;
    }
    const clamped = clampActionBarPosition(
      requestedActionBarPosition,
      frameRef.current,
      actionBarRef.current,
    );
    setBoundedActionBarPosition((current) =>
      sameActionBarPosition(current, clamped) ? current : clamped,
    );
  }, [
    requestedActionBarPosition?.x,
    requestedActionBarPosition?.y,
    isEditingLayout,
    runState.canCancel,
    runState.canRun,
    runState.disabledActionLabel,
    runState.disabledReason,
    runState.isRunning,
    actionBarBoundsVersion,
  ]);

  function effectiveLayout(item: DashboardTopLevelControlItem): GridItemLayout {
    if (layoutOverrides[item.id]) return withLayoutMinimums(layoutOverrides[item.id], item);
    if (item.kind === "group") {
      const fallback = defaultLayoutForWidgetGroup(item.controls.map((control) => control.type));
      if (item.group.layout) return fromBackendLayout(item.group, fallback);
      return fallback;
    }
    if (item.control.layout) return fromBackendLayout(item.control);
    return defaultLayoutForWidgetType(item.control.type);
  }

  function resolveMoveDropLayout(controlId: string, candidate: GridItemLayout): GridItemLayout {
    const fitted = fitMovedLayoutPosition(candidate, DASHBOARD_CANVAS_COLUMNS);
    return findNearestAvailableLayout(controlId, fitted, canvasItems, DASHBOARD_CANVAS_COLUMNS);
  }

  function resolveResizedLayout(controlId: string, candidate: GridItemLayout): GridItemLayout {
    const fitted = fitLayout(candidate, DASHBOARD_CANVAS_COLUMNS);
    return layoutCollides(controlId, fitted)
      ? effectiveLayout(topLevelItems.find((item) => item.id === controlId)!)
      : fitted;
  }

  function layoutCollides(controlId: string, layout: GridItemLayout): boolean {
    const collides = canvasItems.some((item) => {
      if (item.id === controlId || !item.layout) return false;
      return layoutsOverlap(layout, item.layout);
    });
    return collides;
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
      currentLayout: layout,
      dropLayout: layout,
    };
    setMovingControlId(controlId);
    setMovePreview({ controlId, layout });
    setDropPreview({ controlId, layout });

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      const moveState = moveStateRef.current;
      if (!moveState) return;
      const deltaColumns = Math.round((pointerEvent.clientX - moveState.startClientX) / moveState.columnWidth);
      const deltaRows = Math.round((pointerEvent.clientY - moveState.startClientY) / DASHBOARD_CANVAS_ROW_HEIGHT);
      const candidate = fitMovedLayoutPosition(
        {
          ...moveState.startLayout,
          x: Math.max(0, Math.min(moveState.startLayout.x + deltaColumns, DASHBOARD_CANVAS_COLUMNS - moveState.startLayout.w)),
          y: Math.max(0, moveState.startLayout.y + deltaRows),
        },
        DASHBOARD_CANVAS_COLUMNS,
      );
      const dropLayout = resolveMoveDropLayout(moveState.controlId, candidate);
      if (sameGridLayout(candidate, moveState.currentLayout) && sameGridLayout(dropLayout, moveState.dropLayout)) return;
      moveState.currentLayout = candidate;
      moveState.dropLayout = dropLayout;
      setMovePreview({ controlId: moveState.controlId, layout: candidate });
      setDropPreview({ controlId: moveState.controlId, layout: dropLayout });
    }

    function finishMove(shouldCommit: boolean) {
      const finalLayout = moveStateRef.current?.dropLayout;
      if (shouldCommit && finalLayout) onLayoutOverride(controlId, finalLayout);
      moveStateRef.current = null;
      setMovingControlId(null);
      setMovePreview(null);
      setDropPreview(null);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerCancel);
    }

    function handlePointerUp() {
      finishMove(true);
    }

    function handlePointerCancel() {
      finishMove(false);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerCancel);
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
      onLayoutOverride(resizeState.controlId, resolveResizedLayout(resizeState.controlId, candidate));
    }

    function handlePointerUp() {
      resizeStateRef.current = null;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  function handleActionBarDragStart(event: PointerEvent<HTMLButtonElement>) {
    const frame = frameRef.current;
    const actionBar = actionBarRef.current;
    if (!frame || !actionBar) return;

    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    event.currentTarget.ownerDocument.getSelection()?.removeAllRanges();

    const frameRect = frame.getBoundingClientRect();
    const actionBarRect = actionBar.getBoundingClientRect();
    const startPosition = clampActionBarPosition(
      {
        x: actionBarRect.left - frameRect.left,
        y: actionBarRect.top - frameRect.top,
      },
      frame,
      actionBar,
    );
    actionBarDragStateRef.current = {
      startPosition,
      startClientX: event.clientX,
      startClientY: event.clientY,
      currentPosition: startPosition,
    };
    setDraggingActionBarPosition(startPosition);

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      const dragState = actionBarDragStateRef.current;
      if (!dragState) return;
      pointerEvent.preventDefault();
      const nextPosition = clampActionBarPosition(
        {
          x: dragState.startPosition.x + pointerEvent.clientX - dragState.startClientX,
          y: dragState.startPosition.y + pointerEvent.clientY - dragState.startClientY,
        },
        frameRef.current,
        actionBarRef.current,
      );
      if (sameActionBarPosition(dragState.currentPosition, nextPosition)) return;
      dragState.currentPosition = nextPosition;
      setDraggingActionBarPosition(nextPosition);
    }

    function finishDrag(shouldCommit: boolean) {
      const finalPosition = actionBarDragStateRef.current?.currentPosition;
      actionBarDragStateRef.current = null;
      setDraggingActionBarPosition(null);
      if (shouldCommit && finalPosition) onActionBarPositionChange(finalPosition);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerCancel);
    }

    function handlePointerUp() {
      finishDrag(true);
    }

    function handlePointerCancel() {
      finishDrag(false);
    }

    window.addEventListener("pointermove", handlePointerMove, { passive: false });
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerCancel);
  }

  const canvasRows = canvasRowsForItems(canvasItems);
  const displayedActionBarPosition = requestedActionBarPosition
    ? boundedActionBarPosition ?? requestedActionBarPosition
    : null;
  const actionBarStyle = displayedActionBarPosition
    ? {
        left: `${displayedActionBarPosition.x}px`,
        right: "auto",
        top: `${displayedActionBarPosition.y}px`,
      }
    : undefined;

  return (
    <div className="canvas-dashboard">
      <DashboardCanvasFrame ref={frameRef} className="canvas-dashboard__canvas" aria-label="Workflow dashboard canvas">
        <div
          ref={actionBarRef}
          className={`canvas-action-cluster${displayedActionBarPosition ? " canvas-action-cluster--positioned" : ""}${
            draggingActionBarPosition ? " canvas-action-cluster--dragging" : ""
          }`}
          style={actionBarStyle}
          aria-label={isEditingLayout ? "Dashboard layout actions" : "Workflow actions"}
        >
          <button
            className="canvas-action-cluster__drag-handle"
            type="button"
            aria-label="Move workflow action bar"
            title="Move action bar"
            onPointerDown={handleActionBarDragStart}
          >
            <GripVertical size={14} aria-hidden="true" />
          </button>
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
                  <SlidersHorizontal size={16} aria-hidden="true" />
                </button>

                {optionsOpen ? (
                  <div className="canvas-options-menu__content" role="menu" aria-label="Workflow options">
                    <button
                      className="canvas-options-menu__item"
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        setOptionsOpen(false);
                        setExportDialog({ extension: ".noofy", url: exportNoofyUrl });
                      }}
                    >
                      Export as Noofy
                    </button>
                    <button
                      className="canvas-options-menu__item"
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        setOptionsOpen(false);
                        setExportDialog({ extension: ".json", url: exportComfyJsonUrl });
                      }}
                    >
                      Export ComfyUI JSON
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
                <Square size={14} aria-hidden="true" />
                Cancel Run
              </button>
              <button
                className="primary-button canvas-action-cluster__run"
                type="button"
                disabled={!runState.canRun}
                title={!runState.canRun && runState.disabledReason ? runState.disabledReason : undefined}
                aria-describedby={!runState.canRun && runState.disabledReason ? "canvas-run-disabled-reason" : undefined}
                onClick={onRun}
              >
                {runState.isRunning ? (
                  <Loader2 className="spin" size={16} aria-hidden="true" />
                ) : (
                  <Play size={16} aria-hidden="true" />
                )}
                Run Workflow
              </button>
              {!runState.canRun && runState.disabledReason ? (
                <div className="canvas-action-cluster__reason" id="canvas-run-disabled-reason" role="status">
                  <AlertCircle size={14} aria-hidden="true" />
                  <span>{runState.disabledReason}</span>
                </div>
              ) : null}
              {!runState.canRun && runState.disabledActionLabel && onDisabledRunAction ? (
                <button
                  className="secondary-button canvas-action-cluster__download"
                  type="button"
                  onClick={onDisabledRunAction}
                >
                  <Download size={14} aria-hidden="true" />
                  {runState.disabledActionLabel}
                </button>
              ) : null}
            </>
          )}
        </div>

        <DashboardCanvasSurface
          id="canvas-dashboard-surface"
          ref={canvasRef}
          className={isEditingLayout ? "canvas-dashboard__surface--editing" : ""}
          rows={canvasRows}
        >
          {dropPreview ? (
            <CanvasWidgetDropPreview
              item={topLevelItems.find((item) => item.id === dropPreview.controlId) ?? null}
              layout={dropPreview.layout}
            />
          ) : null}

          {topLevelItems.map((item) => {
            const layout = effectiveLayout(item);
            const previewLayout = movePreview?.controlId === item.id ? movePreview.layout : null;
            const displayLayout = previewLayout ?? layout;
            const isMoving = movingControlId === item.id;

            return (
              <CanvasWidgetCell
                key={item.id}
                item={item}
                layout={displayLayout}
                isMoving={isMoving}
                isEditingLayout={isEditingLayout}
                inputIndex={inputIndex}
                outputIndex={outputIndex}
                outputImagesByNodeId={outputImagesByNodeId}
                outputAudiosByNodeId={outputAudiosByNodeId}
                outputVideosByNodeId={outputVideosByNodeId}
                comparisonBeforeImageUrl={comparisonBeforeImageUrl}
                inputValues={inputValues}
                outputPreferences={outputPreferences}
                onChange={onChange}
                onImageUpload={onImageUpload}
                onAudioUpload={onAudioUpload}
                onVideoUpload={onVideoUpload}
                loraBrowserFor={loraBrowserFor}
                onOutputPreferenceChange={onOutputPreferenceChange}
                onMoveStart={(event) => handleMoveStart(event, item.id, displayLayout)}
                onResizeStart={(event, handle) => handleResizeStart(event, item.id, displayLayout, handle)}
              />
            );
          })}
        </DashboardCanvasSurface>
      </DashboardCanvasFrame>
      {exportDialog ? (
        <WorkflowExportDialog
          workflowName={exportWorkflowName}
          exportUrl={exportDialog.url}
          extension={exportDialog.extension}
          inputValues={inputValues}
          review={exportDialog.extension === ".noofy" ? exportReview : undefined}
          onClose={() => setExportDialog(null)}
        />
      ) : null}
    </div>
  );
}

// ─── Single widget cell ───────────────────────────────────────────────────────

function CanvasWidgetDropPreview({
  item,
  layout,
}: {
  item: DashboardTopLevelControlItem | null;
  layout: GridItemLayout;
}) {
  if (!item) return null;
  const Icon = item.kind === "group" ? LayoutGrid : iconForControlType(item.control.type);
  const title = item.kind === "group" ? item.group.title : item.control.label;
  const description = item.kind === "group" ? item.group.description : item.control.type === "note" ? undefined : item.control.description;

  return (
    <DashboardCanvasWidgetShell
      className="layout-canvas-widget--run layout-canvas-widget--preview layout-canvas-widget--drop-preview"
      layout={layout}
      style={{ height: `${layout.h * DASHBOARD_CANVAS_ROW_HEIGHT}px` }}
      aria-hidden="true"
    >
      <header className="layout-canvas-widget__header">
        <div className="layout-canvas-widget__title">
          <span aria-hidden="true">
            <Icon size={16} />
          </span>
          <div>
            <h3>{title}</h3>
            {description ? <p>{description}</p> : null}
          </div>
        </div>
      </header>
    </DashboardCanvasWidgetShell>
  );
}

function CanvasWidgetCell({
  item,
  layout,
  isMoving,
  isEditingLayout,
  inputIndex,
  outputIndex,
  outputImagesByNodeId,
  outputAudiosByNodeId,
  outputVideosByNodeId,
  comparisonBeforeImageUrl,
  inputValues,
  outputPreferences,
  onChange,
  onImageUpload,
  onAudioUpload,
  onVideoUpload,
  loraBrowserFor,
  onOutputPreferenceChange,
  onMoveStart,
  onResizeStart,
}: {
  item: DashboardTopLevelControlItem;
  layout: GridItemLayout;
  isMoving: boolean;
  isEditingLayout: boolean;
  inputIndex: Map<string, WorkflowInputDef>;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
  outputAudiosByNodeId: Map<string, OutputAudioMedia[]>;
  outputVideosByNodeId: Map<string, OutputVideoMedia[]>;
  comparisonBeforeImageUrl?: string | null;
  inputValues: Record<string, unknown>;
  outputPreferences: OutputPreferences;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
  onOutputPreferenceChange: (controlId: string, autoSave: boolean) => void;
  onMoveStart: (event: PointerEvent<HTMLElement>) => void;
  onResizeStart: (event: PointerEvent<HTMLButtonElement>, handle: DashboardResizeHandle) => void;
}) {
  const isGroup = item.kind === "group";
  const control = item.kind === "control" ? item.control : null;
  const isOutput = control ? control.type === "display_image" || control.type === "display_audio" || control.type === "display_video" || control.type === "result_image" : false;
  const Icon = isGroup ? LayoutGrid : iconForControlType(control!.type);
  const title = isGroup ? item.group.title : control!.label;
  const description = isGroup ? item.group.description : control!.type === "note" ? undefined : control!.description;

  return (
    <DashboardCanvasWidgetShell
      className={`layout-canvas-widget--run${
        isEditingLayout ? " layout-canvas-widget--run-editing" : " layout-canvas-widget--readonly"
      }${isMoving ? " layout-canvas-widget--moving" : ""}`}
      layout={layout}
      style={{ height: `${layout.h * DASHBOARD_CANVAS_ROW_HEIGHT}px` }}
      onPointerDown={isEditingLayout ? onMoveStart : undefined}
    >
      <header className="layout-canvas-widget__header">
        <div className="layout-canvas-widget__title">
          <span aria-hidden="true">
            <Icon size={16} />
          </span>
          <div>
            <h3>{title}</h3>
            {description ? <p>{description}</p> : null}
          </div>
        </div>
        {control && isOutput ? (
          <button
            className={`auto-save-toggle${outputPreferences[control.id]?.auto_save ? " auto-save-toggle--on" : ""}`}
            type="button"
            aria-pressed={Boolean(outputPreferences[control.id]?.auto_save)}
            aria-label={`${outputPreferences[control.id]?.auto_save ? "Disable" : "Enable"} Auto Save for ${control.label}`}
            onClick={(event) => {
              event.stopPropagation();
              onOutputPreferenceChange(control.id, !outputPreferences[control.id]?.auto_save);
            }}
          >
            <span className="auto-save-toggle__track" aria-hidden="true">
              <span className="auto-save-toggle__knob" />
            </span>
            <span>Auto Save</span>
          </button>
        ) : null}
      </header>

      <div className="widget-canvas-cell__content">
        {item.kind === "group" ? (
          <GroupedCanvasControls
            item={item}
            inputIndex={inputIndex}
            outputIndex={outputIndex}
            outputImagesByNodeId={outputImagesByNodeId}
            outputAudiosByNodeId={outputAudiosByNodeId}
            outputVideosByNodeId={outputVideosByNodeId}
            comparisonBeforeImageUrl={comparisonBeforeImageUrl}
            inputValues={inputValues}
            outputPreferences={outputPreferences}
            disabled={isEditingLayout}
            onChange={onChange}
            onImageUpload={onImageUpload}
            onAudioUpload={onAudioUpload}
            onVideoUpload={onVideoUpload}
            loraBrowserFor={loraBrowserFor}
            onOutputPreferenceChange={onOutputPreferenceChange}
          />
        ) : control!.type === "note" ? (
          <DashboardNoteBody body={control!.description} />
        ) : isOutput ? (
          <OutputWidgetContent
            control={control!}
            outputIndex={outputIndex}
            outputImagesByNodeId={outputImagesByNodeId}
            outputAudiosByNodeId={outputAudiosByNodeId}
            outputVideosByNodeId={outputVideosByNodeId}
            comparisonBeforeImageUrl={comparisonBeforeImageUrl}
            imagePreviewEnabled={!isEditingLayout}
          />
        ) : (
          <InputWidgetContent
            control={control!}
            inputIndex={inputIndex}
            inputValues={inputValues}
            disabled={isEditingLayout}
            onChange={onChange}
            onImageUpload={onImageUpload}
            onAudioUpload={onAudioUpload}
            onVideoUpload={onVideoUpload}
            loraBrowserFor={loraBrowserFor}
          />
        )}
      </div>
      {isEditingLayout ? <DashboardCanvasResizeHandles label={title} onResizeStart={(handle, event) => onResizeStart(event, handle)} /> : null}
    </DashboardCanvasWidgetShell>
  );
}

function GroupedCanvasControls({
  item,
  inputIndex,
  outputIndex,
  outputImagesByNodeId,
  outputAudiosByNodeId,
  outputVideosByNodeId,
  comparisonBeforeImageUrl,
  inputValues,
  outputPreferences,
  disabled,
  onChange,
  onImageUpload,
  onAudioUpload,
  onVideoUpload,
  loraBrowserFor,
  onOutputPreferenceChange,
}: {
  item: Extract<DashboardTopLevelControlItem, { kind: "group" }>;
  inputIndex: Map<string, WorkflowInputDef>;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
  outputAudiosByNodeId: Map<string, OutputAudioMedia[]>;
  outputVideosByNodeId: Map<string, OutputVideoMedia[]>;
  comparisonBeforeImageUrl?: string | null;
  inputValues: Record<string, unknown>;
  outputPreferences: OutputPreferences;
  disabled: boolean;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
  onOutputPreferenceChange: (controlId: string, autoSave: boolean) => void;
}) {
  return (
    <div className="canvas-widget-group">
      {item.controls.map((control) => {
        const isOutput = control.type === "display_image" || control.type === "display_audio" || control.type === "display_video" || control.type === "result_image";
        return (
          <div className="canvas-widget-group__control" key={control.id}>
            {control.type !== "note" && control.description ? <p className="canvas-widget-group__description">{control.description}</p> : null}
            {control.type === "note" ? (
              <DashboardNoteBody title={control.label} body={control.description} />
            ) : isOutput ? (
              <>
                <OutputWidgetContent
                  control={control}
                  outputIndex={outputIndex}
                  outputImagesByNodeId={outputImagesByNodeId}
                  outputAudiosByNodeId={outputAudiosByNodeId}
                  outputVideosByNodeId={outputVideosByNodeId}
                  comparisonBeforeImageUrl={comparisonBeforeImageUrl}
                  imagePreviewEnabled={!disabled}
                />
                <button
                  className={`auto-save-toggle canvas-widget-group__auto-save${
                    outputPreferences[control.id]?.auto_save ? " auto-save-toggle--on" : ""
                  }`}
                  type="button"
                  aria-pressed={Boolean(outputPreferences[control.id]?.auto_save)}
                  aria-label={`${outputPreferences[control.id]?.auto_save ? "Disable" : "Enable"} Auto Save for ${control.label}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onOutputPreferenceChange(control.id, !outputPreferences[control.id]?.auto_save);
                  }}
                >
                  <span className="auto-save-toggle__track" aria-hidden="true">
                    <span className="auto-save-toggle__knob" />
                  </span>
                  <span>Auto Save</span>
                </button>
              </>
            ) : (
              <InputWidgetContent
                control={control}
                inputIndex={inputIndex}
                inputValues={inputValues}
                disabled={disabled}
                onChange={onChange}
                onImageUpload={onImageUpload}
                onAudioUpload={onAudioUpload}
                onVideoUpload={onVideoUpload}
                loraBrowserFor={loraBrowserFor}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function iconForControlType(type: string): typeof Type {
  if (type === "slider") return SlidersHorizontal;
  if (type === "toggle") return ToggleLeft;
  if (type === "load_image") return ImagePlus;
  if (type === "load_image_mask") return UploadCloud;
  if (type === "load_audio") return FileAudio;
  if (type === "load_video") return Video;
  if (type === "display_image" || type === "result_image") return Sparkles;
  if (type === "display_audio") return FileAudio;
  if (type === "display_video") return Video;
  if (type === "seed_widget") return Shuffle;
  if (type === "lora_loader") return Sparkles;
  if (type === "select") return ChevronDown;
  if (type === "note") return StickyNote;
  return Type;
}

function DashboardNoteBody({ title, body }: { title?: string; body?: string }) {
  return (
    <div className="dashboard-note-card dashboard-note-card--canvas">
      {title ? <h3>{title}</h3> : null}
      <p>{body || "No note text added yet."}</p>
    </div>
  );
}

function shouldIgnoreWidgetMove(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      "button, input, textarea, select, audio, video, a, [role='button'], .layout-canvas-resize-handle, .layout-canvas-resize-handles",
    ),
  );
}

function capturePointer(target: Element, pointerId: number) {
  try {
    target.setPointerCapture?.(pointerId);
  } catch {
    // Window-level listeners keep dragging active if pointer capture is not available.
  }
}

// ─── Output widget ────────────────────────────────────────────────────────────

function OutputWidgetContent({
  control,
  outputIndex,
  outputImagesByNodeId,
  outputAudiosByNodeId,
  outputVideosByNodeId,
  comparisonBeforeImageUrl,
  imagePreviewEnabled = true,
}: {
  control: DashboardControlDef;
  outputIndex: Map<string, WorkflowOutputDef>;
  outputImagesByNodeId: Map<string, string[]>;
  outputAudiosByNodeId: Map<string, OutputAudioMedia[]>;
  outputVideosByNodeId: Map<string, OutputVideoMedia[]>;
  comparisonBeforeImageUrl?: string | null;
  imagePreviewEnabled?: boolean;
}) {
  const output = control.output_id ? outputIndex.get(control.output_id) : null;
  const outputKind = output?.kind ?? output?.type;
  const wantsAudio = outputKind === "audio" || control.type === "display_audio";
  const wantsVideo = outputKind === "video" || control.type === "display_video";
  const imageUrls = output && !wantsAudio && !wantsVideo ? outputImagesByNodeId.get(output.node_id) ?? [] : [];
  const audioOutputs = output && wantsAudio ? outputAudiosByNodeId.get(output.node_id) ?? [] : [];
  const videoOutputs = output && wantsVideo ? outputVideosByNodeId.get(output.node_id) ?? [] : [];
  const firstImageUrl = imageUrls[0];
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string; beforeImageUrl?: string } | null>(null);

  useEffect(() => {
    if (previewImage && !imageUrls.includes(previewImage.url)) {
      setPreviewImage(null);
    }
  }, [imageUrls, previewImage]);

  if (imageUrls.length > 0) {
    return (
      <div className="widget-output-image">
        <div className={`widget-output-image__grid${imageUrls.length > 1 ? " widget-output-image__grid--multi" : ""}`}>
          {imageUrls.map((imageUrl, index) => {
            const alt = imageUrls.length > 1 ? `Generated workflow output ${index + 1}` : "Generated workflow output";
            const canCompare = Boolean(imagePreviewEnabled && comparisonBeforeImageUrl);
            if (!imagePreviewEnabled) {
              return (
                <div className="widget-output-image__preview widget-output-image__preview--static" key={`${imageUrl}-${index}`}>
                  <img src={imageUrl} alt={alt} />
                </div>
              );
            }
            if (canCompare && comparisonBeforeImageUrl) {
              return (
                <div
                  className="widget-output-image__preview widget-output-image__preview--comparison"
                  key={`${imageUrl}-${index}`}
                >
                  <ImageComparisonSlider
                    beforeSrc={comparisonBeforeImageUrl}
                    afterSrc={imageUrl}
                    alt={alt}
                    onOpen={() => setPreviewImage({ url: imageUrl, alt, beforeImageUrl: comparisonBeforeImageUrl })}
                  />
                </div>
              );
            }
            return (
              <button
                key={`${imageUrl}-${index}`}
                className="widget-output-image__preview"
                type="button"
                aria-label={`Open ${alt} full-screen`}
                onClick={(event) => {
                  event.stopPropagation();
                  setPreviewImage({ url: imageUrl, alt });
                }}
              >
                <img src={imageUrl} alt={alt} />
              </button>
            );
          })}
        </div>
        {firstImageUrl ? (
          <button
            className="widget-output-image__download"
            type="button"
            aria-label={`Download ${control.label} image`}
            title="Download image"
            onClick={(event) => {
              event.stopPropagation();
              void downloadImage(firstImageUrl).catch((error: unknown) => {
                console.error("Image download failed", error);
              });
            }}
          >
            <Download size={14} aria-hidden="true" />
            Download
          </button>
        ) : null}
        {previewImage ? (
          <ImagePreviewViewer
            imageUrl={previewImage.url}
            beforeImageUrl={previewImage.beforeImageUrl}
            alt={previewImage.alt}
            label={control.label}
            onClose={() => setPreviewImage(null)}
          />
        ) : null}
      </div>
    );
  }

  if (audioOutputs.length > 0) {
    return (
      <div className="widget-output-audio">
        {audioOutputs.map((audio, index) => (
          <div className="widget-output-audio__item" key={`${audio.url}-${index}`}>
            <audio className="widget-output-audio__player" controls src={audio.url} preload="metadata" />
            <div className="widget-output-audio__meta">
              <strong>{audio.filename}</strong>
              <span>{audioOutputMetaLabel(audio)}</span>
            </div>
            <div className="widget-output-audio__actions">
              <button
                className="secondary-button secondary-button--small"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  downloadMediaDirect(audio.url, audio.filename);
                }}
              >
                <Download size={14} aria-hidden="true" />
                Download
              </button>
              <button
                className="secondary-button secondary-button--small"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  window.open(audio.url, "_blank", "noopener,noreferrer");
                }}
              >
                <ExternalLink size={14} aria-hidden="true" />
                Open
              </button>
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (videoOutputs.length > 0) {
    return (
      <div className="widget-output-video">
        {videoOutputs.map((video, index) => (
          <div className="widget-output-video__item" key={`${video.url}-${index}`}>
            <video className="widget-output-video__player" controls src={video.url} poster={video.thumbnailUrl ?? undefined} preload="metadata" />
            <div className="widget-output-video__meta">
              <strong>{video.filename}</strong>
              <span>{videoOutputMetaLabel(video)}</span>
            </div>
            <div className="widget-output-video__actions">
              <button
                className="secondary-button secondary-button--small"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  downloadMediaDirect(video.url, video.filename);
                }}
              >
                <Download size={14} aria-hidden="true" />
                Download
              </button>
              <button
                className="secondary-button secondary-button--small"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  window.open(video.url, "_blank", "noopener,noreferrer");
                }}
              >
                <ExternalLink size={14} aria-hidden="true" />
                Open
              </button>
              <button
                className="secondary-button secondary-button--small"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  const player = event.currentTarget.closest(".widget-output-video__item")?.querySelector("video");
                  void player?.requestFullscreen?.();
                }}
              >
                <Maximize size={14} aria-hidden="true" />
                Fullscreen
              </button>
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="widget-output-placeholder">
      {wantsAudio ? <FileAudio size={36} aria-hidden="true" /> : wantsVideo ? <Video size={36} aria-hidden="true" /> : <ImageIcon size={36} aria-hidden="true" />}
      <span>{wantsAudio ? "Your generated audio will appear here." : wantsVideo ? "Your generated video will appear here." : "Your generated image will appear here."}</span>
    </div>
  );
}

function ImagePreviewViewer({
  imageUrl,
  beforeImageUrl,
  alt,
  label,
  onClose,
}: {
  imageUrl: string;
  beforeImageUrl?: string;
  alt: string;
  label: string;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const viewerRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startX: number;
    startY: number;
  } | null>(null);
  const previousImageUrlRef = useRef(imageUrl);
  const lastTapRef = useRef<{ time: number; clientX: number; clientY: number } | null>(null);
  const gestureScaleRef = useRef(1);
  const [naturalImageSize, setNaturalImageSize] = useState<{ width: number; height: number } | null>(null);
  const [stageSize, setStageSize] = useState<{ width: number; height: number } | null>(null);
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });
  const isZoomed = transform.scale > 1.001;

  const measureImageStage = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    setStageSize((current) => {
      if (current && Math.abs(current.width - rect.width) < 0.5 && Math.abs(current.height - rect.height) < 0.5) {
        return current;
      }
      return { width: rect.width, height: rect.height };
    });
  }, []);

  const zoomAtPoint = useCallback((factor: number, point: { x: number; y: number }) => {
    if (!Number.isFinite(factor) || factor <= 0) return;
    setTransform((current) => {
      const nextScale = clampImageScale(current.scale * factor);
      if (Math.abs(nextScale - current.scale) < 0.001) return current;
      if (nextScale === 1) return { scale: 1, x: 0, y: 0 };
      const ratio = nextScale / current.scale;
      return {
        scale: nextScale,
        x: point.x - ratio * (point.x - current.x),
        y: point.y - ratio * (point.y - current.y),
      };
    });
  }, []);

  const fittedImageSize = useMemo(() => {
    if (!naturalImageSize || !stageSize) return null;
    const fitScale = Math.min(1, stageSize.width / naturalImageSize.width, stageSize.height / naturalImageSize.height);
    if (!Number.isFinite(fitScale) || fitScale <= 0) return null;
    return {
      width: Math.max(1, naturalImageSize.width * fitScale),
      height: Math.max(1, naturalImageSize.height * fitScale),
    };
  }, [naturalImageSize, stageSize]);

  useEffect(() => {
    if (previousImageUrlRef.current === imageUrl) return;
    previousImageUrlRef.current = imageUrl;
    setNaturalImageSize(null);
    setStageSize(null);
    setTransform({ scale: 1, x: 0, y: 0 });
  }, [imageUrl]);

  useLayoutEffect(() => {
    measureImageStage();
    const stage = stageRef.current;
    if (!stage) return;

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(() => measureImageStage());
      observer.observe(stage);
      return () => observer.disconnect();
    }

    window.addEventListener("resize", measureImageStage);
    return () => window.removeEventListener("resize", measureImageStage);
  }, [measureImageStage]);

  useEffect(() => {
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const stage = stageRef.current;
    if (!viewer || !stage) return;
    const viewerElement = viewer;
    const stageElement = stage;

    function handleWheel(event: globalThis.WheelEvent) {
      event.preventDefault();
      event.stopPropagation();
      const point = viewerPointFromClient(stageElement, event.clientX, event.clientY);
      zoomAtPoint(Math.exp(-event.deltaY * IMAGE_VIEWER_WHEEL_ZOOM_SENSITIVITY), point);
    }

    viewerElement.addEventListener("wheel", handleWheel, { passive: false });
    return () => viewerElement.removeEventListener("wheel", handleWheel);
  }, [zoomAtPoint]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const stage = stageRef.current;
    if (!viewer || !stage) return;
    const viewerElement = viewer;
    const stageElement = stage;

    function handleGestureStart(event: Event) {
      event.preventDefault();
      gestureScaleRef.current = 1;
    }

    function handleGestureChange(event: Event) {
      event.preventDefault();
      const gestureEvent = event as Event & { scale?: number; clientX?: number; clientY?: number };
      const gestureScale = gestureEvent.scale ?? 1;
      const stageRect = stageElement.getBoundingClientRect();
      const point = viewerPointFromClient(
        stageElement,
        gestureEvent.clientX ?? stageRect.left + stageRect.width / 2,
        gestureEvent.clientY ?? stageRect.top + stageRect.height / 2,
      );
      zoomAtPoint(Math.pow(gestureScale / gestureScaleRef.current, IMAGE_VIEWER_GESTURE_ZOOM_POWER), point);
      gestureScaleRef.current = gestureScale;
    }

    viewerElement.addEventListener("gesturestart", handleGestureStart, { passive: false });
    viewerElement.addEventListener("gesturechange", handleGestureChange, { passive: false });
    return () => {
      viewerElement.removeEventListener("gesturestart", handleGestureStart);
      viewerElement.removeEventListener("gesturechange", handleGestureChange);
    };
  }, [zoomAtPoint]);

  function resetImageView() {
    setTransform({ scale: 1, x: 0, y: 0 });
  }

  function handleImageLoad(event: SyntheticEvent<HTMLImageElement>) {
    const { naturalWidth, naturalHeight } = event.currentTarget;
    if (naturalWidth > 0 && naturalHeight > 0) {
      setNaturalImageSize({ width: naturalWidth, height: naturalHeight });
    }
    measureImageStage();
  }

  function handleImageDoubleClick(event: MouseEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();
    const stage = stageRef.current;
    const point = stage ? viewerPointFromClient(stage, event.clientX, event.clientY) : { x: 0, y: 0 };
    zoomAtPoint(isZoomed ? 1.6 : 2.5, point);
  }

  function handleImagePointerDown(event: PointerEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();

    const lastTap = lastTapRef.current;
    const now = window.performance.now();
    if (
      event.pointerType === "touch" &&
      lastTap &&
      now - lastTap.time < 320 &&
      Math.hypot(event.clientX - lastTap.clientX, event.clientY - lastTap.clientY) < 28
    ) {
      lastTapRef.current = null;
      const stage = stageRef.current;
      zoomAtPoint(isZoomed ? 1.6 : 2.5, stage ? viewerPointFromClient(stage, event.clientX, event.clientY) : { x: 0, y: 0 });
      return;
    }
    lastTapRef.current = { time: now, clientX: event.clientX, clientY: event.clientY };

    if (!isZoomed) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: transform.x,
      startY: transform.y,
    };
  }

  function handleImagePointerMove(event: PointerEvent<HTMLElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    setTransform((current) => ({
      ...current,
      x: drag.startX + event.clientX - drag.startClientX,
      y: drag.startY + event.clientY - drag.startClientY,
    }));
  }

  function finishImageDrag(event: PointerEvent<HTMLElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = null;
  }

  return createPortal(
    <div ref={viewerRef} className="widget-image-viewer" role="dialog" aria-modal="true" aria-label={`${label} full-screen preview`}>
      <div className="widget-image-viewer__bar">
        <button className="widget-image-viewer__reset" type="button" disabled={!isZoomed} onClick={resetImageView}>
          Reset View
        </button>
        <button
          ref={closeButtonRef}
          className="widget-image-viewer__close"
          type="button"
          aria-label="Close full-screen image preview"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
          Close
        </button>
      </div>
      <div ref={stageRef} className="widget-image-viewer__stage" role="presentation" onClick={onClose}>
        {beforeImageUrl ? (
          <div
            className={`widget-image-viewer__comparison${isZoomed ? " widget-image-viewer__comparison--zoomed" : ""}`}
            style={{
              width: fittedImageSize ? `${fittedImageSize.width}px` : undefined,
              height: fittedImageSize ? `${fittedImageSize.height}px` : undefined,
              transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            }}
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={handleImageDoubleClick}
            onPointerDown={handleImagePointerDown}
            onPointerMove={handleImagePointerMove}
            onPointerUp={finishImageDrag}
            onPointerCancel={finishImageDrag}
          >
            <ImageComparisonSlider
              beforeSrc={beforeImageUrl}
              afterSrc={imageUrl}
              alt={`${alt} full-screen preview`}
              onAfterImageLoad={handleImageLoad}
            />
          </div>
        ) : (
          <img
            src={imageUrl}
            alt={`${alt} full-screen preview`}
            className={`widget-image-viewer__image${isZoomed ? " widget-image-viewer__image--zoomed" : ""}`}
            draggable={false}
            style={{
              width: fittedImageSize ? `${fittedImageSize.width}px` : undefined,
              height: fittedImageSize ? `${fittedImageSize.height}px` : undefined,
              transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            }}
            onLoad={handleImageLoad}
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={handleImageDoubleClick}
            onPointerDown={handleImagePointerDown}
            onPointerMove={handleImagePointerMove}
            onPointerUp={finishImageDrag}
            onPointerCancel={finishImageDrag}
          />
        )}
      </div>
    </div>,
    document.body,
  );
}

function audioOutputMetaLabel(audio: OutputAudioMedia): string {
  return audioMetadataLabel(null, audio.mimeType, audio.size, audio.durationSeconds, "Audio output");
}

function videoOutputMetaLabel(video: OutputVideoMedia): string {
  return videoMetadataLabel(null, video.mimeType, video.size, video.durationSeconds, video.width, video.height, video.fps, "Video output");
}

function clampImageScale(scale: number) {
  return Math.min(Math.max(scale, 1), 8);
}

const IMAGE_VIEWER_WHEEL_ZOOM_SENSITIVITY = 0.005;
const IMAGE_VIEWER_GESTURE_ZOOM_POWER = 1.75;

function viewerPointFromClient(stage: HTMLElement, clientX: number, clientY: number) {
  const rect = stage.getBoundingClientRect();
  return {
    x: clientX - (rect.left + rect.width / 2),
    y: clientY - (rect.top + rect.height / 2),
  };
}

// ─── Input widget ─────────────────────────────────────────────────────────────

function InputWidgetContent({
  control,
  inputIndex,
  inputValues,
  disabled,
  onChange,
  onImageUpload,
  onAudioUpload,
  onVideoUpload,
  loraBrowserFor,
}: {
  control: DashboardControlDef;
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  disabled: boolean;
  onChange: (inputId: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
}) {
  const inputId = control.input_id ?? control.id;
  const input = control.type === "api_credential"
    ? credentialInputForControl(control)
    : inputIndex.get(inputId);
  if (!input) return null;

  return (
    <DashboardInputControl
      control={control}
      input={input}
      value={inputValues[input.id]}
      disabled={disabled}
      variant="canvas"
      loraBrowser={loraBrowserFor?.(control, input)}
      onChange={(value) => onChange(input.id, value)}
      onImageUpload={(file) => onImageUpload(input.id, file)}
      onAudioUpload={(file, onProgress, signal) => onAudioUpload(input.id, file, onProgress, signal)}
      onVideoUpload={(file, onProgress, signal) => onVideoUpload(input.id, file, onProgress, signal)}
    />
  );
}

function credentialInputForControl(control: DashboardControlDef): WorkflowInputDef {
  return {
    id: control.input_id ?? control.id,
    label: control.label || "ComfyUI Account API Key",
    control: "api_credential",
    binding: { node_id: "", input_name: "" },
    default: {
      kind: "api_key_ref",
      provider: control.provider ?? "comfy_org",
      secret_ref: control.secret_ref ?? "api-key:comfy_org",
    },
    validation: {},
  };
}

async function downloadImage(imageUrl: string) {
  const response = await fetch(imageUrl);
  if (!response.ok) {
    throw new Error(`Image download failed: ${response.status}`);
  }

  const blobUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filenameFromImageUrl(imageUrl);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 0);
}

function downloadMediaDirect(mediaUrl: string, filename: string) {
  const link = document.createElement("a");
  const url = new URL(mediaUrl, window.location.href);
  url.searchParams.set("download", "true");
  link.href = url.toString();
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function filenameFromImageUrl(imageUrl: string) {
  try {
    const url = new URL(imageUrl, window.location.href);
    const pathParts = url.pathname.split("/").filter(Boolean);
    return url.searchParams.get("filename") || pathParts[pathParts.length - 1] || "noofy-output.png";
  } catch {
    return "noofy-output.png";
  }
}

function fromBackendLayout(item: DashboardControlDef | DashboardControlGroupDef, fallbackOverride?: GridItemLayout): GridItemLayout {
  const fallback = fallbackOverride ?? ("type" in item
    ? defaultLayoutForWidgetType(item.type)
    : defaultLayoutForWidgetGroup([]));
  const layout = item.layout;
  return withLayoutMinimums({
    x: layout?.x ?? 0,
    y: layout?.y ?? 0,
    w: layout?.w ?? 4,
    h: layout?.h ?? 2,
    minW: layout?.min_w ?? fallback.minW,
    minH: layout?.min_h ?? fallback.minH,
  }, item, fallback);
}

function withLayoutMinimums(
  layout: GridItemLayout,
  item: DashboardControlDef | DashboardControlGroupDef | DashboardTopLevelControlItem,
  fallbackOverride?: GridItemLayout,
): GridItemLayout {
  const source = "kind" in item ? (item.kind === "group" ? item.group : item.control) : item;
  const fallback = fallbackOverride ?? (
    "kind" in item && item.kind === "group"
      ? defaultLayoutForWidgetGroup(item.controls.map((control) => control.type))
      : "type" in source
        ? defaultLayoutForWidgetType(source.type)
        : defaultLayoutForWidgetGroup([])
  );
  const minW = layout.minW ?? source.layout?.min_w ?? fallback.minW;
  const minH = layout.minH ?? source.layout?.min_h ?? fallback.minH;
  const isGroup = "kind" in item ? item.kind === "group" : !("type" in item);
  return {
    ...layout,
    w: isGroup ? Math.max(layout.w, minW ?? 2) : layout.w,
    h: isGroup ? Math.max(layout.h, minH ?? 2) : layout.h,
    minW,
    minH,
  };
}

function clampActionBarPosition(
  position: CanvasActionBarPosition,
  frame: HTMLElement | null,
  actionBar: HTMLElement | null,
): CanvasActionBarPosition {
  const fallback = {
    x: Math.max(0, Math.round(position.x)),
    y: Math.max(0, Math.round(position.y)),
  };
  const frameRect = frame?.getBoundingClientRect();
  const actionBarRect = actionBar?.getBoundingClientRect();
  if (
    !frameRect ||
    !actionBarRect ||
    frameRect.width <= 0 ||
    frameRect.height <= 0 ||
    actionBarRect.width <= 0 ||
    actionBarRect.height <= 0
  ) {
    return fallback;
  }

  const maxX = Math.max(
    ACTION_BAR_BOUNDARY_PADDING,
    frameRect.width - actionBarRect.width - ACTION_BAR_BOUNDARY_PADDING,
  );
  const maxY = Math.max(
    ACTION_BAR_BOUNDARY_PADDING,
    frameRect.height - actionBarRect.height - ACTION_BAR_BOUNDARY_PADDING,
  );
  return {
    x: clampNumber(Math.round(position.x), ACTION_BAR_BOUNDARY_PADDING, maxX),
    y: clampNumber(Math.round(position.y), ACTION_BAR_BOUNDARY_PADDING, maxY),
  };
}

function sameActionBarPosition(
  a: CanvasActionBarPosition | null,
  b: CanvasActionBarPosition | null,
): boolean {
  return a?.x === b?.x && a?.y === b?.y;
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
