// Regenerates docs/img/*.png by driving the real UI through the tutorial. Run after `npm run build`:
//   node docs/make-screenshots.mjs
import { createRequire } from 'module';
import { preview } from 'vite';
import path from 'path'; import { fileURLToPath } from 'url'; import fs from 'fs';
const here = path.dirname(fileURLToPath(import.meta.url)); const root = path.join(here, '..');
let chromium; try { ({ chromium } = createRequire(root + '/')('playwright')); } catch { ({ chromium } = createRequire('/opt/node-tools/')('playwright')); }
const F = path.join(root, 'test/fixtures'), OUT = path.join(here, 'img'); fs.mkdirSync(OUT, { recursive: true });
const server = await preview({ root, preview: { port: 4395, strictPort: true } });
const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1400, height: 820 } })).newPage();
await p.addInitScript(() => { delete window.showSaveFilePicker; });
await p.goto('http://localhost:4395/');
const shot = (n) => p.screenshot({ path: path.join(OUT, n + '.png') });
const ev = (f, a) => p.evaluate(f, a);
const pt = (x, y) => ev(([x, y]) => { const r = document.querySelector('#board').getBoundingClientRect(); return [r.left + x * __app.zoom, r.top + y * __app.zoom]; }, [x, y]);
const frame = (id) => ev((id) => { const d = __app.doc; return __api.PN.panelFrame(d.pages[__api.PN.panelPage(d, id)], id); }, id);

await shot('01-start');
await p.click('#w-new'); await p.waitForSelector('#np-size'); await p.fill('#np-name', 'Paper figures'); await shot('02-new-project');
await p.selectOption('#np-size', 'US Letter, landscape'); await p.click('#modal .close'); await p.waitForTimeout(300);
await p.setInputFiles('#plot-input', [F + '/plots_v1/cells.pdf', F + '/plots_v1/cells.json', F + '/plots_v1/ephys.pdf', F + '/plots_v1/ephys.json']);
await p.waitForFunction(() => Object.keys(__app.doc.panels).length === 2); await p.waitForTimeout(500);
await ev(() => { const q = Object.values(__app.doc.panels).find((x) => x.name === 'cells'); __api.select(__app.doc.pages[0].objects.filter((o) => o.panel === q.id).map((o) => o.id)); });
await shot('03-placed');
await p.setInputFiles('#img-input', path.join(root, 'examples/tutorial/micrograph.png')); await p.waitForTimeout(700);
await p.keyboard.press('Escape');
await p.check('#chk-grid'); await p.waitForTimeout(200); await shot('04-image-and-grid'); await p.uncheck('#chk-grid');
// legend with a reference
await p.keyboard.press('t'); const [tx, ty] = await pt(40, 250); await p.mouse.click(tx, ty); await p.waitForTimeout(150);
await p.keyboard.type('Cell counts are shown in '); await p.keyboard.down('Control'); await p.keyboard.press('Enter'); await p.keyboard.up('Control');
await ev(() => document.activeElement?.blur()); await p.keyboard.press('v');
const legendId = await ev(() => __app.doc.pages[0].objects.filter((o) => o.type === 'text' && !o.panel).at(-1).id);
await ev((id) => __api.select([id]), legendId); await p.waitForTimeout(100);
const cellsId = await ev(() => Object.values(__app.doc.panels).find((x) => x.name === 'cells').id);
await p.selectOption('#props select[data-insref]', cellsId); await ev(() => document.activeElement?.blur()); await p.waitForTimeout(800);
await shot('05-reference');
// reorder: drag cells to the far right of its row
const fc = await frame(cellsId); const micId = await ev(() => Object.values(__app.doc.panels).find((x) => x.name === 'micrograph').id); const fm = await frame(micId);
const [x0, y0] = await pt((fc[0] + fc[2]) / 2, (fc[1] + fc[3]) / 2); const [x1, y1] = await pt(fm[2] + 40 + (fc[2] - fc[0]) / 2, (fc[1] + fc[3]) / 2); // drop it to the right of the micrograph
await p.mouse.move(x0, y0); await p.mouse.down(); await p.mouse.move(x1, y1, { steps: 8 }); await p.mouse.up(); await p.waitForTimeout(300); await shot('06-reordered');
await p.click('#panels [data-act="changelog"]'); await p.waitForTimeout(200); await shot('07-change-log'); await p.click('#modal .close');
// supplement
await p.click('#panels [data-act="addfig-supp"]'); await p.waitForTimeout(300);
await ev(() => { __app.page = 0; __api.renderAll(); }); await p.selectOption(`#panels [data-pid="${cellsId}"] select[data-pmove]`, '1'); await p.waitForTimeout(500);
await shot('08-moved-to-supplement');
await p.click('#btn-undo'); await p.waitForTimeout(300);
await ev(() => { __app.page = 0; __api.renderAll(); });
// data changed
await ev((id) => { __app.updateTarget = id; }, cellsId);
await p.setInputFiles('#update-input', [F + '/plots_v2/cells.pdf', F + '/plots_v2/cells.json']); await p.waitForTimeout(900);
await ev((id) => __api.select(__app.doc.pages[0].objects.filter((o) => o.panel === id).map((o) => o.id)), cellsId); await p.waitForTimeout(200);
await shot('09-data-changed');
// journals: start from a Nature-width figure, then switch to Cell
await ev(() => { const d = __app.doc; __api.JN.setFigureWidth(d, 0, 'double'); __api.JN.applyJournalSettings(d, 'nature'); __api.renderAll(); __api.fit(); });
await p.waitForTimeout(300);
await p.click('#panels [data-act="jswitch"]'); await p.waitForSelector('#sj-to'); await p.selectOption('#sj-to', 'cell'); await shot('10-switch-journal');
await p.click('#modal .close'); await p.waitForTimeout(500); await shot('11-converted'); await p.click('#modal .close');
await p.click('#panels [data-act="jedit"]'); await p.waitForSelector('#er-t1'); await shot('12-edit-rules'); await p.click('#modal .cancel');
await p.click('#panels [data-act="jcheck"]'); await p.waitForTimeout(200); await shot('13-check-rules');
await b.close(); await server.close(); console.log('screenshots written to', OUT);
