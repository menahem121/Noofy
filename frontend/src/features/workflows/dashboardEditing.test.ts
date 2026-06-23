import { describe, expect, it } from "vitest";

import { toBackendPayload } from "../dashboard-builder/dashboardBuilderContent";
import { buildDashboardSchemaForEditing } from "./dashboardEditing";

describe("buildDashboardSchemaForEditing", () => {
  it("round-trips decimal slider defaults and validation when reopening the builder", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-slider", name: "Slider workflow", version: "1.0.0", description: "" },
      inputs: [
        {
          id: "denoise",
          label: "Transformation level",
          control: "slider",
          binding: { node_id: "3", input_name: "denoise" },
          default: 0.3,
          validation: { min: 0, max: 1, step: 0.01 },
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
                id: "denoise",
                type: "slider",
                label: "Transformation level",
                input_id: "denoise",
              },
            ],
          },
        ],
      },
    });

    expect(schema.widgets[0]).toMatchObject({
      widgetType: "slider",
      defaultValue: 0.3,
      min: 0,
      max: 1,
      step: 0.01,
    });
  });

  it("round-trips optional number-field bounds when reopening the builder", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-number", name: "Number workflow", version: "1.0.0", description: "" },
      inputs: [
        {
          id: "steps",
          label: "Steps",
          control: "int_field",
          binding: { node_id: "3", input_name: "steps" },
          default: 20,
          validation: { min: 1, max: 80 },
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
                id: "steps",
                type: "int_field",
                label: "Steps",
                input_id: "steps",
              },
            ],
          },
        ],
      },
    });

    expect(schema.widgets[0]).toMatchObject({
      widgetType: "int_field",
      defaultValue: 20,
      min: 1,
      max: 80,
    });
  });

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

  it("preserves durable legacy, media, and credential widget types when reopening the builder", () => {
    const schema = buildDashboardSchemaForEditing({
      metadata: { id: "wf-durable", name: "Durable Workflow", version: "1.0.0", description: "" },
      inputs: [
        {
          id: "comfy-key",
          label: "Comfy API key",
          control: "api_credential",
          binding: { node_id: "1", input_name: "text" },
          default: null,
          validation: {},
        },
        {
          id: "mask",
          label: "Mask",
          control: "load_image_mask",
          binding: { node_id: "2", input_name: "image" },
          default: null,
          validation: {},
        },
        {
          id: "audio",
          label: "Audio",
          control: "load_audio",
          binding: { node_id: "3", input_name: "audio" },
          default: null,
          validation: {},
        },
        {
          id: "video",
          label: "Video",
          control: "load_video",
          binding: { node_id: "4", input_name: "video" },
          default: null,
          validation: {},
        },
        {
          id: "model",
          label: "Model",
          control: "load_3d",
          binding: { node_id: "5", input_name: "model" },
          default: null,
          validation: {},
        },
        {
          id: "file",
          label: "File",
          control: "load_file",
          binding: { node_id: "6", input_name: "file" },
          default: null,
          validation: { accepted_extensions: [".json"] },
        },
      ],
      outputs: [
        { id: "legacy-image", label: "Image", node_id: "10", type: "image", kind: "image" },
        { id: "audio-out", label: "Audio", node_id: "11", type: "audio", kind: "audio" },
        { id: "video-out", label: "Video", node_id: "12", type: "video", kind: "video" },
        { id: "model-out", label: "3D", node_id: "13", type: "3d", kind: "3d" },
        { id: "text-out", label: "Text", node_id: "14", type: "text", kind: "text" },
        { id: "file-out", label: "File", node_id: "15", type: "file", kind: "file" },
      ],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [{
          id: "main",
          title: "Main",
          controls: [
            {
              id: "comfy-key",
              type: "api_credential",
              label: "Comfy API key",
              input_id: "comfy-key",
              provider: "comfy_org",
              required: true,
              secret_ref: "api-key:comfy_org",
              injection_strategy: { kind: "comfyui_extra_data", field: "api_key_comfy_org" },
            },
            {
              id: "control-owned-key",
              type: "api_credential",
              label: "Control-owned key",
              provider: "comfy_org",
              required: true,
              secret_ref: "api-key:comfy_org",
              injection_strategy: { kind: "comfyui_extra_data", field: "api_key_comfy_org" },
            },
            { id: "mask", type: "load_image_mask", label: "Mask", input_id: "mask" },
            { id: "audio", type: "load_audio", label: "Audio", input_id: "audio" },
            { id: "video", type: "load_video", label: "Video", input_id: "video" },
            { id: "model", type: "load_3d", label: "Model", input_id: "model" },
            { id: "file", type: "load_file", label: "File", input_id: "file" },
            { id: "legacy-image", type: "result_image", label: "Legacy image", output_id: "legacy-image" },
            { id: "audio-out", type: "display_audio", label: "Audio out", output_id: "audio-out" },
            { id: "video-out", type: "display_video", label: "Video out", output_id: "video-out" },
            { id: "model-out", type: "display_3d", label: "3D out", output_id: "model-out" },
            { id: "text-out", type: "display_text", label: "Text out", output_id: "text-out" },
            { id: "file-out", type: "display_file", label: "File out", output_id: "file-out" },
          ],
        }],
      },
    });

    expect(schema.widgets.map((widget) => widget.widgetType)).toEqual([
      "api_credential",
      "api_credential",
      "load_image_mask",
      "load_audio",
      "load_video",
      "load_3d",
      "load_file",
      "result_image",
      "display_audio",
      "display_video",
      "display_3d",
      "display_text",
      "display_file",
    ]);
    expect(schema.widgets.find((widget) => widget.id === "legacy-image")).toMatchObject({
      backendOutputId: "legacy-image",
      widgetType: "result_image",
    });
    const payload = toBackendPayload(schema);
    const credentialControls = payload.dashboard.sections[0].controls.slice(0, 2);
    expect(credentialControls).toEqual([
      expect.objectContaining({
        id: "comfy-key",
        type: "api_credential",
        input_id: "comfy-key",
        provider: "comfy_org",
        required: true,
        secret_ref: "api-key:comfy_org",
        injection_strategy: { kind: "comfyui_extra_data", field: "api_key_comfy_org" },
      }),
      expect.objectContaining({
        id: "control-owned-key",
        type: "api_credential",
        provider: "comfy_org",
        required: true,
        secret_ref: "api-key:comfy_org",
        injection_strategy: { kind: "comfyui_extra_data", field: "api_key_comfy_org" },
      }),
    ]);
    expect(credentialControls[1]).not.toHaveProperty("input_id");
  });
});
