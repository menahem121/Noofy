import { beforeEach, describe, expect, it } from "vitest";

import {
  addAutomaticDashboardWidgets,
  addAutomaticImageOutputWidget,
  addAutomaticVideoOutputWidget,
  addAutomaticImageInputWidgets,
  addAutomaticNoteWidgets,
  normalizeDashboardSchema,
  buildInitialDashboard,
  createDashboardWidgetForValue,
  clearDashboardDraft,
  dashboardDraftKey,
  dashboardSchemaFingerprint,
  ensureRequiredRuntimeInputWidgets,
  loadDashboardDraft,
  loadDashboardDraftEntry,
  resolveBuilderSchemaSource,
  saveDashboardDraft,
  toBackendPayload,
  workflowFromBindableInputs,
  type DashboardSchema,
} from "./dashboardBuilderContent";

function draftSchema(defaultValue = "a lake"): DashboardSchema {
  return {
    version: 1,
    workflowId: "wf-draft",
    workflowName: "Draft workflow",
    layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
    presentation: { actionBar: { x: 10, y: 20 } },
    groups: [
      {
        id: "main",
        title: "Main",
        description: "Primary controls",
        widgetIds: ["prompt", "note"],
        layout: { x: 0, y: 0, w: 16, h: 8 },
      },
    ],
    widgets: [
      {
        id: "prompt",
        valueId: "prompt",
        binding: { nodeId: "6", inputName: "text" },
        widgetType: "textarea",
        title: "Prompt",
        description: "Describe the result.",
        defaultValue,
        layout: { x: 0, y: 0, w: 16, h: 6 },
      },
      {
        id: "note",
        valueId: "note:note",
        binding: { nodeId: "", inputName: "" },
        widgetType: "note",
        title: "Tip",
        description: "Use a detailed prompt.",
        defaultValue: null,
      },
    ],
  };
}

describe("dashboard draft trust", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("fingerprints widget content but ignores geometry, presentation, seed mode, and workflow name", () => {
    const schema = draftSchema();
    const sameContent = {
      ...schema,
      workflowName: "Renamed workflow",
      layout: { gridColumns: 20, rowHeight: 48, gridGap: 2, responsive: false },
      presentation: { actionBar: { x: 400, y: 300 } },
      widgets: schema.widgets.map((widget) => ({
        ...widget,
        seedMode: "randomize" as const,
        layout: { x: 8, y: 12, w: 20, h: 9 },
      })),
      groups: schema.groups.map((group) => ({
        ...group,
        layout: { x: 8, y: 12, w: 20, h: 9 },
      })),
    };

    expect(dashboardSchemaFingerprint(sameContent)).toBe(dashboardSchemaFingerprint(schema));
    expect(dashboardSchemaFingerprint({
      ...schema,
      widgets: schema.widgets.map((widget, index) => index === 0 ? { ...widget, title: "Idea" } : widget),
    }))
      .not.toBe(dashboardSchemaFingerprint(schema));
    expect(dashboardSchemaFingerprint({ ...schema, widgets: schema.widgets.slice(0, 1) }))
      .not.toBe(dashboardSchemaFingerprint(schema));
    expect(dashboardSchemaFingerprint(draftSchema("a mountain"))).not.toBe(dashboardSchemaFingerprint(schema));
    expect(dashboardSchemaFingerprint({ ...schema, groups: [{ ...schema.groups[0], title: "Inputs" }] }))
      .not.toBe(dashboardSchemaFingerprint(schema));
  });

  it("canonicalizes empty LoRA defaults to None", () => {
    const schema = draftSchema();
    const lora = {
      ...schema.widgets[0],
      id: "lora",
      widgetType: "lora_loader" as const,
      defaultValue: null,
    };

    expect(dashboardSchemaFingerprint({ ...schema, widgets: [lora] })).toBe(
      dashboardSchemaFingerprint({ ...schema, widgets: [{ ...lora, defaultValue: "None" }] }),
    );
  });

  it("resolves fresh, genuine, in-flow, stale, and legacy draft sources", () => {
    const saved = draftSchema();
    const savedKey = dashboardSchemaFingerprint(saved);
    const edited = draftSchema("edited prompt");

    expect(resolveBuilderSchemaSource("wf-draft", saved)).toMatchObject({
      schema: normalizeDashboardSchema(saved),
      baseKey: savedKey,
      fromDraft: false,
    });

    saveDashboardDraft(edited, "");
    expect(resolveBuilderSchemaSource("wf-draft")).toMatchObject({
      schema: normalizeDashboardSchema(edited),
      baseKey: "",
      fromDraft: true,
    });

    saveDashboardDraft(edited, savedKey);
    expect(resolveBuilderSchemaSource("wf-draft", saved)).toMatchObject({
      schema: normalizeDashboardSchema(edited),
      baseKey: savedKey,
      fromDraft: true,
    });

    saveDashboardDraft(edited, savedKey);
    expect(resolveBuilderSchemaSource("wf-draft", edited)).toMatchObject({
      schema: normalizeDashboardSchema(edited),
      baseKey: savedKey,
      fromDraft: true,
    });

    const newerSaved = draftSchema("new saved prompt");
    saveDashboardDraft(edited, savedKey);
    expect(resolveBuilderSchemaSource("wf-draft", newerSaved)).toMatchObject({
      schema: normalizeDashboardSchema(newerSaved),
      baseKey: dashboardSchemaFingerprint(newerSaved),
      fromDraft: false,
    });
    expect(window.localStorage.getItem(dashboardDraftKey("wf-draft"))).toBeNull();

    window.localStorage.setItem(
      dashboardDraftKey("wf-draft"),
      JSON.stringify({ ...edited, status: "draft" }),
    );
    expect(loadDashboardDraftEntry("wf-draft")).toBeNull();
    expect(resolveBuilderSchemaSource("wf-draft", saved)).toMatchObject({
      schema: normalizeDashboardSchema(saved),
      fromDraft: false,
    });
    expect(window.localStorage.getItem(dashboardDraftKey("wf-draft"))).toBeNull();

    window.localStorage.setItem(
      dashboardDraftKey("wf-draft"),
      JSON.stringify({
        ...edited,
        status: "saved",
        baseKey: savedKey,
        updatedAt: Date.now(),
      }),
    );
    expect(loadDashboardDraftEntry("wf-draft")).toBeNull();
    expect(resolveBuilderSchemaSource("wf-draft", saved)).toMatchObject({
      schema: normalizeDashboardSchema(saved),
      fromDraft: false,
    });
    expect(window.localStorage.getItem(dashboardDraftKey("wf-draft"))).toBeNull();
  });

  it("stores a stamped envelope while exposing only its schema to draft consumers", () => {
    const schema = draftSchema();
    const baseKey = dashboardSchemaFingerprint(schema);
    saveDashboardDraft(schema, baseKey);

    const raw = JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-draft")) ?? "{}");
    expect(raw).toMatchObject({ status: "draft", baseKey, updatedAt: expect.any(Number) });
    expect(loadDashboardDraft("wf-draft")).toEqual(normalizeDashboardSchema(schema));
    expect(loadDashboardDraftEntry("wf-draft")).toMatchObject({
      schema: normalizeDashboardSchema(schema),
      baseKey,
    });

    clearDashboardDraft("wf-draft");
    expect(loadDashboardDraft("wf-draft")).toBeNull();
  });
});

