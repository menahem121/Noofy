import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("saves API credentials through settings and emits only a reference", async () => {
    const onChange = vi.fn();
    fetchMock.mockResolvedValue(
      jsonResponse({
        status: "saved",
        provider: {
          provider: "comfy_org",
          label: "ComfyUI Account API Key",
          configured: true,
          last_four: "1234",
        },
      }),
    );

    render(
      <DashboardInputControl
        control={{
          id: "comfy_account_key",
          type: "api_credential",
          label: "ComfyUI Account API Key",
          provider: "comfy_org",
          required: true,
          secret_ref: "api-key:comfy_org",
          injection_strategy: {
            kind: "comfyui_extra_data",
            field: "api_key_comfy_org",
          },
        }}
        input={{
          id: "comfy_account_key",
          label: "ComfyUI Account API Key",
          control: "api_credential",
          binding: { node_id: "", input_name: "" },
          default: null,
          validation: {},
        }}
        value={null}
        onChange={onChange}
        onImageUpload={vi.fn()}
      />,
    );

    const field = screen.getByLabelText("ComfyUI Account API Key");
    expect(field).toHaveAttribute("type", "password");
    fireEvent.change(field, { target: { value: "raw-comfy-secret-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/settings/apis/comfy_org/key"),
        expect.objectContaining({
          body: JSON.stringify({ api_key: "raw-comfy-secret-1234" }),
        }),
      );
      expect(onChange).toHaveBeenCalledWith({
        kind: "api_key_ref",
        provider: "comfy_org",
        secret_ref: "api-key:comfy_org",
        configured: true,
        last_four: "1234",
      });
    });
  });

  it("disables the LoRA browser button when the CivitAI key is missing", () => {
    render(
      <DashboardInputControl
        control={{ id: "style_lora", type: "lora_loader", label: "Style LoRA", input_id: "style_lora" }}
        input={{
          id: "style_lora",
          label: "Style LoRA",
          control: "lora_loader",
          binding: { node_id: "12", input_name: "lora_name" },
          default: "None",
          validation: { options: ["None"] },
        }}
        value="None"
        loraBrowser={{
          enabled: false,
          disabledReason: "Requires a CivitAI API key. Add one in Settings to search and download LoRAs.",
          onOpen: vi.fn(),
        }}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    const button = screen.getByRole("button", { name: /Download more LoRAs/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      "title",
      "Requires a CivitAI API key. Add one in Settings to search and download LoRAs.",
    );
  });
});
