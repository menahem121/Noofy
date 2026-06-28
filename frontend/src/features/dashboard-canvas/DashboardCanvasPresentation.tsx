import {
  forwardRef,
  useCallback,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type PointerEvent,
  type RefObject,
  type ReactNode,
} from "react";

import type { GridItemLayout } from "../../lib/gridLayout";

export const DASHBOARD_CANVAS_COLUMNS = 32;
export const DASHBOARD_CANVAS_ROW_HEIGHT = 32;
export const DASHBOARD_CANVAS_GRID_GAP = 14;
export const DASHBOARD_CANVAS_MIN_ROWS = 24;
export const DASHBOARD_CANVAS_MIN_VISUAL_GAP = 10;
export const DASHBOARD_CANVAS_MAX_VISUAL_GAP = 24;

export interface DashboardCanvasMetrics {
  columns?: number;
  rowHeight?: number;
}

export interface DashboardCanvasRowHeightOptions {
  availableHeight?: number | null;
  rowHeight?: number;
  responsive?: boolean;
  minRows?: number;
}

export interface DashboardCanvasVisualGapOptions {
  rowHeight?: number;
  gridGap?: number;
}

export interface DashboardCanvasItem {
  layout?: GridItemLayout | null;
}

export type DashboardResizeHandle = "northwest" | "northeast" | "southwest" | "southeast";

interface CanvasPointerLocation {
  clientX: number;
  clientY: number;
}

export function canvasRowsForItems(
  items: DashboardCanvasItem[],
  minRows = DASHBOARD_CANVAS_MIN_ROWS,
): number {
  return Math.max(
    minRows,
    ...items.map((item) => (item.layout ? item.layout.y + item.layout.h : 0)),
  );
}

export function dashboardCanvasRenderRowHeight({
  availableHeight,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  responsive = true,
  minRows = DASHBOARD_CANVAS_MIN_ROWS,
}: DashboardCanvasRowHeightOptions = {}): number {
  const fallbackRowHeight = positiveFinite(rowHeight) ? rowHeight : DASHBOARD_CANVAS_ROW_HEIGHT;
  if (!responsive) return fallbackRowHeight;
  if (!positiveFinite(availableHeight) || !positiveFinite(minRows)) return fallbackRowHeight;
  return Math.max(1, availableHeight / minRows);
}

export function dashboardCanvasVisualGap({
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  gridGap = DASHBOARD_CANVAS_GRID_GAP,
}: DashboardCanvasVisualGapOptions = {}): number {
  const fallbackGap = positiveFinite(gridGap) ? gridGap : DASHBOARD_CANVAS_GRID_GAP;
  if (!positiveFinite(rowHeight)) return fallbackGap;
  const scaledGap = fallbackGap * (rowHeight / DASHBOARD_CANVAS_ROW_HEIGHT);
  return clamp(
    scaledGap,
    Math.min(fallbackGap, DASHBOARD_CANVAS_MIN_VISUAL_GAP),
    Math.max(fallbackGap, DASHBOARD_CANVAS_MAX_VISUAL_GAP),
  );
}

export function dashboardCanvasAvailableHeight(
  frame: HTMLElement | null,
  surface: HTMLElement | null = null,
): number | null {
  const frameRect = frame?.getBoundingClientRect();
  const surfaceRect = surface?.getBoundingClientRect();
  const height = frame
    ? frame.clientHeight || frameRect?.height || surfaceRect?.height
    : surface?.clientHeight || surfaceRect?.height;
  return positiveFinite(height) ? height : null;
}

