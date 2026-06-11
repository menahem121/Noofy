import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RetainedImage } from "./RetainedImage";

describe("RetainedImage", () => {
  it("keeps the displayed image when a replacement fails to load", async () => {
    const onError = vi.fn();
    const { rerender } = render(
      <div style={{ position: "relative" }}>
        <RetainedImage src="/previous.png" alt="Previous result" />
      </div>,
    );

    fireEvent.load(screen.getByAltText("Previous result"));
    await waitFor(() => {
      expect(screen.getByAltText("Previous result")).not.toHaveClass("retained-image--pending");
    });

    rerender(
      <div style={{ position: "relative" }}>
        <RetainedImage src="/replacement.png" alt="New result" onError={onError} />
      </div>,
    );

    expect(screen.getByAltText("New result")).toHaveClass("retained-image--pending");
    expect(document.querySelector('img[src="/previous.png"]')).toHaveAttribute("aria-hidden", "true");

    fireEvent.error(screen.getByAltText("New result"));

    await waitFor(() => {
      expect(screen.getByAltText("Previous result")).not.toHaveAttribute("aria-hidden");
      expect(screen.queryByAltText("New result")).not.toBeInTheDocument();
    });
    expect(onError).toHaveBeenCalledOnce();
  });
});
