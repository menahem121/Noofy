import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GalleryPage } from "./GalleryPage";
import type { GalleryResponse } from "../../lib/api/noofyApi";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

const galleryResponse: GalleryResponse = {
  total: 2,
  images: [
    {
      id: "img-favorite",
      thumbnailUrl: "/favorite-thumb.png",
      imageUrl: "/favorite.png",
      fileState: "available",
      workflowId: "workflow-portraits",
      workflowName: "Portrait Maker",
      prompt: "studio portrait with soft light",
      createdAt: "2026-05-05T12:00:00Z",
      width: 1024,
      height: 1024,
      favorite: true,
      usedSettings: { Style: "Portrait" },
      fileRef: "outputs/favorite.png",
    },
    {
      id: "img-landscape",
      thumbnailUrl: "/landscape-thumb.png",
      imageUrl: "/landscape.png",
      fileState: "available",
      workflowId: "workflow-landscapes",
      workflowName: "Landscape Builder",
      prompt: "wide mountain landscape at sunrise",
      createdAt: "2026-05-04T12:00:00Z",
      width: 1024,
      height: 1024,
      favorite: false,
      usedSettings: { Style: "Landscape" },
      fileRef: "outputs/landscape.png",
    },
  ],
};

describe("GalleryPage", () => {
  const fetchMock = vi.fn();
  const onNavigate = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/gallery")) {
        return Promise.resolve(jsonResponse(galleryResponse));
      }

      if (url.includes("/api/gallery/") && init?.method === "DELETE") {
        const id = url.split("/").pop() ?? "";
        return Promise.resolve(jsonResponse({ id, deleted: true }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onNavigate.mockReset();
  });

  it("shows a subtle clear button when filters are active and restores the unfiltered list", async () => {
    render(<GalleryPage onNavigate={onNavigate} />);

    expect(await screen.findByAltText("studio portrait with soft light")).toBeInTheDocument();
    expect(screen.getByAltText("wide mountain landscape at sunrise")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear gallery filters" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /sort & filter/i }));
    fireEvent.click(screen.getByRole("button", { name: /favorites only/i }));

    expect(screen.getByRole("button", { name: "Clear gallery filters" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByAltText("wide mountain landscape at sunrise")).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Clear gallery filters" }));

    expect(screen.queryByRole("button", { name: "Clear gallery filters" })).not.toBeInTheDocument();
    expect(screen.getByAltText("studio portrait with soft light")).toBeInTheDocument();
    expect(screen.getByAltText("wide mountain landscape at sunrise")).toBeInTheDocument();
  });

  it("selects multiple image cards and deletes the selected images", async () => {
    render(<GalleryPage onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: /select image: studio portrait/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select image: wide mountain/i }));

    expect(screen.getByText("2 selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download selected/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));

    await waitFor(() => {
      expect(screen.queryByAltText("studio portrait with soft light")).not.toBeInTheDocument();
      expect(screen.queryByAltText("wide mountain landscape at sunrise")).not.toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/gallery/img-favorite", expect.objectContaining({ method: "DELETE" }));
    expect(fetchMock).toHaveBeenCalledWith("/api/gallery/img-landscape", expect.objectContaining({ method: "DELETE" }));
  });

  it("downloads each selected available image", async () => {
    const downloaded: Array<{ href: string; filename: string }> = [];
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) {
      downloaded.push({ href: this.href, filename: this.download });
    });

    render(<GalleryPage onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: /select image: studio portrait/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select image: wide mountain/i }));
    fireEvent.click(screen.getByRole("button", { name: /download selected/i }));

    expect(downloaded).toHaveLength(2);
    expect(downloaded[0].href).toContain("/api/favorite.png");
    expect(downloaded[0].filename).toBe("noofy-img-favorite.png");
    expect(downloaded[1].href).toContain("/api/landscape.png");
    expect(downloaded[1].filename).toBe("noofy-img-landscape.png");

    clickSpy.mockRestore();
  });
});
