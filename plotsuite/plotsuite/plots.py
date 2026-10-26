"""Matplotlib rendering. Every drawer takes (ax, dataset, graph params, Style, group-style callback)."""
from __future__ import annotations

import io
import re
from typing import Callable

import matplotlib as mpl
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import scipy.stats as st
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.ticker import AutoMinorLocator, FormatStrFormatter, MultipleLocator

from .data import Dataset
from .style import Style

GStyle = Callable[[str], dict]


# ------------------------------------------------------------------ helpers
def font_family(name: str) -> str:
    try:
        font_manager.findfont(name, fallback_to_default=False)
        return name
    except Exception:
        return "DejaVu Sans"


def rc_for(style: Style) -> dict:
    return {"font.family": font_family(style.font_family), "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
            "axes.unicode_minus": False, "hatch.linewidth": 1.0}


def darker(c, f=0.6):
    r, g, b = mcolors.to_rgb(c)
    return (r * f, g * f, b * f)


def center_err(v: np.ndarray, center: str, error: str):
    c = float(np.median(v) if center == "median" else np.mean(v))
    n = len(v)
    if error == "none" or n < 2:
        return c, c, c
    if error == "SD":
        s = v.std(ddof=1); return c, c - s, c + s
    if error == "SEM":
        s = v.std(ddof=1) / np.sqrt(n); return c, c - s, c + s
    if error == "CI95":
        s = st.t.ppf(.975, n - 1) * v.std(ddof=1) / np.sqrt(n); return c, c - s, c + s
    if error == "range":
        return c, v.min(), v.max()
    if error == "IQR":
        return c, np.percentile(v, 25), np.percentile(v, 75)
    raise ValueError(error)


def swarm_offsets(v: np.ndarray, spread: float, nbins: int = 25) -> np.ndarray:
    off = np.zeros(len(v))
    if np.ptp(v) == 0:
        edges = np.array([v.min() - .5, v.max() + .5])
    else:
        edges = np.linspace(v.min(), v.max(), nbins + 1)
    idx = np.clip(np.digitize(v, edges) - 1, 0, len(edges) - 2)
    step = spread / max(np.bincount(idx).max() / 2, 1)
    for b in np.unique(idx):
        m = np.where(idx == b)[0]
        k = len(m)
        pos = (np.arange(k) - (k - 1) / 2) * step
        off[m] = pos
    return np.clip(off, -spread, spread)


def edge_color(style: Style, col):
    return {"black": "black", "white": "white", "match": col, "darker": darker(col)}[style.point_edge]


def fmt_p_label(p: float, style: Style) -> str:
    from .stats import stars, fmt_p
    s = stars(p)
    if s == "ns" and not style.sig_ns and style.sig_label == "stars":
        return ""
    if style.sig_label == "stars":
        return s
    pl = ("p " + fmt_p(p)) if style.sig_label == "p-value" else None
    return pl if pl else f"{s}\n{fmt_p(p)}"


def draw_brackets(ax, comps, xpos: dict, style: Style, y_start: float, rng: float):
    """comps: list of Comparison; xpos: name -> x. Stacks brackets by span so they never overlap."""
    items = []
    for c in comps:
        if c.a in xpos and c.b in xpos and c.a != c.b:
            if style.sig_only_significant and c.p >= 0.05:
                continue
            xa, xb = sorted((xpos[c.a], xpos[c.b]))
            items.append((xb - xa, xa, xb, c))
    items.sort(key=lambda t: (t[0], t[1]))
    tiers: list[list[tuple]] = []  # greedy stacking: each tier holds non-overlapping spans
    for it in items:
        for t in tiers:
            if all(it[2] < o[1] - 1e-9 or it[1] > o[2] + 1e-9 for o in t):
                t.append(it); break
        else:
            tiers.append([it])
    top = y_start
    tick = style.sig_step * rng * 0.35
    for ti, tier in enumerate(tiers):
        y = y_start + ti * style.sig_step * rng
        for _, xa, xb, c in tier:
            ax.plot([xa, xa, xb, xb], [y, y + tick, y + tick, y], color="black", lw=style.sig_lw, clip_on=False, solid_capstyle="butt")
            lab = fmt_p_label(c.p, style)
            if lab:
                gap = tick * 0.8 if lab.strip("*") == "" else tick * 0.8 + 0.012 * rng  # asterisks sit high in the glyph box; words need a little air
                ax.text((xa + xb) / 2, y + gap, lab, ha="center", va="bottom", fontsize=style.sig_size, linespacing=0.9, fontweight="normal")
        top = y + tick + style.sig_step * rng * (1.6 if style.sig_label == "both" else 1.0)
    return top


