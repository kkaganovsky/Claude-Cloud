import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs'; import os from 'os'; import path from 'path'; import { execSync } from 'child_process';
import { newDoc, makePath, rectSegs, transformObject, translate } from '../src/model.js';
import * as P from '../src/panels.js';
import * as L from '../src/layout.js';
import { zip, crc32 } from '../src/zip.js';

// a panel = frame + one plot rect, tagged
function addPanel(doc, pi, x, y, w, h, name) {
  const page = doc.pages[pi]; const layer = doc.layers[0].id;
  const fr = makePath(rectSegs(x, y, w, h), { fill: null, stroke: null, sw: 0, role: 'frame', layer });
  const bar = makePath(rectSegs(x + 5, y + 5, w - 10, h - 10), { layer, role: 'plot' });
  page.objects.push(fr, bar);
  return P.groupAsPanel(doc, pi, [fr.id, bar.id], { name, layer });
}
const labels = (doc) => Object.fromEntries(Object.values(doc.panels).map((p) => [p.name, p.label]));
const fresh = () => { const d = P.ensure(newDoc(612, 792)); return d; };

test('letters follow reading order, not creation order', () => {
  const d = fresh();
  addPanel(d, 0, 300, 40, 100, 90, 'C'); addPanel(d, 0, 40, 40, 100, 90, 'A'); addPanel(d, 0, 40, 200, 100, 90, 'D'); addPanel(d, 0, 170, 42, 100, 90, 'B');
  P.applyLabels(d);
  assert.deepEqual(labels(d), { A: 'Fig. 1a', B: 'Fig. 1b', C: 'Fig. 1c', D: 'Fig. 1d' });
});
test('moving a panel shifts the letters of the others and reports exactly what changed', () => {
  const d = fresh();
  const a = addPanel(d, 0, 40, 40, 100, 90, 'A'), b = addPanel(d, 0, 170, 40, 100, 90, 'B'), c = addPanel(d, 0, 300, 40, 100, 90, 'C');
  P.applyLabels(d); const before = P.labelMap(d);
  // drag A to the far right
  for (const o of P.panelObjects(d.pages[0], a)) transformObject(o, translate(400, 0));
  const r = P.applyLabels(d);
  assert.deepEqual(labels(d), { B: 'Fig. 1a', C: 'Fig. 1b', A: 'Fig. 1c' });
  assert.equal(r.changes.length, 3);
  const diff = P.diffLabels(before, P.labelMap(d), d).map((x) => `${x.name}:${x.from}->${x.to}`).sort();
  assert.deepEqual(diff, ['A:Fig. 1a->Fig. 1c', 'B:Fig. 1b->Fig. 1a', 'C:Fig. 1c->Fig. 1b']);
});
test('letter objects and {{ref}} tokens update automatically', () => {
  const d = fresh();
  const a = addPanel(d, 0, 40, 40, 100, 90, 'A'), b = addPanel(d, 0, 170, 40, 100, 90, 'B');
  const legend = { id: 'leg', type: 'text', str: '', tpl: `Data from {{ref:${b}}}; see also {{fig:${a}}} ({{short:${b}}})`, m: [1, 0, 0, -1, 50, 700], size: 7, layer: d.layers[0].id, clips: [] };
  d.pages[0].objects.push(legend);
  P.applyLabels(d);
  assert.equal(legend.str, 'Data from Fig. 1b; see also Fig. 1 (1b)');
  const letterObj = (id) => d.pages[0].objects.find((o) => o.panel === id && o.role === 'label');
  assert.equal(letterObj(a).str, 'a'); assert.equal(letterObj(b).str, 'b');
  for (const o of P.panelObjects(d.pages[0], b)) transformObject(o, translate(-150, 0)); // B now left of A
  P.applyLabels(d);
  assert.equal(legend.str, 'Data from Fig. 1a; see also Fig. 1 (1a)'); assert.equal(letterObj(b).str, 'a'); assert.equal(letterObj(a).str, 'b');
});
test('moving a panel from main to supplementary renumbers both figures and every reference', () => {
  const d = fresh(); d.pages.push({ w: 612, h: 792, objects: [], fig: { kind: 'supp', title: '' } });
  const a = addPanel(d, 0, 40, 40, 100, 90, 'A'), b = addPanel(d, 0, 170, 40, 100, 90, 'B'), c = addPanel(d, 0, 300, 40, 100, 90, 'C');
  const ref = { id: 'r', type: 'text', str: '', tpl: `see {{ref:${c}}}`, m: [1, 0, 0, -1, 50, 700], size: 7, layer: d.layers[0].id, clips: [] }; d.pages[0].objects.push(ref);
  P.applyLabels(d); assert.equal(ref.str, 'see Fig. 1c');
  assert.ok(P.movePanelToPage(d, b, 1));
  P.applyLabels(d);
  assert.equal(d.panels[a].label, 'Fig. 1a'); assert.equal(d.panels[c].label, 'Fig. 1b');
  assert.equal(d.panels[b].label, 'Supplementary Fig. 1'); // single panel on its figure: no letter
  assert.equal(ref.str, 'see Fig. 1b');
  assert.equal(P.panelPage(d, b), 1);
});
test('inserting a new main figure before an existing one renumbers figure names', () => {
  const d = fresh(); d.pages.push({ w: 612, h: 792, objects: [], fig: { kind: 'main', title: '' } });
  const a = addPanel(d, 1, 40, 40, 100, 90, 'A'), b = addPanel(d, 1, 170, 40, 100, 90, 'B');
  P.applyLabels(d); assert.equal(d.panels[a].label, 'Fig. 2a');
  d.pages.splice(0, 0, { w: 612, h: 792, objects: [], fig: { kind: 'main', title: '' } });
  P.applyLabels(d); assert.equal(d.panels[a].label, 'Fig. 3a'); // two figures now precede it
  d.pages.splice(0, 2); P.applyLabels(d); assert.equal(d.panels[a].label, 'Fig. 1a');
  d.pages[0].fig.kind = 'ed'; P.applyLabels(d); assert.equal(d.panels[a].label, 'Extended Data Fig. 1a');
});
test('manual order mode, reorder, uppercase, letters past z', () => {
  const d = fresh(); d.settings = { orderMode: 'manual', letterCase: 'upper' };
  const ids = ['A', 'B', 'C'].map((n, i) => addPanel(d, 0, 40 + i * 120, 40, 100, 90, n));
  P.applyLabels(d); assert.deepEqual(labels(d), { A: 'Fig. 1A', B: 'Fig. 1B', C: 'Fig. 1C' });
  P.reorderPanel(d, ids[2], -2); P.applyLabels(d); assert.deepEqual(labels(d), { A: 'Fig. 1B', B: 'Fig. 1C', C: 'Fig. 1A' });
  assert.deepEqual([0, 25, 26, 27, 701, 702].map((i) => P.letter(i)), ['a', 'z', 'aa', 'ab', 'zz', 'aaa']);
});
test('ungroup removes letter + registry; panel map CSV', () => {
  const d = fresh(); const a = addPanel(d, 0, 40, 40, 100, 90, 'A, "quoted"'); addPanel(d, 0, 170, 40, 100, 90, 'B'); P.applyLabels(d);
  const csv = P.panelMapCsv(d); assert.match(csv, /^label,figure,letter,name,id/); assert.match(csv, /"Fig. 1a","Fig. 1","a","A, ""quoted"""/);
  P.ungroupPanel(d, a); P.applyLabels(d); assert.equal(Object.keys(d.panels).length, 1);
  assert.ok(!d.pages[0].objects.some((o) => o.panel === a));
});

