import {
  forwardRef,
  type CSSProperties,
  type DragEvent,
  type HTMLAttributes,
  type ReactNode,
} from "react";

import type { GridItemLayout } from "../../lib/gridLayout";

export const DASHBOARD_CANVAS_COLUMNS = 12;
export const DASHBOARD_CANVAS_ROW_HEIGHT = 64;
export const DASHBOARD_CANVAS_GRID_GAP = 14;
export const DASHBOARD_CANVAS_MIN_ROWS = 12;

export interface DashboardCanvasMetrics {
  columns?: number;
  rowHeight?: number;
  gridGap?: number;
}

export interface DashboardCanvasItem {
  layout?: GridItemLayout | null;
}

export function canvasRowsForItems(
  items: DashboardCanvasItem[],
  minRows = DASHBOARD_CANVAS_MIN_ROWS,
): number {
  return Math.max(
    minRows,
    ...items.map((item) => (item.layout ? item.layout.y + item.layout.h + 2 : 0)),
  );
}

export function dashboardCanvasWidgetStyle(
  layout: GridItemLayout,
  {
    columns = DASHBOARD_CANVAS_COLUMNS,
    rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
    gridGap = DASHBOARD_CANVAS_GRID_GAP,
  }: DashboardCanvasMetrics = {},
): CSSProperties {
  return {
    left: `calc(${(layout.x / columns) * 100}% + ${gridGap / 2}px)`,
    top: `${layout.y * rowHeight + gridGap / 2}px`,
    width: `calc(${(layout.w / columns) * 100}% - ${gridGap}px)`,
    minHeight: `${layout.h * rowHeight - gridGap}px`,
  };
}

export function layoutFromCanvasPointer(
  event: DragEvent,
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
    x: clamp(rawX, 0, columns - currentLayout.w),
    y: Math.max(0, rawY),
  };
}

export function DashboardCanvasFrame({
  children,
  className = "",
  "aria-label": ariaLabel,
}: {
  children: ReactNode;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <main className={`layout-canvas${className ? ` ${className}` : ""}`} aria-label={ariaLabel}>
      {children}
    </main>
  );
}

export const DashboardCanvasSurface = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement> & {
  rows: number;
  rowHeight?: number;
  gridGap?: number;
  empty?: boolean;
}>(function DashboardCanvasSurface({
  children,
  className = "",
  rows,
  rowHeight = DASHBOARD_CANVAS_ROW_HEIGHT,
  gridGap = DASHBOARD_CANVAS_GRID_GAP,
  empty = false,
  style,
  ...props
}, ref) {
  const surfaceStyle = {
    minHeight: `${rows * rowHeight}px`,
    "--layout-row-height": `${rowHeight}px`,
    "--layout-grid-gap": `${gridGap}px`,
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
  gridGap = DASHBOARD_CANVAS_GRID_GAP,
  selected = false,
  preview = false,
  className = "",
  ...props
}: HTMLAttributes<HTMLElement> & {
  children: ReactNode;
  layout: GridItemLayout;
  columns?: number;
  rowHeight?: number;
  gridGap?: number;
  selected?: boolean;
  preview?: boolean;
}) {
  const layoutStyle = dashboardCanvasWidgetStyle(layout, { columns, rowHeight, gridGap });
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

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
