import { createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardBuilderPage } from "./DashboardBuilderPage";
import {
  dashboardDraftKey,
  dashboardSchemaFingerprint,
  saveDashboardDraft,
  toBackendPayload,
  type DashboardSchema,
} from "./dashboardBuilderContent";
import { topLevelDashboardControlItems } from "../workflows/dashboardTopLevelItems";

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
  groups: [],
  widgets: [
    {
      id: "ctrl-node-3-sampler_name",
      valueId: "node-3-sampler_name",
      binding: { nodeId: "3", inputName: "sampler_name" },
      widgetType: "select",
      title: "Sampler",
      description: "",
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
  groups: [],
  widgets: [
    {
      id: "ctrl-node-3-denoise",
      valueId: "node-3-denoise",
      binding: { nodeId: "3", inputName: "denoise" },
      widgetType: "slider",
      title: "Transformation level",
      description: "",
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

function savedDashboardBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "6",
        node_type: "CLIPTextEncode",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "text",
            current_value: "a lake",
            kind: "string",
            suggested_widget_type: "textarea",
            widget_types: ["textarea", "string_field"],
          },
        ],
      },
      {
        node_id: "3",
        node_type: "KSampler",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "steps",
            current_value: 20,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
        ],
      },
    ],
  };
}

function imageWorkflowBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "10",
        node_type: "LoadImage",
        is_image_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: "123e4567-e89b-12d3-a456-426614174000.png",
            kind: "image_input",
            suggested_widget_type: "load_image",
            widget_types: ["load_image", "load_image_mask"],
          },
        ],
      },
      {
        node_id: "9",
        node_type: "PreviewImage",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_image",
            current_value: null,
            kind: "image_output",
            suggested_widget_type: "display_image",
            widget_types: ["display_image"],
            auto_select: true,
          },
        ],
      },
    ],
  };
}

function requiredTextPathBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "22:4",
        node_type: "LoadText",
        node_title: "Load first text file",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: "/creator/input-a.txt",
            kind: "string",
            suggested_widget_type: "string_field",
            widget_types: ["string_field", "textarea"],
            required_runtime_input: true,
            required_runtime_kind: "text",
          },
        ],
      },
      {
        node_id: "22:5",
        node_type: "LoadText",
        node_title: "Load second text file",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: "/creator/input-b.txt",
            kind: "string",
            suggested_widget_type: "string_field",
            widget_types: ["string_field", "textarea"],
            required_runtime_input: true,
            required_runtime_kind: "text",
          },
        ],
      },
      {
        node_id: "9",
        node_type: "PreviewImage",
        node_title: "Preview result",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_image",
            current_value: null,
            kind: "image_output",
            suggested_widget_type: "display_image",
            widget_types: ["display_image"],
            auto_select: true,
          },
        ],
      },
    ],
  };
}

function optionalMediaAndTextOutputBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "2",
        node_type: "NoofyOptionalLoadImage",
        node_title: "Optional Load Image",
        is_image_node: true,
        is_audio_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: "",
            kind: "image_input",
            suggested_widget_type: "load_image",
            widget_types: ["load_image", "load_image_mask"],
          },
        ],
      },
      {
        node_id: "7",
        node_type: "NoofyOptionalLoadAudio",
        node_title: "Optional Load Audio",
        is_image_node: false,
        is_audio_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "audio",
            current_value: "",
            kind: "audio_input",
            suggested_widget_type: "load_audio",
            widget_types: ["load_audio"],
          },
        ],
      },
      {
        node_id: "4",
        node_type: "PreviewAny",
        node_title: "Preview as Text",
        is_image_node: false,
        is_audio_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_text",
            current_value: null,
            kind: "text_output",
            suggested_widget_type: "display_text",
            widget_types: ["display_text"],
            auto_select: true,
          },
        ],
      },
    ],
  };
}

function valuePreviewBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "20",
        node_type: "LoadFile",
        node_title: "Source file",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "file",
            current_value: "",
            kind: "file_input",
            suggested_widget_type: "load_file",
            widget_types: ["load_file"],
          },
        ],
      },
      {
        node_id: "75",
        node_type: "SaveVideo",
        node_title: "Video result",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_video",
            current_value: null,
            kind: "video_output",
            suggested_widget_type: "display_video",
            widget_types: ["display_video"],
            auto_select: true,
          },
        ],
      },
    ],
  };
}

function duplicateNodeNamesBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "1",
        node_type: "PrimitiveInt",
        node_title: "Int (Steps)",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "value",
            current_value: 8,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
        ],
      },
      {
        node_id: "2",
        node_type: "PrimitiveInt",
        node_title: "Int (Split Steps)",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "value",
            current_value: 16,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
        ],
      },
      {
        node_id: "3",
        node_type: "ComfyMathExpression",
        node_title: "Math Expression",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "expression",
            current_value: "a + b",
            kind: "string",
            suggested_widget_type: "string_field",
            widget_types: ["string_field", "textarea"],
          },
        ],
      },
      {
        node_id: "4",
        node_type: "PrimitiveInt",
        node_title: "Int (Steps)",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "value",
            current_value: 32,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
        ],
      },
    ],
  };
}

function dragWorkflowBindableInputsResponse() {
  return {
    nodes: [
      {
        node_id: "6",
        node_type: "CLIPTextEncode",
        node_title: "Positive prompt",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "text",
            current_value: "a lake",
            kind: "string",
            suggested_widget_type: "textarea",
            widget_types: ["textarea", "string_field"],
          },
        ],
      },
      {
        node_id: "7",
        node_type: "CLIPTextEncode",
        node_title: "Negative prompt",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "text",
            current_value: "blurry",
            kind: "string",
            suggested_widget_type: "textarea",
            widget_types: ["textarea", "string_field"],
          },
        ],
      },
      {
        node_id: "5",
        node_type: "EmptyLatentImage",
        node_title: "Image size",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "width",
            current_value: 1024,
            kind: "number",
            suggested_widget_type: "slider",
            widget_types: ["slider", "int_field"],
          },
          {
            input_name: "height",
            current_value: 768,
            kind: "number",
            suggested_widget_type: "slider",
            widget_types: ["slider", "int_field"],
          },
        ],
      },
    ],
  };
}

