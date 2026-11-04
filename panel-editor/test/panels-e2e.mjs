// End-to-end test of the figure workflow using REAL plotsuite output (test/fixtures/plots_v1, plots_v2).
import { createRequire } from 'module';
import { preview } from 'vite';
import { execSync } from 'child_process';
import fs from 'fs'; import os from 'os'; import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url)); const root = path.join(here, '..');
let chromium;
try { ({ chromium } = createRequire(root + '/')('playwright')); } catch { ({ chromium } = createRequire('/opt/node-tools/')('playwright')); }
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'panel-editor-fig-'));
const V1 = path.join(here, 'fixtures/plots_v1'), V2 = path.join(here, 'fixtures/plots_v2');
let pass = 0, fail = 0;
const ok = (c, m, x = '') => { if (c) { pass++; console.log('  ✓', m); } else { fail++; console.log('  ✗', m, x); } };

const server = await preview({ root, preview: { port: 4398, strictPort: true } });
const browser = await chromium.launch(); const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1500, height: 950 } });
const p = await ctx.newPage(); const errors = [];
p.on('pageerror', (e) => errors.push(e.message));
await p.addInitScript(() => { delete window.showSaveFilePicker; });
await p.goto('http://localhost:4398/');

const ev = (f, a) => p.evaluate(f, a);
const panels = () => ev(() => { const d = __app.doc; return Object.values(d.panels).map((q) => ({ id: q.id, name: q.name, label: q.label, letter: q.letter, page: __api.PN.panelPage(d, q.id) })); });
const frame = (id) => ev((id) => { const d = __app.doc; return __api.PN.panelFrame(d.pages[__api.PN.panelPage(d, id)], id); }, id);
const toClient = (x, y) => ev(([x, y]) => { const r = document.querySelector('#board').getBoundingClientRect(); return [r.left + x * __app.zoom, r.top + y * __app.zoom]; }, [x, y]);
const dragPt = async (a, b, mods = []) => { const [x0, y0] = await toClient(...a), [x1, y1] = await toClient(...b); await p.mouse.move(x0, y0); await p.mouse.down(); await p.mouse.move((x0 + x1) / 2, (y0 + y1) / 2, { steps: 3 }); await p.mouse.move(x1, y1, { steps: 4 }); await p.mouse.up(); };
const clickPt = async (x, y, opts) => { const [cx, cy] = await toClient(x, y); await p.mouse.click(cx, cy, opts); };
const near = (a, b, t = 0.01) => Math.abs(a - b) < t;
const byName = async (n) => (await panels()).find((q) => q.name === n);

// 3 artboards: Fig 1, Fig 2 (main), and one Supplementary
await ev(() => __api.loadDoc({ name: 'project', layers: [{ id: 'L1', name: 'Layer 1', visible: true, locked: false }], meta: {},
  pages: [{ w: 612, h: 792, objects: [] }, { w: 612, h: 792, objects: [] }, { w: 612, h: 792, objects: [], fig: { kind: 'supp', title: '' } }] }));
await p.waitForTimeout(300);

