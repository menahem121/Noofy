import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HistoryPage } from "./HistoryPage";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

const readyRuntime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  sidecar_starting: false,
  pid: 123,
  error: null,
  environment: null,
  crash_count: 0,
  restart_attempt: 0,
  max_restart_attempts: 3,
  uptime_seconds: 12,
  last_crash_at: null,
};

const historyList = {
  total: 1,
  next_cursor: "offset-2",
  has_more: true,
  events: [
    {
      id: "run-job-1",
      type: "run",
      status: "completed",
      title: "Workflow run completed",
      workflow_id: "text_to_image",
      workflow_name: "Text to Image",
      created_at: "2026-05-13T10:00:00+00:00",
      started_at: "2026-05-13T09:59:00+00:00",
      completed_at: "2026-05-13T10:00:00+00:00",
      duration_seconds: 60,
      thumbnail_url: "/api/gallery/image-1/thumbnail",
      output_url: "/api/gallery/image-1/image",
      gallery_item_id: "image-1",
      error_summary: null,
      can_open_workflow: true,
    },
  ],
};

const historyDetail = {
  ...historyList.events[0],
  prompt: "A studio portrait",
  used_settings: {
    Prompt: "A studio portrait",
    Steps: 20,
  },
};

describe("HistoryPage", () => {
  const fetchMock = vi.fn();
  const onNavigate = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.includes("/api/history/run-job-1")) {
        return Promise.resolve(jsonResponse(historyDetail));
      }

      if (url.includes("/api/history")) {
        return Promise.resolve(jsonResponse(historyList));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onNavigate.mockReset();
  });

  it("loads real history events and sends backend filters", async () => {
    render(<HistoryPage onNavigate={onNavigate} />);

    expect(await screen.findByText("Workflow run completed")).toBeInTheDocument();
    expect(screen.getByText("Workflow: Text to Image")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "failed" } });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("status=failed"))).toBe(true);
    });
  });

  it("loads detail on selection and only shows supported actions", async () => {
    render(<HistoryPage onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open details for Workflow run completed" }));

    expect((await screen.findAllByText("A studio portrait")).length).toBeGreaterThan(0);
    expect(screen.getByText("Steps")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open result/i })).toHaveAttribute("href", "/api/gallery/image-1/image");
    expect(screen.getByRole("button", { name: /open workflow/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reuse settings/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reinstall/i })).not.toBeInTheDocument();
  });
});