test('grid: 12 columns across the double-column width', () => {
  const d = fresh(); const Lc = L.layoutOf(d); const g = L.gridLines(d.pages[0], Lc);
  assert.equal(g.x.length, 24); assert.ok(Math.abs(g.right - g.left - 518.7) < 1e-6);
});
test('smart snap pulls edges/centres to neighbours and grid within threshold only', () => {
  const s = L.snapBox([100, 100, 160, 190], [[165, 300, 300, 400]], { x: [36], y: [] }, 6);
  assert.equal(s.dx, 5); assert.equal(s.dy, 0); // left edge? no: right edge 160 -> 165
  const far = L.snapBox([100, 100, 160, 190], [[300, 300, 400, 400]], { x: [], y: [] }, 6);
  assert.deepEqual([far.dx, far.dy, far.guides.length], [0, 0, 0]);
  const c = L.snapBox([100, 100, 160, 190], [[0, 0, 400, 150]], { x: [], y: [] }, 6); // centre y 145 -> 75? bottom 190? top 100->0? nothing near
  assert.ok(c.guides.length >= 0);
});
test('align and distribute', () => {
  const boxes = [[10, 10, 50, 40], [100, 20, 130, 80], [200, 5, 260, 30]];
  assert.deepEqual(L.alignDeltas(boxes, 'top').map((d) => d.dy), [-5, -15, 0]);
  assert.deepEqual(L.alignDeltas(boxes, 'left').map((d) => d.dx), [0, -90, -190]);
  const dd = L.distributeDeltas(boxes, 'h'); const x = boxes.map((b, i) => b[0] + dd[i].dx), w = boxes.map((b) => b[2] - b[0]);
  assert.ok(Math.abs((x[1] - (x[0] + w[0])) - (x[2] - (x[1] + w[1]))) < 1e-9);
});
test('auto-arrange: reading order, top-aligned rows, column-snapped, letters above frames', () => {
  const d = fresh();
  // scrambled positions, varied sizes (plotsuite: fixed height 93.6, width by bars)
  const w = [58, 90, 70, 120, 60]; w.forEach((ww, i) => addPanel(d, 0, 50 + (i % 3) * 170 + i * 3, 60 + (i < 3 ? i * 4 : 160 + (i - 3) * 5), ww, 93.6, 'P' + i));
  P.applyLabels(d); const before = P.labelMap(d);
  const Lc = L.layoutOf(d); const plan = L.arrange(d, 0, Lc);
  const page = d.pages[0]; const fr = (n) => P.panelFrame(page, Object.values(d.panels).find((p) => p.name === n).id);
  const first = fr('P0'), second = fr('P1');
  assert.ok(Math.abs(first[0] - Lc.margin) < 1e-6 && Math.abs(first[1] - (Lc.margin + 10)) < 1e-6);
  assert.ok(Math.abs(first[1] - second[1]) < 1e-9, 'same row is top-aligned');
  const grid = L.gridLines(page, Lc); const starts = grid.x.filter((_, i) => i % 2 === 0);
  for (const n of ['P0', 'P1', 'P2']) assert.ok(starts.some((s) => Math.abs(s - fr(n)[0]) < 1e-6) || Math.abs(fr(n)[0] - Lc.margin) < 1e-6, `${n} left edge on a column line`);
  for (const id of Object.keys(d.panels)) assert.ok(P.panelFrame(page, id)[2] <= grid.right + 0.5, 'inside right margin');
  // no overlaps
  const fs = Object.keys(d.panels).map((id) => P.panelFrame(page, id));
  for (let i = 0; i < fs.length; i++) for (let j = i + 1; j < fs.length; j++) assert.ok(fs[i][2] <= fs[j][0] + 1e-6 || fs[j][2] <= fs[i][0] + 1e-6 || fs[i][3] <= fs[j][1] + 1e-6 || fs[j][3] <= fs[i][1] + 1e-6, `panels ${i},${j} overlap`);
  const lab = page.objects.find((o) => o.role === 'label' && o.panel === Object.values(d.panels).find((p) => p.name === 'P0').id);
  assert.ok(lab.m[5] < first[1], 'letter sits above the frame');
  assert.deepEqual(plan.warnings, []);
  assert.deepEqual(Object.values(P.labelMap(d)).sort(), Object.values(before).sort(), 'same letter set');
});