def finish_axes(ax, style: Style, xlabel="", ylabel="", categorical=False):
    for s in ("top", "right"):
        ax.spines[s].set_visible(not style.hide_top_right)
    for s in ax.spines.values():
        s.set_linewidth(style.spine_width)
    if style.spine_offset:
        ax.spines["left"].set_position(("outward", style.spine_offset)); ax.spines["bottom"].set_position(("outward", style.spine_offset))
    ax.tick_params(direction=style.tick_dir, length=style.tick_len, width=style.tick_width, labelsize=style.tick_size)
    fw = "bold" if style.bold_labels else "normal"
    ax.set_xlabel(style.x_label or xlabel, fontsize=style.label_size, fontweight=fw)
    ax.set_ylabel(style.y_label or ylabel, fontsize=style.label_size, fontweight=fw)
    if style.title:
        ax.set_title(style.title, fontsize=style.title_size, fontweight=fw)
    ax.set_yscale(style.y_scale)
    if not categorical:
        ax.set_xscale(style.x_scale)
    lo, hi = ax.get_ylim()
    ax.set_ylim(style.y_min if style.y_min is not None else lo, style.y_max if style.y_max is not None else hi)
    if not categorical and (style.x_min is not None or style.x_max is not None):
        l, h = ax.get_xlim()
        ax.set_xlim(style.x_min if style.x_min is not None else l, style.x_max if style.x_max is not None else h)
    if style.y_major_step and style.y_scale == "linear":
        ax.yaxis.set_major_locator(MultipleLocator(style.y_major_step))
    if style.x_major_step and not categorical and style.x_scale == "linear":
        ax.xaxis.set_major_locator(MultipleLocator(style.x_major_step))
    if style.y_decimals is not None:
        ax.yaxis.set_major_formatter(FormatStrFormatter(f"%.{int(style.y_decimals)}f"))
    if style.minor_ticks:
        if style.y_scale == "linear":
            ax.yaxis.set_minor_locator(AutoMinorLocator())
        if not categorical and style.x_scale == "linear":
            ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.tick_params(which="minor", direction=style.tick_dir, length=style.tick_len * .55, width=style.tick_width * .8)
    if style.grid != "none":
        ax.grid(True, axis={"y": "y", "x": "x", "both": "both"}[style.grid], alpha=style.grid_alpha, lw=.6)
        ax.set_axisbelow(True)
    if style.x_tick_rotation:
        for l in ax.get_xticklabels():
            l.set_rotation(style.x_tick_rotation); l.set_ha("right" if style.x_tick_rotation < 80 else "center")
    ax.patch.set_alpha(0)


def add_legend(ax, handles, style: Style):
    if style.legend_loc == "none" or not handles:
        return
    kw = dict(fontsize=style.legend_size, frameon=style.legend_frame, handlelength=1.2)
    if style.legend_loc == "outside right":
        ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.02, .5), **kw)
    else:
        ax.legend(handles=handles, loc=style.legend_loc, **kw)


def patch_handle(color, style, label, marker=None):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    if marker:
        return Line2D([], [], color=color, marker=marker, ms=5, label=label, lw=0)
    return Patch(facecolor=color if style.bar_filled else "none", edgecolor=darker(color), label=label, alpha=1)


