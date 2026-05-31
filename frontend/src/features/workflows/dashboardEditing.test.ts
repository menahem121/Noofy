import { describe, expect, it } from "vitest";

import { buildDashboardSchemaForEditing } from "./dashboardEditing";

describe("buildDashboardSchemaForEditing", () => {
  it("keeps dashboard-only notes editable without inventing workflow bindings", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-1", name: "Workflow", version: "1.0.0", description: "" },
      inputs: [],
      outputs: [],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [
          {
            id: "main",
            title: "Main",
            controls: [
              {
                id: "creator-note",
                type: "note",
                label: "Before you run",
                description: "Use a square source image.\nLarge images take longer.",
                layout: { x: 0, y: 0, w: 6, h: 4, min_w: 6, min_h: 4 },
              },
            ],
          },
        ],
      },
    });

    expect(schema.widgets).toEqual([
      {
        id: "creator-note",
        valueId: "note:creator-note",
        binding: { nodeId: "", inputName: "" },
        widgetType: "note",
        title: "Before you run",
        description: "Use a square source image.\nLarge images take longer.",
        defaultValue: null,
        layout: { x: 0, y: 0, w: 6, h: 4, minW: 6, minH: 4 },
      },
    ]);
  });

  it("keeps an intentional workflow binding when reopening an imported note", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-1", name: "Workflow", version: "1.0.0", description: "" },
      inputs: [
        {
          id: "note-input",
          label: "Creator note",
          control: "note",
          binding: { node_id: "11", input_name: "text" },
          default: "runtime value",
          validation: {},
        },
      ],
      outputs: [],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [
          {
            id: "main",
            title: "Main",
            controls: [
              {
                id: "creator-note",
                type: "note",
                label: "Creator note",
                description: "Visible guidance.",
                input_id: "note-input",
              },
            ],
          },
        ],
      },
    });

    expect(schema.widgets[0]).toMatchObject({
      valueId: "note-input",
      binding: { nodeId: "11", inputName: "text" },
      widgetType: "note",
      defaultValue: "runtime value",
      hasExecutableBinding: true,
    });
  });

  it("round-trips declared video input and output widgets", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-video", name: "Video Workflow", version: "1.0.0", description: "" },
      inputs: [{
        id: "source-video",
        label: "Source video",
        control: "load_video",
        binding: { node_id: "30", input_name: "video_path" },
        default: null,
        validation: {},
      }],
      outputs: [{ id: "result-video", label: "Video", node_id: "32", type: "video", kind: "video" }],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [{
          id: "main",
          title: "Main",
          controls: [
            { id: "source", type: "load_video", label: "Source video", input_id: "source-video" },
            { id: "result", type: "display_video", label: "Result video", output_id: "result-video" },
          ],
        }],
      },
    });

    expect(schema.widgets.map((widget) => widget.widgetType)).toEqual(["load_video", "display_video"]);
    expect(schema.widgets[0].binding).toEqual({ nodeId: "30", inputName: "video_path" });
  });
});
