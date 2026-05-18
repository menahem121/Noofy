import { describe, expect, it } from "vitest";

import {
  createDashboardWidgetForValue,
  dashboardDraftKey,
  loadDashboardDraft,
  saveDashboardDraft,
  toBackendPayload,
  workflowFromBindableInputs,
  type DashboardSchema,
} from "./dashboardBuilderContent";

describe("toBackendPayload", () => {
  it("preserves slider range and decimal step validation", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-strength",
          valueId: "node-3-denoise",
          binding: { nodeId: "3", inputName: "denoise" },
          widgetType: "slider",
          title: "Transformation level",
          description: "",
          defaultValue: 0.5,
          min: 0,
          max: 1,
          step: 0.25,
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs[0]).toMatchObject({
      id: "ctrl-strength",
      control: "slider",
      default: 0.5,
      validation: { min: 0, max: 1, step: 0.25 },
    });
  });

  it("writes dropdown choices into input validation", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-sampler",
          valueId: "node-3-sampler_name",
          binding: { nodeId: "3", inputName: "sampler_name" },
          widgetType: "select",
          title: "Sampler",
          description: "",
          defaultValue: "euler",
          options: ["euler", "euler_ancestral"],
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs[0]).toMatchObject({
      id: "ctrl-sampler",
      control: "select",
      binding: { node_id: "3", input_name: "sampler_name" },
      default: "euler",
      validation: { options: ["euler", "euler_ancestral"] },
    });
  });

  it("writes output records that match output widget bindings", () => {
    const schema: DashboardSchema = {
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
          layout: { x: 0, y: 0, w: 16, h: 6, minW: 16, minH: 6 },
        },
        {
          id: "ctrl-output",
          valueId: "node-9-output",
          binding: { nodeId: "9", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result",
          description: "",
          defaultValue: null,
          layout: { x: 16, y: 0, w: 16, h: 12, minW: 13, minH: 10 },
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.dashboard.status).toBe("configured");
    expect(payload.dashboard.outputs).toEqual([
      { id: "image", label: "Result", node_id: "9", type: "image" },
    ]);
    expect(payload.dashboard.sections[0].controls[1]).toMatchObject({
      id: "ctrl-output",
      output_id: "image",
    });
    expect(payload.dashboard.sections[0].controls[1]).not.toHaveProperty("show_download");
    expect(payload.dashboard.sections[0].controls[0].layout).toMatchObject({
      min_w: 16,
      min_h: 6,
    });
  });

  it("writes visual groups without merging child controls or layouts", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [
        {
          id: "size-group",
          title: "Image size",
          description: "Output dimensions.",
          widgetIds: ["ctrl-width", "ctrl-height"],
          layout: { x: 0, y: 0, w: 12, h: 8 },
        },
      ],
      widgets: [
        {
          id: "ctrl-width",
          valueId: "node-5-width",
          binding: { nodeId: "5", inputName: "width" },
          widgetType: "slider",
          title: "Width",
          description: "Output width.",
          defaultValue: 512,
          min: 256,
          max: 1024,
          step: 64,
          layout: { x: 0, y: 0, w: 10, h: 4 },
        },
        {
          id: "ctrl-height",
          valueId: "node-5-height",
          binding: { nodeId: "5", inputName: "height" },
          widgetType: "slider",
          title: "Height",
          description: "Output height.",
          defaultValue: 512,
          min: 256,
          max: 1024,
          step: 64,
          layout: { x: 12, y: 0, w: 10, h: 4 },
        },
      ],
    };

    const payload = toBackendPayload(schema);
    const section = payload.dashboard.sections[0];

    expect(payload.inputs).toHaveLength(2);
    expect(payload.inputs.map((input) => input.binding)).toEqual([
      { node_id: "5", input_name: "width" },
      { node_id: "5", input_name: "height" },
    ]);
    expect(section.groups).toEqual([
      {
        id: "size-group",
        title: "Image size",
        description: "Output dimensions.",
        control_ids: ["ctrl-width", "ctrl-height"],
        layout: { x: 0, y: 0, w: 12, h: 8, min_w: undefined, min_h: undefined },
      },
    ]);
    expect(section.controls[0].layout).toBeUndefined();
    expect(section.controls[1].layout).toBeUndefined();
  });
});

describe("saveDashboardDraft", () => {
  it("persists a draft with the same key used by both builder steps", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };

    saveDashboardDraft(schema);

    const stored = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-1")) ?? "{}");
    expect(stored).toMatchObject({ workflowId: "wf-1", status: "draft" });
  });

  it("loads a previously saved draft for the same workflow", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };

    saveDashboardDraft(schema);

    expect(loadDashboardDraft("wf-1")).toMatchObject({ workflowId: "wf-1", workflowName: "Workflow" });
    expect(loadDashboardDraft("other-workflow")).toBeNull();
  });
});

describe("workflowFromBindableInputs", () => {
  it("suggests sliders with image-dimension defaults for width and height inputs", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "5",
        node_type: "EmptyLatentImage",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "width",
            current_value: 1024,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
          {
            input_name: "height",
            current_value: 768,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
        ],
      },
    ]);

    const [widthValue, heightValue] = workflow.nodes[0].values;

    expect(createDashboardWidgetForValue(widthValue, workflow.nodes[0])).toMatchObject({
      widgetType: "slider",
      min: 64,
      max: 2048,
      step: 64,
      defaultValue: 1024,
    });
    expect(createDashboardWidgetForValue(heightValue, workflow.nodes[0])).toMatchObject({
      widgetType: "slider",
      min: 64,
      max: 2048,
      step: 64,
      defaultValue: 768,
    });
  });

  it("turns option-enriched ComfyUI inputs into selectable workflow values", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "3",
        node_type: "KSampler",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "sampler_name",
            current_value: "euler",
            kind: "select",
            suggested_widget_type: "select",
            widget_types: ["select", "string_field"],
            options: ["euler", "euler_ancestral"],
            hint: "The algorithm used when sampling.",
          },
        ],
      },
    ]);

    expect(workflow.nodes[0].values[0]).toMatchObject({
      valueKind: "select",
      rawValue: "euler",
      options: ["euler", "euler_ancestral"],
      hint: "The algorithm used when sampling.",
    });
  });

  it("turns backend image_output records into display image values", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "9",
        node_type: "SaveImage",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_image",
            current_value: null,
            kind: "image_output",
            suggested_widget_type: "display_image",
            widget_types: ["display_image"],
          },
        ],
      },
    ]);

    expect(workflow.nodes[0]).toMatchObject({
      id: "9",
      iconKind: "save",
      values: [
        {
          id: "node-9-output_image",
          nodeId: "9",
          inputName: "output_image",
          valueKind: "image_output",
        },
      ],
    });
  });
});
