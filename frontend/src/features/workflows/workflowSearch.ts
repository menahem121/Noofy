import type { WorkflowSummary } from "../../lib/api/noofyApi";

export type WorkflowSearchStatus = "ready" | "need_setup" | "missing_models";

export interface WorkflowSearchFilters {
  query?: string;
  activeCategory?: string;
  categoryFilter?: string;
  sourceFilter?: string;
  statusFilter?: string;
  tagFilter?: string;
}

export function workflowStatus(summary: WorkflowSummary): WorkflowSearchStatus {
  if ((summary.missing_model_count ?? 0) > 0) return "missing_models";
  if (summary.needs_setup) return "need_setup";
  return "ready";
}

export function workflowStatusLabel(summary: WorkflowSummary) {
  const status = workflowStatus(summary);
  if (status === "need_setup") return "Need setup";
  if (status === "missing_models") return "Missing models";
  return "Ready";
}

export function searchWorkflows(workflows: WorkflowSummary[], filters: WorkflowSearchFilters) {
  const query = filters.query?.trim().toLowerCase() ?? "";

  return workflows.filter((workflow) => {
    const category = workflow.category ?? "Txt2img";
    const tags = workflow.tags ?? [];

    if (filters.activeCategory && filters.activeCategory !== "All" && category !== filters.activeCategory) return false;
    if (filters.categoryFilter && filters.categoryFilter !== "all" && category !== filters.categoryFilter) return false;
    if (filters.sourceFilter && filters.sourceFilter !== "all" && workflow.source_label !== filters.sourceFilter) return false;
    if (filters.tagFilter && filters.tagFilter !== "all" && !tags.includes(filters.tagFilter)) return false;
    if (filters.statusFilter && filters.statusFilter !== "all" && workflowStatus(workflow) !== filters.statusFilter) return false;

    if (query) {
      const haystack = [
        workflow.name,
        workflow.description,
        workflow.main_model?.name,
        category,
        workflow.source_label,
        ...tags,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(query)) return false;
    }

    return true;
  });
}
