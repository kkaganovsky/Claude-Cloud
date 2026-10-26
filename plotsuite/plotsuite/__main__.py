import pathlib
import subprocess
import sys


def main():
    app = pathlib.Path(__file__).parent / "app.py"
    raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), *sys.argv[1:]]))


if __name__ == "__main__":
    main()
