import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EngineSettingsPage } from "./EngineSettingsPage";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const readyRuntime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  pid: 123,
  error: null,
  environment: { prepared: true },
  version: {
    active_tag: "v0.20.1",
    source_hash: "sha256:abc",
    source_kind: "installed",
    local_validation_status: "locally_verified",
  },
};

const versions = {
  updates_allowed: true,
  disabled_reason: null,
  latest_tag: "v0.21.0",
  current: {
    tag: "v0.20.1",
    available_upstream: true,
    installed: true,
    active: true,
    locally_verified: true,
    failed_validation: false,
    failed_reason: null,
    source_hash: "sha256:abc",
    commit_sha: "abc",
    source_path: "/runtime/core-engines/v0.20.1",
    env_path: "/runtime/core-envs/v0.20.1/venv",
    archive_url: null,
    installed_at: null,
    activated_at: null,
    validated_at: null,
  },
  options: [
    {
      tag: "v0.21.0",
      label: "v0.21.0 (Available upstream)",
      status: "Available upstream",
      available_upstream: true,
      installed: false,
      active: false,
      locally_verified: false,
      failed_validation: false,
      failed_reason: null,
      source_hash: null,
      commit_sha: "def",
      published_at: null,
    },
    {
      tag: "v0.20.1",
      label: "v0.20.1 (Current)",
      status: "Current",
      available_upstream: true,
      installed: true,
      active: true,
      locally_verified: true,
      failed_validation: false,
      failed_reason: null,
      source_hash: "sha256:abc",
      commit_sha: "abc",
      published_at: null,
    },
  ],
  release_fetch_error: null,
};

describe("EngineSettingsPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
  });

  it("persists the dashboard view preference from the settings toggle", async () => {
    render(<EngineSettingsPage onNavigate={vi.fn()} />);

    const classic = await screen.findByRole("radio", { name: /classic/i });
    fireEvent.click(classic);

    expect(classic).toBeChecked();
    expect(JSON.parse(window.localStorage.getItem("noofy.prefs") ?? "{}")).toMatchObject({
      viewMode: "classic",
    });
  });

  it("shows current ComfyUI version and upstream release options", async () => {
    render(<EngineSettingsPage onNavigate={vi.fn()} />);

    expect((await screen.findAllByText("v0.20.1")).length).toBeGreaterThan(0);
    const select = await screen.findByRole("combobox", { name: /version/i });

    expect(select).toHaveValue("latest");
    expect(screen.getByRole("option", { name: /latest version.*v0.21.0/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /v0.20.1.*current/i })).toBeInTheDocument();
  });

  it("shows repair-blocked and incompatible ComfyUI version states", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) {
        return Promise.resolve(jsonResponse({
          ...versions,
          current: {
            ...versions.current,
            repair_status: "repair_blocked",
            repair_blocked_until: "2026-05-06T12:00:00+00:00",
          },
          options: [
            ...versions.options,
            {
              tag: "v0.19.0",
              label: "v0.19.0 (Incompatible)",
              status: "Incompatible",
              available_upstream: true,
              installed: true,
              active: false,
              locally_verified: false,
              failed_validation: true,
              failed_reason: "route /prompt failed",
              source_hash: "sha256:old",
              commit_sha: "old",
              published_at: null,
              repair_status: "incompatible",
              repair_attempt_count: 1,
              last_repair_attempt_at: null,
              last_repair_error: "route /prompt failed",
              repair_blocked_until: null,
              incompatible: true,
              incompatible_reason: "route /prompt failed",
            },
          ],
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<EngineSettingsPage onNavigate={vi.fn()} />);

    expect(await screen.findByText("Automatic repair paused")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /v0.19.0.*incompatible/i })).toBeInTheDocument();
  });

  it("shows fallback copy when start triggers repair and falls back", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/start") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ status: "repair_failed_fallback_active" }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<EngineSettingsPage onNavigate={vi.fn()} />);

    const start = await screen.findByRole("button", { name: /start/i });
    fireEvent.click(start);

    expect(await screen.findByText(/previous working engine/i)).toBeInTheDocument();
  });

  it("shows repair progress while start is repairing the managed runtime", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/update/status")) {
        return Promise.resolve(jsonResponse({
          operation: "repair",
          phase: "repairing_environment",
          selected_version: "v0.20.1",
          resolved_tag: "v0.20.1",
          progress_label: "Rebuilding a fresh ComfyUI environment.",
          status: "running",
          error: null,
          installed_path: null,
          activated_version: null,
        }));
      }
      if (url.endsWith("/api/engine/comfyui/start") && init?.method === "POST") {
        return new Promise((resolve) => {
          setTimeout(() => resolve(jsonResponse({ status: "repair_completed_started" })), 800);
        });
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<EngineSettingsPage onNavigate={vi.fn()} />);

    const start = await screen.findByRole("button", { name: /start/i });
    fireEvent.click(start);

    expect(await screen.findByText("repair: repairing_environment")).toBeInTheDocument();
    expect(await screen.findByText(/repaired and started/i)).toBeInTheDocument();
  });

  it("runs a manual environment rebuild and shows rebuild progress", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/rebuild") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({
          operation: "rebuild",
          phase: "repairing_environment",
          selected_version: "current",
          resolved_tag: "v0.20.1",
          progress_label: "Rebuilding a fresh ComfyUI environment.",
          status: "running",
          error: null,
          installed_path: null,
          activated_version: null,
        }));
      }
      if (url.endsWith("/api/engine/comfyui/update/status")) {
        return Promise.resolve(jsonResponse({
          operation: "rebuild",
          phase: "completed",
          selected_version: "current",
          resolved_tag: "v0.20.1",
          progress_label: "ComfyUI v0.20.1 environment was rebuilt and validated.",
          status: "completed",
          error: null,
          installed_path: "/runtime/core-engines/v0.20.1",
          activated_version: "v0.20.1",
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<EngineSettingsPage onNavigate={vi.fn()} />);

    const rebuild = await screen.findByRole("button", { name: /rebuild environment/i });
    fireEvent.click(rebuild);

    expect(await screen.findByText("repairing_environment")).toBeInTheDocument();
    expect(
      await screen.findByText(/environment was rebuilt and validated successfully/i, {}, { timeout: 2500 }),
    ).toBeInTheDocument();
  });
});
