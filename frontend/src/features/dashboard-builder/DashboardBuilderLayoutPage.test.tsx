import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardBuilderLayoutPage } from "./DashboardBuilderLayoutPage";
import { dashboardDraftKey, type DashboardSchema } from "./dashboardBuilderContent";

const builderLayoutCss = readFileSync(resolve(process.cwd(), "src/styles/dashboard-builder.css"), "utf8");
const canvasCss = readFileSync(resolve(process.cwd(), "src/styles/canvas.css"), "utf8");
const componentsCss = readFileSync(resolve(process.cwd(), "src/styles/components.css"), "utf8");

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
  groups: [],
  widgets: [
    {
      id: "ctrl-prompt",
      valueId: "node-6-text",
      binding: { nodeId: "6", inputName: "text" },
      widgetType: "textarea",
      title: "Prompt",
      description: "",
      defaultValue: "a lake",
      layout: { x: 0, y: 0, w: 16, h: 6 },
    },
  ],
};

const groupedPlacedSchema: DashboardSchema = {
  version: 1,
  workflowId: "wf-1",
  workflowName: "Workflow",
  layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
  groups: [
    {
      id: "main-group",
      title: "Main group",
      description: "Grouped controls",
      widgetIds: ["ctrl-prompt", "ctrl-steps"],
      layout: { x: 0, y: 0, w: 16, h: 10 },
    },
  ],
  widgets: [
    {
      id: "ctrl-prompt",
      valueId: "node-6-text",
      binding: { nodeId: "6", inputName: "text" },
      widgetType: "textarea",
      title: "Prompt",
      description: "",
      defaultValue: "a lake",
    },
    {
      id: "ctrl-steps",
      valueId: "node-3-steps",
      binding: { nodeId: "3", inputName: "steps" },
      widgetType: "int_field",
      title: "Steps",
      description: "",
      defaultValue: 20,
    },
  ],
};

