// E2E: journal switching, rule editing, pre-flight and naming through the real UI, using real plotsuite fixtures.
import { createRequire } from 'module';
import { preview } from 'vite';
import { execSync } from 'child_process';
import fs from 'fs'; import os from 'os'; import path from 'path';
import { fileURLToPath } from 'url';
const here = path.dirname(fileURLToPath(import.meta.url)); const root = path.join(here, '..');
let chromium; try { ({ chromium } = createRequire(root + '/')('playwright')); } catch { ({ chromium } = createRequire('/opt/node-tools/')('playwright')); }
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pe-journal-')); const V1 = path.join(here, 'fixtures/plots_v1');
let pass = 0, fail = 0; const ok = (c, m, x = '') => { if (c) { pass++; console.log('  ✓', m); } else { fail++; console.log('  ✗', m, x); } };
const server = await preview({ root, preview: { port: 4394, strictPort: true } });
const browser = await chromium.launch(); const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1500, height: 950 } });
const p = await ctx.newPage(); const errors = []; p.on('pageerror', (e) => errors.push(e.message));
await p.addInitScript(() => { delete window.showSaveFilePicker; });
await p.goto('http://localhost:4394/');
const ev = (f, a) => p.evaluate(f, a);
const MM = 72 / 25.4;
const labels = () => ev(() => Object.values(__app.doc.panels).map((q) => `${q.name}=${q.label}`).sort().join(' '));

// Nature-sized project (183 mm), two figures: Fig 1 with two plots, supplementary with one
await ev(() => { const w = 183 * 72 / 25.4; __api.loadDoc({ name: 'proj', layers: [{ id: 'L1', name: 'Layer 1', visible: true, locked: false }], meta: {}, settings: {},
  pages: [{ w, h: 300, objects: [] }, { w, h: 300, objects: [], fig: { kind: 'supp', title: '' } }] }); __api.JN.applyJournalSettings(__app.doc, 'nature'); __api.renderAll(); });
await p.setInputFiles('#plot-input', [V1 + '/cells.pdf', V1 + '/cells.json', V1 + '/ephys.pdf', V1 + '/ephys.json']);
await p.waitForFunction(() => Object.keys(__app.doc.panels).length === 2); await p.waitForTimeout(400);
await ev(() => { __app.page = 1; __api.renderAll(); });
await p.setInputFiles('#plot-input', [V1 + '/cells.pdf', V1 + '/cells.json']); await p.waitForFunction(() => Object.keys(__app.doc.panels).length === 3); await p.waitForTimeout(300);
await ev(() => { __app.page = 0; __api.renderAll(); });

console.log('Nature (as shipped)');
ok(/Fig\. 1a/.test(await labels()) && /Supplementary Fig\. 1/.test(await labels()), 'Nature naming: Fig. 1a / Supplementary Fig. 1', await labels());
ok((await p.textContent('#panels .jbox')).includes('Nature') && (await p.textContent('#panels .jbox')).includes('unverified'), 'Nature is labelled UNVERIFIED in the sidebar');
const lab0 = await ev(() => { const o = __app.doc.pages[0].objects.find((q) => q.role === 'label'); return [o.str, o.size, o.bold]; });
ok(lab0[0] === 'a' && lab0[1] === 8 && lab0[2] === true, 'letters are lowercase bold 8 pt');

console.log('Check rules');
await p.click('#panels [data-act="jcheck"]'); await p.waitForTimeout(150);
let body = await p.textContent('#modal .body');
ok(/UNVERIFIED/.test(body), 'pre-flight repeats the unverified warning');
ok(/smaller than 5 pt/.test(body) || /No problems/.test(body) || /larger than/.test(body) || /ℹ/.test(body), 'pre-flight lists results', body.slice(0, 200));
await p.click('#modal .close');

console.log('Switch journal: Nature -> Cell (one undo step)');
const undoBefore = await ev(() => __app.undo.length); const w0 = await ev(() => __app.doc.pages[0].w);
await p.click('#panels [data-act="jswitch"]'); await p.waitForSelector('#sj-to');
ok(/unverified/.test(await p.textContent('#sj-warn')), 'dialog warns that Cell rules are unverified');
await p.selectOption('#sj-to', 'cell'); await p.click('#modal .close'); await p.waitForTimeout(500);
ok(Math.abs((await ev(() => __app.doc.pages[0].w)) - 174 * MM) < 1e-3, 'artboard is now 174 mm wide');
ok(/cells=Figure 1A/.test(await labels()) || /=Figure 1A/.test(await labels()), 'letters are capitals and naming is "Figure 1A"', await labels());
ok(/Figure S1/.test(await labels()), 'supplementary panel is "Figure S1" (single panel: no letter)', await labels());
const lab1 = await ev(() => { const o = __app.doc.pages[0].objects.find((q) => q.role === 'label'); return [o.str, o.size, o.bold]; });
ok(lab1[0] === lab1[0].toUpperCase() && lab1[1] === 8, 'letter objects restyled', JSON.stringify(lab1));
const wf = await ev(() => { const d = __app.doc; const q = Object.values(d.panels).find((x) => x.name === 'cells'); const f = __api.PN.panelFrame(d.pages[0], q.id); return f[3] - f[1]; });
ok(Math.abs(wf - 93.6 * 174 / 183) < 0.05, `artwork scaled with the page (plot height ${wf.toFixed(2)} pt)`);
ok((await ev(() => __app.undo.length)) === undoBefore + 1, 'whole conversion is ONE undo step');
ok(/Converted: Nature → Cell/.test(await p.textContent('#modal h3')) , 'conversion report shown'); await p.click('#modal .close');
ok((await ev(() => __app.changeLog[0]?.changes.length)) >= 2, 'renumbering is in the change log (for fixing manuscript text)');
await p.click('#btn-undo'); await p.waitForTimeout(300);
ok(Math.abs((await ev(() => __app.doc.pages[0].w)) - w0) < 1e-3 && /Fig\. 1a/.test(await labels()), 'undo returns to Nature: width, naming and letters');
await p.click('#btn-redo'); await p.waitForTimeout(300);

