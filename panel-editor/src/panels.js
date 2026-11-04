// Panel registry, figure numbering, automatic panel letters and cross-reference tokens.
//
// Model additions (all optional, so plain .ai files keep working):
//   doc.pages[i].fig   = { kind: 'main'|'supp'|'ed', title }        // an artboard is a figure
//   doc.panels[id]     = { id, name, order, link, label }           // registry
//   obj.panel = id, obj.role = 'plot' | 'frame' | 'label' | 'annot'
//   text obj .tpl      = "… {{ref:ID}} …"  -> obj.str is the resolved text
//   doc.settings       = { orderMode, letterCase, omitSingle, rowTol, names }
import { newId, translate, transformObject, clone, rectSegs, makePath } from './model.js';
import { objBounds, union } from './geom.js';

export const DEFAULTS = {
  orderMode: 'reading', // 'reading' (top→bottom, left→right) | 'manual'
  letterCase: 'lower', // 'lower' (Nature) | 'upper'
  omitSingle: true, // no letter when a figure has a single panel
  rowTol: 30, // pt: panels whose tops differ by less than this are in the same row
  names: { main: 'Fig. ', supp: 'Supplementary Fig. ', ed: 'Extended Data Fig. ' }, // prefix + number, so 'Figure S' + 1 = 'Figure S1'
  fileNames: { main: 'Figure ', supp: 'Supplementary Figure ', ed: 'Extended Data Figure ' },
  labelSize: 8, labelBold: true,
};
export const KINDS = { main: 'Main figure', supp: 'Supplementary', ed: 'Extended Data' };

export const settings = (doc) => ({ ...DEFAULTS, ...(doc.settings || {}), names: { ...DEFAULTS.names, ...(doc.settings?.names || {}) }, fileNames: { ...DEFAULTS.fileNames, ...(doc.settings?.fileNames || {}) } });
export const ensure = (doc) => { doc.panels ??= {}; doc.settings ??= {}; for (const p of doc.pages) p.fig ??= { kind: 'main', title: '' }; return doc; };

export function letter(i, upper = false) {
  let s = ''; i += 1;
  while (i > 0) { const r = (i - 1) % 26; s = String.fromCharCode(97 + r) + s; i = Math.floor((i - 1) / 26); }
  return upper ? s.toUpperCase() : s;
}

// ---- figures -------------------------------------------------------------------------------------
export function figureInfo(doc) {
  ensure(doc); const st = settings(doc); const count = {};
  return doc.pages.map((p, i) => {
    const kind = p.fig.kind; count[kind] = (count[kind] || 0) + 1;
    return { page: i, kind, n: count[kind], name: `${st.names[kind]}${count[kind]}`, file: `${st.fileNames[kind]}${count[kind]}`, title: p.fig.title || '' };
  });
}

// ---- panels --------------------------------------------------------------------------------------
export const panelObjects = (page, id) => page.objects.filter((o) => o.panel === id);
export function panelPage(doc, id) { return doc.pages.findIndex((p) => p.objects.some((o) => o.panel === id)); }

// Bounds used for layout: the plot frame (or plot) when present, otherwise everything except the letter.
export function panelFrame(page, id) {
  const objs = panelObjects(page, id);
  const core = objs.filter((o) => o.role === 'frame' || o.role === 'plot');
  const use = core.length ? core : objs.filter((o) => o.role !== 'label');
  return use.reduce((u, o) => union(u, objBounds(o)), null);
}
export function panelBounds(page, id) { // full extent including annotations and the letter
  return panelObjects(page, id).reduce((u, o) => union(u, objBounds(o)), null);
}

