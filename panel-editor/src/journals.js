// Journal rule sets, "switch journal" conversion, and a pre-flight check.
//
// !! PROVENANCE !!  The values below come from web-SEARCH SUMMARIES of each journal's author guidelines.
// The journals' own pages could not be fetched when this was written (hosts blocked), and the summaries
// disagree with each other (see `alt`). Nothing here is marked verified. Check every number against the
// journal's CURRENT instructions, correct it in the "Edit rules" dialog, then tick "I checked these".
import { scaleAbout, transformObject } from './model.js';
import { ensure, orderedPanels, panelFrame, placeLabel, applyLabels, computeLabels } from './panels.js';

export const MM = 72 / 25.4; // pt per mm

const NS = 'not found in the sources consulted';
export const JOURNALS = {
  lab: {
    name: 'Lab (custom)', verified: true,
    widthsMm: { single: null, onehalf: null, double: null }, maxHeightMm: null,
    letter: { case: 'lower', bold: true, size: 8 }, text: { minPt: null, maxPt: null }, minLinePt: null,
    names: { main: 'Fig. ', supp: 'Supplementary Fig. ', ed: 'Extended Data Fig. ' }, fileNames: { main: 'Figure ', supp: 'Supplementary Figure ', ed: 'Extended Data Figure ' },
    sources: {}, alt: [], notes: ["Your lab's own conventions. Edit freely."],
  },
  nature: {
    name: 'Nature', verified: false,
    widthsMm: { single: 89, onehalf: 120, double: 183 }, maxHeightMm: 170,
    letter: { case: 'lower', bold: true, size: 8 }, text: { minPt: 5, maxPt: 7 }, minLinePt: null,
    names: { main: 'Fig. ', supp: 'Supplementary Fig. ', ed: 'Extended Data Fig. ' }, fileNames: { main: 'Figure ', supp: 'Supplementary Figure ', ed: 'Extended Data Figure ' },
    sources: {
      widths: 'https://www.nature.com/nature/for-authors/final-submission (via search summary: "89 mm wide (single column) and 183 mm wide (double column) … column-and-a-half 120–136 mm")',
      maxHeight: 'search summary of a Nature page: "maximum height … 170 mm" (that page used 90/180 mm widths)',
      letter: 'https://www.nature.com/documents/nature-final-artwork.pdf (via search summary: "8-pt bold, upright, lowercase a, b, c")',
      text: 'search summary: "maximum text size … 7-pt and the minimum is 5-pt"; Arial or Helvetica recommended',
      minLine: NS, names: 'NOT from the sources: Supplementary / Extended Data naming is convention, please check',
    },
    alt: ['research-figure-guide.nature.com summary: 88 mm and 180 mm (original research / reviews)', 'other Nature pages: 90 mm and 180 mm; page depth 170 mm', '1.5 column given as a range 120–136 mm (120 used here)'],
    notes: ['Three Nature pages gave three different single-column widths. Check the one that applies to your article type.'],
  },
  cell: {
    name: 'Cell', verified: false,
    widthsMm: { single: 85, onehalf: 114, double: 174 }, maxHeightMm: null,
    letter: { case: 'upper', bold: true, size: 8 }, text: { minPt: 6, maxPt: 8 }, minLinePt: null,
    names: { main: 'Figure ', supp: 'Figure S', ed: 'Figure S' }, fileNames: { main: 'Figure ', supp: 'Figure S', ed: 'Figure S' },
    sources: {
      widths: 'https://www.cell.com/information-for-authors/figure-guidelines (via search summary, two-column formats: "8.5 cm (1 column), 11.4 cm (1.5 columns), 17.4 cm (full width)")',
      maxHeight: NS,
      letter: 'search summary: "different panels should be labeled with capital letters"; size and weight NOT stated (8 pt bold is a placeholder)',
      text: 'search summary: "Arial … text about 6–8 pt at the desired print size"; embed fonts',
      minLine: NS, names: 'NOT from the sources: "Figure S1" supplementary naming is convention, please check',
    },
    alt: ['three-column formats (previews, commentaries): 55 / 114 / 174 mm', 'Cell Reports Physical Science: 112 / 172 mm; iScience: 134.6 / 172 mm; Chem uses Avenir'],
    notes: ['Widths depend on the article format. These are the two-column-format widths from the main Cell Press page.'],
  },
  science: {
    name: 'Science', verified: false,
    widthsMm: { single: 57, onehalf: 121, double: 184 }, maxHeightMm: null,
    letter: { case: 'upper', bold: true, size: 10 }, text: { minPt: 5, maxPt: null }, minLinePt: null,
    names: { main: 'Fig. ', supp: 'fig. S', ed: 'fig. S' }, fileNames: { main: 'Figure ', supp: 'Figure S', ed: 'Figure S' },
    sources: {
      widths: 'https://www.science.org/content/page/instructions-preparing-initial-manuscript (via search summary: "5.7 cm (1 column), 12.1 cm (2 columns), 18.4 cm (3 columns)")',
      maxHeight: NS,
      letter: 'same page (via search summary): "capitalized, 10 pt, and bold, upper left corner of each panel"',
      text: '2022 author figure guide (via search summary): "minimum font size is 5 pt", Helvetica preferred; no maximum found',
      minLine: NS, names: 'NOT from the sources: "fig. S1" supplementary naming is convention, please check',
    },
    alt: ['2022 figure guide: 5.5 cm / 12 cm / 18.3 cm (1 / 2 / 3 columns), 10 pt bold labels', 'Science Advances 2026 guide (a different journal): 9 cm / 18.4 cm widths, 9 pt bold labels, 6 pt minimum text', "Science's 1 / 2 / 3 columns are mapped here to single / 1.5 / double"],
    notes: ['This is the flagship Science journal, not Science Advances.'],
  },
};
export const JOURNAL_KEYS = Object.keys(JOURNALS);

