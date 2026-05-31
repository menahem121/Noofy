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
});
