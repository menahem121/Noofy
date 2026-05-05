import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardInputControl } from "./DashboardInputControl";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DashboardInputControl", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("shows the uploaded asset original filename in classic image controls", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        asset_id: "12345678-1234-1234-1234-123456789abc.png",
        original_filename: "reference portrait.png",
        content_type: "image/png",
      }),
    );

    render(
      <DashboardInputControl
        control={{ id: "image", type: "load_image", label: "Input image", input_id: "image" }}
        input={{
          id: "image",
          label: "Input image",
          control: "load_image",
          binding: { node_id: "10", input_name: "image" },
          default: null,
          validation: {},
        }}
        value="12345678-1234-1234-1234-123456789abc.png"
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Loaded: reference portrait.png")).toBeInTheDocument();
    });
  });
});