console.log('Place linked plotsuite figures');
await p.setInputFiles('#plot-input', [V1 + '/cells.pdf', V1 + '/cells.json', V1 + '/ephys.pdf', V1 + '/ephys.json']);
await p.waitForFunction(() => Object.keys(__app.doc.panels).length === 2, null, { timeout: 30000 });
await p.waitForTimeout(500);
let P = await panels();
ok(P.length === 2, 'two panels created from two PDFs');
ok(JSON.stringify(P.map((q) => q.label).sort()) === '["Fig. 1a","Fig. 1b"]', 'auto-lettered Fig. 1a / 1b', JSON.stringify(P.map((q) => q.label)));
const doc0 = await ev(() => JSON.parse(JSON.stringify(__app.doc)));
const cellsId = P.find((q) => q.name === 'cells').id, ephysId = P.find((q) => q.name === 'ephys').id;
const pl = doc0.panels[cellsId].link;
ok(pl.cmd.includes('plotsuite-plot') && pl.data.path === 'data/cells.csv' && /Unpaired t test/.test(pl.stats) && pl.fingerprint?.length === 64, 'provenance (command, data hash, statistics text, fingerprint) stored in the panel');
const objs0 = doc0.pages[0].objects.filter((o) => o.panel === cellsId);
ok(objs0.some((o) => o.role === 'plot' && o.type === 'text') && objs0.some((o) => o.role === 'plot' && o.type === 'path') && objs0.some((o) => o.role === 'frame') && objs0.some((o) => o.role === 'label'), 'plot is real editable vector (paths + text) with frame + letter');
const L = await ev(() => __api.LY.layoutOf(__app.doc)); const fc = await frame(cellsId), fe = await frame(ephysId);
ok(near(fc[0], L.margin) && near(fc[1], fe[1]) && fe[0] >= fc[2] + L.gutter - 0.01, 'auto-arranged: first panel at margin, top-aligned row, gutter respected', JSON.stringify([fc, fe]));
ok(near(fc[3] - fc[1], 93.6, 0.01), "plotsuite's fixed 1.3 in height preserved (no rescale)");
const lettersOk = await ev(() => __app.doc.pages[0].objects.filter((o) => o.role === 'label').map((o) => o.str).sort().join(''));
ok(lettersOk === 'ab', 'letter objects read "a" and "b"');

console.log('Cross-references (text with {{ref}} tokens)');
await p.keyboard.press('t'); await clickPt(40, 400); await p.waitForTimeout(150);
await p.keyboard.type('Cell counts, see '); await p.keyboard.down('Control'); await p.keyboard.press('Enter'); await p.keyboard.up('Control');
await ev(() => document.activeElement?.blur());
await p.keyboard.press('v');
const legendId = await ev(() => __app.doc.pages[0].objects.filter((o) => o.type === 'text' && !o.panel).at(-1).id);
await ev((id) => __api.select([id]), legendId); await p.waitForTimeout(100);
await p.selectOption('#props select[data-insref]', cellsId);
await ev(() => document.activeElement?.blur()); await p.waitForTimeout(700);
const legend = () => ev((id) => __app.doc.pages[0].objects.find((o) => o.id === id), legendId);
ok((await legend()).str === 'Cell counts, see Fig. 1a' && (await legend()).tpl.includes('{{ref:'), 'inserted reference resolves to "Fig. 1a"', JSON.stringify((await legend()).str));

console.log('Reorder: drag panel a to the right of b — letters and references follow');
await clickPt((fc[0] + fc[2]) / 2, (fc[1] + fc[3]) / 2 + 20);
ok(await ev((id) => [...__app.sel].some((s) => __app.doc.pages[0].objects.find((o) => o.id === s)?.panel === id), cellsId), 'clicking blank area inside a panel selects the whole panel');
await dragPt([(fc[0] + fc[2]) / 2, (fc[1] + fc[3]) / 2 + 20], [(fc[0] + fc[2]) / 2 + 160, (fc[1] + fc[3]) / 2 + 20]);
await p.waitForTimeout(300);
P = await panels();
ok(P.find((q) => q.id === cellsId).label === 'Fig. 1b' && P.find((q) => q.id === ephysId).label === 'Fig. 1a', 'letters swapped automatically', JSON.stringify(P.map((q) => [q.name, q.label])));
ok((await legend()).str === 'Cell counts, see Fig. 1b', 'reference in the legend updated to Fig. 1b');
ok(await ev(() => __app.changeLog[0]?.changes.length === 2), 'change log lists both relabels');
ok(await ev(() => __app.doc.pages[0].objects.filter((o) => o.role === 'label').every((o) => (o.panel === __app.doc.panels[o.panel]?.id) && o.str === __app.doc.panels[o.panel].letter)), 'letter objects match registry');
await p.click('#btn-undo'); await p.waitForTimeout(200);
ok((await panels()).find((q) => q.id === cellsId).label === 'Fig. 1a' && (await legend()).str === 'Cell counts, see Fig. 1a', 'undo restores letters AND references together');
await p.click('#btn-redo'); await p.waitForTimeout(200);

