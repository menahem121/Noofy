import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useModelDownloadJob } from "./useModelDownloadJob";

const ACTIVE_JOB_STORAGE_KEY = "noofy.models.activeDownloadJobId";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function activeDownloadJob(status = "running") {
  return {
    job_id: "model-download-1",
    status,
    user_facing_message: "Downloading required models...",
    current_model_filename: "model.safetensors",
    current_model_index: 1,
    total_models: 1,
    bytes_downloaded: 1,
    total_bytes: 10,
    percent: 10,
    speed_bytes_per_second: null,
    models: [],
  };
}

describe("useModelDownloadJob", () => {
  const fetchMock = vi.fn();
  const onFinished = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    vi.useRealTimers();
    window.localStorage.clear();
  });

  it("clears a stale stored job without polling the missing job on startup", async () => {
    window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, "model-download-stale");
    fetchMock.mockResolvedValueOnce(jsonResponse({ job: null }));

    renderHook(() => useModelDownloadJob(onFinished));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/models/downloads/active", expect.any(Object)));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY)).toBeNull();
  });

  it("stops polling and clears storage when an active job disappears", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/models/downloads/active")) {
        return Promise.resolve(jsonResponse({ job: activeDownloadJob() }));
      }
      if (url.endsWith("/api/models/downloads/model-download-1")) {
        return Promise.resolve(
          jsonResponse(
            {
              detail: {
                code: "model_download_not_found",
                message: "Unknown model download job: model-download-1",
              },
            },
            404,
          ),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { result } = renderHook(() => useModelDownloadJob(onFinished));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.downloadJob?.job_id).toBe("model-download-1");
    expect(window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY)).toBe("model-download-1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });

    expect(result.current.downloadJob).toBeNull();
    expect(result.current.downloadError).toBeNull();
    expect(window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY)).toBeNull();
  });
});
