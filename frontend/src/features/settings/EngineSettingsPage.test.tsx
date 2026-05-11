import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RuntimeStatusProvider } from "../app/RuntimeStatusProvider";
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

const stoppedRuntime = {
  ...readyRuntime,
  reachable: false,
  managed_process_running: false,
  pid: null,
  uptime_seconds: null,
};

const versions = {
  updates_allowed: true,
  disabled_reason: null,
  upstream_checked: true,
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

const localVersions = {
  ...versions,
  upstream_checked: false,
  latest_tag: null,
  options: [versions.options[1]],
};

const launchSettings = {
  vram_mode: "normal",
  applies_to_managed_runtime: true,
  disabled_reason: null,
  options: [
    {
      value: "cpu",
      label: "CPU only",
      description: "Runs without GPU acceleration, usually the slowest",
    },
    {
      value: "novram",
      label: "No VRAM",
      description: "Extreme memory-saving mode, very slow but may still use GPU",
    },
    {
      value: "lowvram",
      label: "Low VRAM",
      description: "For smaller GPUs",
    },
    {
      value: "normal",
      label: "Normal VRAM",
      description: "Recommended",
    },
    {
      value: "highvram",
      label: "High VRAM",
      description: "Faster if you have lots of VRAM",
    },
  ],
};

const apiSettings = {
  providers: {
    hugging_face: {
      provider: "hugging_face",
      label: "Hugging Face",
      configured: false,
      last_four: null,
    },
    civitai: {
      provider: "civitai",
      label: "Civitai",
      configured: false,
      last_four: null,
    },
  },
  credential_store: {
    available: true,
    status: "available",
    error: null,
    kind: "os-keyring",
    backend: "keyring.backends.secretservice.Keyring",
    display_path: null,
    guidance: null,
  },
};

const modelFolderSettings = {
  noofy_models_dir: "/Users/test/Documents/Noofy Models",
  external_comfyui_models_dir: null,
  categories: ["checkpoints", "loras", "vae"],
  noofy_folder_exists: true,
  external_folder_exists: null,
};

function renderSettingsPage() {
  return render(
    <RuntimeStatusProvider
      initialRuntimeState={{
        backendStatus: "reachable",
        engineStatus: "ready",
        runtime: readyRuntime as never,
        hasKnownState: true,
        lastCheckedAt: Date.now(),
      }}
      skipInitialRefresh
    >
      <EngineSettingsPage onNavigate={vi.fn()} />
    </RuntimeStatusProvider>,
  );
}

describe("EngineSettingsPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions?check_upstream=true")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(localVersions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    fetchMock.mockReset();
    window.localStorage.clear();
  });

  it("persists the dashboard view preference from the settings toggle", async () => {
    renderSettingsPage();

    const classic = await screen.findByRole("radio", { name: /classic/i });
    fireEvent.click(classic);

    expect(classic).toBeChecked();
    expect(JSON.parse(window.localStorage.getItem("noofy.prefs") ?? "{}")).toMatchObject({
      viewMode: "classic",
    });
  });

  it("shows Restart, Stop, and Repair actions in the ComfyUI engine card", async () => {
    renderSettingsPage();

    const panel = (await screen.findByRole("heading", { name: "ComfyUI Engine" })).closest("article");
    expect(panel).not.toBeNull();

    const actions = within(panel as HTMLElement).getAllByRole("button");
    expect(actions.map((button) => button.textContent?.trim())).toEqual([
      "Restart",
      "Stop",
      "Repair Installation",
    ]);
    expect(actions[0]).toHaveClass("primary-button");
    expect(actions[1]).toHaveClass("secondary-button");
    expect(actions[2]).toHaveClass("secondary-button");
  });

  it("restarts a running managed engine by stopping and then starting it", async () => {
    const actionUrls: string[] = [];
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/engine/comfyui/stop") && init?.method === "POST") {
        actionUrls.push("stop");
        return Promise.resolve(jsonResponse({ status: "stopped" }));
      }
      if (url.endsWith("/api/engine/comfyui/start") && init?.method === "POST") {
        actionUrls.push("start");
        return Promise.resolve(jsonResponse({ status: "started" }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("button", { name: "Restart" }));

    expect(await screen.findByText("Engine started.")).toBeInTheDocument();
    expect(actionUrls).toEqual(["stop", "start"]);
  });

  it("starts the managed engine from Restart when it is stopped", async () => {
    const actionUrls: string[] = [];
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(stoppedRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/engine/comfyui/stop") && init?.method === "POST") {
        actionUrls.push("stop");
        return Promise.resolve(jsonResponse({ status: "stopped" }));
      }
      if (url.endsWith("/api/engine/comfyui/start") && init?.method === "POST") {
        actionUrls.push("start");
        return Promise.resolve(jsonResponse({ status: "started" }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("button", { name: "Restart" }));

    expect(await screen.findByText("Engine started.")).toBeInTheDocument();
    expect(actionUrls).toEqual(["start"]);
  });

  it("loads upstream ComfyUI release options only when explicitly requested", async () => {
    renderSettingsPage();

    expect((await screen.findAllByText("v0.20.1")).length).toBeGreaterThan(0);
    const select = await screen.findByRole("combobox", { name: /version/i });

    expect(select).toHaveValue("latest");
    expect(screen.queryByRole("option", { name: /latest version.*v0.21.0/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/engine/comfyui/versions?check_upstream=true", expect.anything());

    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));

    expect(await screen.findByRole("option", { name: /latest version.*v0.21.0/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /v0.20.1.*current/i })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/engine/comfyui/versions?check_upstream=true", {
      headers: { Accept: "application/json" },
    });
  });

  it("shows repair-blocked and incompatible ComfyUI version states", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
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

    renderSettingsPage();

    expect(await screen.findByText("Automatic repair paused")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /v0.19.0.*incompatible/i })).toBeInTheDocument();
  });

  it("stages a managed VRAM launch mode and saves it explicitly", async () => {
    let currentLaunchSettings = launchSettings;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse({
        ...readyRuntime,
        managed_vram_mode: "lowvram",
      }));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ vram_mode: "lowvram" }));
        currentLaunchSettings = {
          ...launchSettings,
          vram_mode: "lowvram",
        };
        return Promise.resolve(jsonResponse({
          status: "updated_restarted",
          settings: currentLaunchSettings,
          restart_status: "started",
          error: null,
        }));
      }
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(currentLaunchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    const slider = await screen.findByRole("slider", { name: /managed launch mode/i });
    const save = screen.getByRole("button", { name: "Save" });

    expect(slider).toHaveValue("3");
    expect(save).toBeDisabled();
    expect(screen.getByText("Recommended")).toBeInTheDocument();

    fireEvent.change(slider, { target: { value: "2" } });

    expect(screen.getByText("For smaller GPUs")).toBeInTheDocument();
    expect(save).toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/engine/comfyui/launch-settings", expect.objectContaining({
      method: "PUT",
    }));

    fireEvent.click(save);

    expect(await screen.findByText(/managed engine restarted/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/engine/comfyui/launch-settings", expect.objectContaining({
      method: "PUT",
    }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows the APIs settings card with hidden API key inputs", async () => {
    renderSettingsPage();

    expect(await screen.findByRole("heading", { name: "APIs" })).toBeInTheDocument();
    const huggingFaceInput = screen.getByLabelText("Hugging Face API Key");
    const civitaiInput = screen.getByLabelText("Civitai API Key");

    expect(huggingFaceInput).toHaveAttribute("type", "password");
    expect(civitaiInput).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Show Hugging Face API Key" }));

    expect(huggingFaceInput).toHaveAttribute("type", "text");
  });

  it("shows headless credential-store guidance without full storage paths", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) {
        return Promise.resolve(jsonResponse({
          ...apiSettings,
          credential_store: {
            available: false,
            status: "unavailable",
            error: "No OS-backed credential store is available.",
            kind: "os-keyring",
            backend: null,
            display_path: null,
            guidance: "On headless Linux, configure Secret Service or explicitly opt in to encrypted-vault mode.",
          },
        }));
      }
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(await screen.findByText("Credential store unavailable")).toBeInTheDocument();
    expect(screen.getByText(/headless linux/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Hugging Face API Key")).toBeDisabled();
  });

  it("shows encrypted-vault repo-local rejection with a display path only", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) {
        return Promise.resolve(jsonResponse({
          ...apiSettings,
          credential_store: {
            available: false,
            status: "unavailable",
            error: "Encrypted API key vault cannot use a Noofy data directory inside the repo checkout.",
            kind: "encrypted-vault",
            backend: "encrypted-vault",
            display_path: "<app-data>/settings/api-key-vault.json",
            guidance: "Set NOOFY_DATA_DIR to an app data directory outside the repo checkout.",
          },
        }));
      }
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(await screen.findByText(/cannot use a Noofy data directory inside the repo checkout/i)).toBeInTheDocument();
    expect(screen.getByText(/<app-data>\/settings\/api-key-vault\.json/i)).toBeInTheDocument();
    expect(screen.queryByText(/\/home\/ubuntu\/Noofy/)).not.toBeInTheDocument();
  });

  it("saves a Hugging Face API key without fetching it back", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis/hugging_face/key") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ api_key: "hf_test_secret_1234" }));
        return Promise.resolve(jsonResponse({
          status: "saved",
          provider: {
            provider: "hugging_face",
            label: "Hugging Face",
            configured: true,
            last_four: "1234",
          },
        }));
      }
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    const input = await screen.findByLabelText("Hugging Face API Key");
    fireEvent.change(input, { target: { value: "hf_test_secret_1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Hugging Face API Key" }));

    expect(await screen.findByText("Hugging Face API key saved.")).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(screen.getByText("Saved key ending in 1234")).toBeInTheDocument();
    expect(screen.queryByText("hf_test_secret_1234")).not.toBeInTheDocument();
  });

  it("shows and updates the Noofy model folder setting", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("/Volumes/AI/Noofy Models");
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ noofy_models_dir: "/Volumes/AI/Noofy Models" }));
        return Promise.resolve(jsonResponse({
          status: "updated",
          restart_required: true,
          settings: {
            ...modelFolderSettings,
            noofy_models_dir: "/Volumes/AI/Noofy Models",
          },
        }));
      }
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(await screen.findByRole("heading", { name: "Model Folder" })).toBeInTheDocument();
    expect(screen.getByText("/Users/test/Documents/Noofy Models")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /move folder/i }));

    expect(await screen.findByText("/Volumes/AI/Noofy Models")).toBeInTheDocument();
    expect(screen.getByText(/restart the noofy engine/i)).toBeInTheDocument();
  });

  it("shows fallback copy when restart triggers repair and falls back", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(stoppedRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/engine/comfyui/start") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ status: "repair_failed_fallback_active" }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    const restart = await screen.findByRole("button", { name: "Restart" });
    fireEvent.click(restart);

    expect(await screen.findByText(/previous working engine/i)).toBeInTheDocument();
  });

  it("shows repair progress while restart is repairing the managed runtime", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(stoppedRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
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

    renderSettingsPage();

    const restart = await screen.findByRole("button", { name: "Restart" });
    fireEvent.click(restart);

    expect(await screen.findByText("repair: repairing_environment")).toBeInTheDocument();
    expect(await screen.findByText(/repaired and started/i)).toBeInTheDocument();
  });

  it("runs a manual environment rebuild and shows rebuild progress", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions")) return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings")) return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolderSettings));
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

    renderSettingsPage();

    const rebuild = await screen.findByRole("button", { name: /rebuild environment/i });
    fireEvent.click(rebuild);

    expect(await screen.findByText("repairing_environment")).toBeInTheDocument();
    expect(
      await screen.findByText(/environment was rebuilt and validated successfully/i, {}, { timeout: 2500 }),
    ).toBeInTheDocument();
  });
});