const sliderGroupSchema: DashboardSchema = {
  version: 1,
  workflowId: "wf-1",
  workflowName: "Workflow",
  layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
  groups: [
    {
      id: "size-group",
      title: "Width + Height",
      description: "",
      widgetIds: ["ctrl-width", "ctrl-height"],
      layout: { x: 0, y: 0, w: 16, h: 18 },
    },
  ],
  widgets: [
    {
      id: "ctrl-width",
      valueId: "node-4-width",
      binding: { nodeId: "4", inputName: "width" },
      widgetType: "slider",
      title: "Width",
      description: "Output width in pixels.",
      defaultValue: 640,
      min: 64,
      max: 2048,
      step: 64,
    },
    {
      id: "ctrl-height",
      valueId: "node-4-height",
      binding: { nodeId: "4", inputName: "height" },
      widgetType: "slider",
      title: "Height",
      description: "Output height in pixels.",
      defaultValue: 640,
      min: 64,
      max: 2048,
      step: 64,
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

  it("autosaves layout changes without requiring the draft button", async () => {
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

    await screen.findByRole("textbox");
    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-1")) ?? "{}");
      expect(stored).toMatchObject({
        workflowId: "wf-1",
        status: "draft",
        widgets: [expect.objectContaining({ id: "ctrl-prompt" })],
      });
    });
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
    // The clear validation reason is shown, not just hidden in a tooltip.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("invalid payload");
    expect(alert).toHaveTextContent("Your local draft was kept.");
    expect(onSaveComplete).not.toHaveBeenCalled();
    const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-1")) ?? "{}");
    expect(stored).toMatchObject({ workflowId: "wf-1", status: "draft" });
  });

  it("clears the local draft and opens the workflow after backend save succeeds", async () => {
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

    await waitFor(() => expect(onSaveComplete).toHaveBeenCalledWith("wf-1"));
    expect(onSaveComplete).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(dashboardDraftKey("wf-1"))).toBeNull();
    expect(screen.queryByRole("button", { name: /open workflow/i })).not.toBeInTheDocument();
  });

  it("does not keep the previous workflow canvas when the workflow id changes", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { rerender } = render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow One"
        initialSchema={placedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByRole("textbox")).toBeInTheDocument();

    rerender(
      <DashboardBuilderLayoutPage
        workflowId="wf-2"
        workflowName="Workflow Two"
        initialSchema={placedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resize prompt/i })).not.toBeInTheDocument();
    expect(screen.getByText("Start building your dashboard")).toBeInTheDocument();
  });

  it("removes an unplaced widget from viewable controls and preserves its saved default", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const onBackToWidgets = vi.fn();
    const { layout: _layout, ...unplacedPrompt } = placedSchema.widgets[0];
    const schema: DashboardSchema = {
      ...placedSchema,
      widgets: [{ ...unplacedPrompt, defaultPinned: true }],
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={schema}
        onBackToWidgets={onBackToWidgets}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const removeButton = await screen.findByRole("button", { name: /remove prompt from viewable widgets/i });
    expect(removeButton.closest("article")?.firstElementChild).toBe(removeButton);
    fireEvent.click(removeButton);

    expect(screen.queryByRole("button", { name: /remove prompt from viewable widgets/i })).not.toBeInTheDocument();
    expect(screen.getByText("No viewable widgets")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /back to widgets/i }));

    const nextSchema = onBackToWidgets.mock.calls[0][0] as DashboardSchema;
    expect(nextSchema.widgets).toEqual([]);
    expect(nextSchema.hiddenWidgets).toEqual([
      expect.objectContaining({
        id: "ctrl-prompt",
        defaultPinned: true,
        binding: { nodeId: "6", inputName: "text" },
      }),
    ]);
  });

  it("removes every member of an unplaced group from viewable controls", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const onBackToWidgets = vi.fn();
    const schema: DashboardSchema = {
      ...groupedPlacedSchema,
      groups: groupedPlacedSchema.groups.map(({ layout: _layout, ...group }) => group),
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={schema}
        onBackToWidgets={onBackToWidgets}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /remove main group from viewable widgets/i }));
    fireEvent.click(screen.getByRole("button", { name: /back to widgets/i }));

    const nextSchema = onBackToWidgets.mock.calls[0][0] as DashboardSchema;
    expect(nextSchema.widgets).toEqual([]);
    expect(nextSchema.groups).toEqual([]);
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

    expect(await screen.findByRole("button", { name: /resize prompt from top-left/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resize prompt from top-right/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resize prompt from bottom-left/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resize prompt from bottom-right/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /resize prompt from/i })).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /resize prompt width/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resize prompt height/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^move prompt$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Compact" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Media Large" })).not.toBeInTheDocument();
  });

  it("marks widgets compact only when their layout is below the widget default", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const schema: DashboardSchema = {
      ...placedSchema,
      widgets: [
        {
          ...placedSchema.widgets[0],
          id: "default-prompt",
          title: "Default prompt",
          layout: { x: 0, y: 0, w: 8, h: 6 },
        },
        {
          ...placedSchema.widgets[0],
          id: "compact-prompt",
          title: "Compact prompt",
          layout: { x: 8, y: 0, w: 5, h: 4 },
        },
      ],
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={schema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect((await screen.findByText("Default prompt")).closest("article")).not.toHaveClass("layout-canvas-widget--compact");
    expect(screen.getByText("Compact prompt").closest("article")).toHaveClass("layout-canvas-widget--compact");
  });

  it("resizes a widget down to the current Noofy minimum and serializes that minimum", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-1/dashboard")) {
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

    const resizeHandle = await screen.findByRole("button", { name: /resize prompt from bottom-right/i });
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

    dispatchPointer(resizeHandle, "pointerdown", { clientX: 600, clientY: 192 });
    dispatchPointer(window, "pointermove", { clientX: 0, clientY: 0 });
    dispatchPointer(window, "pointerup", { clientX: 0, clientY: 0 });

    const promptCell = screen.getByRole("textbox").closest("article");
    expect(promptCell).toHaveStyle({ width: "15.625%", minHeight: "128px" });
    expect(promptCell).toHaveClass("layout-canvas-widget--compact");

    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      const body = JSON.parse((putCall?.[1] as RequestInit).body as string);
      expect(body.dashboard.sections[0].controls[0].layout).toEqual({
        x: 0,
        y: 0,
        w: 5,
        h: 4,
        min_w: 5,
        min_h: 4,
      });
    });
  });

  it("preserves grouped widget height during width-only resize", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={sliderGroupSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const resizeHandle = await screen.findByRole("button", { name: /resize width \+ height from bottom-right/i });
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

    dispatchPointer(resizeHandle, "pointerdown", { clientX: 600, clientY: 576 });
    dispatchPointer(window, "pointermove", { clientX: 375, clientY: 576 });

    const groupCell = screen.getByText("Width + Height").closest("article")!;
    await waitFor(() => {
      expect(groupCell).toHaveStyle({ width: "31.25%", height: "576px" });
    });
    expect(groupCell).not.toHaveClass("layout-canvas-widget--compact");
    dispatchPointer(window, "pointerup", { clientX: 375, clientY: 576 });
  });

  it("treats a group as one canvas item instead of child widget blocks", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={groupedPlacedSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByRole("button", { name: /resize main group from top-left/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resize prompt from top-left/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resize steps from top-left/i })).not.toBeInTheDocument();
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

  it("keeps canvas widget separation visual without changing tile geometry", () => {
    expect(builderLayoutCss).toMatch(/--layout-widget-visual-gap:\s*var\(--layout-grid-gap, 0px\);/);
    expect(builderLayoutCss).toMatch(/--layout-widget-visual-inset:\s*calc\(var\(--layout-widget-visual-gap\) \/ 2\);/);
    expect(builderLayoutCss).toMatch(/\.layout-canvas-widget\s*{[^}]*padding:\s*calc\(15px \+ var\(--layout-widget-visual-inset, 0px\)\);/);
    expect(builderLayoutCss).toMatch(/\.layout-canvas-widget::before\s*{[^}]*inset:\s*var\(--layout-widget-visual-inset, 0px\);/);
    expect(canvasCss).toMatch(/\.layout-canvas-resize-handles\s*{[^}]*inset:\s*var\(--layout-widget-visual-inset, 0px\);/);
  });

  it("lets multiline text widgets fill the resized canvas widget height", () => {
    expect(builderLayoutCss).toMatch(/\.layout-canvas-widget__preview-surface\s*{[^}]*flex:\s*1;/);
    expect(builderLayoutCss).toMatch(/\.layout-preview-input--textarea\s*{[^}]*flex:\s*1;/);
    expect(builderLayoutCss).toMatch(/\.layout-preview-input--textarea\s*{[^}]*overflow:\s*auto;/);
    expect(canvasCss).toMatch(/\.canvas-widget-textarea\s*{[^}]*flex:\s*1 1 0;/);
    expect(canvasCss).toMatch(/\.canvas-widget-textarea\s*{[^}]*overflow:\s*auto;/);
  });

  it("lets media placeholder frames fill resized preview widgets", () => {
    expect(builderLayoutCss).toMatch(
      /\.layout-canvas-widget__preview-surface > :is\(\.layout-preview-image-input, \.layout-preview-output\)\s*{[^}]*flex:\s*1 1 0;/,
    );
    expect(builderLayoutCss).toMatch(
      /\.layout-canvas-widget__preview-surface > :is\(\.layout-preview-image-input, \.layout-preview-output\)\s*{[^}]*width:\s*100%;/,
    );
    expect(builderLayoutCss).toMatch(
      /\.layout-canvas-widget__preview-surface > :is\(\.layout-preview-image-input, \.layout-preview-output\)\s*{[^}]*min-height:\s*0;/,
    );
  });

  it("lets 3D result previews fill resized canvas widgets", () => {
    expect(canvasCss).toMatch(
      /\.widget-canvas-cell__content > \.widget-output-three-d\s*{[^}]*flex:\s*1 1 0;[^}]*min-height:\s*0;[^}]*grid-auto-rows:\s*minmax\(280px, 1fr\);/,
    );
    expect(canvasCss).toMatch(
      /\.widget-canvas-cell__content > \.widget-output-three-d \.three-d-viewer\s*{[^}]*height:\s*100%;[^}]*grid-template-rows:\s*minmax\(240px, 1fr\) auto auto;/,
    );
    expect(canvasCss).toMatch(
      /\.layout-canvas-widget--compact \.widget-canvas-cell__content > \.widget-output-three-d \.three-d-viewer\s*{[^}]*grid-template-rows:\s*minmax\(0, 1fr\) auto auto;/,
    );
  });

  it("keeps compact widget content contained with scrolling and flexible minimum heights", () => {
    expect(builderLayoutCss).toMatch(/\.layout-canvas-widget--compact \.layout-canvas-widget__preview-surface\s*{[^}]*overflow:\s*auto;/);
    expect(builderLayoutCss).toMatch(/\.layout-canvas-widget--compact \.layout-preview-input--textarea,[^}]*min-height:\s*0;/);
    expect(canvasCss).toMatch(/\.layout-canvas-widget--compact \.widget-canvas-cell__content\s*{[^}]*overflow:\s*auto;/);
    expect(canvasCss).toMatch(/\.layout-canvas-widget--compact \.widget-output-placeholder\s*{[^}]*min-height:\s*0;/);
    expect(componentsCss).toMatch(/\.layout-canvas-widget--compact :where\([^}]*\.dashboard-media-source[^}]*\)\s*{[^}]*min-height:\s*0;/);
    expect(componentsCss).toMatch(/\.layout-canvas-widget--compact :where\([^}]*\.three-d-viewer[^}]*\)\s*{[^}]*min-height:\s*0;/);
    expect(canvasCss).toMatch(/\.layout-canvas-widget--compact \.canvas-widget-group__description\s*{[^}]*display:\s*none;/);
  });

  it("moves a loaded below-minimum widget without changing its dimensions", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-1/dashboard")) {
        expect(init?.method).toBe("PUT");
        return Promise.resolve(jsonResponse({ workflow_id: "wf-1", status: "configured", valid: true }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const compactSchema: DashboardSchema = {
      ...placedSchema,
      widgets: placedSchema.widgets.map((widget) => ({
        ...widget,
        layout: { x: 0, y: 0, w: 3, h: 2, minW: 99, minH: 99 },
      })),
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={compactSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

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

    const promptCell = (await screen.findByRole("textbox")).closest("article")!;
    dispatchPointer(promptCell, "pointerdown", { clientX: 60, clientY: 32 });
    dispatchPointer(window, "pointermove", { clientX: 60, clientY: 96 });
    dispatchPointer(window, "pointerup", { clientX: 60, clientY: 96 });

    expect(promptCell).toHaveStyle({ top: "64px", width: "9.375%", height: "64px" });
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      const body = JSON.parse((putCall?.[1] as RequestInit).body as string);
      expect(body.dashboard.sections[0].controls[0].layout).toEqual({
        x: 0,
        y: 2,
        w: 3,
        h: 2,
        min_w: 5,
        min_h: 4,
      });
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

    await screen.findByRole("button", { name: /resize prompt from bottom-right/i });
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
      expect(body.dashboard.sections[0].controls[0].layout).toEqual({
        x: 0,
        y: 4,
        w: 16,
        h: 6,
        min_w: 5,
        min_h: 4,
      });
    });
  });

  it("lets placed widgets drag through occupied cells and lands them in a free grid cell", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const wallSchema: DashboardSchema = {
      ...placedSchema,
      widgets: [
        placedSchema.widgets[0],
        {
          ...placedSchema.widgets[0],
          id: "result-a",
          valueId: "result-a",
          binding: { nodeId: "9", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result A",
          layout: { x: 16, y: 0, w: 16, h: 8 },
        },
        {
          ...placedSchema.widgets[0],
          id: "result-b",
          valueId: "result-b",
          binding: { nodeId: "10", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result B",
          layout: { x: 0, y: 8, w: 16, h: 8 },
        },
      ],
    };

    render(
      <DashboardBuilderLayoutPage
        workflowId="wf-1"
        workflowName="Workflow"
        initialSchema={wallSchema}
        onBackToWidgets={vi.fn()}
        onSaveComplete={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("button", { name: /resize prompt from bottom-right/i });
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
    dispatchPointer(promptCell, "pointerdown", { clientX: 300, clientY: 96 });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 224 });

    await waitFor(() => {
      expect(promptCell).toHaveStyle({ top: "128px" });
    });
    const dropPreview = document.querySelector(".layout-canvas-widget--drop-preview") as HTMLElement;
    expect(dropPreview).toBeInTheDocument();
    expect(dropPreview).toHaveStyle({ left: "0%", top: "64px" });

    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 224 });

    expect(promptCell).toHaveStyle({ left: "0%", top: "64px" });
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

    const trayWidget = screen.getByText("Prompt").closest("article")!;
    dispatchPointer(trayWidget, "pointerdown", { clientX: 120, clientY: 120 });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 96 });
    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 96 });

    const textbox = screen.getByRole("textbox");
    const promptCell = textbox.closest("article")!;
    expect(promptCell).toHaveStyle({ top: "0px" });
    expect(textbox.closest(".layout-canvas-widget__preview-surface")).toBeInTheDocument();
    Object.defineProperty(promptCell, "setPointerCapture", {
      configurable: true,
      value: vi.fn(() => {
        throw new Error("capture unavailable");
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

  it("moves directly to the snapped grid cell during fast placed-widget drags", async () => {
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

    await screen.findByRole("button", { name: /resize prompt from bottom-right/i });
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

    await waitFor(() => {
      expect(promptCell).toHaveStyle({ left: "18.75%" });
    });
    dispatchPointer(window, "pointerup", { clientX: 325, clientY: 160 });
  });
});
