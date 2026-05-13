import type { ModelDownloadReference, ModelDownloadSelection } from "../../lib/api/noofyApi";

export function uniqueDownloadSelections(refs: ModelDownloadReference[]): ModelDownloadSelection[] {
  const seen = new Set<string>();
  const selections: ModelDownloadSelection[] = [];
  for (const ref of refs) {
    const key = `${ref.workflow_id}:${ref.requirement_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    selections.push({ workflow_id: ref.workflow_id, requirement_id: ref.requirement_id });
  }
  return selections;
}
