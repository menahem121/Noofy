import { useState } from "react";

import type { AppRouteId } from "./features/app/AppLayout";
import { EngineSettingsPage } from "./features/settings/EngineSettingsPage";
import { HomePage } from "./features/home/HomePage";
import { ModelsPage } from "./features/models/ModelsPage";
import { WorkflowRunPage } from "./features/workflows/WorkflowRunPage";

type AppRoute =
  | { name: "home" }
  | { name: "models" }
  | { name: "settings" }
  | { name: "workflow"; workflowId: string };

export default function App() {
  const [route, setRoute] = useState<AppRoute>({ name: "home" });

  function navigate(routeId: AppRouteId) {
    if (routeId === "settings") {
      setRoute({ name: "settings" });
      return;
    }
    if (routeId === "models") {
      setRoute({ name: "models" });
      return;
    }
    setRoute({ name: "home" });
  }

  if (route.name === "workflow") {
    return (
      <WorkflowRunPage
        workflowId={route.workflowId}
        onBack={() => setRoute({ name: "home" })}
        onNavigate={navigate}
      />
    );
  }

  if (route.name === "settings") {
    return <EngineSettingsPage onNavigate={navigate} />;
  }

  if (route.name === "models") {
    return <ModelsPage onNavigate={navigate} />;
  }

  return <HomePage onOpenWorkflow={(workflowId) => setRoute({ name: "workflow", workflowId })} onNavigate={navigate} />;
}