export function orderedPanels(doc, pi) {
  ensure(doc); const st = settings(doc); const page = doc.pages[pi];
  const ids = [...new Set(page.objects.filter((o) => o.panel && doc.panels[o.panel]).map((o) => o.panel))];
  const items = ids.map((id) => ({ id, f: panelFrame(page, id) || [0, 0, 0, 0] }));
  if (st.orderMode === 'manual') {
    return items.sort((a, b) => (doc.panels[a.id].order ?? 1e9) - (doc.panels[b.id].order ?? 1e9) || a.f[1] - b.f[1] || a.f[0] - b.f[0]).map((x) => x.id);
  }
  items.sort((a, b) => a.f[1] - b.f[1] || a.f[0] - b.f[0]);
  const rows = [];
  for (const it of items) {
    const r = rows.find((row) => Math.abs(it.f[1] - row.top) <= st.rowTol);
    if (r) r.items.push(it); else rows.push({ top: it.f[1], items: [it] });
  }
  rows.sort((a, b) => a.top - b.top);
  return rows.flatMap((r) => r.items.sort((a, b) => a.f[0] - b.f[0]).map((x) => x.id));
}

// id -> { fig:'Fig. 2', letter:'b', full:'Fig. 2b', short:'2b', page, index }
export function computeLabels(doc) {
  ensure(doc); const st = settings(doc); const figs = figureInfo(doc); const out = {};
  doc.pages.forEach((page, pi) => {
    const ids = orderedPanels(doc, pi); const f = figs[pi];
    ids.forEach((id, idx) => {
      const single = st.omitSingle && ids.length === 1;
      const l = single ? '' : letter(idx, st.letterCase === 'upper');
      out[id] = { fig: f.name, letter: l, full: `${f.name}${l}`, short: `${f.n}${l}`, page: pi, index: idx, kind: f.kind, n: f.n };
    });
  });
  return out;
}

export function resolveTemplate(tpl, labels) {
  return tpl.replace(/\{\{(ref|fig|letter|short):([^}]+)\}\}/g, (_, kind, id) => {
    const L = labels[id.trim()]; if (!L) return '??';
    return kind === 'ref' ? L.full : kind === 'fig' ? L.fig : kind === 'letter' ? L.letter : L.short;
  });
}

// Recompute every letter / reference. Returns { changes, touched } where changes lists labels that moved.
export function applyLabels(doc) {
  ensure(doc); const labels = computeLabels(doc); const changes = []; const touched = [];
  for (const [id, L] of Object.entries(labels)) {
    const p = doc.panels[id];
    if (p.label !== undefined && p.label !== L.full) changes.push({ id, name: p.name, from: p.label, to: L.full });
    p.label = L.full; p.letter = L.letter; p.figName = L.fig;
  }
  for (const page of doc.pages) for (const o of page.objects) {
    if (o.type !== 'text') continue;
    let s = null;
    if (o.role === 'label' && o.panel && labels[o.panel]) s = labels[o.panel].letter;
    else if (o.tpl) s = resolveTemplate(o.tpl, labels);
    if (s !== null && s !== o.str) { o.str = s; delete o.origW; touched.push(o.id); }
  }
  // drop registry entries whose objects are gone
  for (const id of Object.keys(doc.panels)) if (!labels[id]) delete doc.panels[id];
  return { changes, touched, labels };
}

// ---- creating / editing panels ------------------------------------------------------------------
export function makeLabelObject(doc, panelId, layer) {
  const st = settings(doc);
  return {
    id: newId(), type: 'text', str: 'a', m: [1, 0, 0, -1, 0, 0], size: st.labelSize, fontName: 'Helvetica', family: 'Helvetica', generic: 'sans-serif',
    bold: st.labelBold, italic: false, fill: '#000000', stroke: null, sw: 1, opacity: 1, leading: 1.2, clips: [], layer, panel: panelId, role: 'label',
  };
}

// Place the letter at the top-left corner of the panel frame, offset by (dx,dy) (doc space, y down).
export function placeLabel(doc, pi, panelId, offset = [0, 0]) {
  const page = doc.pages[pi]; const lab = page.objects.find((o) => o.panel === panelId && o.role === 'label'); const f = panelFrame(page, panelId);
  if (!lab || !f) return;
  const size = lab.size;
  // baseline so that the letter's cap-top sits on the frame top (+offset)
  lab.m = [Math.abs(lab.m[0]) || 1, 0, 0, -(Math.abs(lab.m[3]) || 1), f[0] + offset[0], f[1] + offset[1] + size * 0.72];
}