def fill_kw(style: Style, col, hatch=None):
    h = hatch if hatch is not None else style.bar_hatch
    return dict(facecolor=mcolors.to_rgba(col, style.fill_alpha) if style.bar_filled else "none", edgecolor=darker(col) if style.bar_filled else col,
                linewidth=style.bar_edge_width, hatch=h or None)


# ------------------------------------------------------------------ column graph
def box_stats(v, rule, notch):
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    iqr = q3 - q1
    if rule.startswith("1.5"):
        lo = v[v >= q1 - 1.5 * iqr].min(); hi = v[v <= q3 + 1.5 * iqr].max()
        fl = v[(v < lo) | (v > hi)]
    elif rule.startswith("min"):
        lo, hi, fl = v.min(), v.max(), np.array([])
    else:
        lo, hi = np.percentile(v, [5, 95]); fl = v[(v < lo) | (v > hi)]
    d = dict(med=med, q1=q1, q3=q3, whislo=lo, whishi=hi, fliers=fl)
    if notch:
        d.update(cilo=med - 1.57 * iqr / np.sqrt(len(v)), cihi=med + 1.57 * iqr / np.sqrt(len(v)))
    return d


def draw_column(ax, ds: Dataset, params: dict, style: Style, gs: GStyle, comps=None, rng_seed=0):
    g = ds.group_arrays()
    names = [n for n in (params.get("groups") or list(g)) if n in g]
    if not names:
        raise ValueError("No groups to plot.")
    xpos = {n: i for i, n in enumerate(names)}
    all_v = np.concatenate([g[n] for n in names])
    handles = []
    for i, n in enumerate(names):
        v = g[n]; s = gs(n); col = s["color"]; hatch = s.get("hatch")
        c, lo, hi = center_err(v, "mean" if style.center == "none" else style.center, style.error)
        w = style.bar_width
        if style.column_mode == "bar":
            ax.bar(i, c, width=w, zorder=2, **fill_kw(style, col, hatch))
        elif style.column_mode == "box":
            st_ = box_stats(v, style.whisker_rule, style.box_notch)
            bp = ax.bxp([st_], positions=[i], widths=w, patch_artist=True, showfliers=style.show_fliers, shownotches=style.box_notch,
                        boxprops=dict(), medianprops=dict(color=darker(col), lw=style.center_lw), whiskerprops=dict(color=darker(col), lw=style.error_lw),
                        capprops=dict(color=darker(col), lw=style.error_lw), flierprops=dict(marker=s.get("marker") or style.point_marker, ms=3, mfc="none", mec=darker(col)),
                        capwidths=w * .4)
            for b in bp["boxes"]:
                b.set(**fill_kw(style, col, hatch)); b.set_zorder(2)
        elif style.column_mode == "violin":
            if len(v) < 3 or np.ptp(v) == 0:
                raise ValueError(f"Violin plot needs >=3 distinct values per group ('{n}').")
            vp = ax.violinplot([v], positions=[i], widths=w, showextrema=False, bw_method=style.violin_bw)
            for b in vp["bodies"]:
                b.set(**{k: v_ for k, v_ in fill_kw(style, col, hatch).items() if k != "hatch"}); b.set_alpha(None); b.set_zorder(2)
            q1, q2, q3 = np.percentile(v, [25, 50, 75])
            if style.violin_inner == "quartiles":
                for q, ls in ((q1, ":"), (q2, "-"), (q3, ":")):
                    ax.plot([i - w * .18, i + w * .18], [q, q], color=darker(col), lw=style.center_lw * (1 if q == q2 else .8), ls=ls, zorder=3)
            elif style.violin_inner == "box":
                ax.plot([i, i], [v.min(), v.max()], color=darker(col), lw=.8, zorder=3)
                ax.plot([i, i], [q1, q3], color=darker(col), lw=style.center_lw * 2, zorder=3, solid_capstyle="butt")
                ax.plot([i], [q2], "o", color="white", ms=3, zorder=4)
        if style.column_mode in ("bar", "points only"):
            if style.error != "none" and len(v) > 1:
                ax.errorbar(i, c, yerr=[[c - lo], [hi - c]], color=darker(col, .5) if style.column_mode == "bar" else darker(col), capsize=style.cap_size,
                            capthick=style.error_lw, elinewidth=style.error_lw, zorder=5, fmt="none")
            if style.column_mode == "points only" and style.center != "none":
                ax.plot([i - w * style.center_width / 2, i + w * style.center_width / 2], [c, c], color=darker(col, .5), lw=style.center_lw, zorder=5, solid_capstyle="butt")
        handles.append(patch_handle(col, style, n))
    # points
    paired = ds.paired and style.paired_lines and len(names) > 1 and style.show_points
    if style.show_points:
        rng = np.random.default_rng(rng_seed)
        if paired:
            m = ds.numeric(names).dropna()
            for _, row in m.iterrows():
                ax.plot([xpos[n] for n in names], row.to_numpy(), color="0.5", lw=.7, alpha=.7, zorder=3)
        for i, n in enumerate(names):
            v = g[n]; s = gs(n)
            if paired:
                xs = np.full(len(ds.numeric(names).dropna()), i, float); vv = ds.numeric(names).dropna()[n].to_numpy()
            else:
                vv = v
                if style.point_layout == "jitter":
                    xs = i + rng.uniform(-style.jitter_width, style.jitter_width, len(v))
                elif style.point_layout == "swarm":
                    xs = i + swarm_offsets(v, style.jitter_width)
                else:
                    xs = np.full(len(v), float(i))
            ax.scatter(xs, vv, s=style.point_size, marker=s.get("marker") or style.point_marker, facecolor=mcolors.to_rgba(s["color"], style.point_alpha),
                       edgecolor=edge_color(style, s["color"]), linewidth=style.point_edge_width, zorder=4, clip_on=False)
    ax.set_xticks(range(len(names))); ax.set_xticklabels(names)
    ax.set_xlim(-0.6, len(names) - 0.4)
    top_data = max(float(all_v.max()), max(center_err(g[n], "mean", style.error)[2] if style.error != "none" else g[n].mean() for n in names))
    lo_data = min(0.0, float(all_v.min())) if style.column_mode == "bar" and all_v.min() > -np.inf and style.y_scale == "linear" else float(all_v.min())
    pad = (top_data - lo_data) * 0.06 or 1
    ax.set_ylim(lo_data if style.column_mode == "bar" else lo_data - pad, top_data + pad)
    if comps:
        lo, hi = ax.get_ylim()
        base = hi - pad * 0.3 if False else top_data + pad * 0.3
        top = draw_brackets(ax, comps, xpos, style, base, hi - lo)
        ax.set_ylim(lo, max(hi, top))
    finish_axes(ax, style, "", "", categorical=True)
    add_legend(ax, handles, style)
    return xpos


