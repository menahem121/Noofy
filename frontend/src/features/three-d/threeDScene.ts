import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { USDZLoader } from "three/examples/jsm/loaders/USDZLoader.js";

export type ThreeDMaterialMode = "original" | "standard" | "normal" | "wireframe";
export type ThreeDUpAxis = "y" | "z" | "x";
export type ThreeDCameraType = "perspective" | "orthographic";

export interface ThreeDSceneController {
  animations: string[];
  dispose: () => void;
  resetCamera: () => void;
  screenshot: () => void;
  setAnimation: (index: number) => void;
  setAnimationPlaying: (playing: boolean) => void;
  setBackground: (color: string) => void;
  setCameraType: (type: ThreeDCameraType) => void;
  setFov: (fov: number) => void;
  setGridVisible: (visible: boolean) => void;
  setLightIntensity: (intensity: number) => void;
  setMaterialMode: (mode: ThreeDMaterialMode) => void;
  setSkeletonVisible: (visible: boolean) => void;
  setUpAxis: (axis: ThreeDUpAxis) => void;
}

interface LoadedThreeDModel {
  model: THREE.Object3D;
  baseRotation?: THREE.Euler;
  replaceBareDefaultMaterials: boolean;
  isGaussianSplat?: boolean;
  dispose?: () => void;
}

type GaussianSplatExtension = "ply" | "spz" | "splat" | "ksplat";

