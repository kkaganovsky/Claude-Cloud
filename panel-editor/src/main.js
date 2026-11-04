import {
  newDoc, makePath, rectSegs, ellipseSegs, newId, clone, transformObject, mul, scaleAbout, rotateAbout, translate,
  segsBBox, FLIP, invert, apply,
} from './model.js';
import { renderPage, renderObject, el, pageToSvgString, pageToPngBlob } from './render.js';
import { openFile } from './import.js';
import { docToPdf, pickFont } from './export.js';
import * as PN from './panels.js';
import * as LY from './layout.js';
import { placePlot, refreshPlot, checkFolder, stem } from './link.js';
import { objBounds } from './geom.js';
import { zip } from './zip.js';

const $ = (s) => document.querySelector(s);
const S = {
  doc: newDoc(), page: 0, sel: new Set(), tool: 'select', zoom: 1, activeLayer: null,
  style: { fill: '#dddddd', stroke: '#000000', sw: 1 },
  undo: [], redo: [], dirty: false, fileHandle: null, hasDoc: false,
  snap: true, showGrid: false, changeLog: [], dir: null, watching: false, guides: [], arrangeOnPlace: true, notes: [],
};
window.__app = S; // handy for tests / debugging

const pageObjs = () => S.doc.pages[S.page].objects;
const byId = (id) => pageObjs().find((o) => o.id === id);
const selObjs = () => [...S.sel].map(byId).filter(Boolean);
const status = (m) => { $('#status-msg').textContent = m; };

// ---------- history ----------
function snapshot() { return JSON.stringify({ d: S.doc, p: S.page }); }
function relabel() {
  PN.ensure(S.doc); const r = PN.applyLabels(S.doc);
  if (r.changes.length) {
    S.changeLog.unshift({ t: Date.now(), changes: r.changes });
    status('Relabelled: ' + r.changes.slice(0, 6).map((c) => `${c.from} → ${c.to}`).join(', ') + (r.changes.length > 6 ? ' …' : ''));
  }
  r.touched.forEach((id) => { if (byId(id)) rerenderObj(id); });
  return r;
}
function commit() { // call AFTER a mutation
  relabel();
  S.undo.push(S._before); if (S.undo.length > 60) S.undo.shift();
  S.redo = []; S.dirty = true; S._before = snapshot(); syncButtons();
}
function restore(json) {
  const { d, p } = JSON.parse(json);
  S.doc = d; PN.ensure(S.doc); S.page = Math.min(p, d.pages.length - 1);
  S.sel = new Set([...S.sel].filter((id) => byId(id)));
  S._before = json; PN.ensure(S.doc); renderAll(); refreshPages();
}
function undo() { if (!S.undo.length) return; S.redo.push(snapshot()); restore(S.undo.pop()); syncButtons(); }
function redo() { if (!S.redo.length) return; S.undo.push(snapshot()); restore(S.redo.pop()); syncButtons(); }
function syncButtons() { $('#btn-undo').disabled = !S.undo.length; $('#btn-redo').disabled = !S.redo.length; }

// ---------- rendering ----------
let page = null; // { svg, defs, layerNodes }
function renderAll() {
  const host = $('#page-host'); host.textContent = '';
  const P = S.doc.pages[S.page];
  page = renderPage(S.doc, S.page);
  host.appendChild(page.svg);
  for (const L of S.doc.layers) if (L.locked) page.layerNodes[L.id].style.pointerEvents = 'none';
  const b = $('#board'); b.style.width = P.w * S.zoom + 'px'; b.style.height = P.h * S.zoom + 'px';
  const ov = $('#overlay'); ov.setAttribute('viewBox', `0 0 ${P.w} ${P.h}`);
  $('#welcome').hidden = S.hasDoc;
  drawOverlay(); renderProps(); renderLayers(); renderPanels();
}
function rerenderObj(id) {
  const o = byId(id); const old = page.svg.querySelector(`[data-wrap="${id}"]`);
  if (!o || !old) return renderAll();
  const fresh = renderObject(o, page.defs); old.replaceWith(fresh);
}
function setZoom(z, cx, cy) {
  z = Math.min(Math.max(z, 0.05), 32);
  const st = $('#stage'), b = $('#board');
  const rect = b.getBoundingClientRect(), srect = st.getBoundingClientRect();
  cx ??= srect.left + srect.width / 2; cy ??= srect.top + srect.height / 2;
  const dx = (cx - rect.left) / S.zoom, dy = (cy - rect.top) / S.zoom;
  S.zoom = z; const P = S.doc.pages[S.page];
  b.style.width = P.w * z + 'px'; b.style.height = P.h * z + 'px';
  const r2 = b.getBoundingClientRect();
  st.scrollLeft += r2.left + dx * z - cx; st.scrollTop += r2.top + dy * z - cy;
  $('#zoom-label').textContent = Math.round(z * 100) + '%'; drawOverlay();
}
function fit() {
  const st = $('#stage'), P = S.doc.pages[S.page];
  S.zoom = 1; // reset then compute
  const z = Math.min((st.clientWidth - 140) / P.w, (st.clientHeight - 140) / P.h);
  S.zoom = z; renderAll(); $('#zoom-label').textContent = Math.round(z * 100) + '%';
  st.scrollLeft = 0; st.scrollTop = 0;
}
const toDoc = (e) => {
  const r = $('#board').getBoundingClientRect();
  return [(e.clientX - r.left) / S.zoom, (e.clientY - r.top) / S.zoom];
};

// ---------- geometry of objects ----------
function objBBox(o) {
  const inner = page.svg.querySelector(`[data-id="${o.id}"]`);
  if (!inner) return null;
  let bb; try { bb = inner.getBBox(); } catch { return null; }
  if (o.type === 'path') {
    const pad = o.stroke ? o.sw / 2 : 0;
    const b = segsBBox(o.segs); return b ? [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad] : null;
  }
  const t = inner.transform.baseVal.consolidate();
  const M = t ? [t.matrix.a, t.matrix.b, t.matrix.c, t.matrix.d, t.matrix.e, t.matrix.f] : [1, 0, 0, 1, 0, 0];
  const cs = [[bb.x, bb.y], [bb.x + bb.width, bb.y], [bb.x, bb.y + bb.height], [bb.x + bb.width, bb.y + bb.height]].map(([x, y]) => apply(M, x, y));
  return [Math.min(...cs.map((c) => c[0])), Math.min(...cs.map((c) => c[1])), Math.max(...cs.map((c) => c[0])), Math.max(...cs.map((c) => c[1]))];
}
function selBBox() {
  let u = null;
  for (const o of selObjs()) {
    const b = objBBox(o); if (!b) continue;
    u = u ? [Math.min(u[0], b[0]), Math.min(u[1], b[1]), Math.max(u[2], b[2]), Math.max(u[3], b[3])] : b;
  }
  return u;
}

