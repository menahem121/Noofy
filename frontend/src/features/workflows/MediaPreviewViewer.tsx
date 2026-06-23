import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
  type RefObject,
  type SyntheticEvent,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { ThreeDViewer } from "../three-d/ThreeDViewer";
import { ImageComparisonSlider } from "./ImageComparisonSlider";

export function ImagePreviewViewer({
  imageUrl,
  beforeImageUrl,
  alt,
  label,
  onClose,
}: {
  imageUrl: string;
  beforeImageUrl?: string;
  alt: string;
  label: string;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const viewerRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startX: number;
    startY: number;
  } | null>(null);
  const previousImageUrlRef = useRef(imageUrl);
  const lastTapRef = useRef<{ time: number; clientX: number; clientY: number } | null>(null);
  const gestureScaleRef = useRef(1);
  const [naturalImageSize, setNaturalImageSize] = useState<{ width: number; height: number } | null>(null);
  const [stageSize, setStageSize] = useState<{ width: number; height: number } | null>(null);
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });
  const isZoomed = transform.scale > 1.001;

  const measureImageStage = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    setStageSize((current) => {
      if (current && Math.abs(current.width - rect.width) < 0.5 && Math.abs(current.height - rect.height) < 0.5) {
        return current;
      }
      return { width: rect.width, height: rect.height };
    });
  }, []);

  const zoomAtPoint = useCallback((factor: number, point: { x: number; y: number }) => {
    if (!Number.isFinite(factor) || factor <= 0) return;
    setTransform((current) => {
      const nextScale = clampImageScale(current.scale * factor);
      if (Math.abs(nextScale - current.scale) < 0.001) return current;
      if (nextScale === 1) return { scale: 1, x: 0, y: 0 };
      const ratio = nextScale / current.scale;
      return {
        scale: nextScale,
        x: point.x - ratio * (point.x - current.x),
        y: point.y - ratio * (point.y - current.y),
      };
    });
  }, []);

  const fittedImageSize = useMemo(() => {
    if (!naturalImageSize || !stageSize) return null;
    const fitScale = Math.min(1, stageSize.width / naturalImageSize.width, stageSize.height / naturalImageSize.height);
    if (!Number.isFinite(fitScale) || fitScale <= 0) return null;
    return {
      width: Math.max(1, naturalImageSize.width * fitScale),
      height: Math.max(1, naturalImageSize.height * fitScale),
    };
  }, [naturalImageSize, stageSize]);

  useEffect(() => {
    if (previousImageUrlRef.current === imageUrl) return;
    previousImageUrlRef.current = imageUrl;
    setNaturalImageSize(null);
    setStageSize(null);
    setTransform({ scale: 1, x: 0, y: 0 });
  }, [imageUrl]);

  useLayoutEffect(() => {
    measureImageStage();
    const stage = stageRef.current;
    if (!stage) return;

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(() => measureImageStage());
      observer.observe(stage);
      return () => observer.disconnect();
    }

    window.addEventListener("resize", measureImageStage);
    return () => window.removeEventListener("resize", measureImageStage);
  }, [measureImageStage]);

  useViewerCloseShortcut(closeButtonRef, onClose);

  useEffect(() => {
    const viewer = viewerRef.current;
    const stage = stageRef.current;
    if (!viewer || !stage) return;
    const viewerElement = viewer;
    const stageElement = stage;

    function handleWheel(event: globalThis.WheelEvent) {
      event.preventDefault();
      event.stopPropagation();
      const point = viewerPointFromClient(stageElement, event.clientX, event.clientY);
      zoomAtPoint(Math.exp(-event.deltaY * IMAGE_VIEWER_WHEEL_ZOOM_SENSITIVITY), point);
    }

    viewerElement.addEventListener("wheel", handleWheel, { passive: false });
    return () => viewerElement.removeEventListener("wheel", handleWheel);
  }, [zoomAtPoint]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const stage = stageRef.current;
    if (!viewer || !stage) return;
    const viewerElement = viewer;
    const stageElement = stage;

    function handleGestureStart(event: Event) {
      event.preventDefault();
      gestureScaleRef.current = 1;
    }

    function handleGestureChange(event: Event) {
      event.preventDefault();
      const gestureEvent = event as Event & { scale?: number; clientX?: number; clientY?: number };
      const gestureScale = gestureEvent.scale ?? 1;
      const stageRect = stageElement.getBoundingClientRect();
      const point = viewerPointFromClient(
        stageElement,
        gestureEvent.clientX ?? stageRect.left + stageRect.width / 2,
        gestureEvent.clientY ?? stageRect.top + stageRect.height / 2,
      );
      zoomAtPoint(Math.pow(gestureScale / gestureScaleRef.current, IMAGE_VIEWER_GESTURE_ZOOM_POWER), point);
      gestureScaleRef.current = gestureScale;
    }

    viewerElement.addEventListener("gesturestart", handleGestureStart, { passive: false });
    viewerElement.addEventListener("gesturechange", handleGestureChange, { passive: false });
    return () => {
      viewerElement.removeEventListener("gesturestart", handleGestureStart);
      viewerElement.removeEventListener("gesturechange", handleGestureChange);
    };
  }, [zoomAtPoint]);

  function resetImageView() {
    setTransform({ scale: 1, x: 0, y: 0 });
  }

  function handleImageLoad(event: SyntheticEvent<HTMLImageElement>) {
    const { naturalWidth, naturalHeight } = event.currentTarget;
    if (naturalWidth > 0 && naturalHeight > 0) {
      setNaturalImageSize({ width: naturalWidth, height: naturalHeight });
    }
    measureImageStage();
  }

  function handleImageDoubleClick(event: MouseEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();
    const stage = stageRef.current;
    const point = stage ? viewerPointFromClient(stage, event.clientX, event.clientY) : { x: 0, y: 0 };
    zoomAtPoint(isZoomed ? 1.6 : 2.5, point);
  }

  function handleImagePointerDown(event: PointerEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();

    const lastTap = lastTapRef.current;
    const now = window.performance.now();
    if (
      event.pointerType === "touch" &&
      lastTap &&
      now - lastTap.time < 320 &&
      Math.hypot(event.clientX - lastTap.clientX, event.clientY - lastTap.clientY) < 28
    ) {
      lastTapRef.current = null;
      const stage = stageRef.current;
      zoomAtPoint(isZoomed ? 1.6 : 2.5, stage ? viewerPointFromClient(stage, event.clientX, event.clientY) : { x: 0, y: 0 });
      return;
    }
    lastTapRef.current = { time: now, clientX: event.clientX, clientY: event.clientY };

    if (!isZoomed) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: transform.x,
      startY: transform.y,
    };
  }

  function handleImagePointerMove(event: PointerEvent<HTMLElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    setTransform((current) => ({
      ...current,
      x: drag.startX + event.clientX - drag.startClientX,
      y: drag.startY + event.clientY - drag.startClientY,
    }));
  }

  function finishImageDrag(event: PointerEvent<HTMLElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = null;
  }

  return createPortal(
    <div ref={viewerRef} className="widget-image-viewer" role="dialog" aria-modal="true" aria-label={`${label} full-screen preview`}>
      <div className="widget-image-viewer__bar">
        <button className="widget-image-viewer__reset" type="button" disabled={!isZoomed} onClick={resetImageView}>
          Reset View
        </button>
        <button
          ref={closeButtonRef}
          className="widget-image-viewer__close"
          type="button"
          aria-label="Close full-screen image preview"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
          Close
        </button>
      </div>
      <div ref={stageRef} className="widget-image-viewer__stage" role="presentation" onClick={onClose}>
        {beforeImageUrl ? (
          <div
            className={`widget-image-viewer__comparison${isZoomed ? " widget-image-viewer__comparison--zoomed" : ""}`}
            style={{
              width: fittedImageSize ? `${fittedImageSize.width}px` : undefined,
              height: fittedImageSize ? `${fittedImageSize.height}px` : undefined,
              transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            }}
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={handleImageDoubleClick}
            onPointerDown={handleImagePointerDown}
            onPointerMove={handleImagePointerMove}
            onPointerUp={finishImageDrag}
            onPointerCancel={finishImageDrag}
          >
            <ImageComparisonSlider
              beforeSrc={beforeImageUrl}
              afterSrc={imageUrl}
              alt={`${alt} full-screen preview`}
              onAfterImageLoad={handleImageLoad}
            />
          </div>
        ) : (
          <img
            src={imageUrl}
            alt={`${alt} full-screen preview`}
            className={`widget-image-viewer__image${isZoomed ? " widget-image-viewer__image--zoomed" : ""}`}
            draggable={false}
            style={{
              width: fittedImageSize ? `${fittedImageSize.width}px` : undefined,
              height: fittedImageSize ? `${fittedImageSize.height}px` : undefined,
              transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            }}
            onLoad={handleImageLoad}
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={handleImageDoubleClick}
            onPointerDown={handleImagePointerDown}
            onPointerMove={handleImagePointerMove}
            onPointerUp={finishImageDrag}
            onPointerCancel={finishImageDrag}
          />
        )}
      </div>
    </div>,
    document.body,
  );
}