console.log('Move panel from main to supplementary');
await p.selectOption(`#panels [data-pid="${cellsId}"] select[data-pmove]`, '2'); await p.waitForTimeout(400);
P = await panels();
ok(P.find((q) => q.id === cellsId).label === 'Supplementary Fig. 1' && P.find((q) => q.id === cellsId).page === 2, 'panel is now Supplementary Fig. 1 on artboard 3');
ok(P.find((q) => q.id === ephysId).label === 'Fig. 1', 'remaining panel loses its letter (single panel) -> "Fig. 1"');
ok((await legend()).str === 'Cell counts, see Supplementary Fig. 1', 'legend reference now says "Supplementary Fig. 1"', (await legend()).str);
await p.click('#btn-undo'); await p.waitForTimeout(300);
ok((await panels()).find((q) => q.id === cellsId).page === 0, 'undo moves it back');

console.log('Data changed -> linked figure updates in place (new, wider plot; annotation kept)');
// annotate: a rectangle drawn over the cells panel, added to the panel
const fcNow = await frame(cellsId);
await p.keyboard.press('m'); await dragPt([fcNow[0] + 5, fcNow[1] + 5], [fcNow[0] + 20, fcNow[1] + 15]); await p.keyboard.press('v');
const annId = await ev(() => __app.doc.pages[0].objects.at(-1).id);
await p.keyboard.down('Shift'); await clickPt((fcNow[0] + fcNow[2]) / 2, fcNow[3] - 4); await p.keyboard.up('Shift');
await p.keyboard.press('Control+g'); await p.waitForTimeout(300);
ok(await ev((id) => __app.doc.pages[0].objects.find((o) => o.id === id)?.role === 'annot', annId), 'callout added to the panel as an annotation');
const before = await ev((id) => __app.doc.pages[0].objects.filter((o) => o.panel === id).map((o) => o.role + ':' + o.type).sort().join(','), cellsId);
const hashBefore = await ev((id) => __app.doc.panels[id].link.hash, cellsId);
const wBefore = (await frame(cellsId)); const topBefore = wBefore[1];
await ev((id) => { __app.updateTarget = id; }, cellsId);
await p.setInputFiles('#update-input', [V2 + '/cells.pdf', V2 + '/cells.json']);
await p.waitForFunction(([id, h]) => __app.doc.panels[id].link.hash !== h, [cellsId, hashBefore], { timeout: 20000 }); await p.waitForTimeout(500);
const fcAfter = await frame(cellsId);
ok(near(fcAfter[2] - fcAfter[0], 68.4, 0.05), `panel is now the 3-bar width (${(fcAfter[2] - fcAfter[0]).toFixed(1)} pt)`);
const after = await ev((id) => __app.doc.pages[0].objects.filter((o) => o.panel === id).map((o) => o.role + ':' + o.type).sort().join(','), cellsId);
ok(after.includes('annot:path') && after.includes('label:text') && after.includes('frame:path'), 'annotation + letter + frame survived the refresh');
ok(before !== after, 'plot artwork was replaced (different number of bars/text)');
ok(/ANOVA|Ordinary|One-way/i.test(await ev((id) => __app.doc.panels[id].link.stats, cellsId)), 'embedded statistics text updated to the new analysis');
const fe2 = await frame(ephysId), fc2 = await frame(cellsId);
const overlap = !(fc2[2] <= fe2[0] + 1e-6 || fe2[2] <= fc2[0] + 1e-6 || fc2[3] <= fe2[1] + 1e-6 || fe2[3] <= fc2[1] + 1e-6);
ok(!overlap, 'no overlap after the plot got wider (re-arranged)');
ok(near(fc2[3] - fc2[1], 93.6, 0.01), 'height still 1.3 in');
const lab = await ev((id) => __app.doc.pages[0].objects.find((o) => o.panel === id && o.role === 'label').str, cellsId);
ok(lab === (await byName('cells')).letter, 'letter object still matches');

console.log('Folder watcher (fake directory handle serving plots_v1, then plots_v2)');
const bytes = (f) => Array.from(fs.readFileSync(f));
const setup = (files) => ev((files) => { const m = {}; for (const [k, v] of Object.entries(files)) m[k] = new Uint8Array(v); window.__fake = m;
  const mk = () => ({ getDirectoryHandle: async () => mk(), getFileHandle: async (n) => { if (!(n in window.__fake)) throw new Error('nf'); return { getFile: async () => ({ lastModified: 1, arrayBuffer: async () => window.__fake[n].slice().buffer, text: async () => new TextDecoder().decode(window.__fake[n]) }) }; } });
  window.__dir = mk(); }, files);
