export interface WorkflowNameLike {
  id?: string | null;
  name?: string | null;
  display_name?: string | null;
}

export function workflowDisplayName(workflow: WorkflowNameLike | null | undefined) {
  const displayName = workflow?.display_name?.trim();
  if (displayName) return displayName;
  const legacyName = workflow?.name?.trim();
  if (legacyName) return legacyName;
  return "Workflow";
}
