import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThreeDViewer } from "./ThreeDViewer";

describe("ThreeDViewer", () => {
  it("requires an explicit preview action when the model size is unknown", () => {
    render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={null} />);

    expect(screen.getByRole("button", { name: "Preview 3D model" })).toBeInTheDocument();
  });

  it("requires an explicit preview action above the 250 MB auto-preview guard", () => {
    render(<ThreeDViewer url="/api/models/scene.glb" filename="scene.glb" size={250 * 1024 * 1024 + 1} />);

    expect(screen.getByRole("button", { name: "Preview 3D model" })).toBeInTheDocument();
  });
});
