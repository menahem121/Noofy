import { createContext, useContext, ReactNode, useState } from "react";
import {
  Coffee,
  FolderClock,
  Github,
  Images,
  Layers,
  Library,
  Loader2,
  Menu,
  PackageOpen,
  Settings,
  Square,
} from "lucide-react";

import { type MachineResourceSnapshot, type ResourceMetric } from "../../lib/api/noofyApi";
import { NOOFY_GITHUB_REPO_URL } from "../../lib/noofyLinks";
import { openExternalUrl } from "../../lib/openExternalUrl";
import { useOptionalResourceStatus } from "./ResourceStatusProvider";
import { useOptionalRuntimeStatus } from "./RuntimeStatusProvider";
import { WorkflowTabsTopBar, useOptionalWorkflowTabs, type WorkflowTabRuntimeState } from "./WorkflowTabs";

// Replace with your real Tipeee / donation URL when ready.
const SUPPORT_URL = "https://example.com/buy-me-a-coffee";

export type AppRouteId = "home" | "workflows" | "history" | "models" | "gallery" | "settings";
export interface AppNavigateOptions {
  workflowSearch?: string;
}
export type StatusTone = "success" | "warning" | "error" | "info";

export interface AppStatusView {
  label: string;
  description: string;
  tone: StatusTone;
  loading?: boolean;
}

export interface AppTopBarProgress {
  percent: number;
  remainingCount?: number;
  onCancelRemaining?: () => void;
  cancelRemainingTitle?: string;
}

interface AppLayoutProps {
  activeRoute: AppRouteId | null;
  children: ReactNode;
  onNavigate: (route: AppRouteId, options?: AppNavigateOptions) => void;
  mainClassName?: string;
  contentClassName?: string;
  progress?: AppTopBarProgress | null;
}

const navItems = [
  { id: "home", label: "Home", Icon: Library },
  { id: "workflows", label: "Workflows", Icon: PackageOpen },
  { id: "models", label: "Models", Icon: Layers },
  { id: "history", label: "History", Icon: FolderClock },
  { id: "gallery", label: "Gallery", Icon: Images },
] satisfies Array<{ id: AppRouteId; label: string; Icon: typeof Library }>;

const globalProgressStatuses = new Set(["queued", "running", "queued_pending_memory"]);

interface SidebarContextValue {
  sidebarOpen: boolean;
  setSidebarOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

const SidebarContext = createContext<SidebarContextValue>({
  sidebarOpen: true,
  setSidebarOpen: () => {},
});

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  return <SidebarContext.Provider value={{ sidebarOpen, setSidebarOpen }}>{children}</SidebarContext.Provider>;
}

