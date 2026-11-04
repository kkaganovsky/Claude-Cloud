// Document model + geometry helpers. All coordinates are in "document space":
// origin top-left of the artboard, y pointing down, units = PDF points (1/72 in).

export const IDENT = [1, 0, 0, 1, 0, 0];
export const FLIP = [1, 0, 0, -1, 0, 0];

// mul(A,B): apply B first, then A (same convention as PDF `cm` / pdf.js Util.transform).
export function mul(A, B) {
  return [
    A[0] * B[0] + A[2] * B[1],
    A[1] * B[0] + A[3] * B[1],
    A[0] * B[2] + A[2] * B[3],
    A[1] * B[2] + A[3] * B[3],
    A[0] * B[4] + A[2] * B[5] + A[4],
    A[1] * B[4] + A[3] * B[5] + A[5],
  ];
}
export const apply = (m, x, y) => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
export const det = (m) => m[0] * m[3] - m[1] * m[2];
export const scaleOf = (m) => Math.sqrt(Math.abs(det(m))) || 1;
export function invert(m) {
  const d = det(m);
  if (!d) return null;
  return [m[3] / d, -m[1] / d, -m[2] / d, m[0] / d, (m[2] * m[5] - m[3] * m[4]) / d, (m[1] * m[4] - m[0] * m[5]) / d];
}
export const translate = (x, y) => [1, 0, 0, 1, x, y];
export const scaleAbout = (sx, sy, cx, cy) => [sx, 0, 0, sy, cx - sx * cx, cy - sy * cy];
export function rotateAbout(rad, cx, cy) {
  const c = Math.cos(rad), s = Math.sin(rad);
  return [c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy];
}

let idCounter = 1;
export const newId = () => 'o' + (idCounter++).toString(36) + Math.random().toString(36).slice(2, 5);

export const clone = (o) => structuredClone(o);

// ---- path segments: ['M',x,y] ['L',x,y] ['C',x1,y1,x2,y2,x,y] ['Z'] ----
export function mapSegs(segs, m) {
  return segs.map((s) => {
    if (s[0] === 'Z') return ['Z'];
    const out = [s[0]];
    for (let i = 1; i < s.length; i += 2) {
      const p = apply(m, s[i], s[i + 1]);
      out.push(p[0], p[1]);
    }
    return out;
  });
}

export function segsToD(segs) {
  let d = '';
  for (const s of segs) {
    if (s[0] === 'Z') d += 'Z';
    else d += s[0] + s.slice(1).map((n) => +n.toFixed(3)).join(' ');
  }
  return d;
}

export function rectSegs(x, y, w, h) {
  return [['M', x, y], ['L', x + w, y], ['L', x + w, y + h], ['L', x, y + h], ['Z']];
}

const K = 0.5522847498;
export function ellipseSegs(cx, cy, rx, ry) {
  const ox = rx * K, oy = ry * K;
  return [
    ['M', cx + rx, cy],
    ['C', cx + rx, cy + oy, cx + ox, cy + ry, cx, cy + ry],
    ['C', cx - ox, cy + ry, cx - rx, cy + oy, cx - rx, cy],
    ['C', cx - rx, cy - oy, cx - ox, cy - ry, cx, cy - ry],
    ['C', cx + ox, cy - ry, cx + rx, cy - oy, cx + rx, cy],
    ['Z'],
  ];
}

// bounding box of a segment list (curves sampled, so tight enough for UI use)
export function segsBBox(segs) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const add = (x, y) => { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; };
  let cx = 0, cy = 0;
  for (const s of segs) {
    if (s[0] === 'M' || s[0] === 'L') { cx = s[1]; cy = s[2]; add(cx, cy); }
    else if (s[0] === 'C') {
      for (let i = 1; i <= 16; i++) {
        const t = i / 16, u = 1 - t;
        add(
          u * u * u * cx + 3 * u * u * t * s[1] + 3 * u * t * t * s[3] + t * t * t * s[5],
          u * u * u * cy + 3 * u * u * t * s[2] + 3 * u * t * t * s[4] + t * t * t * s[6],
        );
      }
      cx = s[5]; cy = s[6];
    }
  }
  return x0 === Infinity ? null : [x0, y0, x1, y1];
}

// Apply an affine transform T (doc space) to any object, baking it into geometry.
export function transformObject(o, T) {
  if (o.type === 'path') {
    o.segs = mapSegs(o.segs, T);
    const k = scaleOf(T);
    o.sw *= k;
    if (o.dash && o.dash[0]?.length) o.dash = [o.dash[0].map((v) => v * k), o.dash[1] * k];
  } else {
    o.m = mul(T, o.m);
  }
  for (const key of ['fill', 'stroke']) {
    if (o[key] && typeof o[key] === 'object' && o[key].grad) o[key].grad.m = mul(T, o[key].grad.m);
  }
  if (o.clips) for (const c of o.clips) c.segs = mapSegs(c.segs, T);
  return o;
}

export function newDoc(w = 612, h = 792, name = 'Untitled') {
  const layer = { id: 'L1', name: 'Layer 1', visible: true, locked: false };
  return { name, layers: [layer], pages: [{ w, h, objects: [] }], meta: {} };
}

export function makePath(segs, props = {}) {
  return {
    id: newId(), type: 'path', layer: null, segs,
    fill: '#cccccc', stroke: '#000000', sw: 1, cap: 0, join: 0, miter: 10, dash: null,
    rule: 'nonzero', opacity: 1, clips: [], ...props,
  };
}
