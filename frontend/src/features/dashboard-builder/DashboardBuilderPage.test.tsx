import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardBuilderPage } from "./DashboardBuilderPage";
import type { DashboardSchema } from "./dashboardBuilderContent";

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

const selectSchema: DashboardSchema = {
  version: 1,
  workflowId: "imported_text_to_image_demo",
  workflowName: "Text to Image Demo",
  layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
  widgets: [
    {
      id: "ctrl-node-3-sampler_name",
      valueId: "node-3-sampler_name",
      binding: { nodeId: "3", inputName: "sampler_name" },
      widgetType: "select",
      title: "Sampler",
      description: "",
      orientation: "vertical",
      group: "advanced",
      defaultValue: "euler",
      options: ["euler", "heun", "dpm_2"],
      layout: { x: 0, y: 0, w: 10, h: 3 },
    },
  ],
};

const invalidSliderSchema: DashboardSchema = {
  version: 1,
  workflowId: "imported_text_to_image_demo",
  workflowName: "Text to Image Demo",
  layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
  widgets: [
    {
      id: "ctrl-node-3-denoise",
      valueId: "node-3-denoise",
      binding: { nodeId: "3", inputName: "denoise" },
      widgetType: "slider",
      title: "Transformation level",
      description: "",
      orientation: "vertical",
      group: "advanced",
      defaultValue: 0.3,
      min: 0,
      max: 1,
      step: 0.25,
      layout: { x: 0, y: 0, w: 10, h: 4 },
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function bindableInputsResponse(nodeType: string, currentValue: string) {
  return {
    nodes: [
      {
        node_id: "6",
        node_type: nodeType,
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "text",
            current_value: currentValue,
            kind: "string",
            suggested_widget_type: "textarea",
            widget_types: ["textarea", "string_field"],
          },
        ],
      },
    ],
  };
}

describe("DashboardBuilderPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
  });

  it("edits dropdown options as individual grid fields", async () => {
    const onContinue = vi.fn();

    render(
      <DashboardBuilderPage
        initialSchema={selectSchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const firstOption = await screen.findByRole("textbox", { name: /dropdown option 1/i });
    const secondOption = screen.getByRole("textbox", { name: /dropdown option 2/i });
    const thirdOption = screen.getByRole("textbox", { name: /dropdown option 3/i });

    expect(document.querySelector(".builder-options-grid")).toBeInTheDocument();
    expect(screen.queryByText(/one per line/i)).not.toBeInTheDocument();
    expect(firstOption).toHaveValue("euler");
    expect(secondOption).toHaveValue("heun");
    expect(thirdOption).toHaveValue("dpm_2");

    fireEvent.change(secondOption, { target: { value: "dpmpp_2m" } });
    fireEvent.click(screen.getByRole("button", { name: /add option/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /dropdown option 4/i }), {
      target: { value: "uni_pc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /remove option 1/i }));
    fireEvent.click(screen.getByRole("button", { name: /move option 3 up/i }));

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: [
          expect.objectContaining({
            options: ["dpmpp_2m", "uni_pc", "dpm_2"],
          }),
        ],
      }),
    );
  });

  it("validates slider range, step size, and default value inline", async () => {
    const onContinue = vi.fn();

    render(
      <DashboardBuilderPage
        initialSchema={invalidSliderSchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByText("Default value must match the step size from the minimum value.")).toBeInTheDocument();
    expect(screen.getByText("Controls how much the value changes each time the slider moves.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /save as draft/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/default value/i), { target: { value: "0.5" } });

    await waitFor(() => {
      expect(screen.queryByText("Default value must match the step size from the minimum value.")).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: [expect.objectContaining({ defaultValue: 0.5, min: 0, max: 1, step: 0.25 })],
      }),
    );
  });

  it("hides the previous workflow while builder data for the next workflow is loading", async () => {
    const workflowA = deferred<Response>();
    const workflowB = deferred<Response>();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-a/bindable-inputs")) return workflowA.promise;
      if (url.endsWith("/api/workflows/wf-b/bindable-inputs")) return workflowB.promise;
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { rerender } = render(
      <DashboardBuilderPage
        workflowId="wf-a"
        workflowName="Workflow A"
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    workflowA.resolve(jsonResponse(bindableInputsResponse("AlphaNode", "alpha")));
    await waitFor(() => expect(screen.getAllByText("AlphaNode").length).toBeGreaterThan(0));

    rerender(
      <DashboardBuilderPage
        workflowId="wf-b"
        workflowName="Workflow B"
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByText("Loading dashboard builder")).toBeInTheDocument();
    expect(screen.queryAllByText("AlphaNode")).toHaveLength(0);

    workflowB.resolve(jsonResponse(bindableInputsResponse("BetaNode", "beta")));
    await waitFor(() => expect(screen.getAllByText("BetaNode").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("AlphaNode")).toHaveLength(0);
  });

  it("ignores a bindable-input response after navigating to another workflow", async () => {
    const workflowA = deferred<Response>();
    const workflowB = deferred<Response>();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-a/bindable-inputs")) return workflowA.promise;
      if (url.endsWith("/api/workflows/wf-b/bindable-inputs")) return workflowB.promise;
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { rerender } = render(
      <DashboardBuilderPage
        workflowId="wf-a"
        workflowName="Workflow A"
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    rerender(
      <DashboardBuilderPage
        workflowId="wf-b"
        workflowName="Workflow B"
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    workflowA.resolve(jsonResponse(bindableInputsResponse("AlphaNode", "alpha")));
    await waitFor(() => expect(screen.getByText("Loading dashboard builder")).toBeInTheDocument());
    expect(screen.queryAllByText("AlphaNode")).toHaveLength(0);

    workflowB.resolve(jsonResponse(bindableInputsResponse("BetaNode", "beta")));
    await waitFor(() => expect(screen.getAllByText("BetaNode").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("AlphaNode")).toHaveLength(0);
  });
});
