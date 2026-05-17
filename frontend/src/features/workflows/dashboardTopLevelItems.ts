import type { DashboardControlDef, DashboardControlGroupDef } from "../../lib/api/noofyApi";

export type DashboardTopLevelControlItem =
  | { kind: "control"; id: string; control: DashboardControlDef; layout?: DashboardControlDef["layout"] }
  | {
      kind: "group";
      id: string;
      group: DashboardControlGroupDef;
      controls: DashboardControlDef[];
      layout?: DashboardControlGroupDef["layout"];
    };

export function topLevelDashboardControlItems(
  controls: DashboardControlDef[],
  groups: DashboardControlGroupDef[] = [],
): DashboardTopLevelControlItem[] {
  const controlById = new Map(controls.map((control) => [control.id, control]));
  const groupByControlId = new Map<string, DashboardControlGroupDef>();
  const validGroups: DashboardControlGroupDef[] = [];

  for (const group of groups) {
    const uniqueControlIds = [...new Set(group.control_ids ?? [])].filter((controlId) => controlById.has(controlId));
    if (uniqueControlIds.length < 2) continue;
    const normalizedGroup = { ...group, control_ids: uniqueControlIds };
    validGroups.push(normalizedGroup);
    for (const controlId of uniqueControlIds) {
      if (!groupByControlId.has(controlId)) groupByControlId.set(controlId, normalizedGroup);
    }
  }

  const groupById = new Map(validGroups.map((group) => [group.id, group]));
  const emittedGroups = new Set<string>();
  const items: DashboardTopLevelControlItem[] = [];

  for (const control of controls) {
    const group = groupByControlId.get(control.id);
    if (!group) {
      items.push({ kind: "control", id: control.id, control, layout: control.layout });
      continue;
    }
    if (emittedGroups.has(group.id)) continue;
    const resolvedGroup = groupById.get(group.id);
    if (!resolvedGroup) continue;
    const groupedControls = resolvedGroup.control_ids
      .map((controlId) => controlById.get(controlId))
      .filter((item): item is DashboardControlDef => Boolean(item));
    if (groupedControls.length < 2) continue;
    items.push({ kind: "group", id: resolvedGroup.id, group: resolvedGroup, controls: groupedControls, layout: resolvedGroup.layout });
    emittedGroups.add(resolvedGroup.id);
  }

  return items;
}

export function groupedControlIdSet(groups: DashboardControlGroupDef[] = []): Set<string> {
  const ids = new Set<string>();
  for (const group of groups) {
    for (const controlId of group.control_ids ?? []) {
      ids.add(controlId);
    }
  }
  return ids;
}
