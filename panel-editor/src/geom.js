// DOM-free bounds for model objects (so layout / panel logic is testable in Node).
import { segsBBox, apply } from './model.js';

const hyp = (a, b) => Math.hypot(a, b);
const quad = (m, pts) => {
  const c = pts.map(([x, y]) => apply(m, x, y));
  return [Math.min(...c.map((p) => p[0])), Math.min(...c.map((p) => p[1])), Math.max(...c.map((p) => p[0])), Math.max(...c.map((p) => p[1]))];
};

export function objBounds(o) {
  if (o.type === 'path') {
    const b = segsBBox(o.segs); if (!b) return null;
    const pad = o.stroke ? o.sw / 2 : 0;
    return [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
  }
  if (o.type === 'image') return quad(o.m, [[0, 0], [1, 0], [0, 1], [1, 1]]);
  if (o.type === 'text') {
    const lines = (o.str || '').split('\n');
    const w = lines.length === 1 && o.origW ? o.origW : 0.55 * o.size * Math.max(1, ...lines.map((l) => l.length));
    const lead = o.size * (o.leading || 1.2);
    // local text space is y-up: baseline at 0, ascent above, further lines below
    return quad(o.m, [[0, -0.2 * o.size - (lines.length - 1) * lead], [w, 0.8 * o.size]]);
  }
  return null;
}

export const union = (a, b) => (!a ? b : !b ? a : [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);
export const boundsOf = (objs) => objs.reduce((u, o) => union(u, objBounds(o)), null);
