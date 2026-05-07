import { useEffect, useMemo, useRef, useState, type DragEvent, type PointerEvent } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Download,
  GripVertical,
  ImagePlus,
  LayoutGrid,
  Move,
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
  layoutFromCanvasPointer,
  resizeLayoutFromPointerDelta,
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

const DRAG_MIME_TYPE = "application/noofy-dashboard-widget";
const DRAG_TEXT_PREFIX = "noofy-widget:";

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
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null);
  const [activeDragWidgetId, setActiveDragWidgetId] = useState<string | null>(null);
  const activeDragWidgetIdRef = useRef<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ widgetId: string; layout: DashboardWidgetLayout } | null>(null);
  const [savedFlash, setSavedFlash] = useState<"draft" | "saved" | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSavingDashboard, setIsSavingDashboard] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const resizeStateRef = useRef<{
    widgetId: string;
    handle: DashboardResizeHandle;
    startLayout: DashboardWidgetLayout;
    startClientX: number;
    startClientY: number;
  } | null>(null);

  useEffect(() => {
    setSchema(initialSchema ?? loadDashboardDraft(workflow.id) ?? buildInitialDashboard(workflow));
    setSelectedWidgetId(null);
  }, [workflow, initialSchema]);

  const appStatus = runtimeStatusCopy(runtimeState);
  const unplacedWidgets = schema.widgets.filter((widget) => !widget.layout);
  const placedWidgets = schema.widgets.filter((widget) => widget.layout);
  const allWidgetsPlaced = schema.widgets.length > 0 && unplacedWidgets.length === 0;
  const helperCopy = allWidgetsPlaced ? "Dashboard ready to save." : "Place all widgets on the canvas before saving.";
  const canvasRows = canvasRowsForItems(schema.widgets);

  function handleDragStart(event: DragEvent, widgetId: string) {
    activeDragWidgetIdRef.current = widgetId;
    setActiveDragWidgetId(widgetId);
    setDragPreview(null);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(DRAG_MIME_TYPE, JSON.stringify({ widgetId }));
    event.dataTransfer.setData("text/plain", `${DRAG_TEXT_PREFIX}${widgetId}`);
  }

  function handleCanvasDragOver(event: DragEvent) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const widgetId = activeDragWidgetIdRef.current ?? readDragPayload(event)?.widgetId;
    if (!widgetId) return;

    const widget = schema.widgets.find((candidate) => candidate.id === widgetId);
    if (!widget) return;

    const desiredLayout = layoutFromPointer(event, widget);
    if (!desiredLayout) return;

    const previewLayout = findAvailableLayout(
      widget.id,
      desiredLayout,
      schema.widgets,
      schema.layout.gridColumns,
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

  function handleCanvasDrop(event: DragEvent) {
    event.preventDefault();
    const payload = readDragPayload(event);
    const widgetId = payload?.widgetId ?? activeDragWidgetIdRef.current;
    if (!widgetId) {
      clearDragState();
      return;
    }

    const widget = schema.widgets.find((candidate) => candidate.id === widgetId);
    if (!widget) {
      clearDragState();
      return;
    }

    const desiredLayout = layoutFromPointer(event, widget);
    if (!desiredLayout) {
      clearDragState();
      return;
    }

    placeWidget(widget.id, desiredLayout);
    clearDragState();
  }

  function handleCanvasDragLeave(event: DragEvent) {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    setDragPreview(null);
  }

  function handleDragEnd() {
    clearDragState();
  }

  function clearDragState() {
    activeDragWidgetIdRef.current = null;
    setActiveDragWidgetId(null);
    setDragPreview(null);
  }

  function placeWidget(widgetId: string, desiredLayout: DashboardWidgetLayout) {
    setSchema((current) => {
      const widget = current.widgets.find((candidate) => candidate.id === widgetId);
      if (!widget) return current;

      const fitted = fitLayout(desiredLayout, current.layout.gridColumns);
      const layout = findAvailableLayout(widgetId, fitted, current.widgets, current.layout.gridColumns);

      return {
        ...current,
        widgets: current.widgets.map((candidate) =>
          candidate.id === widgetId ? { ...candidate, layout } : candidate,
        ),
      };
    });
    setSelectedWidgetId(widgetId);
  }

  function layoutFromPointer(event: DragEvent, widget: DashboardWidget): DashboardWidgetLayout | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;

    const baseLayout = widget.layout ?? defaultLayoutForWidgetType(widget.widgetType);
    const fitted = fitLayout(baseLayout, schema.layout.gridColumns);
    return layoutFromCanvasPointer(event, fitted, canvas, {
      columns: schema.layout.gridColumns,
      rowHeight: schema.layout.rowHeight,
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

  function handleResizeStart(
    event: PointerEvent<HTMLButtonElement>,
    widgetId: string,
    layout: DashboardWidgetLayout,
    handle: DashboardResizeHandle,
  ) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
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
    <AppLayout activeRoute="workflows" status={appStatus} onNavigate={onNavigate}>
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
                    onDragStart={handleDragStart}
                    onDragEnd={handleDragEnd}
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
              rowHeight={schema.layout.rowHeight}
              gridGap={schema.layout.gridGap}
              onDragOver={handleCanvasDragOver}
              onDragLeave={handleCanvasDragLeave}
              onDrop={handleCanvasDrop}
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

              {placedWidgets.map((widget) =>
                widget.layout ? (
                  <PlacedDashboardWidget
                    key={widget.id}
                    widget={widget}
                    layout={widget.layout}
                    columns={schema.layout.gridColumns}
                    gridGap={schema.layout.gridGap}
                    rowHeight={schema.layout.rowHeight}
                    selected={selectedWidgetId === widget.id}
                    onSelect={() => setSelectedWidgetId(widget.id)}
                    onRemove={() => removeWidget(widget.id)}
                    onDragStart={handleDragStart}
                    onDragEnd={handleDragEnd}
                    onResizeStart={(event, handle) => handleResizeStart(event, widget.id, widget.layout!, handle)}
                  />
                ) : null,
              )}

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
  onDragStart,
  onDragEnd,
}: {
  widget: DashboardWidget;
  onDragStart: (event: DragEvent, widgetId: string) => void;
  onDragEnd: () => void;
}) {
  const Icon = WIDGET_ICONS[widget.widgetType];

  return (
    <article
      className="layout-tray-widget"
      draggable
      onDragStart={(event) => onDragStart(event, widget.id)}
      onDragEnd={onDragEnd}
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
  onDragStart,
  onDragEnd,
  onResizeStart,
  preview = false,
}: {
  widget: DashboardWidget;
  layout: DashboardWidgetLayout;
  columns: number;
  gridGap: number;
  rowHeight: number;
  selected: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onDragStart: (event: DragEvent, widgetId: string) => void;
  onDragEnd: () => void;
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
      draggable={!preview}
      onClick={onSelect}
      onDragStart={preview ? undefined : (event) => onDragStart(event, widget.id)}
      onDragEnd={preview ? undefined : onDragEnd}
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
            <button className="icon-button icon-button--card" type="button" aria-label={`Move ${widget.title}`} title="Drag to move">
              <Move size={14} aria-hidden="true" />
            </button>
            <button className="icon-button icon-button--card" type="button" aria-label={`Remove ${widget.title}`} title="Remove from canvas" onClick={onRemove}>
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </header>

      <WidgetSurfacePreview widget={widget} />
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
      onSelect={() => undefined}
      onRemove={() => undefined}
      onDragStart={() => undefined}
      onDragEnd={() => undefined}
      onResizeStart={() => undefined}
      preview
    />
  );
}

function WidgetSurfacePreview({ widget }: { widget: DashboardWidget }) {
  if (widget.widgetType === "textarea") {
    return (
      <textarea
        className="layout-preview-input layout-preview-input--textarea"
        readOnly
        value={String(widget.defaultValue ?? "")}
      />
    );
  }

  if (widget.widgetType === "string_field") {
    return <input className="layout-preview-input" readOnly type="text" value={String(widget.defaultValue ?? "")} />;
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
        <input className="layout-preview-input" readOnly type="text" value={String(widget.defaultValue ?? 0)} />
        {widget.widgetType === "seed_widget" ? <button type="button">Random</button> : null}
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
          <button type="button">
            <Download size={13} aria-hidden="true" />
            Download
          </button>
        ) : null}
      </div>
    );
  }

  return null;
}

function readDragPayload(event: DragEvent): { widgetId: string } | null {
  const raw = event.dataTransfer.getData(DRAG_MIME_TYPE);
  const fallback = event.dataTransfer.getData("text/plain");

  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { widgetId?: unknown };
      if (typeof parsed.widgetId === "string") return { widgetId: parsed.widgetId };
    } catch {
      return null;
    }
  }

  if (fallback.startsWith(DRAG_TEXT_PREFIX)) {
    return { widgetId: fallback.slice(DRAG_TEXT_PREFIX.length) };
  }

  return null;
}
