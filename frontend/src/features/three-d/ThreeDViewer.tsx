import { useEffect, useRef, useState } from "react";
import { Camera, Download, ExternalLink, Grid3X3, Maximize, RotateCcw } from "lucide-react";

import type { ThreeDCameraType, ThreeDMaterialMode, ThreeDSceneController, ThreeDUpAxis } from "./threeDScene";

const AUTO_PREVIEW_MAX_BYTES = 250 * 1024 * 1024;

interface ThreeDViewerProps {
  url: string;
  filename: string;
  size?: number | null;
  className?: string;
}

export function ThreeDViewer({ url, filename, size, className = "" }: ThreeDViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const controllerRef = useRef<ThreeDSceneController | null>(null);
  const [activated, setActivated] = useState(typeof size === "number" && size <= AUTO_PREVIEW_MAX_BYTES);
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState("");
  const [animations, setAnimations] = useState<string[]>([]);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    setActivated(typeof size === "number" && size <= AUTO_PREVIEW_MAX_BYTES);
    setPhase("idle");
    setPlaying(false);
  }, [filename, size, url]);

  useEffect(() => {
    if (!activated || !containerRef.current) return;
    let canceled = false;
    setPhase("loading");
    import("./threeDScene")
      .then(({ createThreeDScene }) => createThreeDScene(containerRef.current!, url, filename))
      .then((controller) => {
        if (canceled) { controller.dispose(); return; }
        controllerRef.current = controller;
        setAnimations(controller.animations);
        setPhase("ready");
      })
      .catch((reason) => {
        if (canceled) return;
        setError(reason instanceof Error ? reason.message : "Noofy could not preview this 3D model.");
        setPhase("error");
      });
    return () => {
      canceled = true;
      controllerRef.current?.dispose();
      controllerRef.current = null;
    };
  }, [activated, filename, url]);

  function download() {
    const link = document.createElement("a");
    const resolved = new URL(url, window.location.href);
    resolved.searchParams.set("download", "true");
    link.href = resolved.toString();
    link.download = filename;
    link.click();
  }

  return (
    <div className={`three-d-viewer ${className}`}>
      <div className="three-d-viewer__stage">
        <div className="three-d-viewer__canvas" ref={containerRef} />
        {!activated ? <button className="three-d-viewer__activate primary-button primary-button--compact" type="button" onClick={() => setActivated(true)}>Preview 3D model</button> : null}
        {phase === "loading" ? <div className="three-d-viewer__overlay">Loading 3D model...</div> : null}
        {phase === "error" ? <div className="three-d-viewer__overlay three-d-viewer__overlay--error">{error}</div> : null}
      </div>
      <div className="three-d-viewer__toolbar">
        <button type="button" onClick={() => controllerRef.current?.resetCamera()}><RotateCcw size={14} />Reset view</button>
        <button type="button" onClick={() => containerRef.current?.parentElement?.requestFullscreen()}><Maximize size={14} />Fullscreen</button>
        <button type="button" onClick={() => controllerRef.current?.screenshot()}><Camera size={14} />Screenshot</button>
        <button type="button" onClick={() => window.open(url, "_blank", "noopener,noreferrer")}><ExternalLink size={14} />Open</button>
        <button type="button" onClick={download}><Download size={14} />Download</button>
      </div>
      {phase === "ready" ? <ThreeDSettings controller={controllerRef.current} animations={animations} playing={playing} onPlaying={setPlaying} /> : null}
    </div>
  );
}

function ThreeDSettings({ controller, animations, playing, onPlaying }: { controller: ThreeDSceneController | null; animations: string[]; playing: boolean; onPlaying: (value: boolean) => void }) {
  return (
    <details className="three-d-viewer__settings">
      <summary><Grid3X3 size={14} />Viewer settings</summary>
      <div className="three-d-viewer__settings-grid">
        <label>Background<input type="color" defaultValue="#20242b" onChange={(event) => controller?.setBackground(event.target.value)} /></label>
        <label>Material<select defaultValue="original" onChange={(event) => controller?.setMaterialMode(event.target.value as ThreeDMaterialMode)}><option value="original">Original</option><option value="standard">Standard</option><option value="normal">Normals</option><option value="wireframe">Wireframe</option></select></label>
        <label>Up axis<select defaultValue="y" onChange={(event) => controller?.setUpAxis(event.target.value as ThreeDUpAxis)}><option value="y">Y up</option><option value="z">Z up</option><option value="x">X up</option></select></label>
        <label>Camera<select defaultValue="perspective" onChange={(event) => controller?.setCameraType(event.target.value as ThreeDCameraType)}><option value="perspective">Perspective</option><option value="orthographic">Orthographic</option></select></label>
        <label>FOV<input type="range" min="20" max="100" defaultValue="55" onChange={(event) => controller?.setFov(Number(event.target.value))} /></label>
        <label>Lighting<input type="range" min="0" max="5" step="0.1" defaultValue="2.2" onChange={(event) => controller?.setLightIntensity(Number(event.target.value))} /></label>
        <label><input type="checkbox" defaultChecked onChange={(event) => controller?.setGridVisible(event.target.checked)} />Grid</label>
        <label><input type="checkbox" onChange={(event) => controller?.setSkeletonVisible(event.target.checked)} />Skeleton</label>
        {animations.length ? <label>Animation<select onChange={(event) => controller?.setAnimation(Number(event.target.value))}>{animations.map((name, index) => <option value={index} key={`${name}-${index}`}>{name}</option>)}</select><button type="button" onClick={() => { controller?.setAnimationPlaying(!playing); onPlaying(!playing); }}>{playing ? "Pause" : "Play"}</button></label> : null}
      </div>
    </details>
  );
}
