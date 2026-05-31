import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GallerySaveAction } from "./GallerySaveAction";

describe("GallerySaveAction", () => {
  it("shows progress and exposes a clear cancel action while saving", () => {
    const onCancel = vi.fn();
    render(
      <GallerySaveAction
        status={{
          job_id: "job",
          control_id: "result",
          status: "saving",
          message: null,
          bytes_copied: 25,
          total_bytes: 100,
          item_ids: [],
          updated_at: "2026-05-31T00:00:00Z",
        }}
        onSave={vi.fn()}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByText("Saving 25%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Gallery save" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
