import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Download,
  GripVertical,
  ImagePlus,
  LayoutGrid,
  Save,
  Shuffle,
  SlidersHorizontal,
  Sparkles,
  ToggleLeft,
  Trash2,
  Type,
  UploadCloud,
  Wand2,
} from "lucide-react";

import { fetchRuntimeStatus, saveDashboard, type RuntimeStatus } from "../../lib/api/noofyApi";
import { findAvailableLayout, fitLayout, layoutsOverlap, type GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import {
  DashboardCanvasFrame,
  DashboardCanvasResizeHandles,
  DashboardCanvasSurface,
  DashboardCanvasWidgetShell,
  type DashboardResizeHandle,
  canvasRowsForItems,
  fitMovedLayoutPosition,
  layoutFromCanvasPointer,
  moveLayoutFromPointerDelta,
  nextAdjacentMoveLayout,
  resizeLayoutFromPointerDelta,
  sameGridLayout,
} from "../dashboard-canvas/DashboardCanvasPresentation";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import {
  MOCK_WORKFLOW,
  WIDGET_TYPE_LABELS,
  buildInitialDashboard,
  clearDashboardDraft,
  loadDashboardDraft,
  saveDashboardDraft,
  toBackendPayload,
  type DashboardSchema,
  type DashboardWidget,
  type DashboardWidgetLayout,
  type MockWorkflow,
  type WidgetType,
} from "./dashboardBuilderContent";

interface DashboardBuilderLayoutPageProps {
  workflowId?: string;
  workflowName?: string;
  initialSchema?: DashboardSchema;
  onBackToWidgets: (schema: DashboardSchema) => void;
  onSaveComplete: (workflowId: string) => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RuntimeState {
  loading: boolean;
  runtime: RuntimeStatus | null;
}

interface PointerEventLocation {
  clientX: number;
  clientY: number;
}

const WIDGET_ICONS: Record<WidgetType, typeof Type> = {
  slider: SlidersHorizontal,
  int_field: Type,
  string_field: Type,
  textarea: Type,
  toggle: ToggleLeft,
  load_image: ImagePlus,
  load_image_mask: UploadCloud,
  display_image: Sparkles,
  seed_widget: Shuffle,
  lora_loader: Sparkles,
  select: ChevronDown,
};

export function DashboardBuilderLayoutPage({
  workflowId,
  workflowName,
  initialSchema,
  onBackToWidgets,
  onSaveComplete,
  onNavigate,
}: DashboardBuilderLayoutPageProps) {
  const [runtimeState, setRuntimeState] = useState<RuntimeState>({ loading: true, runtime: null });

  useEffect(() => {
    let mounted = true;
    fetchRuntimeStatus()
      .then((runtime) => {
        if (mounted) setRuntimeState({ loading: false, runtime });
      })
      .catch(() => {
        if (mounted) setRuntimeState({ loading: false, runtime: null });
      });
    return () => {
      mounted = false;
    };
  }, []);

  const workflow: MockWorkflow = useMemo(() => {
    return {
      ...MOCK_WORKFLOW,
      id: workflowId ?? MOCK_WORKFLOW.id,
      name: workflowName ?? MOCK_WORKFLOW.name,
    };
  }, [workflowId, workflowName]);

  const [schema, setSchema] = useState<DashboardSchema>(
    () => initialSchema ?? loadDashboardDraft(workflow.id) ?? buildInitialDashboard(workflow),
  );
  const schemaRef = useRef(schema);
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null);
  const [activeDragWidgetId, setActiveDragWidgetId] = useState<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ widgetId: string; layout: DashboardWidgetLayout } | null>(null);
  const [movePreview, setMovePreview] = useState<{ widgetId: string; layout: DashboardWidgetLayout } | null>(null);
  const [savedFlash, setSavedFlash] = useState<"draft" | "saved" | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSavingDashboard, setIsSavingDashboard] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const moveFrameRef = useRef<number | null>(null);
  const resizeStateRef = useRef<{
    widgetId: string;
    handle: DashboardResizeHandle;
    startLayout: DashboardWidgetLayout;
    startClientX: number;
    startClientY: number;
  } | null>(null);
  const moveStateRef = useRef<{
    widgetId: string;
    startLayout: DashboardWidgetLayout;
    startClientX: number;
    startClientY: number;
    lastLayout: DashboardWidgetLayout;
    targetLayout: DashboardWidgetLayout;
  } | null>(null);

  useEffect(() => {
    setSchema(initialSchema ?? loadDashboardDraft(workflow.id) ?? buildInitialDashboard(workflow));
    setSelectedWidgetId(null);
  }, [workflow, initialSchema]);

  useLayoutEffect(() => {
    schemaRef.current = schema;
  }, [schema]);

  const appStatus = runtimeStatusCopy(runtimeState);
  const unplacedWidgets = schema.widgets.filter((widget) => !widget.layout);
  const placedWidgets = schema.widgets.filter((widget) => widget.layout);
  const allWidgetsPlaced = schema.widgets.length > 0 && unplacedWidgets.length === 0;
  const helperCopy = allWidgetsPlaced ? "Dashboard ready to save." : "Place all widgets on the canvas before saving.";
  const canvasRows = canvasRowsForItems(schema.widgets);

  function handleTrayPointerStart(event: PointerEvent<HTMLElement>, widgetId: string) {
    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    setActiveDragWidgetId(widgetId);
    setDragPreview(null);
    event.currentTarget.ownerDocument.getSelection()?.removeAllRanges();

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      pointerEvent.preventDefault();
      updateTrayDragPreview(widgetId, pointerEvent);
    }

    function finishTrayDrag(shouldPlace: boolean, pointerEvent: globalThis.PointerEvent) {
      const widget = schemaRef.current.widgets.find((candidate) => candidate.id === widgetId);
      const desiredLayout = widget && isPointerInsideCanvas(pointerEvent) ? layoutFromPointer(pointerEvent, widget) : null;
      if (shouldPlace && widget && desiredLayout) {
        placeWidget(widget.id, desiredLayout);
      }
      clearDragState();
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerCancel);
    }

    function handlePointerUp(pointerEvent: globalThis.PointerEvent) {
      finishTrayDrag(true, pointerEvent);
    }

    function handlePointerCancel(pointerEvent: globalThis.PointerEvent) {
      finishTrayDrag(false, pointerEvent);
    }

    window.addEventListener("pointermove", handlePointerMove, { passive: false });
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerCancel);
  }

  function updateTrayDragPreview(widgetId: string, event: PointerEventLocation) {
    const currentSchema = schemaRef.current;
    const widget = currentSchema.widgets.find((candidate) => candidate.id === widgetId);
    if (!widget || !isPointerInsideCanvas(event)) {
      setDragPreview(null);
      return;
    }

    const desiredLayout = layoutFromPointer(event, widget);
    if (!desiredLayout) return;
    const previewLayout = findAvailableLayout(
      widget.id,
      desiredLayout,
      currentSchema.widgets,
      currentSchema.layout.gridColumns,
    );

    setDragPreview((current) => {
      if (
        current?.widgetId === widget.id &&
        current.layout.x === previewLayout.x &&
        current.layout.y === previewLayout.y &&
        current.layout.w === previewLayout.w &&
        current.layout.h === previewLayout.h
      ) {
        return current;
      }
      return { widgetId: widget.id, layout: previewLayout };
    });
  }

  function isPointerInsideCanvas(event: PointerEventLocation): boolean {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return false;
    return event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
  }

  function clearDragState() {
    setActiveDragWidgetId(null);
    setDragPreview(null);
  }

  function placeWidget(widgetId: string, desiredLayout: DashboardWidgetLayout) {
    setSchema((current) => {
      const widget = current.widgets.find((candidate) => candidate.id === widgetId);
      if (!widget) return current;

      const fitted = fitLayout(desiredLayout, current.layout.gridColumns);
      const layout = findAvailableLayout(widgetId, fitted, current.widgets, current.layout.gridColumns);

      const nextSchema = {
        ...current,
        widgets: current.widgets.map((candidate) =>
          candidate.id === widgetId ? { ...candidate, layout } : candidate,
        ),
      };
      schemaRef.current = nextSchema;
      return nextSchema;
    });
    setSelectedWidgetId(widgetId);
  }

  function layoutFromPointer(event: PointerEventLocation, widget: DashboardWidget): DashboardWidgetLayout | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;

    const baseLayout = widget.layout ?? defaultLayoutForWidgetType(widget.widgetType);
    const currentSchema = schemaRef.current;
    const fitted = fitLayout(baseLayout, currentSchema.layout.gridColumns);
    return layoutFromCanvasPointer(event, fitted, canvas, {
      columns: currentSchema.layout.gridColumns,
      rowHeight: currentSchema.layout.rowHeight,
    });
  }

  function removeWidget(widgetId: string) {
    setSchema((current) => ({
      ...current,
      widgets: current.widgets.map((widget) => {
        if (widget.id !== widgetId) return widget;
        const { layout: _layout, ...withoutLayout } = widget;
        return withoutLayout;
      }),
    }));
    setSelectedWidgetId((current) => (current === widgetId ? null : current));
  }

  function handleMoveStart(
    event: PointerEvent<HTMLElement>,
    widgetId: string,
    layout: DashboardWidgetLayout,
  ) {
    if (shouldIgnoreWidgetMove(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    setSelectedWidgetId(widgetId);
    setActiveDragWidgetId(widgetId);
    moveStateRef.current = {
      widgetId,
      startLayout: layout,
      startClientX: event.clientX,
      startClientY: event.clientY,
      lastLayout: layout,
      targetLayout: layout,
    };
    setMovePreview({ widgetId, layout });

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      const moveState = moveStateRef.current;
      if (!moveState) return;
      const currentSchema = schemaRef.current;
      const candidate = fitMovedLayoutPosition(
        moveLayoutFromPointerDelta({
          startLayout: moveState.startLayout,
          startClientX: moveState.startClientX,
          startClientY: moveState.startClientY,
          clientX: pointerEvent.clientX,
          clientY: pointerEvent.clientY,
          canvas: canvasRef.current,
          columns: currentSchema.layout.gridColumns,
          rowHeight: currentSchema.layout.rowHeight,
        }),
        currentSchema.layout.gridColumns,
      );
      if (sameGridLayout(candidate, moveState.targetLayout)) return;
      moveState.targetLayout = candidate;
      applyMoveStep();
    }

    function applyMoveStep() {
      const moveState = moveStateRef.current;
      if (!moveState || sameGridLayout(moveState.lastLayout, moveState.targetLayout)) return;

      const currentSchema = schemaRef.current;
      const candidate = fitMovedLayoutPosition(
        nextAdjacentMoveLayout(moveState.lastLayout, moveState.targetLayout),
        currentSchema.layout.gridColumns,
      );
      const collides = currentSchema.widgets.some((widget) => {
        if (widget.id === moveState.widgetId || !widget.layout) return false;
        return layoutsOverlap(candidate, widget.layout);
      });
      if (collides) {
        moveState.targetLayout = moveState.lastLayout;
        return;
      }
      moveState.lastLayout = candidate;
      setMovePreview({ widgetId: moveState.widgetId, layout: candidate });
      if (!sameGridLayout(candidate, moveState.targetLayout)) scheduleMoveStep();
    }

    function scheduleMoveStep() {
      if (moveFrameRef.current !== null) return;
      moveFrameRef.current = window.requestAnimationFrame(() => {
        moveFrameRef.current = null;
        applyMoveStep();
      });
    }

    function commitMove(finalLayout: DashboardWidgetLayout | undefined) {
      if (!finalLayout) return;
      setSchema((current) => {
        const nextSchema = {
          ...current,
          widgets: current.widgets.map((widget) =>
            widget.id === widgetId ? { ...widget, layout: finalLayout } : widget,
          ),
        };
        schemaRef.current = nextSchema;
        return nextSchema;
      });
    }

    function finishMove(shouldCommit: boolean) {
      const finalLayout = moveStateRef.current?.lastLayout;
      if (moveFrameRef.current !== null) {
        window.cancelAnimationFrame(moveFrameRef.current);
        moveFrameRef.current = null;
      }
      if (shouldCommit) commitMove(finalLayout);
      moveStateRef.current = null;
      setActiveDragWidgetId(null);
      setMovePreview(null);
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
    widgetId: string,
    layout: DashboardWidgetLayout,
    handle: DashboardResizeHandle,
  ) {
    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    resizeStateRef.current = {
      widgetId,
      handle,
      startLayout: layout,
      startClientX: event.clientX,
      startClientY: event.clientY,
    };

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      const resizeState = resizeStateRef.current;
      if (!resizeState) return;
      setSchema((current) => {
        const candidate = fitLayout(
          resizeLayoutFromPointerDelta({
            startLayout: resizeState.startLayout,
            startClientX: resizeState.startClientX,
            startClientY: resizeState.startClientY,
            clientX: pointerEvent.clientX,
            clientY: pointerEvent.clientY,
            canvas: canvasRef.current,
            handle: resizeState.handle,
            columns: current.layout.gridColumns,
            rowHeight: current.layout.rowHeight,
          }),
          current.layout.gridColumns,
        );
        const collides = current.widgets.some((widget) => {
          if (widget.id === resizeState.widgetId || !widget.layout) return false;
          return layoutsOverlap(candidate, widget.layout);
        });
        if (collides) return current;
        return {
          ...current,
          widgets: current.widgets.map((widget) =>
            widget.id === resizeState.widgetId ? { ...widget, layout: candidate } : widget,
          ),
        };
      });
    }

    function handlePointerUp() {
      resizeStateRef.current = null;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  function handleSaveDraft() {
    saveDashboardDraft(schema);
    setSaveError(null);
    setSavedFlash("draft");
    window.setTimeout(() => setSavedFlash(null), 2400);
  }

  function handleSaveDashboard() {
    if (!allWidgetsPlaced || isSavingDashboard) return;
    const targetId = workflowId ?? schema.workflowId;
    const payload = toBackendPayload(schema);
    setIsSavingDashboard(true);
    setSaveError(null);
    saveDashboard(targetId, payload)
      .then(() => {
        clearDashboardDraft(targetId);
        setSavedFlash("saved");
        window.setTimeout(() => onSaveComplete(targetId), 300);
      })
      .catch((error) => {
        saveDashboardDraft({ ...schema, workflowId: targetId });
        setSavedFlash(null);
        setSaveError(
          error instanceof Error
            ? error.message
            : "Dashboard could not be saved. A local draft was kept.",
        );
      })
      .finally(() => {
        setIsSavingDashboard(false);
      });
  }

  return (
    <AppLayout
      activeRoute="workflows"
      status={appStatus}
      onNavigate={onNavigate}
      mainClassName="main-workspace--builder-layout"
      contentClassName="workspace-content--builder-layout"
    >
      <div className="builder-layout-page">
        <header className="builder-layout-topbar" aria-labelledby="builder-layout-title">
          <div className="builder-layout-topbar__left">
            <button className="ghost-button ghost-button--back" type="button" onClick={() => onBackToWidgets(schema)}>
              <ArrowLeft size={15} aria-hidden="true" />
              Back to widgets
            </button>
            <div>
              <p className="eyebrow">Dashboard Builder — Layout</p>
              <h1 id="builder-layout-title">Dashboard Builder</h1>
            </div>
          </div>

          <div className="builder-layout-topbar__actions">
            {savedFlash ? (
              <div className="status-pill status-pill--success" role="status">
                <span />
                <span>{savedFlash === "saved" ? "Dashboard saved" : "Draft saved"}</span>
              </div>
            ) : saveError ? (
              <div className="status-pill status-pill--error" role="status" title={saveError}>
                <span />
                <span>Save failed. Draft kept.</span>
              </div>
            ) : (
              <p className={`builder-layout-save-helper ${allWidgetsPlaced ? "builder-layout-save-helper--ready" : ""}`}>
                {helperCopy}
              </p>
            )}
            <button className="secondary-button" type="button" onClick={handleSaveDraft}>
              <Save size={15} aria-hidden="true" />
              Save as draft
            </button>
            <button
              className="primary-button primary-button--compact"
              type="button"
              disabled={!allWidgetsPlaced || isSavingDashboard}
              onClick={handleSaveDashboard}
            >
              <CheckCircle2 size={16} aria-hidden="true" />
              {isSavingDashboard ? "Saving..." : "Save Dashboard"}
            </button>
          </div>
        </header>

        <div className="builder-layout-workspace">
          <aside className="layout-widget-tray" aria-label="Widgets to place">
            <header className="layout-widget-tray__header">
              <div>
                <h2>Widgets to place</h2>
                <p>Drag to canvas</p>
              </div>
              <span>{unplacedWidgets.length}</span>
            </header>

            <div className="layout-widget-tray__list">
              {unplacedWidgets.length === 0 ? (
                <div className="layout-widget-tray__empty">
                  <CheckCircle2 size={24} aria-hidden="true" />
                  <h3>All widgets placed</h3>
                  <p>Remove a Widget from the canvas to make it available again.</p>
                </div>
              ) : (
                unplacedWidgets.map((widget) => (
                  <TrayWidgetItem
                    key={widget.id}
                    widget={widget}
                    onPointerStart={handleTrayPointerStart}
                  />
                ))
              )}
            </div>
          </aside>

          <DashboardCanvasFrame aria-label="Dashboard layout canvas">
            <DashboardCanvasSurface
              ref={canvasRef}
              empty={placedWidgets.length === 0}
              rows={canvasRows}
              columns={schema.layout.gridColumns}
              rowHeight={schema.layout.rowHeight}
              gridGap={schema.layout.gridGap}
            >
              {placedWidgets.length === 0 ? (
                <div className="layout-canvas__empty">
                  <div className="layout-canvas__empty-icon">
                    <Wand2 size={30} aria-hidden="true" />
                  </div>
                  <h2>Start building your dashboard</h2>
                  <p>Drag widgets here to arrange the interface people will see.</p>
                </div>
              ) : null}

              {placedWidgets.map((widget) => {
                if (!widget.layout) return null;
                const previewLayout = movePreview?.widgetId === widget.id ? movePreview.layout : null;
                const displayLayout = previewLayout ?? widget.layout;

                return (
                  <PlacedDashboardWidget
                    key={widget.id}
                    widget={widget}
                    layout={displayLayout}
                    columns={schema.layout.gridColumns}
                    gridGap={schema.layout.gridGap}
                    rowHeight={schema.layout.rowHeight}
                    selected={selectedWidgetId === widget.id}
                    dragging={activeDragWidgetId === widget.id}
                    onSelect={() => setSelectedWidgetId(widget.id)}
                    onRemove={() => removeWidget(widget.id)}
                    onMoveStart={(event) => handleMoveStart(event, widget.id, displayLayout)}
                    onResizeStart={(event, handle) => handleResizeStart(event, widget.id, displayLayout, handle)}
                  />
                );
              })}

              {dragPreview ? (
                <DragPreviewWidget
                  widget={schema.widgets.find((widget) => widget.id === dragPreview.widgetId) ?? null}
                  layout={dragPreview.layout}
                  columns={schema.layout.gridColumns}
                  gridGap={schema.layout.gridGap}
                  rowHeight={schema.layout.rowHeight}
                />
              ) : null}
            </DashboardCanvasSurface>
          </DashboardCanvasFrame>
        </div>
      </div>
    </AppLayout>
  );
}

function TrayWidgetItem({
  widget,
  onPointerStart,
}: {
  widget: DashboardWidget;
  onPointerStart: (event: PointerEvent<HTMLElement>, widgetId: string) => void;
}) {
  const Icon = WIDGET_ICONS[widget.widgetType];

  return (
    <article
      className="layout-tray-widget"
      onPointerDown={(event) => onPointerStart(event, widget.id)}
    >
      <div className="layout-tray-widget__icon" aria-hidden="true">
        <Icon size={17} />
      </div>
      <div className="layout-tray-widget__body">
        <h3>{widget.title}</h3>
        <span>{WIDGET_TYPE_LABELS[widget.widgetType]}</span>
        {widget.description ? <p>{widget.description}</p> : null}
      </div>
      <GripVertical size={16} aria-hidden="true" />
    </article>
  );
}

function PlacedDashboardWidget({
  widget,
  layout,
  columns,
  gridGap,
  rowHeight,
  selected,
  onSelect,
  onRemove,
  onMoveStart,
  onResizeStart,
  dragging = false,
  preview = false,
}: {
  widget: DashboardWidget;
  layout: DashboardWidgetLayout;
  columns: number;
  gridGap: number;
  rowHeight: number;
  selected: boolean;
  dragging?: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onMoveStart: (event: PointerEvent<HTMLElement>) => void;
  onResizeStart: (event: PointerEvent<HTMLButtonElement>, handle: DashboardResizeHandle) => void;
  preview?: boolean;
}) {
  const Icon = WIDGET_ICONS[widget.widgetType];

  return (
    <DashboardCanvasWidgetShell
      layout={layout}
      columns={columns}
      gridGap={gridGap}
      rowHeight={rowHeight}
      selected={selected}
      preview={preview}
      className={dragging ? "layout-canvas-widget--moving" : ""}
      onClick={onSelect}
      onPointerDown={!preview ? onMoveStart : undefined}
    >
      <header className="layout-canvas-widget__header">
        <div className="layout-canvas-widget__title">
          <span aria-hidden="true">
            <Icon size={16} />
          </span>
          <div>
            <h3>{widget.title}</h3>
            <p>{WIDGET_TYPE_LABELS[widget.widgetType]}</p>
          </div>
        </div>
        {!preview ? (
          <div className="layout-canvas-widget__actions" onClick={(event) => event.stopPropagation()}>
            <button className="icon-button icon-button--card" type="button" aria-label={`Remove ${widget.title}`} title="Remove from canvas" onClick={onRemove}>
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </header>

      <div className="layout-canvas-widget__preview-surface">
        <WidgetSurfacePreview widget={widget} />
      </div>
      {!preview ? <DashboardCanvasResizeHandles label={widget.title} onResizeStart={(handle, event) => onResizeStart(event, handle)} /> : null}
    </DashboardCanvasWidgetShell>
  );
}

function DragPreviewWidget({
  widget,
  layout,
  columns,
  gridGap,
  rowHeight,
}: {
  widget: DashboardWidget | null;
  layout: DashboardWidgetLayout;
  columns: number;
  gridGap: number;
  rowHeight: number;
}) {
  if (!widget) return null;

  return (
    <PlacedDashboardWidget
      widget={widget}
      layout={layout}
      columns={columns}
      gridGap={gridGap}
      rowHeight={rowHeight}
      selected
      dragging={false}
      onSelect={() => undefined}
      onRemove={() => undefined}
      onMoveStart={() => undefined}
      onResizeStart={() => undefined}
      preview
    />
  );
}

function shouldIgnoreWidgetMove(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (
    target instanceof HTMLInputElement &&
    target.readOnly &&
    target.classList.contains("layout-preview-input")
  ) {
    return false;
  }
  if (
    target instanceof HTMLTextAreaElement &&
    target.readOnly &&
    target.classList.contains("layout-preview-input")
  ) {
    return false;
  }
  return Boolean(
    target.closest(
      "button, input, textarea, select, a, [role='button'], .layout-canvas-resize-handle, .layout-canvas-resize-handles",
    ),
  );
}

function capturePointer(target: Element, pointerId: number) {
  try {
    target.setPointerCapture?.(pointerId);
  } catch {
    // The window-level listeners below still keep dragging active when a browser
    // refuses capture during an edge transition from native drag/drop.
  }
}

function WidgetSurfacePreview({ widget }: { widget: DashboardWidget }) {
  if (widget.widgetType === "textarea") {
    return (
      <div
        className="layout-preview-input layout-preview-input--textarea"
        role="textbox"
        aria-readonly="true"
      >
        {String(widget.defaultValue ?? "")}
      </div>
    );
  }

  if (widget.widgetType === "string_field") {
    return (
      <div className="layout-preview-input" role="textbox" aria-readonly="true">
        {String(widget.defaultValue ?? "")}
      </div>
    );
  }

  if (widget.widgetType === "slider") {
    const min = widget.min ?? 0;
    const max = widget.max ?? 100;
    const numeric = Number(widget.defaultValue ?? min);
    const percent = max > min ? Math.max(0, Math.min(100, ((numeric - min) / (max - min)) * 100)) : 0;
    return (
      <div className="layout-preview-slider">
        <div className="layout-preview-slider__track">
          <span style={{ width: `${percent}%` }} />
        </div>
        <div className="layout-preview-slider__values">
          <span>{min}</span>
          <strong>{numeric}</strong>
          <span>{max}</span>
        </div>
      </div>
    );
  }

  if (widget.widgetType === "int_field" || widget.widgetType === "seed_widget") {
    return (
      <div className="layout-preview-seed">
        <div className="layout-preview-input" role="textbox" aria-readonly="true">
          {String(widget.defaultValue ?? 0)}
        </div>
        {widget.widgetType === "seed_widget" ? <span className="layout-preview-button">Random</span> : null}
      </div>
    );
  }

  if (widget.widgetType === "toggle") {
    const isOn = Boolean(widget.defaultValue);
    return (
      <div className={`layout-preview-toggle ${isOn ? "layout-preview-toggle--on" : ""}`}>
        <span aria-hidden="true" />
        <strong>{isOn ? "On" : "Off"}</strong>
      </div>
    );
  }

  if (widget.widgetType === "select" || widget.widgetType === "lora_loader") {
    const options = widget.options ?? [];
    return (
      <div className="layout-preview-select">
        <span>{String(widget.defaultValue ?? options[0] ?? "Choose an option")}</span>
        <ChevronDown size={15} aria-hidden="true" />
      </div>
    );
  }

  if (widget.widgetType === "load_image" || widget.widgetType === "load_image_mask") {
    return (
      <div className="layout-preview-image-input">
        <ImagePlus size={22} aria-hidden="true" />
        <span>{widget.widgetType === "load_image_mask" ? "Upload an image, then draw a mask" : "Drop an image or click to upload"}</span>
      </div>
    );
  }

  if (widget.widgetType === "display_image") {
    return (
      <div className="layout-preview-output">
        <Sparkles size={24} aria-hidden="true" />
        <span>Generated image will appear here</span>
        {widget.showDownload ? (
          <span className="layout-preview-button">
            <Download size={13} aria-hidden="true" />
            Download
          </span>
        ) : null}
      </div>
    );
  }

  return null;
}
