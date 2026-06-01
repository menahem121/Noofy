import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

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

  let model: THREE.Object3D;
  try {
    model = await loadModel(manager, sourceUrl, filename);
  } catch (reason) {
    controls.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    renderer.domElement.remove();
    throw reason;
  }
  scene.add(model);
  const skeleton = new THREE.SkeletonHelper(model);
  skeleton.visible = false;
  scene.add(skeleton);
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
    const box = new THREE.Box3().setFromObject(model);
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
    model.rotation.set(0, 0, 0);
    if (axis === "z") model.rotation.x = -Math.PI / 2;
    if (axis === "x") model.rotation.z = Math.PI / 2;
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
    model.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) return;
      child.geometry?.dispose();
      disposeMaterial(child.material);
      const original = originalMaterials.get(child);
      if (original && original !== child.material) disposeMaterial(original);
    });
    skeleton.geometry.dispose();
    disposeMaterial(skeleton.material);
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
    setSkeletonVisible: (visible) => { skeleton.visible = visible; },
    setUpAxis,
  };
}

async function loadModel(manager: THREE.LoadingManager, url: string, filename: string): Promise<THREE.Object3D> {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "glb" || extension === "gltf") {
    const gltf = await new GLTFLoader(manager).loadAsync(url);
    gltf.scene.animations = gltf.animations;
    return gltf.scene;
  }
  if (extension === "obj") return new OBJLoader(manager).loadAsync(url);
  if (extension === "fbx") return new FBXLoader(manager).loadAsync(url);
  if (extension === "stl") return meshGroup(await new STLLoader(manager).loadAsync(url));
  if (extension === "ply") return meshGroup(await new PLYLoader(manager).loadAsync(url));
  throw new Error("Interactive preview is not available for this 3D format. You can still open or download the model.");
}

function meshGroup(geometry: THREE.BufferGeometry): THREE.Group {
  geometry.computeVertexNormals();
  const group = new THREE.Group();
  group.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0x9ca3af, roughness: 0.7, metalness: 0.1 })));
  return group;
}
