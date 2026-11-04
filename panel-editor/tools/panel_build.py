#!/usr/bin/env python3
"""Rebuild linked plots when their data or settings change, and write a provenance sidecar next to each PDF.

    python tools/panel_build.py panels.json            # build whatever is stale
    python tools/panel_build.py panels.json --check    # list stale plots, exit 1 if any
    python tools/panel_build.py panels.json --watch    # keep rebuilding while you edit the data
    python tools/panel_build.py panels.json --force

panels.json (paths are relative to the manifest):
{
  "plots_dir": "plots",
  "plotsuite": "plotsuite-plot",
  "preset": "Xenocortical Nature style",
  "panels": [
    {"id": "cells", "data": "data/cells.csv",
     "args": ["--groups", "Control,Treated", "--test", "unpaired_t", "--opt", "group_a=Control", "group_b=Treated",
              "--set", "y_label=Cells per mm2", "y_min=0"]}
  ]
}

For each panel it writes  <plots_dir>/<id>.pdf  and  <plots_dir>/<id>.json  (command, data hash, statistics text).
Panel Editor reads both: the PDF becomes the artwork, the JSON is kept inside the .ai as the plot's trace.
A plot is rebuilt only when sha256(data file + command) changed, so unrelated edits never churn the figures.
If plotsuite's guardrails block an analysis (exit code 2) the OLD pdf is left untouched, the sidecar records the
error, and this script exits non-zero: the reason must be read and acted on, not worked around.
"""
from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys, time
from datetime import datetime, timezone


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fingerprint(data_bytes: bytes, cmd: list[str]) -> str:
    return sha(data_bytes + b"\0" + json.dumps(cmd).encode())


def plan(manifest_path: str):
    base = os.path.dirname(os.path.abspath(manifest_path))
    m = json.load(open(manifest_path))
    plots = os.path.join(base, m.get("plots_dir", "plots"))
    exe = m.get("plotsuite", "plotsuite-plot")
    items = []
    for p in m["panels"]:
        data = os.path.join(base, p["data"])
        args = list(p.get("args", []))
        if "--preset" not in args and m.get("preset"):
            args += ["--preset", m["preset"]]
        pdf = os.path.join(plots, p["id"] + ".pdf")
        cmd = [exe, p["data"], "-o", os.path.relpath(pdf, base), *args]
        items.append(dict(id=p["id"], data=data, data_rel=p["data"], pdf=pdf, side=os.path.join(plots, p["id"] + ".json"), cmd=cmd, base=base))
    return items


def is_stale(it, retry_failed: bool = True) -> tuple[bool, str]:
    if not os.path.exists(it["data"]):
        return True, "data file missing"
    fp = fingerprint(open(it["data"], "rb").read(), it["cmd"])
    failed = it["side"] + ".failed"
    if os.path.exists(failed):
        try:
            same = json.load(open(failed)).get("fingerprint") == fp
        except Exception:
            same = False
        if same:  # this exact data+command already failed: report it, and only retry when asked to
            return retry_failed, "last build failed (see " + os.path.basename(failed) + ")"
        os.remove(failed)  # the data/command changed since the failure, so the marker is obsolete
    if not os.path.exists(it["pdf"]) or not os.path.exists(it["side"]):
        return True, "not built yet"
    try:
        old = json.load(open(it["side"]))
    except Exception:
        return True, "unreadable sidecar"
    return old.get("fingerprint") != fp, "data or settings changed"


def build(it) -> int:
    os.makedirs(os.path.dirname(it["pdf"]), exist_ok=True)
    data_bytes = open(it["data"], "rb").read()
    env = dict(os.environ, SOURCE_DATE_EPOCH="0")  # makes matplotlib PDFs byte-identical for identical input
    r = subprocess.run(it["cmd"], cwd=it["base"], capture_output=True, text=True, env=env)
    side = dict(id=it["id"], cmd=" ".join(_q(c) for c in it["cmd"]), tool=it["cmd"][0],
                builtAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                data=dict(path=it["data_rel"], sha256=sha(data_bytes)), fingerprint=fingerprint(data_bytes, it["cmd"]))
    if r.returncode != 0:
        side["error"] = (r.stderr or r.stdout).strip()[-4000:]
        json.dump(side, open(it["side"] + ".failed", "w"), indent=2)  # keep the previous good sidecar/PDF intact
        print(f"  ✗ {it['id']}: exit {r.returncode}\n" + "\n".join("      " + l for l in side["error"].splitlines()[-8:]), file=sys.stderr)
        return r.returncode
    side["stats"] = r.stdout.strip()[-20000:]
    json.dump(side, open(it["side"], "w"), indent=2, ensure_ascii=False)
    if os.path.exists(it["side"] + ".failed"):
        os.remove(it["side"] + ".failed")
    print(f"  ✓ {it['id']}  ({it['data_rel']})")
    return 0


def _q(s: str) -> str:
    return s if s and all(c.isalnum() or c in "-_./=,:" for c in s) else "'" + s.replace("'", "'\\''") + "'"


def run_once(items, force=False, retry_failed=True) -> int:
    rc = 0
    for it in items:
        stale, why = is_stale(it, retry_failed)
        if force or stale:
            print(f"building {it['id']} ({'forced' if force and not stale else why})")
            rc = max(rc, build(it))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest"); ap.add_argument("--check", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--watch", action="store_true"); ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args(argv)
    items = plan(a.manifest)
    if a.check:
        n = 0
        for it in items:
            s, why = is_stale(it)
            print(("STALE  " if s else "ok     ") + it["id"] + (f"  ({why})" if s else "")); n += s
        return 1 if n else 0
    rc = run_once(items, a.force)
    if a.watch:
        print("watching… (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(a.interval); rc = run_once(plan(a.manifest), retry_failed=False)
        except KeyboardInterrupt:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