export async function createThreeDScene(
  container: HTMLElement,
  sourceUrl: string,
  filename: string,
): Promise<ThreeDSceneController> {
  const primaryUrl = new URL(sourceUrl, window.location.href).href;
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((candidate) => {
    const resolved = new URL(candidate, primaryUrl).href;
    if (resolved === primaryUrl || candidate.startsWith("data:") || candidate.startsWith("blob:")) return candidate;
    throw new Error("This model references an external file. Convert it to a self-contained GLB to preview it in Noofy.");
  });

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#20242b");
  const perspective = new THREE.PerspectiveCamera(55, 1, 0.01, 10000);
  const orthographic = new THREE.OrthographicCamera(-5, 5, 5, -5, 0.01, 10000);
  let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera = perspective;
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.screenSpacePanning = true;

  const ambient = new THREE.HemisphereLight(0xffffff, 0x223344, 1.4);
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(4, 8, 6);
  scene.add(ambient, key);
  const grid = new THREE.GridHelper(10, 20, 0x64748b, 0x334155);
  scene.add(grid);

  let loaded: LoadedThreeDModel;
  try {
    loaded = await loadModel(manager, sourceUrl, filename);
  } catch (reason) {
    controls.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    renderer.domElement.remove();
    throw reason;
  }
  const { model } = loaded;
  const baseRotation = loaded.baseRotation?.clone() ?? new THREE.Euler();
  applyThreeDPreviewOrientation(model, baseRotation, "y");
  if (!loaded.isGaussianSplat) {
    prepareThreeDModelForPreview(model, { replaceBareDefaultMaterials: loaded.replaceBareDefaultMaterials });
  }
  scene.add(model);
  const skeleton = loaded.isGaussianSplat ? null : new THREE.SkeletonHelper(model);
  if (skeleton) {
    skeleton.visible = false;
    scene.add(skeleton);
  }
  const originalMaterials = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
  model.traverse((child) => {
    if (child instanceof THREE.Mesh) originalMaterials.set(child, child.material);
  });
  const clips = model.animations ?? [];
  const mixer = clips.length ? new THREE.AnimationMixer(model) : null;
  let action: THREE.AnimationAction | null = null;
  let playing = false;
  let materialMode: ThreeDMaterialMode = "original";
  let upAxis: ThreeDUpAxis = "y";
  let orthographicScale = 5;
  let frame = 0;
  let disposed = false;
  const clock = new THREE.Clock();

  function dimensions() {
    return { width: Math.max(1, container.clientWidth), height: Math.max(1, container.clientHeight) };
  }
  function resize() {
    const { width, height } = dimensions();
    renderer.setSize(width, height, false);
    perspective.aspect = width / height;
    perspective.updateProjectionMatrix();
    orthographic.left = -orthographicScale * width / height;
    orthographic.right = orthographicScale * width / height;
    orthographic.top = orthographicScale;
    orthographic.bottom = -orthographicScale;
    orthographic.updateProjectionMatrix();
  }
  function resetCamera() {
    const box = boundingBoxForModel(model);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.x, size.y, size.z, 1) * 0.8;
    orthographicScale = radius * 1.25;
    resize();
    perspective.position.set(center.x + radius * 1.8, center.y + radius * 1.1, center.z + radius * 1.8);
    orthographic.position.copy(perspective.position);
    perspective.lookAt(center);
    orthographic.lookAt(center);
    controls.target.copy(center);
    controls.update();
  }
  function setMaterialMode(mode: ThreeDMaterialMode) {
    materialMode = mode;
    model.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) return;
      const original = originalMaterials.get(child);
      if (child.material !== original) disposeMaterial(child.material);
      if (mode === "original" && original) child.material = original;
      if (mode === "standard") child.material = new THREE.MeshStandardMaterial({ color: 0x9ca3af, roughness: 0.7, metalness: 0.1 });
      if (mode === "normal") child.material = new THREE.MeshNormalMaterial();
      if (mode === "wireframe") child.material = new THREE.MeshBasicMaterial({ color: 0xdbeafe, wireframe: true });
    });
  }
  function setUpAxis(axis: ThreeDUpAxis) {
    upAxis = axis;
    applyThreeDPreviewOrientation(model, baseRotation, axis);
    resetCamera();
  }
  function setCameraType(type: ThreeDCameraType) {
    const next = type === "orthographic" ? orthographic : perspective;
    next.position.copy(camera.position);
    next.quaternion.copy(camera.quaternion);
    camera = next;
    controls.object = camera;
    resize();
    controls.update();
  }
  function selectAnimation(index: number) {
    action?.stop();
    action = mixer && clips[index] ? mixer.clipAction(clips[index]) : null;
    if (action) {
      action.reset().play();
      action.paused = !playing;
    }
  }
  function render() {
    if (disposed) return;
    frame = requestAnimationFrame(render);
    const delta = clock.getDelta();
    if (playing) mixer?.update(delta);
    controls.update();
    renderer.render(scene, camera);
  }
  function disposeMaterial(material: THREE.Material | THREE.Material[]) {
    (Array.isArray(material) ? material : [material]).forEach((item) => item.dispose());
  }
  function dispose() {
    disposed = true;
    cancelAnimationFrame(frame);
    observer.disconnect();
    controls.dispose();
    mixer?.stopAllAction();
    if (!loaded.isGaussianSplat) {
      model.traverse((child) => {
        if (!(child instanceof THREE.Mesh)) return;
        child.geometry?.dispose();
        disposeMaterial(child.material);
        const original = originalMaterials.get(child);
        if (original && original !== child.material) disposeMaterial(original);
      });
    }
    loaded.dispose?.();
    skeleton?.geometry.dispose();
    if (skeleton) disposeMaterial(skeleton.material);
    grid.geometry.dispose();
    disposeMaterial(grid.material);
    renderer.dispose();
    renderer.forceContextLoss();
    renderer.domElement.remove();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();
  resetCamera();
  render();

  return {
    animations: clips.map((clip, index) => clip.name || `Animation ${index + 1}`),
    dispose,
    resetCamera,
    screenshot: () => {
      renderer.render(scene, camera);
      const link = document.createElement("a");
      link.download = `${filename.replace(/\.[^.]+$/, "") || "model"}-preview.png`;
      link.href = renderer.domElement.toDataURL("image/png");
      link.click();
    },
    setAnimation: selectAnimation,
    setAnimationPlaying: (value) => {
      playing = value;
      if (!action && clips[0]) selectAnimation(0);
      if (action) action.paused = !value;
    },
    setBackground: (color) => { scene.background = new THREE.Color(color); },
    setCameraType,
    setFov: (fov) => { perspective.fov = fov; perspective.updateProjectionMatrix(); },
    setGridVisible: (visible) => { grid.visible = visible; },
    setLightIntensity: (intensity) => { ambient.intensity = intensity * 0.65; key.intensity = intensity; },
    setMaterialMode,
    setSkeletonVisible: (visible) => { if (skeleton) skeleton.visible = visible; },
    setUpAxis,
  };
}