describe("toBackendPayload", () => {
  it("persists the creator-defined canvas action bar presentation", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      presentation: { actionBar: { x: 120, y: 18 } },
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

    const payload = toBackendPayload(schema);

    expect(payload.dashboard.presentation).toEqual({ action_bar: { x: 120, y: 18 } });
  });

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

  it("writes the seed behavior mode into input validation", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-seed",
          valueId: "node-7-seed",
          binding: { nodeId: "7", inputName: "seed" },
          widgetType: "seed_widget",
          title: "Seed",
          description: "",
          defaultValue: 123,
          seedMode: "increment",
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs[0]).toMatchObject({
      id: "ctrl-seed",
      control: "seed_widget",
      default: 123,
      validation: { seed_mode: "increment" },
    });
  });

  it("defaults the seed behavior mode to randomize when unset", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-seed",
          valueId: "node-7-seed",
          binding: { nodeId: "7", inputName: "seed" },
          widgetType: "seed_widget",
          title: "Seed",
          description: "",
          defaultValue: 0,
        },
      ],
    };

    expect(toBackendPayload(schema).inputs[0].validation).toMatchObject({ seed_mode: "randomize" });
  });

  it("preserves an intentional executable binding on an imported note", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "bound-note",
          valueId: "note-value",
          binding: { nodeId: "11", inputName: "text" },
          widgetType: "note",
          title: "Creator note",
          description: "Visible guidance.",
          defaultValue: "runtime value",
          hasExecutableBinding: true,
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "bound-note",
        control: "note",
        binding: { node_id: "11", input_name: "text" },
        default: "runtime value",
      }),
    ]);
    expect(payload.dashboard.sections[0].controls[0]).toMatchObject({
      id: "bound-note",
      type: "note",
      input_id: "bound-note",
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
      { id: "image", label: "Result", node_id: "9", type: "image", kind: "image" },
    ]);
    expect(payload.dashboard.sections[0].controls[1]).toMatchObject({
      id: "ctrl-output",
      output_id: "image",
    });
    expect(payload.dashboard.sections[0].controls[1]).not.toHaveProperty("show_download");
    expect(payload.dashboard.sections[0].controls[0].layout).toMatchObject({
      x: 0,
      y: 0,
      w: 16,
      h: 6,
      min_w: 5,
      min_h: 4,
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
        layout: { x: 0, y: 0, w: 12, h: 8, min_w: 6, min_h: 6 },
      },
    ]);
    expect(section.controls[0].layout).toBeUndefined();
    expect(section.controls[1].layout).toBeUndefined();
  });

  it("saves hidden widgets as workflow inputs without visible dashboard controls", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-result",
          valueId: "node-9-output_image",
          binding: { nodeId: "9", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result",
          description: "",
          defaultValue: null,
          layout: { x: 0, y: 0, w: 16, h: 8 },
        },
      ],
      hiddenWidgets: [
        {
          id: "ctrl-node-10-image",
          valueId: "node-10-image",
          binding: { nodeId: "10", inputName: "image" },
          widgetType: "load_image",
          title: "Input image",
          description: "",
          defaultValue: "123e4567-e89b-12d3-a456-426614174000.png",
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "ctrl-node-10-image",
        control: "load_image",
        binding: { node_id: "10", input_name: "image" },
        default: "123e4567-e89b-12d3-a456-426614174000.png",
      }),
    ]);
    expect(payload.dashboard.sections[0].controls).toEqual([
      expect.objectContaining({ id: "ctrl-result", type: "display_image", output_id: "image" }),
    ]);
  });

  it("preserves backend input ids when saving edited dashboard controls", () => {
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
    };
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
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
          layout: { x: 0, y: 0, w: 16, h: 8 },
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs[0]).toMatchObject({
      id: "image",
      control: "load_image",
      default: packagedDefault,
      default_pinned: true,
    });
    expect(payload.dashboard.sections[0].controls[0]).toMatchObject({
      id: "image-control",
      input_id: "image",
    });
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

    saveDashboardDraft(schema, "");

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
      widgets: [{
        id: "prompt",
        valueId: "prompt",
        binding: { nodeId: "1", inputName: "text" },
        widgetType: "textarea",
        title: "Prompt",
        description: "",
        defaultValue: "",
        layout: { x: 2, y: 3, w: 3, h: 2, minW: 99, minH: 99 },
      }],
    };

    saveDashboardDraft(schema, "");

    expect(JSON.parse(window.localStorage.getItem(dashboardDraftKey("wf-1")) ?? "{}").widgets[0].layout).toEqual({
      x: 2,
      y: 3,
      w: 3,
      h: 2,
      minW: 5,
      minH: 4,
    });
    expect(loadDashboardDraft("wf-1")).toMatchObject({ workflowId: "wf-1", workflowName: "Workflow" });
    expect(loadDashboardDraft("wf-1")?.widgets[0].layout).toEqual({
      x: 2,
      y: 3,
      w: 3,
      h: 2,
      minW: 5,
      minH: 4,
    });
    expect(loadDashboardDraft("other-workflow")).toBeNull();
  });
});

