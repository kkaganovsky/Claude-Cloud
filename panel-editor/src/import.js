// Open a PDF-compatible .ai (or plain .pdf / .svg) and convert it to the editable document model.
// An Illustrator .ai saved with "Create PDF Compatible File" is a PDF with extra private data; we read
// the PDF half. Everything is parsed from pdf.js's operator list into editable vector objects.
import * as pdfjs from 'pdfjs-dist/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';
import { PDFDocument, PDFName, PDFRawStream, decodePDFRawStream } from 'pdf-lib';
import { mul, scaleOf, makePath, newId, newDoc, FLIP } from './model.js';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
const { OPS } = pdfjs;

export const STATE_KEY = 'PanelEditorState';

const hex2 = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
const rgbHex = (r, g, b) => '#' + hex2(r) + hex2(g) + hex2(b);

function sniff(bytes) {
  const head = new TextDecoder('latin1').decode(bytes.subarray(0, 1024));
  if (head.includes('%PDF-')) return 'pdf';
  if (head.startsWith('%!PS-Adobe')) return 'ps';
  if (/<svg[\s>]/i.test(head) || head.includes('<?xml')) return 'svg';
  return 'unknown';
}

export async function openFile(file, onProgress = () => {}) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const kind = sniff(bytes);
  const name = file.name.replace(/\.[^.]+$/, '');
  if (kind === 'pdf') return importPdf(bytes, name, onProgress);
  if (kind === 'svg') throw new Error('SVG import is not supported yet. Open a PDF-compatible .ai or a .pdf instead.');
  if (kind === 'ps') {
    throw new Error('This is an old PostScript-based .ai file (Illustrator 8 or earlier). Ask the sender to re-save it from a current Illustrator with "Create PDF Compatible File" ticked, or export a PDF/SVG.');
  }
  throw new Error('Unrecognised file. Expected a PDF-compatible .ai, a .pdf, or an .svg.');
}

async function loadSavedState(bytes) {
  try {
    const doc = await PDFDocument.load(bytes, { ignoreEncryption: true, updateMetadata: false, throwOnInvalidObject: false });
    const obj = doc.catalog.get(PDFName.of(STATE_KEY));
    const stream = obj && doc.context.lookup(obj);
    if (!(stream instanceof PDFRawStream)) return null;
    const json = new TextDecoder().decode(decodePDFRawStream(stream).decode());
    const state = JSON.parse(json);
    return state && state.pages && state.layers ? state : null;
  } catch { return null; }
}

export async function importPdf(bytes, name, onProgress) {
  const saved = await loadSavedState(bytes);
  if (saved) { saved.name = name; saved.meta = { ...(saved.meta || {}), restored: true }; return saved; }

  const task = pdfjs.getDocument({
    data: bytes.slice(), disableFontFace: true, fontExtraProperties: true, isEvalSupported: false,
    useSystemFonts: false, verbosity: 0,
  });
  const pdf = await task.promise;
  const ocConfig = await pdf.getOptionalContentConfig().catch(() => null);
  const doc = newDoc(612, 792, name);
  doc.layers = [];
  doc.pages = [];
  const layerByOc = new Map();
  const warnings = new Set();
  const getLayer = (ocId) => {
    if (!ocId) ocId = '__none';
    let L = layerByOc.get(ocId);
    if (!L) {
      let nm = ocId === '__none' ? 'Layer 1' : (ocConfig?.getGroup?.(ocId)?.name || `Layer ${doc.layers.length + 1}`);
      L = { id: 'L' + (doc.layers.length + 1), name: nm, visible: ocId === '__none' ? true : (ocConfig?.getGroup?.(ocId)?.visible ?? true), locked: false };
      layerByOc.set(ocId, L); doc.layers.push(L);
    }
    return L;
  };
  for (let i = 1; i <= pdf.numPages; i++) {
    onProgress(`Reading artboard ${i} of ${pdf.numPages}…`);
    const page = await pdf.getPage(i);
    doc.pages.push(await importPage(page, getLayer, warnings));
  }
  if (!doc.layers.length) getLayer(null);
  doc.meta = { warnings: [...warnings], sourcePages: pdf.numPages };
  await pdf.destroy();
  return doc;
}

