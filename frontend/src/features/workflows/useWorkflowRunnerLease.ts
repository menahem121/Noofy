import { useEffect, useRef } from "react";

import {
  closeWorkflowRunnerLease,
  openWorkflowRunnerLease,
} from "../../lib/api/noofyApi";
import type { WorkflowTabRuntimeState } from "../app/WorkflowTabs";
import { runnerIdFromLease } from "./workflowRunTracking";

interface WorkflowRunnerLeaseTabs {
  runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>;
  setWorkflowRuntime: (workflowId: string, update: Partial<WorkflowTabRuntimeState>) => void;
}

export function useWorkflowRunnerLease({
  workflowId,
  workflowTabs,
  packageReady,
  dashboardSetupRequired,
  runner,
}: {
  workflowId: string;
  workflowTabs: WorkflowRunnerLeaseTabs | null | undefined;
  packageReady: boolean;
  dashboardSetupRequired: boolean;
  runner: Record<string, unknown> | null | undefined;
}) {
  const runnerLeaseRequestRef = useRef<string | null>(null);
  const noRunnerLeaseProbeKeyRef = useRef<string | null>(null);
  const tabRuntime = workflowTabs?.runtimeByWorkflowId[workflowId];
  const heldRunnerLeaseId = tabRuntime?.runnerLeaseId ?? null;
  const boundRunnerId = runnerIdFromLease(runner ?? null);
  const staleRunnerLease = Boolean(
    heldRunnerLeaseId && boundRunnerId && tabRuntime?.runnerId && tabRuntime.runnerId !== boundRunnerId,
  );
  const trackedRunHandleForLease = tabRuntime?.activeJobId ?? tabRuntime?.queueId ?? null;
  const noRunnerLeaseProbeKey = [
    workflowId,
    boundRunnerId ?? "no-bound-runner",
    trackedRunHandleForLease ?? "no-active-run",
  ].join(":");

  useEffect(() => {
    if (!workflowTabs || !packageReady || dashboardSetupRequired) return;
    if (heldRunnerLeaseId && !staleRunnerLease) return;
    if (runnerLeaseRequestRef.current === workflowId) return;
    if (!staleRunnerLease && noRunnerLeaseProbeKeyRef.current === noRunnerLeaseProbeKey) return;
    let canceled = false;
    runnerLeaseRequestRef.current = workflowId;
    const staleLeaseId = staleRunnerLease ? heldRunnerLeaseId : null;
    if (staleLeaseId) {
      // The previously leased runner is gone (released or evicted); its lease
      // no longer protects the runner now bound to this workflow.
      void closeWorkflowRunnerLease(workflowId, staleLeaseId).catch(() => undefined);
    }
    openWorkflowRunnerLease(workflowId)
      .then((response) => {
        if (canceled) {
          if (response.lease_id) void closeWorkflowRunnerLease(workflowId, response.lease_id);
          return;
        }
        if (!response.lease_id) {
          noRunnerLeaseProbeKeyRef.current = noRunnerLeaseProbeKey;
          if (staleLeaseId) {
            workflowTabs.setWorkflowRuntime(workflowId, { runnerLeaseId: null, runnerId: null });
          }
          return;
        }
        noRunnerLeaseProbeKeyRef.current = null;
        workflowTabs.setWorkflowRuntime(workflowId, {
          runnerLeaseId: response.lease_id,
          runnerId: runnerIdFromLease(response.runner),
        });
      })
      .catch(() => {
        // A workflow can be opened without a bound isolated runner; tabs remain navigation-only.
      })
      .finally(() => {
        if (runnerLeaseRequestRef.current === workflowId) runnerLeaseRequestRef.current = null;
      });
    return () => {
      canceled = true;
      if (runnerLeaseRequestRef.current === workflowId) runnerLeaseRequestRef.current = null;
    };
  }, [
    workflowId,
    workflowTabs,
    packageReady,
    dashboardSetupRequired,
    heldRunnerLeaseId,
    staleRunnerLease,
    boundRunnerId,
    trackedRunHandleForLease,
    noRunnerLeaseProbeKey,
  ]);
}
