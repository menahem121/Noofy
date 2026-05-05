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
};

describe("EngineSettingsPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
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
});
