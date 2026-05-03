import { ReactNode, useState } from "react";
import {
  FolderClock,
  Images,
  Layers,
  Library,
  Loader2,
  Menu,
  PackageOpen,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";

export type AppRouteId = "home" | "workflows" | "history" | "models" | "gallery" | "settings";
export type StatusTone = "success" | "warning" | "error" | "info";

export interface AppStatusView {
  label: string;
  description: string;
  tone: StatusTone;
  loading?: boolean;
}

interface AppLayoutProps {
  activeRoute: AppRouteId;
  status: AppStatusView;
  children: ReactNode;
  onNavigate: (route: AppRouteId) => void;
}

const navItems = [
  { id: "home", label: "Home", Icon: Library },
  { id: "workflows", label: "Workflows", Icon: PackageOpen },
  { id: "history", label: "History", Icon: FolderClock },
  { id: "models", label: "Models", Icon: Layers },
  { id: "gallery", label: "Gallery", Icon: Images },
  { id: "settings", label: "Settings", Icon: Settings },
] satisfies Array<{ id: AppRouteId; label: string; Icon: typeof Library }>;

export function AppLayout({ activeRoute, status, children, onNavigate }: AppLayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className={sidebarOpen ? "app-shell" : "app-shell app-shell--sidebar-closed"}>
      <header className="topbar">
        <div className="topbar__brand">
          <button
            className="icon-button topbar__menu"
            type="button"
            aria-label={sidebarOpen ? "Close navigation" : "Open navigation"}
            title={sidebarOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen((o) => !o)}
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

        <label className="search-field">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">Search workflows</span>
          <input type="search" placeholder="Search workflows..." />
        </label>

        <div className="topbar__actions">
          <div className={`status-pill status-pill--${status.tone}`}>
            {status.loading ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <span />}
            <span>{status.label}</span>
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
      </header>

      <aside className="sidebar" aria-hidden={!sidebarOpen}>
        <div className="sidebar__inner">
          <div className="workspace-card">
            <div className="workspace-card__avatar" aria-hidden="true">
              <ShieldCheck size={19} />
            </div>
            <div>
              <p>AI Workspace</p>
              <span>{status.label}</span>
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
              <span className={`engine-dot engine-dot--${status.tone}`} />
              <span>Local engine</span>
            </div>
            <p>{status.description}</p>
            <button className="secondary-button secondary-button--full" type="button" onClick={() => onNavigate("settings")}>
              <SlidersHorizontal size={16} aria-hidden="true" />
              Engine Settings
            </button>
          </div>
        </div>
      </aside>

      <main className="main-workspace">
        <div className="workspace-content">{children}</div>
      </main>
    </div>
  );
}
