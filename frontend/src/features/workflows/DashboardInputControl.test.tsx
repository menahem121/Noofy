import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardInputControl } from "./DashboardInputControl";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockMaskEditorCanvas() {
  const calls: string[] = [];
  const contexts = new WeakMap<HTMLCanvasElement, any>();
  vi.stubGlobal("Image", class {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    naturalWidth = 2;
    naturalHeight = 1;
    width = 2;
    height = 1;

    set src(_value: string) {
      queueMicrotask(() => this.onload?.());
    }
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 100,
    bottom: 50,
    width: 100,
    height: 50,
    toJSON: () => ({}),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "hasPointerCapture", {
    configurable: true,
    value: vi.fn(() => true),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "setPointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "releasePointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function getContextMock(this: HTMLCanvasElement) {
    const existing = contexts.get(this);
    if (existing) return existing;
    const context: any = {
      lastStyle: "",
      clearRect: vi.fn(() => calls.push("clear")),
      drawImage: vi.fn(),
      getImageData: vi.fn((_x: number, _y: number, width: number, height: number) => ({
        data: new Uint8ClampedArray(width * height * 4),
        width,
        height,
        colorSpace: "srgb",
      })),
      putImageData: vi.fn(() => calls.push("reset")),
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(() => calls.push(`stroke:${context.lastStyle}`)),
      arc: vi.fn(),
      fill: vi.fn(() => calls.push(`fill:${context.lastStyle}`)),
      set strokeStyle(value: string) {
        context.lastStyle = value;
      },
      get strokeStyle() {
        return context.lastStyle;
      },
      set fillStyle(value: string) {
        context.lastStyle = value;
      },
      get fillStyle() {
        return context.lastStyle;
      },
      lineCap: "round",
      lineJoin: "round",
      lineWidth: 1,
      globalCompositeOperation: "source-over",
    };
    contexts.set(this, context);
    return context;
  });
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function toBlobMock(this: HTMLCanvasElement, callback, type) {
    const context = contexts.get(this);
    callback(new Blob([context?.lastStyle ?? ""], { type: type ?? "image/png" }));
  });
  return { calls };
}

function renderEditableImageWithMask({
  onImageMaskApply,
  onChange,
}: {
  onImageMaskApply: (sourceAssetId: string, mask: Blob) => Promise<string>;
  onChange: (value: unknown) => void;
}) {
  mockMaskEditorCanvas();
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/metadata")) {
      return Promise.resolve(jsonResponse({
        asset_id: "12345678-1234-1234-1234-123456789abc.png",
        original_filename: "reference.png",
        content_type: "image/png",
        kind: "image",
      }));
    }
    return Promise.resolve(new Response(new Blob(["image"], { type: "image/png" })));
  });

  return render(
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
      onChange={onChange}
      onImageUpload={vi.fn()}
      onImageMaskApply={onImageMaskApply}
    />,
  );
}