function dragSchema(groups: DashboardSchema["groups"] = []): DashboardSchema {
  return {
    version: 1,
    workflowId: "wf-drag",
    workflowName: "Drag workflow",
    layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
    groups,
    widgets: [
      {
        id: "ctrl-node-6-text",
        valueId: "node-6-text",
        binding: { nodeId: "6", inputName: "text" },
        widgetType: "textarea",
        title: "Prompt",
        description: "Describe the image.",
        defaultValue: "a lake",
        layout: { x: 0, y: 0, w: 16, h: 6 },
      },
      {
        id: "ctrl-node-7-text",
        valueId: "node-7-text",
        binding: { nodeId: "7", inputName: "text" },
        widgetType: "textarea",
        title: "Negative prompt",
        description: "What to avoid.",
        defaultValue: "blurry",
        layout: { x: 0, y: 6, w: 16, h: 6 },
      },
      {
        id: "ctrl-node-5-width",
        valueId: "node-5-width",
        binding: { nodeId: "5", inputName: "width" },
        widgetType: "slider",
        title: "Width",
        description: "Output width.",
        defaultValue: 1024,
        min: 64,
        max: 2048,
        step: 64,
        layout: { x: 0, y: 12, w: 16, h: 4 },
      },
      {
        id: "ctrl-node-5-height",
        valueId: "node-5-height",
        binding: { nodeId: "5", inputName: "height" },
        widgetType: "slider",
        title: "Height",
        description: "Output height.",
        defaultValue: 768,
        min: 64,
        max: 2048,
        step: 64,
        layout: { x: 0, y: 16, w: 16, h: 4 },
      },
    ],
  };
}

function promptSchema(workflowId: string, defaultValue: string): DashboardSchema {
  return {
    version: 1,
    workflowId,
    workflowName: "Prompt workflow",
    layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
    groups: [],
    widgets: [
      {
        id: "prompt",
        valueId: "prompt",
        binding: { nodeId: "6", inputName: "text" },
        widgetType: "textarea",
        title: "Prompt",
        description: "",
        defaultValue,
      },
    ],
  };
}

function dragDataTransfer() {
  return {
    effectAllowed: "",
    dropEffect: "",
    setData: vi.fn(),
    getData: vi.fn(),
    clearData: vi.fn(),
  };
}

function dragAndDrop(source: Element, target: Element) {
  const dataTransfer = dragDataTransfer();
  fireEvent.dragStart(source, { dataTransfer });
  fireEvent.dragOver(target, { dataTransfer });
  fireEvent.drop(target, { dataTransfer });
  fireEvent.dragEnd(source, { dataTransfer });
}

