import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
        return Promise.resolve(jsonResponse({ cpu: null, ram: null, vram: null }));
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
    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));

    expect(await screen.findByRole("status", { name: "Opening Workflows" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to home" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workflows" })).toHaveClass("sidebar-nav__item--active");
    await waitFor(() => {
      expect(document.querySelector("main.app-route-loading")).not.toBeInTheDocument();
    });
  });
});
