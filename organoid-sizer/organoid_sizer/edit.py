"""Manual corrections on a label image. Each function returns a new label array (inputs are not modified)
plus, where relevant, the ids of the objects that were created."""
from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage as ndi


def _pts(points) -> np.ndarray:
    return np.round(np.asarray(points, np.float64)).astype(np.int32).reshape(-1, 2)


def next_id(lab: np.ndarray) -> int:
    return int(lab.max()) + 1


def delete(lab, ids):
    out = lab.copy()
    out[np.isin(out, list(ids))] = 0
    return out


def add_polygon(lab, points, overwrite=True):
    """Fill a polygon as a new object. overwrite=True: the new object takes pixels from objects it covers."""
    out = lab.copy()
    m = np.zeros(lab.shape, np.uint8)
    pts = _pts(points)
    if len(pts) < 3:
        raise ValueError("A shape needs at least 3 points.")
    cv2.fillPoly(m, [pts], 1)
    m = m.astype(bool)
    if not overwrite:
        m &= out == 0
    if m.sum() < 3:
        raise ValueError("The drawn shape is empty.")
    nid = next_id(out)
    out[m] = nid
    return _drop_fragments(out), [nid]


def ellipse_points(cx, cy, rx, ry, angle_deg=0.0, n=96):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    a = np.deg2rad(angle_deg)
    x = rx * np.cos(t)
    y = ry * np.sin(t)
    return np.stack([cx + x * np.cos(a) - y * np.sin(a), cy + x * np.sin(a) + y * np.cos(a)], 1).tolist()


def brush(lab, points, radius, target=None, erase=False):
    """Paint a stroke. erase=True clears pixels (of `target` only, if given). Otherwise the stroke is added to
    `target` (grow / fix an outline) or becomes a new object when target is None."""
    out = lab.copy()
    m = np.zeros(lab.shape, np.uint8)
    pts = _pts(points)
    r = max(1, int(round(radius)))
    if len(pts) == 1:
        cv2.circle(m, tuple(int(v) for v in pts[0]), r, 1, -1)
    else:
        cv2.polylines(m, [pts], False, 1, thickness=2 * r + 1, lineType=cv2.LINE_8)
        for p in (pts[0], pts[-1]):
            cv2.circle(m, tuple(int(v) for v in p), r, 1, -1)
    m = m.astype(bool)
    created = []
    if erase:
        if target:
            out[m & (out == target)] = 0
        else:
            out[m] = 0
    else:
        if target:
            out[m] = target
        else:
            nid = next_id(out)
            out[m] = nid
            created = [nid]
    if not erase:
        out = _fill_holes(out, [target] if target else created)
    return _drop_fragments(out), created


def merge(lab, ids):
    ids = [int(i) for i in ids if i]
    if len(ids) < 2:
        raise ValueError("Select at least two objects to merge (Shift+click).")
    out = lab.copy()
    keep = min(ids)
    out[np.isin(out, ids)] = keep
    # bridge a small gap between them so the result is one connected object
    m = out == keep
    _, n = ndi.label(m)
    if n > 1:
        closed = ndi.binary_closing(m, structure=np.ones((3, 3)), iterations=6)
        out[closed & (out == 0)] = keep
    out = _fill_holes(out, [keep])
    return out, [keep]


def split_line(lab, points, width=2):
    """Cut every object crossed by a drawn polyline. Pieces become separate objects; the cut pixels are returned
    to the nearest piece so no area is lost."""
    pts = _pts(points)
    if len(pts) < 2:
        raise ValueError("Draw a line across the object to split it.")
    cut = np.zeros(lab.shape, np.uint8)
    cv2.polylines(cut, [pts], False, 1, thickness=max(1, int(width)), lineType=cv2.LINE_8)
    cut = cut.astype(bool)
    hit = [int(v) for v in np.unique(lab[cut]) if v]
    if not hit:
        raise ValueError("The line does not cross any object.")
    out = lab.copy()
    created = []
    for oid in hit:
        obj = out == oid
        pieces, n = ndi.label(obj & ~cut)
        if n < 2:
            continue
        sizes = ndi.sum(np.ones_like(pieces), pieces, index=np.arange(1, n + 1))
        order = np.argsort(sizes)[::-1]
        # tiny slivers (<1% of the object) are merged back instead of becoming objects
        total = sizes.sum()
        newlab = np.zeros_like(out)
        nid = next_id(out)
        for rank, k in enumerate(order):
            pid = k + 1
            if rank > 0 and sizes[k] < 0.01 * total:
                continue
            target = oid if rank == 0 else nid
            if rank > 0:
                nid += 1
                created.append(target)
            newlab[pieces == pid] = target
        # give remaining object pixels (cut line, slivers) to the nearest assigned piece
        rest = obj & (newlab == 0)
        if rest.any():
            idx = ndi.distance_transform_edt(newlab == 0, return_distances=False, return_indices=True)
            newlab[rest] = newlab[idx[0][rest], idx[1][rest]]
        out[obj] = newlab[obj]
    if not created:
        raise ValueError("The line must cross the object completely (from edge to edge).")
    return out, created


