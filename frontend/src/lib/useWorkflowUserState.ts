import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  deleteUserStateLayout,
  deleteUserStateValues,
  fetchUserState,
  saveUserState,
  type WorkflowInputDef,
  type WorkflowUserState,
} from "./api/noofyApi";
import type { GridItemLayout } from "./gridLayout";

const DEBOUNCE_MS = 600;
const EMPTY_CONTROL_IDS: string[] = [];

function emptyState(workflowId: string, dashboardVersion: string): WorkflowUserState {
  return {
    schema_version: "1",
    workflow_id: workflowId,
    dashboard_version: dashboardVersion,
    values: {},
    layout_overrides: {},
  };
}

function pruneState(
  state: WorkflowUserState,
  packageDefaults: Record<string, unknown>,
  inputIndex: Map<string, WorkflowInputDef>,
  validControlIds: string[],
  currentDashboardVersion: string,
): WorkflowUserState {
  if (state.dashboard_version === currentDashboardVersion) return state;

  const validInputIds = new Set(inputIndex.keys());
  const validLayoutIds = new Set(validControlIds);
  const prunedValues: Record<string, unknown> = {};
  for (const id of validInputIds) {
    prunedValues[id] = id in state.values ? state.values[id] : packageDefaults[id];
  }

  const prunedOverrides: Record<string, { x: number; y: number; w: number; h: number }> = {};
  for (const [id, override] of Object.entries(state.layout_overrides)) {
    if (validLayoutIds.has(id)) prunedOverrides[id] = override;
  }

  return {
    ...state,
    dashboard_version: currentDashboardVersion,
    values: prunedValues,
    layout_overrides: prunedOverrides,
  };
}

export function useWorkflowUserState(
  workflowId: string,
  packageDefaults: Record<string, unknown>,
  dashboardVersion: string,
  inputIndex: Map<string, WorkflowInputDef>,
  validControlIds: string[] = EMPTY_CONTROL_IDS,
) {
  const [userState, setUserState] = useState<WorkflowUserState>(() =>
    emptyState(workflowId, dashboardVersion),
  );
  const [loaded, setLoaded] = useState(false);
  const saveTimerRef = useRef<number | null>(null);
  const latestStateRef = useRef<WorkflowUserState>(userState);

  useEffect(() => {
    latestStateRef.current = userState;
  }, [userState]);

  const packageDefaultsKey = useMemo(() => stableRecordKey(packageDefaults), [packageDefaults]);
  const inputIdsKey = useMemo(() => stableListKey(Array.from(inputIndex.keys())), [inputIndex]);
  const controlIdsKey = useMemo(() => stableListKey(validControlIds), [validControlIds]);
  const hasPackageContext = dashboardVersion !== "" || inputIndex.size > 0 || Object.keys(packageDefaults).length > 0;

  function cancelPendingSave() {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
  }

  useEffect(() => {
    let active = true;
    if (!hasPackageContext) {
      const initial = emptyState(workflowId, dashboardVersion);
      setUserState(initial);
      latestStateRef.current = initial;
      setLoaded(true);
      return () => {
        active = false;
        cancelPendingSave();
      };
    }
    setLoaded(false);
    fetchUserState(workflowId)
      .then((remote) => {
        if (!active) return;
        const pruned = pruneState(remote, packageDefaults, inputIndex, validControlIds, dashboardVersion);
        const merged: WorkflowUserState = {
          ...pruned,
          values: {
            ...packageDefaults,
            ...pruned.values,
          },
        };
        setUserState(merged);
        latestStateRef.current = merged;
        if (pruned.dashboard_version !== remote.dashboard_version) {
          void saveUserState(workflowId, merged);
        }
      })
      .catch(() => {
        if (!active) return;
        const initial: WorkflowUserState = {
          ...emptyState(workflowId, dashboardVersion),
          values: { ...packageDefaults },
        };
        setUserState(initial);
        latestStateRef.current = initial;
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
      cancelPendingSave();
    };
  }, [workflowId, dashboardVersion, packageDefaultsKey, inputIdsKey, controlIdsKey, hasPackageContext]);

  function scheduleSave(next: WorkflowUserState) {
    cancelPendingSave();
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      void saveUserState(workflowId, next);
    }, DEBOUNCE_MS);
  }

  const setValue = useCallback(
    (inputId: string, value: unknown) => {
      setUserState((current) => {
        const next = {
          ...current,
          values: { ...current.values, [inputId]: value },
        };
        latestStateRef.current = next;
        scheduleSave(next);
        return next;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workflowId],
  );

  const restoreDefaults = useCallback(async () => {
    cancelPendingSave();
    const restored = await deleteUserStateValues(workflowId);
    const next: WorkflowUserState = {
      ...restored,
      values: { ...packageDefaults, ...restored.values },
    };
    setUserState(next);
    latestStateRef.current = next;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, packageDefaults]);

  const setLayoutOverride = useCallback(
    async (controlId: string, layout: GridItemLayout) => {
      const override = { x: layout.x, y: layout.y, w: layout.w, h: layout.h };
      setUserState((current) => {
        const next = {
          ...current,
          layout_overrides: { ...current.layout_overrides, [controlId]: override },
        };
        latestStateRef.current = next;
        scheduleSave(next);
        return next;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workflowId],
  );

  const resetLayout = useCallback(async () => {
    cancelPendingSave();
    const cleared = await deleteUserStateLayout(workflowId);
    setUserState((current) => {
      const next = { ...current, layout_overrides: cleared.layout_overrides };
      latestStateRef.current = next;
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  const hasLayoutOverrides = Object.keys(userState.layout_overrides).length > 0;

  return {
    values: loaded ? userState.values : packageDefaults,
    setValue,
    restoreDefaults,
    layoutOverrides: userState.layout_overrides as Record<string, GridItemLayout>,
    setLayoutOverride,
    resetLayout,
    hasLayoutOverrides,
  };
}

function stableListKey(values: string[]): string {
  return [...values].sort().join("\u0000");
}

function stableRecordKey(values: Record<string, unknown>): string {
  try {
    return JSON.stringify(
      Object.keys(values)
        .sort()
        .map((key) => [key, values[key]]),
    );
  } catch {
    return stableListKey(Object.keys(values));
  }
}
