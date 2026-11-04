// End-to-end tests: builds nothing itself — run `npm run build` first, then `npm test`.
// Needs `playwright` (npm i -D playwright && npx playwright install chromium). Optional: poppler's pdftocairo
// and ImageMagick `compare` for the independent pixel check of the saved file.
import { createRequire } from 'module';
import { preview } from 'vite';
import { execSync } from 'child_process';
import fs from 'fs'; import os from 'os'; import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');
let chromium;
try { ({ chromium } = createRequire(root + '/')('playwright')); }
catch { try { ({ chromium } = createRequire('/opt/node-tools/')('playwright')); } catch { console.error('playwright not found: npm i -D playwright'); process.exit(2); } }

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'panel-editor-'));
const fixture = path.join(here, 'fixtures', 'sample.ai');
let pass = 0, fail = 0;
const ok = (cond, msg, extra = '') => { if (cond) { pass++; console.log('  ✓', msg); } else { fail++; console.log('  ✗', msg, extra); } };
const has = (cmd) => { try { execSync(`which ${cmd}`, { stdio: 'ignore' }); return true; } catch { return false; } };

const server = await preview({ root, preview: { port: 4399, strictPort: true } });
const browser = await chromium.launch();
const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1400, height: 900 } });
const p = await ctx.newPage();
const errors = [];
await p.addInitScript(() => { delete window.showSaveFilePicker; }); // headless has no file-picker UI; exercise the download path
p.on('pageerror', (e) => errors.push(e.message));
await p.goto('http://localhost:4399/');