def split_auto(lab, oid, n=2):
    """Split one object into n parts: seeds from distance-transform peaks (fused spheres have one peak each);
    if not enough peaks, k-means on pixel positions. Boundaries from watershed on the distance transform."""
    oid = int(oid)
    n = max(2, int(n))
    sl = ndi.find_objects((lab == oid).astype(np.int32))
    if not sl or sl[0] is None:
        raise ValueError("Object not found.")
    sl = sl[0]
    pad = 2
    y0, x0 = max(0, sl[0].start - pad), max(0, sl[1].start - pad)
    y1, x1 = min(lab.shape[0], sl[0].stop + pad), min(lab.shape[1], sl[1].stop + pad)
    obj = lab[y0:y1, x0:x1] == oid
    dist = ndi.distance_transform_edt(obj)
    seeds = _peaks(dist, n)
    if len(seeds) < n:
        ys, xs = np.nonzero(obj)
        data = np.stack([xs, ys], 1).astype(np.float32)
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.1)
        _, _, centers = cv2.kmeans(data, n, None, crit, 5, cv2.KMEANS_PP_CENTERS)
        seeds = [(int(round(c[1])), int(round(c[0]))) for c in centers]
    seeds = [_snap_inside(obj, yy, xx) for yy, xx in seeds]
    radii = [float(dist[yy, xx]) for yy, xx in seeds]
    markers = power_split(obj, seeds, radii)
    out = lab.copy()
    sub = out[y0:y1, x0:x1]
    nid = next_id(out)
    created = []
    for k in range(1, n + 1):
        part = obj & (markers == k)
        if not part.any():
            continue
        if k == 1:
            sub[part] = oid
        else:
            sub[part] = nid
            created.append(nid)
            nid += 1
    if not created:
        raise ValueError("Could not split this object.")
    return out, created


def _snap_inside(obj, yy, xx):
    yy = min(max(int(yy), 0), obj.shape[0] - 1)
    xx = min(max(int(xx), 0), obj.shape[1] - 1)
    if not obj[yy, xx]:
        ys, xs = np.nonzero(obj)
        j = np.argmin((ys - yy) ** 2 + (xs - xx) ** 2)
        yy, xx = int(ys[j]), int(xs[j])
    return yy, xx


def power_split(obj: np.ndarray, seeds, radii) -> np.ndarray:
    """Split a mask between spheres (centre, radius): each pixel goes to the sphere minimising |p-c|^2 - r^2.
    For two overlapping circles the boundary is exactly their intersection chord. Disconnected crumbs are given
    to the touching part. Returns labels 1..n inside obj, 0 outside."""
    ys, xs = np.nonzero(obj)
    c = np.asarray(seeds, np.float64)
    r = np.asarray(radii, np.float64)
    cost = (ys[:, None] - c[None, :, 0]) ** 2 + (xs[:, None] - c[None, :, 1]) ** 2 - r[None, :] ** 2
    lab = np.zeros(obj.shape, np.int32)
    lab[ys, xs] = np.argmin(cost, axis=1) + 1
    for k in range(1, len(seeds) + 1):
        cc, n = ndi.label(lab == k)
        if n > 1:
            sizes = ndi.sum(np.ones_like(cc), cc, index=np.arange(1, n + 1))
            big = int(np.argmax(sizes)) + 1
            lab[(cc > 0) & (cc != big)] = 0
    rest = obj & (lab == 0)
    if rest.any():
        idx = ndi.distance_transform_edt(lab == 0, return_distances=False, return_indices=True)
        lab[rest] = lab[idx[0][rest], idx[1][rest]]
    return lab


def _peaks(dist, n):
    if dist.max() <= 0:
        return []
    r = max(2, int(dist.max() * 0.6))
    mx = ndi.maximum_filter(dist, size=2 * r + 1)
    cand = np.argwhere((dist == mx) & (dist > 0.35 * dist.max()))
    cand = sorted(cand.tolist(), key=lambda p: -dist[p[0], p[1]])
    chosen = []
    for p in cand:
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > (0.8 * dist[p[0], p[1]]) ** 2 for q in chosen):
            chosen.append(p)
        if len(chosen) == n:
            break
    return [tuple(p) for p in chosen]


def _fill_holes(lab, ids):
    out = lab
    for i in ids:
        if not i:
            continue
        m = out == i
        f = ndi.binary_fill_holes(m)
        out[f & (out == 0)] = i
    return out


def _drop_fragments(lab, min_frac=0.05):
    """After painting over an object, keep only its main connected piece; tiny leftovers are cleared."""
    out = lab
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        m = out[sl] == i
        cc, n = ndi.label(m)
        if n <= 1:
            continue
        sizes = ndi.sum(np.ones_like(cc), cc, index=np.arange(1, n + 1))
        big = int(np.argmax(sizes)) + 1
        for k in range(1, n + 1):
            if k != big and sizes[k - 1] < max(min_frac * sizes.max(), 4):
                out[sl][cc == k] = 0
    return out
