import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ImageComparisonSlider } from "./ImageComparisonSlider";

describe("ImageComparisonSlider", () => {
  it("keeps the loaded output image mounted while comparison interaction is disabled and restored", async () => {
    const onOpen = vi.fn();
    const { rerender } = render(
      <ImageComparisonSlider
        beforeSrc="/before.png"
        afterSrc="/after.png"
        alt="Generated workflow output"
        onOpen={onOpen}
      />,
    );

    const pendingImage = screen.getByAltText("Generated workflow output");
    fireEvent.load(pendingImage);
    await waitFor(() => {
      expect(screen.getByAltText("Generated workflow output")).not.toHaveClass("retained-image--pending");
    });
    const loadedImage = screen.getByAltText("Generated workflow output");

    rerender(
      <ImageComparisonSlider
        beforeSrc="/before.png"
        afterSrc="/after.png"
        alt="Generated workflow output"
        comparisonEnabled={false}
      />,
    );

    expect(screen.getByAltText("Generated workflow output")).toBe(loadedImage);
    expect(screen.queryByRole("button", { name: /open generated workflow output full-screen/i })).not.toBeInTheDocument();

    rerender(
      <ImageComparisonSlider
        beforeSrc="/before.png"
        afterSrc="/after.png"
        alt="Generated workflow output"
        onOpen={onOpen}
      />,
    );

    expect(screen.getByAltText("Generated workflow output")).toBe(loadedImage);
    fireEvent.keyDown(screen.getByRole("button", { name: /open generated workflow output full-screen/i }), {
      key: "Enter",
    });
    expect(onOpen).toHaveBeenCalledOnce();
  });
});
