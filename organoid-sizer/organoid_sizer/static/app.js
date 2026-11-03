/* Organoid Sizer – browser front end (no external libraries, works offline). */
"use strict";

// ============================================================ icons (inline SVG, stroke = currentColor)
const ICONS = {
  images: '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="M7 3h12a2 2 0 0 1 2 2v12"/><circle cx="8" cy="10" r="1.5"/><path d="m3 17 4-4 3 3 3-3 4 4"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
  sparkle: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  download: '<path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14"/><circle cx="12" cy="17.5" r=".6" fill="currentColor"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  left: '<path d="m15 6-6 6 6 6"/>', right: '<path d="m9 6 6 6-6 6"/>', up: '<path d="m6 15 6-6 6 6"/>',
  home: '<path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z"/>',
  cursor: '<path d="M5 3l14 7-6 2-2 6z"/>',
  hand: '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12m0-7a1.5 1.5 0 0 1 3 0v7m0-5.5a1.5 1.5 0 0 1 3 0V13m0-3.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-1.5a6 6 0 0 1-4.9-2.6L5 15.5a1.6 1.6 0 0 1 2.6-1.8L8 14"/>',
  circle: '<circle cx="12" cy="12" r="7.5"/><path d="M12 9v6M9 12h6"/>',
  lasso: '<path d="M7 18c-2-1-3.5-3-3.5-5.5C3.5 8 7.3 5 12 5s8.5 3 8.5 7.5S16.7 20 12 20c-1.2 0-2.3-.2-3.3-.5"/><circle cx="7" cy="19" r="1.5"/>',
  brush: '<path d="M18.4 2.6a2 2 0 0 1 2.9 2.9L11 15.9 8.1 13z"/><path d="M8 13.5c-2 0-3.5 1.5-3.5 3.5 0 1.2-.7 2.3-1.8 2.8 1 .8 2.4 1.2 3.8 1.2a4 4 0 0 0 4-4z"/>',
  eraser: '<path d="m7 21-4.3-4.3a1 1 0 0 1 0-1.4L13.3 4.7a1 1 0 0 1 1.4 0l5.6 5.6a1 1 0 0 1 0 1.4L11 21"/><path d="M7 21h14M9 11l6 6"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/>',
  ruler: '<path d="M3 17 17 3l4 4L7 21z"/><path d="m7 13 2 2m1-5 2 2m1-5 2 2"/>',
  roi: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><circle cx="12" cy="12" r="4"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  check: '<path d="m5 12 5 5L20 7"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
  split: '<circle cx="8" cy="12" r="5"/><circle cx="16" cy="12" r="5"/><path d="M12 4v16" stroke-dasharray="2 2"/>',
  merge: '<path d="M7 4v5a5 5 0 0 0 5 5 5 5 0 0 1 5 5v1M17 4v5a5 5 0 0 1-3 4.6"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
  ticks: '<path d="M3 16h18M5 16v-4M9 16v-6M13 16v-4M17 16v-6M21 16v-4"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  flag: '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
};
function hydrateIcons(root = document) {
  root.querySelectorAll("i[data-i]").forEach(el => {
    if (el.firstChild) return;
    el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[el.dataset.i] || ""}</svg>`;
  });
}

// ============================================================ helpers
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const store = {
  get(k, d) { try { const v = localStorage.getItem("os." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("os." + k, JSON.stringify(v)); } catch {} },
};
async function api(path, body, raw) {
  const opt = raw ? { method: "POST", body: raw } :
    body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  let j;
  try { j = await r.json(); } catch { throw new Error(`Server error (${r.status})`); }
  if (!r.ok) throw new Error(j.error || `Error ${r.status}`);
  return j;
}
function toast(msg, kind = "", ms = 3800) {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = 0; setTimeout(() => t.remove(), 300); }, ms);
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function sig(v, n = 3) { if (v == null || !isFinite(v)) return "—"; const a = Math.abs(v); return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toPrecision(n); }
function upp() { return S.cur && S.cur.calibration && S.cur.calibration.um_per_px; }
function fmtLen(px, u = upp()) {
  if (px == null) return "—";
  if (!u) return `${sig(px)} px`;
  const um = px * u;
  return um >= 1000 ? `${(um / 1000).toFixed(um >= 10000 ? 2 : 3)} mm` : `${sig(um)} µm`;
}
function fmtArea(pxa, u = upp()) {
  if (pxa == null) return "—";
  if (!u) return `${sig(pxa)} px²`;
  const mm2 = pxa * u * u / 1e6;
  return mm2 >= 0.01 ? `${mm2.toPrecision(3)} mm²` : `${sig(pxa * u * u)} µm²`;
}
const EXCLUDE_REASONS = ["Touching a neighbour", "Fused organoids", "Out of focus", "Not an organoid / debris", "Cut by image edge", "Other…"];

// ============================================================ state
const S = {
  state: null, cur: null, key: null, img: null, mini: null,
  view: { s: 1, ox: 0, oy: 0 }, dpr: window.devicePixelRatio || 1,
  tool: "select", sel: new Set(), hover: null, paths: new Map(),
  draw: null, line: null, cands: [], tick: null, brushR: store.get("brushR", 12),
  opt: store.get("opt", { outline: true, fill: true, numbers: true, excluded: true, opacity: 55, bright: 100, contrast: 100 }),
  lineW: store.get("lineW", 2), overlayHidden: false, sort: { k: "number", asc: true }, filter: "all",
  spaceDown: false, busy: false, flagIdx: -1, pointers: new Map(),
};
const cv = $("#canvas"), ctx = cv.getContext("2d");
const mm = $("#minimap"), mctx = mm.getContext("2d");

// ============================================================ boot
async function boot() {
  hydrateIcons();
  applyUiPrefs();
  bindUi();
  new ResizeObserver(resize).observe($("#viewer"));
  await refreshState();
  const st = S.state;
  if (st.images.length) await openImage(store.get("lastKey") && st.images.find(i => i.key === store.get("lastKey")) ? store.get("lastKey") : st.images[0].key);
  else showEmpty(true);
  if (st.job && st.job.running) pollJob();
}

async function refreshState() {
  S.state = await api("/api/state");
  renderFolder();
  renderList();
  renderCellposeStatus();
  loadSettingsIntoUi(S.state.settings);
  $("#trustDpi").checked = !!S.state.trust_dpi;
  renderPresets();
  $("#outDir").textContent = S.state.output_dir || "—";
}

function renderFolder() {
  const d = S.state.input_dir;
  $("#folderLabel").textContent = d ? d : "Open a folder…";
  $("#btnFolder").title = d ? `Images: ${d}\nResults: ${S.state.output_dir}` : "Open a folder of images";
}

function renderCellposeStatus() {
  const c = S.state.cellpose;
  $("#cpStatus").innerHTML = c.available
    ? `<span class="ok">●</span> Cellpose ${esc(c.version)} ready · ${c.gpu ? '<span class="ok">GPU</span>' : "CPU (slower)"}`
    : `<span class="bad">●</span> Cellpose not found in this Python. Use “Threshold”, or start the app from your Cellpose environment.`;
}

// ============================================================ image list
let thumbObserver = null;
function renderList() {
  const list = $("#imgList");
  const q = $("#imgSearch").value.trim().toLowerCase();
  const imgs = S.state.images.filter(i => !q || i.name.toLowerCase().includes(q));
  list.innerHTML = imgs.map(i => `
    <div class="img-item ${i.key === S.key ? "on" : ""}" data-key="${esc(i.key)}" title="${esc(i.name)}">
      <div class="th" data-thumb="${esc(i.key)}"></div>
      <div style="min-width:0"><div class="nm">${esc(i.name)}</div><div class="meta">${tagsFor(i)}</div></div>
    </div>`).join("");
  if (thumbObserver) thumbObserver.disconnect();
  thumbObserver = new IntersectionObserver(ents => ents.forEach(e => {
    if (e.isIntersecting) {
      const el = e.target;
      el.style.backgroundImage = `url("/api/image/${encodeURIComponent(el.dataset.thumb)}/thumb.jpg")`;
      thumbObserver.unobserve(el);
    }
  }), { root: list, rootMargin: "200px" });
  list.querySelectorAll("[data-thumb]").forEach(el => thumbObserver.observe(el));
  const n = S.state.images.length, done = S.state.images.filter(i => i.segmented).length, rev = S.state.images.filter(i => i.reviewed).length;
  $("#listFoot").textContent = n ? `${n} images · ${done} detected · ${rev} reviewed` : "No images yet";
}
function tagsFor(i) {
  const t = [];
  if (i.segmented) t.push(`<span class="tag acc">${i.n_included}${i.n_excluded ? ` +${i.n_excluded}✕` : ""}</span>`);
  else t.push(`<span class="tag">not detected</span>`);
  if (!(i.calibration && i.calibration.um_per_px)) t.push(`<span class="tag warn">no scale</span>`);
  if (i.reviewed) t.push(`<span class="tag ok">✓ reviewed</span>`);
  return t.join("");
}
function updateListItem() {
  if (!S.cur) return;
  const i = S.state.images.find(x => x.key === S.key);
  if (!i) return;
  const objs = S.cur.objects;
  Object.assign(i, { n_objects: objs.length, n_included: objs.filter(o => o.status === "included").length,
    n_excluded: objs.filter(o => o.status !== "included").length, segmented: S.cur.segmented, reviewed: S.cur.reviewed, calibration: S.cur.calibration });
  const el = $(`.img-item[data-key="${CSS.escape(S.key)}"] .meta`);
  if (el) el.innerHTML = tagsFor(i);
  const n = S.state.images.length, done = S.state.images.filter(i => i.segmented).length, rev = S.state.images.filter(i => i.reviewed).length;
  $("#listFoot").textContent = `${n} images · ${done} detected · ${rev} reviewed`;
}

// ============================================================ open image
async function openImage(key) {
  if (!key) return;
  showEmpty(false);
  S.key = key; store.set("lastKey", key);
  S.sel.clear(); S.hover = null; S.draw = null; S.line = null; S.cands = []; S.tick = null; S.flagIdx = -1;
  $$(".img-item").forEach(el => el.classList.toggle("on", el.dataset.key === key));
  $("#imgName").textContent = "Loading…";
  try {
    const [data, img] = await Promise.all([api(`/api/image/${encodeURIComponent(key)}/data`), loadImg(`/api/image/${encodeURIComponent(key)}/display.png`)]);
    if (S.key !== key) return;
    S.img = img;
    buildMini();
    setPayload(data);
    fit();
    $(`.img-item[data-key="${CSS.escape(key)}"]`)?.scrollIntoView({ block: "nearest" });
  } catch (e) { toast(`Could not open image: ${e.message}`, "err", 7000); }
  renderLineInfo(); renderCands(); renderTick();
}
function loadImg(src) {
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error("image could not be loaded")); im.src = src; });
}
function setPayload(d) {
  S.cur = d;
  S.paths.clear();
  for (const o of d.objects) {
    const p = new Path2D();
    const c = o.contour;
    if (!c.length) continue;
    p.moveTo(c[0][0] + .5, c[0][1] + .5);
    for (let k = 1; k < c.length; k++) p.lineTo(c[k][0] + .5, c[k][1] + .5);
    p.closePath();
    S.paths.set(o.label, p);
  }
  for (const id of [...S.sel]) if (!S.paths.has(id)) S.sel.delete(id);
  $("#imgName").textContent = d.name;
  const i = S.state.images.findIndex(x => x.key === d.key);
  $("#imgSub").textContent = `${i + 1} / ${S.state.images.length} · ${d.width} × ${d.height} px`;
  $("#chkReviewed").checked = !!d.reviewed;
  $("#btnUndo").disabled = !d.can_undo; $("#btnRedo").disabled = !d.can_redo;
  $("#log").textContent = (d.log || []).join("\n") + ((d.info && d.info.notes && d.info.notes.length) ? "\n\nFile: " + d.info.notes.join("\n") : "");
  renderCalibration(); renderObjects(); renderSelBar(); updateListItem(); updateStatusCal(); updateFilterUnits();
  draw();
}
function showEmpty(on) {
  $("#empty").hidden = !on;
  $("#toolbar").style.visibility = on ? "hidden" : "";
  $(".view-ctrl").style.visibility = on ? "hidden" : "";
  $(".viewer-top").style.visibility = on ? "hidden" : "";
  mm.style.visibility = on ? "hidden" : "";
}

// ============================================================ view transform
function resize() {
  const r = $("#viewer").getBoundingClientRect();
  S.dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(r.width * S.dpr); cv.height = Math.round(r.height * S.dpr);
  const mr = mm.getBoundingClientRect();
  mm.width = Math.round(mr.width * S.dpr); mm.height = Math.round(mr.height * S.dpr);
  draw();
}
function vw() { return cv.width / S.dpr; } function vh() { return cv.height / S.dpr; }
function toImg(sx, sy) { return [(sx - S.view.ox) / S.view.s, (sy - S.view.oy) / S.view.s]; }
function toScr(ix, iy) { return [ix * S.view.s + S.view.ox, iy * S.view.s + S.view.oy]; }
function fit() {
  if (!S.cur) return;
  const pad = 40, w = vw(), h = vh();
  const s = Math.min((w - 2 * pad - 60) / S.cur.width, (h - 2 * pad - 40) / S.cur.height);
  S.view.s = Math.max(0.01, s);
  S.view.ox = (w - S.cur.width * S.view.s) / 2 + 20;
  S.view.oy = (h - S.cur.height * S.view.s) / 2 + 10;
  draw();
}
function zoomAt(f, sx = vw() / 2, sy = vh() / 2) {
  const ns = Math.min(64, Math.max(0.02, S.view.s * f));
  const [ix, iy] = toImg(sx, sy);
  S.view.s = ns; S.view.ox = sx - ix * ns; S.view.oy = sy - iy * ns;
  draw();
}
function setZoom(s) { zoomAt(s / S.view.s); }
function zoomTo(o, tight = 3.5) {
  const [x0, y0, x1, y1] = o.bbox;
  const w = (x1 - x0) * tight, h = (y1 - y0) * tight;
  const s = Math.min(16, Math.min(vw() / Math.max(w, 20), vh() / Math.max(h, 20)));
  S.view.s = s;
  S.view.ox = vw() / 2 - o.cx * s; S.view.oy = vh() / 2 - o.cy * s;
  draw();
}
function centerOn(o) {
  const [sx, sy] = toScr(o.cx, o.cy);
  if (sx < 60 || sy < 60 || sx > vw() - 60 || sy > vh() - 60) { S.view.ox += vw() / 2 - sx; S.view.oy += vh() / 2 - sy; }
  draw();
}

// ============================================================ rendering
let raf = 0;
function draw() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); }
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
let COL = {};
function readColors() { COL = { inc: css("--inc"), exc: css("--exc"), sel: css("--sel"), flag: css("--flag"), acc: css("--accent"), acc2: css("--accent2") }; }

function render() {
  const d = S.dpr, s = S.view.s;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  $("#zoomVal").textContent = `${Math.round(s * 100)}%`;
  if (!S.img || !S.cur) return;
  ctx.setTransform(d * s, 0, 0, d * s, d * S.view.ox, d * S.view.oy);
  ctx.imageSmoothingEnabled = s < 2.5;
  if (S.opt.bright !== 100 || S.opt.contrast !== 100) ctx.filter = `brightness(${S.opt.bright}%) contrast(${S.opt.contrast}%)`;
  ctx.drawImage(S.img, 0, 0);
  ctx.filter = "none";
  const lw = S.lineW / s;

  // analysis region: dim outside
  const roi = S.cur.roi;
  if (roi && roi.points && roi.points.length > 2) {
    const p = new Path2D();
    p.rect(-1e5, -1e5, 2e5, 2e5);
    p.moveTo(roi.points[0][0], roi.points[0][1]);
    for (const q of roi.points.slice(1)) p.lineTo(q[0], q[1]);
    p.closePath();
    ctx.fillStyle = "rgba(0,0,0,.38)"; ctx.fill(p, "evenodd");
    ctx.save(); ctx.setLineDash([8 / s, 6 / s]); ctx.strokeStyle = COL.acc2; ctx.lineWidth = lw;
    const r = new Path2D(); r.moveTo(roi.points[0][0], roi.points[0][1]); for (const q of roi.points.slice(1)) r.lineTo(q[0], q[1]); r.closePath();
    ctx.stroke(r); ctx.restore();
  }

  // objects
  const alpha = S.opt.opacity / 100;
  if (!S.overlayHidden) {
    for (const o of S.cur.objects) {
      const exc = o.status !== "included";
      if (exc && !S.opt.excluded) continue;
      const p = S.paths.get(o.label); if (!p) continue;
      const sel = S.sel.has(o.label), hov = S.hover === o.label;
      const col = exc ? COL.exc : COL.inc;
      if (S.opt.fill) {
        ctx.globalAlpha = alpha * (sel ? .55 : hov ? .45 : .28);
        ctx.fillStyle = sel ? COL.sel : col; ctx.fill(p);
        ctx.globalAlpha = 1;
      }
      if (S.opt.outline || sel || hov) {
        ctx.globalAlpha = Math.min(1, .35 + alpha);
        if (exc) ctx.setLineDash([5 / s, 4 / s]);
        ctx.strokeStyle = sel ? COL.sel : col;
        ctx.lineWidth = (sel ? 2.2 : hov ? 1.6 : 1) * lw;
        ctx.stroke(p);
        ctx.setLineDash([]); ctx.globalAlpha = 1;
      }
    }
  }

  // scale bar candidates
  for (const [k, c] of S.cands.entries()) {
    const [x0, y0, x1, y1] = c.bbox;
    ctx.strokeStyle = COL.acc2; ctx.lineWidth = 2 / s; ctx.setLineDash([4 / s, 3 / s]);
    ctx.strokeRect(x0 - 3 / s, y0 - 3 / s, x1 - x0 + 6 / s, y1 - y0 + 6 / s); ctx.setLineDash([]);
  }

  // drawing previews (image space)
  drawPreview(s, lw);

  // screen-space overlays
  ctx.setTransform(d, 0, 0, d, 0, 0);
  if (!S.overlayHidden && S.opt.numbers) drawNumbers();
  for (const [k, c] of S.cands.entries()) {
    const [sx, sy] = toScr(c.bbox[0], c.bbox[1]);
    label(`bar ${k + 1}: ${c.length_px.toFixed(0)} px`, sx, sy - 10, COL.acc2);
  }
  drawLine();
  drawMini();
}

function drawNumbers() {
  const s = S.view.s;
  const fs = 12;
  ctx.font = `600 ${fs}px Inter, system-ui, sans-serif`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const o of S.cur.objects) {
    const exc = o.status !== "included";
    if (exc && !S.opt.excluded) continue;
    const dscr = o.eq_diam_px * s;
    const [sx, sy] = toScr(o.cx, o.cy);
    if (sx < -20 || sy < -20 || sx > vw() + 20 || sy > vh() + 20) continue;
    if (dscr >= 12 || S.sel.has(o.label)) {
      const t = String(o.number);
      ctx.lineWidth = 3.5; ctx.strokeStyle = "rgba(0,0,0,.75)"; ctx.strokeText(t, sx, sy);
      ctx.fillStyle = S.sel.has(o.label) ? COL.sel : exc ? COL.exc : "#fff"; ctx.fillText(t, sx, sy);
    }
    if (o.flags && o.flags.includes("possibly fused / irregular") && !exc && dscr >= 8) {
      const [bx, by] = toScr(o.bbox[2], o.bbox[1]);
      ctx.beginPath(); ctx.arc(bx, by, 7, 0, 7); ctx.fillStyle = COL.flag; ctx.fill();
      ctx.fillStyle = "#111"; ctx.font = `700 10px Inter, system-ui, sans-serif`; ctx.fillText("?", bx, by + .5);
      ctx.font = `600 ${fs}px Inter, system-ui, sans-serif`;
    }
  }
}
function label(t, x, y, bg) {
  ctx.font = `600 12px Inter, system-ui, sans-serif`;
  const w = ctx.measureText(t).width + 12;
  ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x - w / 2, y - 10, w, 20, 6) : ctx.rect(x - w / 2, y - 10, w, 20); ctx.fill();
  ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(t, x, y);
}
function drawPreview(s, lw) {
  const D = S.draw;
  if (!D) return;
  ctx.strokeStyle = COL.sel; ctx.lineWidth = 1.6 * lw; ctx.fillStyle = "rgba(250,204,21,.18)";
  if ((D.type === "polygon" || D.type === "roi-poly") && D.pts.length) {
    ctx.beginPath(); ctx.moveTo(D.pts[0][0], D.pts[0][1]);
    for (const p of D.pts.slice(1)) ctx.lineTo(p[0], p[1]);
    if (D.hoverPt) ctx.lineTo(D.hoverPt[0], D.hoverPt[1]);
    ctx.fill(); ctx.stroke();
    for (const [k, p] of D.pts.entries()) { ctx.beginPath(); ctx.arc(p[0], p[1], (k === 0 ? 5 : 3) / s, 0, 7); ctx.fillStyle = k === 0 ? COL.sel : "#fff"; ctx.fill(); }
  } else if ((D.type === "ellipse" || D.type === "roi-ellipse") && D.b) {
    const e = ellipseFrom(D);
    ctx.beginPath(); ctx.ellipse(e.cx, e.cy, Math.max(.5, e.rx), Math.max(.5, e.ry), 0, 0, 7); ctx.fill(); ctx.stroke();
  } else if (D.type === "brush" || D.type === "erase") {
    ctx.save(); ctx.globalAlpha = .5; ctx.strokeStyle = D.type === "erase" ? "#ef4444" : COL.sel;
    ctx.lineWidth = 2 * S.brushR + 1; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(D.pts[0][0], D.pts[0][1]); for (const p of D.pts) ctx.lineTo(p[0], p[1]);
    if (D.pts.length === 1) ctx.lineTo(D.pts[0][0] + .01, D.pts[0][1]);
    ctx.stroke(); ctx.restore();
  } else if (D.type === "split" && D.pts.length > 1) {
    ctx.save(); ctx.strokeStyle = "#ef4444"; ctx.lineWidth = 2 * lw; ctx.setLineDash([6 / s, 4 / s]);
    ctx.beginPath(); ctx.moveTo(D.pts[0][0], D.pts[0][1]); for (const p of D.pts) ctx.lineTo(p[0], p[1]); ctx.stroke(); ctx.restore();
  } else if (D.type === "box" && D.b) {
    ctx.save(); ctx.setLineDash([4 / s, 3 / s]); ctx.strokeStyle = COL.sel; ctx.lineWidth = lw; ctx.fillStyle = "rgba(250,204,21,.08)";
    ctx.fillRect(D.a[0], D.a[1], D.b[0] - D.a[0], D.b[1] - D.a[1]); ctx.strokeRect(D.a[0], D.a[1], D.b[0] - D.a[0], D.b[1] - D.a[1]); ctx.restore();
  }
}
function drawBrushCursor() {
  if (!S.mouse || !(S.tool === "brush" || S.tool === "erase")) return;
  const [sx, sy] = S.mouse;
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.beginPath(); ctx.arc(sx, sy, Math.max(2, S.brushR * S.view.s), 0, 7);
  ctx.strokeStyle = S.tool === "erase" ? "#ef4444" : COL.sel; ctx.lineWidth = 1.5; ctx.stroke();
}
function drawLine() {
  const L = S.line;
  if (L && L.p0 && L.p1) {
    const [a, b] = [toScr(...L.p0), toScr(...L.p1)];
    ctx.strokeStyle = "#000"; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
    ctx.strokeStyle = COL.sel; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
    // perpendicular end caps
    const ang = Math.atan2(b[1] - a[1], b[0] - a[0]) + Math.PI / 2, c = 8;
    for (const p of [a, b]) {
      ctx.beginPath(); ctx.moveTo(p[0] - c * Math.cos(ang), p[1] - c * Math.sin(ang)); ctx.lineTo(p[0] + c * Math.cos(ang), p[1] + c * Math.sin(ang)); ctx.stroke();
      ctx.beginPath(); ctx.arc(p[0], p[1], 5, 0, 7); ctx.fillStyle = "#fff"; ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = 1; ctx.stroke();
      ctx.strokeStyle = COL.sel; ctx.lineWidth = 2;
    }
    const px = Math.hypot(L.p1[0] - L.p0[0], L.p1[1] - L.p0[1]);
    label(`${px.toFixed(1)} px${upp() ? " · " + fmtLen(px) : ""}`, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 18, "rgba(15,23,42,.85)");
  }
  drawBrushCursor();
}
function ellipseFrom(D) {
  let [x0, y0] = D.a, [x1, y1] = D.b;
  if (D.square) { const m = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)); x1 = x0 + Math.sign(x1 - x0 || 1) * m; y1 = y0 + Math.sign(y1 - y0 || 1) * m; }
  return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, rx: Math.abs(x1 - x0) / 2, ry: Math.abs(y1 - y0) / 2 };
}

// minimap
function buildMini() {
  const c = document.createElement("canvas");
  const k = Math.min(1, 400 / Math.max(S.img.width, S.img.height));
  c.width = Math.max(1, Math.round(S.img.width * k)); c.height = Math.max(1, Math.round(S.img.height * k));
  c.getContext("2d").drawImage(S.img, 0, 0, c.width, c.height);
  S.mini = c;
}
function miniGeom() {
  const W = mm.width / S.dpr, H = mm.height / S.dpr;
  const k = Math.min(W / S.cur.width, H / S.cur.height);
  return { k, x: (W - S.cur.width * k) / 2, y: (H - S.cur.height * k) / 2 };
}
function drawMini() {
  if (!S.mini || !S.cur || mm.offsetParent === null) return;
  const g = miniGeom();
  const whole = vw() >= S.cur.width * S.view.s && vh() >= S.cur.height * S.view.s;
  mm.style.opacity = whole ? 0 : 1; mm.style.pointerEvents = whole ? "none" : "auto";
  mctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  mctx.clearRect(0, 0, mm.width, mm.height);
  mctx.drawImage(S.mini, g.x, g.y, S.cur.width * g.k, S.cur.height * g.k);
  const [ix0, iy0] = toImg(0, 0), [ix1, iy1] = toImg(vw(), vh());
  mctx.strokeStyle = COL.sel; mctx.lineWidth = 1.5;
  mctx.strokeRect(g.x + ix0 * g.k, g.y + iy0 * g.k, (ix1 - ix0) * g.k, (iy1 - iy0) * g.k);
}
mm.addEventListener("pointerdown", e => {
  const move = ev => {
    const r = mm.getBoundingClientRect(), g = miniGeom();
    const ix = (ev.clientX - r.left - g.x) / g.k, iy = (ev.clientY - r.top - g.y) / g.k;
    S.view.ox = vw() / 2 - ix * S.view.s; S.view.oy = vh() / 2 - iy * S.view.s; draw();
  };
  move(e); mm.setPointerCapture(e.pointerId);
  mm.onpointermove = move; mm.onpointerup = () => { mm.onpointermove = null; };
});

// ============================================================ hit testing
function objectAt(ix, iy) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  let best = null, bestA = Infinity;
  for (const o of S.cur.objects) {
    if (o.status !== "included" && !S.opt.excluded) continue;
    const [x0, y0, x1, y1] = o.bbox;
    if (ix < x0 - 1 || iy < y0 - 1 || ix > x1 + 1 || iy > y1 + 1) continue;
    const p = S.paths.get(o.label);
    if (p && ctx.isPointInPath(p, ix, iy) && o.area_px < bestA) { best = o; bestA = o.area_px; }
  }
  return best;
}
const objByLabel = l => S.cur.objects.find(o => o.label === l);

// ============================================================ tools
const HINTS = {
  select: "Click an organoid to select · Shift+click adds · Shift+drag box-selects · drag to pan · right-click for actions",
  pan: "Drag to move around · wheel to zoom",
  ellipse: "Drag a box around the organoid · Shift = perfect circle",
  polygon: "Click points around the organoid or hold & drag to trace · Enter / double-click / first point to finish · Esc cancels",
  brush: "Start on an organoid to grow it, or on empty space to paint a new one · [ ] size",
  erase: "Start on an organoid to remove pixels from it · [ ] size",
  split: "Draw a line through the organoid(s), from outside to outside, to cut them apart",
  ruler: "Drag along a ruler or scale bar · drag the ends to adjust · then enter the real length in the Scale tab",
  roi: "Drag for a round region (Shift = circle) or click points for a polygon · Enter to finish",
};
let hintTimer = 0;
function setTool(t, quiet) {
  S.tool = t; S.draw = null;
  $$(".toolbar [data-tool]").forEach(b => b.classList.toggle("on", b.dataset.tool === t));
  $("#brushSize").hidden = !(t === "brush" || t === "erase");
  cv.style.cursor = t === "pan" ? "grab" : t === "select" ? "default" : (t === "brush" || t === "erase") ? "none" : "crosshair";
  if (!quiet) { const h = $("#toolHint"); h.textContent = HINTS[t] || ""; h.classList.add("show"); clearTimeout(hintTimer); hintTimer = setTimeout(() => h.classList.remove("show"), 4500); }
  $("#sbHint").textContent = HINTS[t] || "";
  draw();
}

function evPos(e) { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

cv.addEventListener("contextmenu", e => e.preventDefault());
cv.addEventListener("wheel", e => {
  e.preventDefault();
  const [sx, sy] = evPos(e);
  if (e.ctrlKey || !e.shiftKey) {
    const dy = e.deltaMode === 1 ? e.deltaY * 30 : e.deltaY;
    zoomAt(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0018)), sx, sy);
  } else { S.view.ox -= e.deltaY; draw(); }
}, { passive: false });

cv.addEventListener("pointerdown", e => {
  if (!S.cur) return;
  closePops();
  cv.setPointerCapture(e.pointerId);
  S.pointers.set(e.pointerId, evPos(e));
  if (S.pointers.size === 2) { startPinch(); return; }
  const [sx, sy] = evPos(e);
  const [ix, iy] = toImg(sx, sy);
  const pan = e.button === 1 || e.button === 2 || S.spaceDown || S.tool === "pan";
  if (pan) { S.drag = { mode: "pan", sx, sy, ox: S.view.ox, oy: S.view.oy, moved: false, button: e.button, ix, iy, cx: e.clientX, cy: e.clientY }; cv.style.cursor = "grabbing"; return; }
  if (e.button !== 0) return;
  const t = S.tool;
  // ruler: grab an end handle?
  if (S.line && S.line.p0 && (t === "ruler" || t === "select")) {
    for (const k of ["p0", "p1"]) {
      const [hx, hy] = toScr(...S.line[k]);
      if (Math.hypot(hx - sx, hy - sy) < 10) { S.drag = { mode: "handle", k }; return; }
    }
  }
  if (t === "select") {
    if (e.shiftKey) { S.drag = { mode: "box" }; S.draw = { type: "box", a: [ix, iy], b: [ix, iy] }; return; }
    const o = objectAt(ix, iy);
    S.drag = { mode: "select-or-pan", sx, sy, ox: S.view.ox, oy: S.view.oy, moved: false, o };
    return;
  }
  if (t === "ellipse" || t === "roi") {
    if (t === "roi" && S.draw && S.draw.type === "roi-poly") { addPolyPoint(ix, iy, sx, sy, e); return; }
    S.draw = { type: t === "roi" ? "roi-ellipse" : "ellipse", a: [ix, iy], b: null, square: e.shiftKey, sx, sy };
    S.drag = { mode: "draw" }; return;
  }
  if (t === "polygon") {
    if (!S.draw) S.draw = { type: "polygon", pts: [] };
    addPolyPoint(ix, iy, sx, sy, e);
    S.drag = { mode: "trace", n0: S.draw.pts.length, last: [sx, sy] }; return;
  }
  if (t === "brush" || t === "erase") {
    const o = objectAt(ix, iy);
    S.draw = { type: t, pts: [[ix, iy]], target: o ? o.label : null };
    S.drag = { mode: "paint" }; draw(); return;
  }
  if (t === "split") { S.draw = { type: "split", pts: [[ix, iy]] }; S.drag = { mode: "paint" }; return; }
  if (t === "ruler") { S.line = { p0: [ix, iy], p1: [ix, iy], purpose: S.linePurpose || "line" }; S.drag = { mode: "handle", k: "p1" }; return; }
});

function addPolyPoint(ix, iy, sx, sy, e) {
  const D = S.draw;
  if (D.pts.length >= 3) {
    const [fx, fy] = toScr(...D.pts[0]);
    if (Math.hypot(fx - sx, fy - sy) < 9) { finishPolygon(); return; }
  }
  D.pts.push([ix, iy]);
  draw();
}

cv.addEventListener("pointermove", e => {
  if (!S.cur) return;
  const [sx, sy] = evPos(e);
  S.mouse = [sx, sy];
  if (S.pointers.has(e.pointerId)) S.pointers.set(e.pointerId, [sx, sy]);
  if (S.pinch && S.pointers.size === 2) { doPinch(); return; }
  const [ix, iy] = toImg(sx, sy);
  updatePosStatus(ix, iy);
  const Dg = S.drag, D = S.draw;
  if (!Dg) {
    if (S.tool === "select" || S.tool === "brush" || S.tool === "erase") {
      const o = objectAt(ix, iy);
      const h = o ? o.label : null;
      if (h !== S.hover) { S.hover = h; showHoverInfo(o); }
    }
    if (D && (D.type === "polygon" || D.type === "roi-poly")) D.hoverPt = [ix, iy];
    draw(); return;
  }
  if (Dg.mode === "pan" || (Dg.mode === "select-or-pan" && (Dg.moved || Math.hypot(sx - Dg.sx, sy - Dg.sy) > 4))) {
    Dg.moved = true; S.view.ox = Dg.ox + sx - Dg.sx; S.view.oy = Dg.oy + sy - Dg.sy; cv.style.cursor = "grabbing"; draw(); return;
  }
  if (Dg.mode === "box" || Dg.mode === "draw") { D.b = [ix, iy]; D.square = e.shiftKey; draw(); return; }
  if (Dg.mode === "trace") {
    if (Math.hypot(sx - Dg.last[0], sy - Dg.last[1]) > 4) { D.pts.push([ix, iy]); Dg.last = [sx, sy]; }
    draw(); return;
  }
  if (Dg.mode === "paint") { const l = D.pts[D.pts.length - 1]; if (Math.hypot(ix - l[0], iy - l[1]) * S.view.s > 2) D.pts.push([ix, iy]); draw(); return; }
  if (Dg.mode === "handle") { S.line[Dg.k] = [ix, iy]; renderLineInfo(); draw(); }
});

cv.addEventListener("pointerup", async e => {
  S.pointers.delete(e.pointerId);
  if (S.pinch) { if (S.pointers.size < 2) S.pinch = null; return; }
  const Dg = S.drag; S.drag = null;
  if (!Dg || !S.cur) return;
  cv.style.cursor = S.tool === "pan" ? "grab" : S.tool === "select" ? "default" : (S.tool === "brush" || S.tool === "erase") ? "none" : "crosshair";
  const D = S.draw;
  if (Dg.mode === "pan") {
    if (Dg.button === 2 && !Dg.moved) openCtx(e.clientX, e.clientY, Dg.ix, Dg.iy);
    return;
  }
  if (Dg.mode === "select-or-pan") {
    if (Dg.moved) return;
    const o = Dg.o;
    if (!o) { if (!e.shiftKey) S.sel.clear(); }
    else if (e.shiftKey || e.ctrlKey || e.metaKey) { S.sel.has(o.label) ? S.sel.delete(o.label) : S.sel.add(o.label); }
    else { S.sel.clear(); S.sel.add(o.label); }
    afterSelect(); return;
  }
  if (Dg.mode === "box") {
    const [x0, x1] = [Math.min(D.a[0], D.b[0]), Math.max(D.a[0], D.b[0])], [y0, y1] = [Math.min(D.a[1], D.b[1]), Math.max(D.a[1], D.b[1])];
    for (const o of S.cur.objects) if (o.cx >= x0 && o.cx <= x1 && o.cy >= y0 && o.cy <= y1 && (S.opt.excluded || o.status === "included")) S.sel.add(o.label);
    S.draw = null; afterSelect(); return;
  }
  if (Dg.mode === "draw") {
    const moved = D.b && Math.hypot(D.b[0] - D.a[0], D.b[1] - D.a[1]) * S.view.s > 5;
    if (!moved) {
      if (D.type === "roi-ellipse") { S.draw = { type: "roi-poly", pts: [D.a] }; draw(); return; } // start polygon ROI
      S.draw = null; draw(); return;
    }
    const el = ellipseFrom(D);
    S.draw = null;
    if (D.type === "ellipse") await edit("ellipse", { cx: el.cx, cy: el.cy, rx: el.rx, ry: el.ry }, "Organoid added");
    else await setRoi(ellipsePts(el));
    return;
  }
  if (Dg.mode === "trace") {
    if (D.pts.length - Dg.n0 > 12) finishPolygon(); // a freehand trace finishes itself
    return;
  }
  if (Dg.mode === "paint") {
    S.draw = null;
    if (D.type === "split") {
      if (D.pts.length < 2) { draw(); return; }
      await edit("split_line", { points: D.pts }, "Split");
    } else {
      await edit(D.type, { points: D.pts, radius: S.brushR, target: D.target }, D.type === "erase" ? "" : "");
    }
    return;
  }
  if (Dg.mode === "handle") {
    renderLineInfo();
    if (S.line && S.line.purpose === "ticks") measureTicks();
    else if (S.tool === "ruler") { activateTab("calib"); $("#calLineCard").open = true; $("#lineReal").focus(); }
  }
});
cv.addEventListener("dblclick", e => {
  if (!S.cur) return;
  if (S.draw && (S.draw.type === "polygon" || S.draw.type === "roi-poly")) { finishPolygon(); return; }
  if (S.tool === "select") { const [ix, iy] = toImg(...evPos(e)); const o = objectAt(ix, iy); if (o) zoomTo(o); }
});
cv.addEventListener("pointerleave", () => { S.mouse = null; if (S.hover) { S.hover = null; } draw(); });

function startPinch() {
  const [a, b] = [...S.pointers.values()];
  S.drag = null; S.draw = null;
  S.pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), c: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], s: S.view.s, ox: S.view.ox, oy: S.view.oy };
}
function doPinch() {
  const [a, b] = [...S.pointers.values()], P = S.pinch;
  const d = Math.hypot(a[0] - b[0], a[1] - b[1]), c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const ns = Math.min(64, Math.max(0.02, P.s * d / P.d));
  const ix = (P.c[0] - P.ox) / P.s, iy = (P.c[1] - P.oy) / P.s;
  S.view.s = ns; S.view.ox = c[0] - ix * ns; S.view.oy = c[1] - iy * ns; draw();
}

function ellipsePts(e, n = 120) {
  const p = [];
  for (let k = 0; k < n; k++) { const t = 2 * Math.PI * k / n; p.push([e.cx + e.rx * Math.cos(t), e.cy + e.ry * Math.sin(t)]); }
  return p;
}
async function finishPolygon() {
  const D = S.draw;
  if (!D) return;
  S.draw = null;
  if (D.pts.length < 3) { draw(); toast("An outline needs at least 3 points", "warn"); return; }
  if (D.type === "roi-poly") await setRoi(D.pts);
  else await edit("polygon", { points: D.pts }, "Organoid added");
}
async function setRoi(points) {
  try {
    setPayload(await api(`/api/image/${encodeURIComponent(S.key)}/roi`, { roi: points ? { points } : null }));
    toast(points ? "Analysis region set – press “Detect in this image” to use it" : "Analysis region removed");
    setTool("select", true);
  } catch (e) { toast(e.message, "err"); }
}

function showHoverInfo(o) {
  $("#sbSel").textContent = o ? `#${o.number} · Ø ${fmtLen(o.eq_diam_px)} · ${fmtArea(o.area_px)} · circ ${o.circularity.toFixed(2)}${o.status !== "included" ? " · excluded: " + o.reason : ""}` : selText();
}
function selText() { return S.sel.size ? `${S.sel.size} selected` : ""; }
function updatePosStatus(ix, iy) {
  const inside = ix >= 0 && iy >= 0 && ix < S.cur.width && iy < S.cur.height;
  $("#sbPos").textContent = inside ? `x ${ix.toFixed(0)}  y ${iy.toFixed(0)} px` + (upp() ? `  (${fmtLen(ix)}, ${fmtLen(iy)})` : "") : "—";
}
function updateStatusCal() {
  const u = upp();
  $("#sbCal").textContent = u ? `Scale ${u.toPrecision(4)} µm/px (${S.cur.calibration.source})` : "No scale – pixels only";
  $("#calDot").classList.toggle("ok", !!u);
}

// ============================================================ selection & edits
function afterSelect() { renderSelBar(); renderObjects(true); showHoverInfo(null); draw(); }
function renderSelBar() {
  const n = S.sel.size;
  $("#selBar").hidden = n === 0;
  $("#selCount").textContent = n === 1 ? `#${objByLabel([...S.sel][0])?.number ?? ""}` : `${n} selected`;
  $("#selBar [data-act=merge]").disabled = n < 2;
  $("#selBar [data-act=split2]").disabled = n !== 1;
  $("#selBar [data-act=split3]").disabled = n !== 1;
  $("#sbSel").textContent = selText();
}
async function edit(op, body = {}, msg) {
  if (S.busy) return;
  S.busy = true;
  try {
    const d = await api(`/api/image/${encodeURIComponent(S.key)}/edit`, { op, ...body });
    setPayload(d);
    if (msg) toast(msg);
  } catch (e) { toast(e.message, "err", 5000); }
  finally { S.busy = false; draw(); }
}
async function act(a, extra) {
  const ids = [...S.sel];
  if (!ids.length && !["deselect"].includes(a)) return toast("Select an organoid first (click it)", "warn");
  if (a === "delete") { await edit("delete", { ids }); S.sel.clear(); renderSelBar(); toast(`${ids.length} deleted – Ctrl+Z to undo`); }
  else if (a === "include") await edit("include", { ids }, `${ids.length} included`);
  else if (a === "exclude") {
    let reason = extra || "Touching a neighbour";
    if (reason === "Other…") { reason = prompt("Reason for excluding:", ""); if (reason === null) return; reason = reason || "other"; }
    await edit("exclude", { ids, reason }, `${ids.length} excluded (${reason})`);
  }
  else if (a === "split2" || a === "split3") {
    if (ids.length !== 1) return toast("Select exactly one organoid to split", "warn");
    await edit("split_auto", { id: ids[0], n: a === "split2" ? 2 : 3 }, "Split – check the result; Ctrl+Z to undo, or use the Split line tool (S)");
  }
  else if (a === "merge") { await edit("merge", { ids }, "Merged"); }
  else if (a === "deselect") { S.sel.clear(); afterSelect(); }
}
function buildExcludeMenu(el, close) {
  el.innerHTML = EXCLUDE_REASONS.map(r => `<button data-r="${esc(r)}"><i data-i="ban"></i>${esc(r)}</button>`).join("");
  hydrateIcons(el);
  el.querySelectorAll("button").forEach(b => b.onclick = () => { close(); act("exclude", b.dataset.r); });
}
function openCtx(x, y, ix, iy) {
  const o = objectAt(ix, iy);
  if (o && !S.sel.has(o.label)) { S.sel.clear(); S.sel.add(o.label); afterSelect(); }
  const n = S.sel.size;
  const m = $("#ctx");
  if (!n) {
    m.innerHTML = `<button data-c="fit"><i data-i="fit"></i>Fit image<span class="k">F</span></button>
      <button data-c="ellipse"><i data-i="circle"></i>Add circle here<span class="k">C</span></button>
      <button data-c="selall"><i data-i="cursor"></i>Select all<span class="k">Ctrl+A</span></button>`;
  } else {
    m.innerHTML = `<button data-c="include"><i data-i="check"></i>Include<span class="k">I</span></button>
      ${EXCLUDE_REASONS.map(r => `<button data-c="exclude" data-r="${esc(r)}"><i data-i="ban"></i>Exclude: ${esc(r)}</button>`).join("")}
      <hr>
      ${n === 1 ? `<button data-c="split2"><i data-i="split"></i>Split into 2<span class="k">2</span></button><button data-c="split3"><i data-i="split"></i>Split into 3<span class="k">3</span></button>` : ""}
      ${n > 1 ? `<button data-c="merge"><i data-i="merge"></i>Merge ${n}<span class="k">M</span></button>` : ""}
      ${n === 1 ? `<button data-c="zoom"><i data-i="fit"></i>Zoom to<span class="k">dbl-click</span></button>` : ""}
      <hr><button data-c="delete" style="color:var(--danger)"><i data-i="trash"></i>Delete<span class="k">Del</span></button>`;
  }
  hydrateIcons(m);
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
  m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  m.querySelectorAll("button").forEach(b => b.onclick = () => {
    m.hidden = true;
    const c = b.dataset.c;
    if (c === "fit") fit();
    else if (c === "ellipse") setTool("ellipse");
    else if (c === "selall") selectAll();
    else if (c === "zoom") zoomTo(objByLabel([...S.sel][0]));
    else act(c, b.dataset.r);
  });
}
function selectAll() { S.cur.objects.forEach(o => (S.opt.excluded || o.status === "included") && S.sel.add(o.label)); afterSelect(); }
function closePops() { $("#ctx").hidden = true; $("#excludeMenu").classList.remove("open"); $("#layersPop").classList.remove("open"); }

// ============================================================ objects tab
function renderObjects(onlySel) {
  if (!S.cur) return;
  const objs = S.cur.objects;
  const inc = objs.filter(o => o.status === "included");
  $("#objBadge").textContent = inc.length;
  if (!onlySel) {
    const d = inc.map(o => o.eq_diam_px);
    const mean = d.length ? d.reduce((a, b) => a + b, 0) / d.length : null;
    const sd = d.length > 1 ? Math.sqrt(d.reduce((a, b) => a + (b - mean) ** 2, 0) / (d.length - 1)) : null;
    const med = d.length ? [...d].sort((a, b) => a - b)[Math.floor((d.length - 1) / 2)] : null;
    const flagged = inc.filter(o => o.flags.includes("possibly fused / irregular")).length;
    $("#stats").innerHTML = `
      <div class="stat"><div class="l">Included</div><div class="v">${inc.length}<small> / ${objs.length}</small></div></div>
      <div class="stat"><div class="l">Mean Ø</div><div class="v">${mean ? fmtLen(mean) : "—"}</div></div>
      <div class="stat"><div class="l">SD Ø</div><div class="v">${sd ? fmtLen(sd) : "—"}</div></div>
      <div class="stat"><div class="l">Median Ø</div><div class="v">${med ? fmtLen(med) : "—"}</div></div>
      ${flagged ? `<div class="stat" style="grid-column:span 2;border-color:rgba(251,146,60,.4)"><div class="l" style="color:var(--flag)">Possibly fused / irregular</div><div class="v">${flagged} <small>check with “Next flagged”</small></div></div>` : ""}
      ${!upp() ? `<div class="stat" style="grid-column:span 2;border-color:rgba(245,158,11,.4)"><div class="l" style="color:var(--warn)">No scale</div><div class="v"><small>Sizes in pixels – set the scale in the Scale tab</small></div></div>` : ""}`;
  }
  let rows = objs.filter(o => S.filter === "all" || (S.filter === "flagged" ? o.flags.length && o.status === "included" : o.status === S.filter));
  const k = S.sort.k, dir = S.sort.asc ? 1 : -1;
  rows.sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * dir);
  $$("#objTable th").forEach(th => { th.classList.toggle("sorted", th.dataset.k === k); th.classList.toggle("asc", th.dataset.k === k && S.sort.asc); });
  $("#objTable tbody").innerHTML = rows.map(o => `
    <tr data-l="${o.label}" class="${S.sel.has(o.label) ? "sel" : ""}">
      <td>${o.number}${o.flags.includes("possibly fused / irregular") ? '<span class="fl" title="possibly fused / irregular">?</span>' : ""}</td>
      <td>${fmtLen(o.eq_diam_px)}</td><td>${fmtArea(o.area_px)}</td><td>${o.circularity.toFixed(2)}</td>
      <td>${o.status === "included" ? '<span class="st inc">included</span>' : `<span class="st exc" title="${esc(o.reason)}">excluded</span>`}</td>
    </tr>`).join("") || `<tr><td colspan="5" class="hint" style="padding:1rem;text-align:center">${S.cur.segmented ? "Nothing here" : "Press “Detect in this image” first, or add organoids by hand with the circle tool (C)."}</td></tr>`;
  if (onlySel) { const r = $("#objTable tr.sel"); if (r) r.scrollIntoView({ block: "nearest" }); }
}
function nextFlagged() {
  const f = S.cur.objects.filter(o => o.status === "included" && o.flags.includes("possibly fused / irregular"));
  if (!f.length) return toast("No flagged organoids in this image", "", 2500);
  S.flagIdx = (S.flagIdx + 1) % f.length;
  const o = f[S.flagIdx];
  S.sel.clear(); S.sel.add(o.label); afterSelect(); zoomTo(o);
  $("#toolHint").textContent = `Flagged ${S.flagIdx + 1}/${f.length}: keep it (N = next), exclude (X), split (2) or delete (Del)`;
  $("#toolHint").classList.add("show"); clearTimeout(hintTimer); hintTimer = setTimeout(() => $("#toolHint").classList.remove("show"), 5000);
}

