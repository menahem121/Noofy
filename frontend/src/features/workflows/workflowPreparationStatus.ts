import type {
  WorkflowStatusResponse,
  WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import type {
  PreparationPhase,
  PreparationPhaseStatus,
  RunPreparationDialogState,
} from "./workflowRunStateTypes";

const preparationFailureStatuses = new Set([
  "blocked_by_policy",
  "cannot_prepare_automatically",
  "failed",
  "prepared_needs_input_setup",
  "unsupported",
  "unsupported_runtime_profile",
]);
const preparationBlockedStatuses = new Set([
  "blocked_by_policy",
  "prepared_needs_input_setup",
  "unsupported",
  "unsupported_runtime_profile",
]);
const passivePreparationStatuses = new Set(["pending", "imported"]);

export function installRequiresPreparation(workflowStatus: WorkflowStatusResponse | null) {
  return (workflowStatus?.install ?? {})["requires_preparation"] !== false;
}

export function shouldShowRunPreparationDialog(workflowStatus: WorkflowStatusResponse | null) {
  const installStatus = workflowInstallStatus(workflowStatus);
  return Boolean(
    installRequiresPreparation(workflowStatus) && installStatus && installStatus !== "ready",
  );
}

export function runPreparationDialogFromStatus(workflowStatus: WorkflowStatusResponse | null): RunPreparationDialogState | null {
  if (!shouldShowRunPreparationDialog(workflowStatus)) return null;
  const install = workflowStatus?.install ?? {};
  const installStatus = workflowInstallStatus(workflowStatus);
  if (installStatus && passivePreparationStatuses.has(installStatus)) return null;
  const lastError = installString(install, "last_error");
  const userMessage = installString(install, "user_facing_message");
  const failed = Boolean(installStatus && preparationFailureStatuses.has(installStatus));
  return {
    message: failed
      ? lastError ?? userMessage ?? "Noofy could not prepare this workflow automatically."
      : userMessage ?? "Setting up this workflow before it runs.",
    detail: failed ? userMessage && userMessage !== lastError ? userMessage : null : preparationStatusDetail(installStatus),
    phases: preparationPhases(workflowStatus),
    failed,
    developerDetailsAvailable: install["developer_details_available"] === true,
  };
}

export function runPreparationDialogFromValidation(
  validation: WorkflowValidationResult,
): RunPreparationDialogState {
  const message = workflowValidationErrorMessage(validation);
  const failedPhase = validation.error_code?.startsWith("dependency_") === true
    ? "dependencies"
    : "runner";
  const phaseOrder = [
    ["models", "Check required models"],
    ["dependencies", "Install workflow extras"],
    ["stage_custom_nodes", "Set up workflow files"],
    ["runner", "Start workflow engine"],
    ["custom_registration", "Verify workflow extras"],
    ["resume", "Start run"],
  ] as const;
  const failedIndex = phaseOrder.findIndex(([id]) => id === failedPhase);
  return {
    message,
    detail: null,
    phases: phaseOrder.map(([id, label], index) => ({
      id,
      label,
      status: index < failedIndex ? "passed" : index === failedIndex ? "failed" : "pending",
    })),
    failed: true,
    developerDetailsAvailable:
      validation.developer_details?.developer_details_available === true,
  };
}

export function workflowValidationErrorMessage(validation: WorkflowValidationResult) {
  const errors = validation.errors.map((error) => error.trim()).filter(Boolean);
  if (errors.length > 0) return errors.join("\n");
  if (validation.missing_models.length > 0) return "This workflow still needs required models before it can run.";
  return "Noofy could not start this workflow.";
}

export function firstRunUserFixableError(validation: WorkflowValidationResult) {
  return validation.user_errors?.find((error) => error.severity === "user_fixable") ?? null;
}

export function workflowInstallStatus(workflowStatus: WorkflowStatusResponse | null) {
  return installString(workflowStatus?.install ?? {}, "status");
}

export function preparationPhaseStatusLabel(status: PreparationPhaseStatus) {
  switch (status) {
    case "passed":
      return "Ready";
    case "active":
      return "Working";
    case "failed":
      return "Failed";
    case "blocked":
      return "Blocked";
    default:
      return "Waiting";
  }
}

function installString(install: Record<string, unknown>, key: string) {
  const value = install[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function preparationStatusDetail(status: string | null) {
  switch (status) {
    case "resolving_models":
    case "downloading":
    case "materializing_model_view":
      return "Checking the model files this workflow needs.";
    case "resolving_dependencies":
    case "materializing_dependencies":
    case "preparing_dependency_env":
    case "resolving_runtime_profile":
      return "Installing the extra pieces this workflow needs before the first run.";
    case "materializing_custom_nodes":
    case "checking_compatibility":
      return "Setting up this workflow's extra files.";
    case "smoke_testing":
      return "Starting the workflow engine for this setup.";
    default:
      return "Noofy will start the run automatically when setup finishes.";
  }
}

function preparationPhases(workflowStatus: WorkflowStatusResponse | null): PreparationPhase[] {
  const install = workflowStatus?.install ?? {};
  const installStatus = workflowInstallStatus(workflowStatus);
  const activePhase = activePreparationPhase(
    installStatus,
    installString(install, "last_error_code"),
  );
  const failed = Boolean(installStatus && preparationFailureStatuses.has(installStatus));
  const blocked = Boolean(installStatus && preparationBlockedStatuses.has(installStatus));
  const ready = installStatus === "ready";
  const phases: PreparationPhase[] = [
    { id: "models", label: "Check required models", status: "pending" },
    { id: "dependencies", label: "Install workflow extras", status: "pending" },
    { id: "stage_custom_nodes", label: "Set up workflow files", status: "pending" },
    { id: "runner", label: "Start workflow engine", status: "pending" },
    { id: "custom_registration", label: "Verify workflow extras", status: "pending" },
    { id: "resume", label: "Start run", status: "pending" },
  ];
  const activeIndex = activePhase ? phases.findIndex((phase) => phase.id === activePhase) : -1;

  return phases.map((phase, index) => {
    const smokeStage = smokeStageStatus(install, phase.id);
    if (smokeStage === "passed") return { ...phase, status: "passed" };
    if (smokeStage === "failed" || smokeStage === "blocked") return { ...phase, status: smokeStage };
    if (ready) return { ...phase, status: phase.id === "resume" ? "active" : "passed" };
    if (phase.id === activePhase) return { ...phase, status: failed ? (blocked ? "blocked" : "failed") : "active" };
    if (activeIndex > 0 && index < activeIndex) return { ...phase, status: "passed" };
    return phase;
  });
}

function activePreparationPhase(status: string | null, errorCode: string | null) {
  if (status === "failed" && errorCode?.startsWith("dependency_")) {
    return "dependencies";
  }
  switch (status) {
    case "resolving_models":
    case "downloading":
    case "materializing_model_view":
      return "models";
    case "resolving_dependencies":
    case "materializing_dependencies":
    case "preparing_dependency_env":
    case "resolving_runtime_profile":
    case "preparing":
    case "pending":
    case "imported":
      return "dependencies";
    case "materializing_custom_nodes":
    case "checking_compatibility":
      return "stage_custom_nodes";
    case "smoke_testing":
    case "prepared":
    case "starting":
      return "runner";
    case "ready":
      return "resume";
    case "failed":
    case "cannot_prepare_automatically":
    case "blocked_by_policy":
    case "unsupported_runtime_profile":
    case "unsupported":
      return "runner";
    case "prepared_needs_input_setup":
      return "resume";
    default:
      return "dependencies";
  }
}

function smokeStageStatus(install: Record<string, unknown>, phaseId: string): PreparationPhaseStatus | null {
  const stageName =
    phaseId === "dependencies"
      ? "dependency_env"
      : phaseId === "custom_registration"
        ? "custom_node_import"
        : phaseId === "runner"
          ? "runner_health"
          : null;
  if (!stageName) return null;
  const report = install.smoke_test_report;
  if (!report || typeof report !== "object") return null;
  const stage = (report as Record<string, unknown>)[stageName];
  if (!stage || typeof stage !== "object") return null;
  const status = (stage as Record<string, unknown>).status;
  if (status === "passed" || status === "failed" || status === "blocked") return status;
  return null;
}
