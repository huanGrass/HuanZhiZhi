"""Run a UTF-8 script with the private Python; Ctrl+C stops a running script."""
import argparse
from pathlib import Path
import sys

from huanzhizhi.python_runtime import run_code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        result = run_code(args.script.read_text(encoding="utf-8-sig"), timeout=args.timeout)
    except KeyboardInterrupt:
        print("脚本已停止", file=sys.stderr)
        raise SystemExit(130)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    print(f"\n运行目录：{result.workdir}", file=sys.stderr)
    if result.timed_out:
        print("脚本运行超时，已停止", file=sys.stderr)
    raise SystemExit(124 if result.timed_out else result.returncode)
