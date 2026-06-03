import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NOOFY_GITHUB_REPO_URL } from "../../lib/noofyLinks";
import { openExternalUrl } from "../../lib/openExternalUrl";
import { AppLayout, SidebarProvider } from "./AppLayout";

vi.mock("../../lib/openExternalUrl", () => ({
  openExternalUrl: vi.fn(),
}));

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

    fireEvent.click(screen.getByRole("button", { name: "Open Noofy on GitHub" }));

    expect(openExternalUrl).toHaveBeenCalledWith(NOOFY_GITHUB_REPO_URL);
  });
});
