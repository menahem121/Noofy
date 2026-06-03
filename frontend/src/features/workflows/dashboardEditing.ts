import {
  canPreserveWidgetAsHiddenInput,
  type DashboardSchema,
  type DashboardWidget,
  type WidgetType,
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
  const referencedInputIds = new Set<string>();
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
        if (input) referencedInputIds.add(input.id);
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
        referencedInputIds.add(input.id);
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
          acceptedExtensions: stringArrayValidation(input.validation.accepted_extensions),
          acceptedMimeTypes: stringArrayValidation(input.validation.accepted_mime_types),
          layout,
        });
      } else if (control.output_id) {
        const output = outputIndex.get(control.output_id);
        if (!output) continue;
        const outputKind = output.kind ?? output.type;
        widgets.push({
          id: control.id,
          valueId: output.id,
          binding: { nodeId: output.node_id, inputName: "" },
          widgetType: outputKind === "audio" ? "display_audio" : outputKind === "video" ? "display_video" : outputKind === "3d" ? "display_3d" : outputKind === "file" ? "display_file" : "display_image",
          title: control.label,
          description: control.description ?? "",
          defaultValue: null,
          layout,
        });
      }
    }
  }
  const hiddenWidgets = packageData.inputs
    .filter((input) => !referencedInputIds.has(input.id))
    .map(hiddenWidgetForInput)
    .filter((widget): widget is DashboardWidget => Boolean(widget));

  return {
    version: 1,
    workflowId: packageData.metadata.id,
    workflowName: packageData.metadata.display_name ?? packageData.display_name ?? packageData.metadata.name,
    widgets,
    hiddenWidgets: hiddenWidgets.length > 0 ? hiddenWidgets : undefined,
    groups,
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
  };
}

function hiddenWidgetForInput(input: WorkflowInputDef): DashboardWidget | null {
  const widgetType = inputWidgetTypeForBuilder(input.control);
  if (!widgetType) return null;
  const widget: DashboardWidget = {
    id: input.id,
    valueId: input.id,
    binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
    widgetType,
    title: input.label,
    description: "",
    defaultValue: input.default,
    min: numberValidation(input.validation.min),
    max: numberValidation(input.validation.max),
    step: numberValidation(input.validation.step),
    options: stringArrayValidation(input.validation.options),
    acceptedExtensions: stringArrayValidation(input.validation.accepted_extensions),
    acceptedMimeTypes: stringArrayValidation(input.validation.accepted_mime_types),
  };
  if (widget.widgetType === "note") widget.hasExecutableBinding = true;
  return canPreserveWidgetAsHiddenInput(widget) ? widget : null;
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
  return inputWidgetTypeForBuilder(type) ?? "string_field";
}

function inputWidgetTypeForBuilder(type: string): WidgetType | null {
  const knownTypes = new Set<WidgetType>([
    "slider",
    "int_field",
    "string_field",
    "textarea",
    "note",
    "toggle",
    "load_image",
    "load_image_mask",
    "load_audio",
    "load_video",
    "load_file",
    "load_3d",
    "display_image",
    "display_audio",
    "display_video",
    "display_file",
    "display_3d",
    "seed_widget",
    "lora_loader",
    "select",
  ]);
  return knownTypes.has(type as WidgetType) ? (type as WidgetType) : null;
}

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : undefined;
}
