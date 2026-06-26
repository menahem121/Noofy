import { describe, expect, it } from "vitest";

import {
  findAvailableLayout,
  findNearestAvailableLayout,
  findNearestAvailablePosition,
  findNearestAvailablePositionWithinRows,
  fitLayout,
  layoutsOverlap,
  type GridItemLayout,
} from "./gridLayout";

describe("layoutsOverlap", () => {
  it("returns true for identical cells", () => {
    const a: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    expect(layoutsOverlap(a, a)).toBe(true);
  });

  it("returns true when one cell partially overlaps another", () => {
    const a: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const b: GridItemLayout = { x: 3, y: 1, w: 4, h: 2 };
    expect(layoutsOverlap(a, b)).toBe(true);
  });

  it("returns false for cells in the same row that do not overlap horizontally", () => {
    const a: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const b: GridItemLayout = { x: 4, y: 0, w: 4, h: 2 };
    expect(layoutsOverlap(a, b)).toBe(false);
  });

  it("returns false for cells in the same column that do not overlap vertically", () => {
    const a: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const b: GridItemLayout = { x: 0, y: 2, w: 4, h: 2 };
    expect(layoutsOverlap(a, b)).toBe(false);
  });

  it("returns false for cells that are completely separate", () => {
    const a: GridItemLayout = { x: 0, y: 0, w: 3, h: 2 };
    const b: GridItemLayout = { x: 6, y: 5, w: 3, h: 2 };
    expect(layoutsOverlap(a, b)).toBe(false);
  });
});

describe("fitLayout", () => {
  it("clamps x so widget fits within column count", () => {
    const layout: GridItemLayout = { x: 10, y: 0, w: 4, h: 2 };
    const result = fitLayout(layout, 12);
    expect(result.x).toBe(8); // 12 - 4
  });

  it("does not move widget that already fits", () => {
    const layout: GridItemLayout = { x: 3, y: 2, w: 4, h: 2 };
    const result = fitLayout(layout, 12);
    expect(result).toEqual(layout);
  });

  it("clamps x to 0 if widget is wider than grid", () => {
    const layout: GridItemLayout = { x: 0, y: 0, w: 14, h: 2 };
    const result = fitLayout(layout, 12);
    expect(result.x).toBe(0);
  });

  it("preserves y and h unchanged", () => {
    const layout: GridItemLayout = { x: 10, y: 5, w: 4, h: 3 };
    const result = fitLayout(layout, 12);
    expect(result.y).toBe(5);
    expect(result.h).toBe(3);
  });
});

describe("findAvailableLayout", () => {
  it("returns the desired position when there is no collision", () => {
    const desired: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const result = findAvailableLayout("new", desired, [], 12);
    expect(result).toEqual(desired);
  });

  it("returns the desired position when the only collision is with self", () => {
    const desired: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const items = [{ id: "self", layout: desired }];
    const result = findAvailableLayout("self", desired, items, 12);
    expect(result).toEqual(desired);
  });

  it("avoids a collision at the desired position", () => {
    const desired: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const blocker: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const items = [{ id: "blocker", layout: blocker }];
    const result = findAvailableLayout("new", desired, items, 12);
    expect(layoutsOverlap(result, blocker)).toBe(false);
  });

  it("finds space when the row below the desired position is also occupied", () => {
    const desired: GridItemLayout = { x: 0, y: 0, w: 4, h: 2 };
    const items = [
      { id: "a", layout: { x: 0, y: 0, w: 4, h: 2 } },
      { id: "b", layout: { x: 0, y: 2, w: 4, h: 2 } },
    ];
    const result = findAvailableLayout("new", desired, items, 12);
    expect(layoutsOverlap(result, items[0].layout)).toBe(false);
    expect(layoutsOverlap(result, items[1].layout)).toBe(false);
  });
});

describe("findNearestAvailableLayout", () => {
  it("returns the desired position when there is no collision", () => {
    const desired: GridItemLayout = { x: 4, y: 3, w: 4, h: 2 };
    const result = findNearestAvailableLayout("new", desired, [], 12);
    expect(result).toEqual(desired);
  });

  it("chooses the nearest free position instead of scanning forward by row", () => {
    const desired: GridItemLayout = { x: 0, y: 4, w: 4, h: 2 };
    const items = [
      { id: "self", layout: { x: 0, y: 0, w: 4, h: 2 } },
      { id: "blocker", layout: { x: 0, y: 4, w: 4, h: 2 } },
      { id: "below", layout: { x: 0, y: 6, w: 4, h: 2 } },
    ];

    const result = findNearestAvailableLayout("self", desired, items, 12);

    expect(result).toEqual({ x: 0, y: 2, w: 4, h: 2 });
  });

  it("uses deterministic tie-breaking for equally near free positions", () => {
    const desired: GridItemLayout = { x: 4, y: 4, w: 4, h: 2 };
    const items = [{ id: "blocker", layout: { x: 4, y: 4, w: 4, h: 2 } }];

    const result = findNearestAvailableLayout("new", desired, items, 12);

    expect(result).toEqual({ x: 4, y: 2, w: 4, h: 2 });
  });
});

describe("findNearestAvailablePosition", () => {
  it("preserves dimensions below the current minimum while moving", () => {
    const desired: GridItemLayout = { x: 4, y: 3, w: 3, h: 2, minW: 5, minH: 4 };

    expect(findNearestAvailablePosition("item", desired, [], 12)).toEqual(desired);
  });

  it("finds a collision-free position without fitting dimensions", () => {
    const desired: GridItemLayout = { x: 0, y: 4, w: 3, h: 2, minW: 5, minH: 4 };
    const items = [{ id: "blocker", layout: { x: 0, y: 4, w: 3, h: 2 } }];

    const result = findNearestAvailablePosition("item", desired, items, 12);

    expect(result.w).toBe(3);
    expect(result.h).toBe(2);
    expect(layoutsOverlap(result, items[0].layout)).toBe(false);
  });
});

describe("findNearestAvailablePositionWithinRows", () => {
  it("clamps the desired position to the bottom row that still fits the item", () => {
    const desired: GridItemLayout = { x: 0, y: 50, w: 4, h: 3 };

    expect(findNearestAvailablePositionWithinRows("item", desired, [], 12, 10)).toEqual({
      x: 0,
      y: 7,
      w: 4,
      h: 3,
    });
  });

  it("chooses a visible free cell instead of falling below the bounded rows", () => {
    const desired: GridItemLayout = { x: 0, y: 7, w: 4, h: 3 };
    const items = [{ id: "blocker", layout: { x: 0, y: 7, w: 12, h: 3 } }];

    expect(findNearestAvailablePositionWithinRows("item", desired, items, 12, 10)).toEqual({
      x: 0,
      y: 4,
      w: 4,
      h: 3,
    });
  });

  it("returns null when no visible row can accept the item", () => {
    const desired: GridItemLayout = { x: 0, y: 0, w: 12, h: 2 };
    const items = [
      { id: "a", layout: { x: 0, y: 0, w: 12, h: 2 } },
      { id: "b", layout: { x: 0, y: 2, w: 12, h: 2 } },
    ];

    expect(findNearestAvailablePositionWithinRows("item", desired, items, 12, 4)).toBeNull();
  });
});
