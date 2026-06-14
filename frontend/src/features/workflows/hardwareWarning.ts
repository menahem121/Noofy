import type { WorkflowHardwareWarning } from "../../lib/api/noofyApi";

export interface HardwareWarningPillView {
  tone: "medium" | "high";
  label: string;
  tooltip: string;
}

export function hardwareWarningPillView(warning: WorkflowHardwareWarning): HardwareWarningPillView {
  if (warning.exceeds_machine_capacity) {
    return {
      tone: "high",
      label: "Not enough memory",
      tooltip: capacityShortfallTooltip(warning),
    };
  }
  if (warning.severity === "high") {
    return {
      tone: "high",
      label: "Likely too heavy",
      tooltip: "This workflow will probably need more memory than this machine can comfortably provide. You can still try it.",
    };
  }
  return {
    tone: "medium",
    label: "May be heavy",
    tooltip: "This workflow may run slowly or fail on this machine, depending on settings and available memory. You can still try it.",
  };
}

function capacityShortfallTooltip(warning: WorkflowHardwareWarning) {
  const estimate = warning.estimate;
  const machine = warning.machine_signal;
  if (
    estimate.estimated_peak_vram_mb != null
    && machine?.total_vram_mb != null
    && estimate.estimated_peak_vram_mb > machine.total_vram_mb
  ) {
    return `This workflow needs about ${formatMemoryMb(estimate.estimated_peak_vram_mb)} VRAM, but this machine has ${formatMemoryMb(machine.total_vram_mb)}. Lower-memory settings or a lighter workflow may be required.`;
  }
  if (
    estimate.estimated_peak_ram_mb != null
    && machine?.total_ram_mb != null
    && estimate.estimated_peak_ram_mb > machine.total_ram_mb
  ) {
    return `This workflow needs about ${formatMemoryMb(estimate.estimated_peak_ram_mb)} RAM, but this machine has ${formatMemoryMb(machine.total_ram_mb)}. Lower-memory settings or a lighter workflow may be required.`;
  }
  return "This workflow requires more memory than this machine has. Lower-memory settings or a lighter workflow may be required.";
}

export function hardwareWarningExplanation(warning: WorkflowHardwareWarning) {
  return hardwareWarningPillView(warning).tooltip;
}

export function hardwareWarningBasis(warning: WorkflowHardwareWarning) {
  const reasons = warning.reason_codes;
  if (reasons.includes("local_memory_error")) return "Local runs on this machine";
  if (reasons.includes("local_memory_error_settings_mismatch")) return "Local runs with different settings";
  if (reasons.includes("model_size_heuristic")) return "Model size estimate and current machine";
  if (reasons.includes("creator_observed_memory_hint")) return "Package observations and current machine";
  if (reasons.some((reason) => reason.startsWith("estimated_"))) return "Memory estimate and current machine";
  if (reasons.includes("temporary_low_free_memory") || reasons.includes("memory_pressure_high")) {
    return "Current available memory";
  }
  return "Best available signals";
}

export function hardwareWarningEstimateText(warning: WorkflowHardwareWarning) {
  const parts = [
    warning.estimate.estimated_peak_vram_mb != null
      ? `${formatMemoryMb(warning.estimate.estimated_peak_vram_mb)} VRAM`
      : null,
    warning.estimate.estimated_peak_ram_mb != null
      ? `${formatMemoryMb(warning.estimate.estimated_peak_ram_mb)} RAM`
      : null,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" / ") : "No exact estimate";
}

export function hardwareWarningMachineText(warning: WorkflowHardwareWarning) {
  const signal = warning.machine_signal;
  if (!signal) return "Machine memory signal unavailable";
  if (signal.free_vram_mb != null && signal.total_vram_mb != null) {
    return `${formatMemoryMb(signal.free_vram_mb)} VRAM free of ${formatMemoryMb(signal.total_vram_mb)}`;
  }
  if (signal.free_ram_mb != null && signal.total_ram_mb != null) {
    return `${formatMemoryMb(signal.free_ram_mb)} RAM free of ${formatMemoryMb(signal.total_ram_mb)}`;
  }
  return signal.memory_pressure === "unknown" ? "Memory signal is limited" : `Memory pressure is ${signal.memory_pressure}`;
}

export function hardwareWarningDeveloperDetailsText(warning: WorkflowHardwareWarning) {
  return JSON.stringify(warning.developer_details ?? {}, null, 2);
}

function formatMemoryMb(value: number) {
  if (value >= 1024) return `${(value / 1024).toFixed(1)} GB`;
  return `${Math.round(value)} MB`;
}
