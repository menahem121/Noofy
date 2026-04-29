import type { RuntimeStatus } from "../../lib/api/noofyApi";
import type { AppStatusView } from "./AppLayout";

interface RuntimeState {
  loading: boolean;
  runtime: RuntimeStatus | null;
}

export function runtimeStatusCopy(state: RuntimeState): AppStatusView {
  if (state.loading) {
    return {
      label: "Checking backend",
      description: "Looking for the local app service",
      tone: "info",
      loading: true,
    };
  }

  if (!state.runtime) {
    return {
      label: "Backend offline",
      description: "Start the Noofy backend to load live workflows",
      tone: "error",
    };
  }

  if (state.runtime.reachable) {
    return {
      label: "Engine ready",
      description: "Local workflow engine is reachable",
      tone: "success",
    };
  }

  if (state.runtime.managed_process_running) {
    return {
      label: "Engine starting",
      description: "The local engine process is still warming up",
      tone: "info",
      loading: true,
    };
  }

  return {
    label: "Engine offline",
    description: "Open settings to start or repair the local engine",
    tone: "warning",
  };
}
