// Linked plots: a panel whose artwork comes from a generated PDF (e.g. plotsuite-plot output).
// Re-importing a newer PDF swaps only the plot artwork (role 'plot'/'frame'); the letter, your annotations
// (role 'annot'), the panel's position and its scale are kept.
import { importPdf } from './import.js';
import { newId, makePath, rectSegs, transformObject, translate, mul, scaleAbout } from './model.js';
import { ensure, groupAsPanel, panelObjects, panelFrame, panelPage, makeLabelObject, placeLabel, orderedPanels } from './panels.js';
import { union } from './geom.js';

export async function sha256(bytes) {
  const d = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
export const stem = (name) => name.replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');

// Convert the first page of a PDF into plot objects + an invisible frame rectangle (the page box).
async function readPlot(bytes, name) {
  const d = await importPdf(bytes, name, () => {});
  const pg = d.pages[0];
  const objs = pg.objects.map((o) => ({ ...o, id: newId(), role: 'plot' }));
  const frame = makePath(rectSegs(0, 0, pg.w, pg.h), { fill: null, stroke: null, sw: 0, role: 'frame' });
  return { objs: [frame, ...objs], w: pg.w, h: pg.h, warnings: d.meta?.warnings || [] };
}

export function cleanSidecar(raw) {
  if (!raw || typeof raw !== 'object') return {};
  const keep = {};
  for (const k of ['cmd', 'tool', 'builtAt', 'stats', 'args', 'preset', 'fingerprint']) if (raw[k] != null) keep[k] = typeof raw[k] === 'string' ? raw[k].slice(0, 20000) : raw[k];
  if (raw.data && typeof raw.data === 'object') keep.data = { path: String(raw.data.path ?? ''), sha256: String(raw.data.sha256 ?? '') };
  return keep;
}

export async function placePlot(doc, pi, bytes, { name, src, sidecar = {}, at: atOpt, layer, panelName }) {
  ensure(doc); const page = doc.pages[pi];
  const { objs, w, h, warnings } = await readPlot(bytes, name || src);
  const at = typeof atOpt === 'function' ? atOpt(w, h) : atOpt; // a function lets the caller pick a free slot once the plot's size is known
  for (const o of objs) { o.layer = layer; transformObject(o, translate(at[0], at[1])); }
  page.objects.push(...objs);
  const link = { src: src || name, hash: await sha256(bytes), frame: [w, h], placedAt: Date.now(), ...cleanSidecar(sidecar) };
  const id = groupAsPanel(doc, pi, objs.map((o) => o.id), { name: panelName || stem(src || name), link, layer });
  return { id, w, h, warnings };
}

export async function refreshPlot(doc, id, bytes, { name, sidecar = {} } = {}) {
  ensure(doc); const pi = panelPage(doc, id); if (pi < 0) throw new Error('panel not found');
  const page = doc.pages[pi]; const p = doc.panels[id]; const link = p.link; if (!link) throw new Error('panel is not linked to a plot');
  const f0 = panelFrame(page, id);
  const { objs, w, h, warnings } = await readPlot(bytes, name || link.src);
  // keep the user's scale: ratio of current frame height to the native height of the previous plot file
  const s = link.frame?.[1] ? (f0[3] - f0[1]) / link.frame[1] : 1;
  const layer = page.objects.find((o) => o.panel === id)?.layer;
  const T = mul(translate(f0[0], f0[1]), scaleAbout(s, s, 0, 0));
  for (const o of objs) { o.layer = layer; transformObject(o, T); o.panel = id; }
  const idx = page.objects.findIndex((o) => o.panel === id && (o.role === 'plot' || o.role === 'frame'));
  page.objects = page.objects.filter((o) => !(o.panel === id && (o.role === 'plot' || o.role === 'frame')));
  page.objects.splice(Math.max(0, idx), 0, ...objs);
  const oldW = f0[2] - f0[0];
  p.link = { ...link, hash: await sha256(bytes), frame: [w, h], updatedAt: Date.now(), ...cleanSidecar(sidecar) };
  return { widthBefore: oldW, widthAfter: w * s, oldRight: f0[2], warnings, overlaps: overlapsFor(doc, pi, id) };
}

// other panels whose frame intersects this one
export function overlapsFor(doc, pi, id) {
  const page = doc.pages[pi]; const a = panelFrame(page, id); if (!a) return [];
  return orderedPanels(doc, pi).filter((o) => o !== id).filter((o) => {
    const b = panelFrame(page, o); return b && a[0] < b[2] - 0.5 && a[2] > b[0] + 0.5 && a[1] < b[3] - 0.5 && a[3] > b[1] + 0.5;
  });
}

// ---- watching a plots folder (File System Access API; any object with the same shape works for tests) ----
async function fileAt(dir, path) {
  const parts = path.split('/').filter(Boolean); let d = dir;
  for (const part of parts.slice(0, -1)) d = await d.getDirectoryHandle(part);
  return (await d.getFileHandle(parts.at(-1))).getFile();
}
export async function readFromFolder(dir, src) {
  const file = await fileAt(dir, src); const bytes = new Uint8Array(await file.arrayBuffer());
  let sidecar = {}; try { sidecar = JSON.parse(await (await fileAt(dir, src.replace(/\.pdf$/i, '.json'))).text()); } catch { /* optional */ }
  return { bytes, sidecar, lastModified: file.lastModified };
}
// One pass over every linked panel. Calls onUpdate(id, bytes, sidecar) when the file's content hash differs.
export async function checkFolder(dir, doc, onUpdate, onProblem = () => {}) {
  let n = 0;
  for (const p of Object.values(doc.panels || {})) {
    if (!p.link?.src) continue;
    try {
      const { bytes, sidecar, lastModified } = await readFromFolder(dir, p.link.src);
      const h = await sha256(bytes);
      // the sidecar's fingerprint (data + command) is authoritative when present: a PDF can differ byte-wise without the plot changing
      const changed = sidecar.fingerprint ? sidecar.fingerprint !== p.link.fingerprint : h !== p.link.hash;
      if (changed) { await onUpdate(p.id, bytes, sidecar); n++; }
      else if (lastModified) p.link.seen = lastModified;
    } catch (e) { onProblem(p.id, e); }
  }
  return n;
}