# ------------------------------------------------------------------ grouped graph
def _key(v):
    try:
        return (0, float(v))
    except (TypeError, ValueError):
        return (1, str(v))


def draw_grouped(ax, ds: Dataset, params: dict, style: Style, gs: GStyle, comps=None):
    t = ds.long()
    xf, hf = ("a", "b") if params.get("x_factor", "a") == "a" else ("b", "a")
    if hf not in t:
        t["_h"] = ds.roles.get("value", "value"); hf = "_h"
    xl = sorted(t[xf].unique(), key=_key); hl = sorted(t[hf].unique(), key=_key)
    nh = len(hl)
    gap = style.group_gap
    bw = (1 - gap) / nh if style.grouped_mode == "bars" else 0.0
    handles = []
    pos = {}
    ymax = -np.inf; ymin = np.inf
    rng = np.random.default_rng(0)
    for j, h in enumerate(hl):
        s = gs(str(h)); col = s["color"]
        xs, cs, los, his = [], [], [], []
        for i, x in enumerate(xl):
            v = t[(t[xf] == x) & (t[hf] == h)]["value"].to_numpy()
            if len(v) == 0:
                continue
            c, lo, hi = center_err(v, "mean" if style.center == "none" else style.center, style.error)
            xc = i + ((j - (nh - 1) / 2) * bw if style.grouped_mode == "bars" else 0)
            pos[(str(x), str(h))] = xc
            xs.append(xc); cs.append(c); los.append(lo); his.append(hi)
            ymax = max(ymax, hi, v.max() if style.show_points else hi); ymin = min(ymin, lo, v.min() if style.show_points else lo)
            if style.show_points:
                off = rng.uniform(-bw * .3, bw * .3, len(v)) if style.grouped_mode == "bars" else rng.uniform(-.06, .06, len(v))
                ax.scatter(xc + off, v, s=style.point_size * .7, marker=s.get("marker") or style.point_marker, facecolor=mcolors.to_rgba(col, style.point_alpha),
                           edgecolor=edge_color(style, col), linewidth=style.point_edge_width, zorder=4)
        if style.grouped_mode == "bars":
            ax.bar(xs, cs, width=bw * .95, zorder=2, **fill_kw(style, col, s.get("hatch")))
            if style.error != "none":
                ax.errorbar(xs, cs, yerr=[np.array(cs) - los, np.array(his) - cs], fmt="none", color=darker(col, .5), capsize=style.cap_size,
                            capthick=style.error_lw, elinewidth=style.error_lw, zorder=5)
            handles.append(patch_handle(col, style, str(h)))
        else:
            ax.errorbar(xs, cs, yerr=None if style.error == "none" else [np.array(cs) - los, np.array(his) - cs], color=col, marker=s.get("marker") or style.point_marker,
                        ms=style.marker_size, lw=style.line_width, ls=style.line_style, capsize=style.cap_size, elinewidth=style.error_lw, capthick=style.error_lw, zorder=3)
            handles.append(patch_handle(col, style, str(h), marker=s.get("marker") or style.point_marker))
    ax.set_xticks(range(len(xl))); ax.set_xticklabels([str(x) for x in xl])
    ax.set_xlim(-0.6, len(xl) - 0.4)
    pad = (ymax - min(ymin, 0)) * 0.06 or 1
    ax.set_ylim(min(0, ymin) if style.grouped_mode == "bars" else ymin - pad, ymax + pad)
    if comps:
        lo, hi = ax.get_ylim()
        xfname = ds.roles.get("a" if xf == "a" else "b")
        sel = []
        from .stats import Comparison
        for c in comps:
            m = re.fullmatch(r"(.+?)=(.+)", c.label or "")
            if m and m.group(1) == xfname:
                lvl = m.group(2)
                if (lvl, c.a) in pos and (lvl, c.b) in pos:
                    sel.append((lvl, c))
        for lvl in dict.fromkeys(l for l, _ in sel):
            cs_ = [Comparison(f"{lvl}|{c.a}", f"{lvl}|{c.b}", c.p) for l, c in sel if l == lvl]
            xp = {f"{lvl}|{h}": pos[(lvl, str(h))] for h in hl if (lvl, str(h)) in pos}
            top = draw_brackets(ax, cs_, xp, style, ymax + pad * .2, hi - lo)
            ax.set_ylim(lo, max(ax.get_ylim()[1], top))
    finish_axes(ax, style, ds.roles.get(xf, ""), "", categorical=True)
    add_legend(ax, handles, style)