// ---------- overlay (selection box, handles, anchors, pen preview) ----------
let overlayExtra = null; // function(g, px) for tool-specific drawing
function movingBox() { // snapping uses panel frames (not letters / annotations) so plots line up, not their callouts
  const objs = selObjs(); if (!objs.length) return null;
  const pids = [...new Set(objs.map((o) => o.panel))];
  if (pids.every(Boolean)) { return pids.reduce((u, id) => { const f = PN.panelFrame(S.doc.pages[S.page], id); return f ? (u ? [Math.min(u[0], f[0]), Math.min(u[1], f[1]), Math.max(u[2], f[2]), Math.max(u[3], f[3])] : f) : u; }, null); }
  return selBBox();
}
function snapTargets() {
  const page = S.doc.pages[S.page]; const out = []; const done = new Set(); let n = 0;
  for (const o of page.objects) {
    if (S.sel.has(o.id) || n > 400) continue;
    if (o.panel) { if (done.has(o.panel)) continue; done.add(o.panel); const f = PN.panelFrame(page, o.panel); if (f) out.push(f); n++; }
    else { const b = objBounds(o); if (b) { out.push(b); n++; } }
  }
  return out;
}
function drawOverlay() {
  const ov = $('#overlay'); ov.textContent = '';
  const px = 1 / S.zoom; const g = el('g', {}, ov);
  const P0 = S.doc.pages[S.page];
  if (S.showGrid && S.hasDoc) {
    const gl = LY.gridLines(P0, LY.layoutOf(S.doc));
    for (const x of gl.x) el('line', { x1: x, y1: 0, x2: x, y2: P0.h, stroke: '#ff3d9a', 'stroke-opacity': 0.35, 'stroke-width': px }, g);
    for (const y of gl.y) el('line', { x1: 0, y1: y, x2: P0.w, y2: y, stroke: '#ff3d9a', 'stroke-opacity': 0.35, 'stroke-width': px }, g);
  }
  for (const gd of S.guides) {
    if (gd.axis === 'x') el('line', { x1: gd.pos, y1: 0, x2: gd.pos, y2: P0.h, stroke: '#ff3d9a', 'stroke-width': px }, g);
    else el('line', { x1: 0, y1: gd.pos, x2: P0.w, y2: gd.pos, stroke: '#ff3d9a', 'stroke-width': px }, g);
  }
  const bb = S.sel.size && selBBox();
  if (bb && S.tool !== 'direct') {
    el('rect', { x: bb[0], y: bb[1], width: bb[2] - bb[0], height: bb[3] - bb[1], fill: 'none', stroke: '#4a9eff', 'stroke-width': px }, g);
    const hs = 4 * px; const cx = (bb[0] + bb[2]) / 2, cy = (bb[1] + bb[3]) / 2;
    const pts = { nw: [bb[0], bb[1]], n: [cx, bb[1]], ne: [bb[2], bb[1]], e: [bb[2], cy], se: [bb[2], bb[3]], s: [cx, bb[3]], sw: [bb[0], bb[3]], w: [bb[0], cy] };
    for (const k in pts) el('rect', { x: pts[k][0] - hs, y: pts[k][1] - hs, width: hs * 2, height: hs * 2, fill: '#fff', stroke: '#4a9eff', 'stroke-width': px, 'data-h': k, class: 'h', style: `cursor:${k}-resize` }, g);
    el('line', { x1: cx, y1: bb[1], x2: cx, y2: bb[1] - 22 * px, stroke: '#4a9eff', 'stroke-width': px }, g);
    el('circle', { cx, cy: bb[1] - 22 * px, r: 4.5 * px, fill: '#fff', stroke: '#4a9eff', 'stroke-width': px, 'data-h': 'rot', class: 'h', style: 'cursor:grab' }, g);
  }
  if (S.tool === 'direct') for (const o of selObjs()) if (o.type === 'path') drawAnchors(o, g, px);
  overlayExtra?.(g, px);
}
function drawAnchors(o, g, px) {
  const r = 3.5 * px; let prev = null;
  o.segs.forEach((s, i) => {
    if (s[0] === 'Z') return;
    const ax = s[s.length - 2], ay = s[s.length - 1];
    if (s[0] === 'C') {
      const nxt = o.segs[i + 1];
      el('line', { x1: s[3], y1: s[4], x2: ax, y2: ay, stroke: '#4a9eff', 'stroke-width': px }, g);
      el('circle', { cx: s[3], cy: s[4], r: r * .8, fill: '#fff', stroke: '#4a9eff', 'stroke-width': px, class: 'h', 'data-pt': `${i}:3` }, g);
      if (prev) {
        el('line', { x1: s[1], y1: s[2], x2: prev[0], y2: prev[1], stroke: '#4a9eff', 'stroke-width': px }, g);
        el('circle', { cx: s[1], cy: s[2], r: r * .8, fill: '#fff', stroke: '#4a9eff', 'stroke-width': px, class: 'h', 'data-pt': `${i}:1` }, g);
      }
    }
    el('rect', { x: ax - r, y: ay - r, width: r * 2, height: r * 2, fill: '#4a9eff', stroke: '#fff', 'stroke-width': px, class: 'h', 'data-pt': `${i}:${s.length - 2}` }, g);
    prev = [ax, ay];
  });
}

// ---------- selection ----------
function expandIds(ids, isolate = false) { // clicking one part of a panel selects the whole panel (Alt = just that object)
  if (isolate || S.tool === 'direct') return ids;
  const out = new Set(ids);
  for (const id of ids) { const o = byId(id); if (o?.panel) for (const q of pageObjs()) if (q.panel === o.panel) out.add(q.id); }
  return [...out];
}
function select(ids, additive = false, isolate = false) {
  const all = expandIds(ids, isolate);
  if (!additive) S.sel = new Set(all);
  else { const has = all.every((id) => S.sel.has(id)); for (const id of all) has ? S.sel.delete(id) : S.sel.add(id); }
  drawOverlay(); renderProps(); renderPanels();
}
function selectableAt(target) {
  const inner = target.closest?.('[data-id]'); if (!inner) return null;
  const o = byId(inner.dataset.id); if (!o) return null;
  const L = S.doc.layers.find((l) => l.id === o.layer); if (L && (L.locked || !L.visible)) return null;
  return o;
}

// ---------- pointer interaction ----------
let drag = null;
const stage = $('#stage');
stage.addEventListener('pointerdown', (e) => {
  if (e.button === 1 || S.tool === 'hand' || spaceDown) { drag = { kind: 'pan', sx: e.clientX, sy: e.clientY, l: stage.scrollLeft, t: stage.scrollTop }; stage.setPointerCapture(e.pointerId); return; }
  if (!S.hasDoc) return;
  if (e.target.closest('#text-edit')) return;
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) && S.tool !== 'text') document.activeElement.blur();
  const [x, y] = toDoc(e);
  const hit = e.target.closest?.('[data-h]'), ptHit = e.target.closest?.('[data-pt]');
  stage.setPointerCapture(e.pointerId);
  S._before ??= snapshot();
  if (S.tool === 'zoom') { setZoom(S.zoom * (e.altKey ? 0.5 : 2), e.clientX, e.clientY); return; }
  if (ptHit) { // direct-select point drag
    const [si, k] = ptHit.dataset.pt.split(':').map(Number); const o = selObjs()[0];
    drag = { kind: 'pt', o: clone(o), id: o.id, si, k, x, y }; return;
  }
  if (hit && S.sel.size) {
    const bb = selBBox();
    drag = { kind: hit.dataset.h === 'rot' ? 'rot' : 'scale', h: hit.dataset.h, bb, x, y, orig: new Map(selObjs().map((o) => [o.id, clone(o)])) };
    return;
  }
  if (S.tool === 'select' || S.tool === 'direct') {
    const o = selectableAt(e.target);
    if (o) {
      if (e.shiftKey) { select([o.id], true, e.altKey); }
      else if (!S.sel.has(o.id) || e.altKey) select([o.id], false, e.altKey);
      if (S.sel.has(o.id)) drag = { kind: 'move', x, y, dx: 0, dy: 0, orig: new Map(selObjs().map((s) => [s.id, clone(s)])), bb0: movingBox(), targets: snapTargets() };
    } else {
      if (!e.shiftKey) select([]);
      drag = { kind: 'marquee', x, y, add: e.shiftKey };
    }
    return;
  }
  if (S.tool === 'text') { e.preventDefault(); const o = selectableAt(e.target); if (o?.type === 'text') { select([o.id]); editText(o); } else createText(x, y); return; }
  if (['rect', 'ellipse', 'line'].includes(S.tool)) { drag = { kind: 'shape', x, y }; return; }
  if (S.tool === 'pencil') { drag = { kind: 'pencil', pts: [[x, y]] }; return; }
  if (S.tool === 'pen') penDown(x, y, e);
});
stage.addEventListener('pointermove', (e) => {
  if (S.hasDoc) { const [x, y] = toDoc(e); $('#status-xy').textContent = `${x.toFixed(1)}, ${y.toFixed(1)} pt`; }
  if (!drag) { if (S.tool === 'pen') penMove(e); return; }
  const [x, y] = toDoc(e);
  switch (drag.kind) {
    case 'pan': stage.scrollLeft = drag.l - (e.clientX - drag.sx); stage.scrollTop = drag.t - (e.clientY - drag.sy); break;
    case 'move': {
      let dx = x - drag.x, dy = y - drag.y;
      if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      S.guides = [];
      if (S.snap && !e.altKey && drag.bb0) {
        const L = LY.layoutOf(S.doc); const bb = [drag.bb0[0] + dx, drag.bb0[1] + dy, drag.bb0[2] + dx, drag.bb0[3] + dy];
        const sn = LY.snapBox(bb, drag.targets, S.showGrid ? LY.gridLines(S.doc.pages[S.page], L) : null, (L.snap || 6) / S.zoom);
        if (!e.shiftKey || sn.dx) dx += sn.dx; if (!e.shiftKey || sn.dy) dy += sn.dy; S.guides = sn.guides;
      }
      applyToSel(drag.orig, translate(dx, dy)); break;
    }
    case 'scale': {
      const { bb, h } = drag; let [x0, y0, x1, y1] = bb;
      const ax = h.includes('w') ? x1 : x0, ay = h.includes('n') ? y1 : y0;
      let sx = h.includes('e') || h.includes('w') ? (h.includes('e') ? x - x0 : x1 - x) / (x1 - x0) : 1;
      let sy = h.includes('n') || h.includes('s') ? (h.includes('s') ? y - y0 : y1 - y) / (y1 - y0) : 1;
      if (e.shiftKey) { const k = Math.max(Math.abs(sx), Math.abs(sy)); sx = Math.sign(sx || 1) * (h.length === 1 && (h === 'n' || h === 's') ? k : k); sy = Math.sign(sy || 1) * k; }
      if (!isFinite(sx) || !isFinite(sy) || Math.abs(sx) < 0.001 || Math.abs(sy) < 0.001) break;
      applyToSel(drag.orig, scaleAbout(sx, sy, ax, ay)); break;
    }
    case 'rot': {
      const cx = (drag.bb[0] + drag.bb[2]) / 2, cy = (drag.bb[1] + drag.bb[3]) / 2;
      let a = Math.atan2(y - cy, x - cx) - Math.atan2(drag.y - cy, drag.x - cx);
      if (e.shiftKey) a = Math.round(a / (Math.PI / 12)) * (Math.PI / 12);
      applyToSel(drag.orig, rotateAbout(a, cx, cy)); break;
    }
    case 'pt': {
      const o = clone(drag.o); const s = o.segs[drag.si]; const dx = x - drag.x, dy = y - drag.y; const k = drag.k;
      if (s[0] === 'C' && k === s.length - 2) { // anchor: bring handles along
        s[k] += dx; s[k + 1] += dy; s[3] += dx; s[4] += dy;
        const n = o.segs[drag.si + 1]; if (n?.[0] === 'C') { n[1] += dx; n[2] += dy; }
      } else {
        s[k] += dx; s[k + 1] += dy;
        if (s[0] !== 'C' || k === s.length - 2) { const n = o.segs[drag.si + 1]; if (n?.[0] === 'C') { n[1] += dx; n[2] += dy; } }
      }
      replaceObj(o); drawOverlay(); break;
    }
    case 'marquee': {
      overlayExtra = (g, px) => el('rect', { x: Math.min(drag.x, x), y: Math.min(drag.y, y), width: Math.abs(x - drag.x), height: Math.abs(y - drag.y), fill: '#4a9eff22', stroke: '#4a9eff', 'stroke-width': px, 'stroke-dasharray': `${4 * px} ${3 * px}` }, g);
      drawOverlay(); break;
    }
    case 'shape': {
      let w = x - drag.x, h = y - drag.y;
      if (e.shiftKey) { const m = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * m; h = Math.sign(h || 1) * m; }
      drag.cur = [w, h];
      overlayExtra = (g, px) => {
        const [rx, ry] = [Math.min(drag.x, drag.x + w), Math.min(drag.y, drag.y + h)];
        if (S.tool === 'line') el('line', { x1: drag.x, y1: drag.y, x2: drag.x + w, y2: drag.y + h, stroke: '#4a9eff', 'stroke-width': px }, g);
        else if (S.tool === 'rect') el('rect', { x: rx, y: ry, width: Math.abs(w), height: Math.abs(h), fill: 'none', stroke: '#4a9eff', 'stroke-width': px }, g);
        else el('ellipse', { cx: rx + Math.abs(w) / 2, cy: ry + Math.abs(h) / 2, rx: Math.abs(w) / 2, ry: Math.abs(h) / 2, fill: 'none', stroke: '#4a9eff', 'stroke-width': px }, g);
      };
      drawOverlay(); break;
    }
    case 'pencil': {
      drag.pts.push([x, y]);
      overlayExtra = (g, px) => el('polyline', { points: drag.pts.map((p) => p.join(',')).join(' '), fill: 'none', stroke: '#4a9eff', 'stroke-width': px }, g);
      drawOverlay(); break;
    }
  }
  if (S.tool === 'pen') penMove(e);
});
stage.addEventListener('pointerup', (e) => {
  const d = drag; drag = null; S.guides = [];
  if (S.tool === 'pen') { penUp(e); return; }
  if (!d) return;
  const [x, y] = toDoc(e);
  overlayExtra = null;
  if (d.kind === 'marquee') {
    const r = [Math.min(d.x, x), Math.min(d.y, y), Math.max(d.x, x), Math.max(d.y, y)];
    if (r[2] - r[0] > 2 || r[3] - r[1] > 2) {
      const ids = pageObjs().filter((o) => {
        const L = S.doc.layers.find((l) => l.id === o.layer); if (L && (L.locked || !L.visible)) return false;
        const b = objBBox(o); return b && b[0] < r[2] && b[2] > r[0] && b[1] < r[3] && b[3] > r[1];
      }).map((o) => o.id);
      select(ids, d.add && false);
    } else drawOverlay();
  } else if (d.kind === 'shape') finishShape(d);
  else if (d.kind === 'pencil') finishPencil(d);
  else if (['move', 'scale', 'rot', 'pt'].includes(d.kind)) { if (snapshot() !== S._before) commit(); drawOverlay(); renderProps(); }
});
stage.addEventListener('dblclick', (e) => {
  if (S.tool === 'pen') { penFinish(false); return; }
  const o = selectableAt(e.target);
  if (o?.type === 'text') editText(o);
});
stage.addEventListener('wheel', (e) => {
  if (e.ctrlKey || e.metaKey) { e.preventDefault(); setZoom(S.zoom * Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY); }
}, { passive: false });

