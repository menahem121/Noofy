import { createContext, useContext, ReactNode, useState } from "react";
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

import { openExternalUrl } from "../../lib/openExternalUrl";

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

export function AppLayout({ activeRoute, status, children, onNavigate }: AppLayoutProps) {
  const { sidebarOpen, setSidebarOpen } = useContext(SidebarContext);
  const isHome = activeRoute === "home";
  const effectiveOpen = isHome ? true : sidebarOpen;

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

      <aside className="sidebar" aria-hidden={!effectiveOpen}>
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

      <main className="main-workspace">
        <div className="workspace-content">{children}</div>
      </main>
    </div>
  );
}
