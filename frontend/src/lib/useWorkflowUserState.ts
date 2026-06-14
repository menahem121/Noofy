import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  deleteUserStateLayout,
  deleteUserStateValues,
  fetchUserState,
  saveUserState,
  type WorkflowInputDef,
  type WorkflowUserState,
  type OutputPreference,
  type OutputPreferences,
  type UserStateActionBarPosition,
} from "./api/noofyApi";
import type { GridItemLayout } from "./gridLayout";

const DEBOUNCE_MS = 600;
const EMPTY_CONTROL_IDS: string[] = [];
const EMPTY_VALUES: Record<string, unknown> = {};
const EMPTY_LAYOUT_OVERRIDES: Record<string, GridItemLayout> = {};
const EMPTY_OUTPUT_PREFERENCES: OutputPreferences = {};

interface WorkflowUserStateCacheEntry {
  state: WorkflowUserState;
  revision: number;
  dirtyRevision: number | null;
}

interface PendingWorkflowUserStateSave {
  workflowId: string;
  state: WorkflowUserState;
  revision: number;
}

const workflowUserStateCache = new Map<string, WorkflowUserStateCacheEntry>();

function emptyState(workflowId: string, dashboardVersion: string): WorkflowUserState {
  return {
    schema_version: "1",
    workflow_id: workflowId,
    dashboard_version: dashboardVersion,
    values: {},
    layout_overrides: {},
    presentation_overrides: {},
    output_preferences: {},
  };
}

function pruneState(
  state: WorkflowUserState,
  packageDefaults: Record<string, unknown>,
  inputIndex: Map<string, WorkflowInputDef>,
  validLayoutIds: string[],
  validOutputControlIds: string[],
  currentDashboardVersion: string,
): WorkflowUserState {
  if (state.dashboard_version === currentDashboardVersion) return state;

  const validInputIds = new Set(inputIndex.keys());
  const validLayoutIdSet = new Set(validLayoutIds);
  const validOutputControlIdSet = new Set(validOutputControlIds);
  const prunedValues: Record<string, unknown> = {};
  for (const id of validInputIds) {
    prunedValues[id] = packageDefaults[id];
  }

  const prunedOverrides: Record<string, { x: number; y: number; w: number; h: number }> = {};
  for (const [id, override] of Object.entries(state.layout_overrides)) {
    if (validLayoutIdSet.has(id)) prunedOverrides[id] = override;
  }
  const prunedOutputPreferences: OutputPreferences = {};
  for (const [id, preference] of Object.entries(state.output_preferences ?? {})) {
    if (validOutputControlIdSet.has(id)) prunedOutputPreferences[id] = preference;
  }
  const presentationOverrides = state.presentation_overrides ?? {};

  return {
    ...state,
    dashboard_version: currentDashboardVersion,
    values: prunedValues,
    layout_overrides: prunedOverrides,
    presentation_overrides: presentationOverrides,
    output_preferences: prunedOutputPreferences,
  };
}

function mergeCurrentContext(
  state: WorkflowUserState,
  packageDefaults: Record<string, unknown>,
  inputIndex: Map<string, WorkflowInputDef>,
  validLayoutIds: string[],
  validOutputControlIds: string[],
  currentDashboardVersion: string,
): WorkflowUserState {
  const values: Record<string, unknown> = {};
  for (const id of inputIndex.keys()) {
    values[id] = Object.prototype.hasOwnProperty.call(state.values, id)
      ? state.values[id]
      : packageDefaults[id];
  }

  const validLayoutIdSet = new Set(validLayoutIds);
  const layoutOverrides = Object.fromEntries(
    Object.entries(state.layout_overrides).filter(([id]) => validLayoutIdSet.has(id)),
  );
  const validOutputControlIdSet = new Set(validOutputControlIds);
  const outputPreferences = Object.fromEntries(
    Object.entries(state.output_preferences ?? {}).filter(([id]) => validOutputControlIdSet.has(id)),
  );

  if (
    state.dashboard_version === currentDashboardVersion &&
    sameRecord(state.values, values) &&
    sameRecord(state.layout_overrides, layoutOverrides) &&
    sameRecord(state.output_preferences ?? {}, outputPreferences)
  ) {
    return state;
  }

  return {
    ...state,
    dashboard_version: currentDashboardVersion,
    values,
    layout_overrides: layoutOverrides,
    output_preferences: outputPreferences,
  };
}

