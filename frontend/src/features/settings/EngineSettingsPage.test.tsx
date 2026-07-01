import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
      description:
        "Extreme memory-saving mode, very slow but may still use GPU",
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
    comfy_org: {
      provider: "comfy_org",
      label: "ComfyUI Account API Key",
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

const noofyRuntimeSettings = {
  available: true,
  disabled_reason: null,
  packaged_runtime: true,
  developer_override: false,
  update_repo: "noofy/app",
  target: "macos-arm64",
  current_version: "0.1.0",
  current_runtime_id: "noofy-runtime-current",
  current_runtime_path:
    "/Applications/Noofy.app/Contents/Resources/noofy-runtime",
  current_source: "bundled",
  latest: null,
  pending: null,
  active: null,
};

const noofyRuntimeCheckedSettings = {
  ...noofyRuntimeSettings,
  latest: {
    tag: "v0.2.0",
    name: "Noofy v0.2.0",
    published_at: null,
    html_url: "https://example.test/releases/v0.2.0",
    asset_name: "noofy-runtime-macos-arm64.zip",
    asset_url: "https://example.test/noofy-runtime.zip",
    asset_sha256: "a".repeat(64),
    asset_size: 100,
    checked_at: "2026-06-05T12:00:00+00:00",
  },
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
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions?check_upstream=true"))
        return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/versions"))
        return Promise.resolve(jsonResponse(localVersions));
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis"))
        return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime"))
        return Promise.resolve(jsonResponse(noofyRuntimeSettings));
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
    expect(
      JSON.parse(window.localStorage.getItem("noofy.prefs") ?? "{}"),
    ).toMatchObject({
      viewMode: "classic",
    });
  });

  it("shows engine controls and update controls in one workflow engine card", async () => {
    renderSettingsPage();

    const panel = (
      await screen.findByRole("heading", { name: "ComfyUI Workflow Engine" })
    ).closest("article");
    expect(panel).not.toBeNull();

    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: "Restart ComfyUI",
      }),
    ).toHaveClass("primary-button");
    expect(
      within(panel as HTMLElement).getByRole("button", { name: "Stop" }),
    ).toHaveClass("secondary-button");
    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: "Repair Setup",
      }),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: "Remove Local Engine Files",
      }),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: /check updates/i,
      }),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: /update comfyui/i,
      }),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: /repair files/i,
      }),
    ).toBeInTheDocument();
  });

  it("restarts a running managed engine by stopping and then starting it", async () => {
    const actionUrls: string[] = [];
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(readyRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (
          url.endsWith("/api/engine/comfyui/stop") &&
          init?.method === "POST"
        ) {
          actionUrls.push("stop");
          return Promise.resolve(jsonResponse({ status: "stopped" }));
        }
        if (
          url.endsWith("/api/engine/comfyui/start") &&
          init?.method === "POST"
        ) {
          actionUrls.push("start");
          return Promise.resolve(jsonResponse({ status: "started" }));
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    fireEvent.click(
      await screen.findByRole("button", { name: "Restart ComfyUI" }),
    );

    expect(await screen.findByText("ComfyUI started.")).toBeInTheDocument();
    expect(actionUrls).toEqual(["stop", "start"]);
  });

  it("starts the managed engine from Restart when it is stopped", async () => {
    const actionUrls: string[] = [];
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(stoppedRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (
          url.endsWith("/api/engine/comfyui/stop") &&
          init?.method === "POST"
        ) {
          actionUrls.push("stop");
          return Promise.resolve(jsonResponse({ status: "stopped" }));
        }
        if (
          url.endsWith("/api/engine/comfyui/start") &&
          init?.method === "POST"
        ) {
          actionUrls.push("start");
          return Promise.resolve(jsonResponse({ status: "started" }));
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    fireEvent.click(
      await screen.findByRole("button", { name: "Restart ComfyUI" }),
    );

    expect(await screen.findByText("ComfyUI started.")).toBeInTheDocument();
    expect(actionUrls).toEqual(["start"]);
  });

  it("removes local engine files only after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(stoppedRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (
          url.endsWith("/api/settings/local-engine-files") &&
          init?.method === "DELETE"
        ) {
          return Promise.resolve(
            jsonResponse({
              status: "removed",
              bytes_deleted: 3_221_225_472,
              deleted_paths: [
                {
                  path: "/Users/test/Library/Application Support/Noofy/runtime",
                  bytes_deleted: 3_221_225_472,
                },
              ],
              skipped_paths: [],
              preserved_paths: {
                models: "/Users/test/Documents/Noofy Models",
                outputs: "/Users/test/Library/Application Support/Noofy/outputs",
                workflows:
                  "/Users/test/Library/Application Support/Noofy/workflow-store",
              },
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Remove Local Engine Files",
      }),
    );

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining("Noofy will keep your workflows"),
    );
    expect(
      await screen.findByText(/Removed 3\.0 GB of local engine files/i),
    ).toBeInTheDocument();
  });

  it("loads upstream ComfyUI release options only when explicitly requested", async () => {
    renderSettingsPage();

    expect((await screen.findAllByText("v0.20.1")).length).toBeGreaterThan(0);
    const select = await screen.findByRole("combobox", { name: /version/i });

    expect(select).toHaveValue("latest");
    expect(
      screen.queryByRole("option", { name: /latest comfyui.*v0.21.0/i }),
    ).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/engine/comfyui/versions?check_upstream=true",
      expect.anything(),
    );

    const comfyVersionPanel = screen
      .getByRole("heading", { name: "ComfyUI Workflow Engine" })
      .closest("article");
    fireEvent.click(
      within(comfyVersionPanel as HTMLElement).getByRole("button", {
        name: /check updates/i,
      }),
    );

    expect(
      await screen.findByRole("option", { name: /latest comfyui.*v0.21.0/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "v0.21.0" })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /available upstream/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /v0.20.1.*current/i }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/engine/comfyui/versions?check_upstream=true",
      {
        headers: { Accept: "application/json" },
      },
    );
  });

  it("shows the bundled ComfyUI version when Noofy is using the included runtime", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(
          jsonResponse({
            ...readyRuntime,
            version: {
              active_tag: "v0.20.1",
              source_hash: null,
              source_kind: "bundled",
              local_validation_status: null,
            },
          }),
        );
      if (url.endsWith("/api/engine/comfyui/versions"))
        return Promise.resolve(
          jsonResponse({
            ...localVersions,
            current: null,
            options: [],
          }),
        );
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis"))
        return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime"))
        return Promise.resolve(jsonResponse(noofyRuntimeSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    const panel = (
      await screen.findByRole("heading", { name: "ComfyUI Workflow Engine" })
    ).closest("article");

    expect(
      within(panel as HTMLElement).getByText("v0.20.1"),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByText("Included with Noofy"),
    ).toBeInTheDocument();
  });

  it("shows repair-blocked and incompatible ComfyUI version states", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis"))
        return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime"))
        return Promise.resolve(jsonResponse(noofyRuntimeSettings));
      if (url.endsWith("/api/engine/comfyui/versions")) {
        return Promise.resolve(
          jsonResponse({
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
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(
      await screen.findByText("Automatic repair paused"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /v0.19.0.*not compatible/i }),
    ).toBeInTheDocument();
  });

  it("stages a managed VRAM launch mode and saves it explicitly", async () => {
    let currentLaunchSettings = launchSettings;
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(
            jsonResponse({
              ...readyRuntime,
              managed_vram_mode: "lowvram",
            }),
          );
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (
          url.endsWith("/api/engine/comfyui/launch-settings") &&
          init?.method === "PUT"
        ) {
          expect(init.body).toBe(JSON.stringify({ vram_mode: "lowvram" }));
          currentLaunchSettings = {
            ...launchSettings,
            vram_mode: "lowvram",
          };
          return Promise.resolve(
            jsonResponse({
              status: "updated_restarted",
              settings: currentLaunchSettings,
              restart_status: "started",
              error: null,
            }),
          );
        }
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(currentLaunchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    const slider = await screen.findByRole("slider", {
      name: /managed launch mode/i,
    });
    const save = screen.getByRole("button", { name: "Save" });

    expect(slider).toHaveValue("3");
    expect(save).toBeDisabled();
    expect(screen.getByText("Recommended for most computers.")).toBeInTheDocument();

    fireEvent.change(slider, { target: { value: "2" } });

    expect(screen.getByText("Uses less GPU memory for smaller cards.")).toBeInTheDocument();
    expect(save).toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/engine/comfyui/launch-settings",
      expect.objectContaining({
        method: "PUT",
      }),
    );

    fireEvent.click(save);

    expect(
      await screen.findByText(/saved and ComfyUI restarted/i),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/engine/comfyui/launch-settings",
      expect.objectContaining({
        method: "PUT",
      }),
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows the model download API key settings card with hidden API key inputs", async () => {
    renderSettingsPage();

    expect(
      await screen.findByRole("heading", { name: "Model download API keys" }),
    ).toBeInTheDocument();
    const huggingFaceInput = screen.getByLabelText("Hugging Face API Key");
    const civitaiInput = screen.getByLabelText("Civitai API Key");

    expect(huggingFaceInput).toHaveAttribute("type", "password");
    expect(civitaiInput).toHaveAttribute("type", "password");

    fireEvent.click(
      screen.getByRole("button", { name: "Show Hugging Face API Key" }),
    );

    expect(huggingFaceInput).toHaveAttribute("type", "text");
  });

  it("shows headless credential-store guidance without full storage paths", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions"))
        return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) {
        return Promise.resolve(
          jsonResponse({
            ...apiSettings,
            credential_store: {
              available: false,
              status: "unavailable",
              error: "No OS-backed credential store is available.",
              kind: "os-keyring",
              backend: null,
              display_path: null,
              guidance:
                "On headless Linux, configure Secret Service or explicitly opt in to encrypted-vault mode.",
            },
          }),
        );
      }
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime"))
        return Promise.resolve(jsonResponse(noofyRuntimeSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(
      await screen.findByText("Credential store unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByText(/headless linux/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Hugging Face API Key")).toBeDisabled();
  });

  it("shows encrypted-vault repo-local rejection without exposing repo-local paths", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions"))
        return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis")) {
        return Promise.resolve(
          jsonResponse({
            ...apiSettings,
            credential_store: {
              available: false,
              status: "unavailable",
              error:
                "Encrypted API key vault cannot use a Noofy data directory inside the repo checkout.",
              kind: "encrypted-vault",
              backend: "encrypted-vault",
              display_path: "<app-data>/settings/api-key-vault.json",
              guidance:
                "Set NOOFY_DATA_DIR to an app data directory outside the repo checkout.",
            },
          }),
        );
      }
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime"))
        return Promise.resolve(jsonResponse(noofyRuntimeSettings));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(
      await screen.findByText(
        /cannot use a Noofy data directory inside the repo checkout/i,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/api-key-vault\.json/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/home\/ubuntu\/Noofy/)).not.toBeInTheDocument();
  });

  it("saves a Hugging Face API key without fetching it back", async () => {
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(readyRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (
          url.endsWith("/api/settings/apis/hugging_face/key") &&
          init?.method === "PUT"
        ) {
          expect(init.body).toBe(
            JSON.stringify({ api_key: "hf_test_secret_1234" }),
          );
          return Promise.resolve(
            jsonResponse({
              status: "saved",
              provider: {
                provider: "hugging_face",
                label: "Hugging Face",
                configured: true,
                last_four: "1234",
              },
            }),
          );
        }
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    const input = await screen.findByLabelText("Hugging Face API Key");
    fireEvent.change(input, { target: { value: "hf_test_secret_1234" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save Hugging Face API Key" }),
    );

    expect(
      await screen.findByText("Hugging Face API key saved."),
    ).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(screen.getByText("Saved key ending in 1234")).toBeInTheDocument();
    expect(screen.queryByText("hf_test_secret_1234")).not.toBeInTheDocument();
  });

  it("shows and updates the Noofy model folder setting", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("/Volumes/AI/Noofy Models");
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(readyRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (
          url.endsWith("/api/settings/model-folders") &&
          init?.method === "PUT"
        ) {
          expect(init.body).toBe(
            JSON.stringify({ noofy_models_dir: "/Volumes/AI/Noofy Models" }),
          );
          return Promise.resolve(
            jsonResponse({
              status: "updated",
              restart_required: true,
              settings: {
                ...modelFolderSettings,
                noofy_models_dir: "/Volumes/AI/Noofy Models",
              },
            }),
          );
        }
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    expect(
      await screen.findByRole("heading", { name: "Model Folder" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("/Users/test/Documents/Noofy Models"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /move folder/i }));

    expect(
      await screen.findByText("/Volumes/AI/Noofy Models"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Restart ComfyUI so it can scan the new model folder location/i),
    ).toBeInTheDocument();
  });

  it("shows fallback copy when restart triggers repair and falls back", async () => {
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(stoppedRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (
          url.endsWith("/api/engine/comfyui/start") &&
          init?.method === "POST"
        ) {
          return Promise.resolve(
            jsonResponse({ status: "repair_failed_fallback_active" }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    const restart = await screen.findByRole("button", {
      name: "Restart ComfyUI",
    });
    fireEvent.click(restart);

    expect(
      await screen.findByText(/last working ComfyUI version/i),
    ).toBeInTheDocument();
  });

  it("shows repair progress while restart is repairing the managed runtime", async () => {
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(stoppedRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (url.endsWith("/api/engine/comfyui/update/status")) {
          return Promise.resolve(
            jsonResponse({
              operation: "repair",
              phase: "repairing_environment",
              selected_version: "v0.20.1",
              resolved_tag: "v0.20.1",
              progress_label: "Rebuilding a fresh ComfyUI environment.",
              status: "running",
              error: null,
              installed_path: null,
              activated_version: null,
            }),
          );
        }
        if (
          url.endsWith("/api/engine/comfyui/start") &&
          init?.method === "POST"
        ) {
          return new Promise((resolve) => {
            setTimeout(
              () =>
                resolve(jsonResponse({ status: "repair_completed_started" })),
              800,
            );
          });
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    const restart = await screen.findByRole("button", {
      name: "Restart ComfyUI",
    });
    fireEvent.click(restart);

    expect(
      await screen.findByText(/repaired and restarted/i),
    ).toBeInTheDocument();
  });

  it("runs a manual environment rebuild and shows rebuild progress", async () => {
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(readyRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (
          url.endsWith("/api/engine/comfyui/rebuild") &&
          init?.method === "POST"
        ) {
          return Promise.resolve(
            jsonResponse({
              operation: "rebuild",
              phase: "repairing_environment",
              selected_version: "current",
              resolved_tag: "v0.20.1",
              progress_label: "Rebuilding a fresh ComfyUI environment.",
              status: "running",
              error: null,
              installed_path: null,
              activated_version: null,
            }),
          );
        }
        if (url.endsWith("/api/engine/comfyui/update/status")) {
          return Promise.resolve(
            jsonResponse({
              operation: "rebuild",
              phase: "completed",
              selected_version: "current",
              resolved_tag: "v0.20.1",
              progress_label:
                "ComfyUI v0.20.1 environment was rebuilt and validated.",
              status: "completed",
              error: null,
              installed_path: "/runtime/core-engines/v0.20.1",
              activated_version: "v0.20.1",
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    const rebuild = await screen.findByRole("button", {
      name: /repair files/i,
    });
    fireEvent.click(rebuild);

    expect(
      (await screen.findAllByText("ComfyUI was repaired and checked.", {}, { timeout: 2500 })).length,
    ).toBeGreaterThan(0);
  });

  it("keeps the update result when the post-update summary refresh fails", async () => {
    let versionRequests = 0;
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(readyRuntime));
        if (url.endsWith("/api/engine/comfyui/versions")) {
          versionRequests += 1;
          if (versionRequests > 1) {
            return Promise.resolve(jsonResponse({}, 400));
          }
          return Promise.resolve(jsonResponse(versions));
        }
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(noofyRuntimeSettings));
        if (
          url.endsWith("/api/engine/comfyui/update") &&
          init?.method === "POST"
        ) {
          expect(init.body).toBe(JSON.stringify({ version: "latest" }));
          return Promise.resolve(
            jsonResponse({
              operation: "update",
              phase: "completed",
              selected_version: "latest",
              resolved_tag: "v0.21.0",
              progress_label: "ComfyUI v0.21.0 is active.",
              status: "completed",
              error: null,
              installed_path: "/runtime/core-engines/v0.21.0",
              activated_version: "v0.21.0",
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /update comfyui/i }),
    );

    expect(
      (
        await screen.findAllByText("ComfyUI was updated and checked.")
      ).length,
    ).toBeGreaterThan(0);
    await waitFor(() => expect(versionRequests).toBeGreaterThan(1));
    expect(
      screen.queryByText("Noofy could not complete that action"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Noofy reported an error while loading this data/i),
    ).not.toBeInTheDocument();
  });

  it("loads Noofy runtime status without checking GitHub automatically", async () => {
    renderSettingsPage();

    expect(
      await screen.findByRole("heading", { name: "Noofy App Update" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Not checked")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/settings/noofy-runtime/check",
      expect.anything(),
    );
  });

  it("checks, stages, and manually activates a Noofy runtime update", async () => {
    let currentNoofySettings: any = noofyRuntimeSettings;
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/runtime"))
          return Promise.resolve(jsonResponse(readyRuntime));
        if (url.endsWith("/api/engine/comfyui/versions"))
          return Promise.resolve(jsonResponse(versions));
        if (url.endsWith("/api/engine/comfyui/launch-settings"))
          return Promise.resolve(jsonResponse(launchSettings));
        if (url.endsWith("/api/settings/apis"))
          return Promise.resolve(jsonResponse(apiSettings));
        if (url.endsWith("/api/settings/model-folders"))
          return Promise.resolve(jsonResponse(modelFolderSettings));
        if (
          url.endsWith("/api/settings/noofy-runtime/check") &&
          init?.method === "POST"
        ) {
          currentNoofySettings = noofyRuntimeCheckedSettings;
          return Promise.resolve(
            jsonResponse({
              status: "checked",
              latest: noofyRuntimeCheckedSettings.latest,
              disabled_reason: null,
            }),
          );
        }
        if (
          url.endsWith("/api/settings/noofy-runtime/stage") &&
          init?.method === "POST"
        ) {
          return Promise.resolve(
            jsonResponse({
              job_id: "runtime-job-1",
              phase: "downloading",
              status: "running",
              progress_label: "Downloading Noofy runtime update.",
              latest_version: "v0.2.0",
              staged_runtime_id: null,
              error: null,
            }),
          );
        }
        if (url.endsWith("/api/settings/noofy-runtime/update/status")) {
          currentNoofySettings = {
            ...noofyRuntimeCheckedSettings,
            pending: {
              runtime_id: "noofy-runtime-v0.2.0",
              tag: "v0.2.0",
              target: "macos-arm64",
              runtime_path:
                "/app-data/runtime-store/noofy-runtime/runtimes/noofy-runtime-v0.2.0/noofy-runtime",
              manifest_sha256: "b".repeat(64),
              backend_sha256: "c".repeat(64),
              python_version: "3.13.1",
              uv_version: "0.5.0",
              asset_name: "noofy-runtime-macos-arm64.zip",
              asset_url: "https://example.test/noofy-runtime.zip",
              asset_sha256: "a".repeat(64),
              staged_at: "2026-06-05T12:01:00+00:00",
              activated_at: null,
            },
          };
          return Promise.resolve(
            jsonResponse({
              job_id: "runtime-job-1",
              phase: "ready_to_activate",
              status: "completed",
              progress_label:
                "Noofy runtime update was downloaded and validated.",
              latest_version: "v0.2.0",
              staged_runtime_id: "noofy-runtime-v0.2.0",
              error: null,
            }),
          );
        }
        if (
          url.endsWith("/api/settings/noofy-runtime/activate") &&
          init?.method === "POST"
        ) {
          currentNoofySettings = {
            ...currentNoofySettings,
            active: currentNoofySettings.pending,
            pending: null,
          };
          return Promise.resolve(
            jsonResponse({
              status: "activated",
              active: currentNoofySettings.active,
              disabled_reason: null,
              error: null,
            }),
          );
        }
        if (url.endsWith("/api/settings/noofy-runtime"))
          return Promise.resolve(jsonResponse(currentNoofySettings));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      },
    );

    renderSettingsPage();

    const panel = (
      await screen.findByRole("heading", { name: "Noofy App Update" })
    ).closest("article");
    fireEvent.click(
      within(panel as HTMLElement).getByRole("button", {
        name: /check for updates/i,
      }),
    );

    expect(await screen.findByText("v0.2.0")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/noofy-runtime/check",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(
      within(panel as HTMLElement).getByRole("button", {
        name: /download and validate/i,
      }),
    );

    expect(
      await screen.findByText(/checked and is ready/i, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        /It will be used next time you open Noofy/i,
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();

    fireEvent.click(
      within(panel as HTMLElement).getByRole("button", {
        name: /activate on next launch/i,
      }),
    );

    expect(
      await screen.findByText(/Noofy v0\.2\.0 will be used the next time you open Noofy\./i),
    ).toBeInTheDocument();
  });

  it("shows Noofy runtime updates disabled in packaged builds without update config", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions"))
        return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis"))
        return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime")) {
        return Promise.resolve(
          jsonResponse({
            ...noofyRuntimeSettings,
            available: false,
            disabled_reason:
              "Noofy runtime updates are not configured for this build.",
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    const panel = (
      await screen.findByRole("heading", { name: "Noofy App Update" })
    ).closest("article");
    expect(
      within(panel as HTMLElement).getByText(/not configured for this build/i),
    ).toBeInTheDocument();
    expect(
      within(panel as HTMLElement).getByRole("button", {
        name: /check for updates/i,
      }),
    ).toBeDisabled();
  });

  it("hides Noofy runtime updates in source checkout mode", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime"))
        return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/engine/comfyui/versions"))
        return Promise.resolve(jsonResponse(versions));
      if (url.endsWith("/api/engine/comfyui/launch-settings"))
        return Promise.resolve(jsonResponse(launchSettings));
      if (url.endsWith("/api/settings/apis"))
        return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders"))
        return Promise.resolve(jsonResponse(modelFolderSettings));
      if (url.endsWith("/api/settings/noofy-runtime")) {
        return Promise.resolve(
          jsonResponse({
            ...noofyRuntimeSettings,
            available: false,
            packaged_runtime: false,
            disabled_reason:
              "Noofy runtime updates are available only in packaged app builds.",
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderSettingsPage();

    expect(
      await screen.findByRole("heading", { name: "ComfyUI Workflow Engine" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Noofy App Update" }),
    ).not.toBeInTheDocument();
  });
});
