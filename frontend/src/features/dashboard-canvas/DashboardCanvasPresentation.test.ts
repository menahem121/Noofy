import { describe, expect, it, vi } from "vitest";

import { fitMovedLayoutPosition, resizeLayoutFromPointerDelta } from "./DashboardCanvasPresentation";

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
