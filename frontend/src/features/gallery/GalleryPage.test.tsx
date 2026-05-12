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
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/gallery")) {
        return Promise.resolve(jsonResponse(galleryResponse));
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
});
