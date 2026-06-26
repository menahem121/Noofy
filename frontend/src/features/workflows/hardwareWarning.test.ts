import { describe, expect, it } from "vitest";

import type { WorkflowHardwareWarning } from "../../lib/api/noofyApi";
import { hardwareWarningPillView } from "./hardwareWarning";

const baseWarning: WorkflowHardwareWarning = {
  severity: "high",
  confidence: "medium",
  exceeds_machine_capacity: false,
  reason_codes: ["estimated_vram_capacity_risk"],
  estimate: {
    estimated_peak_vram_mb: 21_000,
    estimated_peak_ram_mb: null,
    source: "local_observed",
    confidence: "high",
  },
  machine_signal: {
    backend: "cuda",
    memory_pressure: "low",
    total_vram_mb: 23_000,
    free_vram_mb: 13_000,
    total_ram_mb: 64_000,
    free_ram_mb: 48_000,
    signal_quality: "backend_api",
  },
  evidence: {
    local_successful_runs: 1,
    local_memory_error_runs: 0,
    local_input_profile_match: "matching",
    creator_observation_available: false,
    model_size_heuristic_available: false,
    required_model_size_mb: null,
  },
  developer_details: {},
};

describe("hardwareWarningPillView", () => {
  it("reserves likely too heavy for local memory errors", () => {
    expect(hardwareWarningPillView(baseWarning).label).toBe("May be heavy");

    expect(
      hardwareWarningPillView({
        ...baseWarning,
        reason_codes: ["local_memory_error"],
        evidence: {
          ...baseWarning.evidence,
          local_successful_runs: 0,
          local_memory_error_runs: 1,
        },
      }).label,
    ).toBe("Likely too heavy");
  });
});