async function getObj(page, id) {
  const store = id.startsWith('g_') ? page.commonObjs : page.objs;
  if (store.has(id)) return store.get(id);
  return new Promise((res) => { const t = setTimeout(() => res(null), 4000); store.get(id, (d) => { clearTimeout(t); res(d); }); });
}

function imageToHref(d) {
  const w = d.width, h = d.height;
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  if (d.bitmap) { ctx.drawImage(d.bitmap, 0, 0); return c.toDataURL('image/png'); }
  const out = ctx.createImageData(w, h), o = out.data, s = d.data;
  if (d.kind === 3) o.set(s.subarray(0, w * h * 4));
  else if (d.kind === 2) for (let i = 0, j = 0, n = w * h; i < n; i++, j += 3) { o[i * 4] = s[j]; o[i * 4 + 1] = s[j + 1]; o[i * 4 + 2] = s[j + 2]; o[i * 4 + 3] = 255; }
  else if (d.kind === 1) {
    const rowBytes = (w + 7) >> 3;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const v = (s[y * rowBytes + (x >> 3)] >> (7 - (x & 7))) & 1 ? 255 : 0;
      const k = (y * w + x) * 4; o[k] = o[k + 1] = o[k + 2] = v; o[k + 3] = 255;
    }
  } else return null;
  ctx.putImageData(out, 0, 0);
  return c.toDataURL('image/png');
}

function gradFromIR(ir, m) {
  if (!ir || ir[0] !== 'RadialAxial') return null;
  const [, type, , stops, p0, p1, r0, r1] = ir;
  return { grad: { type, stops: stops.map((s) => [s[0], s[1]]), p0, p1, r0: r0 || 0, r1: r1 || 0, m } };
}

