// Write the document back out as a PDF. Saved with the .ai extension this is the
// "PDF-compatible" half of an Illustrator file: Illustrator, Inkscape, Affinity, Acrobat, etc. open it.
import { PDFDocument, PDFName, PDFHexString, PDFString } from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import pako from 'pako';
import { mul, FLIP, translate } from './model.js';
import { STATE_KEY } from './import.js';

const f = (n) => (Math.abs(n) < 1e-9 ? 0 : +Number(n).toFixed(4));
const FONT_FILES = {};
for (const fam of ['Sans', 'Serif', 'Mono']) for (const sty of ['Regular', 'Bold', 'Italic', 'BoldItalic']) {
  FONT_FILES[`${fam}-${sty}`] = `${import.meta.env.BASE_URL}fonts/Liberation${fam}-${sty}.ttf`;
}

export function pickFont(o) {
  const n = `${o.family || ''} ${o.fontName || ''}`.toLowerCase();
  let fam = 'Sans';
  if (/courier|mono|consolas|menlo|typewriter/.test(n) || o.generic === 'monospace') fam = 'Mono';
  else if (!/sans|arial|helvet/.test(n) && (/times|serif|georgia|minion|garamond|palatino|book|cambria|roman/.test(n) || o.generic === 'serif')) fam = 'Serif';
  const sty = o.bold ? (o.italic ? 'BoldItalic' : 'Bold') : (o.italic ? 'Italic' : 'Regular');
  return `${fam}-${sty}`;
}

const dataUrlBytes = (u) => {
  const [head, b64] = u.split(',');
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return { bytes: out, mime: /image\/(\w+)/.exec(head)?.[1] || 'png' };
};

function segOps(segs) {
  let s = '';
  for (const g of segs) {
    if (g[0] === 'M') s += `${f(g[1])} ${f(g[2])} m\n`;
    else if (g[0] === 'L') s += `${f(g[1])} ${f(g[2])} l\n`;
    else if (g[0] === 'C') s += `${g.slice(1).map(f).join(' ')} c\n`;
    else s += 'h\n';
  }
  return s;
}
const rgb = (hex) => [1, 3, 5].map((i) => f(parseInt(hex.slice(i, i + 2), 16) / 255)).join(' ');