export const rulesFor = (doc, key = doc.settings?.journal || 'lab') => {
  const base = structuredClone(JOURNALS[key] || JOURNALS.lab); const ov = doc.settings?.journalOverrides?.[key] || {};
  const merge = (a, b) => { for (const k of Object.keys(b || {})) { if (b[k] && typeof b[k] === 'object' && !Array.isArray(b[k])) { a[k] ??= {}; merge(a[k], b[k]); } else a[k] = b[k]; } return a; };
  const r = merge(base, ov); if (doc.settings?.journalChecked?.[key]) r.verified = true; return r;
};
export const COLS = ['single', 'onehalf', 'double'];
export const COL_LABEL = { single: 'Single column', onehalf: '1.5 column', double: 'Double / full width' };
export const widthPt = (rules, col) => (rules.widthsMm[col] ? rules.widthsMm[col] * MM : null);

// which column a page currently is (within 3 mm of one of the journal's widths), or null
export function currentColumn(rules, w) {
  let best = null;
  for (const c of COLS) { const wp = widthPt(rules, c); if (wp && (!best || Math.abs(wp - w) < Math.abs(best.d))) best = { c, d: wp - w }; }
  return best && Math.abs(best.d) <= 3 * MM ? best.c : null;
}
function nearestColumn(rules, w) { let b = null; for (const c of COLS) { const wp = widthPt(rules, c); if (wp && (!b || Math.abs(wp - w) < b.d)) b = { c, d: Math.abs(wp - w) }; } return b?.c || null; }

// Apply a journal's rules to the document settings (lettering, naming, grid). Does not touch artwork.
export function applyJournalSettings(doc, key) {
  ensure(doc); const r = rulesFor(doc, key);
  doc.settings.journal = key;
  doc.settings.letterCase = r.letter.case; doc.settings.labelSize = r.letter.size; doc.settings.labelBold = r.letter.bold;
  doc.settings.names = { ...r.names }; doc.settings.fileNames = { ...r.fileNames };
  const off = [0, -(r.letter.size + 2)];
  if (key !== 'lab') { // the artboard IS the journal's figure width, so use a tight grid across it
    const wMm = doc.pages[0] ? doc.pages[0].w / MM : 183; const cols = Math.max(2, Math.round((wMm * MM) / 43.2));
    doc.settings.layout = { margin: 6, cols, gutter: 6, rowGap: 6, snap: 6, labelOffset: off, alignTop: true, colSnap: true, widthPt: null };
    delete doc.settings.layoutName;
  } else doc.settings.layout = { ...(doc.settings.layout || {}), labelOffset: off };
  return r;
}