function applyToSel(orig, T) {
  for (const [id, o] of orig) { const n = transformObject(clone(o), T); replaceObj(n); }
  drawOverlay();
}
function replaceObj(n) {
  const arr = pageObjs(); const i = arr.findIndex((o) => o.id === n.id); if (i < 0) return;
  arr[i] = n; rerenderObj(n.id);
}

// ---------- creating objects ----------
const styleProps = () => ({ fill: S.style.fill, stroke: S.style.stroke, sw: S.style.sw });
function addObject(o) {
  o.layer = S.activeLayer && S.doc.layers.find((l) => l.id === S.activeLayer && !l.locked)?.id || S.doc.layers.find((l) => !l.locked)?.id || S.doc.layers[0].id;
  pageObjs().push(o); S.sel = new Set([o.id]); commit(); renderAll();
}
function finishShape(d) {
  const [w, h] = d.cur || [0, 0];
  if (Math.abs(w) < 1 && Math.abs(h) < 1) { drawOverlay(); return; }
  const x0 = Math.min(d.x, d.x + w), y0 = Math.min(d.y, d.y + h);
  if (S.tool === 'rect') addObject(makePath(rectSegs(x0, y0, Math.abs(w), Math.abs(h)), styleProps()));
  else if (S.tool === 'ellipse') addObject(makePath(ellipseSegs(x0 + Math.abs(w) / 2, y0 + Math.abs(h) / 2, Math.abs(w) / 2, Math.abs(h) / 2), styleProps()));
  else addObject(makePath([['M', d.x, d.y], ['L', d.x + w, d.y + h]], { ...styleProps(), fill: null }));
}
function rdp(pts, eps) {
  if (pts.length < 3) return pts;
  const [a, b] = [pts[0], pts[pts.length - 1]]; let dm = 0, im = 0;
  const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1e-9;
  for (let i = 1; i < pts.length - 1; i++) {
    const d = Math.abs((b[0] - a[0]) * (a[1] - pts[i][1]) - (a[0] - pts[i][0]) * (b[1] - a[1])) / L;
    if (d > dm) { dm = d; im = i; }
  }
  if (dm <= eps) return [a, b];
  return [...rdp(pts.slice(0, im + 1), eps).slice(0, -1), ...rdp(pts.slice(im), eps)];
}
function smoothSegs(pts, closed = false) {
  const segs = [['M', ...pts[0]]];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    segs.push(['C', p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6, p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1]]);
  }
  if (closed) segs.push(['Z']); return segs;
}
function finishPencil(d) {
  if (d.pts.length < 2) { drawOverlay(); return; }
  const pts = rdp(d.pts, 1.5 / S.zoom);
  addObject(makePath(smoothSegs(pts), { ...styleProps(), fill: null }));
}

// pen tool
let pen = null;
function penDown(x, y, e) {
  if (!pen) pen = { pts: [], cur: null };
  const first = pen.pts[0];
  if (first && pen.pts.length > 2 && Math.hypot(x - first.x, y - first.y) < 7 / S.zoom) { penFinish(true); return; }
  const p = { x, y, hin: null, hout: null }; pen.pts.push(p); pen.drag = p; pen.cur = [x, y];
}
function penMove(e) {
  if (!pen) return;
  const [x, y] = toDoc(e);
  if (pen.drag && e.buttons) { const p = pen.drag; p.hout = [x, y]; p.hin = [2 * p.x - x, 2 * p.y - y]; }
  pen.cur = [x, y]; penOverlay();
}
function penUp() { if (pen) pen.drag = null; }
function penSegs(closed) {
  const pts = pen.pts; const segs = [['M', pts[0].x, pts[0].y]];
  const link = (a, b) => {
    if (a.hout || b.hin) segs.push(['C', ...(a.hout || [a.x, a.y]), ...(b.hin || [b.x, b.y]), b.x, b.y]); else segs.push(['L', b.x, b.y]);
  };
  for (let i = 1; i < pts.length; i++) link(pts[i - 1], pts[i]);
  if (closed) { link(pts[pts.length - 1], pts[0]); segs.push(['Z']); }
  return segs;
}
function penOverlay() {
  overlayExtra = (g, px) => {
    if (!pen?.pts.length) return;
    const segs = penSegs(false);
    if (pen.cur && !pen.drag) { const l = pen.pts[pen.pts.length - 1]; segs.push(l.hout ? ['C', ...l.hout, ...pen.cur, ...pen.cur] : ['L', ...pen.cur]); }
    el('path', { d: segs.map((s) => s.join(' ')).join(' '), fill: 'none', stroke: '#4a9eff', 'stroke-width': px }, g);
    pen.pts.forEach((p, i) => el('rect', { x: p.x - 3 * px, y: p.y - 3 * px, width: 6 * px, height: 6 * px, fill: i ? '#fff' : '#4a9eff', stroke: '#4a9eff', 'stroke-width': px }, g));
  };
  drawOverlay();
}
function penFinish(closed) {
  if (!pen) return;
  if (pen.pts.length > 1) addObject(makePath(penSegs(closed), { ...styleProps(), fill: closed ? S.style.fill : null }));
  pen = null; overlayExtra = null; drawOverlay();
}

