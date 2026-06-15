export interface PendingImportedSetup {
  workflowId: string;
  workflowName: string;
  dismissed: boolean;
}

export const PENDING_IMPORTED_SETUP_STORAGE_KEY = "noofy.pendingImportedSetupBanners.v1";

export function loadPendingImportedSetups(): PendingImportedSetup[] {
  try {
    const raw = window.localStorage.getItem(PENDING_IMPORTED_SETUP_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    const seen = new Set<string>();
    return parsed.filter((item): item is PendingImportedSetup => {
      if (
        !item ||
        typeof item !== "object" ||
        typeof item.workflowId !== "string" ||
        !item.workflowId ||
        typeof item.workflowName !== "string" ||
        !item.workflowName ||
        typeof item.dismissed !== "boolean" ||
        seen.has(item.workflowId)
      ) {
        return false;
      }
      seen.add(item.workflowId);
      return true;
    });
  } catch {
    return [];
  }
}

export function removePendingImportedSetupReminder(workflowId: string) {
  const next = loadPendingImportedSetups().filter((item) => item.workflowId !== workflowId);
  savePendingImportedSetups(next);
}

export function addPendingImportedSetupReminder(workflowId: string, workflowName: string) {
  const pendingSetup = { workflowId, workflowName, dismissed: false };
  const current = loadPendingImportedSetups();
  savePendingImportedSetups([
    pendingSetup,
    ...current.filter((item) => item.workflowId !== workflowId),
  ]);
}

export function dismissPendingImportedSetupReminder(workflowId: string) {
  const current = loadPendingImportedSetups();
  savePendingImportedSetups(
    current.map((item) => (
      item.workflowId === workflowId ? { ...item, dismissed: true } : item
    )),
  );
}

export function savePendingImportedSetups(setups: PendingImportedSetup[]) {
  try {
    if (setups.length === 0) {
      window.localStorage.removeItem(PENDING_IMPORTED_SETUP_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(PENDING_IMPORTED_SETUP_STORAGE_KEY, JSON.stringify(setups));
  } catch {
    // Banner state can remain in memory when browser storage is unavailable.
  }
}