describe("normalizeDashboardSchema", () => {
  it("preserves loaded dimensions while replacing widget and group minimums", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [{
        id: "compact-group",
        title: "Compact group",
        description: "",
        widgetIds: ["prompt", "steps"],
        layout: { x: 1, y: 2, w: 5, h: 4, minW: 99, minH: 99 },
      }],
      widgets: [
        {
          id: "prompt",
          valueId: "prompt",
          binding: { nodeId: "1", inputName: "text" },
          widgetType: "textarea",
          title: "Prompt",
          description: "",
          defaultValue: "",
          layout: { x: 3, y: 4, w: 3, h: 2, minW: 99, minH: 99 },
        },
        {
          id: "steps",
          valueId: "steps",
          binding: { nodeId: "2", inputName: "steps" },
          widgetType: "int_field",
          title: "Steps",
          description: "",
          defaultValue: 20,
        },
      ],
    };

    const normalized = normalizeDashboardSchema(schema);

    expect(normalized.widgets[0].layout).toEqual({ x: 3, y: 4, w: 3, h: 2, minW: 5, minH: 4 });
    expect(normalized.groups[0].layout).toEqual({ x: 1, y: 2, w: 5, h: 4, minW: 6, minH: 6 });
  });

  it("collapses duplicate output widgets that target the same node and kind", () => {
    // Stale state (e.g. a draft saved before the output-dedup fix, reloaded
    // after a reimport) can hold two display widgets for the same output node,
    // one keyed by the builder's synthetic value id and one rebuilt from a
    // saved dashboard. Loading it must heal back to a single widget, keeping
    // the placed one.
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-node-75-output_video",
          valueId: "node-75-output_video",
          binding: { nodeId: "75", inputName: "output_video" },
          widgetType: "display_video",
          title: "Output video",
          description: "",
          defaultValue: null,
        },
        {
          id: "c_result",
          valueId: "video",
          binding: { nodeId: "75", inputName: "" },
          widgetType: "display_video",
          title: "Video Output",
          description: "",
          defaultValue: null,
          layout: { x: 0, y: 0, w: 16, h: 8 },
        },
      ],
    };

    const normalized = normalizeDashboardSchema(schema);

    expect(normalized.widgets).toHaveLength(1);
    // The placed widget wins so its layout is preserved.
    expect(normalized.widgets[0].id).toBe("c_result");
  });

  it("keeps distinct output widgets that target different nodes", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "a",
          valueId: "node-9-output_image",
          binding: { nodeId: "9", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result A",
          description: "",
          defaultValue: null,
        },
        {
          id: "b",
          valueId: "node-10-output_image",
          binding: { nodeId: "10", inputName: "output_image" },
          widgetType: "display_image",
          title: "Result B",
          description: "",
          defaultValue: null,
        },
      ],
    };

    expect(normalizeDashboardSchema(schema).widgets).toHaveLength(2);
  });

  it("drops a hidden widget when the same binding is visible again", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-node-10-image",
          valueId: "node-10-image",
          binding: { nodeId: "10", inputName: "image" },
          widgetType: "load_image",
          title: "Input image",
          description: "",
          defaultValue: null,
        },
      ],
      hiddenWidgets: [
        {
          id: "hidden-node-10-image",
          valueId: "node-10-image",
          binding: { nodeId: "10", inputName: "image" },
          widgetType: "load_image",
          title: "Input image",
          description: "",
          defaultValue: "old.png",
        },
      ],
    };

    expect(normalizeDashboardSchema(schema).hiddenWidgets).toBeUndefined();
  });
});