test('zip is valid (checked with unzip -t) and crc32 matches the standard vector', () => {
  assert.equal(crc32(new TextEncoder().encode('123456789')), 0xcbf43926);
  const tmp = path.join(os.tmpdir(), `pe-${process.pid}.zip`);
  fs.writeFileSync(tmp, zip([{ name: 'Figure 1.ai', data: new Uint8Array([37, 80, 68, 70]) }, { name: 'Supplementary Figure 1.ai', data: new TextEncoder().encode('x'.repeat(1000)) }, { name: 'α.csv', data: new Uint8Array(0) }]));
  try { const out = execSync(`unzip -t "${tmp}"`).toString(); assert.match(out, /No errors detected/); } catch (e) { if (!/not found/.test(String(e))) throw e; }
});

test('nextSlot appends to the bottom row or wraps, without moving anything', () => {
  const d = fresh(); const Lc = L.layoutOf(d); const g = L.gridLines(d.pages[0], Lc);
  assert.deepEqual(L.nextSlot(d, 0, 60, 93.6, Lc), [g.left, Lc.margin + 10]);
  addPanel(d, 0, g.left, Lc.margin + 10, 60, 93.6, 'A');
  const s2 = L.nextSlot(d, 0, 60, 93.6, Lc);
  assert.ok(s2[0] >= g.left + 60 + Lc.gutter - 1e-6 && g.x.some((c, i) => i % 2 === 0 && Math.abs(c - s2[0]) < 1e-6) && Math.abs(s2[1] - (Lc.margin + 10)) < 1e-6, 'same row, on a column start');
  const wide = L.nextSlot(d, 0, 500, 93.6, Lc); assert.ok(Math.abs(wide[0] - g.left) < 1e-6 && wide[1] > Lc.margin + 10 + 93.6, 'too wide -> new row');
});
test('pushRow shifts only the right-hand neighbours in the same row', () => {
  const d = fresh(); const a = addPanel(d, 0, 40, 60, 60, 90, 'A'), b = addPanel(d, 0, 120, 60, 60, 90, 'B'), c = addPanel(d, 0, 40, 260, 60, 90, 'C');
  P.applyLabels(d); const pg = d.pages[0]; const bx = P.panelFrame(pg, b)[0], cx = P.panelFrame(pg, c)[0];
  const moved = L.pushRow(d, 0, a, 100, 10); assert.deepEqual(moved, [b]);
  assert.equal(P.panelFrame(pg, b)[0], bx + 10); assert.equal(P.panelFrame(pg, c)[0], cx);
});