// text
function createText(x, y) {
  const o = {
    id: newId(), type: 'text', str: 'Text', m: [1, 0, 0, -1, x, y], size: 18, fontName: 'Arial', family: 'Arial', bold: false, italic: false,
    generic: 'sans-serif', fill: S.style.fill === null ? '#000000' : '#000000', stroke: null, sw: 1, opacity: 1, leading: 1.2,
  };
  addObject(o); editText(o);
}
const TOKEN = /\{\{(ref|fig|letter|short):/;
function setText(o, v) { if (TOKEN.test(v)) o.tpl = v; else delete o.tpl; o.str = v || ' '; delete o.origW; }
function editText(o) {
  const ta = $('#text-edit'); const bb = objBBox(o); if (!bb) return;
  const inner = page.svg.querySelector(`[data-id="${o.id}"]`);
  ta.hidden = false; ta.value = o.tpl ?? o.str;
  const z = S.zoom; Object.assign(ta.style, {
    left: bb[0] * z - 2 + 'px', top: bb[1] * z - 2 + 'px', minWidth: Math.max(60, (bb[2] - bb[0]) * z + 8) + 'px', height: Math.max(24, (bb[3] - bb[1]) * z + 6) + 'px',
    fontSize: o.size * z * Math.hypot(o.m[0], o.m[1]) + 'px', fontFamily: inner.getAttribute('font-family'), fontWeight: o.bold ? 700 : 400, fontStyle: o.italic ? 'italic' : 'normal',
  });
  ta.focus(); ta.select();
  const done = (cancel) => {
    ta.removeEventListener('blur', onBlur); ta.removeEventListener('keydown', onKey); ta.hidden = true;
    if (!cancel && ta.value !== (o.tpl ?? o.str)) { const cur = byId(o.id); S._before ??= snapshot(); setText(cur, ta.value); commit(); rerenderObj(o.id); drawOverlay(); renderProps(); }
  };
  const onBlur = () => done(false);
  const onKey = (e) => { e.stopPropagation(); if (e.key === 'Escape') done(true); else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) done(false); };
  ta.addEventListener('blur', onBlur); ta.addEventListener('keydown', onKey);
}

// ---------- object operations ----------
function mutate(fn) { S._before ??= snapshot(); fn(); commit(); renderAll(); }
function del() { if (!S.sel.size) return; mutate(() => { S.doc.pages[S.page].objects = pageObjs().filter((o) => !S.sel.has(o.id)); S.sel = new Set(); }); }
let clipboard = [];
function copy() { clipboard = selObjs().map(clone); status(`Copied ${clipboard.length} object(s)`); }
function paste(offset = 10) {
  if (!clipboard.length) return;
  mutate(() => {
    S.sel = new Set();
    for (const c of clipboard) { const n = transformObject(clone(c), translate(offset, offset)); n.id = newId(); pageObjs().push(n); S.sel.add(n.id); }
    clipboard = clipboard.map((c) => transformObject(clone(c), translate(offset, offset)));
  });
}
function arrange(where) {
  mutate(() => {
    const arr = pageObjs(); const ids = S.sel;
    const pick = arr.filter((o) => ids.has(o.id)), rest = arr.filter((o) => !ids.has(o.id));
    if (where === 'front') S.doc.pages[S.page].objects = [...rest, ...pick];
    else if (where === 'back') S.doc.pages[S.page].objects = [...pick, ...rest];
    else {
      const a = arr.slice(); const dir = where === 'fwd' ? 1 : -1;
      const idx = a.map((o, i) => (ids.has(o.id) ? i : -1)).filter((i) => i >= 0);
      for (const i of dir > 0 ? idx.reverse() : idx) { const j = i + dir; if (j >= 0 && j < a.length && !ids.has(a[j].id)) [a[i], a[j]] = [a[j], a[i]]; }
      S.doc.pages[S.page].objects = a;
    }
  });
}
function nudge(dx, dy) {
  if (!S.sel.size) return;
  S._before ??= snapshot();
  for (const o of selObjs()) replaceObj(transformObject(clone(o), translate(dx, dy)));
  clearTimeout(nudge.t); nudge.t = setTimeout(() => { commit(); renderProps(); }, 300); drawOverlay();
}

// ---------- properties panel ----------
function renderProps() {
  const box = $('#props'); const objs = selObjs();
  const st = (label, key) => {
    const v = objs.length ? objs[0][key] : S.style[key];
    const none = v === null; const isGrad = v && typeof v === 'object';
    return `<div class="row"><label>${label}</label>
      <input type="color" data-k="${key}" value="${isGrad ? v.grad.stops[0][1] : (v || '#000000')}" ${none ? 'style="opacity:.3"' : ''}>
      <label style="width:auto"><input type="checkbox" data-none="${key}" ${none ? 'checked' : ''}> none</label>
      ${isGrad ? `<span class="dim">gradient</span><button data-act="flat-${key}">Flatten</button>` : ''}</div>`;
  };
  let h = `<h4>${objs.length ? objs.length + ' selected' : 'Defaults for new shapes'}</h4>`;
  if (!objs.length || objs.some((o) => o.type !== 'image')) {
    h += st('Fill', 'fill') + st('Stroke', 'stroke');
    h += `<div class="row"><label>Stroke wt</label><input type="number" min="0" step="0.25" data-k="sw" value="${+((objs[0]?.sw ?? S.style.sw)).toFixed(3)}"></div>`;
  }
  if (objs.length) {
    const bb = selBBox();
    h += `<div class="row"><label>Opacity</label><input type="range" min="0" max="1" step="0.01" data-k="opacity" value="${objs[0].opacity ?? 1}" style="flex:1"></div>`;
    if (bb) h += `<div class="row"><label>X / Y</label><input type="number" data-geo="x" step="0.5" value="${bb[0].toFixed(2)}"><input type="number" data-geo="y" step="0.5" value="${bb[1].toFixed(2)}"></div>
      <div class="row"><label>W / H</label><input type="number" data-geo="w" step="0.5" value="${(bb[2] - bb[0]).toFixed(2)}"><input type="number" data-geo="h" step="0.5" value="${(bb[3] - bb[1]).toFixed(2)}"></div>`;
    const t = objs.length === 1 && objs[0].type === 'text' ? objs[0] : null;
    if (t) {
      const fam = pickFont(t).split('-')[0];
      h += `<div class="row"><label>Text</label><textarea data-text>${(t.tpl ?? t.str).replace(/&/g, '&amp;').replace(/</g, '&lt;')}</textarea></div>
        <div class="row"><label></label><select data-insref><option value="">Insert panel reference…</option>${Object.values(S.doc.panels).map((p) => `<option value="${p.id}">${p.label || p.name} — ${p.name}</option>`).join('')}</select></div>
        <div class="row"><label>Size</label><input type="number" min="1" step="0.5" data-tk="size" value="${+t.size.toFixed(2)}">
        <select data-tk="fam"><option ${fam === 'Sans' ? 'selected' : ''} value="Sans">Sans (Arial)</option><option ${fam === 'Serif' ? 'selected' : ''} value="Serif">Serif (Times)</option><option ${fam === 'Mono' ? 'selected' : ''} value="Mono">Mono (Courier)</option></select></div>
        <div class="row"><label></label><label style="width:auto"><input type="checkbox" data-tk="bold" ${t.bold ? 'checked' : ''}> Bold</label><label style="width:auto"><input type="checkbox" data-tk="italic" ${t.italic ? 'checked' : ''}> Italic</label></div>
        <div class="dim" style="font-size:11px">Original font: ${t.fontName}. Saved files use a Liberation font of the same style (Arial/Times/Courier metrics) unless you have it installed in Illustrator.</div>`;
    }
    const layerOpts = S.doc.layers.slice().reverse().map((l) => `<option value="${l.id}" ${objs[0].layer === l.id ? 'selected' : ''}>${l.name}</option>`).join('');
    h += `<div class="row"><label>Layer</label><select data-layerpick>${layerOpts}</select></div>
      <div class="btns"><button data-act="front">To front</button><button data-act="fwd">Forward</button><button data-act="bwd">Backward</button><button data-act="back">To back</button>
      <button data-act="dup">Duplicate</button>${objs.some((o) => o.clips?.length) ? '<button data-act="unclip">Release clip</button>' : ''}<button data-act="del">Delete</button></div>
      ${units().length > 1 ? `<div class="dim" style="margin-top:8px">Align ${units().length} ${units().every((u) => u[0].panel) ? 'panels' : 'items'}</div><div class="btns"><button data-align="left">⇤ Left</button><button data-align="hcenter">⇹ Centre</button><button data-align="right">⇥ Right</button><button data-align="top">⤒ Top</button><button data-align="vmiddle">⇳ Middle</button><button data-align="bottom">⤓ Bottom</button><button data-align="dh">↔ Distribute</button><button data-align="dv">↕ Distribute</button></div>` : ''}`;
  } else {
    const P = S.doc.pages[S.page];
    h += `<div class="row"><label>Artboard</label><input type="number" data-pg="w" value="${+P.w.toFixed(2)}"><input type="number" data-pg="h" value="${+P.h.toFixed(2)}"><span class="dim">pt</span></div>`;
  }
  box.innerHTML = h;
}
$('#props').addEventListener('input', (e) => {
  const t = e.target; const objs = selObjs();
  if (t.dataset.none) {
    const key = t.dataset.none;
    if (!objs.length) S.style[key] = t.checked ? null : ($(`#props [data-k="${key}"]`).value);
    else mutateLive(() => objs.forEach((o) => { o[key] = t.checked ? null : $(`#props [data-k="${key}"]`).value; }));
    return;
  }
  if (t.dataset.k) {
    const k = t.dataset.k; let v = t.type === 'color' ? t.value : parseFloat(t.value);
    if (k === 'sw' || k === 'opacity') { if (isNaN(v)) return; }
    if (!objs.length) { if (k in S.style) S.style[k] = v; return; }
    mutateLive(() => objs.forEach((o) => { if (k === 'sw' && o.type === 'image') return; o[k] = v; if ((k === 'fill' || k === 'stroke')) { const cb = $(`#props [data-none="${k}"]`); if (cb) cb.checked = false; } }));
    return;
  }
  if (t.dataset.text !== undefined) { mutateLive(() => setText(objs[0], t.value)); return; }
  if (t.dataset.insref !== undefined) { if (!t.value) return; const ta = $('#props textarea[data-text]'); ta.value += `{{ref:${t.value}}}`; ta.dispatchEvent(new Event('input', { bubbles: true })); t.value = ''; return; }
  if (t.dataset.tk) {
    const o = objs[0], k = t.dataset.tk;
    mutateLive(() => {
      if (k === 'size') o.size = Math.max(1, parseFloat(t.value) || o.size);
      else if (k === 'fam') { o.family = { Sans: 'Arial', Serif: 'Times New Roman', Mono: 'Courier New' }[t.value]; o.fontName = o.family; o.generic = { Sans: 'sans-serif', Serif: 'serif', Mono: 'monospace' }[t.value]; delete o.origW; }
      else { o[k] = t.checked; delete o.origW; }
    }); return;
  }
  if (t.dataset.layerpick) { mutate(() => objs.forEach((o) => { o.layer = t.value; })); return; }
  if (t.dataset.pg) { mutate(() => { S.doc.pages[S.page][t.dataset.pg] = Math.max(1, parseFloat(t.value) || 1); }); return; }
  if (t.dataset.geo) {
    const bb = selBBox(); const v = parseFloat(t.value); if (isNaN(v) || !bb) return;
    const g = t.dataset.geo; S._before ??= snapshot();
    let T;
    if (g === 'x') T = translate(v - bb[0], 0); else if (g === 'y') T = translate(0, v - bb[1]);
    else if (g === 'w' && v > 0) T = scaleAbout(v / (bb[2] - bb[0]), 1, bb[0], bb[1]);
    else if (g === 'h' && v > 0) T = scaleAbout(1, v / (bb[3] - bb[1]), bb[0], bb[1]);
    if (T) { for (const o of objs) replaceObj(transformObject(clone(o), T)); drawOverlay(); }
    clearTimeout(S._gt); S._gt = setTimeout(() => commit(), 400); return;
  }
});
function mutateLive(fn) { // live edit with debounced history commit; does not rebuild the panel (keeps focus)
  S._before ??= snapshot(); fn(); const r = PN.applyLabels(S.doc); selObjs().forEach((o) => rerenderObj(o.id)); r.touched.forEach((id) => { if (byId(id)) rerenderObj(id); }); drawOverlay();
  clearTimeout(S._lt); S._lt = setTimeout(() => commit(), 500);
}
$('#props').addEventListener('click', (e) => {
  const a = e.target.dataset.act; if (!a) return;
  if (a === 'del') del(); else if (a === 'dup') paste_dup();
  else if (['front', 'fwd', 'bwd', 'back'].includes(a)) arrange(a);
  else if (a === 'unclip') mutate(() => selObjs().forEach((o) => { o.clips = []; }));
  else if (a.startsWith('flat-')) { const k = a.slice(5); mutate(() => selObjs().forEach((o) => { if (o[k]?.grad) o[k] = o[k].grad.stops[0][1]; })); }
});
function paste_dup() { copy(); paste(10); }

// ---------- layers panel ----------
function renderLayers() {
  const box = $('#layers'); S.activeLayer ??= S.doc.layers[0].id;
  if (!S.doc.layers.find((l) => l.id === S.activeLayer)) S.activeLayer = S.doc.layers[0].id;
  let h = '<h4>Layers</h4>';
  for (const L of S.doc.layers.slice().reverse()) {
    const n = pageObjs().filter((o) => o.layer === L.id).length;
    h += `<div class="layer ${L.id === S.activeLayer ? 'active' : ''}" data-lid="${L.id}">
      <button data-lact="vis" title="Show/hide" class="${L.visible ? '' : 'off'}">👁</button><button data-lact="lock" title="Lock" class="${L.locked ? '' : 'off'}">🔒</button>
      <span class="nm" title="Double-click to rename">${L.name.replace(/</g, '&lt;')}</span><span class="dim">${n}</span>
      <button data-lact="up" title="Move up">▲</button><button data-lact="down" title="Move down">▼</button></div>`;
  }
  h += '<div class="btns"><button data-lact="add">+ Layer</button><button data-lact="remove">Delete layer</button></div>';
  box.innerHTML = h;
}
$('#layers').addEventListener('click', (e) => {
  const row = e.target.closest('[data-lid]'); const act = e.target.dataset.lact;
  const L = row && S.doc.layers.find((l) => l.id === row.dataset.lid);
  if (act === 'add') { mutate(() => { const id = 'L' + newId(); S.doc.layers.push({ id, name: `Layer ${S.doc.layers.length + 1}`, visible: true, locked: false }); S.activeLayer = id; }); return; }
  if (act === 'remove') {
    const cur = S.doc.layers.find((l) => l.id === S.activeLayer);
    if (S.doc.layers.length < 2) return status('Cannot delete the only layer');
    const count = S.doc.pages.reduce((n, p) => n + p.objects.filter((o) => o.layer === cur.id).length, 0);
    if (count && !confirm(`Delete layer "${cur.name}" and its ${count} object(s)?`)) return;
    mutate(() => { S.doc.layers = S.doc.layers.filter((l) => l !== cur); S.doc.pages.forEach((p) => { p.objects = p.objects.filter((o) => o.layer !== cur.id); }); S.sel = new Set(); S.activeLayer = S.doc.layers[0].id; });
    return;
  }
  if (!L) return;
  if (act === 'vis') mutate(() => { L.visible = !L.visible; });
  else if (act === 'lock') mutate(() => { L.locked = !L.locked; });
  else if (act === 'up' || act === 'down') mutate(() => { const a = S.doc.layers, i = a.indexOf(L), j = i + (act === 'up' ? 1 : -1); if (j >= 0 && j < a.length) [a[i], a[j]] = [a[j], a[i]]; });
  else { S.activeLayer = L.id; renderLayers(); }
});
$('#layers').addEventListener('dblclick', (e) => {
  const row = e.target.closest('[data-lid]'); if (!row || !e.target.classList.contains('nm')) return;
  const L = S.doc.layers.find((l) => l.id === row.dataset.lid); const nm = prompt('Layer name', L.name);
  if (nm) mutate(() => { L.name = nm; });
});

// ---------- tools ----------
const TOOLS = [
  ['select', 'V', '➤', 'Selection (V)'], ['direct', 'A', '▷', 'Direct selection — edit anchor points (A)'], ['pen', 'P', '✒', 'Pen (P): click for corners, drag for curves, double-click to finish, click first point to close'],
  ['pencil', 'N', '✎', 'Pencil (N)'], ['rect', 'M', '▭', 'Rectangle (M)'], ['ellipse', 'L', '◯', 'Ellipse (L)'], ['line', '\\', '╱', 'Line (\\)'],
  ['text', 'T', 'T', 'Type (T)'], ['hand', 'H', '✋', 'Hand (H / hold Space)'], ['zoom', 'Z', '🔍', 'Zoom (Z, Alt-click to zoom out)'],
];
const toolBox = $('#tools');
toolBox.innerHTML = TOOLS.map(([id, , ic, tip]) => `<button data-tool="${id}" title="${tip}">${ic}</button>`).join('');
toolBox.addEventListener('click', (e) => { const b = e.target.closest('[data-tool]'); if (b) setTool(b.dataset.tool); });
function setTool(t) {
  if (pen && t !== 'pen') penFinish(false);
  S.tool = t; overlayExtra = null;
  toolBox.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.tool === t));
  stage.style.cursor = { hand: 'grab', zoom: 'zoom-in', select: 'default', direct: 'default' }[t] || 'crosshair';
  drawOverlay();
}
let spaceDown = false;
window.addEventListener('keydown', (e) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
  if (mod && e.key.toLowerCase() === 'o') { e.preventDefault(); $('#file-input').click(); return; }
  if (typing) return;
  if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
  if (mod && e.key.toLowerCase() === 'c') { copy(); return; }
  if (mod && e.key.toLowerCase() === 'v') { e.preventDefault(); paste(); return; }
  if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); paste_dup(); return; }
  if (mod && e.key.toLowerCase() === 'a') { e.preventDefault(); select(pageObjs().map((o) => o.id)); return; }
  if (mod && e.key === '0') { e.preventDefault(); fit(); return; }
  if (mod && (e.key === '=' || e.key === '+')) { e.preventDefault(); setZoom(S.zoom * 1.25); return; }
  if (mod && e.key === '-') { e.preventDefault(); setZoom(S.zoom / 1.25); return; }
  if (mod && e.key === ']') { e.preventDefault(); arrange(e.shiftKey ? 'front' : 'fwd'); return; }
  if (mod && e.key === '[') { e.preventDefault(); arrange(e.shiftKey ? 'back' : 'bwd'); return; }
  if (e.key === ' ') { spaceDown = true; stage.style.cursor = 'grab'; e.preventDefault(); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); del(); return; }
  if (e.key === 'Escape') { if (pen) { pen = null; overlayExtra = null; drawOverlay(); } else select([]); return; }
  if (e.key === 'Enter' && pen) { penFinish(false); return; }
  const step = e.shiftKey ? 10 : 1;
  if (e.key === 'ArrowLeft') { e.preventDefault(); nudge(-step, 0); } else if (e.key === 'ArrowRight') { e.preventDefault(); nudge(step, 0); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); nudge(0, -step); } else if (e.key === 'ArrowDown') { e.preventDefault(); nudge(0, step); }
  else if (!mod) { const t = TOOLS.find((x) => x[1].toLowerCase() === e.key.toLowerCase()); if (t) setTool(t[0]); }
});
window.addEventListener('keyup', (e) => { if (e.key === ' ') { spaceDown = false; setTool(S.tool); } });

