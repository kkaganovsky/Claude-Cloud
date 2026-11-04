// Lab layout rules: grid, smart snapping, alignment and auto-arrange. Pure functions (no DOM).
import { translate, transformObject } from './model.js';
import { panelFrame, panelObjects, orderedPanels, placeLabel, settings } from './panels.js';
import { union, objBounds } from './geom.js';

// Edit these to match the lab's conventions. Units are points (1 pt = 1/72 in; 1 mm = 2.835 pt).
// The "nature" preset uses Nature's published column widths (89 mm / 183 mm); everything else is a guess to be adjusted.
export const PRESETS = {
  'Nature, double column': { margin: 36, cols: 12, gutter: 8, rowGap: 8, snap: 6, labelOffset: [0, -10], alignTop: true, colSnap: true, widthPt: 518.7 },
  'Nature, single column': { margin: 20, cols: 6, gutter: 6, rowGap: 8, snap: 6, labelOffset: [0, -10], alignTop: true, colSnap: true, widthPt: 252.3 },
  'Page margins only': { margin: 36, cols: 1, gutter: 0, rowGap: 8, snap: 6, labelOffset: [0, -10], alignTop: true, colSnap: false },
};
export const layoutOf = (doc) => ({ ...PRESETS['Nature, double column'], ...(doc.settings?.layout || {}) });

// Column edges and margins as snap lines. If the preset has widthPt the grid is that wide, anchored at the left margin.
export function gridLines(page, L) {
  const W = L.widthPt ? Math.min(L.widthPt, page.w - 2 * L.margin) : page.w - 2 * L.margin;
  const colW = (W - (L.cols - 1) * L.gutter) / L.cols;
  const x = [];
  for (let c = 0; c < L.cols; c++) { const x0 = L.margin + c * (colW + L.gutter); x.push(x0, x0 + colW); }
  return { x: [...new Set(x.map((v) => +v.toFixed(3)))], y: [L.margin, page.h - L.margin], colW, left: L.margin, right: L.margin + W };
}

// Smart snapping: align edges / centres of the moving box to other boxes and grid lines.
// Returns the correction (dx,dy) to add and the guides that fired.
export function snapBox(box, others, grid, thr) {
  const xs = [box[0], (box[0] + box[2]) / 2, box[2]], ys = [box[1], (box[1] + box[3]) / 2, box[3]];
  const candX = [...(grid?.x || [])], candY = [...(grid?.y || [])];
  for (const o of others) { candX.push(o[0], (o[0] + o[2]) / 2, o[2]); candY.push(o[1], (o[1] + o[3]) / 2, o[3]); }
  const best = (vals, cands) => { let b = null; for (const v of vals) for (const c of cands) { const d = c - v; if (Math.abs(d) <= thr && (!b || Math.abs(d) < Math.abs(b.d))) b = { d, at: c }; } return b; };
  const bx = best(xs, candX), by = best(ys, candY);
  return { dx: bx?.d || 0, dy: by?.d || 0, guides: [...(bx ? [{ axis: 'x', pos: bx.at }] : []), ...(by ? [{ axis: 'y', pos: by.at }] : [])] };
}

export function alignDeltas(boxes, mode) { // returns [{dx,dy}] per box
  const u = boxes.reduce((a, b) => union(a, b), null);
  return boxes.map((b) => {
    switch (mode) {
      case 'left': return { dx: u[0] - b[0], dy: 0 };
      case 'right': return { dx: u[2] - b[2], dy: 0 };
      case 'hcenter': return { dx: (u[0] + u[2]) / 2 - (b[0] + b[2]) / 2, dy: 0 };
      case 'top': return { dx: 0, dy: u[1] - b[1] };
      case 'bottom': return { dx: 0, dy: u[3] - b[3] };
      case 'vmiddle': return { dx: 0, dy: (u[1] + u[3]) / 2 - (b[1] + b[3]) / 2 };
      default: return { dx: 0, dy: 0 };
    }
  });
}
export function distributeDeltas(boxes, axis, gap = null) { // equal gaps between boxes along an axis
  const i0 = axis === 'h' ? 0 : 1, i1 = i0 + 2;
  const order = boxes.map((b, i) => i).sort((a, b) => boxes[a][i0] - boxes[b][i0]);
  const out = boxes.map(() => ({ dx: 0, dy: 0 }));
  const total = boxes[order.at(-1)][i1] - boxes[order[0]][i0];
  const sizes = order.reduce((s, i) => s + (boxes[i][i1] - boxes[i][i0]), 0);
  const g = gap ?? (total - sizes) / Math.max(1, order.length - 1);
  let pos = boxes[order[0]][i0];
  for (const i of order) { const d = pos - boxes[i][i0]; if (axis === 'h') out[i].dx = d; else out[i].dy = d; pos += boxes[i][i1] - boxes[i][i0] + g; }
  return out;
}

// Flow the panels of one artboard into rows: reading order, top-aligned within a row, fixed gutters,
// left edges snapped to grid columns. Returns { moves: {id:{dx,dy}}, warnings }.
export function planArrange(doc, pi, L = layoutOf(doc)) {
  const page = doc.pages[pi]; const ids = orderedPanels(doc, pi); const grid = gridLines(page, L);
  const frames = Object.fromEntries(ids.map((id) => [id, panelFrame(page, id)]));
  const moves = {}, warnings = []; const extra = Math.max(0, -(L.labelOffset?.[1] || 0)); // room the letter needs above each row
  let x = grid.left, y = L.margin + extra, rowH = 0, rowStart = true;
  const right = grid.right;
  const snapLeft = (v) => { if (!L.colSnap) return v; const cands = grid.x.filter((c, i) => i % 2 === 0); // column starts
    return cands.find((c) => c >= v - 1e-6) ?? v; };
  for (const id of ids) {
    const f = frames[id]; const w = f[2] - f[0], h = f[3] - f[1];
    let px = rowStart ? grid.left : snapLeft(x);
    if (!rowStart && px + w > right + 0.5) { y += rowH + L.rowGap + extra; rowH = 0; px = grid.left; rowStart = true; }
    moves[id] = { dx: px - f[0], dy: y - f[1] };
    x = px + w + L.gutter; rowH = Math.max(rowH, h); rowStart = false;
    if (w > right - grid.left + 0.5) warnings.push(`${doc.panels[id].name} is wider than the grid (${w.toFixed(0)} pt > ${(right - grid.left).toFixed(0)} pt)`);
  }
  if (y + rowH > page.h - L.margin + 0.5) warnings.push(`Panels extend ${(y + rowH - (page.h - L.margin)).toFixed(0)} pt past the bottom margin`);
  return { moves, warnings };
}

export function applyMoves(doc, pi, moves, L = layoutOf(doc)) {
  const page = doc.pages[pi];
  for (const [id, d] of Object.entries(moves)) for (const o of panelObjects(page, id)) transformObject(o, translate(d.dx, d.dy));
  for (const id of Object.keys(moves)) placeLabel(doc, pi, id, L.labelOffset);
}
export const arrange = (doc, pi, L = layoutOf(doc)) => { const plan = planArrange(doc, pi, L); applyMoves(doc, pi, plan.moves, L); return plan; };
