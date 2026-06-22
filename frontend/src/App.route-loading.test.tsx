import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const runtime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  sidecar_starting: false,
  pid: 123,
  error: null,
  environment: { prepared: true },
  crash_count: 0,
  restart_attempt: 0,
  max_restart_attempts: 3,
  uptime_seconds: 1,
  last_crash_at: null,
};

const workflowSummary = {
  id: "text_to_image_v0",
  name: "Text to Image",
  version: "0.1.0",
  description: "Generate new images from a simple text prompt.",
  source_label: "Native Noofy",
  main_model: { name: "SDXL Base", type: "checkpoint", size_bytes: 1 },
  category: "Txt2img",
  last_opened: null,
  tags: ["starter"],
  missing_model_count: 0,
  needs_setup: false,
  can_remove: false,
  can_export_noofy: true,
  can_export_comfyui_json: true,
  status: "installed",
  status_label: "Installed",
};

const resourceSnapshot = {
  observed_at: "2026-06-22T12:00:00Z",
  cpu: { available: true, percent: 23, used_mb: null, total_mb: null, free_mb: null, source: "test", error: null },
  ram: { available: true, percent: 50, used_mb: 8192, total_mb: 16384, free_mb: 8192, source: "test", error: null },
  vram: { available: true, percent: 50, used_mb: 4096, total_mb: 8192, free_mb: 4096, source: "test", error: null },
  backend: "test",
  device_name: "Test GPU",
  memory_pressure: "low",
};

describe("App route loading", () => {
  const fetchMock = vi.fn();

  afterEach(() => {
    cleanup();
    vi.doUnmock("./features/workflows/WorkflowsPage");
    vi.resetModules();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
    window.sessionStorage.clear();
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  it("keeps the app shell visible while a first lazy route page is loading", async () => {
    const pendingRoute = new Promise(() => {});
    vi.doMock("./features/workflows/WorkflowsPage", () => ({
      WorkflowsPage: () => {
        throw pendingRoute;
      },
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(runtime));
      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse(resourceSnapshot));
      }
      if (url.endsWith("/api/settings/apis")) {
        return Promise.resolve(jsonResponse({ providers: {}, credential_store: { available: true } }));
      }
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([workflowSummary]));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Built-in Workflows")).toBeInTheDocument();
    expect(await screen.findByText("8.0 / 16 GB")).toBeInTheDocument();
    expect(screen.getByText("4.0 / 8.0 GB")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));

    const loadingStatus = await screen.findByRole("status", { name: "Opening Workflows" });
    expect(loadingStatus).toBeInTheDocument();
    const loadingShell = loadingStatus.closest(".app-shell");
    expect(loadingShell).not.toBeNull();
    expect(within(loadingShell as HTMLElement).getByText("8.0 / 16 GB")).toBeInTheDocument();
    expect(within(loadingShell as HTMLElement).getByText("4.0 / 8.0 GB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to home" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workflows" })).toHaveClass("sidebar-nav__item--active");
    await waitFor(() => {
      expect(document.querySelector("main.app-route-loading")).not.toBeInTheDocument();
    });
  });
});
