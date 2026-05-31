import type {
  DashboardSchema,
  DashboardWidget,
  WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import type {
  DashboardControlDef,
  DashboardControlGroupDef,
  WorkflowInputDef,
  WorkflowOutputDef,
  WorkflowPackageResponse,
} from "../../lib/api/noofyApi";
import { defaultLayoutForWidgetGroup } from "../../lib/widgetSizes";

export function buildDashboardSchemaForEditing(packageData: WorkflowPackageResponse): DashboardSchema {
  const inputIndex = new Map<string, WorkflowInputDef>();
  for (const input of packageData.inputs) inputIndex.set(input.id, input);
  const outputIndex = new Map<string, WorkflowOutputDef>();
  for (const output of packageData.outputs) outputIndex.set(output.id, output);

  const widgets: DashboardWidget[] = [];
  const groups = packageData.dashboard.sections.flatMap((section) =>
    dashboardGroupsForBuilder(section.groups ?? [], section.controls),
  );
  const groupedControlIds = new Set(groups.flatMap((group) => group.widgetIds));
  for (const section of packageData.dashboard.sections) {
    for (const control of section.controls) {
      const layout = !groupedControlIds.has(control.id) && control.layout
        ? {
            x: control.layout.x,
            y: control.layout.y,
            w: control.layout.w,
            h: control.layout.h,
            minW: control.layout.min_w,
            minH: control.layout.min_h,
          }
        : undefined;

      if (control.type === "note") {
        const input = control.input_id ? inputIndex.get(control.input_id) : undefined;
        widgets.push({
          id: control.id,
          valueId: input?.id ?? `note:${control.id}`,
          binding: input
            ? { nodeId: input.binding.node_id, inputName: input.binding.input_name }
            : { nodeId: "", inputName: "" },
          widgetType: "note",
          title: control.label,
          description: control.description ?? "",
          defaultValue: input?.default ?? null,
          ...(input ? { hasExecutableBinding: true } : {}),
          layout,
        });
      } else if (control.input_id) {
        const input = inputIndex.get(control.input_id);
        if (!input) continue;
        widgets.push({
          id: control.id,
          valueId: input.id,
          binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
          widgetType: toBuilderWidgetType(control.type),
          title: control.label,
          description: control.description ?? "",
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
          defaultValue: null,
          layout,
        });
      }
    }
  }

  return {
    version: 1,
    workflowId: packageData.metadata.id,
    workflowName: packageData.metadata.display_name ?? packageData.display_name ?? packageData.metadata.name,
    widgets,
    groups,
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
  };
}

function dashboardGroupsForBuilder(groups: DashboardControlGroupDef[], controls: DashboardControlDef[]) {
  const controlTypeById = new Map(controls.map((control) => [control.id, control.type]));
  return groups.map((group) => {
    const childTypes = group.control_ids
      .map((controlId) => controlTypeById.get(controlId))
      .filter((type): type is string => Boolean(type));
    return groupForBuilder(group, childTypes);
  });
}

function groupForBuilder(group: DashboardControlGroupDef, childTypes: string[]) {
  const fallback = defaultLayoutForWidgetGroup(childTypes);
  const minW = group.layout?.min_w ?? fallback.minW;
  const minH = group.layout?.min_h ?? fallback.minH;
  return {
    id: group.id,
    title: group.title,
    description: group.description ?? "",
    widgetIds: group.control_ids,
    layout: group.layout
      ? {
          x: group.layout.x,
          y: group.layout.y,
          w: Math.max(group.layout.w, minW ?? 2),
          h: Math.max(group.layout.h, minH ?? 2),
          minW,
          minH,
        }
      : undefined,
  };
}

function toBuilderWidgetType(type: string): WidgetType {
  if (type === "result_image") return "display_image";
  const knownTypes = new Set<WidgetType>([
    "slider",
    "int_field",
    "string_field",
    "textarea",
    "note",
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

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : undefined;
}