export function VideoPreviewViewer({
  videoUrl,
  posterUrl,
  filename,
  label,
  onClose,
}: {
  videoUrl: string;
  posterUrl?: string | null;
  filename: string;
  label: string;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  useViewerCloseShortcut(closeButtonRef, onClose);

  return createPortal(
    <div className="widget-image-viewer widget-image-viewer--video" role="dialog" aria-modal="true" aria-label={`${label} full-screen preview`}>
      <div className="widget-image-viewer__bar">
        <button
          ref={closeButtonRef}
          className="widget-image-viewer__close"
          type="button"
          aria-label="Close full-screen video preview"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
          Close
        </button>
      </div>
      <div className="widget-image-viewer__stage widget-image-viewer__stage--video" role="presentation" onClick={onClose}>
        <video
          className="widget-image-viewer__video"
          controls
          src={videoUrl}
          poster={posterUrl ?? undefined}
          preload="metadata"
          aria-label={filename}
          onClick={(event) => event.stopPropagation()}
        />
      </div>
    </div>,
    document.body,
  );
}

export function ThreeDPreviewViewer({
  modelUrl,
  filename,
  size,
  label,
  onClose,
}: {
  modelUrl: string;
  filename: string;
  size?: number | null;
  label: string;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  useViewerCloseShortcut(closeButtonRef, onClose);

  return createPortal(
    <div className="widget-image-viewer widget-image-viewer--three-d" role="dialog" aria-modal="true" aria-label={`${label} full-screen preview`}>
      <div className="widget-image-viewer__bar">
        <button
          ref={closeButtonRef}
          className="widget-image-viewer__close"
          type="button"
          aria-label="Close full-screen 3D preview"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
          Close
        </button>
      </div>
      <div className="widget-image-viewer__stage widget-image-viewer__stage--three-d" role="presentation">
        <ThreeDViewer
          className="widget-image-viewer__three-d"
          url={modelUrl}
          filename={filename}
          size={size}
          autoPreviewUnknownSize
          showFullscreenButton={false}
        />
      </div>
    </div>,
    document.body,
  );
}

function useViewerCloseShortcut(
  closeButtonRef: RefObject<HTMLButtonElement | null>,
  onClose: () => void,
) {
  useEffect(() => {
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeButtonRef, onClose]);
}

function clampImageScale(scale: number) {
  return Math.min(Math.max(scale, 1), 8);
}

function viewerPointFromClient(stage: HTMLElement, clientX: number, clientY: number) {
  const rect = stage.getBoundingClientRect();
  return {
    x: clientX - (rect.left + rect.width / 2),
    y: clientY - (rect.top + rect.height / 2),
  };
}

const IMAGE_VIEWER_WHEEL_ZOOM_SENSITIVITY = 0.005;
const IMAGE_VIEWER_GESTURE_ZOOM_POWER = 1.75;
