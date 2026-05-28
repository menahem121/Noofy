import type { WorkflowImportResponse } from "../../lib/api/noofyApi";

export function importNeedsConfiguration(importResult: WorkflowImportResponse) {
  return importResult.status === "needs_input_setup" || importResult.unresolved_input_count > 0;
}