async function loadModel(manager: THREE.LoadingManager, url: string, filename: string): Promise<LoadedThreeDModel> {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "spz" || extension === "splat" || extension === "ksplat") {
    return loadGaussianSplatModel(extension, await loadArrayBuffer(manager, url));
  }
  if (extension === "glb" || extension === "gltf") {
    const gltf = await new GLTFLoader(manager).loadAsync(url);
    gltf.scene.animations = gltf.animations;
    return {
      model: gltf.scene,
      replaceBareDefaultMaterials: !Array.isArray(gltf.parser.json.materials) || gltf.parser.json.materials.length === 0,
    };
  }
  if (extension === "obj") return { model: await new OBJLoader(manager).loadAsync(url), replaceBareDefaultMaterials: false };
  if (extension === "fbx") return { model: await new FBXLoader(manager).loadAsync(url), replaceBareDefaultMaterials: false };
  if (extension === "stl") return { model: meshGroup(await new STLLoader(manager).loadAsync(url)), replaceBareDefaultMaterials: false };
  if (extension === "ply") return loadPlyModel(manager, url);
  if (extension === "usdz") return { model: await new USDZLoader(manager).loadAsync(url), replaceBareDefaultMaterials: false };
  throw new Error("Interactive preview is not available for this 3D format. You can still open or download the model.");
}

async function loadPlyModel(manager: THREE.LoadingManager, url: string): Promise<LoadedThreeDModel> {
  const data = await loadArrayBuffer(manager, url);
  if (isGaussianSplatPlyData(data)) return loadGaussianSplatModel("ply", data);
  return {
    model: meshGroup(new PLYLoader(manager).parse(data)),
    replaceBareDefaultMaterials: false,
  };
}

function loadArrayBuffer(manager: THREE.LoadingManager, url: string): Promise<ArrayBuffer> {
  const loader = new THREE.FileLoader(manager);
  loader.setResponseType("arraybuffer");
  return loader.loadAsync(url).then((data) => data as ArrayBuffer);
}

async function loadGaussianSplatModel(
  extension: GaussianSplatExtension,
  fileBytes: ArrayBuffer,
): Promise<LoadedThreeDModel> {
  const { SplatFileType, SplatMesh } = await import("@sparkjsdev/spark");
  const fileType = {
    ply: SplatFileType.PLY,
    spz: SplatFileType.SPZ,
    splat: SplatFileType.SPLAT,
    ksplat: SplatFileType.KSPLAT,
  }[extension];
  // Spark transfers loader buffers to its worker. Pass it an owned copy so retries
  // and browser-specific detached-buffer behavior cannot poison Noofy's source data.
  const splatFileBytes = new Uint8Array(fileBytes).slice();
  const splat = new SplatMesh({ fileBytes: splatFileBytes, fileType, editable: false });
  await splat.initialized;
  return {
    model: splat as unknown as THREE.Object3D,
    baseRotation: new THREE.Euler(0, 0, Math.PI),
    replaceBareDefaultMaterials: false,
    isGaussianSplat: true,
    dispose: () => splat.dispose(),
  };
}

export function isGaussianSplatPlyData(data: ArrayBuffer): boolean {
  const header = plyHeaderText(data);
  if (!header) return false;
  const propertyNames = new Set(
    Array.from(header.matchAll(/^property\s+\S+\s+([A-Za-z0-9_]+)\s*$/gm), (match) => match[1]?.toLowerCase()),
  );
  return Boolean(
    propertyNames.has("opacity")
      && (
        propertyNames.has("scale_0")
        || propertyNames.has("rot_0")
        || propertyNames.has("f_dc_0")
      ),
  );
}

