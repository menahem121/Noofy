import { useState } from "react";

import type { AppRouteId } from "./features/app/AppLayout";
import { SidebarProvider } from "./features/app/AppLayout";
import { RuntimeStatusProvider } from "./features/app/RuntimeStatusProvider";
import type { DashboardSchema } from "./features/dashboard-builder/dashboardBuilderContent";
import { DashboardBuilderPage } from "./features/dashboard-builder/DashboardBuilderPage";
import { DashboardBuilderLayoutPage } from "./features/dashboard-builder/DashboardBuilderLayoutPage";
import { EngineSettingsPage } from "./features/settings/EngineSettingsPage";
import { GalleryPage } from "./features/gallery/GalleryPage";
import { HistoryPage } from "./features/history/HistoryPage";
import { HomePage } from "./features/home/HomePage";
import { WorkflowLibraryProvider } from "./features/home/WorkflowLibraryProvider";
import { ModelsPage } from "./features/models/ModelsPage";
import { WorkflowRunPage } from "./features/workflows/WorkflowRunPage";

type AppRoute =
  | { name: "home" }
  | { name: "gallery" }
  | { name: "history" }
  | { name: "models" }
  | { name: "settings" }
  | { name: "workflow"; workflowId: string }
  | { name: "dashboard-builder"; workflowId?: string; workflowName?: string; initialSchema?: DashboardSchema }
  | { name: "dashboard-builder-layout"; workflowId?: string; workflowName?: string; initialSchema?: DashboardSchema };

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
    if (routeId === "gallery") {
      setRoute({ name: "gallery" });
      return;
    }
    if (routeId === "history") {
      setRoute({ name: "history" });
      return;
    }
    setRoute({ name: "home" });
  }

  function renderPage() {
    if (route.name === "workflow") {
      return (
        <WorkflowRunPage
          workflowId={route.workflowId}
          onBack={() => setRoute({ name: "home" })}
          onEditWidgets={(schema) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: schema.workflowId,
              workflowName: schema.workflowName,
              initialSchema: schema,
            })
          }
          onNavigate={navigate}
        />
      );
    }

    if (route.name === "dashboard-builder") {
      return (
        <DashboardBuilderPage
          workflowId={route.workflowId}
          workflowName={route.workflowName}
          initialSchema={route.initialSchema}
          onBack={() => setRoute({ name: "home" })}
          onContinue={(schema) =>
            setRoute({
              name: "dashboard-builder-layout",
              workflowId: route.workflowId,
              workflowName: route.workflowName,
              initialSchema: schema,
            })
          }
          onNavigate={navigate}
        />
      );
    }

    if (route.name === "dashboard-builder-layout") {
      return (
        <DashboardBuilderLayoutPage
          workflowId={route.workflowId}
          workflowName={route.workflowName}
          initialSchema={route.initialSchema}
          onBackToWidgets={(schema) =>
            setRoute({
              name: "dashboard-builder",
              workflowId: route.workflowId,
              workflowName: route.workflowName,
              initialSchema: schema,
            })
          }
          onSaveComplete={(workflowId) => setRoute({ name: "workflow", workflowId })}
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

    if (route.name === "gallery") {
      return <GalleryPage onNavigate={navigate} />;
    }

    if (route.name === "history") {
      return <HistoryPage onNavigate={navigate} />;
    }

    return (
      <HomePage
        onOpenWorkflow={(workflowId) => setRoute({ name: "workflow", workflowId })}
        onConfigureDashboard={(workflowId, workflowName) =>
          setRoute({
            name: "dashboard-builder",
            workflowId: workflowId ?? undefined,
            workflowName: workflowName ?? undefined,
          })
        }
        onNavigate={navigate}
      />
    );
  }

  return (
    <RuntimeStatusProvider>
      <WorkflowLibraryProvider>
        <SidebarProvider>{renderPage()}</SidebarProvider>
      </WorkflowLibraryProvider>
    </RuntimeStatusProvider>
  );
}
