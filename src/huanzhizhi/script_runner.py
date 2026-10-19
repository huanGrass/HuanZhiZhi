"""Run a script with its own sibling modules under the shared interpreter."""
from pathlib import Path
import runpy
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法：python -m huanzhizhi.script_runner <script.py> [参数...]")
    script = Path(sys.argv[1]).resolve(strict=True)
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
