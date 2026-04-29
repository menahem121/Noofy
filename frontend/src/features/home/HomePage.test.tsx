import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomePage } from "./HomePage";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

describe("HomePage", () => {
  const fetchMock = vi.fn();
  const onOpenWorkflow = vi.fn();
  const onNavigate = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onOpenWorkflow.mockReset();
    onNavigate.mockReset();
  });

  it("loads backend runtime and workflow summaries through the Noofy API", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(
          jsonResponse({
            mode: "managed",
            reachable: true,
            base_url: "http://127.0.0.1:8188",
            repo_dir: "/tmp/ComfyUI",
            managed_process_running: true,
            pid: 123,
            error: null,
            environment: null,
          }),
        );
      }

      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "text_to_image_v0",
              name: "Text to Image",
              version: "0.1.0",
              description: "Milestone 1 text-to-image workflow package.",
            },
          ]),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

    expect(await screen.findAllByText("Engine ready")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "Text to Image" }).length).toBeGreaterThan(0);
    expect(screen.getByText("1 workflow loaded locally.")).toBeInTheDocument();
    expect(screen.getAllByText("Installed").length).toBeGreaterThan(0);
  });

  it("shows starter content and a clear status when the backend is unavailable", async () => {
    fetchMock.mockRejectedValue(new Error("connect failed"));

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

    expect(await screen.findByText("Backend is not reachable")).toBeInTheDocument();
    expect(screen.getAllByText("Backend offline")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "Text to Image" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Connect backend")).toBeInTheDocument();
  });
});
