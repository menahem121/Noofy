import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RequiredModelSummary } from "../../lib/api/noofyApi";
import { Fp8CompatibilityModal } from "./Fp8CompatibilityModal";

const fp8Model = { folder: "diffusion_models", filename: "model-fp8.safetensors", fp8_dtypes: ["F8_E4M3"] };

const readySummary: RequiredModelSummary = {
  workflow_id: "wf-1",
  total_count: 1,
  available_count: 1,
  possible_match_count: 0,
  missing_count: 0,
  needs_manual_download_count: 0,
  ready_to_run: true,
  models: [],
};

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function deferredJsonResponse(payload: unknown) {
  let resolve!: () => void;
  const promise = new Promise<Response>((innerResolve) => {
    resolve = () => innerResolve(jsonResponse(payload));
  });
  return { promise, resolve };
}

describe("Fp8CompatibilityModal", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    vi.useRealTimers();
  });

  function renderModal(overrides: Partial<Parameters<typeof Fp8CompatibilityModal>[0]> = {}) {
    const onResolved = vi.fn();
    const onClose = vi.fn();
    render(
      <Fp8CompatibilityModal
        workflowId="wf-1"
        models={[fp8Model]}
        onResolved={onResolved}
        onClose={onClose}
        {...overrides}
      />,
    );
    return { onResolved, onClose };
  }

  it("renders the initial state with a purple Convert action and disabled Download", () => {
    renderModal();

    expect(screen.getByText("Model not supported on Apple Silicon")).toBeInTheDocument();
    expect(screen.getByText(/can't run FP8 models/)).toBeInTheDocument();

    const convertButton = screen.getByRole("button", { name: "Convert" });
    expect(convertButton).toHaveClass("primary-button");
    expect(convertButton).toBeEnabled();

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveClass("secondary-button");
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("keeps Download disabled for invalid links and enables it for a valid https model link", () => {
    renderModal();
    const input = screen.getByPlaceholderText("https://huggingface.co/.../model-bf16.safetensors");

    fireEvent.change(input, { target: { value: "not a link" } });
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    expect(screen.getByText(/Enter a direct https link/)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "http://example.com/model.safetensors" } });
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();

    fireEvent.change(input, { target: { value: "https://example.com/readme.txt" } });
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();

    fireEvent.change(input, {
      target: { value: "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors" },
    });
    expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
  });

  it("converts the model and reports the refreshed summary", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/wf-1/fp8-compatibility/convert") && init?.method === "POST") {
        return jsonResponse({ job_id: "job-1", status: "queued", user_facing_message: null });
      }
      if (url.endsWith("/api/workflows/wf-1/fp8-compatibility/convert/job-1")) {
        return jsonResponse({
          job_id: "job-1",
          workflow_id: "wf-1",
          folder: fp8Model.folder,
          filename: fp8Model.filename,
          status: "completed",
          percent: 100,
          user_facing_message: "Model converted for Apple Silicon.",
          error_code: null,
          converted_filename: "model-fp8-converted-for-mac.safetensors",
          target_dtype: "bf16",
          source_removed: true,
          source_removal_skipped_reason: null,
          model_summary: readySummary,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    const { onResolved } = renderModal();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Convert" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });

    expect(onResolved).toHaveBeenCalledWith(readySummary);
    const convertCall = fetchMock.mock.calls.find(([request]) =>
      String(request).endsWith("/fp8-compatibility/convert"),
    );
    expect(convertCall).toBeTruthy();
    expect(JSON.parse(String(convertCall?.[1]?.body))).toEqual({
      folder: fp8Model.folder,
      filename: fp8Model.filename,
    });
  });

  it("continues polling when conversion is already running", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/wf-1/fp8-compatibility/convert") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            detail: {
              code: "fp8_conversion_already_running",
              message: "A conversion for this model is already running.",
              job_id: "job-existing",
            },
          }),
          {
            status: 409,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      if (url.endsWith("/api/workflows/wf-1/fp8-compatibility/convert/job-existing")) {
        return jsonResponse({
          job_id: "job-existing",
          workflow_id: "wf-1",
          folder: fp8Model.folder,
          filename: fp8Model.filename,
          status: "completed",
          percent: 100,
          user_facing_message: "Model converted for Apple Silicon.",
          error_code: null,
          converted_filename: "model-fp8-converted-for-mac.safetensors",
          target_dtype: "bf16",
          source_removed: true,
          source_removal_skipped_reason: null,
          model_summary: readySummary,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    const { onResolved } = renderModal();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Convert" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });

    expect(onResolved).toHaveBeenCalledWith(readySummary);
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  it("ignores rapid duplicate Convert clicks while the start request is in flight", async () => {
    const startResponse = deferredJsonResponse({
      job_id: "job-1",
      status: "queued",
      user_facing_message: null,
    });
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/fp8-compatibility/convert") && init?.method === "POST") {
        return startResponse.promise;
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderModal();
    act(() => {
      const convertButton = screen.getByRole("button", { name: "Convert" });
      fireEvent.click(convertButton);
      fireEvent.click(convertButton);
    });

    expect(
      fetchMock.mock.calls.filter(([request]) => String(request).endsWith("/fp8-compatibility/convert")),
    ).toHaveLength(1);

    await act(async () => {
      startResponse.resolve();
      await startResponse.promise;
      await Promise.resolve();
    });
  });

  it("shows conversion progress while the job is running", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/fp8-compatibility/convert") && init?.method === "POST") {
        return jsonResponse({ job_id: "job-1", status: "queued", user_facing_message: null });
      }
      return jsonResponse({
        job_id: "job-1",
        workflow_id: "wf-1",
        folder: fp8Model.folder,
        filename: fp8Model.filename,
        status: "converting",
        percent: 42,
        user_facing_message: "Converting model for Apple Silicon...",
        error_code: null,
        converted_filename: null,
        target_dtype: null,
        source_removed: null,
        source_removal_skipped_reason: null,
        model_summary: null,
      });
    });

    renderModal();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Convert" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });

    expect(screen.getByRole("progressbar", { name: "Model conversion progress" })).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Converting..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel Conversion" })).toBeInTheDocument();
  });

  it("cancels the backend job even before the first status poll", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/fp8-compatibility/convert") && init?.method === "POST") {
        return jsonResponse({ job_id: "job-1", status: "queued", user_facing_message: null });
      }
      if (url.endsWith("/fp8-compatibility/convert/job-1/cancel") && init?.method === "POST") {
        return jsonResponse({
          job_id: "job-1",
          workflow_id: "wf-1",
          folder: fp8Model.folder,
          filename: fp8Model.filename,
          status: "canceled",
          percent: null,
          user_facing_message: "Conversion canceled.",
          error_code: null,
          converted_filename: null,
          target_dtype: null,
          source_removed: null,
          source_removal_skipped_reason: null,
          model_summary: null,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderModal();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Convert" }));
    });
    // Cancel immediately — no 700ms poll has populated the job state yet.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel Conversion" }));
    });

    const cancelCall = fetchMock.mock.calls.find(([request]) =>
      String(request).endsWith("/fp8-compatibility/convert/job-1/cancel"),
    );
    expect(cancelCall).toBeTruthy();
    expect(screen.getByRole("button", { name: "Convert" })).toBeEnabled();
  });

  it("cancels conversion when the start response arrives after Cancel", async () => {
    const startResponse = deferredJsonResponse({
      job_id: "job-pending",
      status: "queued",
      user_facing_message: null,
    });
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/fp8-compatibility/convert") && init?.method === "POST") {
        return startResponse.promise;
      }
      if (url.endsWith("/fp8-compatibility/convert/job-pending/cancel") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-pending",
            workflow_id: "wf-1",
            folder: fp8Model.folder,
            filename: fp8Model.filename,
            status: "canceled",
            percent: null,
            user_facing_message: "Conversion canceled.",
            error_code: null,
            converted_filename: null,
            target_dtype: null,
            source_removed: null,
            source_removal_skipped_reason: null,
            model_summary: null,
          }),
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderModal();
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Convert" }));
    });
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel Conversion" }));
    });

    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/fp8-compatibility/convert/job-pending/cancel"),
      ),
    ).toBe(false);

    await act(async () => {
      startResponse.resolve();
      await startResponse.promise;
      await Promise.resolve();
    });

    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/fp8-compatibility/convert/job-pending/cancel"),
      ),
    ).toBe(true);
    expect(screen.getByRole("button", { name: "Convert" })).toBeEnabled();
  });

  it("starts the alternative download with the pasted link", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/wf-1/fp8-compatibility/download") && init?.method === "POST") {
        return jsonResponse({ job_id: "download-1", status: "queued", user_facing_message: "Queued" });
      }
      return jsonResponse({});
    });

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("https://huggingface.co/.../model-bf16.safetensors"), {
      target: { value: "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Download" }));
    });

    const downloadCall = fetchMock.mock.calls.find(([request]) =>
      String(request).endsWith("/fp8-compatibility/download"),
    );
    expect(downloadCall).toBeTruthy();
    expect(JSON.parse(String(downloadCall?.[1]?.body))).toEqual({
      folder: fp8Model.folder,
      filename: fp8Model.filename,
      url: "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors",
    });
  });

  it("cancels the alternative download when the start response arrives after Cancel", async () => {
    const startResponse = deferredJsonResponse({
      job_id: "download-pending",
      status: "queued",
      user_facing_message: "Queued",
    });
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/fp8-compatibility/download") && init?.method === "POST") {
        return startResponse.promise;
      }
      if (url.endsWith("/models/downloads/download-pending/cancel") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            job_id: "download-pending",
            status: "canceled",
            user_facing_message: "Model download was canceled.",
          }),
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderModal();
    fireEvent.change(screen.getByPlaceholderText("https://huggingface.co/.../model-bf16.safetensors"), {
      target: { value: "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors" },
    });
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Download" }));
    });
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel Download" }));
    });

    await act(async () => {
      startResponse.resolve();
      await startResponse.promise;
      await Promise.resolve();
    });

    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/models/downloads/download-pending/cancel"),
      ),
    ).toBe(true);
    expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
  });

  it("dismisses on Cancel and closes the dialog", async () => {
    fetchMock.mockImplementation(async () => jsonResponse({ status: "dismissed" }));
    const { onClose } = renderModal();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });

    expect(onClose).toHaveBeenCalled();
    const dismissCall = fetchMock.mock.calls.find(([request]) =>
      String(request).endsWith("/fp8-compatibility/dismiss"),
    );
    expect(dismissCall).toBeTruthy();
  });
});