const eff = (o) => o.size * Math.hypot(o.m[0], o.m[1]);

// Convert the artwork to another journal. Returns a report. The caller wraps this in one undoable step.
//   column: 'auto' (keep each figure's current column choice) | 'single' | 'onehalf' | 'double'
export function convertJournal(doc, key, { column = 'auto', scaleContent = true, fixText = true } = {}) {
  ensure(doc); const oldKey = doc.settings.journal || 'lab'; const oldR = rulesFor(doc, oldKey); const newR = rulesFor(doc, key);
  const report = { from: oldR.name, to: newR.name, pages: [], changes: [], warnings: [] };
  const before = Object.fromEntries(Object.entries(computeLabels(doc)).map(([k, v]) => [k, v.full]));
  applyJournalSettings(doc, key);
  doc.pages.forEach((page, pi) => {
    const colKey = column === 'auto' ? (currentColumn(oldR, page.w) || nearestColumn(oldR, page.w) || 'double') : column;
    const target = widthPt(newR, colKey) || widthPt(newR, nearestColumn(newR, page.w));
    if (!target) { report.warnings.push(`Figure ${pi + 1}: ${newR.name} has no figure widths set, so the artboard width was kept.`); return; }
    const k = target / page.w; const wasMm = page.w / MM;
    if (Math.abs(k - 1) > 1e-4) {
      if (scaleContent) { for (const o of page.objects) transformObject(o, scaleAbout(k, k, 0, 0)); page.h *= k; }
      page.w = target; report.pages.push({ page: pi, fromMm: +wasMm.toFixed(1), toMm: +(target / MM).toFixed(1), column: colKey, scaled: scaleContent, k: +k.toFixed(3) });
    }
    if (newR.maxHeightMm && page.h > newR.maxHeightMm * MM + 0.5) report.warnings.push(`Figure ${pi + 1} is ${(page.h / MM).toFixed(0)} mm tall; ${newR.name} allows ${newR.maxHeightMm} mm. Reduce it manually.`);
  });
  // lettering: restyle the letters (size, weight, upright), then relabel (case and prefixes follow the new settings)
  for (const page of doc.pages) for (const o of page.objects) if (o.type === 'text' && o.role === 'label') { o.size = newR.letter.size; o.bold = newR.letter.bold; o.m = [1, 0, 0, -1, o.m[4], o.m[5]]; delete o.origW; }
  let clamped = 0;
  if (fixText) for (const page of doc.pages) for (const o of page.objects) {
    if (o.type !== 'text' || o.role === 'label') continue;
    const e = eff(o); let t = e;
    if (newR.text.minPt && e < newR.text.minPt - 0.01) t = newR.text.minPt; else if (newR.text.maxPt && e > newR.text.maxPt + 0.01) t = newR.text.maxPt;
    if (t !== e) { o.size *= t / e; delete o.origW; clamped++; }
  }
  if (clamped) report.changes.push(`${clamped} text object(s) resized into ${newR.text.minPt ?? '–'}–${newR.text.maxPt ?? '–'} pt (check for crowding)`);
  let thin = 0;
  if (newR.minLinePt) for (const page of doc.pages) for (const o of page.objects) if (o.type === 'path' && o.stroke && o.role !== 'frame' && o.sw < newR.minLinePt - 0.01) { o.sw = newR.minLinePt; thin++; }
  if (thin) report.changes.push(`${thin} line(s) thickened to ${newR.minLinePt} pt`);
  const r = applyLabels(doc);
  doc.pages.forEach((page, pi) => { for (const id of orderedPanels(doc, pi)) placeLabel(doc, pi, id, doc.settings.layout.labelOffset); });
  report.relabelled = r.changes; report.labelsBefore = before;
  return report;
}

