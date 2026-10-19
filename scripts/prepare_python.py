"""Prepare an application-local Windows Python from the build interpreter."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def verify_python(directory):
    directory = Path(directory).resolve()
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    result = subprocess.run(
        [str(directory / "python.exe"), "-I", "-X", "utf8", "-c",
         "import ctypes, json, multiprocessing, pip, sqlite3, ssl, sys, urllib.request; "
         "from pathlib import Path; "
         "root = Path(sys.executable).resolve().parent; "
         "assert Path(sys.prefix).resolve() == root; "
         "allowed = {root, root / 'Lib', root / 'DLLs', root / 'Lib/site-packages', "
         "root.parent, root.parent / 'src'}; "
         "assert all(Path(p).resolve() in allowed for p in sys.path), sys.path; "
         "print(json.dumps({'version': sys.version.split()[0], 'prefix': sys.prefix, "
         "'pip': pip.__version__}))"],
        cwd=directory, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(f"独立 Python 验证失败：{result.stderr}")
    return json.loads(result.stdout)


def prepare_python(destination):
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        raise RuntimeError("请使用 Windows 开发环境的 Python 运行此脚本")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"目标已存在，不覆盖已有环境：{destination}")
    source = Path(sys.base_prefix)
    version_tag = f"{sys.version_info.major}{sys.version_info.minor}"
    required = [source / name for name in (
        "python.exe", f"python{version_tag}.dll", "LICENSE.txt", "Lib", "DLLs",
    )]
    for path in required:
        if not path.exists():
            raise FileNotFoundError(f"构建 Python 安装不完整：{path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-python-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "python"
        stage.mkdir()
        for path in [source / "python.exe", source / "pythonw.exe", source / "LICENSE.txt",
                     *source.glob("*.dll")]:
            if path.is_file():
                shutil.copy2(path, stage / path.name)
        shutil.copytree(source / "DLLs", stage / "DLLs")
        shutil.copytree(source / "Lib", stage / "Lib",
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc"))
        (stage / "Lib" / "site-packages").mkdir()
        # Only relative, application-owned paths. Ignore registry, user site and PYTHONPATH.
        (stage / f"python{version_tag}._pth").write_text(
            ".\nLib\nDLLs\nLib/site-packages\nimport site\n", encoding="utf-8",
        )
        subprocess.run(
            [str(stage / "python.exe"), "-I", "-X", "utf8", "-m", "ensurepip"],
            check=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        information = verify_python(stage)
        # pip's .exe launchers contain absolute paths; use relocatable `python -m pip`.
        scripts = stage / "Scripts"
        for launcher in scripts.glob("pip*.exe"):
            launcher.unlink()
        information.pop("prefix")
        (stage / "runtime-info.json").write_text(
            json.dumps(information, indent=2) + "\n", encoding="utf-8",
        )
        stage.rename(destination)
    return verify_python(destination)


def setup_project_python(destination, project_root):
    destination = Path(destination).resolve()
    project_root = Path(project_root).resolve()
    if not destination.exists():
        prepare_python(destination)
    # Keep source and project imports relative, so a release can move to any directory.
    verify_python(destination)
    path_files = list(destination.glob("python*._pth"))
    if len(path_files) != 1:
        raise RuntimeError("独立 Python 必须包含一个 python*._pth 文件")
    path_files[0].write_text(
        ".\nLib\nDLLs\nLib/site-packages\n../src\n..\nimport site\n", encoding="utf-8",
    )
    subprocess.run(
        [str(destination / "python.exe"), "-I", "-X", "utf8", "-m", "pip", "install",
         "--only-binary=:all:", "-r", str(project_root / "requirements.txt")],
        check=True,
    )
    subprocess.run([str(destination / "python.exe"), "-I", "-X", "utf8", "-m", "pip", "check"], check=True)
    return verify_python(destination)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "python")
    args = parser.parse_args()
    print(json.dumps(setup_project_python(args.destination, Path(__file__).resolve().parents[1]),
                     ensure_ascii=False, indent=2))
