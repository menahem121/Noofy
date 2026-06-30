import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EngineInstallProgressModal, engineInstallProgress } from "./EngineInstallProgressModal";
import { RuntimeStatusProvider, type RuntimeHealthState } from "./RuntimeStatusProvider";

const installingRuntimeState: Partial<RuntimeHealthState> = {
  backendStatus: "reachable",
  engineStatus: "installing",
  runtime: {
    mode: "managed",
    reachable: false,
    base_url: "http://127.0.0.1:8188",
    repo_dir: "/tmp/ComfyUI",
    managed_process_running: false,
    sidecar_starting: false,
    environment_bootstrap_running: true,
    environment_bootstrap_label: "Install PyTorch runtime (mps)",
    pid: null,
    error: null,
    environment: null,
    crash_count: 0,
    restart_attempt: 0,
    max_restart_attempts: 3,
    uptime_seconds: null,
    last_crash_at: null,
  },
  refreshing: false,
  refreshError: null,
  lastCheckedAt: Date.now(),
  consecutiveSilentFailures: 0,
  hasKnownState: true,
};

describe("EngineInstallProgressModal", () => {
  it("shows a non-dismissible progress dialog while engine bootstrap is running", () => {
    render(
      <RuntimeStatusProvider initialRuntimeState={installingRuntimeState} skipInitialRefresh>
        <EngineInstallProgressModal />
      </RuntimeStatusProvider>,
    );

    expect(screen.getByRole("dialog", { name: "Installing ComfyUI engine" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Engine installation progress" })).toHaveAttribute(
      "aria-valuetext",
      "Install PyTorch runtime (mps)",
    );
    expect(screen.getByText("Install PyTorch runtime (mps)")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("uses fallback copy when the backend has not published a stage label yet", () => {
    const runtime = {
      ...installingRuntimeState.runtime!,
      environment_bootstrap_label: "   ",
    };

    expect(engineInstallProgress(runtime)?.label).toBe("Preparing ComfyUI engine dependencies");
  });
});