describe("DashboardInputControl", () => {
  const fetchMock = vi.fn();
  const createObjectUrlMock = vi.fn(() => "blob:noofy-upload-preview");
  const revokeObjectUrlMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectUrlMock,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectUrlMock,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    createObjectUrlMock.mockClear();
    revokeObjectUrlMock.mockClear();
  });

  it("renders integer jump sliders with configured min, max, and step", () => {
    const onChange = vi.fn();

    render(
      <DashboardInputControl
        control={{ id: "width", type: "slider", label: "Width", input_id: "width" }}
        input={{
          id: "width",
          label: "Width",
          control: "slider",
          binding: { node_id: "5", input_name: "width" },
          default: 1024,
          validation: { min: 0, max: 2048, step: 512 },
        }}
        value={1024}
        onChange={onChange}
        onImageUpload={vi.fn()}
      />,
    );

    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("min", "0");
    expect(slider).toHaveAttribute("max", "2048");
    expect(slider).toHaveAttribute("step", "512");

    fireEvent.change(slider, { target: { value: "1536" } });
    expect(onChange).toHaveBeenCalledWith(1536);
  });

  it("renders decimal sliders with configured step size", () => {
    const onChange = vi.fn();

    render(
      <DashboardInputControl
        control={{ id: "strength", type: "slider", label: "Strength", input_id: "strength" }}
        input={{
          id: "strength",
          label: "Strength",
          control: "slider",
          binding: { node_id: "3", input_name: "denoise" },
          default: 0.5,
          validation: { min: 0, max: 1, step: 0.25 },
        }}
        value={0.5}
        onChange={onChange}
        onImageUpload={vi.fn()}
      />,
    );

    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("step", "0.25");

    fireEvent.change(slider, { target: { value: "0.75" } });
    expect(onChange).toHaveBeenCalledWith(0.75);
  });

  it("shows a split upload and Gallery chooser when no image is selected", () => {
    const onImageUpload = vi.fn();
    const { container } = render(
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
        value={null}
        onChange={vi.fn()}
        onImageUpload={onImageUpload}
      />,
    );

    const uploadTarget = screen.getByRole("button", { name: "Upload from computer" });
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeInTheDocument();
    expect(fileInput).toHaveClass("dashboard-image-input__file");
    expect(screen.getByRole("button", { name: "Choose from Gallery" })).toBeInTheDocument();
    expect(screen.queryByText(/Image not found/i)).not.toBeInTheDocument();

    const clickSpy = vi.spyOn(fileInput!, "click").mockImplementation(() => undefined);
    fireEvent.click(uploadTarget);
    expect(clickSpy).toHaveBeenCalled();

    const file = new File(["image"], "reference.png", { type: "image/png" });
    fireEvent.change(fileInput!, { target: { files: [file] } });
    expect(onImageUpload).toHaveBeenCalledWith(file);
  });

  it("shows the uploaded asset preview and original filename in classic image controls", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/metadata")) {
        return Promise.resolve(
          jsonResponse({
            asset_id: "12345678-1234-1234-1234-123456789abc.png",
            original_filename: "reference portrait.png",
            content_type: "image/png",
          }),
        );
      }
      return Promise.resolve(new Response(new Blob(["image"], { type: "image/png" })));
    });

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
      expect(screen.getByAltText("Uploaded input")).toHaveAttribute("src", "blob:noofy-upload-preview");
      expect(screen.getByText("reference portrait.png")).toBeInTheDocument();
      expect(screen.getByText("Replace image")).toBeInTheDocument();
    });
  });

  it.each(["load_image", "load_image_mask"] as const)("shows Mask for editable %s assets", async (controlType) => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/metadata")) {
        return Promise.resolve(jsonResponse({
          asset_id: "12345678-1234-1234-1234-123456789abc.png",
          original_filename: "reference.png",
          content_type: "image/png",
          kind: "image",
        }));
      }
      return Promise.resolve(new Response(new Blob(["image"], { type: "image/png" })));
    });

    render(
      <DashboardInputControl
        control={{ id: "image", type: controlType, label: "Input image", input_id: "image" }}
        input={{
          id: "image",
          label: "Input image",
          control: controlType,
          binding: { node_id: "10", input_name: "image" },
          default: null,
          validation: {},
        }}
        value="12345678-1234-1234-1234-123456789abc.png"
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
        onImageMaskApply={vi.fn()}
      />,
    );

    expect(await screen.findByRole("button", { name: "Mask" })).toBeInTheDocument();
  });

  it.each(["load_image", "load_image_mask"] as const)("copies a selected Gallery image before opening the mask editor for %s", async (controlType) => {
    mockMaskEditorCanvas();
    const prepareGalleryMask = vi.fn().mockResolvedValue("22222222-2222-2222-2222-222222222222.png");
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/metadata")) {
        return Promise.resolve(jsonResponse({
          asset_id: "22222222-2222-2222-2222-222222222222.png",
          original_filename: "portrait.png",
          content_type: "image/png",
          kind: "image",
        }));
      }
      return Promise.resolve(new Response(new Blob(["image"], { type: "image/png" })));
    });

    render(
      <DashboardInputControl
        control={{ id: "image", type: controlType, label: "Input image", input_id: "image" }}
        input={{
          id: "image",
          label: "Input image",
          control: controlType,
          binding: { node_id: "10", input_name: "image" },
          default: null,
          validation: {},
        }}
        value={{
          source: "gallery",
          gallery_item_id: "gallery-image-1",
          kind: "image",
          filename: "portrait.png",
        }}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
        onGalleryImageMaskPrepare={prepareGalleryMask}
        onImageMaskApply={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Mask" }));

    await waitFor(() => {
      expect(prepareGalleryMask).toHaveBeenCalledWith("image", "gallery-image-1");
      expect(screen.getByRole("dialog", { name: "Mask" })).toBeInTheDocument();
    });
  });

  it("applies soft mask opacity and updates the input value to the masked asset", async () => {
    const onChange = vi.fn();
    const onImageMaskApply = vi.fn(async (_sourceAssetId: string, mask: Blob) => {
      expect(await mask.text()).toContain("0.5");
      return "33333333-3333-3333-3333-333333333333.png";
    });
    const { container } = renderEditableImageWithMask({
      onImageMaskApply,
      onChange,
    });

    fireEvent.click(await screen.findByRole("button", { name: "Mask" }));
    await screen.findByRole("dialog", { name: "Mask" });
    fireEvent.change(screen.getByLabelText("Mask strength"), { target: { value: "0.5" } });
    const maskCanvas = container.ownerDocument.querySelector(".mask-editor__canvas--mask") as HTMLCanvasElement;
    fireEvent.pointerDown(maskCanvas, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerUp(maskCanvas, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Apply mask" }));

    await waitFor(() => {
      expect(onImageMaskApply).toHaveBeenCalledWith("12345678-1234-1234-1234-123456789abc.png", expect.any(Blob));
      expect(onChange).toHaveBeenCalledWith("33333333-3333-3333-3333-333333333333.png");
      expect(screen.queryByRole("dialog", { name: "Mask" })).not.toBeInTheDocument();
    });
  });

  it("clears and resets the mask canvas", async () => {
    const canvasMock = mockMaskEditorCanvas();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/metadata")) {
        return Promise.resolve(jsonResponse({
          asset_id: "12345678-1234-1234-1234-123456789abc.png",
          original_filename: "reference.png",
          content_type: "image/png",
          kind: "image",
          has_mask: true,
          source_asset_id: "11111111-1111-1111-1111-111111111111.png",
        }));
      }
      return Promise.resolve(new Response(new Blob(["image"], { type: "image/png" })));
    });

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
        onImageMaskApply={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Mask" }));
    await screen.findByRole("dialog", { name: "Mask" });
    fireEvent.click(screen.getByRole("button", { name: "Clear mask" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset mask" }));

    expect(canvasMock.calls).toContain("clear");
    expect(canvasMock.calls).toContain("reset");
  });

  it("shows a recoverable missing-asset state only when a selected asset cannot load", async () => {
    fetchMock.mockRejectedValue(new Error("missing asset"));

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
        onImageMaskApply={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Image could not be loaded")).toBeInTheDocument();
      expect(screen.getByText("Upload from computer")).toBeInTheDocument();
      expect(screen.queryByText(/Image not found/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Mask" })).not.toBeInTheDocument();
    });
  });

  it("selects a paged Gallery image reference without persisting media URLs", async () => {
    const onChange = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/gallery")) {
        expect(url).toContain("kind=image");
        expect(url).toContain("limit=50");
        expect(url).toContain("accepted_extensions=.png");
        return Promise.resolve(jsonResponse({
          total: 1,
          next_cursor: null,
          items: [{
            id: "gallery-image-1",
            kind: "image",
            type: "image",
            content_url: "/api/gallery/gallery-image-1/content?token=secret",
            thumbnail_url: "/api/gallery/gallery-image-1/thumbnail?token=secret",
            file_state: "available",
            workflow_id: "wf",
            workflow_title: "Workflow",
            job_id: "job",
            control_id: "result",
            output_id: "image",
            widget_title: "Result",
            filename: "portrait.png",
            mime_type: "image/png",
            extension: ".png",
            size_bytes: 123,
            width: 64,
            height: 64,
            duration_seconds: null,
            fps: null,
            favorite: false,
            generation_settings: {},
          }],
        }));
      }
      return Promise.resolve(jsonResponse({}));
    });

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
        value={null}
        onChange={onChange}
        onImageUpload={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Choose from Gallery" }));
    fireEvent.click(await screen.findByRole("button", { name: /portrait.png/i }));
    fireEvent.click(screen.getByRole("button", { name: "Select" }));

    expect(onChange).toHaveBeenCalledWith({
      source: "gallery",
      gallery_item_id: "gallery-image-1",
      kind: "image",
      filename: "portrait.png",
      extension: ".png",
      mime_type: "image/png",
      size_bytes: 123,
      width: 64,
      height: 64,
      duration_seconds: null,
      fps: null,
    });
    expect(JSON.stringify(onChange.mock.calls[0][0])).not.toContain("token=secret");
    expect(createObjectUrlMock).not.toHaveBeenCalled();
  });

  it("renders a selected Gallery image through its durable content endpoint", () => {
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
        value={{
          source: "gallery",
          gallery_item_id: "gallery-image-1",
          kind: "image",
          filename: "portrait.png",
        }}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    expect(screen.getByAltText("Gallery input")).toHaveAttribute(
      "src",
      expect.stringContaining("/api/gallery/gallery-image-1/content"),
    );
  });

  it("closes the Gallery picker with Escape", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ total: 0, next_cursor: null, items: [] }));
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
        value={null}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Choose from Gallery" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows only the Gallery loading error when the first picker request fails", async () => {
    fetchMock.mockRejectedValue(new Error("Gallery could not be loaded."));
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
        value={null}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Choose from Gallery" }));
    expect(await screen.findByText("Gallery could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("No compatible image items found.")).not.toBeInTheDocument();
  });

  it("filters Gallery results by workflow-specific media validation", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      expect(url).toContain("accepted_extensions=.png");
      expect(url).toContain("accepted_mime_types=image%2Fpng");
      expect(url).not.toContain("accepted_extensions=.jpg");
      return Promise.resolve(jsonResponse({ total: 0, next_cursor: null, items: [] }));
    });
    render(
      <DashboardInputControl
        control={{ id: "image", type: "load_image", label: "Input image", input_id: "image" }}
        input={{
          id: "image",
          label: "Input image",
          control: "load_image",
          binding: { node_id: "10", input_name: "image" },
          default: null,
          validation: { accepted_extensions: [".png"], accepted_mime_types: ["image/png"] },
        }}
        value={null}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Choose from Gallery" }));
    expect(await screen.findByText("No compatible image items found.")).toBeInTheDocument();
  });

  it.each([
    ["load_audio", "audio", "speech.wav", ".wav", "audio/wav"],
    ["load_video", "video", "clip.mp4", ".mp4", "video/mp4"],
    ["load_3d", "3d", "mesh.glb", ".glb", "model/gltf-binary"],
  ] as const)("selects a Gallery %s reference through paged backend results", async (controlType, kind, filename, extension, mimeType) => {
    const onChange = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      expect(url).toContain(`/api/gallery?`);
      expect(url).toContain(`kind=${encodeURIComponent(kind)}`);
      expect(url).toContain("limit=50");
      return Promise.resolve(jsonResponse({
        total: 1,
        next_cursor: null,
        items: [{
          id: `gallery-${kind}-1`,
          kind,
          type: kind,
          content_url: `/api/gallery/gallery-${kind}-1/content?token=secret`,
          thumbnail_url: null,
          file_state: "available",
          workflow_id: "wf",
          workflow_title: "Workflow",
          job_id: "job",
          control_id: "result",
          output_id: kind,
          widget_title: "Result",
          filename,
          mime_type: mimeType,
          extension,
          size_bytes: 456,
          width: kind === "video" ? 1920 : null,
          height: kind === "video" ? 1080 : null,
          duration_seconds: kind === "audio" || kind === "video" ? 2 : null,
          fps: kind === "video" ? 24 : null,
          favorite: false,
          generation_settings: {},
        }],
      }));
    });

    render(
      <DashboardInputControl
        control={{ id: kind, type: controlType, label: "Input media", input_id: kind }}
        input={{
          id: kind,
          label: "Input media",
          control: controlType,
          binding: { node_id: "10", input_name: "media" },
          default: null,
          validation: {},
        }}
        value={null}
        onChange={onChange}
        onImageUpload={vi.fn()}
        onAudioUpload={vi.fn()}
        onVideoUpload={vi.fn()}
        onThreeDUpload={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Choose from Gallery" }));
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(filename) }));
    fireEvent.click(screen.getByRole("button", { name: "Select" }));

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      source: "gallery",
      gallery_item_id: `gallery-${kind}-1`,
      kind,
      filename,
      extension,
      mime_type: mimeType,
      size_bytes: 456,
    }));
    expect(JSON.stringify(onChange.mock.calls[0][0])).not.toContain("token=secret");
  });

  it("does not offer Gallery selection for image masks or generic file inputs", () => {
    const { rerender } = render(
      <DashboardInputControl
        control={{ id: "mask", type: "load_image_mask", label: "Mask", input_id: "mask" }}
        input={{
          id: "mask",
          label: "Mask",
          control: "load_image_mask",
          binding: { node_id: "10", input_name: "mask" },
          default: null,
          validation: {},
        }}
        value={null}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Choose from Gallery" })).not.toBeInTheDocument();

    rerender(
      <DashboardInputControl
        control={{ id: "file", type: "load_file", label: "Source file", input_id: "file" }}
        input={{
          id: "file",
          label: "Source file",
          control: "load_file",
          binding: { node_id: "10", input_name: "file" },
          default: null,
          validation: { accepted_extensions: [".json"], accepted_mime_types: ["application/json"] },
        }}
        value={null}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
        onFileUpload={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Choose from Gallery" })).not.toBeInTheDocument();
  });

  it("renders audio assets through backend media URLs with metadata and remove controls", async () => {
    const onChange = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      expect(url).toContain("/api/assets/12345678-1234-1234-1234-123456789abc.wav/metadata");
      return Promise.resolve(
        jsonResponse({
          asset_id: "12345678-1234-1234-1234-123456789abc.wav",
          kind: "audio",
          original_filename: "narration.wav",
          content_type: "audio/wav",
          size: 2048,
          format: "wav",
          duration_seconds: 3.5,
        }),
      );
    });

    render(
      <DashboardInputControl
        control={{ id: "audio", type: "load_audio", label: "Input audio", input_id: "audio" }}
        input={{
          id: "audio",
          label: "Input audio",
          control: "load_audio",
          binding: { node_id: "10", input_name: "audio_path" },
          default: null,
          validation: {},
        }}
        value="12345678-1234-1234-1234-123456789abc.wav"
        onChange={onChange}
        onImageUpload={vi.fn()}
        onAudioUpload={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("narration.wav")).toBeInTheDocument();
      expect(screen.getByText(/WAV/)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenCalledWith(null);
    const audio = document.querySelector("audio");
    expect(audio).toHaveAttribute("src", "/api/assets/12345678-1234-1234-1234-123456789abc.wav");
    expect(createObjectUrlMock).not.toHaveBeenCalled();
  });

  it("renders selected 3D assets through the guarded shared viewer", async () => {
    const onChange = vi.fn();
    fetchMock.mockResolvedValue(jsonResponse({
      asset_id: "12345678-1234-1234-1234-123456789abc.glb",
      kind: "3d",
      original_filename: "mesh.glb",
      content_type: "model/gltf-binary",
      extension: ".glb",
      size: null,
    }));

    render(
      <DashboardInputControl
        control={{ id: "model", type: "load_3d", label: "Input model", input_id: "model" }}
        input={{
          id: "model",
          label: "Input model",
          control: "load_3d",
          binding: { node_id: "10", input_name: "model_file" },
          default: null,
          validation: {},
        }}
        value="12345678-1234-1234-1234-123456789abc.glb"
        onChange={onChange}
        onImageUpload={vi.fn()}
        onThreeDUpload={vi.fn()}
      />,
    );

    expect(await screen.findByText("mesh.glb")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview 3D model" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("allows a long audio upload to be canceled", async () => {
    let uploadSignal: AbortSignal | undefined;
    const onAudioUpload = vi.fn((_file: File, _onProgress: unknown, signal?: AbortSignal) => {
      uploadSignal = signal;
      return new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new Error("Audio upload was canceled.")), { once: true });
      });
    });
    const { container } = render(
      <DashboardInputControl
        control={{ id: "audio", type: "load_audio", label: "Input audio", input_id: "audio" }}
        input={{
          id: "audio",
          label: "Input audio",
          control: "load_audio",
          binding: { node_id: "10", input_name: "audio" },
          default: null,
          validation: {},
        }}
        value={null}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
        onAudioUpload={onAudioUpload}
      />,
    );

    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    fireEvent.change(fileInput!, { target: { files: [new File(["audio"], "speech.wav", { type: "audio/wav" })] } });
    fireEvent.click(await screen.findByRole("button", { name: "Cancel upload" }));

    expect(uploadSignal?.aborted).toBe(true);
    expect(await screen.findByText("Audio upload was canceled.")).toBeInTheDocument();
  });

  it("renders video assets through backend media URLs with metadata and remove controls", async () => {
    const onChange = vi.fn();
    fetchMock.mockResolvedValue(
      jsonResponse({
        asset_id: "12345678-1234-1234-1234-123456789abc.mp4",
        kind: "video",
        original_filename: "demo.mp4",
        content_type: "video/mp4",
        size: 4096,
        format: "mp4",
      }),
    );

    render(
      <DashboardInputControl
        control={{ id: "video", type: "load_video", label: "Input video", input_id: "video" }}
        input={{
          id: "video",
          label: "Input video",
          control: "load_video",
          binding: { node_id: "10", input_name: "video_path" },
          default: null,
          validation: {},
        }}
        value="12345678-1234-1234-1234-123456789abc.mp4"
        onChange={onChange}
        onImageUpload={vi.fn()}
        onVideoUpload={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("demo.mp4")).toBeInTheDocument();
      expect(screen.getByText(/MP4/)).toBeInTheDocument();
    });
    const video = document.querySelector("video");
    expect(video).toHaveAttribute("src", "/api/assets/12345678-1234-1234-1234-123456789abc.mp4");
    fireEvent.loadedMetadata(video!);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenCalledWith(null);
    expect(createObjectUrlMock).not.toHaveBeenCalled();
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

  it("always shows None as the first LoRA option", () => {
    render(
      <DashboardInputControl
        control={{ id: "style_lora", type: "lora_loader", label: "Style LoRA", input_id: "style_lora" }}
        input={{
          id: "style_lora",
          label: "Style LoRA",
          control: "lora_loader",
          binding: { node_id: "12", input_name: "lora_name" },
          default: "existing.safetensors",
          validation: { options: ["existing.safetensors", "None"] },
        }}
        value="existing.safetensors"
        loraBrowser={{ enabled: true, onOpen: vi.fn() }}
        onChange={vi.fn()}
        onImageUpload={vi.fn()}
      />,
    );

    const select = screen.getByDisplayValue("existing.safetensors") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.value)).toEqual(["None", "existing.safetensors"]);
  });

  it("renders selected generic file metadata and supports remove", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      asset_id: "asset.json",
      original_filename: "settings.json",
      content_type: "application/json",
      kind: "file",
      extension: ".json",
      size: 128,
    }));
    const onChange = vi.fn();

    render(
      <DashboardInputControl
        control={{ id: "source-file", type: "load_file", label: "Source file", input_id: "source-file" }}
        input={{
          id: "source-file",
          label: "Source file",
          control: "load_file",
          binding: { node_id: "10", input_name: "file_path" },
          default: null,
          validation: { accepted_extensions: [".json"], accepted_mime_types: ["application/json"] },
        }}
        value="asset.json"
        onChange={onChange}
        onImageUpload={vi.fn()}
      />,
    );

    expect(await screen.findByText("settings.json")).toBeInTheDocument();
    expect(screen.getByText("JSON · 128 B")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Remove/i }));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
