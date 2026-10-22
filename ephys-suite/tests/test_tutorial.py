"""The tutorial must not rot: every image it shows exists, and the practice-candidates script it tells you to run works."""
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def test_tutorial_images_exist():
    text = open(os.path.join(ROOT, "docs", "TUTORIAL.md"), encoding="utf-8").read()
    imgs = re.findall(r"\]\((img/[^)]+)\)", text)
    assert len(imgs) >= 9
    for i in imgs:
        assert os.path.exists(os.path.join(ROOT, "docs", i)), i


def test_demo_candidates_script_writes_a_valid_csv(cell7_dir, tmp_path):
    abf = os.path.join(cell7_dir, "sEPSC.abf")
    out = tmp_path / "practice.csv"
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "make_demo_candidates.py"), abf, "--out", str(out),
                        "--omit", "0", "400"], capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    rows = out.read_text().splitlines()
    assert rows[0].startswith("sweep,time_in_sweep_ms,score,accepted")
    assert 150 < len(rows) < 260
