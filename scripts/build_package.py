"""Assemble one shared Python, host source and selected skill directories."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib

from scripts.package_support import (
    DEFAULT_TASKS, FORMAT, check_environment, environment_info, fingerprint_tree, task_directory, runtime_ignore, task_ignore,
)
from scripts.prepare_python import verify_python
from scripts.build_launcher import build_launcher
from huanzhizhi.tasks.catalog import TaskCatalog


def build_package(root, output, *, tasks=DEFAULT_TASKS, task=None, base_bundle=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if bool(task) != bool(base_bundle):
        raise ValueError("--task 和 --base-bundle 必须同时指定")
    if output.exists():
        raise FileExistsError(f"不覆盖已有输出，请使用新的 --output：{output}")
    if any(output.is_relative_to(root / part) for part in ("src", "tasks", "python")):
        raise ValueError("输出目录不能位于 src、tasks 或 python 内")
    task_ids = (task,) if task else tuple(tasks)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("技能列表不能重复")
    directories = [(task_id, task_directory(root, task_id)) for task_id in task_ids]
    if task:
        manifest = check_environment(base_bundle, root)
    else:
        verify_python(root / "python")
        manifest = environment_info(root / "python")
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        manifest.update(format=FORMAT, version=project["version"], tasks=list(task_ids))
    output.parent.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.egg-info")
    with tempfile.TemporaryDirectory(prefix=".package-", dir=output.parent) as temporary:
        stage = Path(temporary) / "bundle"
        (stage / "tasks").mkdir(parents=True)
        for task_id, directory in directories:
            shutil.copytree(directory, stage / "tasks" / task_id,
                            ignore=lambda current, names: task_ignore(current, names, skill_root=directory))
        if not task:
            shutil.copytree(root / "python", stage / "python", ignore=runtime_ignore)
            shutil.copytree(root / "src", stage / "src", ignore=ignore)
            for name in ("CHANGELOG.md", "LICENSE", "requirements.txt", "pyproject.toml"):
                shutil.copy2(root / name, stage / name)
            for directory in ("licenses", "docs"):
                shutil.copytree(root / directory, stage / directory, ignore=ignore)
            shutil.copy2(root / "docs" / "portable-readme.md", stage / "README.md")
            (stage / "dependencies").mkdir()
            shutil.copy2(root / "dependencies" / "README.md", stage / "dependencies" / "README.md")
            build_launcher(root, stage / "幻之之.exe")
        descriptors = TaskCatalog(stage / "tasks").reload()
        if {item.id for item in descriptors} != set(task_ids):
            raise ValueError("打包后的技能列表与选择不一致")
        if task:
            update = {key: manifest[key] for key in ("format", "runtime_sha256", "host_sha256")}
            update["task"] = task
            (stage / "update-info.json").write_text(json.dumps(update, indent=2) + "\n", encoding="utf-8")
        else:
            if environment_info(stage / "python") != {k: manifest[k] for k in ("python", "architecture", "dependencies")}:
                raise ValueError("复制后的 Python 环境与构建环境不一致")
            manifest["runtime_sha256"] = fingerprint_tree(stage / "python")
            manifest["host_sha256"] = fingerprint_tree(stage / "src")
            (stage / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    return output


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
    selection.add_argument("--task")
    parser.add_argument("--base-bundle", type=Path)
    args = parser.parse_args()
    print(build_package(Path(__file__).resolve().parents[1], args.output,
                        tasks=args.tasks, task=args.task, base_bundle=args.base_bundle))
