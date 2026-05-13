import { useCallback, useState } from "react";

import { fetchModelInventory, type ModelInventoryResponse } from "../../lib/api/noofyApi";

export function useModelInventory() {
  const [inventoryState, setInventoryState] = useState<{
    loading: boolean;
    inventory: ModelInventoryResponse | null;
    error: string | null;
  }>({ loading: true, inventory: null, error: null });

  const refreshInventory = useCallback(async (options: { silent?: boolean } = {}) => {
    if (!options.silent) {
      setInventoryState((current) => ({ ...current, loading: true, error: null }));
    }
    try {
      const inventory = await fetchModelInventory();
      setInventoryState({ loading: false, inventory, error: null });
      return inventory;
    } catch (error) {
      setInventoryState({
        loading: false,
        inventory: null,
        error: error instanceof Error ? error.message : String(error),
      });
      return null;
    }
  }, []);

  return { inventoryState, refreshInventory };
}
