import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NOOFY_GITHUB_REPO_URL } from "../../lib/noofyLinks";
import { openExternalUrl } from "../../lib/openExternalUrl";
import { AppLayout, SidebarProvider } from "./AppLayout";
import { RuntimeStatusProvider, type RuntimeHealthState } from "./RuntimeStatusProvider";

vi.mock("../../lib/openExternalUrl", () => ({
  openExternalUrl: vi.fn(),
}));

const layoutCss = readFileSync(resolve(process.cwd(), "src/styles/layout.css"), "utf8");

const offlineRuntimeState: Partial<RuntimeHealthState> = {
  backendStatus: "reachable",
  engineStatus: "offline",
  runtime: {
    mode: "managed",
    reachable: false,
    base_url: "http://127.0.0.1:8188",
    repo_dir: "/tmp/ComfyUI",
    managed_process_running: true,
    sidecar_starting: false,
    pid: 123,
    error: "ComfyUI did not answer the health check.",
    environment: { prepared: true },
    crash_count: 0,
    restart_attempt: 0,
    max_restart_attempts: 3,
    uptime_seconds: 60,
    last_crash_at: null,
  },
  refreshing: false,
  refreshError: null,
  lastCheckedAt: Date.now(),
  consecutiveSilentFailures: 0,
  hasKnownState: true,
};

const backendOfflineRuntimeState: Partial<RuntimeHealthState> = {
  ...offlineRuntimeState,
  backendStatus: "unreachable",
  engineStatus: "offline",
  refreshError: "The local service did not answer in time.",
};

describe("AppLayout sidebar", () => {
  it("keeps the support card above the GitHub repository card", () => {
    render(
      <SidebarProvider>
        <AppLayout activeRoute="home" onNavigate={vi.fn()}>
          <div>Dashboard</div>
        </AppLayout>
      </SidebarProvider>,
    );

    expect(screen.queryByText("AI Workspace")).not.toBeInTheDocument();
    expect(screen.getByText("Buy Me a Coffee")).toBeInTheDocument();
    expect(screen.getByText("Help me build Noofy (And maybe buy a computer that can run it lol)")).toBeInTheDocument();
    expect(screen.getByText("Noofy on GitHub")).toBeInTheDocument();
    expect(screen.getByText("View source & updates")).toBeInTheDocument();
    expect(layoutCss).toContain(".workspace-card--github .workspace-card__avatar");
    expect(layoutCss).toContain("background: #0d1117;");
    expect(layoutCss).toContain(".support-card + .workspace-card--github");
    expect(layoutCss).toContain("margin-top: 12px;");

    const supportCard = screen.getByText("Buy Me a Coffee").closest(".support-card");
    const githubCard = screen.getByRole("button", { name: "Open Noofy on GitHub" });
    const version = document.querySelector(".sidebar__version");
    expect(supportCard).not.toBeNull();
    expect(version).not.toBeNull();
    expect(supportCard!.compareDocumentPosition(githubCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(githubCard.compareDocumentPosition(version!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Support Noofy" }));
    expect(openExternalUrl).toHaveBeenCalledWith("https://example.com/buy-me-a-coffee");

    fireEvent.click(screen.getByRole("button", { name: "Open Noofy on GitHub" }));

    expect(openExternalUrl).toHaveBeenCalledWith(NOOFY_GITHUB_REPO_URL);
  });

  it("places active workflow run controls inside the resource monitor before CPU", () => {
    const onCancelRemaining = vi.fn();

    render(
      <SidebarProvider>
        <AppLayout
          activeRoute="workflows"
          onNavigate={vi.fn()}
          progress={{
            percent: 37,
            remainingCount: 3,
            onCancelRemaining,
            cancelRemainingTitle: "Cancel current run and all queued runs for this workflow",
          }}
        >
          <div>Dashboard</div>
        </AppLayout>
      </SidebarProvider>,
    );

    const resourceMonitor = screen.getByLabelText("Resource monitor");
    const runControls = screen.getByLabelText("Active workflow runs");
    const cpuLabel = screen.getByText("CPU");

    expect(resourceMonitor).toContainElement(runControls);
    expect(runControls.compareDocumentPosition(cpuLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTitle("3 runs remaining")).toHaveClass("resource-monitor__run-count");
    expect(screen.getByRole("button", { name: "Cancel current run and all queued runs for this workflow" })).toHaveClass(
      "resource-monitor__run-stop",
    );
    expect(document.querySelector(".topbar-progress__remaining")).not.toBeInTheDocument();
    expect(document.querySelector(".topbar-progress__stop")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel current run and all queued runs for this workflow" }));
    expect(onCancelRemaining).toHaveBeenCalledTimes(1);
  });

  it("keeps the status pill on Working while workflow progress is active", () => {
    render(
      <RuntimeStatusProvider initialRuntimeState={offlineRuntimeState} skipInitialRefresh>
        <SidebarProvider>
          <AppLayout
            activeRoute="workflows"
            onNavigate={vi.fn()}
            progress={{ percent: 37 }}
          >
            <div>Dashboard</div>
          </AppLayout>
        </SidebarProvider>
      </RuntimeStatusProvider>,
    );

    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.queryByText("ComfyUI offline")).not.toBeInTheDocument();
  });

  it("keeps transient backend misses from replacing active workflow status", () => {
    render(
      <RuntimeStatusProvider initialRuntimeState={backendOfflineRuntimeState} skipInitialRefresh>
        <SidebarProvider>
          <AppLayout activeRoute="workflows" onNavigate={vi.fn()} progress={{ percent: 37 }}>
            <div>Dashboard</div>
          </AppLayout>
        </SidebarProvider>
      </RuntimeStatusProvider>,
    );

    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.queryByText("Offline")).not.toBeInTheDocument();
  });

});