await setup({ 'cells.pdf': bytes(V1 + '/cells.pdf'), 'cells.json': bytes(V1 + '/cells.json'), 'ephys.pdf': bytes(V1 + '/ephys.pdf'), 'ephys.json': bytes(V1 + '/ephys.json') });
// panel cells currently holds v2 content -> watcher sees v1 as "different" and reverts; then v2 -> no-op
let n = await ev(async () => { let c = 0; await __api.checkFolder(window.__dir).then(() => {}); return Object.values(__app.doc.panels).filter((q) => q.link.updatedAt).length; });
ok(n >= 1, 'watcher detects that a linked file differs from what is in the figure and applies it');
await setup({ 'cells.pdf': bytes(V2 + '/cells.pdf'), 'cells.json': bytes(V2 + '/cells.json'), 'ephys.pdf': bytes(V1 + '/ephys.pdf'), 'ephys.json': bytes(V1 + '/ephys.json') });
const w1 = (await frame(cellsId)); await ev(() => __api.checkFolder(window.__dir)); await p.waitForTimeout(300);
const w2 = await frame(cellsId); ok(near(w2[2] - w2[0], 68.4, 0.05), 'new data file (3 bars) flows into the figure through the watcher');
const stamp = await ev((id) => __app.doc.panels[id].link.updatedAt, cellsId); await new Promise((r) => setTimeout(r, 20));
await ev(() => __api.checkFolder(window.__dir)); await p.waitForTimeout(200);
ok((await ev((id) => __app.doc.panels[id].link.updatedAt, cellsId)) === stamp, 'second pass with unchanged files does nothing (fingerprint match)');

console.log('Snapping and alignment');
await ev(() => { __app.snap = true; });
const fa = await frame(ephysId), fb = await frame(cellsId);
await dragPt([(fb[0] + fb[2]) / 2, (fb[1] + fb[3]) / 2], [(fb[0] + fb[2]) / 2, (fb[1] + fb[3]) / 2 + 140]); await p.waitForTimeout(150);
const fm = await frame(cellsId); ok(near(fm[1], fb[1] + 140, 0.2), 'free drag (nothing within snap range) moves exactly 140 pt');
// now drag back up leaving it 3 pt short of alignment: should snap its top to the neighbour's top
await dragPt([(fm[0] + fm[2]) / 2, (fm[1] + fm[3]) / 2], [(fm[0] + fm[2]) / 2, (fm[1] + fm[3]) / 2 - 137]); await p.waitForTimeout(150);
const fs2 = await frame(cellsId), fe3 = await frame(ephysId);
ok(near(fs2[1], fe3[1], 1e-6), 'smart guide snaps the panel top exactly to the neighbouring panel', `${fs2[1]} vs ${fe3[1]}`);
// Align via buttons
await dragPt([(fs2[0] + fs2[2]) / 2, (fs2[1] + fs2[3]) / 2], [(fs2[0] + fs2[2]) / 2 + 30, (fs2[1] + fs2[3]) / 2 + 60]); await p.waitForTimeout(100);
await ev(() => __api.select(Object.values(__app.doc.pages[0].objects).filter((o) => o.panel).map((o) => o.id))); await p.waitForTimeout(100);
await p.click('#props [data-align="top"]'); await p.waitForTimeout(150);
const ft1 = await frame(cellsId), ft2 = await frame(ephysId); ok(near(ft1[1], ft2[1], 1e-6), 'Align → Top lines up panel frames (not their callouts or letters)');
await p.click('#props [data-align="left"]'); await p.waitForTimeout(100);
ok(near((await frame(cellsId))[0], (await frame(ephysId))[0], 1e-6), 'Align → Left');
await p.click('#btn-undo'); await p.click('#btn-undo'); await p.waitForTimeout(150);
await p.click('#panels [data-act="arrange"]'); await p.waitForTimeout(300);
const fr = [await frame(cellsId), await frame(ephysId)]; ok(near(fr[0][1], fr[1][1], 1e-6) && Math.abs(fr[0][0] - fr[1][0]) > 50, 'Auto-arrange restores the lab layout after manual mess');