console.log('Per-figure width (Cell single column)');
await p.selectOption('#panels select[data-jwidth]', 'single'); await p.waitForTimeout(400);
ok(Math.abs((await ev(() => __app.doc.pages[0].w)) - 85 * MM) < 1e-3 && Math.abs((await ev(() => __app.doc.pages[1].w)) - 174 * MM) < 1e-3, 'only the current figure changed to 85 mm');

console.log('Edit rules');
await p.click('#panels [data-act="jedit"]'); await p.waitForSelector('#er-t1');
ok((await p.textContent('#modal .body')).includes('search summaries') && (await p.textContent('#modal .body')).includes('Where these values came from'), 'rule editor shows provenance of the numbers');
await p.fill('#er-t1', '9'); await p.fill('#er-ls', '12'); await p.check('#er-ok'); await p.click('#modal .close'); await p.waitForTimeout(400);
ok((await p.textContent('#panels .jbox')).includes('checked'), 'ticking "I checked these" flips the badge');
const l2 = await ev(() => __app.doc.pages[0].objects.find((q) => q.role === 'label').size); ok(l2 === 12, 'new letter size applied to existing letters');
await p.click('#panels [data-act="jcheck"]'); await p.waitForTimeout(150); body = await p.textContent('#modal .body');
ok(/smaller than 9 pt/.test(body) && !/UNVERIFIED/.test(body), 'edited minimum (9 pt) is enforced; no unverified warning once checked', body.slice(0, 300));
await p.click('#modal .pf-sel'); await p.waitForTimeout(200);
ok((await ev(() => __app.sel.size)) > 0, 'pre-flight "select" jumps to the offending objects');

console.log('Switch to Science and back to Nature');
await p.click('#panels [data-act="jswitch"]'); await p.waitForSelector('#sj-to'); await p.selectOption('#sj-to', 'science'); await p.click('#modal .close'); await p.waitForTimeout(400); await p.click('#modal .close');
const sl = await ev(() => { const o = __app.doc.pages[0].objects.find((q) => q.role === 'label'); return [o.size, o.str]; });
ok(sl[0] === 10 && /fig\. S1/.test(await labels()), 'Science: 10 pt letters, "fig. S1" naming', await labels());
await p.click('#panels [data-act="jswitch"]'); await p.waitForSelector('#sj-to'); await p.selectOption('#sj-to', 'nature'); await p.click('#modal .close'); await p.waitForTimeout(400); await p.click('#modal .close');
ok(/Fig\. 1a/.test(await labels()) && /Supplementary Fig\. 1/.test(await labels()), 'back to Nature naming and lowercase letters', await labels());

console.log('Export uses the journal\'s file naming, journal choice survives save/reopen');
await p.click('#panels [data-act="jswitch"]'); await p.waitForSelector('#sj-to'); await p.selectOption('#sj-to', 'cell'); await p.click('#modal .close'); await p.waitForTimeout(400); await p.click('#modal .close');
const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#panels [data-act="exportfigs"]')]); const zf = path.join(tmp, 'f.zip'); await dl.saveAs(zf);
const names = execSync(`unzip -Z1 "${zf}"`).toString().trim().split('\n'); ok(names.includes('Figure 1.ai') && names.includes('Figure S1.ai'), 'zip holds "Figure 1.ai" and "Figure S1.ai"', names.join('|'));
const [dl2] = await Promise.all([p.waitForEvent('download'), p.click('#btn-save')]); const af = path.join(tmp, 'j.ai'); await dl2.saveAs(af);
await p.setInputFiles('#file-input', af); await p.waitForTimeout(2500);
ok((await ev(() => __app.doc.settings.journal)) === 'cell' && /Figure 1A/.test(await labels()), 'saved file remembers the journal and its settings');
ok(errors.length === 0, 'no uncaught page errors', errors.join(' | '));
await browser.close(); await server.close(); console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