export function useDashboardCanvasRowHeight({
  frameRef,
  surfaceRef,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  responsive = true,
  minRows = DASHBOARD_CANVAS_MIN_ROWS,
}: {
  frameRef: RefObject<HTMLElement | null>;
  surfaceRef?: RefObject<HTMLElement | null>;
} & Omit<DashboardCanvasRowHeightOptions, "availableHeight">): number {
  const [availableHeight, setAvailableHeight] = useState<number | null>(null);

  const measure = useCallback(() => {
    const nextHeight = dashboardCanvasAvailableHeight(frameRef.current, surfaceRef?.current ?? null);
    setAvailableHeight((currentHeight) => {
      if (currentHeight === null || nextHeight === null) return currentHeight === nextHeight ? currentHeight : nextHeight;
      return Math.abs(currentHeight - nextHeight) < 0.5 ? currentHeight : nextHeight;
    });
  }, [frameRef, surfaceRef]);

  useLayoutEffect(() => {
    measure();

    const frame = frameRef.current;
    const surface = surfaceRef?.current ?? null;
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(measure);
    if (resizeObserver) {
      if (frame) resizeObserver.observe(frame);
      if (surface) resizeObserver.observe(surface);
    }

    window.addEventListener("resize", measure);
    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [frameRef, measure, surfaceRef]);

  return dashboardCanvasRenderRowHeight({
    availableHeight,
    rowHeight,
    responsive,
    minRows,
  });
}

export function dashboardCanvasWidgetStyle(
  layout: GridItemLayout,
  {
    columns = DASHBOARD_CANVAS_COLUMNS,
    rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  }: DashboardCanvasMetrics = {},
): CSSProperties {
  const visualInset = "var(--layout-widget-visual-inset, 0px)";
  return {
    left: `${(layout.x / columns) * 100}%`,
    top: `${layout.y * rowHeight}px`,
    width: `${(layout.w / columns) * 100}%`,
    height: `${layout.h * rowHeight}px`,
    minHeight: `${layout.h * rowHeight}px`,
    "--layout-widget-inset-top": visualInset,
    "--layout-widget-inset-right": visualInset,
    "--layout-widget-inset-bottom": visualInset,
    "--layout-widget-inset-left": visualInset,
  } as CSSProperties;
}

export function layoutFromCanvasPointer(
  event: CanvasPointerLocation,
  currentLayout: GridItemLayout,
  canvas: HTMLElement | null,
  {
    columns = DASHBOARD_CANVAS_COLUMNS,
    rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  }: DashboardCanvasMetrics = {},
): GridItemLayout | null {
  if (!canvas) return null;

  const rect = canvas.getBoundingClientRect();
  const columnWidth = rect.width / columns;
  const rawX = Math.floor((event.clientX - rect.left - (currentLayout.w * columnWidth) / 2) / columnWidth);
  const rawY = Math.floor((event.clientY - rect.top - (currentLayout.h * rowHeight) / 2) / rowHeight);

  return {
    ...currentLayout,
    x: clamp(rawX, 0, Math.max(0, columns - currentLayout.w)),
    y: Math.max(0, rawY),
  };
}

export function resizeLayoutFromPointerDelta({
  startLayout,
  startClientX,
  startClientY,
  clientX,
  clientY,
  canvas,
  handle,
  columns = DASHBOARD_CANVAS_COLUMNS,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
}: {
  startLayout: GridItemLayout;
  startClientX: number;
  startClientY: number;
  clientX: number;
  clientY: number;
  canvas: HTMLElement | null;
  handle: DashboardResizeHandle;
} & DashboardCanvasMetrics): GridItemLayout {
  const rect = canvas?.getBoundingClientRect();
  const columnWidth = rect ? rect.width / columns : 1;
  const deltaColumns = Math.round((clientX - startClientX) / columnWidth);
  const deltaRows = Math.round((clientY - startClientY) / rowHeight);
  const minW = startLayout.minW ?? 2;
  const minH = startLayout.minH ?? 2;

  const right = startLayout.x + startLayout.w;
  const bottom = startLayout.y + startLayout.h;
  const nextLayout = { ...startLayout };

  if (handle === "northeast" || handle === "southeast") {
    nextLayout.w = clamp(startLayout.w + deltaColumns, minW, columns - startLayout.x);
  }

  if (handle === "northwest" || handle === "southwest") {
    nextLayout.x = clamp(startLayout.x + deltaColumns, 0, Math.max(0, right - minW));
    nextLayout.w = Math.max(minW, right - nextLayout.x);
  }

  if (handle === "southwest" || handle === "southeast") {
    nextLayout.h = Math.max(minH, startLayout.h + deltaRows);
  }

  if (handle === "northwest" || handle === "northeast") {
    nextLayout.y = clamp(startLayout.y + deltaRows, 0, Math.max(0, bottom - minH));
    nextLayout.h = Math.max(minH, bottom - nextLayout.y);
  }

  return nextLayout;
}

export function moveLayoutFromPointerDelta({
  startLayout,
  startClientX,
  startClientY,
  clientX,
  clientY,
  canvas,
  columns = DASHBOARD_CANVAS_COLUMNS,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
}: {
  startLayout: GridItemLayout;
  startClientX: number;
  startClientY: number;
  clientX: number;
  clientY: number;
  canvas: HTMLElement | null;
} & DashboardCanvasMetrics): GridItemLayout {
  const rect = canvas?.getBoundingClientRect();
  const columnWidth = rect ? rect.width / columns : 1;
  const deltaColumns = Math.round((clientX - startClientX) / columnWidth);
  const deltaRows = Math.round((clientY - startClientY) / rowHeight);

  return {
    ...startLayout,
    x: clamp(startLayout.x + deltaColumns, 0, Math.max(0, columns - startLayout.w)),
    y: Math.max(0, startLayout.y + deltaRows),
  };
}

export function fitMovedLayoutPosition(
  layout: GridItemLayout,
  columns = DASHBOARD_CANVAS_COLUMNS,
): GridItemLayout {
  return {
    ...layout,
    x: clamp(layout.x, 0, Math.max(0, columns - layout.w)),
    y: Math.max(0, layout.y),
  };
}

export function sameGridLayout(a: GridItemLayout, b: GridItemLayout): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}

