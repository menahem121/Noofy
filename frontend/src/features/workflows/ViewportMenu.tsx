import {
  useCallback,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

const VIEWPORT_MARGIN = 8;
const TRIGGER_GAP = 8;

type MenuPlacement = "top" | "bottom";

type ViewportMenuPosition = {
  left: number;
  top: number;
  maxHeight: number;
  placement: MenuPlacement;
};

export function calculateViewportMenuPosition({
  menuHeight,
  menuWidth,
  triggerRect,
  viewportHeight,
  viewportWidth,
}: {
  menuHeight: number;
  menuWidth: number;
  triggerRect: Pick<DOMRect, "bottom" | "right" | "top">;
  viewportHeight: number;
  viewportWidth: number;
}): ViewportMenuPosition {
  const availableWidth = Math.max(0, viewportWidth - VIEWPORT_MARGIN * 2);
  const fittedMenuWidth = Math.min(menuWidth, availableWidth);
  const maxLeft = Math.max(VIEWPORT_MARGIN, viewportWidth - VIEWPORT_MARGIN - fittedMenuWidth);
  const left = Math.min(Math.max(VIEWPORT_MARGIN, triggerRect.right - fittedMenuWidth), maxLeft);
  const bottomTop = triggerRect.bottom + TRIGGER_GAP;
  const topBottom = triggerRect.top - TRIGGER_GAP;
  const spaceBelow = Math.max(0, viewportHeight - VIEWPORT_MARGIN - bottomTop);
  const spaceAbove = Math.max(0, topBottom - VIEWPORT_MARGIN);
  const placement: MenuPlacement =
    menuHeight <= spaceBelow || (menuHeight > spaceAbove && spaceBelow >= spaceAbove)
      ? "bottom"
      : "top";
  const maxHeight = placement === "bottom" ? spaceBelow : spaceAbove;
  const top = placement === "bottom"
    ? bottomTop
    : Math.max(VIEWPORT_MARGIN, topBottom - Math.min(menuHeight, maxHeight));

  return { left, top, maxHeight, placement };
}

export function ViewportMenu({
  children,
  menuRef,
  open,
  triggerRef,
}: {
  children: ReactNode;
  menuRef: MutableRefObject<HTMLDivElement | null>;
  open: boolean;
  triggerRef: MutableRefObject<HTMLElement | null>;
}) {
  const [position, setPosition] = useState<ViewportMenuPosition | null>(null);

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const menu = menuRef.current;
    if (!trigger || !menu) return;

    const menuRect = menu.getBoundingClientRect();
    setPosition(calculateViewportMenuPosition({
      menuHeight: menuRect.height,
      menuWidth: menuRect.width,
      triggerRect: trigger.getBoundingClientRect(),
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
    }));
  }, [menuRef, triggerRef]);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(updatePosition);
    if (triggerRef.current) resizeObserver?.observe(triggerRef.current);
    if (menuRef.current) resizeObserver?.observe(menuRef.current);

    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      resizeObserver?.disconnect();
    };
  }, [menuRef, open, triggerRef, updatePosition]);

  if (!open) return null;

  const style: CSSProperties = position
    ? {
        left: position.left,
        maxHeight: position.maxHeight,
        top: position.top,
      }
    : { visibility: "hidden" };

  return createPortal(
    <div
      ref={(node) => {
        menuRef.current = node;
      }}
      className="canvas-options-menu__content"
      role="menu"
      aria-label="Workflow options"
      data-placement={position?.placement}
      style={style}
    >
      {children}
    </div>,
    document.body,
  );
}
