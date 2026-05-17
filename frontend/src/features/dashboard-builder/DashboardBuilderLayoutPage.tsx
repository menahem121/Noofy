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
import { findAvailableLayout, findNearestAvailableLayout, fitLayout, layoutsOverlap, type GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetGroup, defaultLayoutForWidgetType } from "../../lib/widgetSizes";
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
  normalizeDashboardSchema,
  saveDashboardDraft,
  topLevelDashboardItems,
  toBackendPayload,
  type DashboardSchema,
  type DashboardTopLevelItem,
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
  const activeWorkflowId = workflowId ?? MOCK_WORKFLOW.id;
  const activeWorkflowName = workflowName ?? (workflowId ? workflowId : MOCK_WORKFLOW.name);
  const scopedInitialSchema = initialSchema?.workflowId === activeWorkflowId ? initialSchema : undefined;
  const saveSequenceRef = useRef(0);
  const activeWorkflowIdRef = useRef(activeWorkflowId);
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
      id: activeWorkflowId,
      name: activeWorkflowName,
      source: workflowId ? "imported_noofy_package" : MOCK_WORKFLOW.source,
      nodes: workflowId ? [] : MOCK_WORKFLOW.nodes,
    };
  }, [activeWorkflowId, activeWorkflowName, workflowId]);

  const [schema, setSchema] = useState<DashboardSchema>(
    () => normalizeDashboardSchema(scopedInitialSchema ?? loadDashboardDraft(activeWorkflowId) ?? buildInitialDashboard(workflow)),
  );
  const schemaRef = useRef(schema);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [activeDragItemId, setActiveDragItemId] = useState<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ itemId: string; layout: DashboardWidgetLayout } | null>(null);
  const [movePreview, setMovePreview] = useState<{ itemId: string; layout: DashboardWidgetLayout } | null>(null);
  const [dropPreview, setDropPreview] = useState<{ itemId: string; layout: DashboardWidgetLayout } | null>(null);
  const [savedFlash, setSavedFlash] = useState<"draft" | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSavingDashboard, setIsSavingDashboard] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const resizeStateRef = useRef<{
    itemId: string;
    handle: DashboardResizeHandle;
    startLayout: DashboardWidgetLayout;
    startClientX: number;
    startClientY: number;
  } | null>(null);
  const moveStateRef = useRef<{
    itemId: string;
    startLayout: DashboardWidgetLayout;
    startClientX: number;
    startClientY: number;
    currentLayout: DashboardWidgetLayout;
    dropLayout: DashboardWidgetLayout;
  } | null>(null);

  useLayoutEffect(() => {
    activeWorkflowIdRef.current = activeWorkflowId;
    saveSequenceRef.current += 1;
    const nextSchema = normalizeDashboardSchema(scopedInitialSchema ?? loadDashboardDraft(activeWorkflowId) ?? buildInitialDashboard(workflow));
    setSchema(nextSchema);
    setSelectedItemId(null);
    setActiveDragItemId(null);
    setDragPreview(null);
    setMovePreview(null);
    setDropPreview(null);
    setSavedFlash(null);
    setSaveError(null);
    setIsSavingDashboard(false);
    resizeStateRef.current = null;
    moveStateRef.current = null;
    schemaRef.current = nextSchema;
  }, [activeWorkflowId]);

  useLayoutEffect(() => {
    schemaRef.current = schema;
  }, [schema]);

  const appStatus = runtimeStatusCopy(runtimeState);
  const schemaReady = schema.workflowId === activeWorkflowId;
  const topLevelItems = schemaReady ? topLevelDashboardItems(schema) : [];
  const unplacedItems = topLevelItems.filter((item) => !dashboardItemLayout(item));
  const placedItems = topLevelItems.filter((item) => dashboardItemLayout(item));
  const allWidgetsPlaced = schemaReady && topLevelItems.length > 0 && unplacedItems.length === 0;
  const helperCopy = allWidgetsPlaced ? "Dashboard ready to save." : "Place all widgets on the canvas before saving.";
  const canvasRows = schemaReady ? canvasRowsForItems(topLevelItems.map((item) => ({ layout: dashboardItemLayout(item) }))) : 0;

  function handleTrayPointerStart(event: PointerEvent<HTMLElement>, itemId: string) {
    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    setActiveDragItemId(itemId);
    setDragPreview(null);
    event.currentTarget.ownerDocument.getSelection()?.removeAllRanges();

    function handlePointerMove(pointerEvent: globalThis.PointerEvent) {
      pointerEvent.preventDefault();
      updateTrayDragPreview(itemId, pointerEvent);
    }

    function finishTrayDrag(shouldPlace: boolean, pointerEvent: globalThis.PointerEvent) {
      const item = topLevelDashboardItems(schemaRef.current).find((candidate) => candidate.id === itemId);
      const desiredLayout = item && isPointerInsideCanvas(pointerEvent) ? layoutFromPointer(pointerEvent, item) : null;
      if (shouldPlace && item && desiredLayout) {
        placeItem(item.id, desiredLayout);
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

  function updateTrayDragPreview(itemId: string, event: PointerEventLocation) {
    const currentSchema = schemaRef.current;
    const item = topLevelDashboardItems(currentSchema).find((candidate) => candidate.id === itemId);
    if (!item || !isPointerInsideCanvas(event)) {
      setDragPreview(null);
      return;
    }

    const desiredLayout = layoutFromPointer(event, item);
    if (!desiredLayout) return;
    const previewLayout = findAvailableLayout(
      item.id,
      desiredLayout,
      topLevelDashboardItems(currentSchema).map((candidate) => ({ id: candidate.id, layout: dashboardItemLayout(candidate) })),
      currentSchema.layout.gridColumns,
    );

    setDragPreview((current) => {
      if (
        current?.itemId === item.id &&
        current.layout.x === previewLayout.x &&
        current.layout.y === previewLayout.y &&
        current.layout.w === previewLayout.w &&
        current.layout.h === previewLayout.h
      ) {
        return current;
      }
      return { itemId: item.id, layout: previewLayout };
    });
  }

  function isPointerInsideCanvas(event: PointerEventLocation): boolean {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return false;
    return event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
  }

  function clearDragState() {
    setActiveDragItemId(null);
    setDragPreview(null);
  }

  function placeItem(itemId: string, desiredLayout: DashboardWidgetLayout) {
    setSchema((current) => {
      const item = topLevelDashboardItems(current).find((candidate) => candidate.id === itemId);
      if (!item) return current;

      const fitted = fitLayout(desiredLayout, current.layout.gridColumns);
      const layout = findAvailableLayout(
        itemId,
        fitted,
        topLevelDashboardItems(current).map((candidate) => ({ id: candidate.id, layout: dashboardItemLayout(candidate) })),
        current.layout.gridColumns,
      );

      const nextSchema = setDashboardItemLayout(current, itemId, layout);
      schemaRef.current = nextSchema;
      return nextSchema;
    });
    setSelectedItemId(itemId);
  }

  function layoutFromPointer(event: PointerEventLocation, item: DashboardTopLevelItem): DashboardWidgetLayout | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;

    const baseLayout = dashboardItemLayout(item) ?? defaultLayoutForTopLevelItem(item);
    const currentSchema = schemaRef.current;
    const fitted = fitLayout(baseLayout, currentSchema.layout.gridColumns);
    return layoutFromCanvasPointer(event, fitted, canvas, {
      columns: currentSchema.layout.gridColumns,
      rowHeight: currentSchema.layout.rowHeight,
    });
  }

  function removeItem(itemId: string) {
    setSchema((current) => removeDashboardItemLayout(current, itemId));
    setSelectedItemId((current) => (current === itemId ? null : current));
  }

  function handleMoveStart(
    event: PointerEvent<HTMLElement>,
    itemId: string,
    layout: DashboardWidgetLayout,
  ) {
    if (shouldIgnoreWidgetMove(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    setSelectedItemId(itemId);
    setActiveDragItemId(itemId);
    moveStateRef.current = {
      itemId,
      startLayout: layout,
      startClientX: event.clientX,
      startClientY: event.clientY,
      currentLayout: layout,
      dropLayout: layout,
    };
    setMovePreview({ itemId, layout });
    setDropPreview({ itemId, layout });

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
      const dropLayout = findNearestAvailableLayout(
        moveState.itemId,
        candidate,
        topLevelDashboardItems(currentSchema).map((item) => ({ id: item.id, layout: dashboardItemLayout(item) })),
        currentSchema.layout.gridColumns,
      );
      if (sameGridLayout(candidate, moveState.currentLayout) && sameGridLayout(dropLayout, moveState.dropLayout)) return;
      moveState.currentLayout = candidate;
      moveState.dropLayout = dropLayout;
      setMovePreview({ itemId: moveState.itemId, layout: candidate });
      setDropPreview({ itemId: moveState.itemId, layout: dropLayout });
    }

    function commitMove(finalLayout: DashboardWidgetLayout | undefined) {
      if (!finalLayout) return;
      setSchema((current) => {
        const nextSchema = setDashboardItemLayout(current, itemId, finalLayout);
        schemaRef.current = nextSchema;
        return nextSchema;
      });
    }

    function finishMove(shouldCommit: boolean) {
      const finalLayout = moveStateRef.current?.dropLayout;
      if (shouldCommit) commitMove(finalLayout);
      moveStateRef.current = null;
      setActiveDragItemId(null);
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
    itemId: string,
    layout: DashboardWidgetLayout,
    handle: DashboardResizeHandle,
  ) {
    event.preventDefault();
    event.stopPropagation();
    capturePointer(event.currentTarget, event.pointerId);
    resizeStateRef.current = {
      itemId,
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
        const topLevelCollides = topLevelDashboardItems(current).some((item) => {
          const layout = dashboardItemLayout(item);
          if (item.id === resizeState.itemId || !layout) return false;
          return layoutsOverlap(candidate, layout);
        });
        if (topLevelCollides) return current;
        return setDashboardItemLayout(current, resizeState.itemId, candidate);
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
    if (!schemaReady) return;
    saveDashboardDraft(schema);
    setSaveError(null);
    setSavedFlash("draft");
    window.setTimeout(() => setSavedFlash(null), 2400);
  }

  function handleSaveDashboard() {
    if (!schemaReady || !allWidgetsPlaced || isSavingDashboard) return;
    const targetId = activeWorkflowId;
    const saveSequence = ++saveSequenceRef.current;
    const payload = toBackendPayload(schema);
    setIsSavingDashboard(true);
    setSaveError(null);
    setSavedFlash(null);
    saveDashboard(targetId, payload)
      .then(() => {
        if (saveSequence !== saveSequenceRef.current || activeWorkflowIdRef.current !== targetId) return;
        clearDashboardDraft(targetId);
        saveSequenceRef.current += 1;
        onSaveComplete(targetId);
      })
      .catch((error) => {
        if (saveSequence !== saveSequenceRef.current || activeWorkflowIdRef.current !== targetId) return;
        saveDashboardDraft({ ...schema, workflowId: targetId });
        setSavedFlash(null);
        setSaveError(
          error instanceof Error
            ? error.message
            : "Dashboard could not be saved. A local draft was kept.",
        );
      })
      .finally(() => {
        if (saveSequence !== saveSequenceRef.current || activeWorkflowIdRef.current !== targetId) return;
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
            <button
              className="ghost-button ghost-button--back"
              type="button"
              onClick={() => onBackToWidgets(schemaReady ? schema : buildInitialDashboard(workflow))}
            >
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
                <span>Draft saved</span>
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
            <button className="secondary-button" type="button" onClick={handleSaveDraft} disabled={!schemaReady}>
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

        {!schemaReady ? (
          <div className="builder-layout-loading" aria-live="polite" aria-busy="true">
            <div className="builder-loading__panel builder-loading__panel--layout" role="status">
              <div className="builder-loading__status">
                <span className="builder-loading__spinner" aria-hidden="true">
                  <LayoutGrid size={18} />
                </span>
                <div>
                  <strong>Loading dashboard layout</strong>
                  <span>Preparing this workflow's saved dashboard canvas.</span>
                </div>
              </div>
              <div className="builder-loading__layout-preview" aria-hidden="true">
                <div className="builder-loading__tray">
                  <div className="builder-loading-line builder-loading-line--title" />
                  <div className="builder-loading-block" />
                  <div className="builder-loading-block builder-loading-block--short" />
                </div>
                <div className="builder-loading__canvas">
                  <div className="builder-loading-canvas-widget builder-loading-canvas-widget--primary" />
                  <div className="builder-loading-canvas-widget builder-loading-canvas-widget--secondary" />
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="builder-layout-workspace">
          <aside className="layout-widget-tray" aria-label="Widgets to place">
            <header className="layout-widget-tray__header">
              <div>
                <h2>Widgets to place</h2>
                <p>Drag to canvas</p>
              </div>
              <span>{unplacedItems.length}</span>
            </header>

            <div className="layout-widget-tray__list">
              {unplacedItems.length === 0 ? (
                <div className="layout-widget-tray__empty">
                  <CheckCircle2 size={24} aria-hidden="true" />
                  <h3>All widgets placed</h3>
                  <p>Remove a Widget from the canvas to make it available again.</p>
                </div>
              ) : (
                unplacedItems.map((item) => (
                  <TrayDashboardItem
                    key={item.id}
                    item={item}
                    onPointerStart={handleTrayPointerStart}
                  />
                ))
              )}
            </div>
          </aside>

          <DashboardCanvasFrame aria-label="Dashboard layout canvas">
            <DashboardCanvasSurface
              ref={canvasRef}
              empty={placedItems.length === 0}
              rows={canvasRows}
              columns={schema.layout.gridColumns}
              rowHeight={schema.layout.rowHeight}
              gridGap={schema.layout.gridGap}
            >
              {placedItems.length === 0 ? (
                <div className="layout-canvas__empty">
                  <div className="layout-canvas__empty-icon">
                    <Wand2 size={30} aria-hidden="true" />
                  </div>
                  <h2>Start building your dashboard</h2>
                  <p>Drag widgets here to arrange the interface people will see.</p>
                </div>
              ) : null}

              {dropPreview ? (
                <DragPreviewItem
                  item={topLevelItems.find((item) => item.id === dropPreview.itemId) ?? null}
                  layout={dropPreview.layout}
                  columns={schema.layout.gridColumns}
                  gridGap={schema.layout.gridGap}
                  rowHeight={schema.layout.rowHeight}
                  dropPreview
                />
              ) : null}

              {placedItems.map((item) => {
                const layout = dashboardItemLayout(item);
                if (!layout) return null;
                const previewLayout = movePreview?.itemId === item.id ? movePreview.layout : null;
                const displayLayout = previewLayout ?? layout;

                return (
                  <PlacedDashboardItem
                    key={item.id}
                    item={item}
                    layout={displayLayout}
                    columns={schema.layout.gridColumns}
                    gridGap={schema.layout.gridGap}
                    rowHeight={schema.layout.rowHeight}
                    selected={selectedItemId === item.id}
                    dragging={activeDragItemId === item.id}
                    onSelect={() => setSelectedItemId(item.id)}
                    onRemove={() => removeItem(item.id)}
                    onMoveStart={(event) => handleMoveStart(event, item.id, displayLayout)}
                    onResizeStart={(event, handle) => handleResizeStart(event, item.id, displayLayout, handle)}
                  />
                );
              })}

              {dragPreview ? (
                <DragPreviewItem
                  item={topLevelItems.find((item) => item.id === dragPreview.itemId) ?? null}
                  layout={dragPreview.layout}
                  columns={schema.layout.gridColumns}
                  gridGap={schema.layout.gridGap}
                  rowHeight={schema.layout.rowHeight}
                />
              ) : null}
            </DashboardCanvasSurface>
          </DashboardCanvasFrame>
          </div>
        )}
      </div>
    </AppLayout>
  );
}

function TrayDashboardItem({
  item,
  onPointerStart,
}: {
  item: DashboardTopLevelItem;
  onPointerStart: (event: PointerEvent<HTMLElement>, itemId: string) => void;
}) {
  const Icon = item.kind === "group" ? LayoutGrid : WIDGET_ICONS[item.widget.widgetType];
  const title = item.kind === "group" ? item.group.title : item.widget.title;
  const description = item.kind === "group" ? item.group.description : item.widget.description;

  return (
    <article
      className="layout-tray-widget"
      onPointerDown={(event) => onPointerStart(event, item.id)}
    >
      <div className="layout-tray-widget__icon" aria-hidden="true">
        <Icon size={17} />
      </div>
      <div className="layout-tray-widget__body">
        <h3>{title}</h3>
        <span>{item.kind === "group" ? `${item.widgets.length} grouped widgets` : WIDGET_TYPE_LABELS[item.widget.widgetType]}</span>
        {description ? <p>{description}</p> : null}
      </div>
      <GripVertical size={16} aria-hidden="true" />
    </article>
  );
}

function PlacedDashboardItem({
  item,
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
  dropPreview = false,
}: {
  item: DashboardTopLevelItem;
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
  dropPreview?: boolean;
}) {
  const Icon = item.kind === "group" ? LayoutGrid : WIDGET_ICONS[item.widget.widgetType];
  const title = item.kind === "group" ? item.group.title : item.widget.title;
  const subtitle = item.kind === "group" ? `${item.widgets.length} grouped widgets` : WIDGET_TYPE_LABELS[item.widget.widgetType];
  const description = item.kind === "group" ? item.group.description : undefined;

  return (
    <DashboardCanvasWidgetShell
      layout={layout}
      columns={columns}
      gridGap={gridGap}
      rowHeight={rowHeight}
      selected={selected}
      preview={preview}
      className={`${dragging ? "layout-canvas-widget--moving" : ""}${
        dropPreview ? " layout-canvas-widget--drop-preview" : ""
      }`}
      aria-hidden={preview ? true : undefined}
      onClick={onSelect}
      onPointerDown={!preview ? onMoveStart : undefined}
    >
      <header className="layout-canvas-widget__header">
        <div className="layout-canvas-widget__title">
          <span aria-hidden="true">
            <Icon size={16} />
          </span>
          <div>
            <h3>{title}</h3>
            <p>{description || subtitle}</p>
          </div>
        </div>
        {!preview ? (
          <div className="layout-canvas-widget__actions" onClick={(event) => event.stopPropagation()}>
            <button className="icon-button icon-button--card" type="button" aria-label={`Remove ${title}`} title="Remove from canvas" onClick={onRemove}>
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </header>

      <div className="layout-canvas-widget__preview-surface">
        {item.kind === "group" ? <GroupSurfacePreview item={item} /> : <WidgetSurfacePreview widget={item.widget} />}
      </div>
      {!preview ? <DashboardCanvasResizeHandles label={title} onResizeStart={(handle, event) => onResizeStart(event, handle)} /> : null}
    </DashboardCanvasWidgetShell>
  );
}

function DragPreviewItem({
  item,
  layout,
  columns,
  gridGap,
  rowHeight,
  dropPreview = false,
}: {
  item: DashboardTopLevelItem | null;
  layout: DashboardWidgetLayout;
  columns: number;
  gridGap: number;
  rowHeight: number;
  dropPreview?: boolean;
}) {
  if (!item) return null;

  return (
    <PlacedDashboardItem
      item={item}
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
      dropPreview={dropPreview}
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

function dashboardItemLayout(item: DashboardTopLevelItem): DashboardWidgetLayout | undefined {
  return item.kind === "group" ? item.group.layout : item.widget.layout;
}

function defaultLayoutForTopLevelItem(item: DashboardTopLevelItem): DashboardWidgetLayout {
  if (item.kind === "group") {
    return defaultLayoutForWidgetGroup(item.widgets.map((widget) => widget.widgetType));
  }
  return defaultLayoutForWidgetType(item.widget.widgetType);
}

function setDashboardItemLayout(schema: DashboardSchema, itemId: string, layout: DashboardWidgetLayout): DashboardSchema {
  if (schema.groups.some((group) => group.id === itemId)) {
    return {
      ...schema,
      groups: schema.groups.map((group) => (group.id === itemId ? { ...group, layout } : group)),
    };
  }
  return {
    ...schema,
    widgets: schema.widgets.map((widget) => (widget.id === itemId ? { ...widget, layout } : widget)),
  };
}

function removeDashboardItemLayout(schema: DashboardSchema, itemId: string): DashboardSchema {
  if (schema.groups.some((group) => group.id === itemId)) {
    return {
      ...schema,
      groups: schema.groups.map((group) => {
        if (group.id !== itemId) return group;
        const { layout: _layout, ...withoutLayout } = group;
        return withoutLayout;
      }),
    };
  }
  return {
    ...schema,
    widgets: schema.widgets.map((widget) => {
      if (widget.id !== itemId) return widget;
      const { layout: _layout, ...withoutLayout } = widget;
      return withoutLayout;
    }),
  };
}

function GroupSurfacePreview({ item }: { item: Extract<DashboardTopLevelItem, { kind: "group" }> }) {
  return (
    <div className="layout-group-preview">
      {item.widgets.map((widget) => (
        <div className="layout-group-preview__item" key={widget.id}>
          <div className="layout-group-preview__label">
            <span>{widget.title}</span>
            <small>{WIDGET_TYPE_LABELS[widget.widgetType]}</small>
          </div>
          <WidgetSurfacePreview widget={widget} />
        </div>
      ))}
    </div>
  );
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
