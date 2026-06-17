import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";

import { isGaussianSplatPlyData, prepareThreeDModelForPreview } from "./threeDScene";

function bytes(text: string): ArrayBuffer {
  const encoded = new TextEncoder().encode(text);
  return encoded.buffer.slice(encoded.byteOffset, encoded.byteOffset + encoded.byteLength);
}

function triangleGeometry() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute([
    0, 0, 0,
    1, 0, 0,
    0, 1, 0,
  ], 3));
  geometry.setIndex([0, 1, 2]);
  return geometry;
}

describe("prepareThreeDModelForPreview", () => {
  it("repairs a material-less GLTF mesh so it is visible in the preview", () => {
    const geometry = triangleGeometry();
    const material = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 1,
      roughness: 1,
    });
    const dispose = vi.spyOn(material, "dispose");
    const mesh = new THREE.Mesh(geometry, material);

    prepareThreeDModelForPreview(mesh, { replaceBareDefaultMaterials: true });

    expect(geometry.getAttribute("normal")).toBeDefined();
    expect(mesh.material).not.toBe(material);
    expect(mesh.material).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect((mesh.material as THREE.MeshStandardMaterial).color.getHex()).toBe(0x9ca3af);
    expect((mesh.material as THREE.MeshStandardMaterial).metalness).toBe(0.1);
    expect((mesh.material as THREE.MeshStandardMaterial).roughness).toBe(0.8);
    expect((mesh.material as THREE.MeshStandardMaterial).side).toBe(THREE.DoubleSide);
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("preserves authored materials while repairing missing normals", () => {
    const geometry = triangleGeometry();
    const material = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 1,
      roughness: 1,
    });
    const mesh = new THREE.Mesh(geometry, material);

    prepareThreeDModelForPreview(mesh);

    expect(geometry.getAttribute("normal")).toBeDefined();
    expect(mesh.material).toBe(material);
  });

  it("preserves textured materials in a material-less model fallback pass", () => {
    const material = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: new THREE.Texture(),
      metalness: 1,
      roughness: 1,
    });
    const mesh = new THREE.Mesh(triangleGeometry(), material);

    prepareThreeDModelForPreview(mesh, { replaceBareDefaultMaterials: true });

    expect(mesh.material).toBe(material);
  });

  it("preserves vertex colors when replacing a material-less GLTF default", () => {
    const geometry = triangleGeometry();
    geometry.setAttribute("color", new THREE.Float32BufferAttribute([
      1, 0, 0,
      0, 1, 0,
      0, 0, 1,
    ], 3));
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 1,
      roughness: 1,
      vertexColors: true,
    }));

    prepareThreeDModelForPreview(mesh, { replaceBareDefaultMaterials: true });

    expect((mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(true);
  });
});

describe("isGaussianSplatPlyData", () => {
  it("recognizes ComfyUI Gaussian splat PLY headers", () => {
    expect(isGaussianSplatPlyData(bytes([
      "ply",
      "format binary_little_endian 1.0",
      "element vertex 1",
      "property float x",
      "property float y",
      "property float z",
      "property float f_dc_0",
      "property float f_dc_1",
      "property float f_dc_2",
      "property float opacity",
      "property float scale_0",
      "property float scale_1",
      "property float scale_2",
      "property float rot_0",
      "property float rot_1",
      "property float rot_2",
      "property float rot_3",
      "end_header",
    ].join("\n")))).toBe(true);
  });

  it("keeps ordinary mesh PLY files on the mesh loader path", () => {
    expect(isGaussianSplatPlyData(bytes([
      "ply",
      "format ascii 1.0",
      "element vertex 3",
      "property float x",
      "property float y",
      "property float z",
      "element face 1",
      "property list uchar int vertex_indices",
      "end_header",
    ].join("\n")))).toBe(false);
  });
});