describe("workflowFromBindableInputs", () => {
  it("uses backend graph-derived prompt labels for generated widget titles", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "6",
        node_type: "CLIPTextEncode",
        node_title: "Misleading negative title",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "text",
            current_value: "a lake",
            kind: "string",
            suggested_widget_type: "textarea",
            widget_types: ["textarea", "string_field"],
            suggested_label: "Positive prompt",
          },
        ],
      },
    ]);

    expect(workflow.nodes[0].values[0].label).toBe("Positive prompt");
    expect(buildInitialDashboard(workflow).widgets[0].title).toBe("Positive prompt");
  });

  it("uses the backend-suggested multiline widget for negative prompts by default", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "7",
        node_type: "CLIPTextEncode",
        node_title: "Negative prompt",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "text",
            current_value: "",
            kind: "string",
            suggested_widget_type: "textarea",
            widget_types: ["textarea", "string_field"],
            suggested_label: "Negative prompt",
          },
        ],
      },
    ]);

    expect(createDashboardWidgetForValue(workflow.nodes[0].values[0], workflow.nodes[0])).toMatchObject({
      title: "Negative prompt",
      widgetType: "textarea",
    });
  });

  it("preselects detected ComfyUI Note nodes as dashboard-only note cards", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "11",
        node_type: "Note",
        node_title: "Before you run",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "note",
            current_value: "Use a square source image.\nLarge images take longer.",
            kind: "note",
            suggested_widget_type: "note",
            widget_types: ["note"],
            auto_select: true,
          },
        ],
      },
    ]);

    const schema = addAutomaticNoteWidgets(buildInitialDashboard(workflow), workflow);
    const payload = toBackendPayload(schema);

    expect(schema.widgets).toEqual([
      expect.objectContaining({
        id: "ctrl-node-11-note",
        widgetType: "note",
        title: "Before you run",
        description: "Use a square source image.\nLarge images take longer.",
      }),
    ]);
    expect(payload.inputs).toEqual([]);
    expect(payload.dashboard.sections[0].controls).toEqual([
      expect.objectContaining({
        id: "ctrl-node-11-note",
        type: "note",
        label: "Before you run",
        description: "Use a square source image.\nLarge images take longer.",
      }),
    ]);
    expect(payload.dashboard.sections[0].controls[0]).not.toHaveProperty("input_id");
  });

  it("does not duplicate a saved dashboard-only note when its ComfyUI Note node is analyzed again", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "11",
        node_type: "Note",
        node_title: "Before you run",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "note",
            current_value: "Original creator guidance.",
            kind: "note",
            suggested_widget_type: "note",
            widget_types: ["note"],
            auto_select: true,
          },
        ],
      },
    ]);
    const savedSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-node-11-note",
          valueId: "note:ctrl-node-11-note",
          binding: { nodeId: "", inputName: "" },
          widgetType: "note",
          title: "Edited title",
          description: "Edited dashboard guidance.",
          defaultValue: null,
        },
      ],
    };

    const schema = addAutomaticNoteWidgets(savedSchema, workflow);

    expect(schema.widgets).toEqual(savedSchema.widgets);
  });

  it("auto-creates load image widgets for bindable LoadImage inputs", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "10",
        node_type: "LoadImage",
        is_image_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: "creator-local-input.png",
            kind: "image_input",
            suggested_widget_type: "load_image",
            widget_types: ["load_image", "load_image_mask"],
            hint: "Reference image for the workflow.",
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
    ]);

    const schema = buildInitialDashboard(workflow);

    expect(schema.widgets).toEqual([
      expect.objectContaining({
        id: "ctrl-node-10-image",
        valueId: "node-10-image",
        binding: { nodeId: "10", inputName: "image" },
        widgetType: "load_image",
        title: "Input image",
        description: "Pick an image",
        defaultValue: null,
      }),
      expect.objectContaining({
        id: "ctrl-node-9-output_image",
        valueId: "node-9-output_image",
        binding: { nodeId: "9", inputName: "output_image" },
        widgetType: "display_image",
        title: "Result",
        description: "Generated image will appear here.",
        defaultValue: null,
      }),
    ]);

    expect(toBackendPayload(schema).inputs).toEqual([
      expect.objectContaining({
        id: "ctrl-node-10-image",
        control: "load_image",
        binding: { node_id: "10", input_name: "image" },
        default: null,
      }),
    ]);
    expect(toBackendPayload(schema).dashboard.outputs).toEqual([
      { id: "image", label: "Result", node_id: "9", type: "image", kind: "image" },
    ]);
  });

  it("auto-creates empty text widgets for backend-marked required runtime string inputs", () => {
    const workflow = workflowFromBindableInputs("wf-required-text", "Required Text Workflow", [
      {
        node_id: "22:4",
        node_type: "LoadText",
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
    ]);

    const schema = buildInitialDashboard(workflow);

    expect(schema.widgets).toEqual([
      expect.objectContaining({
        id: "ctrl-node-22:4-image",
        binding: { nodeId: "22:4", inputName: "image" },
        widgetType: "string_field",
        title: "Input image",
        defaultValue: "",
      }),
      expect.objectContaining({
        id: "ctrl-node-22:5-image",
        binding: { nodeId: "22:5", inputName: "image" },
        widgetType: "string_field",
        title: "Input image",
        defaultValue: "",
      }),
      expect.objectContaining({
        id: "ctrl-node-9-output_image",
        widgetType: "display_image",
      }),
    ]);
    expect(toBackendPayload(schema).inputs).toEqual([
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
  });

  it("repairs a draft missing backend-marked required runtime text widgets", () => {
    const workflow = workflowFromBindableInputs("wf-required-text", "Required Text Workflow", [
      {
        node_id: "22:4",
        node_type: "LoadText",
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
    ]);
    const brokenDraft: DashboardSchema = {
      version: 1,
      workflowId: "wf-required-text",
      workflowName: "Required Text Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "note-1",
          valueId: "note:note-1",
          binding: { nodeId: "", inputName: "" },
          widgetType: "note",
          title: "Instructions",
          description: "",
          defaultValue: null,
        },
      ],
    };

    const repaired = ensureRequiredRuntimeInputWidgets(brokenDraft, workflow);

    expect(repaired.widgets).toEqual([
      expect.objectContaining({ id: "note-1" }),
      expect.objectContaining({
        id: "ctrl-node-22:4-image",
        binding: { nodeId: "22:4", inputName: "image" },
        widgetType: "string_field",
        defaultValue: "",
      }),
      expect.objectContaining({
        id: "ctrl-node-22:5-image",
        binding: { nodeId: "22:5", inputName: "image" },
        widgetType: "string_field",
        defaultValue: "",
      }),
    ]);
  });

  it("repairs required media and file runtime inputs with empty dashboard defaults", () => {
    const workflow = workflowFromBindableInputs("wf-required-media", "Required Media Workflow", [
      {
        node_id: "10",
        node_type: "LoadImage",
        is_image_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: "/creator/image.png",
            kind: "image_input",
            suggested_widget_type: "load_image",
            widget_types: ["load_image"],
            required_runtime_input: true,
            required_runtime_kind: "image",
          },
        ],
      },
      {
        node_id: "11",
        node_type: "LoadAudio",
        is_image_node: false,
        is_audio_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "audio",
            current_value: "/creator/audio.wav",
            kind: "audio_input",
            suggested_widget_type: "load_audio",
            widget_types: ["load_audio"],
            required_runtime_input: true,
            required_runtime_kind: "audio",
          },
        ],
      },
      {
        node_id: "12",
        node_type: "LoadVideo",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "video",
            current_value: "/creator/video.mp4",
            kind: "video_input",
            suggested_widget_type: "load_video",
            widget_types: ["load_video"],
            required_runtime_input: true,
            required_runtime_kind: "video",
          },
        ],
      },
      {
        node_id: "13",
        node_type: "Load3D",
        is_image_node: false,
        is_three_d_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "model",
            current_value: "/creator/model.glb",
            kind: "three_d_input",
            suggested_widget_type: "load_3d",
            widget_types: ["load_3d"],
            required_runtime_input: true,
            required_runtime_kind: "3d",
          },
        ],
      },
      {
        node_id: "14",
        node_type: "LoadFile",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "file",
            current_value: "/creator/data.json",
            kind: "file_input",
            suggested_widget_type: "load_file",
            widget_types: ["load_file"],
            required_runtime_input: true,
            required_runtime_kind: "file",
          },
        ],
      },
    ]);

    const repaired = ensureRequiredRuntimeInputWidgets({
      version: 1,
      workflowId: "wf-required-media",
      workflowName: "Required Media Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    }, workflow);

    expect(repaired.widgets).toEqual([
      expect.objectContaining({ widgetType: "load_image", defaultValue: null }),
      expect.objectContaining({ widgetType: "load_audio", defaultValue: null }),
      expect.objectContaining({ widgetType: "load_video", defaultValue: null }),
      expect.objectContaining({ widgetType: "load_3d", defaultValue: null }),
      expect.objectContaining({ widgetType: "load_file", defaultValue: null }),
    ]);
  });

  it("builds an initial image widget from an exported packaged default", () => {
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
    };
    const workflow = workflowFromBindableInputs("wf-exported-default", "Exported default", [
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
    ]);

    const schema = buildInitialDashboard(workflow);

    expect(schema.widgets).toEqual([
      expect.objectContaining({
        backendInputId: "input-13-image",
        binding: { nodeId: "13", inputName: "image" },
        widgetType: "load_image",
        defaultValue: packagedDefault,
        defaultPinned: true,
      }),
    ]);
    expect(toBackendPayload(schema).inputs).toEqual([
      expect.objectContaining({
        id: "input-13-image",
        default: packagedDefault,
        default_pinned: true,
      }),
    ]);
  });

  it("does not re-add intentionally removed optional output widgets while repairing a draft", () => {
    const workflow = workflowFromBindableInputs("wf-required-text", "Required Text Workflow", [
      {
        node_id: "22:4",
        node_type: "LoadText",
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
    ]);

    const repaired = ensureRequiredRuntimeInputWidgets({
      version: 1,
      workflowId: "wf-required-text",
      workflowName: "Required Text Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    }, workflow);

    expect(repaired.widgets).toEqual([
      expect.objectContaining({ id: "ctrl-node-22:4-image", widgetType: "string_field" }),
    ]);
    expect(repaired.widgets).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ widgetType: "display_image" })]),
    );
  });

  it("auto-creates generic file widgets with accepted extension validation", () => {
    const workflow = workflowFromBindableInputs("wf-file", "File Workflow", [
      {
        node_id: "10",
        node_type: "LoadFile",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "file_path",
            current_value: "",
            kind: "file_input",
            suggested_widget_type: "load_file",
            widget_types: ["load_file"],
            hint: "Workflow input file.",
          },
        ],
      },
      {
        node_id: "20",
        node_type: "SaveFile",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_file",
            current_value: null,
            kind: "file_output",
            suggested_widget_type: "display_file",
            widget_types: ["display_file"],
          },
        ],
      },
    ]);

    const schema = buildInitialDashboard(workflow);

    expect(schema.widgets.map((widget) => widget.widgetType)).toEqual(["load_file", "display_file"]);
    expect(toBackendPayload(schema).inputs[0]).toMatchObject({
      id: "ctrl-node-10-file_path",
      control: "load_file",
      validation: { accepted_extensions: [".txt", ".json", ".csv", ".srt", ".pdf", ".zip", ".npy", ".pt"] },
    });
    expect(toBackendPayload(schema).dashboard.outputs).toEqual([
      { id: "file", label: "Result", node_id: "20", type: "file", kind: "file" },
    ]);
  });

  it("keeps visible empty text input widgets in the backend save payload", () => {
    const schema: DashboardSchema = {
      version: 1,
      workflowId: "wf-empty-text-inputs",
      workflowName: "Empty Text Inputs",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-node-22-4-image",
          valueId: "node-22:4-image",
          binding: { nodeId: "22:4", inputName: "image" },
          widgetType: "string_field",
          title: "Image",
          description: "Choose a value when running the workflow.",
          defaultValue: "",
          layout: { x: 0, y: 0, w: 16, h: 4 },
        },
        {
          id: "ctrl-node-22-5-image",
          valueId: "node-22:5-image",
          binding: { nodeId: "22:5", inputName: "image" },
          widgetType: "string_field",
          title: "Image",
          description: "Choose a value when running the workflow.",
          defaultValue: "",
          layout: { x: 16, y: 0, w: 16, h: 4 },
        },
      ],
    };

    const payload = toBackendPayload(schema);

    expect(payload.inputs).toEqual([
      expect.objectContaining({
        id: "ctrl-node-22-4-image",
        binding: { node_id: "22:4", input_name: "image" },
        control: "string_field",
        default: "",
      }),
      expect.objectContaining({
        id: "ctrl-node-22-5-image",
        binding: { node_id: "22:5", input_name: "image" },
        control: "string_field",
        default: "",
      }),
    ]);
    expect(payload.dashboard.sections[0].controls).toEqual([
      expect.objectContaining({ id: "ctrl-node-22-4-image", input_id: "ctrl-node-22-4-image" }),
      expect.objectContaining({ id: "ctrl-node-22-5-image", input_id: "ctrl-node-22-5-image" }),
    ]);
  });

  it("auto-creates 3D input and canonical output widgets", () => {
    const workflow = workflowFromBindableInputs("wf-three-d", "3D Workflow", [
      {
        node_id: "10",
        node_type: "Load3D",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "model_file",
            current_value: "",
            kind: "three_d_input",
            suggested_widget_type: "load_3d",
            widget_types: ["load_3d"],
          },
        ],
      },
      {
        node_id: "20",
        node_type: "SaveGLB",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "output_3d",
            current_value: null,
            kind: "three_d_output",
            suggested_widget_type: "display_3d",
            widget_types: ["display_3d"],
          },
        ],
      },
    ]);

    const schema = buildInitialDashboard(workflow);

    expect(schema.widgets.map((widget) => widget.widgetType)).toEqual(["load_3d", "display_3d"]);
    expect(toBackendPayload(schema).dashboard.outputs).toEqual([
      { id: "3d", label: "3D model", node_id: "20", type: "3d", kind: "3d" },
    ]);
  });

  it("adds missing LoadImage widgets to an existing builder schema without duplicating widgets", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "10",
        node_type: "LoadImage",
        is_image_node: true,
        is_lora_node: false,
        inputs: [
          {
            input_name: "image",
            current_value: null,
            kind: "image_input",
            suggested_widget_type: "load_image",
            widget_types: ["load_image", "load_image_mask"],
          },
        ],
      },
    ]);
    const baseSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };

    const schema = addAutomaticImageInputWidgets(baseSchema, workflow);
    const roundTrip = addAutomaticImageInputWidgets(schema, workflow);

    expect(schema.widgets).toHaveLength(1);
    expect(schema.widgets[0]).toMatchObject({
      valueId: "node-10-image",
      widgetType: "load_image",
    });
    expect(roundTrip.widgets).toHaveLength(1);
  });

  it("preserves current media input values when auto-adding input widgets", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
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
    ]);
    const baseSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };

    const schema = addAutomaticImageInputWidgets(baseSchema, workflow);

    expect(schema.widgets[0]).toMatchObject({
      valueId: "node-10-image",
      defaultValue: "123e4567-e89b-12d3-a456-426614174000.png",
    });
  });

  it("adds only the selected final image output widget to an existing builder schema", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "8",
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
            auto_select: false,
          },
        ],
      },
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
            auto_select: true,
          },
        ],
      },
    ]);
    const baseSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [],
    };

    const schema = addAutomaticImageOutputWidget(baseSchema, workflow);
    const roundTrip = addAutomaticImageOutputWidget(schema, workflow);

    expect(schema.widgets).toEqual([
      expect.objectContaining({
        valueId: "node-9-output_image",
        binding: { nodeId: "9", inputName: "output_image" },
        widgetType: "display_image",
      }),
    ]);
    expect(roundTrip.widgets).toHaveLength(1);
  });

  it("does not add an automatic output widget when the schema already has an image output", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "8",
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
            auto_select: false,
          },
        ],
      },
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
            auto_select: true,
          },
        ],
      },
    ]);
    const baseSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "ctrl-node-8-output_image",
          valueId: "node-8-output_image",
          binding: { nodeId: "8", inputName: "output_image" },
          widgetType: "display_image",
          title: "Preview",
          description: "",
          defaultValue: null,
        },
      ],
    };

    const schema = addAutomaticImageOutputWidget(baseSchema, workflow);

    expect(schema.widgets).toHaveLength(1);
    expect(schema.widgets[0].valueId).toBe("node-8-output_image");
  });

  it("does not duplicate an output widget rebuilt from a saved dashboard for the same node", () => {
    // A saved dashboard control rebuilt for editing uses the backend output id
    // (e.g. "video") with an empty input name, which differs from the builder's
    // synthetic value id ("node-75-output_video"). The auto-add must still match
    // it by node id so the output widget is not duplicated.
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "75",
        node_type: "SaveVideo",
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
    ]);
    const baseSchema: DashboardSchema = {
      version: 1,
      workflowId: "wf-1",
      workflowName: "Workflow",
      layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
      groups: [],
      widgets: [
        {
          id: "c_result",
          valueId: "video",
          binding: { nodeId: "75", inputName: "" },
          widgetType: "display_video",
          title: "Video Output",
          description: "",
          defaultValue: null,
          layout: { x: 0, y: 0, w: 16, h: 8 },
        },
      ],
    };

    const schema = addAutomaticVideoOutputWidget(baseSchema, workflow);

    expect(schema.widgets).toHaveLength(1);
    expect(schema.widgets[0].id).toBe("c_result");
  });

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

  it("suggests a refinement slider with beginner-friendly defaults for steps inputs", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
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
    ]);

    workflow.nodes[0].values[0].numberRange = { min: 1, max: 60, step: 1 };
    const widget = createDashboardWidgetForValue(workflow.nodes[0].values[0], workflow.nodes[0]);

    expect(widget).toMatchObject({
      widgetType: "slider",
      title: "Refinement Level",
      defaultValue: 20,
      min: 1,
      max: 100,
      step: 1,
    });

    expect(
      toBackendPayload({
        version: 1,
        workflowId: workflow.id,
        workflowName: workflow.name,
        layout: { gridColumns: 32, rowHeight: 32, gridGap: 14, responsive: true },
        groups: [],
        widgets: [widget],
      }).inputs[0],
    ).toMatchObject({
      control: "slider",
      default: 20,
      validation: { min: 1, max: 100, step: 1 },
    });
  });

  it("recognizes common refinement aliases and keeps generated defaults within the slider range", () => {
    const workflow = workflowFromBindableInputs("wf-1", "Workflow", [
      {
        node_id: "3",
        node_type: "CustomSampler",
        is_image_node: false,
        is_lora_node: false,
        inputs: [
          {
            input_name: "num_inference_steps",
            current_value: 140,
            kind: "number",
            suggested_widget_type: "int_field",
            widget_types: ["int_field", "slider"],
          },
        ],
      },
    ]);

    expect(createDashboardWidgetForValue(workflow.nodes[0].values[0], workflow.nodes[0])).toMatchObject({
      widgetType: "slider",
      title: "Refinement Level",
      defaultValue: 100,
      min: 1,
      max: 100,
      step: 1,
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
            auto_select: true,
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
          autoSelect: true,
        },
      ],
    });
  });

  it("turns PreviewAny text outputs into display text values and widgets", () => {
    const workflow = workflowFromBindableInputs("wf-text", "Text Workflow", [
      {
        node_id: "4",
        node_type: "PreviewAny",
        node_title: "Preview as Text",
        is_image_node: false,
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
    ]);

    const value = workflow.nodes[0].values[0];
    expect(value).toMatchObject({
      valueKind: "text_output",
      inputName: "output_text",
      autoSelect: true,
    });
    expect(createDashboardWidgetForValue(value, workflow.nodes[0])).toMatchObject({
      widgetType: "display_text",
      title: "Result",
    });

    const schema = addAutomaticDashboardWidgets(
      {
        version: 1,
        workflowId: "wf-text",
        workflowName: "Text Workflow",
        widgets: [],
        groups: [],
        layout: { gridColumns: 32, rowHeight: 30, gridGap: 8, responsive: true },
      },
      workflow,
    );

    expect(schema.widgets).toEqual([
      expect.objectContaining({
        widgetType: "display_text",
        binding: { nodeId: "4", inputName: "output_text" },
      }),
    ]);
    expect(toBackendPayload(schema).dashboard.outputs).toEqual([
      expect.objectContaining({ node_id: "4", type: "text", kind: "text" }),
    ]);
  });
});
