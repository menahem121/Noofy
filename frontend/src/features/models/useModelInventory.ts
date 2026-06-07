import { useCallback, useRef, useState } from "react";

import { fetchModelInventory, type ModelInventoryResponse } from "../../lib/api/noofyApi";

export function useModelInventory() {
  const requestSequence = useRef(0);
  const [inventoryState, setInventoryState] = useState<{
    loading: boolean;
    inventory: ModelInventoryResponse | null;
    error: string | null;
  }>({ loading: true, inventory: null, error: null });

  const refreshInventory = useCallback(async (options: { silent?: boolean } = {}) => {
    const requestId = ++requestSequence.current;
    if (!options.silent) {
      setInventoryState((current) => ({ ...current, loading: true, error: null }));
    }
    try {
      const inventory = await fetchModelInventory();
      if (requestId !== requestSequence.current) return null;
      setInventoryState({ loading: false, inventory, error: null });
      return inventory;
    } catch (error) {
      if (requestId !== requestSequence.current) return null;
      setInventoryState((current) => ({
        loading: false,
        inventory: options.silent ? current.inventory : null,
        error: error instanceof Error ? error.message : String(error),
      }));
      return null;
    }
  }, []);

  return { inventoryState, refreshInventory };
}