async function importPage(page, getLayer, warnings) {
  const vp = page.getViewport({ scale: 1 });
  const base = vp.transform;
  const out = { w: vp.width, h: vp.height, objects: [] };
  const ol = await page.getOperatorList();
  const { fnArray: fn, argsArray: args } = ol;

  const fresh = () => ({
    ctm: base, fill: '#000000', stroke: '#000000', fa: 1, sa: 1, lw: 1, cap: 0, join: 0, miter: 10, dash: null,
    clips: [], fontId: null, fs: 0, rise: 0, cs: 0, ws: 0, hs: 1, leading: 0, mode: 0,
  });
  let st = fresh();
  const stack = [];
  let path = [], cx = 0, cy = 0, sx = 0, sy = 0;
  let pendingClip = null;
  const oc = []; // marked-content stack of OCG ids (null where not an OC)
  let tm = [1, 0, 0, 1, 0, 0], tlm = [1, 0, 0, 1, 0, 0];
  const fontCache = new Map();
  const curLayer = () => getLayer(oc.find((x) => x) || null).id;
  const push = (o) => { o.layer = curLayer(); o.clips = st.clips.map((c) => ({ segs: c.segs, rule: c.rule })); out.objects.push(o); };

  const pt = (x, y) => [st.ctm[0] * x + st.ctm[2] * y + st.ctm[4], st.ctm[1] * x + st.ctm[3] * y + st.ctm[5]];

  const setPattern = async (a, which) => {
    let v = '#808080';
    if (a[0] === 'Shading') {
      const ir = await getObj(page, a[1]);
      const g = gradFromIR(ir, mul(base, a[2] || [1, 0, 0, 1, 0, 0]));
      if (g) v = g; else warnings.add('Mesh gradients are shown as flat grey (not supported).');
    } else warnings.add('Tiling pattern fills are shown as flat grey (not supported).');
    st[which] = v;
  };

  const paintPath = (doFill, doStroke, rule) => {
    if (path.length > 1 || (path.length === 1 && path[0][0] !== 'M')) {
      if (doFill || doStroke) {
        push(makePath(path.map((s) => s.slice()), {
          fill: doFill ? st.fill : null, stroke: doStroke ? st.stroke : null,
          sw: st.lw * scaleOf(st.ctm), cap: st.cap, join: st.join, miter: st.miter,
          dash: st.dash && st.dash[0]?.length ? [st.dash[0].map((v) => v * scaleOf(st.ctm)), st.dash[1] * scaleOf(st.ctm)] : null,
          rule, opacity: Math.min(doFill ? st.fa : st.sa, 1),
        }));
      }
    }
    if (pendingClip) { st.clips = [...st.clips, { segs: path.map((s) => s.slice()), rule: pendingClip }]; pendingClip = null; }
    path = [];
  };

  const addPathOps = (ops, a) => {
    let j = 0;
    for (const op of ops) {
      switch (op) {
        case OPS.moveTo: { const [x, y] = pt(a[j], a[j + 1]); j += 2; path.push(['M', x, y]); cx = a[j - 2]; cy = a[j - 1]; sx = cx; sy = cy; break; }
        case OPS.lineTo: { const [x, y] = pt(a[j], a[j + 1]); j += 2; path.push(['L', x, y]); cx = a[j - 2]; cy = a[j - 1]; break; }
        case OPS.curveTo: {
          const p1 = pt(a[j], a[j + 1]), p2 = pt(a[j + 2], a[j + 3]), p3 = pt(a[j + 4], a[j + 5]);
          path.push(['C', ...p1, ...p2, ...p3]); cx = a[j + 4]; cy = a[j + 5]; j += 6; break;
        }
        case OPS.curveTo2: {
          const p1 = pt(cx, cy), p2 = pt(a[j], a[j + 1]), p3 = pt(a[j + 2], a[j + 3]);
          path.push(['C', ...p1, ...p2, ...p3]); cx = a[j + 2]; cy = a[j + 3]; j += 4; break;
        }
        case OPS.curveTo3: {
          const p1 = pt(a[j], a[j + 1]), p3 = pt(a[j + 2], a[j + 3]);
          path.push(['C', ...p1, ...p3, ...p3]); cx = a[j + 2]; cy = a[j + 3]; j += 4; break;
        }
        case OPS.closePath: path.push(['Z']); cx = sx; cy = sy; break;
        case OPS.rectangle: {
          const x = a[j], y = a[j + 1], w = a[j + 2], h = a[j + 3]; j += 4;
          path.push(['M', ...pt(x, y)], ['L', ...pt(x + w, y)], ['L', ...pt(x + w, y + h)], ['L', ...pt(x, y + h)], ['Z']);
          cx = x; cy = y; sx = x; sy = y; break;
        }
      }
    }
  };

  const getFont = (id) => {
    if (fontCache.has(id)) return fontCache.get(id);
    let f = null;
    try { if (page.commonObjs.has(id)) f = page.commonObjs.get(id); } catch { /* not loaded */ }
    const raw = (f?.name || f?.loadedName || 'Helvetica').replace(/^[A-Z]{6}\+/, '');
    const info = {
      fontName: raw,
      family: raw.split(/[-,]/)[0].replace(/([a-z])([A-Z])/g, '$1 $2').replace(/MT$|PS$/, ''),
      bold: !!f?.bold || /bold|black|heavy|semibold|demi/i.test(raw),
      italic: !!f?.italic || /italic|oblique/i.test(raw),
      generic: f?.fallbackName || (f?.isMonospace ? 'monospace' : f?.isSerifFont ? 'serif' : 'sans-serif'),
      fm: f?.fontMatrix || [0.001, 0, 0, 0.001, 0, 0],
      vertical: !!f?.vertical, type3: !!f?.isType3Font,
    };
    fontCache.set(id, info);
    return info;
  };

  const showText = (items) => {
    if (!st.fontId) return;
    const f = getFont(st.fontId);
    if (f.vertical || f.type3) { warnings.add('Vertical/Type 3 text is skipped.'); return; }
    const fs = st.fs;
    const hs = st.hs || 1;
    let str = '', adv = 0, segStart = 0;
    const emit = () => {
      if (st.mode !== 3 && str.trim()) {
        let m = mul(st.ctm, mul(mul(tm, [1, 0, 0, 1, segStart, 0]), [hs, 0, 0, 1, 0, st.rise]));
        let size = fs;
        if (size < 0) { size = -size; m = mul(m, [-1, 0, 0, -1, 0, 0]); }
        push({
          id: newId(), type: 'text', str, m, size, fontName: f.fontName, family: f.family, bold: f.bold, italic: f.italic,
          generic: f.generic, fill: st.mode === 1 ? null : st.fill, stroke: st.mode === 1 || st.mode === 2 ? st.stroke : null,
          sw: st.lw * scaleOf(st.ctm), origW: (adv - segStart) / hs, cs: Math.abs(st.cs) > 1e-6 ? st.cs : 0, opacity: st.fa, leading: 1.2,
        });
      }
      str = ''; segStart = adv;
    };
    for (const g of items) {
      if (typeof g === 'number') {
        // TJ adjustment in 1/1000 em. Huge jumps position a separate label, so start a new text object.
        if (Math.abs(g) > 1000) { emit(); adv -= (g * fs) / 1000 * hs; segStart = adv; continue; }
        adv -= (g * fs) / 1000 * hs;
        if (g < -120 && str && !str.endsWith(' ')) str += ' ';
        continue;
      }
      if (!g) continue;
      str += g.unicode ?? '';
      let w = (g.width || 0) * fs * f.fm[0] + st.cs;
      if (g.isSpace) w += st.ws;
      adv += w * hs;
    }
    emit();
    tm = mul(tm, [1, 0, 0, 1, adv, 0]);
  };

  const saveState = () => stack.push({ ...st });
  for (let i = 0; i < fn.length; i++) {
    const op = fn[i], a = args[i];
    switch (op) {
      case OPS.save: saveState(); break;
      case OPS.restore: if (stack.length) st = stack.pop(); break;
      case OPS.transform: st.ctm = mul(st.ctm, a); break;
      case OPS.paintFormXObjectBegin:
        saveState();
        if (a?.[0]) st.ctm = mul(st.ctm, a[0]);
        if (a?.[1]) { // form bbox acts as a clip
          const [x0, y0, x1, y1] = a[1];
          st.clips = [...st.clips, { segs: [['M', ...pt(x0, y0)], ['L', ...pt(x1, y0)], ['L', ...pt(x1, y1)], ['L', ...pt(x0, y1)], ['Z']], rule: 'nonzero' }];
        }
        break;
      case OPS.paintFormXObjectEnd: if (stack.length) st = stack.pop(); break;
      case OPS.beginMarkedContent: oc.push(null); break;
      case OPS.beginMarkedContentProps: {
        const props = a[1];
        oc.push(a[0] === 'OC' && props?.type === 'OCG' ? props.id : (a[0] === 'OC' && props?.ids?.[0]) || null);
        break;
      }
      case OPS.endMarkedContent: oc.pop(); break;
      case OPS.setLineWidth: st.lw = a[0]; break;
      case OPS.setLineCap: st.cap = a[0]; break;
      case OPS.setLineJoin: st.join = a[0]; break;
      case OPS.setMiterLimit: st.miter = a[0]; break;
      case OPS.setDash: st.dash = [a[0], a[1]]; break;
      case OPS.setGState:
        for (const [k, v] of a[0]) {
          if (k === 'LW') st.lw = v; else if (k === 'LC') st.cap = v; else if (k === 'LJ') st.join = v;
          else if (k === 'ML') st.miter = v; else if (k === 'D') st.dash = [v[0], v[1]];
          else if (k === 'ca') st.fa = v; else if (k === 'CA') st.sa = v;
          else if (k === 'Font') { st.fontId = v[0]; st.fs = v[1]; }
          else if (k === 'SMask' && v) warnings.add('Soft masks / opacity masks are ignored.');
          else if (k === 'BM' && v && v !== 'source-over' && v !== 'Normal') warnings.add('Blend modes (multiply, overlay…) are not preserved.');
        }
        break;
      case OPS.setFillRGBColor: st.fill = rgbHex(a[0], a[1], a[2]); break;
      case OPS.setStrokeRGBColor: st.stroke = rgbHex(a[0], a[1], a[2]); break;
      case OPS.setFillTransparent: st.fill = null; break;
      case OPS.setStrokeTransparent: st.stroke = null; break;
      case OPS.setFillColorN: await setPattern(a, 'fill'); break;
      case OPS.setStrokeColorN: await setPattern(a, 'stroke'); break;
      case OPS.constructPath: addPathOps(a[0], a[1]); break;
      case OPS.moveTo: case OPS.lineTo: case OPS.curveTo: case OPS.curveTo2: case OPS.curveTo3: case OPS.closePath: case OPS.rectangle:
        break; // pdf.js 4.x always batches these into constructPath
      case OPS.clip: pendingClip = 'nonzero'; break;
      case OPS.eoClip: pendingClip = 'evenodd'; break;
      case OPS.stroke: paintPath(false, true, 'nonzero'); break;
      case OPS.closeStroke: path.push(['Z']); paintPath(false, true, 'nonzero'); break;
      case OPS.fill: paintPath(true, false, 'nonzero'); break;
      case OPS.eoFill: paintPath(true, false, 'evenodd'); break;
      case OPS.fillStroke: paintPath(true, true, 'nonzero'); break;
      case OPS.eoFillStroke: paintPath(true, true, 'evenodd'); break;
      case OPS.closeFillStroke: path.push(['Z']); paintPath(true, true, 'nonzero'); break;
      case OPS.closeEOFillStroke: path.push(['Z']); paintPath(true, true, 'evenodd'); break;
      case OPS.endPath: paintPath(false, false, 'nonzero'); break;
      case OPS.shadingFill: {
        const ir = await getObj(page, a[0]);
        const g = gradFromIR(ir, st.ctm);
        if (g) {
          const sv = path; path = [['M', 0, 0], ['L', out.w, 0], ['L', out.w, out.h], ['L', 0, out.h], ['Z']];
          const pf = st.fill; st.fill = g; paintPath(true, false, 'nonzero'); st.fill = pf; path = sv;
        } else warnings.add('Mesh gradients are not supported.');
        break;
      }
      case OPS.paintSolidColorImageMask: {
        const sv = path; path = [['M', ...pt(0, 0)], ['L', ...pt(1, 0)], ['L', ...pt(1, 1)], ['L', ...pt(0, 1)], ['Z']];
        paintPath(true, false, 'nonzero'); path = sv; break;
      }
      case OPS.paintImageXObject: case OPS.paintInlineImageXObject: {
        const d = op === OPS.paintImageXObject ? await getObj(page, a[0]) : a[0];
        const href = d && imageToHref(d);
        if (href) push({ id: newId(), type: 'image', href, m: st.ctm, opacity: st.fa, w: d.width, h: d.height });
        else warnings.add('Some images could not be decoded.');
        break;
      }
      case OPS.paintImageMaskXObject: case OPS.paintImageXObjectRepeat: case OPS.paintInlineImageXObjectGroup:
        warnings.add('Stencil/repeated image masks are not supported.'); break;
      // ---- text state ----
      case OPS.beginText: tm = [1, 0, 0, 1, 0, 0]; tlm = [1, 0, 0, 1, 0, 0]; break;
      case OPS.setCharSpacing: st.cs = a[0]; break;
      case OPS.setWordSpacing: st.ws = a[0]; break;
      case OPS.setHScale: st.hs = a[0] / 100; break;
      case OPS.setLeading: st.leading = a[0]; break;
      case OPS.setFont: st.fontId = a[0]; st.fs = a[1]; break;
      case OPS.setTextRise: st.rise = a[0]; break;
      case OPS.setTextRenderingMode: st.mode = a[0]; break;
      case OPS.setTextMatrix: tm = a.slice(); tlm = a.slice(); break;
      case OPS.moveText: tlm = mul(tlm, [1, 0, 0, 1, a[0], a[1]]); tm = tlm; break;
      case OPS.setLeadingMoveText: st.leading = -a[1]; tlm = mul(tlm, [1, 0, 0, 1, a[0], a[1]]); tm = tlm; break;
      case OPS.nextLine: tlm = mul(tlm, [1, 0, 0, 1, 0, -st.leading]); tm = tlm; break;
      case OPS.showText: case OPS.showSpacedText: showText(a[0]); break;
      case OPS.nextLineShowText: tlm = mul(tlm, [1, 0, 0, 1, 0, -st.leading]); tm = tlm; showText(a[0]); break;
      case OPS.nextLineSetSpacingShowText: st.ws = a[0]; st.cs = a[1]; tlm = mul(tlm, [1, 0, 0, 1, 0, -st.leading]); tm = tlm; showText(a[2]); break;
      default: break;
    }
  }
  return out;
}
