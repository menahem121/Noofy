// Seed "control after generate" behavior, mirroring ComfyUI's seed widget.
//
// A seed widget can advance its value after each generation in one of three
// ways. The selected mode is persisted on the dashboard (the builder's default)
// inside the input validation as `seed_mode`, and applied at run time after each
// queued generation.

export type SeedMode = "randomize" | "fixed" | "increment";

export const SEED_MODES: readonly SeedMode[] = ["randomize", "fixed", "increment"];

export const SEED_MODE_LABELS: Record<SeedMode, string> = {
  randomize: "Randomize each run",
  fixed: "Fixed",
  increment: "Increment",
};

export const DEFAULT_SEED_MODE: SeedMode = "randomize";

// 2^50 — comfortably within Number.MAX_SAFE_INTEGER so increments and random
// draws never lose integer precision.
const MAX_SEED = 1_125_899_906_842_624;

export function isSeedMode(value: unknown): value is SeedMode {
  return value === "randomize" || value === "fixed" || value === "increment";
}

export function seedModeFromValidation(
  validation: Record<string, unknown> | null | undefined,
): SeedMode {
  const raw = validation?.["seed_mode"];
  return isSeedMode(raw) ? raw : DEFAULT_SEED_MODE;
}

// Advance to the next mode in the cycle. The runtime seed control is a single
// toggle button that steps through randomize → fixed → increment on each click.
export function nextSeedMode(mode: SeedMode): SeedMode {
  const index = SEED_MODES.indexOf(mode);
  return SEED_MODES[(index + 1) % SEED_MODES.length];
}

function numericFromValidation(
  validation: Record<string, unknown> | null | undefined,
  key: "min" | "max" | "step",
): number | undefined {
  const raw = validation?.[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : undefined;
}

export function randomSeed(validation?: Record<string, unknown> | null): number {
  const min = numericFromValidation(validation, "min") ?? 0;
  const max = numericFromValidation(validation, "max") ?? MAX_SEED;
  if (!(max > min)) return min;
  const span = max - min + 1;
  return Math.floor(min + Math.random() * span);
}

// Produce the seed value to use for the *next* generation, given the value that
// was just submitted and the selected mode.
export function nextSeedValue(
  current: unknown,
  mode: SeedMode,
  validation?: Record<string, unknown> | null,
): number {
  const base = typeof current === "number" && Number.isFinite(current) ? current : 0;
  if (mode === "fixed") return base;
  if (mode === "randomize") return randomSeed(validation);
  // increment
  const step = numericFromValidation(validation, "step");
  const increment = step !== undefined && step > 0 ? step : 1;
  const max = numericFromValidation(validation, "max") ?? MAX_SEED;
  const min = numericFromValidation(validation, "min") ?? 0;
  const next = base + increment;
  return next > max ? min : next;
}