// ---------- pages ----------
function refreshPages() {
  const sel = $('#page-select');
  sel.innerHTML = S.doc.pages.map((p, i) => `<option value="${i}" ${i === S.page ? 'selected' : ''}>${i + 1} (${Math.round(p.w)}×${Math.round(p.h)})</option>`).join('');
  $('#docname').textContent = S.doc.name + (S.dirty ? ' •' : '');
}
$('#page-select').addEventListener('change', (e) => { S.page = +e.target.value; S.sel = new Set(); fit(); });
$('#btn-addpage').addEventListener('click', () => { const P = S.doc.pages[S.page]; S._before ??= snapshot(); S.doc.pages.push({ w: P.w, h: P.h, objects: [] }); S.page = S.doc.pages.length - 1; commit(); refreshPages(); fit(); });

// ---------- file I/O ----------
function loadDoc(doc) {
  S.doc = PN.ensure(doc); S.page = 0; S.sel = new Set(); S.changeLog = []; S.undo = []; S.redo = []; S.dirty = false; S.hasDoc = true; S.activeLayer = doc.layers[0].id; S.fileHandle = null;
  PN.applyLabels(S.doc); S.labelsAtSave = PN.labelMap(S.doc);
  S._before = snapshot(); syncButtons(); refreshPages(); fit(); setTool('select');
  const w = doc.meta?.warnings || [];
  const wb = $('#btn-warn'); wb.hidden = !w.length; wb.textContent = `⚠ ${w.length} import note${w.length === 1 ? '' : 's'}`;
  const n = doc.pages.reduce((a, p) => a + p.objects.length, 0);
  status(`Opened “${doc.name}” — ${doc.pages.length} artboard(s), ${doc.layers.length} layer(s), ${n} objects${doc.meta?.restored ? ' (restored full edit state)' : ''}`);
}
async function doOpen(file) {
  try { status('Opening…'); loadDoc(await openFile(file, status)); }
  catch (err) { console.error(err); modal('Could not open file', `<p>${String(err.message || err).replace(/</g, '&lt;')}</p>`); status('Open failed'); }
}
$('#btn-open').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', (e) => { const f = e.target.files[0]; e.target.value = ''; if (f) doOpen(f); });
$('#btn-new').addEventListener('click', () => {
  if (S.dirty && !confirm('Discard unsaved changes?')) return;
  const w = parseFloat(prompt('Artboard width (pt)', '612')) || 612, h = parseFloat(prompt('Artboard height (pt)', '792')) || 792;
  loadDoc(newDoc(w, h)); S.hasDoc = true; renderAll();
});
for (const ev of ['dragenter', 'dragover']) window.addEventListener(ev, (e) => { e.preventDefault(); stage.classList.add('drop'); });
for (const ev of ['dragleave', 'drop']) window.addEventListener(ev, (e) => { e.preventDefault(); stage.classList.remove('drop'); });
window.addEventListener('drop', (e) => { const f = e.dataTransfer?.files?.[0]; if (f) { if (/image\/(png|jpeg)/.test(f.type) && S.hasDoc) placeImage(f); else doOpen(f); } });