export function AppLayout({
  activeRoute,
  children,
  onNavigate,
  mainClassName = "",
  contentClassName = "",
  progress = null,
}: AppLayoutProps) {
  const { sidebarOpen, setSidebarOpen } = useContext(SidebarContext);
  const runtimeStatus = useOptionalRuntimeStatus();
  const runtimeStatusView = runtimeStatus?.statusView ?? {
    label: "Checking Noofy",
    description: "Connecting to the local service",
    tone: "info",
    loading: true,
  };
  const isHome = activeRoute === "home";
  const effectiveOpen = isHome ? true : sidebarOpen;
  const resources = useOptionalResourceStatus()?.snapshot ?? null;
  const globalProgress = useGlobalWorkflowProgress();
  const effectiveProgress = progress ?? globalProgress;
  const effectiveStatus = statusViewForWorkflowActivity(runtimeStatusView, effectiveProgress);
  const remainingTitle = effectiveProgress?.remainingCount === 1
    ? "1 run remaining"
    : effectiveProgress?.remainingCount
      ? `${effectiveProgress.remainingCount} runs remaining`
      : null;

  function handleToggle() {
    if (!isHome) setSidebarOpen((o) => !o);
  }

  return (
    <div className={effectiveOpen ? "app-shell" : "app-shell app-shell--sidebar-closed"}>
      <header className="topbar">
        <div className="topbar__brand">
          <button
            className={`icon-button topbar__menu${isHome ? " topbar__menu--disabled" : ""}`}
            type="button"
            aria-label={effectiveOpen ? "Close navigation" : "Open navigation"}
            title={isHome ? "Navigation is always open on the home page" : effectiveOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={effectiveOpen}
            aria-disabled={isHome}
            onClick={handleToggle}
          >
            <Menu size={20} aria-hidden="true" />
          </button>
          <button className="brand-button" type="button" onClick={() => onNavigate("home")} aria-label="Go to home">
            <span className="brand-mark" aria-hidden="true">
              <img src="/assets/brand/noofy-app-icon.png" alt="" />
            </span>
            <span className="brand-name">Noofy</span>
          </button>
        </div>

        <WorkflowTabsTopBar />

        <div className="topbar__actions">
          <ResourceMonitor
            snapshot={resources}
            progress={effectiveProgress}
            remainingTitle={remainingTitle}
          />
          <div className={`status-pill status-pill--${effectiveStatus.tone}`}>
            {effectiveStatus.loading ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <span />}
            <span>{effectiveStatus.label}</span>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Open settings"
            title="Open settings"
            onClick={() => onNavigate("settings")}
          >
            <Settings size={19} aria-hidden="true" />
          </button>
        </div>

        {effectiveProgress ? (
          <div className="topbar-progress">
            <div
              className="topbar-progress__track"
              role="progressbar"
              aria-label="Workflow progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={effectiveProgress.percent}
            >
              <span style={{ width: `${effectiveProgress.percent}%` }} />
            </div>
            <span className="topbar-progress__value">{effectiveProgress.percent}%</span>
          </div>
        ) : null}
      </header>

      <aside className="sidebar" aria-hidden={!effectiveOpen}>
        <div className="sidebar__inner">
          <nav className="sidebar-nav" aria-label="Main navigation">
            {navItems.map(({ id, label, Icon }) => (
              <button
                className={activeRoute === id ? "sidebar-nav__item sidebar-nav__item--active" : "sidebar-nav__item"}
                type="button"
                key={id}
                onClick={() => onNavigate(id)}
              >
                <Icon size={19} aria-hidden="true" />
                <span>{label}</span>
              </button>
            ))}
          </nav>

          <div className="sidebar__spacer" />

          <div className="support-card">
            <div className="support-card__header">
              <Coffee size={15} aria-hidden="true" />
              <span>Buy Me a Coffee</span>
            </div>
            <p>Help me build Noofy (And maybe buy a computer that can run it lol)</p>
            <button
              className="secondary-button secondary-button--full"
              type="button"
              onClick={() => void openExternalUrl(SUPPORT_URL)}
            >
              Support Noofy
            </button>
          </div>

          <button
            className="workspace-card workspace-card--github"
            type="button"
            onClick={() => void openExternalUrl(NOOFY_GITHUB_REPO_URL)}
            aria-label="Open Noofy on GitHub"
          >
            <div className="workspace-card__avatar" aria-hidden="true">
              <Github size={21} />
            </div>
            <div>
              <p>Noofy on GitHub</p>
              <span>View source & updates</span>
            </div>
          </button>

          <p className="sidebar__version">Noofy v{__APP_VERSION__}</p>
        </div>
      </aside>

      <main className={`main-workspace${mainClassName ? ` ${mainClassName}` : ""}`}>
        <div className={`workspace-content${contentClassName ? ` ${contentClassName}` : ""}`}>{children}</div>
      </main>
    </div>
  );
}

function statusViewForWorkflowActivity(
  statusView: AppStatusView,
  progress: AppTopBarProgress | null,
): AppStatusView {
  if (!progress) return statusView;
  return {
    label: "Working",
    description: "A workflow is running",
    tone: "info",
    loading: true,
  };
}

function useGlobalWorkflowProgress(): AppTopBarProgress | null {
  const workflowTabs = useOptionalWorkflowTabs();
  if (!workflowTabs) return null;

  const activeRuntime = Object.values(workflowTabs.runtimeByWorkflowId)
    .filter((runtime) => runtimeTopBarProgress(runtime) !== null)
    .sort((a, b) => (b.activeJobUpdatedAt ?? 0) - (a.activeJobUpdatedAt ?? 0))[0];

  return activeRuntime ? runtimeTopBarProgress(activeRuntime) : null;
}

function runtimeTopBarProgress(runtime: WorkflowTabRuntimeState): AppTopBarProgress | null {
  if (!runtime.activeJobStatus || !globalProgressStatuses.has(runtime.activeJobStatus)) return null;
  return {
    percent: jobProgressPercent(runtime.activeJobProgress, runtime.activeJobStatus),
  };
}

function jobProgressPercent(
  progress: WorkflowTabRuntimeState["activeJobProgress"],
  status: WorkflowTabRuntimeState["activeJobStatus"],
) {
  if (progress?.value !== null && progress?.value !== undefined && progress.max) {
    return Math.min(100, Math.round((progress.value / progress.max) * 100));
  }
  return status === "completed" ? 100 : 0;
}

function ResourceMonitor({
  snapshot,
  progress,
  remainingTitle,
}: {
  snapshot: MachineResourceSnapshot | null;
  progress: AppTopBarProgress | null;
  remainingTitle: string | null;
}) {
  const showRunControls = Boolean(progress?.remainingCount);

  return (
    <div className={`resource-monitor${showRunControls ? " resource-monitor--with-runs" : ""}`} aria-label="Resource monitor">
      {showRunControls && progress?.remainingCount ? (
        <div className="resource-monitor__runs" aria-label="Active workflow runs">
          <span
            className="resource-monitor__run-count"
            title={remainingTitle ?? undefined}
          >
            {progress.remainingCount}
          </span>
          {progress.onCancelRemaining ? (
            <button
              className="resource-monitor__run-stop"
              type="button"
              aria-label={progress.cancelRemainingTitle ?? "Cancel current run and all queued runs for this workflow"}
              title={progress.cancelRemainingTitle ?? "Cancel current run and all queued runs for this workflow"}
              onClick={progress.onCancelRemaining}
            >
              <Square size={10} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      ) : null}
      <ResourceMonitorItem label="CPU" metric={snapshot?.cpu ?? null} value={formatPercent(snapshot?.cpu ?? null)} />
      <ResourceMonitorItem label="RAM" metric={snapshot?.ram ?? null} value={formatMemory(snapshot?.ram ?? null)} />
      <ResourceMonitorItem label="VRAM" metric={snapshot?.vram ?? null} value={formatMemory(snapshot?.vram ?? null)} />
    </div>
  );
}

function ResourceMonitorItem({
  label,
  metric,
  value,
}: {
  label: string;
  metric: ResourceMetric | null;
  value: string;
}) {
  const percent = metric?.available && metric.percent !== null ? Math.round(metric.percent) : null;
  const usageClass = percent !== null && percent >= 90 ? " resource-monitor__item--high" : "";
  const title = resourceTooltip(label, metric);

  return (
    <div className={`resource-monitor__item${usageClass}`} title={title}>
      <span className="resource-monitor__text">
        <span className="resource-monitor__label">{label}</span>
        <span className="resource-monitor__value">{value}</span>
      </span>
      <span className="resource-monitor__bar" aria-hidden="true">
        <span style={{ width: `${percent ?? 0}%` }} />
      </span>
    </div>
  );
}

function formatPercent(metric: ResourceMetric | null) {
  if (!metric?.available || metric.percent === null) return "—";
  return `${Math.round(metric.percent)}%`;
}

function formatMemory(metric: ResourceMetric | null) {
  if (!metric?.available) return "—";
  if (metric.used_mb !== null && metric.total_mb !== null) {
    return `${formatGb(metric.used_mb)} / ${formatGb(metric.total_mb)} GB`;
  }
  if (metric.total_mb !== null) return `— / ${formatGb(metric.total_mb)} GB`;
  if (metric.free_mb !== null) return `${formatGb(metric.free_mb)} GB free`;
  return "—";
}

function formatGb(mb: number) {
  const gb = mb / 1024;
  return gb >= 10 ? String(Math.round(gb)) : gb.toFixed(1);
}

function resourceTooltip(label: string, metric: ResourceMetric | null) {
  if (!metric?.available) return `${label} usage unavailable`;
  const source = metric.source ? `Source: ${metric.source}` : "Source: Reported by Noofy";
  if (metric.used_mb !== null && metric.total_mb !== null && metric.free_mb !== null) {
    return `${label}: ${formatGb(metric.used_mb)} GB used, ${formatGb(metric.free_mb)} GB free of ${formatGb(metric.total_mb)} GB. ${source}`;
  }
  if (metric.percent !== null) return `${label}: ${Math.round(metric.percent)}%. ${source}`;
  return `${label}: available. ${source}`;
}
