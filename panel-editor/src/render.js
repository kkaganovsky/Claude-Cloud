import { segsToD, mul, FLIP } from './model.js';

const NS = 'http://www.w3.org/2000/svg';
export const el = (name, attrs = {}, parent) => {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
};
const mat = (m) => `matrix(${m.map((n) => +n.toFixed(5)).join(' ')})`;
let uid = 0;

function paintAttr(paint, defs, prop) {
  if (!paint) return 'none';
  if (typeof paint === 'string') return paint;
  const g = paint.grad;
  const id = 'g' + uid++;
  const common = { id, gradientUnits: 'userSpaceOnUse', gradientTransform: mat(g.m) };
  const node = g.type === 'axial'
    ? el('linearGradient', { ...common, x1: g.p0[0], y1: g.p0[1], x2: g.p1[0], y2: g.p1[1] }, defs)
    : el('radialGradient', { ...common, cx: g.p1[0], cy: g.p1[1], r: g.r1, fx: g.p0[0], fy: g.p0[1], fr: g.r0 }, defs);
  for (const [off, col] of g.stops) el('stop', { offset: off, 'stop-color': col }, node);
  return `url(#${id})`;
}

export function fontStack(o) {
  const q = (s) => `"${s.replace(/"/g, '')}"`;
  const names = [o.family, o.fontName].filter(Boolean).map(q);
  names.push(o.generic || 'sans-serif');
  return names.join(',');
}

// Build the SVG element for one object (without clip wrappers).
function build(o, defs) {
  if (o.type === 'path') {
    const e = el('path', {
      d: segsToD(o.segs),
      fill: paintAttr(o.fill, defs), stroke: paintAttr(o.stroke, defs),
      'stroke-width': o.stroke ? o.sw : null,
      'stroke-linecap': ['butt', 'round', 'square'][o.cap] || 'butt',
      'stroke-linejoin': ['miter', 'round', 'bevel'][o.join] || 'miter',
      'stroke-miterlimit': o.miter,
      'fill-rule': o.rule,
      'stroke-dasharray': o.dash && o.dash[0]?.length ? o.dash[0].join(' ') : null,
      'stroke-dashoffset': o.dash && o.dash[0]?.length ? o.dash[1] : null,
    });
    return e;
  }
  if (o.type === 'image') {
    // image unit square: PDF has image top at y=1, so flip into SVG's y-down image box
    return el('image', {
      href: o.href, width: 1, height: 1, preserveAspectRatio: 'none',
      transform: mat(mul(o.m, [1, 0, 0, -1, 0, 1])),
    });
  }
  if (o.type === 'text') {
    const t = el('text', {
      transform: mat(mul(o.m, FLIP)),
      'font-size': o.size, 'font-family': fontStack(o),
      'font-weight': o.bold ? 700 : 400, 'font-style': o.italic ? 'italic' : 'normal',
      fill: paintAttr(o.fill, defs), stroke: o.stroke ? paintAttr(o.stroke, defs) : null,
      'stroke-width': o.stroke ? o.sw : null,
      'letter-spacing': o.cs ? o.cs : null,
      'xml:space': 'preserve', style: 'white-space:pre',
    });
    const lines = o.str.split('\n');
    lines.forEach((ln, i) => {
      const ts = el('tspan', { x: 0, y: i * o.size * (o.leading || 1.2) }, t);
      ts.textContent = ln;
      if (i === 0 && lines.length === 1 && o.origW && !o.cs) {
        ts.setAttribute('textLength', o.origW);
        ts.setAttribute('lengthAdjust', 'spacingAndGlyphs');
      }
    });
    return t;
  }
  return el('g');
}

// Returns a <g data-id> for the object, wrapped in its clip paths.
export function renderObject(o, defs) {
  const inner = build(o, defs);
  inner.dataset.id = o.id;
  let node = inner;
  if (o.opacity != null && o.opacity < 1) inner.setAttribute('opacity', o.opacity);
  for (let i = (o.clips || []).length - 1; i >= 0; i--) {
    const c = o.clips[i];
    const id = 'c' + uid++;
    const cp = el('clipPath', { id }, defs);
    el('path', { d: segsToD(c.segs), 'clip-rule': c.rule }, cp);
    const g = el('g', { 'clip-path': `url(#${id})` });
    g.appendChild(node);
    node = g;
  }
  const wrap = el('g', { 'data-wrap': o.id });
  wrap.appendChild(node);
  return wrap;
}

// Render a whole page into an <svg>. opts.editable keeps data-ids for hit-testing.
export function renderPage(doc, pageIndex) {
  const page = doc.pages[pageIndex];
  const svg = el('svg', {
    xmlns: NS, 'xmlns:xlink': 'http://www.w3.org/1999/xlink',
    width: page.w, height: page.h, viewBox: `0 0 ${page.w} ${page.h}`,
  });
  const defs = el('defs', {}, svg);
  el('rect', { width: page.w, height: page.h, fill: '#fff', 'data-artboard': 1 }, svg);
  const layerNodes = {};
  for (const L of doc.layers) {
    const g = el('g', { 'data-layer': L.id, display: L.visible ? null : 'none' }, svg);
    layerNodes[L.id] = g;
  }
  for (const o of page.objects) {
    const g = layerNodes[o.layer] || layerNodes[doc.layers[0].id];
    g.appendChild(renderObject(o, defs));
  }
  return { svg, defs, layerNodes };
}

export function pageToSvgString(doc, pageIndex) {
  const { svg } = renderPage(doc, pageIndex);
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(svg);
}

export async function pageToPngBlob(doc, pageIndex, scale = 2) {
  const page = doc.pages[pageIndex];
  const str = pageToSvgString(doc, pageIndex);
  const url = URL.createObjectURL(new Blob([str], { type: 'image/svg+xml' }));
  const img = new Image();
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
  const c = document.createElement('canvas');
  c.width = Math.round(page.w * scale); c.height = Math.round(page.h * scale);
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(img, 0, 0, c.width, c.height);
  URL.revokeObjectURL(url);
  return new Promise((r) => c.toBlob(r, 'image/png'));
}