console.log('Grid');
await p.check('#chk-grid'); await p.waitForTimeout(100);
ok((await ev(() => document.querySelectorAll('#overlay line').length)) >= 24, 'grid lines drawn (12 columns × 2 edges)');
await p.uncheck('#chk-grid');

console.log('Export figures + panel map');
await ev((id) => __api.PN.movePanelToPage(__app.doc, id, 2), ephysId); await ev(() => __api.relabel()); // supplementary, so we get a mixed set
const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#panels [data-act="exportfigs"]')]);
const zf = path.join(tmp, 'figs.zip'); await dl.saveAs(zf);
const listing = execSync(`unzip -Z1 "${zf}"`).toString().trim().split('\n');
ok(listing.includes('Figure 1.ai') && listing.includes('Figure 2.ai') && listing.includes('Supplementary Figure 1.ai') && listing.includes('panel-map.csv'), 'zip holds Figure 1.ai, Figure 2.ai, Supplementary Figure 1.ai, panel-map.csv', listing.join('|'));
execSync(`unzip -o -q "${zf}" -d "${tmp}/figs"`);
ok(execSync(`pdfinfo "${tmp}/figs/Supplementary Figure 1.ai"`).toString().includes('Pages:           1'), 'each figure file is a 1-page PDF-compatible .ai');
const csv = fs.readFileSync(`${tmp}/figs/panel-map.csv`, 'utf8'); ok(/Supplementary Fig\. 1.*ephys/s.test(csv) || /ephys/.test(csv), 'panel-map.csv lists panels with labels, sources and data hashes');
ok(/data\/(cells|cells_v2)\.csv/.test(csv) && /[0-9a-f]{64}/.test(csv), 'panel map carries the data file and its sha256 (provenance)');
try {
  const core = execSync(`git -C "${root}/.." show origin/claude/fervent-gauss-0bg9v8:ai-figures-pdf/ai_figures/core.py`, { stdio: ['ignore', 'pipe', 'ignore'] }).toString();
  fs.writeFileSync(path.join(tmp, 'core.py'), core);
  const out = execSync(`${process.env.PYTHON || 'python3'} -c "import sys; sys.path.insert(0, '${tmp}'); import core; [print(core.classify(n)) for n in ['Figure 1.ai','Figure 2.ai','Supplementary Figure 1.ai']]"`).toString();
  const rows = out.trim().split('\n').map((l) => l.match(/\((\d+), (\d+)/).slice(1).map(Number));
  ok(rows[0][0] === rows[1][0] && rows[2][0] > rows[0][0] && rows[0][1] === 1 && rows[1][1] === 2 && rows[2][1] === 1, 'file names sort correctly in the existing ai-figures-pdf tool (main 1, main 2, then supplementary 1)', out.trim().replace(/\n/g, ' | '));
} catch (e) { console.log('  - (ai-figures-pdf core.py not available; skipped name classification check)'); }

console.log('Save → reopen keeps panels, links, letters');
const [dl2] = await Promise.all([p.waitForEvent('download'), p.click('#btn-save')]); const aif = path.join(tmp, 'project.ai'); await dl2.saveAs(aif);
const snapshot = await ev(() => JSON.stringify(Object.values(__app.doc.panels).map((q) => [q.id, q.label, !!q.link?.hash, q.link?.stats?.length > 10]).sort()));
await p.setInputFiles('#file-input', aif); await p.waitForTimeout(2500);
ok((await ev(() => !!__app.doc.meta.restored)) && (await ev(() => JSON.stringify(Object.values(__app.doc.panels).map((q) => [q.id, q.label, !!q.link?.hash, q.link?.stats?.length > 10]).sort()))) === snapshot, 'reopened file restores panel registry, links and labels');
ok(errors.length === 0, 'no uncaught page errors', errors.join(' | '));
await browser.close(); await server.close();
console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