export const DashboardCanvasFrame = forwardRef<HTMLElement, {
  children: ReactNode;
  className?: string;
  "aria-label"?: string;
}>(function DashboardCanvasFrame({
  children,
  className = "",
  "aria-label": ariaLabel,
}, ref) {
  return (
    <main ref={ref} className={`layout-canvas${className ? ` ${className}` : ""}`} aria-label={ariaLabel}>
      {children}
    </main>
  );
});

export const DashboardCanvasSurface = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement> & {
  rows: number;
  columns?: number;
  rowHeight?: number;
  gridGap?: number;
  empty?: boolean;
}>(function DashboardCanvasSurface({
  children,
  className = "",
  rows,
  columns = DASHBOARD_CANVAS_COLUMNS,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  gridGap = DASHBOARD_CANVAS_GRID_GAP,
  empty = false,
  style,
  ...props
}, ref) {
  const visualGap = dashboardCanvasVisualGap({ rowHeight, gridGap });
  const surfaceStyle = {
    "--layout-surface-min-height": `${rows * rowHeight}px`,
    "--layout-columns": columns,
    "--layout-column-width": `${100 / columns}%`,
    "--layout-row-height": `${rowHeight}px`,
    "--layout-grid-gap": `${gridGap}px`,
    "--layout-widget-visual-gap": `${visualGap}px`,
    ...style,
  } as CSSProperties;

  return (
    <div
      ref={ref}
      className={`layout-canvas__surface${empty ? " layout-canvas__surface--empty" : ""}${
        className ? ` ${className}` : ""
      }`}
      style={surfaceStyle}
      {...props}
    >
      <div className="layout-canvas__glow" aria-hidden="true" />
      {children}
    </div>
  );
});

export function DashboardCanvasWidgetShell({
  children,
  layout,
  columns = DASHBOARD_CANVAS_COLUMNS,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  selected = false,
  preview = false,
  className = "",
  ...props
}: HTMLAttributes<HTMLElement> & {
  children: ReactNode;
  layout: GridItemLayout;
  columns?: number;
  rowHeight?: number;
  selected?: boolean;
  preview?: boolean;
}) {
  const layoutStyle = dashboardCanvasWidgetStyle(layout, { columns, rowHeight });
  const combinedStyle = { ...layoutStyle, ...props.style };

  return (
    <article
      {...props}
      className={`layout-canvas-widget${selected ? " layout-canvas-widget--selected" : ""}${
        preview ? " layout-canvas-widget--preview" : ""
      }${className ? ` ${className}` : ""}`}
      style={combinedStyle}
    >
      {children}
    </article>
  );
}

export function DashboardCanvasResizeHandles({
  label,
  onResizeStart,
}: {
  label: string;
  onResizeStart: (handle: DashboardResizeHandle, event: PointerEvent<HTMLButtonElement>) => void;
}) {
  const handles: Array<{ handle: DashboardResizeHandle; label: string; title: string }> = [
    { handle: "northwest", label: `Resize ${label} from top-left`, title: "Resize from top-left" },
    { handle: "northeast", label: `Resize ${label} from top-right`, title: "Resize from top-right" },
    { handle: "southwest", label: `Resize ${label} from bottom-left`, title: "Resize from bottom-left" },
    { handle: "southeast", label: `Resize ${label} from bottom-right`, title: "Resize from bottom-right" },
  ];

  return (
    <div className="layout-canvas-resize-handles" aria-hidden="false">
      {handles.map((handle) => (
        <button
          key={handle.handle}
          className={`layout-canvas-resize-handle layout-canvas-resize-handle--${handle.handle}`}
          type="button"
          aria-label={handle.label}
          title={handle.title}
          onPointerDown={(event) => onResizeStart(handle.handle, event)}
        />
      ))}
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function positiveFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}
