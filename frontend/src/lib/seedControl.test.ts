import { describe, expect, it } from "vitest";

import {
  DEFAULT_SEED_MODE,
  isSeedMode,
  nextSeedValue,
  randomSeed,
  seedModeFromValidation,
} from "./seedControl";

describe("seedControl", () => {
  it("recognizes the supported seed modes", () => {
    expect(isSeedMode("randomize")).toBe(true);
    expect(isSeedMode("fixed")).toBe(true);
    expect(isSeedMode("increment")).toBe(true);
    expect(isSeedMode("decrement")).toBe(false);
    expect(isSeedMode(undefined)).toBe(false);
  });

  it("falls back to the default mode when validation is missing or invalid", () => {
    expect(seedModeFromValidation(undefined)).toBe(DEFAULT_SEED_MODE);
    expect(seedModeFromValidation({})).toBe(DEFAULT_SEED_MODE);
    expect(seedModeFromValidation({ seed_mode: "nope" })).toBe(DEFAULT_SEED_MODE);
    expect(seedModeFromValidation({ seed_mode: "fixed" })).toBe("fixed");
  });

  it("keeps the value unchanged for fixed mode", () => {
    expect(nextSeedValue(42, "fixed")).toBe(42);
  });

  it("increments by the step (defaulting to 1) for increment mode", () => {
    expect(nextSeedValue(42, "increment")).toBe(43);
    expect(nextSeedValue(42, "increment", { step: 5 })).toBe(47);
  });

  it("wraps increment back to the minimum when it would exceed the maximum", () => {
    expect(nextSeedValue(10, "increment", { min: 0, max: 10 })).toBe(0);
  });

  it("treats a non-numeric current value as zero", () => {
    expect(nextSeedValue(undefined, "increment")).toBe(1);
    expect(nextSeedValue("oops", "fixed")).toBe(0);
  });

  it("produces a random value within the configured range", () => {
    for (let i = 0; i < 50; i += 1) {
      const value = randomSeed({ min: 5, max: 9 });
      expect(Number.isInteger(value)).toBe(true);
      expect(value).toBeGreaterThanOrEqual(5);
      expect(value).toBeLessThanOrEqual(9);
    }
  });
});
