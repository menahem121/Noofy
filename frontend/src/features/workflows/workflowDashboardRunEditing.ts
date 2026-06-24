import type {
  DashboardControlDef,
  DashboardControlGroupDef,
  DashboardSavePayload,
  WorkflowInputDef,
  WorkflowOutputDef,
  WorkflowPackageResponse,
} from "../../lib/api/noofyApi";
import type { GridItemLayout } from "../../lib/gridLayout";
import {
  minimumSizeForWidgetGroup,
  minimumSizeForWidgetType,
  withCurrentWidgetGroupMinimum,
  withCurrentWidgetMinimum,
} from "../../lib/widgetSizes";
import {
  canPreserveWidgetAsHiddenInput,
  type DashboardSchema,
  type DashboardWidget,
  type WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import type { CanvasActionBarPosition } from "./CanvasDashboardView";
import { groupedControlIdSet } from "./dashboardTopLevelItems";
import { defaultValueForWorkflowInput } from "./workflowRunOutputs";
import type { WorkflowRunPageCachedState } from "./workflowRunPageCache";
import { seedModeFromValidation } from "../../lib/seedControl";

export type RunPageState = WorkflowRunPageCachedState;

export function dashboardUserStateVersion(packageData: WorkflowPackageResponse | null): string {
  if (!packageData) return "";

  const valueStateShape = {
    inputs: packageData.inputs.map((input) => ({
      id: input.id,
      control: input.control,
      binding: input.binding,
      default: input.default,
      validation: input.validation,
    })),
    controls: packageData.dashboard.sections.flatMap((section) =>
      section.controls.map((control) => ({
        id: control.id,
        type: control.type,
        input_id: control.input_id,
        output_id: control.output_id,
      })),
    ),
    groups: packageData.dashboard.sections.flatMap((section) =>
      (section.groups ?? []).map((group) => ({
        id: group.id,
        control_ids: group.control_ids,
        layout: group.layout,
      })),
    ),
  };

  return `${packageData.dashboard.version}:${hashString(stableJson(valueStateShape))}`;
}

export function actionBarPositionFromDashboard(
  position: { x?: unknown; y?: unknown } | null | undefined,
): CanvasActionBarPosition | null {
  if (!position || typeof position !== "object") return null;
  const candidate = position as { x?: unknown; y?: unknown };
  if (typeof candidate.x !== "number" || typeof candidate.y !== "number") return null;
  if (!Number.isFinite(candidate.x) || !Number.isFinite(candidate.y)) return null;
  return {
    x: Math.max(0, Math.round(candidate.x)),
    y: Math.max(0, Math.round(candidate.y)),
  };
}

export function dashboardSavePayloadWithActionBarPosition(
  packageData: WorkflowPackageResponse,
  position: CanvasActionBarPosition,
): DashboardSavePayload {
  return dashboardSavePayloadWithUpdates(packageData, { actionBarPosition: position });
}

export function dashboardSavePayloadWithTitle(
  packageData: WorkflowPackageResponse,
  kind: "control" | "group",
  id: string,
  title: string,
): DashboardSavePayload {
  return dashboardSavePayloadWithUpdates(packageData, {
    titleUpdate: { kind, id, title },
  });
}

export function updatePackageActionBarPosition(
  current: RunPageState,
  position: CanvasActionBarPosition,
): RunPageState {
  if (!current.packageData) return current;
  return {
    ...current,
    packageData: {
      ...current.packageData,
      dashboard: {
        ...current.packageData.dashboard,
        presentation: {
          ...(current.packageData.dashboard.presentation ?? {}),
          action_bar: {
            x: Math.max(0, Math.round(position.x)),
            y: Math.max(0, Math.round(position.y)),
          },
        },
      },
    },
  };
}

export function updatePackageDashboardTitle(
  current: RunPageState,
  kind: "control" | "group",
  id: string,
  title: string,
): RunPageState {
  if (!current.packageData) return current;
  return {
    ...current,
    packageData: {
      ...current.packageData,
      dashboard: {
        ...current.packageData.dashboard,
        sections: current.packageData.dashboard.sections.map((section) => ({
          ...section,
          controls:
            kind === "control"
              ? section.controls.map((control) =>
                  control.id === id ? { ...control, label: title } : control,
                )
              : section.controls,
          groups:
            kind === "group"
              ? section.groups?.map((group) =>
                  group.id === id ? { ...group, title } : group,
                )
              : section.groups,
        })),
      },
    },
  };
}

export function buildDashboardSchemaForEditing(
  workflowId: string,
  workflowName: string,
  controls: DashboardControlDef[],
  groups: DashboardControlGroupDef[],
  inputIndex: Map<string, WorkflowInputDef>,
  outputIndex: Map<string, WorkflowOutputDef>,
  layoutOverrides: Record<string, GridItemLayout>,
  actionBarPosition: CanvasActionBarPosition | null,
): DashboardSchema | null {
  const widgets: DashboardWidget[] = [];
  const referencedInputIds = new Set<string>();
  const groupedControlIds = groupedControlIdSet(groups);
  const controlTypeById = new Map(controls.map((control) => [control.id, control.type]));

  for (const control of controls) {
    const layout = groupedControlIds.has(control.id)
      ? undefined
      : layoutForBuilderControl(control, layoutOverrides[control.id]);

    if (control.type === "note") {
      const input = control.input_id ? inputIndex.get(control.input_id) : undefined;
      if (input) referencedInputIds.add(input.id);
      const defaultValue = input ? builderDefaultValueForInput(input) : null;
      widgets.push({
        id: control.id,
        valueId: input?.id ?? `note:${control.id}`,
        ...(input ? { backendInputId: input.id } : {}),
        binding: input
          ? { nodeId: input.binding.node_id, inputName: input.binding.input_name }
          : { nodeId: "", inputName: "" },
        widgetType: "note",
        title: control.label,
        description: control.description ?? "",
        defaultValue,
        ...(input?.default_pinned === true ? { defaultPinned: true } : {}),
        ...(input ? { hasExecutableBinding: true } : {}),
        layout,
      });
      continue;
    }

    if (control.input_id) {
      const input = inputIndex.get(control.input_id);
      if (!input) continue;
      referencedInputIds.add(input.id);
      widgets.push({
        id: control.id,
        valueId: input.id,
        backendInputId: input.id,
        binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
        widgetType: toBuilderWidgetType(control.type),
        title: control.label,
        description: control.description ?? "",
        defaultValue: builderDefaultValueForInput(input),
        ...(input.default_pinned === true ? { defaultPinned: true } : {}),
        min: numberValidation(input.validation.min),
        max: numberValidation(input.validation.max),
        step: numberValidation(input.validation.step),
        options: stringArrayValidation(input.validation.options),
        acceptedExtensions: stringArrayValidation(input.validation.accepted_extensions),
        acceptedMimeTypes: stringArrayValidation(input.validation.accepted_mime_types),
        ...(control.type === "seed_widget" ? { seedMode: seedModeFromValidation(input.validation) } : {}),
        layout,
      });
      continue;
    }

    if (control.output_id) {
      const output = outputIndex.get(control.output_id);
      if (!output) continue;
      const outputKind = output.kind ?? output.type;
      widgets.push({
        id: control.id,
        valueId: output.id,
        binding: { nodeId: output.node_id, inputName: "" },
        widgetType: outputKind === "audio" ? "display_audio" : outputKind === "text" ? "display_text" : outputKind === "video" ? "display_video" : outputKind === "3d" ? "display_3d" : outputKind === "file" ? "display_file" : "display_image",
        title: control.label,
        description: control.description ?? "",
        defaultValue: null,
        layout,
      });
    }
  }

  if (widgets.length === 0) return null;
  const hiddenWidgets = Array.from(inputIndex.values())
    .filter((input) => !referencedInputIds.has(input.id))
    .map((input) => hiddenBuilderWidgetForInput(input))
    .filter((widget): widget is DashboardWidget => Boolean(widget));

  return {
    version: 1,
    workflowId,
    workflowName,
    widgets,
    hiddenWidgets: hiddenWidgets.length > 0 ? hiddenWidgets : undefined,
    groups: groups.map((group) => {
      const override = layoutOverrides[group.id];
      const childTypes = group.control_ids
        .map((controlId) => controlTypeById.get(controlId))
        .filter((type): type is string => Boolean(type));
      const layout = layoutForBuilderGroup(group, childTypes, override);
      return {
        id: group.id,
        title: group.title,
        description: group.description ?? "",
        widgetIds: group.control_ids,
        layout,
      };
    }),
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
    presentation: actionBarPosition ? { actionBar: actionBarPosition } : undefined,
  };
}

function dashboardSavePayloadWithUpdates(
  packageData: WorkflowPackageResponse,
  updates: {
    actionBarPosition?: CanvasActionBarPosition;
    titleUpdate?: { kind: "control" | "group"; id: string; title: string };
  },
): DashboardSavePayload {
  const sections = packageData.dashboard.sections.map((section) => {
    const controlTypeById = new Map(section.controls.map((control) => [control.id, control.type]));
    return {
      ...section,
      controls: section.controls.map((control) => {
        const label =
          updates.titleUpdate?.kind === "control" && updates.titleUpdate.id === control.id
            ? updates.titleUpdate.title
            : control.label;
        if (!control.layout) return { ...control, label };
        const minimum = minimumSizeForWidgetType(control.type);
        return {
          ...control,
          label,
          layout: {
            x: control.layout.x,
            y: control.layout.y,
            w: control.layout.w,
            h: control.layout.h,
            min_w: minimum.w,
            min_h: minimum.h,
          },
        };
      }),
      groups: (section.groups ?? []).map((group) => {
        const title =
          updates.titleUpdate?.kind === "group" && updates.titleUpdate.id === group.id
            ? updates.titleUpdate.title
            : group.title;
        if (!group.layout) return { ...group, title };
        const childTypes = group.control_ids
          .map((controlId) => controlTypeById.get(controlId))
          .filter((controlType): controlType is string => Boolean(controlType));
        const minimum = minimumSizeForWidgetGroup(childTypes);
        return {
          ...group,
          title,
          layout: {
            x: group.layout.x,
            y: group.layout.y,
            w: group.layout.w,
            h: group.layout.h,
            min_w: minimum.w,
            min_h: minimum.h,
          },
        };
      }),
    };
  });
  return {
    inputs: packageData.inputs,
    dashboard: {
      ...packageData.dashboard,
      status: "configured",
      outputs: packageData.outputs,
      sections,
      presentation: updates.actionBarPosition
        ? {
            ...(packageData.dashboard.presentation ?? {}),
            action_bar: {
              x: Math.max(0, Math.round(updates.actionBarPosition.x)),
              y: Math.max(0, Math.round(updates.actionBarPosition.y)),
            },
          }
        : packageData.dashboard.presentation,
    },
  };
}

function hiddenBuilderWidgetForInput(
  input: WorkflowInputDef,
): DashboardWidget | null {
  const widgetType = inputWidgetTypeForBuilder(input.control);
  if (!widgetType) return null;
  const widget: DashboardWidget = {
    id: input.id,
    valueId: input.id,
    backendInputId: input.id,
    binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
    widgetType,
    title: input.label,
    description: "",
    defaultValue: builderDefaultValueForInput(input),
    ...(input.default_pinned === true ? { defaultPinned: true } : {}),
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

function builderDefaultValueForInput(
  input: WorkflowInputDef,
): unknown {
  return defaultValueForWorkflowInput(input);
}

function layoutForBuilderGroup(
  group: DashboardControlGroupDef,
  childTypes: string[],
  override?: GridItemLayout,
): DashboardWidget["layout"] {
  if (override) {
    return withCurrentWidgetGroupMinimum({
      x: override.x,
      y: override.y,
      w: override.w,
      h: override.h,
    }, childTypes);
  }

  if (!group.layout) return undefined;
  return withCurrentWidgetGroupMinimum({
    x: group.layout.x,
    y: group.layout.y,
    w: group.layout.w,
    h: group.layout.h,
  }, childTypes);
}

function layoutForBuilderControl(
  control: DashboardControlDef,
  override?: GridItemLayout,
): DashboardWidget["layout"] {
  if (override) {
    return withCurrentWidgetMinimum({
      x: override.x,
      y: override.y,
      w: override.w,
      h: override.h,
    }, control.type);
  }

  if (!control.layout) return undefined;

  return withCurrentWidgetMinimum({
    x: control.layout.x,
    y: control.layout.y,
    w: control.layout.w,
    h: control.layout.h,
  }, control.type);
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
    "display_text",
    "display_video",
    "display_file",
    "display_3d",
    "seed_widget",
    "lora_loader",
    "select",
  ]);
  return knownTypes.has(type as WidgetType) ? (type as WidgetType) : null;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function hashString(value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const options = value.filter((option): option is string => typeof option === "string" && option.length > 0);
  return options.length > 0 ? options : undefined;
}