describe("DashboardBuilderPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    delete window.__NOOFY_RUNTIME_CONFIG__;
    delete window.__TAURI_INTERNALS__;
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
    delete window.__NOOFY_RUNTIME_CONFIG__;
    delete window.__TAURI_INTERNALS__;
    window.localStorage.clear();
  });

  function mockDragWorkflowFetch() {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-drag/bindable-inputs")) {
        return Promise.resolve(jsonResponse(dragWorkflowBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  }

  async function renderDragBuilder(initialSchema: DashboardSchema, onContinue = vi.fn()) {
    mockDragWorkflowFetch();
    render(
      <DashboardBuilderPage
        workflowId="wf-drag"
        workflowName="Drag workflow"
        initialSchema={initialSchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );
    await screen.findByTestId("created-widget-ctrl-node-6-text");
    return onContinue;
  }

  it("searches workflow controls by dashboard title and current node value", async () => {
    const schema = dragSchema();
    schema.widgets[0] = { ...schema.widgets[0], title: "Creative brief" };
    await renderDragBuilder(schema);
    const valuesPanel = await screen.findByLabelText("Workflow controls");

    fireEvent.change(within(valuesPanel).getByRole("searchbox", { name: /search workflow controls/i }), {
      target: { value: "creative brief" },
    });

    expect(within(valuesPanel).getByText("Positive prompt")).toBeInTheDocument();
    expect(within(valuesPanel).queryByText("Negative prompt")).not.toBeInTheDocument();

    fireEvent.change(within(valuesPanel).getByRole("searchbox", { name: /search workflow controls/i }), {
      target: { value: "lake" },
    });

    expect(within(valuesPanel).getByText("Positive prompt")).toBeInTheDocument();
    expect(within(valuesPanel).queryByText("Negative prompt")).not.toBeInTheDocument();

    fireEvent.change(within(valuesPanel).getByRole("searchbox", { name: /search workflow controls/i }), {
      target: { value: "positive lake" },
    });

    expect(within(valuesPanel).getByText("Positive prompt")).toBeInTheDocument();

    fireEvent.change(within(valuesPanel).getByRole("searchbox", { name: /search workflow controls/i }), {
      target: { value: "positive blurry" },
    });

    expect(within(valuesPanel).queryByText("Positive prompt")).not.toBeInTheDocument();
    expect(within(valuesPanel).queryByText("Negative prompt")).not.toBeInTheDocument();
    expect(within(valuesPanel).getByText("No controls match your search.")).toBeInTheDocument();
  });

  it("shows cancel instead of save draft when editing saved dashboard widgets and discards local edits", async () => {
    const onCancelEdit = vi.fn();
    mockDragWorkflowFetch();
    render(
      <DashboardBuilderPage
        workflowId="wf-drag"
        workflowName="Drag workflow"
        initialSchema={dragSchema()}
        onBack={vi.fn()}
        onCancelEdit={onCancelEdit}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const widget = await screen.findByTestId("created-widget-ctrl-node-6-text");
    expect(screen.queryByRole("button", { name: /save as draft/i })).not.toBeInTheDocument();
    const cancelButton = screen.getByRole("button", { name: /^cancel$/i });

    fireEvent.doubleClick(within(widget).getByText("Prompt"));
    fireEvent.change(within(widget).getByRole("textbox", { name: "Edit widget name" }), {
      target: { value: "Canceled prompt" },
    });
    await waitFor(() => expect(window.localStorage.getItem(dashboardDraftKey("wf-drag"))).not.toBeNull());

    fireEvent.click(cancelButton);

    expect(window.localStorage.getItem(dashboardDraftKey("wf-drag"))).toBeNull();
    expect(onCancelEdit).toHaveBeenCalledOnce();
  });

  it("edits a widget name inline from the created widgets list without changing its description", async () => {
    const onContinue = await renderDragBuilder(dragSchema());
    const widget = screen.getByTestId("created-widget-ctrl-node-6-text");

    fireEvent.doubleClick(within(widget).getByText("Prompt"));

    const nameInput = within(widget).getByRole("textbox", { name: "Edit widget name" });
    fireEvent.change(nameInput, { target: { value: "Creative prompt" } });

    expect(nameInput).toHaveValue("Creative prompt");
    expect(within(widget).getByText("Describe the image.")).toBeInTheDocument();
    expect(screen.getByLabelText(/widget title/i)).toHaveValue("Creative prompt");

    fireEvent.blur(nameInput);
    expect(within(widget).getByText("Creative prompt")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onContinue.mock.calls[0][0].widgets[0]).toEqual(
      expect.objectContaining({
        title: "Creative prompt",
        description: "Describe the image.",
      }),
    );
  });

  it("lists repeated primitive workflow nodes as separate value dropdowns", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-duplicates",
      workflowName: "Duplicate node workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-duplicates/bindable-inputs")) {
        return Promise.resolve(jsonResponse(duplicateNodeNamesBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-duplicates"
        workflowName="Duplicate node workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const valuesPanel = await screen.findByLabelText("Workflow controls");
    await within(valuesPanel).findByRole("button", { name: /Int \(Split Steps\).*PrimitiveInt/i });

    expect(within(valuesPanel).getAllByText("PrimitiveInt")).toHaveLength(3);
    expect(within(valuesPanel).getByRole("button", { name: /Math Expression.*ComfyMathExpression/i })).toBeInTheDocument();
    expect(within(valuesPanel).getAllByRole("button", { name: /Int \(Steps\).*PrimitiveInt\s*1/i })).toHaveLength(2);
    expect(within(valuesPanel).getByRole("button", { name: /Int \(Split Steps\).*PrimitiveInt\s*1/i })).toBeInTheDocument();
    const nodeTitles = Array.from(valuesPanel.querySelectorAll(".builder-node__title")).map((item) => item.textContent);
    expect(nodeTitles).toEqual(["Int (Split Steps)", "Int (Steps)", "Int (Steps)", "Math Expression"]);

    const primitiveValues = within(valuesPanel).getAllByRole("button", { name: /value/i });
    expect(primitiveValues).toHaveLength(1);
    fireEvent.click(primitiveValues[0]);
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].widgets).toEqual([
      expect.objectContaining({
        valueId: "node-2-value",
        binding: { nodeId: "2", inputName: "value" },
        defaultValue: 16,
      }),
    ]);
  });

  it("shows a hover preview for workflow controls without selecting or adding them", async () => {
    const onContinue = vi.fn();
    const promptOnlySchema: DashboardSchema = {
      version: 1,
      workflowId: "imported_text_to_image_demo",
      workflowName: "Text to Image Demo",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-node-6-text",
          valueId: "node-6-text",
          binding: { nodeId: "6", inputName: "text" },
          widgetType: "textarea",
          title: "Prompt",
          description: "",
          defaultValue: "a lake",
        },
      ],
    };

    render(
      <DashboardBuilderPage
        initialSchema={promptOnlySchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const valuesPanel = screen.getByLabelText("Workflow controls");
    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Prompt");
    fireEvent.click(await within(valuesPanel).findByRole("button", { name: /sampler/i }));
    const samplerValue = within(valuesPanel).getByRole("button", { name: /sampler_name/i });

    fireEvent.mouseEnter(samplerValue);

    const preview = await screen.findByRole("tooltip");
    expect(preview).toHaveTextContent("Widget type");
    expect(preview).toHaveTextContent("Dropdown");
    expect(preview).toHaveTextContent("Current/default");
    expect(preview).toHaveTextContent("euler");
    expect(preview).toHaveTextContent("Options (5)");
    expect(preview).toHaveTextContent("Sampler name");
    expect(within(preview).queryByText(/^Source$/)).not.toBeInTheDocument();
    expect(within(preview).queryByText(/^Other widgets$/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/widget title/i)).toHaveValue("Prompt");

    fireEvent.mouseLeave(samplerValue);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onContinue.mock.calls[0][0].widgets).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ valueId: "node-3-sampler_name" })]),
    );
  });

  it("summarizes file input acceptance and result output kind in value previews", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-preview",
      workflowName: "Preview workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-preview/bindable-inputs")) {
        return Promise.resolve(jsonResponse(valuePreviewBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-preview"
        workflowName="Preview workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Input file");
    const valuesPanel = screen.getByLabelText("Workflow controls");
    const fileValue = within(valuesPanel).getByRole("button", { name: /^file/i });

    fireEvent.mouseEnter(fileValue);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Load file");
    expect(screen.getByRole("tooltip")).toHaveTextContent(".txt, .json, .csv");
    fireEvent.mouseLeave(fileValue);

    fireEvent.click(within(valuesPanel).getByRole("button", { name: /video result/i }));
    const videoOutputValue = within(valuesPanel).getByRole("button", { name: /output_video/i });
    fireEvent.mouseEnter(videoOutputValue);

    expect(await screen.findByRole("tooltip")).toHaveTextContent("Display video");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Generated video result");
  });

  it("shows optional media inputs and PreviewAny text output in Workflow controls", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-optional-media",
      workflowName: "Optional media workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-optional-media/bindable-inputs")) {
        return Promise.resolve(jsonResponse(optionalMediaAndTextOutputBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-optional-media"
        workflowName="Optional media workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const valuesPanel = await screen.findByLabelText("Workflow controls");
    const search = within(valuesPanel).getByRole("searchbox", { name: /search workflow controls/i });
    fireEvent.change(search, { target: { value: "optional load" } });
    expect(await within(valuesPanel).findByRole("button", { name: /^image/i })).toBeInTheDocument();
    expect(await within(valuesPanel).findByRole("button", { name: /^audio/i })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "preview as text" } });
    const textOutput = await within(valuesPanel).findByRole("button", { name: /output_text/i });
    expect(textOutput).toBeInTheDocument();

    fireEvent.click(textOutput);
    expect(screen.getByLabelText(/widget type/i)).toHaveValue("display_text");
  });

  it("reorders top-level widgets through horizontal insertion zones and saves that order", async () => {
    const onContinue = await renderDragBuilder(dragSchema());
    const source = screen.getByTestId("created-widget-ctrl-node-6-text");
    const insertBeforeWidth = screen.getByTestId("created-insert-top-ctrl-node-5-width");
    const dataTransfer = dragDataTransfer();

    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(insertBeforeWidth, { dataTransfer });
    expect(insertBeforeWidth).toHaveClass("preview-insert-zone--active");
    fireEvent.drop(insertBeforeWidth, { dataTransfer });
    fireEvent.dragEnd(source, { dataTransfer });

    fireEvent.click(screen.getByRole("button", { name: /save as draft/i }));
    const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-drag")) ?? "{}") as DashboardSchema;
    expect(stored.widgets.map((widget) => widget.id)).toEqual([
      "ctrl-node-7-text",
      "ctrl-node-6-text",
      "ctrl-node-5-width",
      "ctrl-node-5-height",
    ]);

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    const payload = toBackendPayload(onContinue.mock.calls[0][0]);
    expect(payload.dashboard.sections[0].controls.map((control) => control.id)).toEqual([
      "ctrl-node-7-text",
      "ctrl-node-6-text",
      "ctrl-node-5-width",
      "ctrl-node-5-height",
    ]);
    expect(topLevelDashboardControlItems(payload.dashboard.sections[0].controls, payload.dashboard.sections[0].groups).map((item) => item.id)).toEqual([
      "ctrl-node-7-text",
      "ctrl-node-6-text",
      "ctrl-node-5-width",
      "ctrl-node-5-height",
    ]);
  });

  it("applies the visible top-level insertion preview when dropping on a nearby widget", async () => {
    const onContinue = await renderDragBuilder(dragSchema());
    const source = screen.getByTestId("created-widget-ctrl-node-6-text");
    const insertBeforeWidth = screen.getByTestId("created-insert-top-ctrl-node-5-width");
    const nearbyWidthWidget = screen.getByTestId("created-widget-ctrl-node-5-width");
    const dataTransfer = dragDataTransfer();

    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(insertBeforeWidth, { dataTransfer });
    expect(insertBeforeWidth).toHaveClass("preview-insert-zone--active");
    fireEvent.drop(nearbyWidthWidget, { dataTransfer });
    fireEvent.dragEnd(source, { dataTransfer });

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].groups).toEqual([]);
    expect(onContinue.mock.calls[0][0].widgets.map((widget: { id: string }) => widget.id)).toEqual([
      "ctrl-node-7-text",
      "ctrl-node-6-text",
      "ctrl-node-5-width",
      "ctrl-node-5-height",
    ]);
  });

  it("scrolls the created widgets panel only while dragging close to its lower edge", async () => {
    await renderDragBuilder(dragSchema());
    const source = screen.getByTestId("created-widget-ctrl-node-6-text");
    const lowerInsertZone = screen.getByTestId("created-insert-top-ctrl-node-5-width");
    const previewScroll = document.querySelector(".builder-preview__canvas") as HTMLElement;
    const dataTransfer = dragDataTransfer();
    const rectSpy = vi.spyOn(previewScroll, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 100,
      top: 100,
      left: 0,
      right: 360,
      bottom: 500,
      width: 360,
      height: 400,
      toJSON: () => ({}),
    });

    try {
      previewScroll.scrollTop = 120;
      fireEvent.dragStart(source, { dataTransfer });
      const outsideEdgeDragOver = createEvent.dragOver(lowerInsertZone, { dataTransfer });
      Object.defineProperty(outsideEdgeDragOver, "clientY", { value: 430 });
      fireEvent(lowerInsertZone, outsideEdgeDragOver);
      expect(previewScroll.scrollTop).toBe(120);

      const edgeDragOver = createEvent.dragOver(lowerInsertZone, { dataTransfer });
      Object.defineProperty(edgeDragOver, "clientY", { value: 476 });
      fireEvent(lowerInsertZone, edgeDragOver);
      fireEvent.dragEnd(source, { dataTransfer });

      expect(previewScroll.scrollTop).toBeGreaterThan(120);
      expect(previewScroll.scrollTop).toBeLessThanOrEqual(125);
    } finally {
      rectSpy.mockRestore();
    }
  });

  it("groups widgets only when dropping onto another widget body", async () => {
    const onContinue = await renderDragBuilder(dragSchema());
    const source = screen.getByTestId("created-widget-ctrl-node-6-text");
    const targetBody = screen.getByTestId("created-widget-ctrl-node-7-text");
    const dataTransfer = dragDataTransfer();

    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(targetBody, { dataTransfer });
    expect(targetBody).toHaveClass("preview-widget--group-preview");
    fireEvent.drop(targetBody, { dataTransfer });
    fireEvent.dragEnd(source, { dataTransfer });

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].widgets.map((widget: { id: string }) => widget.id)).toEqual([
      "ctrl-node-6-text",
      "ctrl-node-7-text",
      "ctrl-node-5-width",
      "ctrl-node-5-height",
    ]);
    expect(onContinue.mock.calls[0][0].groups).toEqual([
      expect.objectContaining({
        widgetIds: ["ctrl-node-6-text", "ctrl-node-7-text"],
      }),
    ]);
  });

  it("moves a widget out of a group into the exact top-level insertion slot", async () => {
    const onContinue = await renderDragBuilder(
      dragSchema([
        {
          id: "prompt-group",
          title: "Prompts",
          description: "",
          widgetIds: ["ctrl-node-6-text", "ctrl-node-7-text"],
          layout: { x: 0, y: 0, w: 16, h: 8 },
        },
      ]),
    );

    dragAndDrop(
      screen.getByTestId("created-widget-ctrl-node-6-text"),
      screen.getByTestId("created-insert-top-ctrl-node-5-width"),
    );
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].groups).toEqual([]);
    expect(onContinue.mock.calls[0][0].widgets.map((widget: { id: string }) => widget.id)).toEqual([
      "ctrl-node-7-text",
      "ctrl-node-6-text",
      "ctrl-node-5-width",
      "ctrl-node-5-height",
    ]);
  });

  it("reorders widgets inside an existing group through group insertion zones", async () => {
    const onContinue = await renderDragBuilder(
      dragSchema([
        {
          id: "main-group",
          title: "Main controls",
          description: "",
          widgetIds: ["ctrl-node-6-text", "ctrl-node-7-text", "ctrl-node-5-width"],
          layout: { x: 0, y: 0, w: 16, h: 12 },
        },
      ]),
    );

    dragAndDrop(
      screen.getByTestId("created-widget-ctrl-node-5-width"),
      screen.getByTestId("created-insert-group-main-group-ctrl-node-7-text"),
    );
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].groups).toEqual([
      expect.objectContaining({
        id: "main-group",
        widgetIds: ["ctrl-node-6-text", "ctrl-node-5-width", "ctrl-node-7-text"],
      }),
    ]);
    expect(onContinue.mock.calls[0][0].widgets.find((widget: { id: string }) => widget.id === "ctrl-node-5-width")).toMatchObject({
      binding: { nodeId: "5", inputName: "width" },
      defaultValue: 1024,
      min: 64,
      max: 2048,
      step: 64,
    });
  });

  it("does not show a grouping affordance for sibling widgets that are already in the same group", async () => {
    const onContinue = await renderDragBuilder(
      dragSchema([
        {
          id: "main-group",
          title: "Main controls",
          description: "",
          widgetIds: ["ctrl-node-6-text", "ctrl-node-7-text", "ctrl-node-5-width"],
          layout: { x: 0, y: 0, w: 16, h: 12 },
        },
      ]),
    );
    const source = screen.getByTestId("created-widget-ctrl-node-5-width");
    const siblingBody = screen.getByTestId("created-widget-ctrl-node-7-text");
    const dataTransfer = dragDataTransfer();

    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(siblingBody, { dataTransfer });
    expect(siblingBody).not.toHaveClass("preview-widget--group-preview");
    fireEvent.drop(siblingBody, { dataTransfer });
    fireEvent.dragEnd(source, { dataTransfer });
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].groups).toEqual([
      expect.objectContaining({
        id: "main-group",
        widgetIds: ["ctrl-node-6-text", "ctrl-node-7-text", "ctrl-node-5-width"],
      }),
    ]);
  });

  it("reorders a top-level group as a stable block", async () => {
    const onContinue = await renderDragBuilder(
      dragSchema([
        {
          id: "size-group",
          title: "Size",
          description: "",
          widgetIds: ["ctrl-node-7-text", "ctrl-node-5-width"],
          layout: { x: 0, y: 0, w: 16, h: 8 },
        },
      ]),
    );

    dragAndDrop(
      screen.getByTestId("created-group-size-group"),
      screen.getByTestId("created-insert-top-ctrl-node-6-text"),
    );
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue.mock.calls[0][0].widgets.map((widget: { id: string }) => widget.id)).toEqual([
      "ctrl-node-7-text",
      "ctrl-node-5-width",
      "ctrl-node-6-text",
      "ctrl-node-5-height",
    ]);
    expect(onContinue.mock.calls[0][0].groups).toEqual([
      expect.objectContaining({
        id: "size-group",
        widgetIds: ["ctrl-node-7-text", "ctrl-node-5-width"],
      }),
    ]);
    const payload = toBackendPayload(onContinue.mock.calls[0][0]);
    expect(topLevelDashboardControlItems(payload.dashboard.sections[0].controls, payload.dashboard.sections[0].groups).map((item) => item.id)).toEqual([
      "size-group",
      "ctrl-node-6-text",
      "ctrl-node-5-height",
    ]);
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
        widgets: expect.arrayContaining([
          expect.objectContaining({
            options: ["dpmpp_2m", "uni_pc", "dpm_2"],
          }),
        ]),
      }),
    );
  });

  it("adds and edits a dashboard-only note without a workflow binding", async () => {
    const onContinue = vi.fn();

    render(
      <DashboardBuilderPage
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /add note/i }));
    fireEvent.change(screen.getByLabelText(/note title/i), { target: { value: "Before you run" } });
    fireEvent.change(screen.getByLabelText(/note body/i), { target: { value: "Use a square source image." } });

    const notePreview = document.querySelector(".preview-note-card");
    expect(notePreview).toHaveTextContent("Use a square source image.");
    expect(notePreview?.closest(".preview-widget")?.querySelector(".preview-widget__heading p")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({
            id: "note-1",
            valueId: "note:note-1",
            binding: { nodeId: "", inputName: "" },
            widgetType: "note",
            title: "Before you run",
            description: "Use a square source image.",
          }),
        ]),
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
    expect(screen.queryByText(/^Group$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Orientation$/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /save as draft/i })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /save as draft/i }));
    expect(screen.getByText("Saved as draft")).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(dashboardDraftKey(invalidSliderSchema.workflowId)) ?? "{}")).toMatchObject({
      workflowId: invalidSliderSchema.workflowId,
      widgets: expect.arrayContaining([expect.objectContaining({ defaultValue: 0.3 })]),
      status: "draft",
    });

    fireEvent.change(screen.getByLabelText(/default value/i), { target: { value: "0.5" } });

    await waitFor(() => {
      expect(screen.queryByText("Default value must match the step size from the minimum value.")).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({ defaultValue: 0.5, min: 0, max: 1, step: 0.25 }),
        ]),
      }),
    );
  });

  it("accepts decimal slider steps without losing the denoise default during incomplete edits", async () => {
    const onContinue = vi.fn();

    render(
      <DashboardBuilderPage
        initialSchema={invalidSliderSchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const defaultValueInput = await screen.findByLabelText("Default value");
    const stepInput = screen.getByLabelText("Step size");

    fireEvent.change(defaultValueInput, { target: { value: "" } });
    expect(window.localStorage.getItem(dashboardDraftKey(invalidSliderSchema.workflowId))).toBeNull();
    fireEvent.blur(defaultValueInput);
    expect(defaultValueInput).toHaveValue("0.3");

    fireEvent.change(stepInput, { target: { value: "0" } });
    expect(screen.getByText("Step size must be positive. Decimals such as 0.01 are allowed.")).toBeInTheDocument();
    fireEvent.change(stepInput, { target: { value: "0." } });
    expect(stepInput).toHaveValue("0.");
    fireEvent.change(stepInput, { target: { value: "0.01" } });

    await waitFor(() => {
      expect(screen.queryByText("Step size must be positive. Decimals such as 0.01 are allowed.")).not.toBeInTheDocument();
      expect(JSON.parse(window.localStorage.getItem(dashboardDraftKey(invalidSliderSchema.workflowId)) ?? "{}")).toMatchObject({
        widgets: expect.arrayContaining([expect.objectContaining({ defaultValue: 0.3, step: 0.01 })]),
      });
      expect(screen.queryByText("Default value must match the step size from the minimum value.")).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({ defaultValue: 0.3, min: 0, max: 1, step: 0.01 }),
        ]),
      }),
    );
  });

  it("preselects a refinement slider with the beginner defaults for detected steps values", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-refinement",
      workflowName: "Refinement workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-refinement/bindable-inputs")) {
        return Promise.resolve(jsonResponse(savedDashboardBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-refinement"
        workflowName="Refinement workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const valuesPanel = await screen.findByLabelText("Workflow controls");
    fireEvent.click(within(valuesPanel).getByRole("button", { name: /KSampler/i }));
    fireEvent.click(within(valuesPanel).getByRole("button", { name: /^steps/i }));

    expect(screen.getByLabelText(/widget title/i)).toHaveValue("Refinement Level");
    expect(screen.getByLabelText(/widget type/i)).toHaveValue("slider");
    expect(screen.getByLabelText("Default value")).toHaveValue("20");
    expect(screen.getByLabelText("Minimum value")).toHaveValue("1");
    expect(screen.getByLabelText("Maximum value")).toHaveValue("100");
    expect(screen.getByLabelText("Step size")).toHaveValue("1");

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    const payload = toBackendPayload(onContinue.mock.calls[0][0] as DashboardSchema);
    expect(payload.inputs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Refinement Level",
          control: "slider",
          default: 20,
          validation: { min: 1, max: 100, step: 1 },
        }),
      ]),
    );
  });

  it("opens existing saved widgets in the editor after loading live workflow values", async () => {
    const savedSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-saved",
      workflowName: "Saved workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "prompt",
          valueId: "prompt",
          binding: { nodeId: "6", inputName: "text" },
          widgetType: "textarea",
          title: "Prompt",
          description: "",
          defaultValue: "a lake",
        },
        {
          id: "steps",
          valueId: "steps",
          binding: { nodeId: "3", inputName: "steps" },
          widgetType: "int_field",
          title: "Steps",
          description: "",
          defaultValue: 20,
        },
      ],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-saved/bindable-inputs")) {
        return Promise.resolve(jsonResponse(savedDashboardBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-saved"
        workflowName="Saved workflow"
        initialSchema={savedSchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Prompt");

    const stepsWidget = screen.getByText("Steps").closest("article");
    expect(stepsWidget).toBeInTheDocument();
    fireEvent.click(stepsWidget!);

    expect(screen.getByLabelText(/widget title/i)).toHaveValue("Steps");

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({ id: "prompt", valueId: "node-6-text" }),
          expect.objectContaining({ id: "steps", valueId: "node-3-steps" }),
        ]),
      }),
    );
  });

  it("keeps the current local draft ahead of saved dashboard and workflow values", async () => {
    const workflowId = "wf-current-default";
    saveDashboardDraft(
      promptSchema(workflowId, "current unsaved prompt"),
      dashboardSchemaFingerprint(promptSchema(workflowId, "saved dashboard prompt")),
    );
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith(`/api/workflows/${workflowId}/bindable-inputs`)) {
        return Promise.resolve(jsonResponse(savedDashboardBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const onContinue = vi.fn();
    render(
      <DashboardBuilderPage
        workflowId={workflowId}
        workflowName="Prompt workflow"
        initialSchema={promptSchema(workflowId, "saved dashboard prompt")}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const preview = await screen.findByLabelText("Dashboard preview");
    expect(within(preview).getByDisplayValue("current unsaved prompt")).toBeInTheDocument();
    const defaultEditor = within(screen.getByLabelText("Widget configuration")).getByDisplayValue("current unsaved prompt");
    fireEvent.change(defaultEditor, { target: { value: "just edited prompt" } });
    expect(within(preview).getByDisplayValue("just edited prompt")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({ id: "prompt", defaultValue: "just edited prompt" }),
        ]),
      }),
    );
    expect(
      JSON.parse(window.localStorage.getItem(dashboardDraftKey(workflowId)) ?? "{}"),
    ).toMatchObject({
      widgets: [expect.objectContaining({ id: "prompt", defaultValue: "just edited prompt" })],
    });
  });

  it("keeps saved dashboard defaults ahead of original workflow values", async () => {
    const workflowId = "wf-saved-default";
    const onContinue = vi.fn();
    saveDashboardDraft(
      promptSchema(workflowId, "stale draft prompt"),
      dashboardSchemaFingerprint(promptSchema(workflowId, "older saved prompt")),
    );
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith(`/api/workflows/${workflowId}/bindable-inputs`)) {
        return Promise.resolve(jsonResponse(savedDashboardBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId={workflowId}
        workflowName="Prompt workflow"
        initialSchema={promptSchema(workflowId, "saved dashboard prompt")}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    const preview = await screen.findByLabelText("Dashboard preview");
    expect(within(preview).getByDisplayValue("saved dashboard prompt")).toBeInTheDocument();
    expect(within(preview).queryByDisplayValue("a lake")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(dashboardDraftKey(workflowId))).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onContinue).toHaveBeenCalled();
    expect(window.localStorage.getItem(dashboardDraftKey(workflowId))).toBeNull();
  });

  it("uses original workflow values when no dashboard schema or draft exists", async () => {
    const workflowId = "wf-workflow-fallback";
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith(`/api/workflows/${workflowId}/bindable-inputs`)) {
        return Promise.resolve(jsonResponse(savedDashboardBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId={workflowId}
        workflowName="Prompt workflow"
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const preview = await screen.findByLabelText("Dashboard preview");
    expect(within(preview).getByDisplayValue("a lake")).toBeInTheDocument();
    expect(window.localStorage.getItem(dashboardDraftKey(workflowId))).toBeNull();
  });

  it("sets and clears optional minimum and maximum values for number fields", async () => {
    const savedSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-number-bounds",
      workflowName: "Number bounds",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "steps",
          valueId: "steps",
          binding: { nodeId: "3", inputName: "steps" },
          widgetType: "int_field",
          title: "Steps",
          description: "",
          defaultValue: 20,
        },
      ],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-number-bounds/bindable-inputs")) {
        return Promise.resolve(jsonResponse(savedDashboardBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-number-bounds"
        workflowName="Number bounds"
        initialSchema={savedSchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByLabelText(/widget title/i);
    fireEvent.click(screen.getByText("Steps").closest("article")!);

    const minimum = screen.getByLabelText("Minimum value");
    const maximum = screen.getByLabelText("Maximum value");
    expect(minimum).toHaveValue(null);
    expect(maximum).toHaveValue(null);

    fireEvent.change(minimum, { target: { value: "1" } });
    fireEvent.change(maximum, { target: { value: "80" } });
    const defaultValueInput = screen.getAllByRole("spinbutton")
      .find((element) => (element as HTMLInputElement).value === "20");
    expect(defaultValueInput).toHaveAttribute("min", "1");
    expect(defaultValueInput).toHaveAttribute("max", "80");

    fireEvent.change(maximum, { target: { value: "0" } });
    expect(screen.getByText("Maximum value must be greater than or equal to minimum value.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();

    fireEvent.change(maximum, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    const payload = toBackendPayload(onContinue.mock.calls[0][0] as DashboardSchema);
    expect(payload.inputs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "steps",
          control: "int_field",
          validation: { min: 1 },
        }),
      ]),
    );
  });

  it("automatically creates a load image widget for bindable LoadImage inputs", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Input image");

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({
            valueId: "node-10-image",
            binding: { nodeId: "10", inputName: "image" },
            widgetType: "load_image",
          }),
          expect.objectContaining({
            valueId: "node-9-output_image",
            binding: { nodeId: "9", inputName: "output_image" },
            widgetType: "display_image",
          }),
        ]),
      }),
    );
    expect(onContinue.mock.calls[0][0].widgets).toHaveLength(2);
  });

  it("restores and autosaves a draft without re-adding removed automatic widgets", async () => {
    const draft: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "note-1",
          valueId: "note:note-1",
          binding: { nodeId: "", inputName: "" },
          widgetType: "note",
          title: "Keep this draft",
          description: "The automatic media widgets were removed.",
          defaultValue: null,
        },
      ],
    };
    saveDashboardDraft(draft, "");
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const noteTitle = await screen.findByLabelText(/note title/i);
    expect(noteTitle).toHaveValue("Keep this draft");
    expect(screen.queryByText("Input image")).not.toBeInTheDocument();
    expect(screen.queryByText("Output image")).not.toBeInTheDocument();

    fireEvent.change(noteTitle, { target: { value: "Updated draft title" } });

    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-image")) ?? "{}") as DashboardSchema;
      expect(stored.widgets).toEqual([
        expect.objectContaining({ id: "note-1", title: "Updated draft title" }),
      ]);
    });
  });

  it("repairs a failed-save draft missing required text-path widgets before continuing", async () => {
    const brokenDraft: DashboardSchema = {
      version: 1,
      workflowId: "wf-text-path",
      workflowName: "Text path workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "note-1",
          valueId: "note:note-1",
          binding: { nodeId: "", inputName: "" },
          widgetType: "note",
          title: "Instructions",
          description: "A previous failed save left this draft without required inputs.",
          defaultValue: null,
        },
      ],
    };
    saveDashboardDraft(brokenDraft, dashboardSchemaFingerprint(brokenDraft));
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-text-path/bindable-inputs")) {
        return Promise.resolve(jsonResponse(requiredTextPathBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-text-path"
        workflowName="Text path workflow"
        initialSchema={brokenDraft}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-text-path")) ?? "{}") as DashboardSchema;
      expect(stored.widgets).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ id: "ctrl-node-22:4-image", defaultValue: "" }),
          expect.objectContaining({ id: "ctrl-node-22:5-image", defaultValue: "" }),
        ]),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    const payload = toBackendPayload(onContinue.mock.calls[0][0] as DashboardSchema);
    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "ctrl-node-22:4-image",
        binding: { node_id: "22:4", input_name: "image" },
        control: "string_field",
        default: "",
      }),
      expect.objectContaining({
        id: "ctrl-node-22:5-image",
        binding: { node_id: "22:5", input_name: "image" },
        control: "string_field",
        default: "",
      }),
    ]);
    expect(payload.dashboard.outputs ?? []).toEqual([]);
  });

  it("removes stale draft controls when their workflow bindings no longer exist", async () => {
    const workflowId = "wf-multimodal";
    const staleDraft: DashboardSchema = {
      version: 1,
      workflowId,
      workflowName: "Multimodal workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "note-1",
          valueId: "note:note-1",
          binding: { nodeId: "", inputName: "" },
          widgetType: "note",
          title: "Instructions",
          description: "Keep this note.",
          defaultValue: null,
        },
        {
          id: "ctrl-node-22:4-image",
          valueId: "node-22:4-image",
          binding: { nodeId: "22:4", inputName: "image" },
          widgetType: "string_field",
          title: "Stale image input",
          description: "",
          defaultValue: "",
          layout: { x: 0, y: 0, w: 16, h: 4 },
        },
      ],
    };
    saveDashboardDraft(staleDraft, dashboardSchemaFingerprint(staleDraft));
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith(`/api/workflows/${workflowId}/bindable-inputs`)) {
        return Promise.resolve(jsonResponse({
          nodes: [
            {
              node_id: "22:4",
              node_type: "TextEncodeQwenImageEdit",
              node_title: "TextEncodeQwenImageEdit",
              is_image_node: false,
              is_lora_node: false,
              inputs: [
                {
                  input_name: "prompt",
                  current_value: "turn the dog red",
                  kind: "string",
                  suggested_widget_type: "textarea",
                  widget_types: ["textarea", "string_field"],
                },
              ],
            },
          ],
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId={workflowId}
        workflowName="Multimodal workflow"
        initialSchema={staleDraft}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.queryByText("Stale image input")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    expect(onContinue).toHaveBeenCalledTimes(1);
    const reconciled = onContinue.mock.calls[0][0] as DashboardSchema;
    expect(reconciled.widgets.map((widget) => widget.id)).toEqual(["note-1"]);
  });

  it("preserves a removed input image widget as a hidden workflow input", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Input image");
    expect(screen.queryByText("Default saved.")).not.toBeInTheDocument();
    expect(screen.queryByText("No creator default file saved.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save as default/i }));
    expect(screen.getByRole("button", { name: /save as default/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /default saved/i })).not.toBeInTheDocument();
    expect(await screen.findByText("Default saved.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /remove widget/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /keep hidden default/i }));
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    const continuedSchema = onContinue.mock.calls[0][0] as DashboardSchema;
    const payload = toBackendPayload(continuedSchema);
    expect(continuedSchema.widgets).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ valueId: "node-10-image" })]),
    );
    expect(continuedSchema.hiddenWidgets).toEqual([
      expect.objectContaining({
        valueId: "node-10-image",
        binding: { nodeId: "10", inputName: "image" },
        widgetType: "load_image",
      }),
    ]);
    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "ctrl-node-10-image",
        binding: { node_id: "10", input_name: "image" },
        control: "load_image",
        default_pinned: true,
      }),
    ]);
    expect(payload.dashboard.sections[0].controls).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ input_id: "ctrl-node-10-image" })]),
    );
  });

  it("shows and clears an exported packaged image default in the builder", async () => {
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
      content_type: "image/png",
      size_bytes: 123,
    };
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "image-control",
          valueId: "image",
          backendInputId: "image",
          binding: { nodeId: "10", inputName: "image" },
          widgetType: "load_image",
          title: "Input image",
          description: "",
          defaultValue: packagedDefault,
          defaultPinned: true,
          layout: { x: 0, y: 0, w: 10, h: 8 },
        },
      ],
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        initialSchema={schema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByLabelText(/widget title/i);
    expect(screen.getAllByAltText("Default image: starter.png")[0]).toHaveAttribute(
      "src",
      "/api/workflows/wf-image/inputs/image/default-asset?asset_id=input-defaults%2Fstarter.png",
    );

    fireEvent.click(screen.getByRole("button", { name: /clear default/i }));
    expect(await screen.findByText("Default cleared.")).toBeInTheDocument();
    expect(screen.queryByAltText("Default image: starter.png")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    const payload = toBackendPayload(onContinue.mock.calls[0][0] as DashboardSchema);
    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "image",
        control: "load_image",
        default: null,
        default_pinned: false,
      }),
    ]);
  });

  it("loads an exported packaged image default during initial dashboard setup", async () => {
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
      content_type: "image/png",
      size_bytes: 123,
    };
    const onContinue = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-exported-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse({
          nodes: [
            {
              node_id: "13",
              node_type: "LoadImage",
              is_image_node: true,
              is_lora_node: false,
              inputs: [
                {
                  input_name: "image",
                  backend_input_id: "input-13-image",
                  current_value: packagedDefault,
                  default_pinned: true,
                  kind: "image_input",
                  suggested_widget_type: "load_image",
                  widget_types: ["load_image", "load_image_mask"],
                },
              ],
            },
          ],
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-exported-image"
        workflowName="Exported image workflow"
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    expect((await screen.findAllByAltText("Default image: starter.png"))[0]).toHaveAttribute(
      "src",
      "/api/workflows/wf-exported-image/inputs/input-13-image/default-asset?asset_id=input-defaults%2Fstarter.png",
    );

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    const payload = toBackendPayload(onContinue.mock.calls[0][0] as DashboardSchema);
    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "input-13-image",
        default: packagedDefault,
        default_pinned: true,
      }),
    ]);
  });

  it("explains saved-default removal choices and dismisses the dialog with Escape", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Input image");
    fireEvent.click(screen.getByRole("button", { name: /save as default/i }));
    expect(await screen.findByText("Default saved.")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: /remove widget/i })[0]);
    const dialog = screen.getByRole("dialog", { name: "Remove widget?" });
    expect(within(dialog).getByText("Saved default")).toBeInTheDocument();
    expect(within(dialog).getByText("Keep the saved value")).toBeInTheDocument();
    expect(within(dialog).getByText("Restore the workflow default")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Remove widget?" })).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText(/widget title/i)).toHaveValue("Input image");
  });

  it("shows creator default upload success only after a file upload", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    const onContinue = vi.fn();
    const uploadedAssetId = "12345678-1234-1234-1234-123456789abc.png";
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      if (url.endsWith("/api/workflows/wf-image/assets/image")) {
        return Promise.resolve(jsonResponse({ asset_id: uploadedAssetId, original_filename: "creator.png" }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const view = render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={onContinue}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Input image");
    expect(screen.queryByText("No creator default file saved.")).not.toBeInTheDocument();
    expect(screen.queryByText("creator.png saved as default.")).not.toBeInTheDocument();

    const fileInput = view.container.querySelector<HTMLInputElement>('.builder-default-asset input[type="file"]');
    expect(fileInput).toBeInTheDocument();
    fireEvent.change(fileInput!, {
      target: { files: [new File(["image"], "creator.png", { type: "image/png" })] },
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/wf-image/assets/image",
        expect.objectContaining({ method: "POST" }),
      );
    }, { timeout: 3000 });
    await waitFor(() => {
      expect(screen.getByText("creator.png saved as default.")).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(screen.getAllByAltText(`Default image: ${uploadedAssetId}`)[0]).toHaveAttribute(
      "src",
      `/api/assets/${uploadedAssetId}`,
    );

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    const payload = toBackendPayload(onContinue.mock.calls[0][0] as DashboardSchema);
    expect(payload.inputs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          control: "load_image",
          default: uploadedAssetId,
          default_pinned: true,
        }),
      ]),
    );
  });

  it("shows creator default upload errors only after an upload attempt", async () => {
    const emptySchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-image",
      workflowName: "Image workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/wf-image/bindable-inputs")) {
        return Promise.resolve(jsonResponse(imageWorkflowBindableInputsResponse()));
      }
      if (url.endsWith("/api/workflows/wf-image/assets/image")) {
        return Promise.resolve(jsonResponse({ detail: "Unsupported default file." }, 400));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const view = render(
      <DashboardBuilderPage
        workflowId="wf-image"
        workflowName="Image workflow"
        initialSchema={emptySchema}
        onBack={vi.fn()}
        onContinue={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText(/widget title/i)).toHaveValue("Input image");
    expect(screen.queryByText("Unsupported default file.")).not.toBeInTheDocument();

    const fileInput = view.container.querySelector<HTMLInputElement>('.builder-default-asset input[type="file"]');
    expect(fileInput).toBeInTheDocument();
    fireEvent.change(fileInput!, {
      target: { files: [new File(["bad"], "bad.txt", { type: "text/plain" })] },
    });

    expect(await screen.findByText("Unsupported default file.")).toBeInTheDocument();
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