# ------------------------------------------------------------------ XY graph
def draw_xy(ax, ds: Dataset, params: dict, style: Style, gs: GStyle, results=None):
    series = ds.xy_series()
    names = [n for n in (params.get("series") or list(series)) if n in series]
    handles = []
    for n in names:
        t = series[n]; s = gs(n); col = s["color"]; mk = s.get("marker") or style.point_marker
        grp = t.groupby("x")["y"]
        has_rep = (grp.size() > 1).any()
        if has_rep:
            agg = grp.apply(lambda v: pd.Series(dict(zip(("c", "lo", "hi"), center_err(v.to_numpy(), "mean", style.xy_error if len(v) > 1 else "none"))))).unstack()
            xs, cs = agg.index.to_numpy(), agg["c"].to_numpy()
            ax.errorbar(xs, cs, yerr=None if style.xy_error == "none" else [cs - agg["lo"], agg["hi"] - cs], color=col, marker=mk, ms=style.marker_size,
                        ls=style.line_style if style.connect_points else "none", lw=style.line_width, capsize=style.cap_size, elinewidth=style.error_lw,
                        capthick=style.error_lw, mfc=col, mec=edge_color(style, col), zorder=3)
            if style.show_replicate_points:
                ax.scatter(t.x, t.y, s=style.point_size * .5, color=col, alpha=.35, zorder=2)
        else:
            tt = t.sort_values("x")
            ax.plot(tt.x, tt.y, color=col, marker=mk, ms=style.marker_size, ls=style.line_style if style.connect_points else "none", lw=style.line_width,
                    mfc=col, mec=edge_color(style, col), zorder=3)
        handles.append(patch_handle(col, style, n, marker=mk))
        for r in results or []:
            f = r.curves.get("fit")
            if f and f.get("series") == n:
                ax.plot(f["x"], f["y"], color=darker(col, .85), lw=style.fit_line_width, zorder=2)
                if style.show_fit_ci and "lo" in f:
                    ax.fill_between(f["x"], f["lo"], f["hi"], color=col, alpha=.15, lw=0, zorder=1)
    ax.margins(x=.05)
    finish_axes(ax, style, ds.roles.get("x", ""), "", categorical=False)
    add_legend(ax, handles if len(names) > 1 else [], style)


