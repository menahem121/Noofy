import type {
  DashboardSchema,
  DashboardWidget,
  WidgetGroup,
  WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import type {
  WorkflowInputDef,
  WorkflowOutputDef,
  WorkflowPackageResponse,
} from "../../lib/api/noofyApi";

export function buildDashboardSchemaForEditing(packageData: WorkflowPackageResponse): DashboardSchema {
  const inputIndex = new Map<string, WorkflowInputDef>();
  for (const input of packageData.inputs) inputIndex.set(input.id, input);
  const outputIndex = new Map<string, WorkflowOutputDef>();
  for (const output of packageData.outputs) outputIndex.set(output.id, output);

  const widgets: DashboardWidget[] = [];
  for (const section of packageData.dashboard.sections) {
    for (const control of section.controls) {
      const layout = control.layout
        ? {
            x: control.layout.x,
            y: control.layout.y,
            w: control.layout.w,
            h: control.layout.h,
            minW: control.layout.min_w,
            minH: control.layout.min_h,
          }
        : undefined;

      if (control.input_id) {
        const input = inputIndex.get(control.input_id);
        if (!input) continue;
        widgets.push({
          id: control.id,
          valueId: input.id,
          binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
          widgetType: toBuilderWidgetType(control.type),
          title: control.label,
          description: control.description ?? "",
          orientation: "vertical",
          group: toBuilderWidgetGroup(control.group),
          defaultValue: input.default,
          min: numberValidation(input.validation.min),
          max: numberValidation(input.validation.max),
          step: numberValidation(input.validation.step),
          options: stringArrayValidation(input.validation.options),
          layout,
        });
      } else if (control.output_id) {
        const output = outputIndex.get(control.output_id);
        if (!output) continue;
        widgets.push({
          id: control.id,
          valueId: output.id,
          binding: { nodeId: output.node_id, inputName: "" },
          widgetType: "display_image",
          title: control.label,
          description: control.description ?? "",
          orientation: "vertical",
          group: toBuilderWidgetGroup(control.group),
          defaultValue: null,
          showDownload: Boolean(control.show_download),
          layout,
        });
      }
    }
  }

  return {
    version: 1,
    workflowId: packageData.metadata.id,
    workflowName: packageData.metadata.name,
    widgets,
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
  };
}

function toBuilderWidgetType(type: string): WidgetType {
  if (type === "result_image") return "display_image";
  const knownTypes = new Set<WidgetType>([
    "slider",
    "int_field",
    "string_field",
    "textarea",
    "toggle",
    "load_image",
    "load_image_mask",
    "display_image",
    "seed_widget",
    "lora_loader",
    "select",
  ]);
  return knownTypes.has(type as WidgetType) ? (type as WidgetType) : "string_field";
}

function toBuilderWidgetGroup(group: string | undefined): WidgetGroup {
  return group === "advanced" ? "advanced" : "simple";
}

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : undefined;
}
