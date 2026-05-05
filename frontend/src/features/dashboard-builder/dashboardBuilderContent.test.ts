import { describe, expect, it } from "vitest";

import { toBackendPayload, workflowFromBindableInputs, type DashboardSchema } from "./dashboardBuilderContent";

describe("toBackendPayload", () => {
  it("writes output records that match output widget bindings", () => {
    const schema: DashboardSchema = {
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
          layout: { x: 0, y: 0, w: 6, h: 3, minW: 6, minH: 3 },
        },
        {
          id: "ctrl-output",
          valueId: "node-9-output",
          binding: { nodeId: "9", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result",
          description: "",
          orientation: "vertical",
          group: "simple",
          defaultValue: null,
          showDownload: true,
          layout: { x: 6, y: 0, w: 6, h: 6, minW: 5, minH: 5 },
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.dashboard.outputs).toEqual([
      { id: "image", label: "Result", node_id: "9", type: "image" },
    ]);
    expect(payload.dashboard.sections[0].controls[1]).toMatchObject({
      id: "ctrl-output",
      output_id: "image",
      show_download: true,
    });
    expect(payload.dashboard.sections[0].controls[0].layout).toMatchObject({
      min_w: 6,
      min_h: 3,
    });
  });
});

describe("workflowFromBindableInputs", () => {
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