function sameRecord(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every((key) => Object.prototype.hasOwnProperty.call(right, key) && Object.is(left[key], right[key]))
  );
}

export function useWorkflowUserState(
  workflowId: string,
  packageDefaults: Record<string, unknown>,
  dashboardVersion: string,
  inputIndex: Map<string, WorkflowInputDef>,
  validLayoutIds: string[] = EMPTY_CONTROL_IDS,
  validOutputControlIds: string[] = validLayoutIds,
) {
  const packageDefaultsKey = useMemo(() => stableRecordKey(packageDefaults), [packageDefaults]);
  const inputIdsKey = useMemo(() => stableListKey(Array.from(inputIndex.keys())), [inputIndex]);
  const layoutIdsKey = useMemo(() => stableListKey(validLayoutIds), [validLayoutIds]);
  const outputControlIdsKey = useMemo(() => stableListKey(validOutputControlIds), [validOutputControlIds]);
  const hasPackageContext = dashboardVersion !== "" || inputIndex.size > 0 || Object.keys(packageDefaults).length > 0;
  const contextKey = [
    workflowId,
    dashboardVersion,
    packageDefaultsKey,
    inputIdsKey,
    layoutIdsKey,
    outputControlIdsKey,
  ].join("\u0001");
  const [userState, setUserState] = useState<WorkflowUserState>(() =>
    cachedStateForContext(
      workflowId,
      packageDefaults,
      inputIndex,
      validLayoutIds,
      validOutputControlIds,
      dashboardVersion,
      hasPackageContext,
    ) ?? emptyState(workflowId, dashboardVersion),
  );
  const [loadedWorkflowId, setLoadedWorkflowId] = useState<string | null>(() =>
    cachedStateForContext(
      workflowId,
      packageDefaults,
      inputIndex,
      validLayoutIds,
      validOutputControlIds,
      dashboardVersion,
      hasPackageContext,
    )
      ? workflowId
      : null,
  );
  const saveTimerRef = useRef<number | null>(null);
  const pendingSaveRef = useRef<PendingWorkflowUserStateSave | null>(null);
  const fetchStartedForRef = useRef<string | null>(null);
  const latestStateRef = useRef<WorkflowUserState>(userState);

  useEffect(() => {
    latestStateRef.current = userState;
  }, [userState]);

  const loaded = !hasPackageContext || loadedWorkflowId === workflowId;

  function cancelPendingSave() {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    pendingSaveRef.current = null;
  }

  useLayoutEffect(() => {
    const capturedWorkflowId = workflowId;
    return () => {
      flushPendingSave(capturedWorkflowId);
    };
  // Flush pending edits before a workflow switch hydrates another tab.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  useLayoutEffect(() => {
    if (!hasPackageContext) return;
    const cached = cachedStateForContext(
      workflowId,
      packageDefaults,
      inputIndex,
      validLayoutIds,
      validOutputControlIds,
      dashboardVersion,
      hasPackageContext,
    );
    if (!cached) return;

    const cachedEntry = workflowUserStateCache.get(workflowId);
    const shouldPersistMigration = Boolean(
      cachedEntry && cached.dashboard_version !== cachedEntry.state.dashboard_version,
    );
    if (cachedEntry?.state !== cached) {
      writeCachedState(workflowId, cached, {
        dirty: shouldPersistMigration,
        preserveDirty: true,
      });
    }
    setUserState(cached);
    latestStateRef.current = cached;
    setLoadedWorkflowId(workflowId);
    if (shouldPersistMigration) {
      scheduleSave(cached);
    }
  // Cache hydration must happen before paint on workflow tab switches.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey, hasPackageContext, workflowId]);

  useEffect(() => {
    let active = true;
    const capturedWorkflowId = workflowId;
    if (!hasPackageContext) {
      const initial = emptyState(workflowId, dashboardVersion);
      setUserState(initial);
      latestStateRef.current = initial;
      fetchStartedForRef.current = null;
      return () => {
        active = false;
        flushPendingSave(capturedWorkflowId);
      };
    }
    const cached = cachedStateForContext(
      workflowId,
      packageDefaults,
      inputIndex,
      validLayoutIds,
      validOutputControlIds,
      dashboardVersion,
      hasPackageContext,
    );
    if (cached) {
      setUserState(cached);
      latestStateRef.current = cached;
      setLoadedWorkflowId(workflowId);
    }
    if (fetchStartedForRef.current === workflowId) {
      return () => {
        active = false;
        flushPendingSave(capturedWorkflowId);
      };
    }
    fetchStartedForRef.current = workflowId;
    const fetchBaselineRevision = workflowUserStateCache.get(workflowId)?.revision ?? 0;
    fetchUserState(workflowId)
      .then((remote) => {
        if (!active) return;
        if (!canApplyFetchedState(capturedWorkflowId, fetchBaselineRevision)) return;
        const pruned = pruneState(
          remote,
          packageDefaults,
          inputIndex,
          validLayoutIds,
          validOutputControlIds,
          dashboardVersion,
        );
        const merged: WorkflowUserState = {
          ...pruned,
          values: {
            ...packageDefaults,
            ...pruned.values,
          },
        };
        setUserState(merged);
        latestStateRef.current = merged;
        writeCachedState(capturedWorkflowId, merged, { dirty: false, clearDirty: true });
        if (pruned.dashboard_version !== remote.dashboard_version) {
          scheduleSave(merged);
        }
      })
      .catch(() => {
        if (!active) return;
        if (cached) {
          setUserState(cached);
          latestStateRef.current = cached;
          return;
        }
        const initial: WorkflowUserState = {
          ...emptyState(workflowId, dashboardVersion),
          values: { ...packageDefaults },
          presentation_overrides: {},
          output_preferences: {},
        };
        setUserState(initial);
        latestStateRef.current = initial;
        writeCachedState(capturedWorkflowId, initial, { dirty: false, clearDirty: true });
      })
      .finally(() => {
        if (active) setLoadedWorkflowId(capturedWorkflowId);
      });
    return () => {
      active = false;
      flushPendingSave(capturedWorkflowId);
    };
  // Package details can change while the Run page remains mounted. Fetch the
  // persisted state once; subsequent package changes are merged locally below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, hasPackageContext]);

  useEffect(() => {
    if (!hasPackageContext || loadedWorkflowId !== workflowId) return;
    setUserState((current) => {
      const next = mergeCurrentContext(
        current,
        packageDefaults,
        inputIndex,
        validLayoutIds,
        validOutputControlIds,
        dashboardVersion,
      );
      if (next === current) return current;
      latestStateRef.current = next;
      if (next.dashboard_version !== current.dashboard_version) {
        scheduleSave(next);
      } else {
        writeCachedState(workflowId, next, { dirty: false, preserveDirty: true });
      }
      return next;
    });
  // loadedWorkflowId is required so a context that changed during the initial
  // request is reconciled immediately after that request completes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey, loadedWorkflowId, hasPackageContext, workflowId]);

  function scheduleSave(next: WorkflowUserState) {
    cancelPendingSave();
    latestStateRef.current = next;
    writeCachedState(workflowId, next, { dirty: true });
    const pendingSave = {
      workflowId,
      state: next,
      revision: workflowUserStateCache.get(workflowId)?.revision ?? 0,
    };
    pendingSaveRef.current = pendingSave;
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      if (pendingSaveRef.current === pendingSave) {
        pendingSaveRef.current = null;
      }
      void saveUserState(pendingSave.workflowId, pendingSave.state)
        .then(() => markCachedStateSaved(pendingSave.workflowId, pendingSave.revision))
        .catch(() => undefined);
    }, DEBOUNCE_MS);
  }

  function flushPendingSave(targetWorkflowId: string) {
    if (saveTimerRef.current === null) return;
    window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = null;
    const pendingSave = pendingSaveRef.current;
    pendingSaveRef.current = null;
    if (!pendingSave || pendingSave.workflowId !== targetWorkflowId) return;
    void saveUserState(pendingSave.workflowId, pendingSave.state)
      .then(() => markCachedStateSaved(pendingSave.workflowId, pendingSave.revision))
      .catch(() => undefined);
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
    writeCachedState(workflowId, next, { dirty: false, clearDirty: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, packageDefaults, contextKey]);

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

  const setOutputPreference = useCallback(
    (controlId: string, preference: OutputPreference) => {
      setUserState((current) => {
        const next = {
          ...current,
          output_preferences: {
            ...(current.output_preferences ?? {}),
            [controlId]: preference,
          },
        };
        latestStateRef.current = next;
        scheduleSave(next);
        return next;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workflowId],
  );

  const getOutputPreferencesSnapshot = useCallback(
    () => ({ ...(latestStateRef.current.output_preferences ?? {}) }),
    [],
  );

  const setActionBarPositionOverride = useCallback(
    async (position: UserStateActionBarPosition) => {
      const override = {
        x: Math.max(0, Math.round(position.x)),
        y: Math.max(0, Math.round(position.y)),
      };
      setUserState((current) => {
        const next = {
          ...current,
          presentation_overrides: {
            ...(current.presentation_overrides ?? {}),
            action_bar: override,
          },
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
      const next = {
        ...current,
        layout_overrides: cleared.layout_overrides,
        presentation_overrides: cleared.presentation_overrides ?? {},
      };
      latestStateRef.current = next;
      writeCachedState(workflowId, next, { dirty: false, clearDirty: true });
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, contextKey]);

  const hasLayoutOverrides = Object.keys(userState.layout_overrides).length > 0;

  return {
    loaded,
    values: loaded ? userState.values : EMPTY_VALUES,
    setValue,
    restoreDefaults,
    layoutOverrides: loaded
      ? userState.layout_overrides as Record<string, GridItemLayout>
      : EMPTY_LAYOUT_OVERRIDES,
    setLayoutOverride,
    outputPreferences: loaded
      ? userState.output_preferences ?? EMPTY_OUTPUT_PREFERENCES
      : EMPTY_OUTPUT_PREFERENCES,
    setOutputPreference,
    getOutputPreferencesSnapshot,
    actionBarPositionOverride: loaded
      ? userState.presentation_overrides?.action_bar ?? null
      : null,
    setActionBarPositionOverride,
    resetLayout,
    hasLayoutOverrides: loaded && hasLayoutOverrides,
  };
}

function cachedStateForContext(
  workflowId: string,
  packageDefaults: Record<string, unknown>,
  inputIndex: Map<string, WorkflowInputDef>,
  validLayoutIds: string[],
  validOutputControlIds: string[],
  dashboardVersion: string,
  hasPackageContext: boolean,
): WorkflowUserState | null {
  if (!hasPackageContext) return null;
  const cached = workflowUserStateCache.get(workflowId);
  if (!cached) return null;
  return mergeCurrentContext(
    cached.state,
    packageDefaults,
    inputIndex,
    validLayoutIds,
    validOutputControlIds,
    dashboardVersion,
  );
}

function writeCachedState(
  workflowId: string,
  state: WorkflowUserState,
  options: { dirty: boolean; preserveDirty?: boolean; clearDirty?: boolean },
) {
  const current = workflowUserStateCache.get(workflowId);
  const revision = (current?.revision ?? 0) + 1;
  let dirtyRevision: number | null;
  if (options.clearDirty) {
    dirtyRevision = null;
  } else if (options.dirty) {
    dirtyRevision = revision;
  } else if (options.preserveDirty) {
    dirtyRevision = current?.dirtyRevision ?? null;
  } else {
    dirtyRevision = null;
  }
  workflowUserStateCache.set(workflowId, {
    state,
    revision,
    dirtyRevision,
  });
}

function markCachedStateSaved(workflowId: string, savedRevision: number) {
  const current = workflowUserStateCache.get(workflowId);
  if (!current || current.revision !== savedRevision || current.dirtyRevision === null) return;
  workflowUserStateCache.set(workflowId, {
    ...current,
    dirtyRevision: null,
  });
}

function canApplyFetchedState(workflowId: string, fetchBaselineRevision: number) {
  const current = workflowUserStateCache.get(workflowId);
  if (!current) return true;
  return current.revision === fetchBaselineRevision && current.dirtyRevision === null;
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

export function __resetWorkflowUserStateCacheForTests() {
  workflowUserStateCache.clear();
}

export function invalidateWorkflowUserStateCache(workflowId: string) {
  workflowUserStateCache.delete(workflowId);
}