function download(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
async function pdfBlob(embedState = true) { return new Blob([await docToPdf(S.doc, { onProgress: status, embedState })], { type: 'application/pdf' }); }
async function save() {
  if (!S.hasDoc) return;
  try {
    status('Saving…'); const blob = await pdfBlob(); const name = `${S.doc.name}.ai`;
    if (window.showSaveFilePicker) {
      try {
        S.fileHandle ??= await window.showSaveFilePicker({ suggestedName: name, types: [{ description: 'Illustrator-compatible file', accept: { 'application/illustrator': ['.ai'] } }] });
        const w = await S.fileHandle.createWritable(); await w.write(blob); await w.close();
      } catch (err) { if (err.name === 'AbortError') { status('Save cancelled'); return; } S.fileHandle = null; download(blob, name); }
    } else download(blob, name);
    S.dirty = false; refreshPages(); status(`Saved ${name} (${(blob.size / 1024).toFixed(0)} KB)`);
  } catch (err) { console.error(err); modal('Save failed', `<p>${String(err.message || err)}</p>`); }
}
$('#btn-save').addEventListener('click', save);
$('#export-menu').addEventListener('change', async (e) => {
  const v = e.target.value; e.target.value = ''; if (!v || !S.hasDoc) return;
  const base = S.doc.name;
  try {
    if (v === 'pdf') download(await pdfBlob(false), `${base}.pdf`);
    else if (v === 'svg') download(new Blob([pageToSvgString(S.doc, S.page)], { type: 'image/svg+xml' }), `${base}.svg`);
    else if (v === 'png') download(await pageToPngBlob(S.doc, S.page, 2), `${base}.png`);
    status(`Exported ${v.toUpperCase()}`);
  } catch (err) { console.error(err); modal('Export failed', String(err.message || err)); }
});
$('#btn-place').addEventListener('click', () => $('#img-input').click());
$('#img-input').addEventListener('change', (e) => { const f = e.target.files[0]; e.target.value = ''; if (f) placeImage(f); });
async function placeImage(file) {
  const href = await new Promise((r) => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(file); });
  const img = new Image(); await new Promise((r, j) => { img.onload = r; img.onerror = j; img.src = href; });
  const P = S.doc.pages[S.page]; const k = Math.min(1, (P.w * 0.8) / img.naturalWidth, (P.h * 0.8) / img.naturalHeight);
  const w = img.naturalWidth * k, h = img.naturalHeight * k, x = (P.w - w) / 2, y = (P.h - h) / 2;
  S._before ??= snapshot(); addObject({ id: newId(), type: 'image', href, m: [w, 0, 0, -h, x, y + h], opacity: 1, clips: [], w: img.naturalWidth, h: img.naturalHeight });
}
function modal(title, html) { const m = $('#modal'); m.querySelector('h3').textContent = title; m.querySelector('.body').innerHTML = html; m.hidden = false; }
$('#modal .close').addEventListener('click', () => { $('#modal').hidden = true; });
$('#btn-warn').addEventListener('click', () => modal('Import notes', '<p>These features in the original file are not fully editable here, so they may look different or be dropped when you save:</p><ul>' + (S.doc.meta.warnings || []).map((w) => `<li>${w}</li>`).join('') + '</ul>'));

$('#btn-undo').addEventListener('click', undo); $('#btn-redo').addEventListener('click', redo);
$('#z-in').addEventListener('click', () => setZoom(S.zoom * 1.25)); $('#z-out').addEventListener('click', () => setZoom(S.zoom / 1.25)); $('#z-fit').addEventListener('click', fit);
window.addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('resize', () => drawOverlay());


