import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  type SyntheticEvent,
} from "react";
import { RetainedImage } from "./RetainedImage";

const DEFAULT_COMPARISON_POSITION = 0;
const KEYBOARD_STEP = 5;

interface ImageComparisonSliderProps {
  beforeSrc: string;
  afterSrc: string;
  alt: string;
  comparisonEnabled?: boolean;
  onOpen?: () => void;
  onAfterImageLoad?: (event: SyntheticEvent<HTMLImageElement>) => void;
}

export function ImageComparisonSlider({
  beforeSrc,
  afterSrc,
  alt,
  comparisonEnabled = true,
  onOpen,
  onAfterImageLoad,
}: ImageComparisonSliderProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const dragPointerIdRef = useRef<number | null>(null);
  const [position, setPosition] = useState(DEFAULT_COMPARISON_POSITION);
  const [loadFailed, setLoadFailed] = useState(false);
  const positionStyle = {
    "--image-comparison-position": `${position}%`,
  } as CSSProperties;

  useEffect(() => {
    setPosition(DEFAULT_COMPARISON_POSITION);
    setLoadFailed(false);
  }, [beforeSrc, afterSrc]);

  function positionFromClientX(clientX: number): number | null {
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return null;
    return clamp(((clientX - rect.left) / rect.width) * 100, 0, 100);
  }

  function handlePointerDown(event: PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    dragPointerIdRef.current = event.pointerId;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const nextPosition = positionFromClientX(event.clientX);
    if (nextPosition !== null) setPosition(nextPosition);
  }

  function handlePointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (dragPointerIdRef.current !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    const nextPosition = positionFromClientX(event.clientX);
    if (nextPosition !== null) setPosition(nextPosition);
  }

  function finishPointerDrag(event: PointerEvent<HTMLButtonElement>) {
    if (dragPointerIdRef.current !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    dragPointerIdRef.current = null;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const keyDeltas: Record<string, number> = {
      ArrowLeft: -KEYBOARD_STEP,
      ArrowDown: -KEYBOARD_STEP,
      ArrowRight: KEYBOARD_STEP,
      ArrowUp: KEYBOARD_STEP,
      Home: -100,
      End: 100,
    };
    const delta = keyDeltas[event.key];
    if (delta === undefined) return;
    event.preventDefault();
    event.stopPropagation();
    setPosition((current) => {
      if (event.key === "Home") return 0;
      if (event.key === "End") return 100;
      return clamp(current + delta, 0, 100);
    });
  }

  if (!comparisonEnabled || loadFailed) {
    return (
      <div className="image-comparison-slider">
        <OutputImageStage alt={alt} onOpen={onOpen}>
          <RetainedImage
            className="image-comparison-slider__image"
            src={afterSrc}
            alt={alt}
            onLoad={onAfterImageLoad}
          />
        </OutputImageStage>
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className="image-comparison-slider"
      style={positionStyle}
    >
      <OutputImageStage alt={alt} onOpen={onOpen}>
        <RetainedImage
          className="image-comparison-slider__image image-comparison-slider__image--after"
          src={afterSrc}
          alt={alt}
          onLoad={onAfterImageLoad}
          onError={() => setLoadFailed(true)}
        />
        <span className="image-comparison-slider__before" aria-hidden="true">
          <img
            className="image-comparison-slider__image image-comparison-slider__image--before"
            src={beforeSrc}
            alt=""
            onError={() => setLoadFailed(true)}
          />
        </span>
      </OutputImageStage>
      <button
        className="image-comparison-slider__divider"
        type="button"
        role="slider"
        aria-label="Compare original image"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(position)}
        title="Drag to compare"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointerDrag}
        onPointerCancel={finishPointerDrag}
        onKeyDown={handleKeyDown}
      >
        <span className="image-comparison-slider__divider-line" aria-hidden="true" />
        <span className="image-comparison-slider__divider-knob" aria-hidden="true" />
      </button>
    </div>
  );
}

function OutputImageStage({
  alt,
  children,
  onOpen,
}: {
  alt: string;
  children: ReactNode;
  onOpen?: () => void;
}) {
  if (onOpen) {
    return (
      <button
        className="image-comparison-slider__stage image-comparison-slider__stage--button"
        type="button"
        aria-label={`Open ${alt} full-screen`}
        onClick={(event) => {
          event.stopPropagation();
          onOpen();
        }}
      >
        {children}
      </button>
    );
  }

  return <div className="image-comparison-slider__stage">{children}</div>;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