# ------------------------------------------------------------------ survival
def draw_survival(ax, ds: Dataset, params: dict, style: Style, gs: GStyle):
    from .stats.survival import km_table
    t = ds.survival()
    handles = []
    for g in sorted(t.group.unique(), key=str):
        sub = t[t.group == g]; c = km_table(sub); col = gs(str(g))["color"]
        ax.step(c["times"], c["s"] * 100, where="post", color=col, lw=style.line_width, ls=style.line_style, zorder=3)
        if style.show_ci_band:
            ax.fill_between(c["times"], c["lo"] * 100, c["hi"] * 100, step="post", color=col, alpha=.15, lw=0)
        if style.show_censor and len(c["cens"]):
            idx = np.searchsorted(c["times"], c["cens"], side="right") - 1
            ax.plot(c["cens"], c["s"][idx] * 100, "|", color=col, ms=style.marker_size * 1.4, mew=style.line_width * .8, zorder=4)
        med = c["median"]
        handles.append(patch_handle(col, style, f"{g} (median {'NR' if np.isnan(med) else f'{med:.3g}'})", marker="_"))
    ax.set_ylim(0, 105)
    ax.set_xlim(left=0)
    finish_axes(ax, style, "Time", "Percent survival")
    add_legend(ax, handles, style)


# ------------------------------------------------------------------ others
def draw_histogram(ax, ds: Dataset, params: dict, style: Style, gs: GStyle):
    g = ds.group_arrays()
    names = [n for n in (params.get("groups") or list(g)) if n in g]
    allv = np.concatenate([g[n] for n in names])
    nb = int(params.get("bins") or 0)
    edges = np.histogram_bin_edges(allv, bins=nb if nb else "auto")
    handles = []
    stat = params.get("stat", "count")
    for n in names:
        col = gs(n)["color"]
        w = np.ones(len(g[n])) * (100 / len(g[n]) if stat == "percent" else 1)
        ax.hist(g[n], bins=edges, weights=w if stat != "density" else None, density=stat == "density", color=mcolors.to_rgba(col, style.fill_alpha if len(names) == 1 else .55),
                edgecolor=darker(col), linewidth=style.bar_edge_width, histtype="stepfilled" if style.bar_filled else "step", zorder=2)
        handles.append(patch_handle(col, style, n))
    finish_axes(ax, style, "Value", {"count": "Count", "percent": "% of group", "density": "Density"}[stat])
    add_legend(ax, handles if len(names) > 1 else [], style)


