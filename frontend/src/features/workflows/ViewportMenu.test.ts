import { describe, expect, it } from "vitest";

import { calculateViewportMenuPosition } from "./ViewportMenu";

describe("calculateViewportMenuPosition", () => {
  it("opens above the trigger when the menu cannot fit below it", () => {
    expect(calculateViewportMenuPosition({
      menuHeight: 220,
      menuWidth: 260,
      triggerRect: { bottom: 592, right: 790, top: 560 },
      viewportHeight: 600,
      viewportWidth: 800,
    })).toEqual({
      left: 530,
      maxHeight: 544,
      placement: "top",
      top: 332,
    });
  });

  it("opens below when it fits and clamps the menu inside the left viewport edge", () => {
    expect(calculateViewportMenuPosition({
      menuHeight: 180,
      menuWidth: 260,
      triggerRect: { bottom: 72, right: 120, top: 40 },
      viewportHeight: 600,
      viewportWidth: 800,
    })).toEqual({
      left: 8,
      maxHeight: 512,
      placement: "bottom",
      top: 80,
    });
  });

  it("uses the larger side and limits menu height when neither side fully fits", () => {
    expect(calculateViewportMenuPosition({
      menuHeight: 700,
      menuWidth: 260,
      triggerRect: { bottom: 332, right: 500, top: 300 },
      viewportHeight: 600,
      viewportWidth: 800,
    })).toEqual({
      left: 240,
      maxHeight: 284,
      placement: "top",
      top: 8,
    });
  });
});
