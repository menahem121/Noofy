import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FirstLaunchOnboarding } from "./FirstLaunchOnboarding";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

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

const modelFolders = {
  noofy_models_dir: "/Users/test/Documents/Noofy Models",
  external_comfyui_models_dir: null,
  categories: ["checkpoints", "loras", "vae"],
  noofy_folder_exists: true,
  external_folder_exists: null,
};

const textToImageWorkflow = {
  id: "text_to_image_v0",
  name: "Text to Image",
  version: "0.1.0",
  description: "Generate a new image from a simple text prompt.",
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

const fluxWorkflow = {
  ...textToImageWorkflow,
  id: "text_to_image_flux",
  name: "Text to Image - Flux",
  main_model: { name: "Flux", type: "checkpoint", size_bytes: 1 },
};

const textToImageNeedsSetup = {
  ...textToImageWorkflow,
  status: "needs_input_setup",
  status_label: "Needs input setup",
  needs_setup: true,
};

function renderOnboarding(options: {
  workflows?: Array<typeof textToImageWorkflow>;
  onOpenWorkflow?: (workflowId: string, workflowName?: string) => void;
  onBrowseWorkflows?: () => void;
} = {}) {
  return render(
    <FirstLaunchOnboarding
      workflows={options.workflows ?? [textToImageWorkflow]}
      hasLoadedWorkflows
      refreshWorkflows={vi.fn()}
      onOpenWorkflow={options.onOpenWorkflow ?? vi.fn()}
      onBrowseWorkflows={options.onBrowseWorkflows ?? vi.fn()}
    />,
  );
}

describe("FirstLaunchOnboarding", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/settings/onboarding") && method === "PUT") {
        return Promise.resolve(jsonResponse({
          status: "completed",
          onboarding: { schema_version: "1", completed: true, completed_at: "2026-05-18T00:00:00+00:00" },
        }));
      }
      if (url.endsWith("/api/settings/onboarding")) {
        return Promise.resolve(jsonResponse({ schema_version: "1", completed: false, completed_at: null }));
      }
      if (url.endsWith("/api/settings/apis/civitai/key") && method === "PUT") {
        expect(init?.body).toBe(JSON.stringify({ api_key: "civitai_secret_1234" }));
        return Promise.resolve(jsonResponse({
          status: "saved",
          provider: {
            provider: "civitai",
            label: "Civitai",
            configured: true,
            last_four: "1234",
          },
        }));
      }
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(apiSettings));
      if (url.endsWith("/api/settings/model-folders") && method === "PUT") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        return Promise.resolve(jsonResponse({
          status: "updated",
          restart_required: false,
          settings: {
            ...modelFolders,
            ...body,
          },
        }));
      }
      if (url.endsWith("/api/settings/model-folders")) return Promise.resolve(jsonResponse(modelFolders));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    fetchMock.mockReset();
    window.localStorage.clear();
  });

  it("shows first-launch progress and advances through all four steps", async () => {
    renderOnboarding();

    expect(await screen.findByText("Step 1 of 4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 2 of 4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 3 of 4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 4 of 4")).toBeInTheDocument();
    expect(screen.getByText(/create your first image locally/i)).toBeInTheDocument();
  });

  it("marks onboarding complete from Skip, Finish, X, and Escape", async () => {
    const completionMethods = ["Skip", "Finish", "Close onboarding", "Escape"] as const;

    for (const method of completionMethods) {
      cleanup();
      fetchMock.mockClear();
      renderOnboarding();
      expect(await screen.findByText("Step 1 of 4")).toBeInTheDocument();

      if (method === "Finish") {
        fireEvent.click(screen.getByRole("button", { name: "Next" }));
        fireEvent.click(screen.getByRole("button", { name: "Next" }));
        fireEvent.click(screen.getByRole("button", { name: "Next" }));
        fireEvent.click(screen.getByRole("button", { name: "Finish" }));
      } else if (method === "Close onboarding") {
        fireEvent.click(screen.getByRole("button", { name: "Close onboarding" }));
      } else if (method === "Escape") {
        fireEvent.keyDown(window, { key: "Escape" });
      } else {
        fireEvent.click(screen.getByRole("button", { name: "Skip" }));
      }

      await waitFor(() => {
        expect(fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/api/settings/onboarding") && init?.method === "PUT"
        )).toBe(true);
      });
      await waitFor(() => {
        expect(screen.queryByRole("dialog", { name: /connect model providers/i })).not.toBeInTheDocument();
      });
    }
  });

  it("closes on backdrop click without marking onboarding complete", async () => {
    renderOnboarding();

    const dialog = await screen.findByRole("dialog", { name: /connect model providers/i });
    const backdrop = dialog.parentElement;
    expect(backdrop).not.toBeNull();

    fireEvent.mouseDown(backdrop as HTMLElement);

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /connect model providers/i })).not.toBeInTheDocument();
    });
    expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url).endsWith("/api/settings/onboarding") && init?.method === "PUT"
    )).toBe(false);
  });

  it("saves an API key, clears the draft, and shows only safe status", async () => {
    renderOnboarding();

    const input = await screen.findByLabelText("CivitAI");
    fireEvent.change(input, { target: { value: "civitai_secret_1234" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    expect(await screen.findByText(/CivitAI is configured/i)).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(screen.getByText("Configured, ending in 1234")).toBeInTheDocument();
    expect(screen.queryByText("civitai_secret_1234")).not.toBeInTheDocument();
  });

  it("saves external and Noofy model folder choices through settings", async () => {
    vi.spyOn(window, "prompt")
      .mockReturnValueOnce("/Users/test/ComfyUI/models")
      .mockReturnValueOnce("/Volumes/AI/Noofy Models");

    renderOnboarding();

    expect(await screen.findByText("Step 1 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Choose Folder" }));

    expect(await screen.findByText("/Users/test/ComfyUI/models")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/settings/model-folders", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ external_comfyui_models_dir: "/Users/test/ComfyUI/models" }),
    }));

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Change Folder" }));

    expect(await screen.findByText("/Volumes/AI/Noofy Models")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/settings/model-folders", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ noofy_models_dir: "/Volumes/AI/Noofy Models" }),
    }));
  });

  it("shows clear fallback copy when optional settings cannot load", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/settings/onboarding") && method === "PUT") {
        return Promise.resolve(jsonResponse({
          status: "completed",
          onboarding: { schema_version: "1", completed: true, completed_at: "2026-05-18T00:00:00+00:00" },
        }));
      }
      if (url.endsWith("/api/settings/onboarding")) {
        return Promise.resolve(jsonResponse({ schema_version: "1", completed: false, completed_at: null }));
      }
      if (url.endsWith("/api/settings/apis")) return Promise.reject(new Error("API settings offline"));
      if (url.endsWith("/api/settings/model-folders")) return Promise.reject(new Error("Folder settings offline"));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderOnboarding();

    expect(await screen.findByText("API key settings are unavailable")).toBeInTheDocument();
    expect(screen.getByLabelText("CivitAI")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Noofy could not load the current connected folder")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Noofy could not load the current folder")).toBeInTheDocument();
  });

  it("starts the selected Text to Image variant from the final screen", async () => {
    const onOpenWorkflow = vi.fn();
    renderOnboarding({ workflows: [textToImageWorkflow, fluxWorkflow], onOpenWorkflow });

    expect(await screen.findByText("Step 1 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    fireEvent.change(screen.getByLabelText("Text to Image model workflow"), {
      target: { value: "text_to_image_flux" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Creating" }));

    await waitFor(() => {
      expect(onOpenWorkflow).toHaveBeenCalledWith("text_to_image_flux", "Text to Image - Flux");
    });
  });

  it("falls back to browsing workflows when Text to Image is unavailable", async () => {
    const onBrowseWorkflows = vi.fn();
    renderOnboarding({ workflows: [], onBrowseWorkflows });

    expect(await screen.findByText("Step 1 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByRole("button", { name: "Browse Workflows" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Browse Workflows" }));

    await waitFor(() => {
      expect(onBrowseWorkflows).toHaveBeenCalled();
    });
  });

  it("falls back to browsing workflows when Text to Image needs dashboard setup", async () => {
    const onBrowseWorkflows = vi.fn();
    renderOnboarding({ workflows: [textToImageNeedsSetup], onBrowseWorkflows });

    expect(await screen.findByText("Step 1 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByRole("button", { name: "Browse Workflows" })).toBeInTheDocument();
  });
});
