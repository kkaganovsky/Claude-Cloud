import glob
import os
import zipfile

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(scope="session")
def cell7_dir(tmp_path_factory):
    """The example cell uploaded to the repo root (zip); tests that need it are skipped when it is absent."""
    zips = glob.glob(os.path.join(REPO, "cell 7 DLX kglu*.zip"))
    if not zips:
        pytest.skip("example cell zip not found")
    out = tmp_path_factory.mktemp("cell7")
    with zipfile.ZipFile(zips[0]) as z:
        z.extractall(out)
    d = glob.glob(os.path.join(out, "cell 7*"))
    return d[0] + "/"


@pytest.fixture(autouse=True)
def _run_in_a_temp_dir(tmp_path, monkeypatch):
    """Tests that use a bare recording name (e.g. 'synthetic.abf') must not leave autosave files in the repo."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _private_settings(tmp_path_factory, monkeypatch):
    """Window size, panel visibility and splitters are remembered in QSettings; tests must not read or write the real ones."""
    from PySide6 import QtCore
    d = tmp_path_factory.mktemp("qsettings")
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(d))
    QtCore.QSettings.setPath(QtCore.QSettings.NativeFormat, QtCore.QSettings.UserScope, str(d))