export async function docToPdf(doc, { onProgress = () => {}, embedState = true } = {}) {
  const pdf = await PDFDocument.create();
  pdf.registerFontkit(fontkit);
  const ctx = pdf.context;
  pdf.setProducer('Panel Editor (open source)');
  pdf.setCreator('Panel Editor');
  pdf.setTitle(doc.name || 'Untitled');

  // layers -> optional content groups
  const ocgRef = {};
  for (const L of doc.layers) ocgRef[L.id] = ctx.register(ctx.obj({ Type: 'OCG', Name: PDFHexString.fromText(L.name) }));
  const layerIds = doc.layers.map((L) => L.id);
  pdf.catalog.set(PDFName.of('OCProperties'), ctx.obj({
    OCGs: layerIds.map((id) => ocgRef[id]),
    D: {
      Order: layerIds.map((id) => ocgRef[id]),
      ON: doc.layers.filter((L) => L.visible).map((L) => ocgRef[L.id]),
      OFF: doc.layers.filter((L) => !L.visible).map((L) => ocgRef[L.id]),
    },
  }));

  // Fonts: lazily embedded, full Unicode (Greek letters, ±, °, µ …) via Liberation fonts.
  const fontCache = new Map();
  const getFont = async (key) => {
    if (!fontCache.has(key)) {
      const bytes = new Uint8Array(await (await fetch(FONT_FILES[key])).arrayBuffer());
      fontCache.set(key, await pdf.embedFont(bytes, { subset: true }));
    }
    return fontCache.get(key);
  };

  const imgCache = new Map();
  const embedImage = async (href) => {
    if (imgCache.has(href)) return imgCache.get(href);
    const { bytes, mime } = dataUrlBytes(href);
    const im = /jpe?g/.test(mime) ? await pdf.embedJpg(bytes) : await pdf.embedPng(bytes);
    imgCache.set(href, im);
    return im;
  };

  for (let pi = 0; pi < doc.pages.length; pi++) {
    onProgress(`Writing artboard ${pi + 1} of ${doc.pages.length}…`);
    const P = doc.pages[pi];
    const page = pdf.addPage([P.w, P.h]);
    const res = { Font: {}, XObject: {}, ExtGState: {}, Pattern: {}, Properties: {} };
    let n = 0, gsCount = 0, patCount = 0;
    const gsCache = new Map();
    let c = `1 0 0 -1 0 ${f(P.h)} cm\n`; // draw in y-down document coordinates

    const gradPattern = (g) => {
      const stops = [...g.stops].sort((a, b) => a[0] - b[0]);
      const cols = stops.map((s) => rgb(s[1]).split(' ').map(Number));
      let fn;
      if (stops.length === 1) fn = ctx.obj({ FunctionType: 2, Domain: [0, 1], C0: cols[0], C1: cols[0], N: 1 });
      else {
        const funcs = [], bounds = [], encode = [];
        for (let i = 0; i < stops.length - 1; i++) {
          funcs.push(ctx.obj({ FunctionType: 2, Domain: [0, 1], C0: cols[i], C1: cols[i + 1], N: 1 }));
          encode.push(0, 1);
          if (i > 0) bounds.push(Math.min(Math.max(stops[i][0], bounds.length ? bounds[bounds.length - 1] + 1e-6 : 0), 1));
        }
        fn = ctx.obj({ FunctionType: 3, Domain: [0, 1], Functions: funcs, Bounds: bounds, Encode: encode });
      }
      const axial = g.type === 'axial';
      const shading = ctx.obj({
        ShadingType: axial ? 2 : 3, ColorSpace: 'DeviceRGB',
        Coords: axial ? [...g.p0, ...g.p1] : [g.p0[0], g.p0[1], g.r0, g.p1[0], g.p1[1], g.r1],
        Function: fn, Extend: [true, true],
      });
      // pattern space -> default PDF page space
      const M = mul([1, 0, 0, -1, 0, P.h], g.m);
      return ctx.register(ctx.obj({ PatternType: 2, Shading: shading, Matrix: M.map(f) }));
    };
    const setPaint = (paint, stroke) => {
      if (typeof paint === 'string') return `${rgb(paint)} ${stroke ? 'RG' : 'rg'}\n`;
      const name = `P${++patCount}`;
      res.Pattern[name] = gradPattern(paint.grad);
      return `/Pattern ${stroke ? 'CS' : 'cs'} /${name} ${stroke ? 'SCN' : 'scn'}\n`;
    };
    const gs = (alpha) => {
      const key = f(alpha);
      if (!gsCache.has(key)) { const nm = `G${++gsCount}`; res.ExtGState[nm] = ctx.obj({ Type: 'ExtGState', ca: alpha, CA: alpha }); gsCache.set(key, nm); }
      return `/${gsCache.get(key)} gs\n`;
    };

    // Layer stacking: layers bottom-to-top, objects in z-order within each layer.
    const order = [];
    for (const L of doc.layers) for (const o of P.objects) if ((o.layer || layerIds[0]) === L.id) order.push([L, o]);
    for (const [L, o] of order) {
      const propName = `L${layerIds.indexOf(L.id)}`;
      res.Properties[propName] = ocgRef[L.id];
      c += `/OC /${propName} BDC\nq\n`;
      for (const clip of o.clips || []) c += segOps(clip.segs) + (clip.rule === 'evenodd' ? 'W* n\n' : 'W n\n');
      if (o.opacity != null && o.opacity < 1) c += gs(o.opacity);
      if (o.type === 'path') {
        const doF = !!o.fill, doS = !!o.stroke;
        if (doF || doS) {
          if (doF) c += setPaint(o.fill, false);
          if (doS) {
            c += setPaint(o.stroke, true) + `${f(o.sw)} w ${o.cap} J ${o.join} j ${f(o.miter)} M\n`;
            c += o.dash && o.dash[0]?.length ? `[${o.dash[0].map(f).join(' ')}] ${f(o.dash[1])} d\n` : '[] 0 d\n';
          }
          c += segOps(o.segs);
          const ev = o.rule === 'evenodd' ? '*' : '';
          c += doF && doS ? `B${ev}\n` : doF ? `f${ev}\n` : 'S\n';
        }
      } else if (o.type === 'image') {
        const im = await embedImage(o.href);
        const nm = `Im${++n}`;
        res.XObject[nm] = im.ref;
        c += `${o.m.map(f).join(' ')} cm\n/${nm} Do\n`;
      } else if (o.type === 'text') {
        const font = await getFont(pickFont(o));
        const fnm = `F${Object.keys(res.Font).length + 1}`;
        const existing = Object.entries(res.Font).find(([, r]) => r === font.ref);
        const useName = existing ? existing[0] : fnm;
        res.Font[useName] = font.ref;
        const set = new Set(font.getCharacterSet());
        const lines = o.str.split('\n');
        const mode = o.fill && o.stroke ? 2 : o.stroke ? 1 : 0;
        if (o.fill) c += setPaint(o.fill, false);
        if (o.stroke) c += setPaint(o.stroke, true) + `${f(o.sw)} w\n`;
        lines.forEach((ln, i) => {
          const txt = [...ln].map((ch) => (set.has(ch.codePointAt(0)) ? ch : '?')).join('');
          if (!txt) return;
          let tz = 100;
          if (o.origW && lines.length === 1 && !o.cs) {
            const nat = font.widthOfTextAtSize(txt, o.size);
            if (nat > 0) tz = Math.min(Math.max((o.origW / nat) * 100, 30), 300);
          }
          const m = mul(o.m, translate(0, -i * o.size * (o.leading || 1.2)));
          c += `BT\n/${useName} ${f(o.size)} Tf\n${mode} Tr\n${f(tz)} Tz\n${f(o.cs || 0)} Tc\n${m.map(f).join(' ')} Tm\n${font.encodeText(txt).toString()} Tj\nET\n`;
        });
      }
      c += 'Q\nEMC\n';
    }
    const stream = ctx.flateStream(c);
    page.node.set(PDFName.of('Contents'), ctx.register(stream));
    page.node.set(PDFName.of('Resources'), ctx.obj(res));
  }

  // Our own editable state, so re-opening in Panel Editor is lossless (text stays text, layers stay layers).
  if (!embedState) return pdf.save();
  const state = { ...doc, meta: { savedBy: 'panel-editor', version: 1 } };
  const packed = pako.deflate(new TextEncoder().encode(JSON.stringify(state, (k, v) => (typeof v === 'number' && Number.isFinite(v) ? Number(v.toPrecision(7)) : v))));
  const sref = ctx.register(ctx.stream(packed, { Filter: 'FlateDecode' }));
  pdf.catalog.set(PDFName.of(STATE_KEY), sref);
  return pdf.save();
}
