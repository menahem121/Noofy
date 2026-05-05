export interface GridItemLayout {
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
}

export function layoutsOverlap(a: GridItemLayout, b: GridItemLayout): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

export function fitLayout(layout: GridItemLayout, columns: number): GridItemLayout {
  const w = clamp(layout.w, layout.minW ?? 2, columns);
  return {
    ...layout,
    w,
    h: Math.max(layout.h, layout.minH ?? 2),
    x: clamp(layout.x, 0, columns - w),
    y: Math.max(0, layout.y),
  };
}

export function findAvailableLayout(
  itemId: string,
  desired: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
  columns: number,
): GridItemLayout {
  const fitted = fitLayout(desired, columns);
  if (!hasLayoutCollision(itemId, fitted, items)) return fitted;

  for (let y = fitted.y; y < fitted.y + 40; y += 1) {
    for (let x = 0; x <= columns - fitted.w; x += 1) {
      const candidate = { ...fitted, x, y };
      if (!hasLayoutCollision(itemId, candidate, items)) return candidate;
    }
  }

  const maxY = items.reduce(
    (max, item) => (item.layout ? Math.max(max, item.layout.y + item.layout.h) : max),
    0,
  );
  return { ...fitted, x: 0, y: maxY + 1 };
}

function hasLayoutCollision(
  itemId: string,
  layout: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
): boolean {
  return items.some((item) => {
    if (item.id === itemId || !item.layout) return false;
    return layoutsOverlap(layout, item.layout);
  });
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