def draw_heatmap(ax, ds: Dataset, params: dict, style: Style, gs: GStyle):
    m = ds.numeric()
    m = m.loc[:, m.notna().any()]
    data = m.to_numpy()
    cmap = params.get("cmap", "viridis")
    vmin, vmax = np.nanmin(data), np.nanmax(data)
    kw = {}
    if params.get("center_zero"):
        a = max(abs(vmin), abs(vmax)); kw = dict(vmin=-a, vmax=a)
    im = ax.imshow(data, aspect="auto", cmap=cmap, **kw)
    ax.set_xticks(range(m.shape[1])); ax.set_xticklabels(m.columns)
    ax.set_yticks(range(m.shape[0])); ax.set_yticklabels([str(i + 1) for i in m.index] if len(m) <= 40 else [])
    if params.get("annotate") and data.size <= 400:
        for (i, j), v in np.ndenumerate(data):
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2g}", ha="center", va="center", fontsize=style.tick_size * .8, color="white" if im.norm(v) < .5 else "black")
    cb = ax.figure.colorbar(im, ax=ax, fraction=.05, pad=.03)
    cb.ax.tick_params(labelsize=style.tick_size, width=style.tick_width, length=style.tick_len * .7)
    cb.outline.set_linewidth(style.spine_width)
    finish_axes(ax, style, "", "", categorical=True)
    ax.set_ylim(len(m) - .5, -.5)
    ax.set_xlim(-.5, m.shape[1] - .5)


def draw_stacked(ax, ds: Dataset, params: dict, style: Style, gs: GStyle):
    c = ds.counts()
    if params.get("percent", True):
        c = c.div(c.sum(axis=1), axis=0) * 100
    bottom = np.zeros(len(c)); handles = []
    for col_name in c.columns:
        col = gs(str(col_name))["color"]
        ax.bar(range(len(c)), c[col_name], bottom=bottom, width=style.bar_width, zorder=2, **fill_kw(style, col, gs(str(col_name)).get("hatch")))
        bottom += c[col_name].to_numpy()
        handles.append(patch_handle(col, style, str(col_name)))
    ax.set_xticks(range(len(c))); ax.set_xticklabels(c.index)
    ax.set_xlim(-.6, len(c) - .4)
    finish_axes(ax, style, "", "Percent of row" if params.get("percent", True) else "Count", categorical=True)
    add_legend(ax, handles, style)


def draw_bland_altman(ax, res, style: Style, gs: GStyle):
    b = res.curves["ba"]
    col = gs("Bland-Altman")["color"]
    ax.scatter(b["mean"], b["diff"], s=style.point_size, marker=style.point_marker, facecolor=mcolors.to_rgba(col, style.point_alpha), edgecolor=edge_color(style, col),
               linewidth=style.point_edge_width, zorder=3)
    for y, ls in ((b["bias"], "-"), (b["lo"], "--"), (b["hi"], "--")):
        ax.axhline(y, color="black", lw=style.line_width * .7, ls=ls, zorder=2)
        ax.text(ax.get_xlim()[1], y, f" {y:.3g}", va="center", fontsize=style.tick_size * .9)
    finish_axes(ax, style, "Mean of two methods", "Difference (A - B)")


def draw_roc(ax, res, style: Style, gs: GStyle):
    r = res.curves["roc"]
    col = gs("ROC")["color"]
    ax.plot([0, 1], [0, 1], color="0.6", lw=style.line_width * .6, ls="--", zorder=1)
    ax.plot(r["fpr"], r["tpr"], color=col, lw=style.line_width, drawstyle="steps-post", zorder=3)
    auc = res.tables["AUC"].loc["AUC", "Value"]
    ax.text(.95, .08, f"AUC = {auc:.3f}", ha="right", fontsize=style.tick_size)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    finish_axes(ax, style, "1 - Specificity", "Sensitivity")


def message(ax, text, style):
    ax.axis("off")
    ax.text(.5, .5, text, ha="center", va="center", wrap=True, fontsize=style.label_size, color="firebrick", transform=ax.transAxes)