// ============================================================ detect tab
function loadSettingsIntoUi(s) {
  if (!s) return;
  setSeg("#method", s.method || "cellpose");
  const u = !!(S.cur && upp());
  const dv = s.diameter_um || s.diameter_px;
  $("#diamAuto").checked = !dv;
  $("#diamVal").value = dv || "";
  $("#diamUnit").value = s.diameter_um ? "um" : "px";
  $("#invert").checked = !!s.invert;
  const known = ["auto", "cpsam", "cyto3", "cyto2"];
  $("#cpModel").value = known.includes(s.model) ? s.model : "custom";
  $("#cpCustom").hidden = known.includes(s.model);
  $("#cpCustom").value = known.includes(s.model) ? "" : (s.model || "");
  $("#flow").value = s.flow_threshold; $("#flowVal").textContent = s.flow_threshold;
  $("#prob").value = s.cellprob_threshold; $("#probVal").textContent = s.cellprob_threshold;
  $("#maxSide").value = String(s.max_side || 0);
  $("#useGpu").checked = s.use_gpu !== false;
  $("#polarity").value = s.polarity || "auto";
  $("#smooth").value = s.smooth_px; $("#smoothVal").textContent = s.smooth_px;
  $("#splitTouching").checked = s.split_touching !== false;
  const fu = s.min_diameter_um || s.max_diameter_um ? "um" : "px";
  $("#filtUnit").value = fu;
  $("#minD").value = (fu === "um" ? s.min_diameter_um : s.min_diameter_px) || "";
  $("#maxD").value = (fu === "um" ? s.max_diameter_um : s.max_diameter_px) || "";
  $("#minCirc").value = s.min_circularity || 0; $("#circVal").textContent = s.min_circularity || 0;
  $("#exclBorder").checked = !!s.exclude_border;
  $("#autoWells").checked = !!s.auto_wells;
  updateMethodUi(); updateDiamUi();
}
function readSettings() {
  const num = id => { const v = parseFloat($(id).value); return isFinite(v) && v > 0 ? v : null; };
  const fu = $("#filtUnit").value, du = $("#diamUnit").value;
  const auto = $("#diamAuto").checked;
  const model = $("#cpModel").value === "custom" ? ($("#cpCustom").value.trim() || "auto") : $("#cpModel").value;
  return {
    method: segVal("#method"), model,
    diameter_um: !auto && du === "um" ? num("#diamVal") : null, diameter_px: !auto && du === "px" ? num("#diamVal") : null,
    invert: $("#invert").checked, flow_threshold: +$("#flow").value, cellprob_threshold: +$("#prob").value,
    max_side: +$("#maxSide").value, use_gpu: $("#useGpu").checked,
    polarity: $("#polarity").value, smooth_px: +$("#smooth").value, split_touching: $("#splitTouching").checked,
    min_diameter_um: fu === "um" ? num("#minD") : null, max_diameter_um: fu === "um" ? num("#maxD") : null,
    min_diameter_px: fu === "px" ? (num("#minD") || 0) : 0, max_diameter_px: fu === "px" ? num("#maxD") : null,
    min_circularity: +$("#minCirc").value, exclude_border: $("#exclBorder").checked, auto_wells: $("#autoWells").checked,
  };
}
function updateMethodUi() {
  const cp = segVal("#method") === "cellpose";
  $("#advCellpose").hidden = !cp; $("#advThreshold").hidden = cp; $("#cpStatus").hidden = !cp;
  $("#diamAuto").closest(".card").querySelector("h4").textContent = cp ? "Expected organoid size" : "Expected organoid size (used by filters only)";
}
function updateDiamUi() { $("#diamRow").style.display = $("#diamAuto").checked ? "none" : ""; }
function updateFilterUnits() {
  const u = !!upp();
  for (const sel of ["#filtUnit", "#diamUnit"]) {
    const o = $(sel).querySelector('option[value="um"]');
    o.disabled = !u; o.textContent = u ? "µm" : "µm (set scale first)";
    if (!u && $(sel).value === "um") $(sel).value = "px";
  }
}
let saveTimer = 0;
function settingsChanged() { clearTimeout(saveTimer); saveTimer = setTimeout(() => api("/api/settings", readSettings()).catch(() => {}), 500); }

