import { createContext, useContext, ReactNode, useEffect, useRef, useState } from "react";
import {
  Coffee,
  FolderClock,
  Images,
  Layers,
  Library,
  Loader2,
  Menu,
  PackageOpen,
  Settings,
  ShieldCheck,
} from "lucide-react";

import { fetchResourceSnapshot, type MachineResourceSnapshot, type ResourceMetric } from "../../lib/api/noofyApi";
import { openExternalUrl } from "../../lib/openExternalUrl";
import { useOptionalRuntimeStatus } from "./RuntimeStatusProvider";

// Replace with your real Tipeee / donation URL when ready.
const SUPPORT_URL = "https://example.com/buy-me-a-coffee";

export type AppRouteId = "home" | "workflows" | "history" | "models" | "gallery" | "settings";
export type StatusTone = "success" | "warning" | "error" | "info";

export interface AppStatusView {
  label: string;
  description: string;
  tone: StatusTone;
  loading?: boolean;
}

export interface AppTopBarProgress {
  percent: number;
}

interface AppLayoutProps {
  activeRoute: AppRouteId;
  status?: AppStatusView;
  children: ReactNode;
  onNavigate: (route: AppRouteId) => void;
  mainClassName?: string;
  contentClassName?: string;
  progress?: AppTopBarProgress | null;
}

const navItems = [
  { id: "home", label: "Home", Icon: Library },
  { id: "workflows", label: "Workflows", Icon: PackageOpen },
  { id: "history", label: "History", Icon: FolderClock },
  { id: "models", label: "Models", Icon: Layers },
  { id: "gallery", label: "Gallery", Icon: Images },
] satisfies Array<{ id: AppRouteId; label: string; Icon: typeof Library }>;

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
  status,
  children,
  onNavigate,
  mainClassName = "",
  contentClassName = "",
  progress = null,
}: AppLayoutProps) {
  const { sidebarOpen, setSidebarOpen } = useContext(SidebarContext);
  const runtimeStatus = useOptionalRuntimeStatus();
  const effectiveStatus = status ?? runtimeStatus?.statusView ?? {
    label: "Checking backend",
    description: "Looking for the local app service",
    tone: "info",
    loading: true,
  };
  const isHome = activeRoute === "home";
  const effectiveOpen = isHome ? true : sidebarOpen;
  const resources = useTopBarResources();

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
              <ShieldCheck size={17} />
            </span>
            <span className="brand-name">Noofy</span>
          </button>
        </div>

        <div className="topbar__actions">
          <ResourceMonitor snapshot={resources} />
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

        {progress ? (
          <div className="topbar-progress">
            <div
              className="topbar-progress__track"
              role="progressbar"
              aria-label="Workflow progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.percent}
            >
              <span style={{ width: `${progress.percent}%` }} />
            </div>
            <span className="topbar-progress__value">{progress.percent}%</span>
          </div>
        ) : null}
      </header>

      <aside className="sidebar" aria-hidden={!effectiveOpen}>
        <div className="sidebar__inner">
          <div className="workspace-card">
            <div className="workspace-card__avatar" aria-hidden="true">
              <ShieldCheck size={19} />
            </div>
            <div>
              <p>AI Workspace</p>
              <span>{effectiveStatus.label}</span>
            </div>
          </div>

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

          <div className="engine-card">
            <div className="engine-card__header">
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

          <p className="sidebar__version">Noofy v{__APP_VERSION__}</p>
        </div>
      </aside>

      <main className={`main-workspace${mainClassName ? ` ${mainClassName}` : ""}`}>
        <div className={`workspace-content${contentClassName ? ` ${contentClassName}` : ""}`}>{children}</div>
      </main>
    </div>
  );
}

function useTopBarResources() {
  const [snapshot, setSnapshot] = useState<MachineResourceSnapshot | null>(null);
  const failureCountRef = useRef(0);

  useEffect(() => {
    let active = true;
    let interval: number | null = null;

    async function refresh() {
      try {
        const next = await fetchResourceSnapshot();
        if (active) {
          failureCountRef.current = 0;
          setSnapshot(next);
        }
      } catch {
        if (active) {
          failureCountRef.current += 1;
          setSnapshot(null);
          if (failureCountRef.current >= 3 && interval !== null) {
            window.clearInterval(interval);
            interval = null;
          }
        }
      }
    }

    void refresh();
    interval = window.setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      if (interval !== null) window.clearInterval(interval);
    };
  }, []);

  return snapshot;
}

function ResourceMonitor({ snapshot }: { snapshot: MachineResourceSnapshot | null }) {
  return (
    <div className="resource-monitor" aria-label="Resource monitor">
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
  const source = metric.source ? `Source: ${metric.source}` : "Source: Noofy backend";
  if (metric.used_mb !== null && metric.total_mb !== null && metric.free_mb !== null) {
    return `${label}: ${formatGb(metric.used_mb)} GB used, ${formatGb(metric.free_mb)} GB free of ${formatGb(metric.total_mb)} GB. ${source}`;
  }
  if (metric.percent !== null) return `${label}: ${Math.round(metric.percent)}%. ${source}`;
  return `${label}: available. ${source}`;
}