const objs = () => p.evaluate(() => __app.doc.pages[__app.page].objects.map((o) => ({ ...o, href: o.href ? 'data' : undefined })));
const open = async (file) => { await p.setInputFiles('#file-input', file); await p.waitForFunction(() => window.__app.hasDoc, null, { timeout: 60000 }); await p.waitForTimeout(300); };
const toClient = (x, y) => p.evaluate(([x, y]) => { const r = document.querySelector('#board').getBoundingClientRect(); return [r.left + x * __app.zoom, r.top + y * __app.zoom]; }, [x, y]);
const blur = () => p.evaluate(() => document.activeElement?.blur());
const drag = async (a, b, steps = 6) => { const [x0, y0] = await toClient(...a), [x1, y1] = await toClient(...b); await p.mouse.move(x0, y0); await p.mouse.down(); await p.mouse.move(x1, y1, { steps }); await p.mouse.up(); };
const click = async (x, y) => { const [cx, cy] = await toClient(x, y); await p.mouse.click(cx, cy); };
const save = async (name) => { const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#btn-save')]); const f = path.join(tmp, name); await dl.saveAs(f); return f; };

console.log('Import');
await open(fixture);
let o = await objs();
const by = (t) => o.filter((x) => x.type === t);
ok(by('path').length >= 4, `paths imported (${by('path').length})`);
ok(by('image').length === 1, 'image imported');
ok(by('text').length === 2, 'two text runs imported');
ok(by('path').some((x) => x.fill?.grad?.type === 'axial'), 'linear gradient fill recovered');
ok(by('path').some((x) => x.fill?.grad?.type === 'radial'), 'radial gradient fill recovered');
ok(by('path').some((x) => x.clips?.length), 'clip path recovered');
ok(by('path').some((x) => x.dash?.[0]?.length), 'dash pattern recovered');
ok(by('text').some((x) => x.str === 'Panel A: Test label' && x.bold), 'bold text string + weight recovered');
const blue = by('path').find((x) => x.fill === '#2255cc');
ok(!!blue, 'solid fill colour exact (#2255cc)');
const bb0 = blue && (await p.evaluate((id) => __api.objBBox(__app.doc.pages[0].objects.find((q) => q.id === id)), blue.id));
ok(bb0 && Math.abs(bb0[0] - 20) < 0.01 && Math.abs(bb0[1] - 40) < 0.01 && Math.abs(bb0[2] - 120) < 0.01 && Math.abs(bb0[3] - 100) < 0.01, 'rect geometry exact (y flipped to top-left origin)', JSON.stringify(bb0));

console.log('Selection / transform');
await click(70, 70); // middle of blue rect
ok(await p.evaluate((id) => __app.sel.has(id), blue.id), 'click selects object');
await drag([70, 70], [100, 110]);
let b1 = await p.evaluate((id) => __api.objBBox(__app.doc.pages[0].objects.find((q) => q.id === id)), blue.id);
ok(Math.abs(b1[0] - 50) < 0.5 && Math.abs(b1[1] - 80) < 0.5, 'drag moves by (+30,+40)', JSON.stringify(b1));
await p.keyboard.press('ArrowRight'); await p.waitForTimeout(400);
b1 = await p.evaluate((id) => __api.objBBox(__app.doc.pages[0].objects.find((q) => q.id === id)), blue.id);
ok(Math.abs(b1[0] - 51) < 0.5, 'arrow key nudges 1pt');
await drag([b1[2], b1[3]], [b1[2] + 50, b1[3] + 20]); // se handle
let b2 = await p.evaluate((id) => __api.objBBox(__app.doc.pages[0].objects.find((q) => q.id === id)), blue.id);
ok(Math.abs((b2[2] - b2[0]) - 150) < 1 && Math.abs((b2[3] - b2[1]) - 80) < 1, 'SE handle resizes', JSON.stringify(b2));
// numeric W field
await p.fill('#props [data-geo="w"]', '200'); await p.waitForTimeout(500);
b2 = await p.evaluate((id) => __api.objBBox(__app.doc.pages[0].objects.find((q) => q.id === id)), blue.id);
ok(Math.abs((b2[2] - b2[0]) - 200) < 0.5, 'numeric width field applies');
await blur();
// recolor
await p.evaluate(() => { const i = document.querySelector('#props [data-k="fill"]'); i.value = '#ff8800'; i.dispatchEvent(new Event('input', { bubbles: true })); });
ok((await p.evaluate((id) => __app.doc.pages[0].objects.find((q) => q.id === id).fill, blue.id)) === '#ff8800', 'fill colour change applies');
await blur();

console.log('Undo / redo');
const nUndo = await p.evaluate(() => __app.undo.length);
await p.waitForTimeout(700);
await p.click('#btn-undo'); // colour
ok((await p.evaluate((id) => __app.doc.pages[0].objects.find((q) => q.id === id).fill, blue.id)) === '#2255cc', 'undo reverts colour');
await p.click('#btn-redo');
ok((await p.evaluate((id) => __app.doc.pages[0].objects.find((q) => q.id === id).fill, blue.id)) === '#ff8800', 'redo re-applies colour');

console.log('Drawing tools');
await p.keyboard.press('Escape'); await blur();
const n0 = (await objs()).length;
await p.keyboard.press('m'); await drag([20, 270], [90, 295]);
await p.keyboard.press('l'); await drag([100, 270], [150, 295]);
await p.keyboard.press('\\'); await drag([160, 270], [210, 295]);
await p.keyboard.press('n'); await drag([220, 270], [290, 295], 12);
await p.keyboard.press('p'); await click(300, 270); await click(340, 295); await click(380, 270); await p.keyboard.press('Enter');
o = await objs();
ok(o.length === n0 + 5, `5 shapes created (rect, ellipse, line, pencil, pen) -> ${o.length - n0}`);
ok(o.slice(-5).every((x) => x.type === 'path' && x.segs.length >= 2), 'all are paths');
ok(o[n0 + 1].segs.filter((s) => s[0] === 'C').length === 4, 'ellipse = 4 bezier segments');
// text tool
await p.keyboard.press('t'); await click(30, 20); await p.waitForTimeout(100);
await p.keyboard.type('Hello α β µm'); await p.keyboard.down('Control'); await p.keyboard.press('Enter'); await p.keyboard.up('Control');
o = await objs();
ok(o[o.length - 1].type === 'text' && o[o.length - 1].str === 'Hello α β µm', 'text tool creates + edits text', o[o.length - 1].str);

console.log('Direct selection');
await p.keyboard.press('v'); await click(70, 70);
const pen = o[n0 + 4];
await p.evaluate((id) => { __app.sel = new Set([id]); __api.setTool('direct'); }, pen.id);
const anchor = await p.evaluate(() => { const e = document.querySelector('#overlay [data-pt]'); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
await p.mouse.move(...anchor); await p.mouse.down(); await p.mouse.move(anchor[0] + 20, anchor[1] - 20, { steps: 4 }); await p.mouse.up();
const penAfter = (await objs()).find((x) => x.id === pen.id);
ok(JSON.stringify(penAfter.segs) !== JSON.stringify(pen.segs), 'dragging an anchor edits the path');
await p.keyboard.press('v');

console.log('Layers / z-order');
await p.click('#layers [data-lact="add"]');
ok((await p.evaluate(() => __app.doc.layers.length)) === 2, 'layer added');
await p.keyboard.press('m'); await drag([300, 20], [380, 60]);
const lastId = (await objs()).at(-1);
ok(lastId.layer === (await p.evaluate(() => __app.doc.layers[1].id)), 'new object goes to active layer');
await p.click('#layers [data-lid] [data-lact="vis"]'); // top row = new layer
ok((await p.evaluate(() => __app.doc.layers[1].visible)) === false, 'layer can be hidden');
await p.click('#layers [data-lid] [data-lact="vis"]');

console.log('Save as .ai and reopen');
const aiFile = await save('edited.ai');
ok(fs.statSync(aiFile).size > 1000 && fs.readFileSync(aiFile).subarray(0, 5).toString() === '%PDF-', 'saved file is a valid PDF container (starts with %PDF-)');
const edited = await objs();
await open(aiFile);
ok(await p.evaluate(() => !!__app.doc.meta.restored), 'reopen restores full editable state');
const re = await objs();
ok(re.length === edited.length, `same object count after reopen (${re.length})`);
ok(re.some((x) => x.type === 'text' && x.str === 'Hello α β µm'), 'text still editable text after reopen');
ok((await p.evaluate(() => __app.doc.layers.length)) === 2, 'layers preserved');

console.log('Lean PDF export (no editor state => re-parsed from vectors)');
const [dl] = await Promise.all([p.waitForEvent('download'), p.selectOption('#export-menu', 'pdf')]);
const leanFile = path.join(tmp, 'lean.pdf'); await dl.saveAs(leanFile);
await open(leanFile);
ok(!(await p.evaluate(() => __app.doc.meta.restored)), 'lean PDF has no editor state');
const lean = await objs();
ok(lean.filter((x) => x.type === 'text').some((x) => x.str === 'Hello α β µm'), 'Unicode text survives real PDF round trip', lean.filter((x) => x.type === 'text').map((x) => x.str).join('|'));
ok(lean.some((x) => x.type === 'image'), 'image survives');
ok((await p.evaluate(() => __app.doc.layers.length)) === 2, 'layers survive as PDF optional-content groups', await p.evaluate(() => JSON.stringify(__app.doc.layers.map((l) => l.name))));

if (has('pdftocairo') && has('compare')) {
  console.log('Independent render check (poppler)');
  // Re-open pristine fixture, save untouched, compare pixels with poppler's render of the original.
  await open(fixture);
  const f2 = await save('untouched.ai');
  execSync(`pdftocairo -png -r 100 -singlefile "${fixture}" "${tmp}/a" && pdftocairo -png -r 100 -singlefile "${f2}" "${tmp}/b"`);
  let rmse = 1; try { execSync(`compare -metric RMSE "${tmp}/a.png" "${tmp}/b.png" null:`, { stdio: 'pipe' }); } catch (e) { rmse = parseFloat(/\(([\d.]+)\)/.exec(e.stderr.toString())?.[1] ?? '1'); }
  ok(rmse < 0.06, `saved file renders like the original in poppler (RMSE ${rmse})`);
}
ok(errors.length === 0, 'no uncaught page errors', errors.join(' | '));
await browser.close(); await server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
