import { useEffect, useRef, useState, type SyntheticEvent } from "react";

interface RetainedImageProps {
  src: string;
  alt: string;
  className?: string;
  onLoad?: (event: SyntheticEvent<HTMLImageElement>) => void;
  onError?: (event: SyntheticEvent<HTMLImageElement>) => void;
}

interface DisplayedImage {
  src: string;
  alt: string;
}

export function RetainedImage({
  src,
  alt,
  className,
  onLoad,
  onError,
}: RetainedImageProps) {
  const requestedSrcRef = useRef(src);
  requestedSrcRef.current = src;
  const [displayedImage, setDisplayedImage] = useState<DisplayedImage | null>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const isPending = displayedImage?.src !== src && failedSrc !== src;
  const imageClassName = className ? `${className} retained-image` : "retained-image";

  useEffect(() => {
    setFailedSrc(null);
  }, [src]);

  function handleLoad(event: SyntheticEvent<HTMLImageElement>) {
    if (requestedSrcRef.current !== src) return;
    setFailedSrc(null);
    setDisplayedImage({ src, alt });
    onLoad?.(event);
  }

  function handleError(event: SyntheticEvent<HTMLImageElement>) {
    if (requestedSrcRef.current !== src) return;
    setFailedSrc(src);
    onError?.(event);
  }

  return (
    <>
      {displayedImage ? (
        <img
          className={imageClassName}
          src={displayedImage.src}
          alt={isPending ? "" : displayedImage.alt}
          aria-hidden={isPending ? "true" : undefined}
        />
      ) : null}
      {isPending ? (
        <img
          className={`${imageClassName} retained-image--pending`}
          src={src}
          alt={alt}
          onLoad={handleLoad}
          onError={handleError}
        />
      ) : null}
    </>
  );
}
