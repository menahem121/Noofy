import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThreeDViewer } from "./ThreeDViewer";

const { createThreeDScene, dispose } = vi.hoisted(() => ({
  createThreeDScene: vi.fn(),
  dispose: vi.fn(),
}));

vi.mock("./threeDScene", () => ({
  createThreeDScene,
}));

describe("ThreeDViewer", () => {
  beforeEach(() => {
    dispose.mockReset();
    createThreeDScene.mockReset().mockResolvedValue({
      animations: [],
      dispose,
    });
  });

  it("requires an explicit preview action when the model size is unknown", () => {
    render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={null} />);

    expect(screen.getByRole("button", { name: "Preview 3D model" })).toBeInTheDocument();
  });

  it("auto-previews a generated result when the model size is unknown", async () => {
    render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={null} autoPreviewUnknownSize />);

    expect(screen.queryByRole("button", { name: "Preview 3D model" })).not.toBeInTheDocument();
    await waitFor(() => expect(createThreeDScene).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Reset view" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Screenshot" })).toBeEnabled();
  });

  it("requires an explicit preview action above the 250 MB auto-preview guard", () => {
    render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={250 * 1024 * 1024 + 1} autoPreviewUnknownSize />);

    expect(screen.getByRole("button", { name: "Preview 3D model" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset view" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Screenshot" })).toBeDisabled();
  });

  it("does not load a guarded model when the viewer source changes", async () => {
    const { rerender } = render(<ThreeDViewer url="/api/models/small.glb" filename="small.glb" size={1024} />);
    await waitFor(() => expect(createThreeDScene).toHaveBeenCalledWith(expect.anything(), "/api/models/small.glb", "small.glb"));

    rerender(<ThreeDViewer url="/api/models/large.glb" filename="large.glb" size={250 * 1024 * 1024 + 1} />);

    expect(screen.getByRole("button", { name: "Preview 3D model" })).toBeInTheDocument();
    await waitFor(() => expect(dispose).toHaveBeenCalledOnce());
    expect(createThreeDScene).toHaveBeenCalledTimes(1);
  });

  it("keeps a loaded scene ready when its size metadata arrives", async () => {
    const { rerender } = render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={null} autoPreviewUnknownSize />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Reset view" })).toBeEnabled());

    rerender(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={1024} autoPreviewUnknownSize />);

    expect(screen.getByRole("button", { name: "Reset view" })).toBeEnabled();
    expect(createThreeDScene).toHaveBeenCalledTimes(1);
    expect(dispose).not.toHaveBeenCalled();
  });

  it("can retry a transient scene load failure", async () => {
    createThreeDScene
      .mockRejectedValueOnce(new Error("Model fetch failed."))
      .mockResolvedValueOnce({ animations: [], dispose });
    render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={1024} />);

    fireEvent.click(await screen.findByRole("button", { name: "Retry preview" }));

    await waitFor(() => expect(createThreeDScene).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry preview" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Reset view" })).toBeEnabled();
  });
});
