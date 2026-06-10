import type { LucideIcon } from "lucide-react";
import { Lock, Plus, Shuffle } from "lucide-react";

import type { SeedMode } from "./seedControl";

// Icon shown on the compact seed behaviour toggle, one per mode. Kept in a
// neutral module so the dashboard builder previews and the run-time control
// stay visually in sync without depending on each other's feature code.
export const SEED_MODE_ICONS: Record<SeedMode, LucideIcon> = {
  randomize: Shuffle,
  fixed: Lock,
  increment: Plus,
};
