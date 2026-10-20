"""Protect the one shared environment during source-only skill updates."""
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.build_package import build_package
from scripts.package_support import FORMAT, check_environment, fingerprint_tree, task_directory
from tests.skill_factory import write_skill_manifest


class PackageBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.bundle = Path(self.temp.name) / "release"
        for directory in ("python", "src", "tasks/demo/assets"):
            (self.root / directory).mkdir(parents=True)
        (self.root / "python/python.exe").write_bytes(b"interpreter")
        (self.root / "python/library.dll").write_bytes(b"library")
        (self.root / "src/host.py").write_text("host code", encoding="utf-8")
        (self.root / "tasks/demo/task.py").write_text("skill code", encoding="utf-8")
        write_skill_manifest(self.root / "tasks/demo/task.py")
        (self.root / "tasks/demo/assets/version.txt").write_text("v1", encoding="utf-8")
        (self.root / "tasks/demo/tests").mkdir()
        (self.root / "tasks/demo/tests/sample.png").write_bytes(b"private test sample")
        (self.root / "tasks/demo/.runtime/state").mkdir(parents=True)
        (self.root / "tasks/demo/.runtime/state/checkpoint.json").write_bytes(b"private state")
        (self.root / "tasks/demo/assets/.runtime").mkdir()
        (self.root / "tasks/demo/assets/.runtime/reference.png").write_bytes(b"asset")
        shutil.copytree(self.root, self.bundle)
        self.manifest = {"format": FORMAT, "runtime_sha256": fingerprint_tree(self.root / "python"),
                         "host_sha256": fingerprint_tree(self.root / "src")}
        (self.bundle / "build-info.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_same_host_and_libraries_are_accepted(self):
        self.assertEqual(check_environment(self.bundle, self.root), self.manifest)

    def test_changed_source_or_release_is_rejected_without_overwrite(self):
        for directory in (self.root, self.bundle):
            for file in ("python/library.dll", "src/host.py"):
                with self.subTest(directory=directory, file=file):
                    target = directory / file
                    previous = target.read_bytes()
                    target.write_bytes(b"changed")
                    try:
                        with self.assertRaisesRegex(ValueError, "不兼容"):
                            check_environment(self.bundle, self.root)
                        self.assertEqual(target.read_bytes(), b"changed")
                    finally:
                        target.write_bytes(previous)

    def test_legacy_release_requires_full_build(self):
        (self.bundle / "build-info.json").write_text('{"python":"3.11.9"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "完整重新打包"):
            check_environment(self.bundle, self.root)

    def test_bytecode_does_not_invalidate_but_paths_and_contents_do(self):
        directory = self.root / "python"
        original = fingerprint_tree(directory)
        (directory / "__pycache__").mkdir()
        (directory / "__pycache__/module.pyc").write_bytes(b"cache")
        self.assertEqual(fingerprint_tree(directory), original)
        (directory / "library.dll").rename(directory / "renamed.dll")
        self.assertNotEqual(fingerprint_tree(directory), original)

    def test_invalid_paths_and_missing_entries(self):
        for task_id in ("../demo", "", "a/b", "CON", "技能.", " 技能", "技能:一"):
            with self.assertRaises(ValueError):
                task_directory(self.root, task_id)
        with self.assertRaises(FileNotFoundError):
            task_directory(self.root, "missing")
        with self.assertRaises(ValueError):
            build_package(self.root, self.root / "python/new")

    def test_chinese_task_directory(self):
        directory = self.root / 'tasks/中文 技能（测试）'
        (self.root / 'tasks/demo').rename(directory)
        self.assertEqual(task_directory(self.root, '中文 技能（测试）'), directory.resolve())

    def test_existing_output_and_unpaired_options_are_rejected(self):
        with self.assertRaises(FileExistsError):
            build_package(self.root, self.bundle)
        with self.assertRaises(ValueError):
            build_package(self.root, Path(self.temp.name) / "update", task="demo")
        with self.assertRaises(ValueError):
            build_package(self.root, Path(self.temp.name) / "update", tasks=("demo", "demo"))

    def test_skill_update_contains_only_skill_and_preserves_release(self):
        output = Path(self.temp.name) / "update"
        before = fingerprint_tree(self.bundle)
        with patch("scripts.build_package.TaskCatalog") as catalog:
            catalog.return_value.reload.return_value = (SimpleNamespace(id="demo"),)
            build_package(self.root, output, task="demo", base_bundle=self.bundle)
        self.assertEqual({p.name for p in output.iterdir()}, {"tasks", "update-info.json"})
        self.assertEqual((output / "tasks/demo/assets/version.txt").read_text(encoding="utf-8"), "v1")
        self.assertFalse((output / "tasks/demo/tests").exists())
        self.assertFalse((output / "tasks/demo/.runtime").exists())
        self.assertEqual((output / "tasks/demo/assets/.runtime/reference.png").read_bytes(), b"asset")
        self.assertEqual(json.loads((output / "update-info.json").read_text())["task"], "demo")
        self.assertEqual(fingerprint_tree(self.bundle), before)

    def test_full_package_includes_dependency_instructions_without_local_components(self):
        dependencies = self.root / "dependencies"
        (dependencies / "engine/models").mkdir(parents=True)
        (dependencies / "README.md").write_text("用户自行准备组件", encoding="utf-8")
        (dependencies / "engine/tool.exe").write_bytes(b"local executable")
        (dependencies / "engine/models/weights.bin").write_bytes(b"local model")
        for name in ("run.ps1", "启动.cmd", "README.md", "CHANGELOG.md", "LICENSE", "requirements.txt"):
            (self.root / name).write_text("fixture", encoding="utf-8")
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
        for directory in ("licenses", "docs"):
            (self.root / directory).mkdir()
            (self.root / directory / "README.txt").write_text("notice", encoding="utf-8")
        # Public bundles retain the Widgets runtime, not unused Qt SDK components.
        qt = self.root / "python/Lib/site-packages/PySide6"
        (qt / "qml/QtQuick/VirtualKeyboard").mkdir(parents=True)
        (qt / "plugins/platforms").mkdir(parents=True)
        (qt / "qml/QtQuick/VirtualKeyboard/plugin.dll").write_bytes(b"unused")
        (qt / "Qt6VirtualKeyboard.dll").write_bytes(b"unused")
        (qt / "Qt6Core.dll").write_bytes(b"core")
        (qt / "plugins/platforms/qwindows.dll").write_bytes(b"platform")
        regression = self.root / "python/Lib/test/certdata"
        regression.mkdir(parents=True)
        (regression / "test-key.pem").write_bytes(b"upstream regression fixture")
        (self.root / "docs/portable-readme.md").write_text("双击 幻之之.exe", encoding="utf-8")
        output = Path(self.temp.name) / "full-release"
        before = fingerprint_tree(dependencies)
        with patch("scripts.build_package.verify_python"), \
                patch("scripts.build_package.build_launcher",
                      side_effect=lambda root, output: output.write_bytes(b"launcher")), \
                patch("scripts.build_package.environment_info",
                      side_effect=lambda _: {"python": "3.11.9", "architecture": "64bit", "dependencies": {}}), \
                patch("scripts.build_package.TaskCatalog") as catalog:
            catalog.return_value.reload.return_value = (SimpleNamespace(id="demo"),)
            build_package(self.root, output, tasks=("demo",))
        self.assertEqual(
            {p.relative_to(output / "dependencies").as_posix() for p in (output / "dependencies").rglob("*")},
            {"README.md"},
        )
        self.assertEqual((output / "dependencies/README.md").read_text(encoding="utf-8"), "用户自行准备组件")
        self.assertEqual(fingerprint_tree(dependencies), before)
        self.assertFalse((output / "tasks/demo/tests").exists())
        self.assertFalse((output / "tasks/demo/.runtime").exists())
        self.assertTrue((output / "tasks/demo/assets/version.txt").is_file())
        self.assertEqual((output / "LICENSE").read_text(), "fixture")
        self.assertEqual((output / "幻之之.exe").read_bytes(), b"launcher")
        self.assertFalse((output / "run.ps1").exists())
        self.assertFalse((output / "启动.cmd").exists())
        self.assertTrue((output / "licenses/README.txt").is_file())
        self.assertTrue((output / "docs/README.txt").is_file())
        self.assertEqual(json.loads((output / "build-info.json").read_text())["version"], "0.2.0")
        self.assertTrue((output / "python/Lib/site-packages/PySide6/Qt6Core.dll").is_file())
        self.assertTrue((output / "python/Lib/site-packages/PySide6/plugins/platforms/qwindows.dll").is_file())
        self.assertFalse(list((output / "python").rglob("*VirtualKeyboard*")))
        self.assertFalse((output / "python/Lib/test").exists())
        # A source-only update must accept the same environment after filtering.
        check_environment(output, self.root)

    def test_failed_description_leaves_no_completed_update(self):
        output = Path(self.temp.name) / "update"
        with patch("scripts.build_package.TaskCatalog") as catalog:
            catalog.return_value.reload.side_effect = RuntimeError("bad description")
            with self.assertRaisesRegex(RuntimeError, "bad description"):
                build_package(self.root, output, task="demo", base_bundle=self.bundle)
        self.assertFalse(output.exists())

    def test_default_host_package_does_not_include_installed_skills(self):
        for name in ("README.md", "CHANGELOG.md", "LICENSE", "requirements.txt"):
            (self.root / name).write_text("fixture", encoding="utf-8")
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
        for name in ("licenses", "docs", "dependencies"):
            (self.root / name).mkdir()
        (self.root / "dependencies/README.md").write_text("fixture", encoding="utf-8")
        (self.root / "docs/portable-readme.md").write_text("双击 幻之之.exe", encoding="utf-8")
        output = Path(self.temp.name) / "host-only"
        with patch("scripts.build_package.verify_python"), \
                patch("scripts.build_package.build_launcher",
                      side_effect=lambda root, output: output.write_bytes(b"launcher")), \
                patch("scripts.build_package.environment_info",
                      side_effect=lambda _: {"python": "3.11.9", "architecture": "64bit", "dependencies": {}}):
            build_package(self.root, output)
        self.assertEqual((output / "README.md").read_text(encoding="utf-8"), "双击 幻之之.exe")
        self.assertEqual(list((output / "tasks").iterdir()), [])
        self.assertEqual(json.loads((output / "build-info.json").read_text())["tasks"], [])
        self.assertTrue((self.root / "tasks/demo/task.py").exists())


if __name__ == "__main__":
    unittest.main()
