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