async function detectThis() {
  if (!S.cur) return toast("Open an image first", "warn");
  const b = $("#btnDetect"); const old = b.innerHTML;
  b.disabled = true; b.innerHTML = `<span class="spin"></span>Detecting…`;
  const t0 = performance.now();
  try {
    const d = await api(`/api/image/${encodeURIComponent(S.key)}/segment`, { settings: readSettings() });
    S.sel.clear();
    setPayload(d);
    const n = d.objects.filter(o => o.status === "included").length;
    toast(`${n} organoids found in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    if (!n) toast("Nothing found – try another expected diameter, “Invert image”, or the Threshold method", "warn", 7000);
    activateTab("objects");
  } catch (e) { toast(e.message, "err", 9000); }
  finally { b.disabled = false; b.innerHTML = old; hydrateIcons(b); }
}
async function detectAll() {
  if (!S.state.images.length) return toast("Open a folder first", "warn");
  try {
    await api("/api/segment_all", { settings: readSettings(), only_new: $("#onlyNew").checked });
    pollJob();
  } catch (e) { toast(e.message, "err"); }
}
let jobTimer = 0;
function pollJob() {
  clearInterval(jobTimer);
  $("#job").hidden = false;
  jobTimer = setInterval(async () => {
    try {
      const j = await api("/api/job");
      $("#jobText").textContent = `${j.message} (${j.done}/${j.total})`;
      $("#jobBar").style.width = j.total ? `${100 * j.done / j.total}%` : "0";
      if (!j.running) {
        clearInterval(jobTimer);
        setTimeout(() => $("#job").hidden = true, 1500);
        await refreshState();
        if (S.key) { const d = await api(`/api/image/${encodeURIComponent(S.key)}/data`); setPayload(d); }
        toast(`Detection finished for ${j.total} image(s)` + (j.errors.length ? ` – ${j.errors.length} failed` : ""), j.errors.length ? "warn" : "");
        j.errors.forEach(er => toast(er, "err", 9000));
      }
    } catch { /* keep polling */ }
  }, 700);
}

// ============================================================ calibration tab
function renderCalibration() {
  const c = S.cur.calibration || {}, u = c.um_per_px;
  const box = $("#calNow");
  box.classList.toggle("none", !u);
  box.innerHTML = u
    ? `<h4>Current scale</h4><div class="big">${u.toPrecision(4)} <small>µm / pixel</small></div>
       <div class="hint">= ${(1000 / u).toPrecision(5)} px per mm · source: <b>${esc(c.source)}</b></div>
       <div class="hint">${esc(c.detail || "")}</div>`
    : `<h4>No scale yet</h4><div class="hint">Sizes are reported in pixels. Use one of the options below, then “Apply to all images” if they share the same scale.</div>`;
  const info = S.cur.info || {}, m = info.metadata_calibration, dpi = info.dpi_suggestion;
  let h = "";
  if (m) h += `<div class="status-line"><span class="ok">●</span>${esc(m.source)}: <b>${m.um_per_px.toPrecision(4)} µm/px</b></div><div class="hint">${esc(m.detail)}</div>
               <button class="btn sm" data-apply="meta:this">Use file metadata</button>`;
  if (dpi) h += `<div class="status-line"><span class="warn">●</span>Resolution tag: <b>${dpi.um_per_px.toPrecision(4)} µm/px</b></div><div class="hint">${esc(dpi.detail)}</div>
                 <div class="row"><button class="btn sm" data-apply="dpi:this">Use DPI</button><button class="btn sm ghost" data-apply="dpi:all">Use DPI for all</button></div>`;
  if (!m && !dpi) h += `<div class="hint">No pixel size stored in this file. This is normal for JPEG/PNG exports, screenshots and figures – use a ruler or scale bar instead.</div>`;
  if (info.notes && info.notes.length) h += `<div class="hint">${info.notes.map(esc).join("<br>")}</div>`;
  $("#metaBody").innerHTML = h;
  $("#metaBody").querySelectorAll("[data-apply]").forEach(b => b.onclick = () => applyCal(b.dataset.apply));
  $("#calMeta").open = !!(m || dpi) && !u;
  renderLineInfo();
}
function renderLineInfo() {
  const L = S.line;
  $("#lineLen").textContent = L && L.p0 ? `${Math.hypot(L.p1[0] - L.p0[0], L.p1[1] - L.p0[1]).toFixed(2)} px` : "— (draw a line)";
}
async function applyCal(spec) {
  if (!S.cur) return;
  const [kind, scope] = spec.split(":");
  let body;
  if (kind === "ps") {
    const v = parseFloat($("#psVal").value);
    if (!(v > 0)) return toast("Type the pixel size first", "warn");
    body = { method: "pixel_size", value: v, unit: $("#psUnit").value };
  } else if (kind === "line") {
    const L = S.line, v = parseFloat($("#lineReal").value);
    if (!L || !L.p0) return toast("Draw a line on the ruler / scale bar first", "warn");
    if (!(v > 0)) return toast("Type the real length of the line", "warn");
    body = { method: "line", p0: L.p0, p1: L.p1, length: v, unit: $("#lineUnit").value.replace("µ", "u"), what: S.line.what || "line on known object" };
  } else if (kind === "tick") {
    if (!S.tick) return toast("Draw a line along the ruler ticks first", "warn");
    const v = parseFloat($("#tickSpacing").value);
    if (!(v > 0)) return toast("Type the tick spacing", "warn");
    body = { method: "period", period_px: S.tick.period_px, spacing: v, unit: $("#tickUnit").value.replace("µ", "u"), n_ticks: S.tick.n_ticks };
  } else if (kind === "meta") body = { method: "metadata" };
  else if (kind === "dpi") body = { method: "dpi" };
  try {
    const d = await api(`/api/image/${encodeURIComponent(S.key)}/calibration`, body);
    setPayload(d);
    if (scope === "all") await applyToAll(d.calibration);
    else toast(`Scale set: ${d.calibration.um_per_px.toPrecision(4)} µm/px`);
  } catch (e) { toast(e.message, "err"); }
}
async function applyToAll(cal, mode = "all") {
  if (mode === "all" && !confirm(`Use ${cal.um_per_px.toPrecision(4)} µm/px for ALL ${S.state.images.length} images?\n(Images whose microscope metadata states a different pixel size will be overwritten too.)`)) return;
  const r = await api("/api/default_calibration", { calibration: cal, apply: mode, trust_dpi: $("#trustDpi").checked });
  S.state = r; renderList();
  toast(`Scale applied to ${r.applied_to} image(s)`);
}
async function findBars() {
  try {
    const r = await api(`/api/image/${encodeURIComponent(S.key)}/detect_scalebar`, {});
    S.cands = r.candidates;
    renderCands();
    if (!S.cands.length) toast("No scale bar found – draw a line along it instead", "warn");
    draw();
  } catch (e) { toast(e.message, "err"); }
}
function renderCands() {
  $("#barList").innerHTML = S.cands.map((c, k) => `<div class="cand" data-k="${k}"><span>Bar ${k + 1}: <b>${c.length_px.toFixed(0)} px</b> ${c.orientation}, ${c.polarity}</span><span>Use ›</span></div>`).join("");
  $("#barList").querySelectorAll(".cand").forEach(el => el.onclick = () => {
    const c = S.cands[+el.dataset.k];
    S.line = { p0: [...c.p0], p1: [...c.p1], purpose: "line", what: "scale bar (auto-detected)" };
    $$(".cand").forEach(x => x.classList.toggle("on", x === el));
    zoomTo({ bbox: c.bbox, cx: (c.p0[0] + c.p1[0]) / 2, cy: (c.p0[1] + c.p1[1]) / 2 }, 2.2);
    $("#calLineCard").open = true; renderLineInfo(); $("#lineReal").focus();
    toast("Type the length printed next to the bar, then Apply");
  });
}
async function measureTicks() {
  const L = S.line;
  try {
    S.tick = await api(`/api/image/${encodeURIComponent(S.key)}/ruler_period`, { p0: L.p0, p1: L.p1 });
    renderTick();
  } catch (e) { S.tick = null; renderTick(); toast(e.message, "err"); }
}
function renderTick() {
  const t = S.tick, sp = $("#tickSpark");
  $$('[data-apply^="tick"]').forEach(b => b.disabled = !t);
  if (!t) { $("#tickResult").textContent = ""; sp.style.display = "none"; return; }
  const q = t.strength > 0.5 ? "ok" : t.strength > 0.25 ? "warn" : "bad";
  $("#tickResult").innerHTML = `<span class="${q}">●</span> Tick period <b>${t.period_px.toFixed(3)} px</b> · ~${t.n_ticks} ticks · regularity ${(t.strength * 100).toFixed(0)}%${q === "bad" ? " – unreliable, redraw along the tick marks" : ""}`;
  sp.style.display = "block";
  const w = sp.width = sp.clientWidth * S.dpr, h = sp.height = 40 * S.dpr, c = sp.getContext("2d"), p = t.profile;
  const lo = Math.min(...p), hi = Math.max(...p);
  c.strokeStyle = COL.acc; c.lineWidth = 1.2 * S.dpr; c.beginPath();
  p.forEach((v, k) => { const x = k / (p.length - 1) * w, y = h - (v - lo) / (hi - lo || 1) * (h - 4) - 2; k ? c.lineTo(x, y) : c.moveTo(x, y); });
  c.stroke();
}
function renderPresets() {
  const ps = S.state.presets || [];
  $("#presetList").innerHTML = ps.length ? ps.map((p, k) => `<div class="preset"><span class="nm" title="${esc(p.calibration.detail || "")}">${esc(p.name)}</span>
     <span class="v">${p.calibration.um_per_px.toPrecision(4)} µm/px</span>
     <button class="btn sm" data-p="${k}" data-s="this">Use</button><button class="btn sm ghost" data-p="${k}" data-s="all">All</button>
     <button class="icon-btn sm" data-del="${k}" title="Delete"><i data-i="x"></i></button></div>`).join("")
    : `<div class="hint">Save a scale you use often (a scanner setting, an objective) to apply it with one click later.</div>`;
  hydrateIcons($("#presetList"));
  $("#presetList").querySelectorAll("[data-p]").forEach(b => b.onclick = async () => {
    const p = ps[+b.dataset.p];
    const cal = { ...p.calibration, source: `saved: ${p.name}` };
    try {
      if (b.dataset.s === "all") await applyToAll(cal);
      const d = await api(`/api/image/${encodeURIComponent(S.key)}/calibration`, { method: "preset", calibration: cal });
      setPayload(d); if (b.dataset.s !== "all") toast(`Scale “${p.name}” applied`);
    } catch (e) { toast(e.message, "err"); }
  });
  $("#presetList").querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm(`Delete saved scale “${ps[+b.dataset.del].name}”?`)) return;
    ps.splice(+b.dataset.del, 1); await api("/api/presets", { presets: ps }); renderPresets();
  });
}
async function savePreset() {
  const c = S.cur && S.cur.calibration;
  if (!c || !c.um_per_px) return toast("This image has no scale to save", "warn");
  const name = $("#presetName").value.trim() || `${c.um_per_px.toPrecision(4)} µm/px`;
  const ps = S.state.presets || [];
  ps.push({ name, calibration: c });
  await api("/api/presets", { presets: ps });
  S.state.presets = ps; $("#presetName").value = ""; renderPresets(); toast(`Saved “${name}”`);
}

// ============================================================ export
async function doExport(all) {
  if (!S.state.images.length) return toast("Nothing to export yet", "warn");
  try {
    const r = await api("/api/export", { keys: all ? null : [S.key], overlays: $("#expOverlay").checked, masks: $("#expMask").checked });
    const el = $("#exportResult"); el.hidden = false;
    el.innerHTML = r.files.length ? `<h4>Written to</h4><div class="path">${esc(r.folder)}</div><div class="file-list">${r.files.map(esc).join("<br>")}</div>` : `<div class="hint">No detected images to export yet.</div>`;
    toast(r.files.length ? `Exported ${r.files.filter(f => f.endsWith("_organoids.csv")).length} image(s) to ${r.folder}` : "Nothing detected yet", r.files.length ? "" : "warn", 6000);
    const noScale = S.state.images.filter(i => i.segmented && !(i.calibration && i.calibration.um_per_px)).length;
    if (noScale) toast(`${noScale} exported image(s) have no scale: their sizes are in pixels only`, "warn", 8000);
  } catch (e) { toast(e.message, "err"); }
}

// ============================================================ folder dialog
async function openFolderDialog() {
  $("#folderModal").hidden = false;
  const rec = store.get("recent", []);
  $("#fmRecent").innerHTML = rec.map(p => `<button class="chip" data-p="${esc(p)}" title="${esc(p)}">${esc(p.split(/[\\/]/).filter(Boolean).pop() || p)}</button>`).join("");
  $("#fmRecent").querySelectorAll("[data-p]").forEach(b => b.onclick = () => browse(b.dataset.p));
  await browse(S.state.input_dir || rec[0] || "");
}
async function browse(p) {
  try {
    const r = await api(`/api/browse?path=${encodeURIComponent(p || "")}`);
    S.fm = r;
    $("#fmPath").value = r.path;
    $("#fmList").innerHTML = r.dirs.map(d => `<div class="fm-item" data-p="${esc(d.path)}"><i data-i="folder"></i>${esc(d.name)}</div>`).join("") || `<div class="hint" style="padding:.6rem">No sub-folders</div>`;
    hydrateIcons($("#fmList"));
    $("#fmList").querySelectorAll(".fm-item").forEach(el => el.ondblclick = el.onclick = () => browse(el.dataset.p));
    $("#fmInfo").innerHTML = r.n_images ? `<b>${r.n_images} image(s)</b> here: ${r.examples.map(esc).join(", ")}${r.n_images > r.examples.length ? "…" : ""}` : "No images directly in this folder.";
  } catch (e) { toast(e.message, "err"); }
}
async function openFolder() {
  const p = $("#fmPath").value.trim();
  try {
    S.state = await api("/api/open", { input_dir: p, output_dir: $("#fmOut").value.trim() || null, recursive: $("#fmRecursive").checked });
    const rec = [p, ...store.get("recent", []).filter(x => x !== p)].slice(0, 6); store.set("recent", rec);
    $("#folderModal").hidden = true;
    await refreshState();
    if (S.state.images.length) await openImage(S.state.images[0].key);
    else { showEmpty(true); toast("No supported images in that folder", "warn"); }
  } catch (e) { toast(e.message, "err"); }
}

// ============================================================ upload (drag & drop / file picker)
async function uploadFiles(files) {
  files = [...files].filter(f => /\.(jpe?g|png|tiff?|bmp|webp|gif|czi|nd2|lif|lsm|stk|oib|vsi|ims)$/i.test(f.name));
  if (!files.length) return toast("No supported image files", "warn");
  $("#job").hidden = false;
  for (const [k, f] of files.entries()) {
    $("#jobText").textContent = `Adding ${f.name} (${k + 1}/${files.length})`; $("#jobBar").style.width = `${100 * (k + 1) / files.length}%`;
    try { await api(`/api/upload?name=${encodeURIComponent(f.name)}`, undefined, f); } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
  }
  $("#job").hidden = true;
  const had = S.key;
  await refreshState();
  toast(`${files.length} image(s) added to ${S.state.input_dir}`);
  if (!had && S.state.images.length) openImage(S.state.images[0].key);
}

// ============================================================ UI prefs
function applyUiPrefs() {
  const sc = store.get("uiScale", null);
  if (sc) document.documentElement.style.fontSize = `${15 * sc / 100}px`;
  $("#uiScale").value = sc || 100; $("#uiScaleVal").textContent = `${sc || 100}%`;
  const th = store.get("theme", "auto");
  if (th === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = th;
  setSeg("#themeSeg", th);
  $("#lineW").value = S.lineW; $("#lwVal").textContent = S.lineW;
  $("#optOutline").checked = S.opt.outline; $("#optFill").checked = S.opt.fill; $("#optNumbers").checked = S.opt.numbers; $("#optExcluded").checked = S.opt.excluded;
  $("#optOpacity").value = S.opt.opacity; $("#optBright").value = S.opt.bright; $("#optContrast").value = S.opt.contrast;
  $("#brushR").value = S.brushR; $("#brushRVal").textContent = `${S.brushR} px`;
  readColors();
}
function setSeg(sel, v) { $$(sel + " button").forEach(b => b.classList.toggle("on", b.dataset.v === v)); }
function segVal(sel) { return $(sel + " button.on")?.dataset.v; }
function activateTab(t) {
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  $$(".tab-body").forEach(b => b.hidden = b.dataset.tab !== t);
  if (innerWidth <= 1050) $("#right").classList.add("open");
  if (t === "objects") renderObjects();
}
function setBrush(r) { S.brushR = Math.max(1, Math.min(200, Math.round(r))); $("#brushR").value = S.brushR; $("#brushRVal").textContent = `${S.brushR} px`; store.set("brushR", S.brushR); draw(); }

// ============================================================ bind UI
function bindUi() {
  $("#btnFolder").onclick = $("#btnEmptyOpen").onclick = openFolderDialog;
  $("#btnAddFiles").onclick = $("#btnEmptyFiles").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = e => { uploadFiles(e.target.files); e.target.value = ""; };
  $("#btnDetectAll").onclick = $("#btnDetectAll2").onclick = detectAll;
  $("#btnDetect").onclick = detectThis;
  $("#btnExportAll").onclick = $("#btnExportAll2").onclick = () => doExport(true);
  $("#btnExportThis").onclick = () => doExport(false);
  $("#btnDownloadCsv").onclick = () => S.key && (location.href = `/api/image/${encodeURIComponent(S.key)}/objects.csv`);
  $("#btnReveal").onclick = () => api("/api/reveal", {}).then(r => !r.ok && toast("Could not open the file manager; the path is shown above", "warn"));
  $("#btnHelp").onclick = () => $("#helpModal").hidden = false;
  $("#btnSettings").onclick = () => $("#settingsModal").hidden = false;
  $$("[data-close]").forEach(b => b.onclick = () => b.closest(".modal").hidden = true);
  $$(".modal").forEach(m => m.addEventListener("pointerdown", e => { if (e.target === m) m.hidden = true; }));
  $("#btnToggleLeft").onclick = () => $("#left").classList.toggle("open");
  $("#btnToggleRight").onclick = () => $("#right").classList.toggle("open");
  $("#imgSearch").oninput = renderList;
  $("#imgList").onclick = e => { const it = e.target.closest(".img-item"); if (it) { openImage(it.dataset.key); if (innerWidth <= 760) $("#left").classList.remove("open"); } };
  $("#btnPrev").onclick = () => stepImage(-1); $("#btnNext").onclick = () => stepImage(1);
  $("#chkReviewed").onchange = async e => { try { setPayload(await api(`/api/image/${encodeURIComponent(S.key)}/reviewed`, { reviewed: e.target.checked })); } catch (er) { toast(er.message, "err"); } };

  $$(".toolbar [data-tool]").forEach(b => b.onclick = () => { S.linePurpose = "line"; setTool(b.dataset.tool); });
  $("#btnUndo").onclick = undo; $("#btnRedo").onclick = redo;
  $("#brushR").oninput = e => setBrush(+e.target.value);
  $$("#selBar [data-act]").forEach(b => b.onclick = () => act(b.dataset.act));
  $("#btnExcludeMenu").onclick = e => { e.stopPropagation(); const m = $("#excludeMenu"); buildExcludeMenu(m, () => m.classList.remove("open")); m.classList.toggle("open"); };

  $("#btnZoomIn").onclick = () => zoomAt(1.25); $("#btnZoomOut").onclick = () => zoomAt(0.8);
  $("#btnFit").onclick = fit; $("#zoomVal").onclick = () => setZoom(1);
  $("#btnLayers").onclick = e => { e.stopPropagation(); $("#layersPop").classList.toggle("open"); };
  $("#layersPop").addEventListener("pointerdown", e => e.stopPropagation());
  const optMap = { optOutline: "outline", optFill: "fill", optNumbers: "numbers", optExcluded: "excluded" };
  for (const [id, k] of Object.entries(optMap)) $("#" + id).onchange = e => { S.opt[k] = e.target.checked; store.set("opt", S.opt); draw(); };
  for (const [id, k] of Object.entries({ optOpacity: "opacity", optBright: "bright", optContrast: "contrast" })) $("#" + id).oninput = e => { S.opt[k] = +e.target.value; store.set("opt", S.opt); draw(); };

  $$("#tabs button").forEach(b => b.onclick = () => activateTab(b.dataset.tab));
  $$("#method button").forEach(b => b.onclick = () => { setSeg("#method", b.dataset.v); updateMethodUi(); settingsChanged(); });
  $("#diamAuto").onchange = () => { updateDiamUi(); settingsChanged(); };
  $("#cpModel").onchange = () => { $("#cpCustom").hidden = $("#cpModel").value !== "custom"; settingsChanged(); };
  for (const [id, out] of [["flow", "flowVal"], ["prob", "probVal"], ["smooth", "smoothVal"], ["minCirc", "circVal"]]) $("#" + id).addEventListener("input", e => $("#" + out).textContent = e.target.value);
  $$('section[data-tab="detect"] input, section[data-tab="detect"] select').forEach(el => el.addEventListener("change", settingsChanged));
  $("#btnRoi").onclick = () => setTool("roi");
  $("#btnRoiClear").onclick = () => setRoi(null);

  $$("[data-apply]").forEach(b => b.onclick = () => applyCal(b.dataset.apply));
  $("#btnDrawLine").onclick = () => { S.linePurpose = "line"; setTool("ruler"); };
  $("#btnDrawTicks").onclick = () => { S.linePurpose = "ticks"; setTool("ruler"); $("#toolHint").textContent = "Drag along the ruler across many tick marks"; };
  $("#btnFindBar").onclick = findBars;
  $("#btnSavePreset").onclick = savePreset;
  $("#trustDpi").onchange = e => api("/api/default_calibration", { calibration: S.state.default_calibration, apply: "none", trust_dpi: e.target.checked }).then(() => toast(e.target.checked ? "DPI tags will be used for newly opened images" : "DPI tags will be ignored"));
  $("#btnCalClear").onclick = async () => { setPayload(await api(`/api/image/${encodeURIComponent(S.key)}/calibration`, { method: "none" })); };
  $("#psVal").onkeydown = e => e.key === "Enter" && applyCal("ps:this");
  $("#lineReal").onkeydown = e => e.key === "Enter" && applyCal("line:this");

  $("#btnNextFlag").onclick = nextFlagged;
  $("#btnExclFlag").onclick = () => { const ids = S.cur.objects.filter(o => o.status === "included" && o.flags.includes("possibly fused / irregular")).map(o => o.label); ids.length ? edit("exclude", { ids, reason: "Fused organoids" }, `${ids.length} excluded`) : toast("None flagged"); };
  $("#btnExclEdge").onclick = () => { const ids = S.cur.objects.filter(o => o.status === "included" && o.touches_border).map(o => o.label); ids.length ? edit("exclude", { ids, reason: "Cut by image edge" }, `${ids.length} excluded`) : toast("None touch the edge"); };
  $("#btnClearAll").onclick = () => { if (S.cur.objects.length && confirm("Remove all organoids from this image? (Ctrl+Z undoes it)")) { S.sel.clear(); edit("clear", {}, "All removed – Ctrl+Z to undo"); } };
  $$("#objFilter button").forEach(b => b.onclick = () => { setSeg("#objFilter", b.dataset.v); S.filter = b.dataset.v; renderObjects(); });
  $$("#objTable th").forEach(th => th.onclick = () => { const k = th.dataset.k; S.sort = { k, asc: S.sort.k === k ? !S.sort.asc : true }; renderObjects(); });
  $("#objTable tbody").onclick = e => {
    const tr = e.target.closest("tr[data-l]"); if (!tr) return;
    const l = +tr.dataset.l;
    if (e.shiftKey || e.ctrlKey || e.metaKey) S.sel.has(l) ? S.sel.delete(l) : S.sel.add(l); else { S.sel.clear(); S.sel.add(l); }
    afterSelect(); centerOn(objByLabel(l));
  };
  $("#objTable tbody").ondblclick = e => { const tr = e.target.closest("tr[data-l]"); if (tr) zoomTo(objByLabel(+tr.dataset.l)); };

  $("#fmGo").onclick = () => browse($("#fmPath").value);
  $("#fmPath").onkeydown = e => e.key === "Enter" && browse($("#fmPath").value);
  $("#fmUp").onclick = () => S.fm && S.fm.parent != null && browse(S.fm.parent);
  $("#fmHome").onclick = () => browse(S.fm && S.fm.home || "");
  $("#fmOpen").onclick = openFolder;

  $("#uiScale").oninput = e => { const v = +e.target.value; document.documentElement.style.fontSize = `${15 * v / 100}px`; $("#uiScaleVal").textContent = `${v}%`; store.set("uiScale", v); };
  $$("#themeSeg button").forEach(b => b.onclick = () => { store.set("theme", b.dataset.v); applyUiPrefs(); draw(); });
  $("#lineW").oninput = e => { S.lineW = +e.target.value; $("#lwVal").textContent = S.lineW; store.set("lineW", S.lineW); draw(); };
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); draw(); });

  document.addEventListener("pointerdown", e => { if (!e.target.closest(".ctx, .menu, .menu-wrap, .layers")) closePops(); });
  window.addEventListener("resize", () => { S.dpr = window.devicePixelRatio || 1; resize(); });

  // drag & drop files
  let dragN = 0;
  window.addEventListener("dragenter", e => { if (e.dataTransfer.types.includes("Files")) { dragN++; $("#drop").classList.add("show"); } });
  window.addEventListener("dragleave", () => { if (--dragN <= 0) { dragN = 0; $("#drop").classList.remove("show"); } });
  window.addEventListener("dragover", e => e.preventDefault());
  window.addEventListener("drop", e => { e.preventDefault(); dragN = 0; $("#drop").classList.remove("show"); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });

  document.addEventListener("keydown", onKey);
  document.addEventListener("keyup", e => { if (e.code === "Space") { S.spaceDown = false; if (!S.drag) setTool(S.tool, true); } });
}
async function stepImage(d) {
  const imgs = S.state.images; if (!imgs.length) return;
  const i = imgs.findIndex(x => x.key === S.key);
  const j = Math.max(0, Math.min(imgs.length - 1, i + d));
  if (j !== i) openImage(imgs[j].key);
}
async function undo() { try { setPayload(await api(`/api/image/${encodeURIComponent(S.key)}/undo`, {})); } catch (e) { toast(e.message, "err"); } }
async function redo() { try { setPayload(await api(`/api/image/${encodeURIComponent(S.key)}/redo`, {})); } catch (e) { toast(e.message, "err"); } }

function onKey(e) {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") { if (e.key === "Escape") e.target.blur(); return; }
  if (!$("#folderModal").hidden || !$("#helpModal").hidden || !$("#settingsModal").hidden) { if (e.key === "Escape") $$(".modal").forEach(m => m.hidden = true); return; }
  const k = e.key, mod = e.ctrlKey || e.metaKey;
  if (mod && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && k.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
  if (mod && k.toLowerCase() === "a") { e.preventDefault(); if (S.cur) selectAll(); return; }
  if (mod) return;
  if (k === "?") { $("#helpModal").hidden = false; return; }
  if (!S.cur) return;
  if (e.code === "Space") { e.preventDefault(); if (!S.spaceDown) { S.spaceDown = true; cv.style.cursor = "grab"; } return; }
  const tools = { v: "select", h: "pan", c: "ellipse", p: "polygon", b: "brush", e: "erase", s: "split", r: "ruler", o: "roi" };
  if (tools[k.toLowerCase()] && !e.altKey) { S.linePurpose = "line"; setTool(tools[k.toLowerCase()]); return; }
  switch (k) {
    case "Escape":
      if (S.draw) { S.draw = null; draw(); } else if (S.sel.size) { S.sel.clear(); afterSelect(); } else if (S.tool !== "select") setTool("select"); closePops(); break;
    case "Enter": if (S.draw && (S.draw.type === "polygon" || S.draw.type === "roi-poly")) finishPolygon(); break;
    case "Backspace": if (S.draw && S.draw.pts && S.draw.pts.length) { S.draw.pts.pop(); draw(); } else if (S.sel.size) act("delete"); e.preventDefault(); break;
    case "Delete": act("delete"); break;
    case "x": case "X": if (S.sel.size) act("exclude", "Touching a neighbour"); break;
    case "i": case "I": if (S.sel.size) act("include"); break;
    case "m": case "M": act("merge"); break;
    case "2": act("split2"); break;
    case "3": act("split3"); break;
    case "n": case "N": nextFlagged(); break;
    case "f": case "F": fit(); break;
    case "1": setZoom(1); break;
    case "+": case "=": zoomAt(1.25); break;
    case "-": case "_": zoomAt(0.8); break;
    case "[": setBrush(S.brushR / 1.25); break;
    case "]": setBrush(S.brushR * 1.25 + 1); break;
    case "Tab": e.preventDefault(); S.overlayHidden = !S.overlayHidden; draw(); break;
    case "PageDown": e.preventDefault(); stepImage(1); break;
    case "PageUp": e.preventDefault(); stepImage(-1); break;
    case "ArrowRight": if (e.altKey) stepImage(1); else { S.view.ox -= 60; draw(); } break;
    case "ArrowLeft": if (e.altKey) stepImage(-1); else { S.view.ox += 60; draw(); } break;
    case "ArrowUp": S.view.oy += 60; draw(); break;
    case "ArrowDown": S.view.oy -= 60; draw(); break;
  }
}

boot().catch(e => toast(`Start-up failed: ${e.message}`, "err", 10000));