// ---- pre-flight ---------------------------------------------------------------------------------
export function preflight(doc) {
  ensure(doc); const key = doc.settings.journal || 'lab'; const R = rulesFor(doc, key); const out = [];
  const add = (level, page, msg, ids = []) => out.push({ level, page, msg, ids });
  if (key !== 'lab' && !R.verified) add('info', null, `${R.name} rules are UNVERIFIED (see their sources under "Edit rules"). The results below are only as good as those numbers.`);
  doc.pages.forEach((page, pi) => {
    const wmm = page.w / MM, hmm = page.h / MM; const widths = COLS.map((c) => R.widthsMm[c]).filter(Boolean);
    if (widths.length && !widths.some((w) => Math.abs(w - wmm) <= 1)) add('warn', pi, `Width ${wmm.toFixed(1)} mm is not one of ${R.name}'s widths (${widths.join(' / ')} mm).`);
    if (R.maxHeightMm && hmm > R.maxHeightMm + 0.5) add('warn', pi, `Height ${hmm.toFixed(0)} mm exceeds ${R.maxHeightMm} mm.`);
    const small = [], big = [], thin = [], badLetter = [];
    for (const o of page.objects) {
      if (o.type === 'text' && o.role === 'label') {
        const L = R.letter; const bad = Math.abs(eff(o) - L.size) > 0.3 || !!o.bold !== !!L.bold || (o.str && (L.case === 'upper') !== (o.str === o.str.toUpperCase()));
        if (bad) badLetter.push(o.id);
      } else if (o.type === 'text' && (o.str || '').trim()) {
        const e = eff(o);
        if (R.text.minPt && e < R.text.minPt - 0.05) small.push(o.id);
        if (R.text.maxPt && e > R.text.maxPt + 0.05) big.push(o.id);
      } else if (o.type === 'path' && o.stroke && o.role !== 'frame' && R.minLinePt && o.sw < R.minLinePt - 0.01) thin.push(o.id);
    }
    if (small.length) add('error', pi, `${small.length} text object(s) smaller than ${R.text.minPt} pt.`, small);
    if (big.length) add('warn', pi, `${big.length} text object(s) larger than ${R.text.maxPt} pt.`, big);
    if (thin.length) add('error', pi, `${thin.length} line(s) thinner than ${R.minLinePt} pt.`, thin);
    if (badLetter.length) add('warn', pi, `${badLetter.length} panel letter(s) not in ${R.name} style (${R.letter.case === 'upper' ? 'capital' : 'lowercase'}${R.letter.bold ? ', bold' : ''}, ${R.letter.size} pt).`, badLetter);
    const fr = orderedPanels(doc, pi).map((id) => [id, panelFrame(page, id)]).filter(([, f]) => f);
    for (let i = 0; i < fr.length; i++) for (let j = i + 1; j < fr.length; j++) { const a = fr[i][1], b = fr[j][1]; if (a[0] < b[2] - 0.5 && a[2] > b[0] + 0.5 && a[1] < b[3] - 0.5 && a[3] > b[1] + 0.5) add('warn', pi, `Panels “${doc.panels[fr[i][0]].name}” and “${doc.panels[fr[j][0]].name}” overlap.`); }
    for (const [id, f] of fr) if (f[0] < -0.5 || f[1] < -0.5 || f[2] > page.w + 0.5 || f[3] > page.h + 0.5) add('error', pi, `Panel “${doc.panels[id].name}” extends outside the artboard.`);
  });
  if (!out.some((x) => x.level !== 'info')) add('ok', null, `No problems found against ${R.name}'s rules as entered.`);
  return out;
}

// Set ONE figure to a journal column width (content scaled uniformly by default). Returns the scale factor, or null.
export function setFigureWidth(doc, pi, col, { scaleContent = true } = {}) {
  ensure(doc); const target = widthPt(rulesFor(doc), col); const page = doc.pages[pi]; if (!target || !page) return null;
  const k = target / page.w; if (scaleContent) { for (const o of page.objects) transformObject(o, scaleAbout(k, k, 0, 0)); page.h *= k; }
  page.w = target;
  for (const o of page.objects) if (o.type === 'text' && o.role === 'label') o.m = [1, 0, 0, -1, o.m[4], o.m[5]]; // letters keep their point size
  for (const id of orderedPanels(doc, pi)) placeLabel(doc, pi, id, doc.settings.layout?.labelOffset || [0, -10]);
  return k;
}
