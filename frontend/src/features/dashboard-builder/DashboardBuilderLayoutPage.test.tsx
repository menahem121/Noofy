import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardBuilderLayoutPage } from "./DashboardBuilderLayoutPage";
import { dashboardDraftKey, type DashboardSchema } from "./dashboardBuilderContent";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const readyRuntime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  pid: 123,
  error: null,
  environment: { prepared: true },
};

const placedSchema: DashboardSchema = {
  version: 1,
  workflowId: "wf-1",
  workflowName: "Workflow",
  layout: { gridColumns: 12, rowHeight: 64, gridGap: 14, responsive: true },
  widgets: [
    {
      id: "ctrl-prompt",
      valueId: "node-6-text",
      binding: { nodeId: "6", inputName: "text" },
      widgetType: "textarea",
      title: "Prompt",
      description: "",
      orientation: "vertical",
      group: "simple",
      defaultValue: "a lake",
      layout: { x: 0, y: 0, w: 6, h: 3 },
    },
  ],
};

describe("DashboardBuilderLayoutPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
  });

  it("keeps a local draft and does not navigate when dashboard save fails", async () => {
    const onSaveComplete = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-1/dashboard")) {
        expect(init?.method).toBe("PUT");
        return Promise.resolve(jsonResponse({ detail: "invalid payload" }, 400));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={placedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={onSaveComplete}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /save dashboard/i }));

    expect(await screen.findByText("Save failed. Draft kept.")).toBeInTheDocument();
    expect(onSaveComplete).not.toHaveBeenCalled();
    const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-1")) ?? "{}");
    expect(stored).toMatchObject({ workflowId: "wf-1", status: "draft" });
  });

  it("clears the local draft and navigates only after backend save succeeds", async () => {
    const onSaveComplete = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-1/dashboard")) {
        return Promise.resolve(jsonResponse({ workflow_id: "wf-1", status: "configured", valid: true }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    window.localStorage.setItem(dashboardDraftKey("wf-1"), JSON.stringify({ workflowId: "wf-1", status: "draft" }));

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={placedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={onSaveComplete}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /save dashboard/i }));
    await screen.findByText("Dashboard saved");

    expect(window.localStorage.getItem(dashboardDraftKey("wf-1"))).toBeNull();
    await waitFor(() => expect(onSaveComplete).toHaveBeenCalledWith("wf-1"));
  });

  it("uses resize handles instead of bottom size preset buttons", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={placedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByRole("button", { name: /^resize prompt$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Compact" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Media Large" })).not.toBeInTheDocument();
  });
});
