import { describe, expect, it } from "vitest";

import { buildDashboardSchemaForEditing } from "./dashboardEditing";

describe("buildDashboardSchemaForEditing", () => {
  it("keeps loaded note dimensions and replaces incoming minimums with current policy", () => {
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
                layout: { x: 2, y: 3, w: 3, h: 2, min_w: 99, min_h: 99 },
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
        layout: { x: 2, y: 3, w: 3, h: 2, minW: 4, minH: 3 },
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

  it("keeps loaded group dimensions and replaces the group minimum", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-1", name: "Workflow", version: "1.0.0", description: "" },
      inputs: [
        {
          id: "prompt",
          label: "Prompt",
          control: "textarea",
          binding: { node_id: "1", input_name: "text" },
          default: "",
          validation: {},
        },
        {
          id: "steps",
          label: "Steps",
          control: "int_field",
          binding: { node_id: "2", input_name: "steps" },
          default: 20,
          validation: {},
        },
      ],
      outputs: [],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [{
          id: "main",
          title: "Main",
          controls: [
            { id: "prompt", type: "textarea", label: "Prompt", input_id: "prompt" },
            { id: "steps", type: "int_field", label: "Steps", input_id: "steps" },
          ],
          groups: [{
            id: "settings",
            title: "Settings",
            control_ids: ["prompt", "steps"],
            layout: { x: 2, y: 3, w: 5, h: 4, min_w: 99, min_h: 99 },
          }],
        }],
      },
    });

    expect(schema.groups[0].layout).toEqual({ x: 2, y: 3, w: 5, h: 4, minW: 6, minH: 6 });
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

  it("keeps dashboard inputs without visible controls as hidden widgets", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-image", name: "Image Workflow", version: "1.0.0", description: "" },
      inputs: [
        {
          id: "source-image",
          label: "Source image",
          control: "load_image",
          binding: { node_id: "10", input_name: "image" },
          default: "123e4567-e89b-12d3-a456-426614174000.png",
          validation: {},
        },
      ],
      outputs: [{ id: "result-image", label: "Image", node_id: "20", type: "image", kind: "image" }],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [{
          id: "main",
          title: "Main",
          controls: [
            { id: "result", type: "display_image", label: "Result image", output_id: "result-image" },
          ],
        }],
      },
    });

    expect(schema.widgets.map((widget) => widget.widgetType)).toEqual(["display_image"]);
    expect(schema.hiddenWidgets).toEqual([
      expect.objectContaining({
        id: "source-image",
        valueId: "source-image",
        binding: { nodeId: "10", inputName: "image" },
        widgetType: "load_image",
        defaultValue: "123e4567-e89b-12d3-a456-426614174000.png",
      }),
    ]);
  });

  it("round-trips generic file accept rules and output widgets", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-file", name: "File Workflow", version: "1.0.0", description: "" },
      inputs: [{
        id: "source-file",
        label: "Source file",
        control: "load_file",
        binding: { node_id: "10", input_name: "file_path" },
        default: null,
        validation: { accepted_extensions: [".json", ".csv"], accepted_mime_types: ["application/json"] },
      }],
      outputs: [{ id: "result-file", label: "File", node_id: "20", type: "file", kind: "file" }],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [{
          id: "main",
          title: "Main",
          controls: [
            { id: "source", type: "load_file", label: "Source file", input_id: "source-file" },
            { id: "result", type: "display_file", label: "Result file", output_id: "result-file" },
          ],
        }],
      },
    });

    expect(schema.widgets.map((widget) => widget.widgetType)).toEqual(["load_file", "display_file"]);
    expect(schema.widgets[0]).toMatchObject({
      acceptedExtensions: [".json", ".csv"],
      acceptedMimeTypes: ["application/json"],
    });
  });
});