export function applyThreeDPreviewOrientation(
  model: THREE.Object3D,
  baseRotation: THREE.Euler,
  axis: ThreeDUpAxis,
) {
  model.rotation.copy(baseRotation);
  if (axis === "z") model.rotation.x -= Math.PI / 2;
  if (axis === "x") model.rotation.z += Math.PI / 2;
}

function plyHeaderText(data: ArrayBuffer): string | null {
  const prefix = new Uint8Array(data, 0, Math.min(data.byteLength, 64 * 1024));
  const text = new TextDecoder("ascii").decode(prefix);
  const headerEnd = text.search(/end_header(?:\r\n|\n|\r|$)/);
  if (!text.startsWith("ply") || headerEnd === -1) return null;
  return text.slice(0, headerEnd);
}

function boundingBoxForModel(model: THREE.Object3D): THREE.Box3 {
  model.updateWorldMatrix(true, true);
  const box = new THREE.Box3().setFromObject(model);
  if (!box.isEmpty()) return box;
  const maybeSplat = model as THREE.Object3D & {
    getBoundingBox?: (centersOnly?: boolean) => THREE.Box3;
  };
  if (typeof maybeSplat.getBoundingBox === "function") {
    const splatBox = maybeSplat.getBoundingBox(false).clone();
    splatBox.applyMatrix4(model.matrixWorld);
    if (!splatBox.isEmpty()) return splatBox;
  }
  return new THREE.Box3(
    new THREE.Vector3(-0.5, -0.5, -0.5),
    new THREE.Vector3(0.5, 0.5, 0.5),
  );
}

export function prepareThreeDModelForPreview(
  model: THREE.Object3D,
  { replaceBareDefaultMaterials = false }: { replaceBareDefaultMaterials?: boolean } = {},
) {
  const fallbackMaterials = new Map<THREE.Material, THREE.Material>();
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    if (child.geometry.getAttribute("position") && !child.geometry.getAttribute("normal")) child.geometry.computeVertexNormals();
    if (replaceBareDefaultMaterials) child.material = prepareMaterialForPreview(child.material, fallbackMaterials);
  });
}

function prepareMaterialForPreview(
  material: THREE.Material | THREE.Material[],
  fallbackMaterials: Map<THREE.Material, THREE.Material>,
): THREE.Material | THREE.Material[] {
  if (!Array.isArray(material)) {
    if (!isBareGltfDefaultMaterial(material)) return material;
    const existing = fallbackMaterials.get(material);
    if (existing) return existing;
    const fallback = new THREE.MeshStandardMaterial({
      color: 0x9ca3af,
      metalness: 0.1,
      roughness: 0.8,
      side: THREE.DoubleSide,
      vertexColors: material.vertexColors,
    });
    fallbackMaterials.set(material, fallback);
    material.dispose();
    return fallback;
  }

  const prepared = material.map((item) => prepareMaterialForPreview(item, fallbackMaterials) as THREE.Material);
  return prepared.some((item, index) => item !== material[index]) ? prepared : material;
}

function isBareGltfDefaultMaterial(material: THREE.Material): material is THREE.MeshStandardMaterial {
  if (!(material instanceof THREE.MeshStandardMaterial) || material instanceof THREE.MeshPhysicalMaterial) return false;
  const textureKeys: (keyof THREE.MeshStandardMaterial)[] = [
    "map",
    "lightMap",
    "aoMap",
    "emissiveMap",
    "bumpMap",
    "normalMap",
    "displacementMap",
    "roughnessMap",
    "metalnessMap",
    "alphaMap",
    "envMap",
  ];
  return (
    material.name === ""
    && material.color.getHex() === 0xffffff
    && material.metalness === 1
    && material.roughness === 1
    && textureKeys.every((key) => !material[key])
  );
}

function meshGroup(geometry: THREE.BufferGeometry): THREE.Group {
  geometry.computeVertexNormals();
  const group = new THREE.Group();
  group.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0x9ca3af, roughness: 0.7, metalness: 0.1 })));
  return group;
}
