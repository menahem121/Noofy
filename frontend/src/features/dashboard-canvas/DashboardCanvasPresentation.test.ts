import { describe, expect, it, vi } from "vitest";

import {
  canvasRowsForItems,
  dashboardCanvasRenderRowHeight,
  dashboardCanvasVisualGap,
  dashboardCanvasWidgetStyle,
  fitMovedLayoutPosition,
  resizeLayoutFromPointerDelta,
} from "./DashboardCanvasPresentation";

function mockCanvas(width = 320): HTMLElement {
  const canvas = document.createElement("div");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: 768,
    width,
    height: 768,
    toJSON: () => ({}),
  } as DOMRect);
  return canvas;
}

describe("canvasRowsForItems", () => {
  it("uses 24 rows as the stable visible canvas without adding bottom padding", () => {
    expect(canvasRowsForItems([{ layout: { x: 0, y: 18, w: 32, h: 6 } }])).toBe(24);
  });

  it("grows only when items extend past the stable visible rows", () => {
    expect(canvasRowsForItems([{ layout: { x: 0, y: 24, w: 32, h: 4 } }])).toBe(28);
  });
});

describe("dashboardCanvasRenderRowHeight", () => {
  it("derives responsive row height from the available 24-row canvas", () => {
    expect(dashboardCanvasRenderRowHeight({ availableHeight: 960 })).toBe(40);
    expect(dashboardCanvasRenderRowHeight({ availableHeight: 600 })).toBe(25);
  });

  it("uses fixed row height when responsiveness is disabled or the canvas is not measurable", () => {
    expect(dashboardCanvasRenderRowHeight({ availableHeight: 960, rowHeight: 32, responsive: false })).toBe(32);
    expect(dashboardCanvasRenderRowHeight({ availableHeight: null, rowHeight: 36 })).toBe(36);
  });
});

describe("dashboardCanvasVisualGap", () => {
  it("keeps the original 14px gap at the fallback 32px row height", () => {
    expect(dashboardCanvasVisualGap({ rowHeight: 32, gridGap: 14 })).toBe(14);
  });

  it("scales visual gaps with responsive row height", () => {
    expect(dashboardCanvasVisualGap({ rowHeight: 40, gridGap: 14 })).toBe(17.5);
  });

  it("clamps default gaps on very short and very tall rows", () => {
    expect(dashboardCanvasVisualGap({ rowHeight: 16, gridGap: 14 })).toBe(10);
    expect(dashboardCanvasVisualGap({ rowHeight: 80, gridGap: 14 })).toBe(24);
  });

  it("respects intentionally smaller authored grid gaps", () => {
    expect(dashboardCanvasVisualGap({ rowHeight: 40, gridGap: 2 })).toBe(2.5);
  });
});

describe("dashboardCanvasWidgetStyle", () => {
  it("projects tile position and size through the active row height", () => {
    expect(dashboardCanvasWidgetStyle({ x: 8, y: 2, w: 16, h: 6 }, { columns: 32, rowHeight: 40 })).toMatchObject({
      left: "25%",
      top: "80px",
      width: "50%",
      height: "240px",
      minHeight: "240px",
    });
  });

  it("keeps visual insets at canvas edges while preserving the tile shell", () => {
    expect(
      dashboardCanvasWidgetStyle(
        { x: 0, y: 18, w: 16, h: 6 },
        { columns: 32, rowHeight: 40 },
      ),
    ).toMatchObject({
      left: "0%",
      top: "720px",
      width: "50%",
      height: "240px",
      "--layout-widget-inset-left": "var(--layout-widget-visual-inset, 0px)",
      "--layout-widget-inset-bottom": "var(--layout-widget-visual-inset, 0px)",
      "--layout-widget-inset-top": "var(--layout-widget-visual-inset, 0px)",
      "--layout-widget-inset-right": "var(--layout-widget-visual-inset, 0px)",
    });
  });
});

describe("resizeLayoutFromPointerDelta", () => {
  it("resizes from the top-left corner while keeping the bottom-right corner fixed", () => {
    const result = resizeLayoutFromPointerDelta({
      startLayout: { x: 4, y: 4, w: 8, h: 6, minW: 3, minH: 2 },
      startClientX: 100,
      startClientY: 100,
      clientX: 80,
      clientY: 36,
      canvas: mockCanvas(),
      handle: "northwest",
      columns: 32,
      rowHeight: 32,
    });

    expect(result).toEqual({ x: 2, y: 2, w: 10, h: 8, minW: 3, minH: 2 });
  });

  it("resizes from the top-left corner without crossing minimum size", () => {
    const result = resizeLayoutFromPointerDelta({
      startLayout: { x: 4, y: 4, w: 8, h: 6, minW: 3, minH: 2 },
      startClientX: 100,
      startClientY: 100,
      clientX: 200,
      clientY: 292,
      canvas: mockCanvas(),
      handle: "northwest",
      columns: 32,
      rowHeight: 32,
    });

    expect(result).toEqual({ x: 9, y: 8, w: 3, h: 2, minW: 3, minH: 2 });
  });

  it("resizes each corner on the expected axes", () => {
    const startLayout = { x: 4, y: 4, w: 8, h: 6, minW: 3, minH: 2 };
    const canvas = mockCanvas();

    expect(
      resizeLayoutFromPointerDelta({
        startLayout,
        startClientX: 100,
        startClientY: 100,
        clientX: 120,
        clientY: 36,
        canvas,
        handle: "northeast",
        columns: 32,
        rowHeight: 32,
      }),
    ).toMatchObject({ x: 4, y: 2, w: 10, h: 8 });

    expect(
      resizeLayoutFromPointerDelta({
        startLayout,
        startClientX: 100,
        startClientY: 100,
        clientX: 80,
        clientY: 164,
        canvas,
        handle: "southwest",
        columns: 32,
        rowHeight: 32,
      }),
    ).toMatchObject({ x: 2, y: 4, w: 10, h: 8 });

    expect(
      resizeLayoutFromPointerDelta({
        startLayout,
        startClientX: 100,
        startClientY: 100,
        clientX: 120,
        clientY: 164,
        canvas,
        handle: "southeast",
        columns: 32,
        rowHeight: 32,
      }),
    ).toMatchObject({ x: 4, y: 4, w: 10, h: 8 });
  });

  it("enforces current minimums when resizing a loaded layout that starts below them", () => {
    const result = resizeLayoutFromPointerDelta({
      startLayout: { x: 0, y: 0, w: 3, h: 2, minW: 5, minH: 4 },
      startClientX: 100,
      startClientY: 100,
      clientX: 120,
      clientY: 132,
      canvas: mockCanvas(),
      handle: "northwest",
      columns: 32,
      rowHeight: 32,
    });

    expect(result).toEqual({ x: 0, y: 0, w: 5, h: 4, minW: 5, minH: 4 });
  });

  it("keeps oversized loaded layouts at the left edge while moving", () => {
    expect(fitMovedLayoutPosition({ x: 4, y: 2, w: 40, h: 4 }, 32)).toEqual({
      x: 0,
      y: 2,
      w: 40,
      h: 4,
    });
  });
});