# ------------------------------------------------------------------ top level
def draw_graph(ax, spec, ds: Dataset, style: Style, gs: GStyle, comps=None, results=None):
    from . import stats as S
    k, p = spec.kind, spec.params
    if k == "column":
        draw_column(ax, ds, p, style, gs, comps)
    elif k == "grouped":
        draw_grouped(ax, ds, p, style, gs, comps)
    elif k == "xy":
        draw_xy(ax, ds, p, style, gs, results)
    elif k == "survival":
        draw_survival(ax, ds, p, style, gs)
    elif k == "histogram":
        draw_histogram(ax, ds, p, style, gs)
    elif k == "heatmap":
        draw_heatmap(ax, ds, p, style, gs)
    elif k == "stacked":
        draw_stacked(ax, ds, p, style, gs)
    elif k in ("bland_altman", "roc"):
        opts = {x: p.get(x) for x in ("group_a", "group_b", "pct", "direction") if p.get(x) is not None}
        res = S.run(k, ds, opts)
        (draw_bland_altman if k == "bland_altman" else draw_roc)(ax, res, style, gs)
    else:
        raise ValueError(f"unknown graph kind {k}")


def render(spec, ds: Dataset, style: Style, gs: GStyle, comps=None, results=None) -> Figure:
    fig = Figure(figsize=(style.width, style.height), dpi=100, facecolor="none" if style.background == "transparent" else "white")
    fig._ps_rc = rc_for(style)
    with mpl.rc_context(fig._ps_rc):
        ax = fig.add_subplot(111)
        try:
            draw_graph(ax, spec, ds, style, gs, comps, results)
        except Exception as e:  # show the reason on the canvas, never a stack trace
            ax.clear(); message(ax, f"Cannot draw this graph:\n{e}", style)
            fig._ps_error = str(e)
        fig.tight_layout(pad=0.6)
    return fig


def export(fig: Figure, fmt: str = "png", dpi: int = 300, transparent: bool = False) -> bytes:
    buf = io.BytesIO()
    with mpl.rc_context(getattr(fig, "_ps_rc", {})):
        fig.savefig(buf, format=fmt, dpi=dpi, transparent=transparent, bbox_inches="tight" if fmt != "svg" else None, pad_inches=0.05)
    return buf.getvalue()


def render_layout(layout: dict, project) -> Figure:
    """Compose several graphs into one multi-panel figure with panel letters. Each panel keeps its own effective style."""
    rows, cols = layout["rows"], layout["cols"]
    first = project.effective_style(project.graph(next(p for p in layout["panels"] if p))) if any(layout["panels"]) else project.theme
    fig = Figure(figsize=(layout.get("width", first.width * cols), layout.get("height", first.height * rows)), dpi=100, facecolor="white")
    fig._ps_rc = rc_for(project.theme)
    with mpl.rc_context(fig._ps_rc):
        gsp = fig.add_gridspec(rows, cols)
        for idx, gid in enumerate(layout["panels"][: rows * cols]):
            ax = fig.add_subplot(gsp[idx // cols, idx % cols])
            if not gid:
                ax.axis("off"); continue
            g = project.graph(gid)
            style = project.effective_style(g)
            comps, results = analyses_for(project, g)
            try:
                draw_graph(ax, g, project.datasets[g.dataset], style, project.gs(style), comps, results)
            except Exception as e:
                ax.clear(); message(ax, str(e), style)
            if layout.get("letters", True):
                ax.text(-0.18, 1.08, chr(65 + idx), transform=ax.transAxes, fontsize=layout.get("letter_size", 14), fontweight="bold", va="bottom", ha="right")
        fig.tight_layout(pad=0.8)
    return fig


def analyses_for(project, g):
    comps, results = [], []
    for aid in g.analysis_ids:
        try:
            a = project.analysis(aid)
            r = project.run_analysis(a)
        except Exception:
            continue
        comps += r.comparisons
        results.append(r)
    return comps, results
