"""Verify a portable release using only its own interpreter, libraries and source."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scripts.package_support import FORMAT, environment_info, fingerprint_tree
from scripts.prepare_python import verify_python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--updates", nargs="*", type=Path, default=[])
    parser.add_argument("--report-dir", type=Path, default=Path("artifacts/package-smoke"))
    args = parser.parse_args()
    bundle, report = args.bundle.resolve(), args.report_dir.resolve()
    if report == bundle or bundle in report.parents:
        parser.error("报告目录必须在发行包外")
    report.mkdir(parents=True, exist_ok=False)
    if args.updates:
        validation_bundle = report / "bundle"
        shutil.copytree(bundle, validation_bundle, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "logs", "artifacts"))
        bundle = validation_bundle
    manifest = json.loads((bundle / "build-info.json").read_text(encoding="utf-8"))
    assert manifest["format"] == FORMAT, "请使用统一 Python 结构的发行包"
    assert fingerprint_tree(bundle / "python") == manifest["runtime_sha256"]
    assert fingerprint_tree(bundle / "src") == manifest["host_sha256"]
    runtime = verify_python(bundle / "python")
    assert environment_info(bundle / "python") == {k: manifest[k] for k in ("python", "architecture", "dependencies")}
    version_tag = "".join(runtime["version"].split(".")[:2])
    assert len(list(bundle.rglob(f"python{version_tag}.dll"))) == 1
    assert len(list(bundle.rglob("cv2.pyd"))) == 1
    assert not list((bundle / "tasks").rglob("*.dll"))
    assert not list((bundle / "tasks").rglob("*.exe"))
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    environment["QT_QPA_PLATFORM"] = "offscreen"
    probe = Path(__file__).with_name("verify_packaged_host.py").resolve()
    result = subprocess.run(
        [str(bundle / "python/pythonw.exe"), "-I", "-X", "utf8", str(probe), str(report),
         *(str(path.resolve()) for path in args.updates)],
        cwd=report, env=environment, capture_output=True, text=True, encoding="utf-8",
        timeout=60 + 35 * len(args.updates), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (report / "probe-stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    results = json.loads(result.stdout)
    (report / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
