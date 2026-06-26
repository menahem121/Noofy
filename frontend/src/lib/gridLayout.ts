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

export function findNearestAvailableLayout(
  itemId: string,
  desired: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
  columns: number,
): GridItemLayout {
  const fitted = fitLayout(desired, columns);
  return findNearestAvailableLayoutFromFitted(itemId, fitted, items, columns)
    ?? fallbackLayoutBelowItems(fitted, items);
}

export function findNearestAvailablePosition(
  itemId: string,
  desired: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
  columns: number,
): GridItemLayout {
  const positioned = {
    ...desired,
    x: clamp(desired.x, 0, Math.max(0, columns - desired.w)),
    y: Math.max(0, desired.y),
  };
  return findNearestAvailableLayoutFromFitted(itemId, positioned, items, columns)
    ?? fallbackLayoutBelowItems(positioned, items);
}

export function findNearestAvailablePositionWithinRows(
  itemId: string,
  desired: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
  columns: number,
  rows: number,
): GridItemLayout | null {
  const maxY = Math.max(0, rows - desired.h);
  const positioned = {
    ...desired,
    x: clamp(desired.x, 0, Math.max(0, columns - desired.w)),
    y: clamp(desired.y, 0, maxY),
  };
  return findNearestAvailableLayoutFromFitted(itemId, positioned, items, columns, rows);
}

function findNearestAvailableLayoutFromFitted(
  itemId: string,
  fitted: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
  columns: number,
  rows?: number,
): GridItemLayout | null {
  if (!hasLayoutCollision(itemId, fitted, items)) return fitted;

  const maxY = items.reduce(
    (max, item) => (item.layout ? Math.max(max, item.layout.y + item.layout.h) : max),
    0,
  );
  const searchMaxY = rows === undefined
    ? Math.max(maxY + fitted.h + 40, fitted.y + fitted.h + 40)
    : Math.max(0, rows - fitted.h);
  let best: GridItemLayout | null = null;
  let bestScore: LayoutCandidateScore | null = null;

  for (let y = 0; y <= searchMaxY; y += 1) {
    for (let x = 0; x <= columns - fitted.w; x += 1) {
      const candidate = { ...fitted, x, y };
      if (hasLayoutCollision(itemId, candidate, items)) continue;

      const score = layoutCandidateScore(candidate, fitted);
      if (!bestScore || compareLayoutCandidateScore(score, bestScore) < 0) {
        best = candidate;
        bestScore = score;
      }
    }
  }

  return best;
}

function fallbackLayoutBelowItems(
  fitted: GridItemLayout,
  items: Array<{ id: string; layout?: GridItemLayout | null }>,
): GridItemLayout {
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

interface LayoutCandidateScore {
  squaredDistance: number;
  manhattanDistance: number;
  verticalDistance: number;
  horizontalDistance: number;
  y: number;
  x: number;
}

function layoutCandidateScore(candidate: GridItemLayout, desired: GridItemLayout): LayoutCandidateScore {
  const dx = candidate.x - desired.x;
  const dy = candidate.y - desired.y;
  return {
    squaredDistance: dx * dx + dy * dy,
    manhattanDistance: Math.abs(dx) + Math.abs(dy),
    verticalDistance: Math.abs(dy),
    horizontalDistance: Math.abs(dx),
    y: candidate.y,
    x: candidate.x,
  };
}

function compareLayoutCandidateScore(a: LayoutCandidateScore, b: LayoutCandidateScore): number {
  return (
    a.squaredDistance - b.squaredDistance ||
    a.manhattanDistance - b.manhattanDistance ||
    a.verticalDistance - b.verticalDistance ||
    a.horizontalDistance - b.horizontalDistance ||
    a.y - b.y ||
    a.x - b.x
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
