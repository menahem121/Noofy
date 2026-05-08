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

function dispatchPointer(target: Window | Node, type: string, init: { pointerId?: number; clientX: number; clientY: number }) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    pointerId: { value: init.pointerId ?? 1 },
    clientX: { value: init.clientX },
    clientY: { value: init.clientY },
  });
  fireEvent(target, event);
}

function dispatchDrag(target: Node, type: string, init: { clientX: number; clientY: number; dataTransfer: DataTransfer }) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    clientX: { value: init.clientX },
    clientY: { value: init.clientY },
    dataTransfer: { value: init.dataTransfer },
  });
  fireEvent(target, event);
}

function createDataTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    dropEffect: "none",
    effectAllowed: "all",
    getData: (format: string) => store.get(format) ?? "",
    setData: (format: string, data: string) => {
      store.set(format, data);
    },
    clearData: (format?: string) => {
      if (format) store.delete(format);
      else store.clear();
    },
  } as DataTransfer;
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
  layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
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
      layout: { x: 0, y: 0, w: 16, h: 6 },
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
    expect(screen.queryByRole("button", { name: /^move prompt$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Compact" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Media Large" })).not.toBeInTheDocument();
  });

  it("renders the layout builder canvas in the full workspace shell", async () => {
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

    expect(await screen.findByRole("main", { name: /dashboard layout canvas/i })).toHaveClass("layout-canvas");
    expect(document.querySelector(".main-workspace--builder-layout")).toBeInTheDocument();
    expect(document.querySelector(".workspace-content--builder-layout")).toBeInTheDocument();
    expect(document.querySelector(".layout-canvas__surface")).toHaveStyle({
      "--layout-surface-min-height": "768px",
    });
  });

  it("moves placed widgets by dragging the card body on snapped grid cells", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-1/dashboard")) {
        expect(init?.method).toBe("PUT");
        return Promise.resolve(jsonResponse({ workflow_id: "wf-1", status: "configured", valid: true }));
      }
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

    await screen.findByRole("button", { name: /^resize prompt$/i });
    const canvasSurface = document.querySelector(".layout-canvas__surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const textbox = screen.getByRole("textbox");
    const promptCell = textbox.closest("article")!;
    dispatchPointer(textbox, "pointerdown", { clientX: 300, clientY: 96 });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 160 });

    expect(promptCell).toHaveClass("layout-canvas-widget--moving");
    expect(promptCell).not.toHaveClass("layout-canvas-widget--preview");
    await waitFor(() => {
      expect(promptCell).toHaveStyle({ top: "64px" });
    });
    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 160 });

    expect(promptCell).not.toHaveClass("layout-canvas-widget--moving");
    expect(promptCell).toHaveStyle({ top: "64px" });

    dispatchPointer(screen.getByRole("textbox"), "pointerdown", { clientX: 300, clientY: 160 });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 224 });
    await waitFor(() => {
      expect(promptCell).toHaveStyle({ top: "128px" });
    });
    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 224 });

    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      expect(putCall).toBeDefined();
      const body = JSON.parse((putCall![1] as RequestInit).body as string);
      expect(body.dashboard.sections[0].controls[0].layout).toEqual({ x: 0, y: 4, w: 16, h: 6 });
    });
  });

  it("moves a newly dropped widget on the first follow-up drag", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const unplacedSchema: DashboardSchema = {
      ...placedSchema,
      widgets: placedSchema.widgets.map((widget) => {
        const { layout: _layout, ...unplacedWidget } = widget;
        return unplacedWidget;
      }),
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={unplacedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("main", { name: /dashboard layout canvas/i });
    const canvasSurface = document.querySelector(".layout-canvas__surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const dataTransfer = createDataTransfer();
    const trayWidget = screen.getByText("Prompt").closest("article")!;
    dispatchDrag(trayWidget, "dragstart", { clientX: 120, clientY: 120, dataTransfer });
    dispatchDrag(canvasSurface, "dragover", { clientX: 300, clientY: 96, dataTransfer });
    dispatchDrag(canvasSurface, "drop", { clientX: 300, clientY: 96, dataTransfer });

    const textbox = screen.getByRole("textbox");
    const promptCell = textbox.closest("article")!;
    expect(promptCell).toHaveStyle({ top: "0px" });
    expect(textbox.closest(".layout-canvas-widget__preview-surface")).toBeInTheDocument();
    Object.defineProperty(promptCell, "setPointerCapture", {
      configurable: true,
      value: vi.fn(() => {
        throw new Error("capture unavailable during native drag transition");
      }),
    });

    dispatchPointer(promptCell, "pointerdown", { clientX: 300, clientY: 96 });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 160 });

    expect(promptCell).toHaveClass("layout-canvas-widget--moving");
    await waitFor(() => {
      expect(promptCell).toHaveStyle({ top: "64px" });
    });
    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 160 });

    expect(promptCell).not.toHaveClass("layout-canvas-widget--moving");
    expect(promptCell).toHaveStyle({ top: "64px" });
  });

  it("steps through intermediate grid cells during fast placed-widget drags", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const singleWidgetSchema = {
      ...placedSchema,
      widgets: [
        {
          ...placedSchema.widgets[0],
          layout: { x: 4, y: 2, w: 8, h: 6 },
        },
      ],
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={singleWidgetSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("button", { name: /^resize prompt$/i });
    const canvasSurface = document.querySelector(".layout-canvas__surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const promptCell = screen.getByRole("textbox").closest("article")!;
    dispatchPointer(promptCell, "pointerdown", { clientX: 250, clientY: 160 });
    dispatchPointer(window, "pointermove", { clientX: 325, clientY: 160 });

    expect(promptCell).toHaveStyle({ left: "15.625%" });
    await waitFor(() => {
      expect(promptCell).toHaveStyle({ left: "18.75%" });
    });
    dispatchPointer(window, "pointerup", { clientX: 325, clientY: 160 });
  });
});