// ---------- figures & panels sidebar ----------
const esc = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
function renderPanels() {
  const box = $('#panels'); if (!box || !S.hasDoc) { if (box) box.innerHTML = ''; return; }
  const doc = S.doc; PN.ensure(doc); const st = PN.settings(doc); const figs = PN.figureInfo(doc); const fi = figs[S.page];
  const L = LY.layoutOf(doc); const ids = PN.orderedPanels(doc, S.page);
  const selPanels = new Set(selObjs().map((o) => o.panel).filter(Boolean));
  let h = '<h4>Figure &amp; panels</h4>';
  h += `<div class="row"><label>Artboard is</label><select data-fig="kind">${Object.entries(PN.KINDS).map(([k, v]) => `<option value="${k}" ${doc.pages[S.page].fig.kind === k ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
    <div class="row"><label>Title</label><input type="text" data-fig="title" value="${esc(doc.pages[S.page].fig.title)}" placeholder="(optional)"></div>
    <div class="dim" style="font-size:12px">→ <b>${esc(fi.name)}</b> · exported as “${esc(fi.file)}.ai”</div>`;
  h += '<div style="margin-top:8px">' + (ids.length ? ids.map((id, i) => {
    const p = doc.panels[id]; const lab = p.label || '';
    const others = figs.filter((f) => f.page !== S.page).map((f) => `<option value="${f.page}">${esc(f.name)}</option>`).join('');
    const link = p.link ? `<span class="badge ${p.link.updatedAt ? '' : ''}" title="${esc(`${p.link.src}\n${p.link.cmd || ''}`)}">⛓ ${p.link.updatedAt ? 'updated' : 'linked'}</span>` : '';
    return `<div class="prow ${selPanels.has(id) ? 'on' : ''}" data-pid="${id}"><span class="lt" title="${esc(lab)}">${esc(p.letter || '·')}</span>
      <input type="text" data-pname="${id}" value="${esc(p.name)}" title="${esc(lab)}">${link}
      ${st.orderMode === 'manual' ? `<button data-act="pup" title="Earlier">▲</button><button data-act="pdown" title="Later">▼</button>` : ''}
      ${p.link ? '<button data-act="pupdate" title="Update from a newer file…">↻</button>' : ''}
      ${others ? `<select data-pmove title="Move this panel to another figure"><option value="">→ fig…</option>${others}</select>` : ''}</div>`;
  }).join('') : '<div class="dim">No panels yet. Select objects and press <b>Group as panel</b>, or <b>Place plot(s)…</b>.</div>') + '</div>';
  const one = selPanels.size === 1 ? doc.panels[[...selPanels][0]] : null;
  if (one?.link) {
    const k = one.link;
    h += `<details open><summary>Provenance of “${esc(one.name)}”</summary><div class="dim">file: ${esc(k.src)}<br>sha256: ${esc((k.hash || '').slice(0, 12))}…${k.data?.path ? `<br>data: ${esc(k.data.path)} (${esc((k.data.sha256 || '').slice(0, 12))}…)` : ''}${k.builtAt ? `<br>built: ${esc(k.builtAt)}` : ''}</div>${k.cmd ? `<pre>${esc(k.cmd)}</pre>` : ''}${k.stats ? `<pre>${esc(k.stats)}</pre>` : ''}</details>`;
  }
  if (S.notes.length) h += S.notes.map((n) => `<div class="warn">⚠ ${esc(n)}</div>`).join('');
  h += `<div class="btns"><button data-act="group" title="Ctrl+G. Select a panel plus loose objects to add the objects (callouts, arrows) to that panel">Group as panel / add to panel</button><button data-act="ungroup" title="Ctrl+Shift+G">Ungroup</button>
    <button data-act="place">Place plot(s)…</button><button data-act="watch">${S.watching ? 'Stop watching' : 'Watch plots folder…'}</button>${S.watching ? '<button data-act="check">Check now</button>' : ''}</div>
    <div class="btns"><button data-act="arrange" title="Reading order, top-aligned rows, column-snapped">Auto-arrange</button><button data-act="mapcsv">Panel map (CSV)</button><button data-act="exportfigs" title="One .ai per figure + panel map, in a zip">Export figures (.ai ×${figs.length})</button><button data-act="changelog">Change log (${S.changeLog.length})</button></div>`;
  h += `<h4 style="margin-top:12px">Numbering</h4>
    <div class="row"><label>Order</label><select data-set="orderMode"><option value="reading" ${st.orderMode === 'reading' ? 'selected' : ''}>Reading order (automatic)</option><option value="manual" ${st.orderMode === 'manual' ? 'selected' : ''}>Manual (▲▼)</option></select></div>
    <div class="row"><label>Letters</label><select data-set="letterCase"><option value="lower" ${st.letterCase === 'lower' ? 'selected' : ''}>a, b, c</option><option value="upper" ${st.letterCase === 'upper' ? 'selected' : ''}>A, B, C</option></select>
    <label style="width:auto"><input type="checkbox" data-set="omitSingle" ${st.omitSingle ? 'checked' : ''}> none if 1 panel</label></div>
    <h4 style="margin-top:12px">Lab layout</h4>
    <div class="row"><label>Preset</label><select data-lpreset><option value="">(custom)</option>${Object.keys(LY.PRESETS).map((n) => `<option ${doc.settings.layoutName === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></div>
    <div class="row"><label>Cols</label><input type="number" data-lay="cols" value="${L.cols}" min="1" max="24"><label style="width:auto">Gutter</label><input type="number" data-lay="gutter" value="${L.gutter}" step="0.5"></div>
    <div class="row"><label>Margin</label><input type="number" data-lay="margin" value="${L.margin}" step="1"><label style="width:auto">Row gap</label><input type="number" data-lay="rowGap" value="${L.rowGap}" step="0.5"></div>
    <div class="row"><label>Letter at</label><input type="number" data-lay="lx" value="${L.labelOffset[0]}" step="0.5"><input type="number" data-lay="ly" value="${L.labelOffset[1]}" step="0.5"><span class="dim">x, y from frame corner</span></div>
    <div class="row"><label></label><label style="width:auto"><input type="checkbox" data-aop ${S.arrangeOnPlace ? 'checked' : ''}> auto-arrange when placing / updating plots</label></div>`;
  box.innerHTML = h;
}
const panelEl = $('#panels');
panelEl.addEventListener('click', async (e) => {
  const row = e.target.closest('[data-pid]'); const act = e.target.dataset.act;
  if (!act) { if (row && !e.target.closest('input,select,button')) { S.tool === 'direct' && setTool('select'); const id = row.dataset.pid; select(PN.panelObjects(S.doc.pages[S.page], id).map((o) => o.id)); } return; }
  const pid = row?.dataset.pid;
  switch (act) {
    case 'group': groupSel(); break;
    case 'ungroup': ungroupSel(); break;
    case 'place': $('#plot-input').click(); break;
    case 'pupdate': S.updateTarget = pid; $('#update-input').click(); break;
    case 'watch': S.watching ? stopWatch() : startWatch(); break;
    case 'check': await pollFolder(true); break;
    case 'arrange': doArrange(); break;
    case 'mapcsv': download(new Blob([PN.panelMapCsv(S.doc)], { type: 'text/csv' }), `${S.doc.name}-panel-map.csv`); break;
    case 'exportfigs': await exportFigures(); break;
    case 'changelog': showChangeLog(); break;
    case 'pup': mutate(() => PN.reorderPanel(S.doc, pid, -1)); break;
    case 'pdown': mutate(() => PN.reorderPanel(S.doc, pid, 1)); break;
  }
});
panelEl.addEventListener('change', (e) => {
  const t = e.target;
  if (t.dataset.fig) mutate(() => { S.doc.pages[S.page].fig[t.dataset.fig] = t.value; });
  else if (t.dataset.set) mutate(() => { S.doc.settings[t.dataset.set] = t.type === 'checkbox' ? t.checked : t.value; if (t.dataset.set === 'orderMode' && t.value === 'manual') PN.orderedPanels(S.doc, S.page).forEach((id, i) => { S.doc.panels[id].order ??= i; }); });
  else if (t.dataset.pname) mutate(() => { S.doc.panels[t.dataset.pname].name = t.value || 'Panel'; });
  else if (t.dataset.pmove !== undefined) { if (t.value === '') return; const id = t.closest('[data-pid]').dataset.pid; mutate(() => { PN.movePanelToPage(S.doc, id, +t.value, { margin: LY.layoutOf(S.doc).margin + 10 }); S.sel = new Set(); }); status('Moved panel — letters and references renumbered'); }
  else if (t.dataset.lpreset !== undefined) { if (!t.value) return; mutate(() => { S.doc.settings.layout = { ...LY.PRESETS[t.value] }; S.doc.settings.layoutName = t.value; }); }
  else if (t.dataset.lay) mutate(() => { const L = { ...LY.layoutOf(S.doc) }; const k = t.dataset.lay; const v = parseFloat(t.value); if (k === 'lx') L.labelOffset = [v, L.labelOffset[1]]; else if (k === 'ly') L.labelOffset = [L.labelOffset[0], v]; else L[k] = v; S.doc.settings.layout = L; delete S.doc.settings.layoutName; });
  else if (t.dataset.aop !== undefined) S.arrangeOnPlace = t.checked;
});
$('#chk-snap').addEventListener('change', (e) => { S.snap = e.target.checked; });
$('#chk-grid').addEventListener('change', (e) => { S.showGrid = e.target.checked; drawOverlay(); });

function groupSel() {
  const all = selObjs(); const loose = all.filter((o) => !o.panel);
  const existing = [...new Set(all.map((o) => o.panel).filter(Boolean))];
  if (existing.length === 1 && loose.length) { // add callouts / arrows to a panel: they move, number and export with it
    mutate(() => { loose.forEach((o) => { o.panel = existing[0]; o.role = 'annot'; }); });
    return status(`Added ${loose.length} object(s) to “${S.doc.panels[existing[0]].name}”`);
  }
  if (existing.length > 1) return status('Selection spans several panels — select loose objects, or one panel plus its callouts');
  if (!loose.length) return status('Select some objects first');
  mutate(() => { const id = PN.groupAsPanel(S.doc, S.page, loose.map((o) => o.id), { layer: S.activeLayer }); PN.placeLabel(S.doc, S.page, id, LY.layoutOf(S.doc).labelOffset); S.sel = new Set(PN.panelObjects(S.doc.pages[S.page], id).map((o) => o.id)); });
}
function ungroupSel() {
  const ids = [...new Set(selObjs().map((o) => o.panel).filter(Boolean))]; if (!ids.length) return;
  mutate(() => { ids.forEach((id) => PN.ungroupPanel(S.doc, id)); S.sel = new Set(); });
}
function doArrange() {
  let plan; mutate(() => { plan = LY.arrange(S.doc, S.page, LY.layoutOf(S.doc)); });
  S.notes = plan.warnings; renderPanels(); status(plan.warnings.length ? `Arranged with ${plan.warnings.length} warning(s)` : 'Panels arranged on the lab grid');
}

// align / distribute operate on panels (or loose objects) as units
function units() { const m = new Map(); for (const o of selObjs()) { const k = o.panel || o.id; if (!m.has(k)) m.set(k, []); m.get(k).push(o); } return [...m.values()]; }
function unitBox(objs) {
  const pg = S.doc.pages[S.page];
  if (objs[0].panel) return PN.panelFrame(pg, objs[0].panel);
  return objs.reduce((u, o) => { const b = objBounds(o); return b ? (u ? [Math.min(u[0], b[0]), Math.min(u[1], b[1]), Math.max(u[2], b[2]), Math.max(u[3], b[3])] : b) : u; }, null);
}
function alignSel(mode) {
  const us = units(); if (us.length < 2) return;
  const boxes = us.map(unitBox);
  const d = mode === 'dh' ? LY.distributeDeltas(boxes, 'h') : mode === 'dv' ? LY.distributeDeltas(boxes, 'v') : LY.alignDeltas(boxes, mode);
  mutate(() => us.forEach((u, i) => u.forEach((o) => transformObject(o, translate(d[i].dx, d[i].dy)))));
}
$('#props').addEventListener('click', (e) => { const a = e.target.dataset.align; if (a) alignSel(a); });

// ---------- linked plots ----------
function groupFiles(files) { // pair foo.pdf with foo.json
  const pdfs = files.filter((f) => /\.pdf$/i.test(f.name)); const js = new Map(files.filter((f) => /\.json$/i.test(f.name)).map((f) => [stem(f.name), f]));
  return pdfs.map((f) => ({ pdf: f, json: js.get(stem(f.name)) }));
}
const readJson = async (f) => { try { return f ? JSON.parse(await f.text()) : {}; } catch { return {}; } };
const bytesOf = async (f) => new Uint8Array(await f.arrayBuffer());
function freeSlot() {
  const pg = S.doc.pages[S.page]; const L = LY.layoutOf(S.doc);
  const used = pg.objects.reduce((u, o) => { const b = objBounds(o); return b ? (u ? [Math.min(u[0], b[0]), Math.min(u[1], b[1]), Math.max(u[2], b[2]), Math.max(u[3], b[3])] : b) : u; }, null);
  return [L.margin, used ? used[3] + 24 : L.margin + 12];
}
async function placeFiles(files) {
  const items = groupFiles([...files]); if (!items.length) return status('Choose one or more .pdf plot files (optionally with matching .json sidecars)');
  S._before ??= snapshot(); const notes = []; const added = [];
  for (const it of items) {
    try {
      const r = await placePlot(S.doc, S.page, await bytesOf(it.pdf), { name: it.pdf.name, src: it.pdf.name, sidecar: await readJson(it.json), at: freeSlot(), layer: S.activeLayer });
      added.push(r.id); notes.push(...r.warnings.map((w) => `${it.pdf.name}: ${w}`));
    } catch (err) { console.error(err); notes.push(`${it.pdf.name}: ${err.message}`); }
  }
  if (S.arrangeOnPlace) { const plan = LY.arrange(S.doc, S.page, LY.layoutOf(S.doc)); notes.push(...plan.warnings); }
  commit(); S.notes = notes; S.sel = new Set(added.flatMap((id) => PN.panelObjects(S.doc.pages[S.page], id).map((o) => o.id))); renderAll();
  status(`Placed ${added.length} linked plot(s)`);
}
async function applyUpdate(id, bytes, sidecar, label = '') {
  S._before ??= snapshot(); const p0 = S.doc.panels[id]; const name = p0.name;
  const r = await refreshPlot(S.doc, id, bytes, { name: p0.link.src, sidecar });
  const notes = [...r.warnings.map((w) => `${name}: ${w}`)];
  const pi = PN.panelPage(S.doc, id);
  if (S.arrangeOnPlace) { if (pi === S.page || true) { const plan = LY.arrange(S.doc, pi, LY.layoutOf(S.doc)); notes.push(...plan.warnings); } }
  else if (r.overlaps.length) notes.push(`${name} now overlaps ${r.overlaps.map((o) => S.doc.panels[o].name).join(', ')} — use Auto-arrange`);
  commit(); S.notes = notes; renderAll();
  status(`Updated “${name}” from ${p0.link.src}${Math.abs(r.widthAfter - r.widthBefore) > 0.5 ? ` (width ${r.widthBefore.toFixed(0)} → ${r.widthAfter.toFixed(0)} pt)` : ''}${label}`);
}
async function updateFromFiles(files) {
  const it = groupFiles([...files])[0]; const id = S.updateTarget || [...new Set(selObjs().map((o) => o.panel).filter(Boolean))][0];
  if (!it || !id || !S.doc.panels[id]?.link) return status('Select a linked panel and choose its new .pdf');
  await applyUpdate(id, await bytesOf(it.pdf), await readJson(it.json));
}
$('#plot-input').addEventListener('change', (e) => { const f = [...e.target.files]; e.target.value = ''; placeFiles(f); });
$('#update-input').addEventListener('change', (e) => { const f = [...e.target.files]; e.target.value = ''; updateFromFiles(f); });

let pollTimer = null, polling = false;
async function pollFolder(manual = false) {
  if (!S.dir || polling || drag) return; polling = true;
  try {
    const n = await checkFolder(S.dir, S.doc, (id, bytes, side) => applyUpdate(id, bytes, side, ' (folder watch)'), (id, err) => { if (manual) status(`Cannot read ${S.doc.panels[id]?.link?.src}: ${err.message}`); });
    if (manual && !n) status('All linked plots are up to date');
  } finally { polling = false; }
}
async function startWatch() {
  if (!window.showDirectoryPicker) return modal('Folder watching not available', '<p>This browser has no File System Access API. Use Chrome or Edge, or update plots one at a time with the ↻ button.</p>');
  try { S.dir = await window.showDirectoryPicker({ id: 'plots', mode: 'read' }); } catch { return; }
  S.watching = true; clearInterval(pollTimer); pollTimer = setInterval(() => pollFolder(), 2500); renderPanels(); await pollFolder(true);
}
function stopWatch() { S.watching = false; S.dir = null; clearInterval(pollTimer); renderPanels(); }

// ---------- reports & exports ----------
function showChangeLog() {
  const html = S.changeLog.length ? S.changeLog.map((c) => `<p><b>${new Date(c.t).toLocaleTimeString()}</b></p><ul>${c.changes.map((x) => `<li>${esc(x.name)}: <b>${esc(x.from)}</b> → <b>${esc(x.to)}</b></li>`).join('')}</ul>`).join('')
    : '<p>No panel has been renumbered in this session.</p>';
  const since = PN.diffLabels(S.labelsAtSave || {}, PN.labelMap(S.doc), S.doc);
  modal('Panel renumbering log', `<p>Net change since the file was opened/saved (use this to fix references in the manuscript text):</p>${since.length ? `<ul>${since.map((x) => `<li>${esc(x.name)}: <b>${esc(x.from)}</b> → <b>${esc(x.to)}</b></li>`).join('')}</ul>` : '<p>None.</p>'}<hr>${html}`);
}
async function exportFigures() {
  status('Building per-figure files…'); const figs = PN.figureInfo(S.doc); const files = [];
  for (const f of figs) {
    const sub = { name: f.file, layers: S.doc.layers, pages: [S.doc.pages[f.page]], panels: S.doc.panels, settings: S.doc.settings, meta: {} };
    files.push({ name: `${f.file}.ai`, data: await docToPdf(sub, { embedState: false }) });
  }
  files.push({ name: 'panel-map.csv', data: new TextEncoder().encode(PN.panelMapCsv(S.doc)) });
  download(new Blob([zip(files)], { type: 'application/zip' }), `${S.doc.name}-figures.zip`);
  status(`Exported ${figs.length} figure file(s) + panel-map.csv (files are named for ai-figures-pdf)`);
}

window.addEventListener('keydown', (e) => {
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'g') { e.preventDefault(); e.shiftKey ? ungroupSel() : groupSel(); }
});

// boot
S._before = snapshot(); syncButtons(); setTool('select'); refreshPages(); renderAll();
window.__api = { PN, LY, placeFiles, applyUpdate, groupSel, doArrange, alignSel, relabel, exportFigures, checkFolder: (dir) => checkFolder(dir, S.doc, (id, b, sc) => applyUpdate(id, b, sc, ' (folder watch)')), loadDoc, doOpen, save, docToPdf: () => docToPdf(S.doc), select, setTool, fit, renderAll, objBBox, selBBox };