export function groupAsPanel(doc, pi, objIds, { name, link = null, label = true, layer } = {}) {
  ensure(doc); const page = doc.pages[pi]; const objs = page.objects.filter((o) => objIds.includes(o.id));
  if (!objs.length) return null;
  const id = 'p' + newId().slice(1);
  const n = Object.keys(doc.panels).length + 1;
  doc.panels[id] = { id, name: name || `Panel ${n}`, order: null, link };
  for (const o of objs) { o.panel = id; o.role ??= 'annot'; }
  if (label) { const lab = makeLabelObject(doc, id, layer || objs[0].layer); page.objects.push(lab); placeLabel(doc, pi, id); }
  return id;
}

export function ungroupPanel(doc, id) {
  for (const page of doc.pages) {
    page.objects = page.objects.filter((o) => !(o.panel === id && (o.role === 'label' || o.role === 'frame')));
    for (const o of page.objects) if (o.panel === id) { delete o.panel; delete o.role; }
  }
  delete doc.panels[id];
}

export function movePanelToPage(doc, id, toPage, { margin = 36, gap = 12 } = {}) {
  const from = panelPage(doc, id); if (from < 0 || from === toPage) return false;
  const src = doc.pages[from], dst = doc.pages[toPage];
  const objs = panelObjects(src, id); src.objects = src.objects.filter((o) => o.panel !== id);
  const b = objs.reduce((u, o) => union(u, objBounds(o)), null);
  const used = dst.objects.reduce((u, o) => union(u, objBounds(o)), null);
  // drop it below existing content, or at the top-left margin of an empty artboard
  const tx = margin - b[0], ty = (used ? used[3] + gap : margin) - b[1];
  for (const o of objs) transformObject(o, translate(tx, ty));
  dst.objects.push(...objs);
  return true;
}

export function reorderPanel(doc, id, delta) { // manual mode: move one step earlier (-1) / later (+1)
  const pi = panelPage(doc, id); const ids = orderedPanels(doc, pi); const i = ids.indexOf(id), j = Math.min(Math.max(i + delta, 0), ids.length - 1);
  if (i < 0 || j === i) return false;
  ids.splice(i, 1); ids.splice(j, 0, id); ids.forEach((pid, k) => { doc.panels[pid].order = k; });
  return true;
}

// ---- reporting ----------------------------------------------------------------------------------
export function panelMap(doc) {
  const labels = computeLabels(doc);
  return Object.values(labels).sort((a, b) => a.page - b.page || a.index - b.index).map((L) => {
    const p = doc.panels[Object.keys(labels).find((k) => labels[k] === L)];
    return { id: p.id, name: p.name, label: L.full, figure: L.fig, letter: L.letter, source: p.link?.src || '', data: p.link?.data?.path || '', dataHash: p.link?.data?.sha256 || '', cmd: p.link?.cmd || '' };
  });
}
export function panelMapCsv(doc) {
  const rows = panelMap(doc); const cols = ['label', 'figure', 'letter', 'name', 'id', 'source', 'data', 'dataHash', 'cmd'];
  const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  return [cols.join(','), ...rows.map((r) => cols.map((c) => q(r[c])).join(','))].join('\n') + '\n';
}
// Differences between two label maps ({id: full}) — what you must fix in the manuscript text.
export function diffLabels(before, after, doc) {
  const out = [];
  for (const id of Object.keys({ ...before, ...after })) {
    if (before[id] !== after[id]) out.push({ id, name: doc.panels[id]?.name || id, from: before[id] ?? '(new)', to: after[id] ?? '(removed)' });
  }
  return out;
}
export const labelMap = (doc) => Object.fromEntries(Object.entries(computeLabels(doc)).map(([k, v]) => [k, v.full]));
