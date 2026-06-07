import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NOOFY_GITHUB_REPO_URL } from "../../lib/noofyLinks";
import { openExternalUrl } from "../../lib/openExternalUrl";
import { AppLayout, SidebarProvider } from "./AppLayout";

vi.mock("../../lib/openExternalUrl", () => ({
  openExternalUrl: vi.fn(),
}));

const layoutCss = readFileSync(resolve(process.cwd(), "src/styles/layout.css"), "utf8");

describe("AppLayout sidebar", () => {
  it("replaces the workspace status card with a GitHub repository card", () => {
    render(
      <SidebarProvider>
        <AppLayout activeRoute="home" onNavigate={vi.fn()}>
          <div>Dashboard</div>
        </AppLayout>
      </SidebarProvider>,
    );

    expect(screen.queryByText("AI Workspace")).not.toBeInTheDocument();
    expect(screen.getByText("Noofy on GitHub")).toBeInTheDocument();
    expect(screen.getByText("View source & updates")).toBeInTheDocument();
    expect(layoutCss).toContain(".workspace-card--github .workspace-card__avatar");
    expect(layoutCss).toContain("background: #0d1117;");
    expect(layoutCss).toContain(".engine-card + .workspace-card--github");
    expect(layoutCss).toContain("margin-top: 12px;");

    const coffeeCard = screen.getByText("Buy Me a Coffee").closest(".engine-card");
    const githubCard = screen.getByRole("button", { name: "Open Noofy on GitHub" });
    const version = document.querySelector(".sidebar__version");
    expect(coffeeCard).not.toBeNull();
    expect(version).not.toBeNull();
    expect(coffeeCard!.compareDocumentPosition(githubCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(githubCard.compareDocumentPosition(version!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

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
});
