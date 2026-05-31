import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GalleryPage } from "./GalleryPage";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

const items = [
  { id: "image-1", kind: "image", type: "image", content_url: "/api/gallery/image-1/content", thumbnail_url: "/api/gallery/image-1/thumbnail", file_state: "available", workflow_id: "wf", workflow_title: "Portrait Maker", job_id: "job", control_id: "image", output_id: "i", widget_title: "Portrait", filename: "portrait.png", mime_type: "image/png", extension: ".png", size_bytes: 100, created_at: "2026-05-05T12:00:00Z", width: 1024, height: 1024, favorite: true, generation_settings: { settings: { Prompt: "studio portrait" } } },
  { id: "video-1", kind: "video", type: "video", content_url: "/api/gallery/video-1/content", thumbnail_url: null, file_state: "available", workflow_id: "wf", workflow_title: "Motion Maker", job_id: "job", control_id: "video", output_id: "v", widget_title: "Motion", filename: "motion.webm", mime_type: "video/webm", extension: ".webm", size_bytes: 200, duration_seconds: 12, created_at: "2026-05-04T12:00:00Z", favorite: false },
  { id: "audio-1", kind: "audio", type: "audio", content_url: "/api/gallery/audio-1/content", thumbnail_url: null, file_state: "available", workflow_id: "wf", workflow_title: "Voice Maker", job_id: "job", control_id: "audio", output_id: "a", widget_title: "Voice", filename: "voice.wav", mime_type: "audio/wav", extension: ".wav", size_bytes: 300, duration_seconds: 8, created_at: "2026-05-03T12:00:00Z", favorite: false },
  { id: "file-1", kind: "file", type: "file", content_url: "/api/gallery/file-1/content", thumbnail_url: null, file_state: "available", workflow_id: "wf", workflow_title: "Transcript Maker", job_id: "job", control_id: "file", output_id: "f", widget_title: "Transcript", filename: "captions.srt", mime_type: "application/x-subrip", extension: ".srt", size_bytes: 400, created_at: "2026-05-02T12:00:00Z", favorite: false },
];

describe("GalleryPage", () => {
  const fetchMock = vi.fn();
  const onNavigate = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/gallery")) return Promise.resolve(jsonResponse({ items, total: items.length }));
      if (url.includes("/api/gallery/") && init?.method === "DELETE") return Promise.resolve(jsonResponse({ id: url.split("/").pop(), deleted: true }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onNavigate.mockReset();
  });

  it("renders intentional mixed-media cards and filters by kind", async () => {
    render(<GalleryPage onNavigate={onNavigate} />);
    expect(await screen.findByAltText("studio portrait")).toBeInTheDocument();
    expect(screen.getByText("motion.webm")).toBeInTheDocument();
    expect(screen.getByText("voice.wav")).toBeInTheDocument();
    expect(screen.getByText("captions.srt")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Audio" }));
    expect(screen.getByText("voice.wav")).toBeInTheDocument();
    expect(screen.queryByText("motion.webm")).not.toBeInTheDocument();
  });

  it("opens a safe file detail without embedding arbitrary file content", async () => {
    render(<GalleryPage onNavigate={onNavigate} />);
    fireEvent.click(await screen.findByRole("button", { name: "Open file: captions.srt" }));
    expect(screen.getByRole("dialog", { name: "File details" })).toBeInTheDocument();
    expect(screen.queryByRole("iframe")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
  });

  it("uses full image content when a thumbnail is degraded", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/gallery")) return Promise.resolve(jsonResponse({
        items: [{ ...items[0], file_state: "degraded" }],
        total: 1,
      }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    render(<GalleryPage onNavigate={onNavigate} />);
    expect(await screen.findByAltText("studio portrait")).toHaveAttribute("src", "/api/gallery/image-1/content");
  });

  it("disables open and download actions for missing files", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/gallery")) return Promise.resolve(jsonResponse({
        items: [{ ...items[3], file_state: "missing" }],
        total: 1,
      }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    render(<GalleryPage onNavigate={onNavigate} />);
    expect(await screen.findByText("Output unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select captions.srt" }));
    expect(screen.getByRole("button", { name: /download selected/i })).toBeDisabled();
    expect(screen.getByText("0 available to download")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Open file: captions.srt" }));
    expect(screen.getByRole("button", { name: "Open" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("downloads selected media directly through backend content URLs", async () => {
    const downloads: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.href); });
    render(<GalleryPage onNavigate={onNavigate} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select voice.wav" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select captions.srt" }));
    fireEvent.click(screen.getByRole("button", { name: /download selected/i }));
    expect(downloads).toHaveLength(2);
    expect(downloads.every((url) => url.includes("/api/gallery/") && url.includes("download=true"))).toBe(true);
    click.mockRestore();
  });

  it("deletes selected mixed-media items", async () => {
    render(<GalleryPage onNavigate={onNavigate} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select voice.wav" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select captions.srt" }));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    await waitFor(() => expect(screen.queryByText("voice.wav")).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/api/gallery/audio-1", expect.objectContaining({ method: "DELETE" }));
    expect(fetchMock).toHaveBeenCalledWith("/api/gallery/file-1", expect.objectContaining({ method: "DELETE" }));
  });
});
