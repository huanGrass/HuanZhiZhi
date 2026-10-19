from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from huanzhizhi.tasks import TaskCatalog, TaskCatalogError, TaskDescriptor
from huanzhizhi.tasks.catalog import skill_manifest_path
from tests.skill_factory import write_skill_manifest


class TaskCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "tasks"
        self.python = Path(sys.executable)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_root_is_an_empty_catalog(self) -> None:
        catalog = self._catalog()

        self.assertEqual(catalog.reload(), ())
        self.assertEqual(catalog.tasks(), ())
        self.assertEqual(catalog.signature(), ())

    def test_compiled_task_reads_sidecar_without_starting_executable(self) -> None:
        self.root.mkdir()
        executable = self.root / "task-demo.exe"
        executable.write_bytes(b"not executable")
        write_skill_manifest(executable, name='Compiled task')
        (self.root / "幻之之.exe").write_bytes(b"host is not a task")
        catalog = self._catalog()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('discovery executed code')):
            tasks = catalog.reload()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].script_path, executable.resolve())
        executable.write_bytes(b"second, larger build")
        self.assertNotEqual(catalog.signature(), catalog.loaded_signature)

    def test_manifests_load_sorted_immutable_descriptors(self) -> None:
        second = self._write_descriptor_task(
            "second", name="第二个任务", description=None
        )
        first = self._write_descriptor_task(
            "first", name="第一个任务", description="说明"
        )
        catalog = self._catalog()

        tasks = catalog.reload()

        self.assertEqual([task.id for task in tasks], ["first", "second"])
        self.assertEqual(tasks[0], catalog.get("first"))
        self.assertEqual(tasks[0].name, "first")
        self.assertEqual(tasks[0].description, "说明")
        self.assertEqual(tasks[0].presentation, "persistent")
        self.assertEqual(tasks[0].script_path, first.resolve())
        self.assertEqual(tasks[1].description, "")
        self.assertEqual(tasks[1].presentation, "persistent")
        self.assertEqual(tasks[1].script_path, second.resolve())
        with self.assertRaises(FrozenInstanceError):
            tasks[0].name = "已修改"  # type: ignore[misc]
        with self.assertRaisesRegex(KeyError, "unknown task id"):
            catalog.get("missing")

    def test_signature_covers_mtime_nanoseconds_and_size(self) -> None:
        script = self._write_descriptor_task("sample", name="示例")
        catalog = self._catalog()
        catalog.reload()
        first = catalog.signature()
        self.assertEqual(first, catalog.loaded_signature)
        self.assertEqual(first[0][0], "sample")
        self.assertEqual(first[0][2], skill_manifest_path(script).stat().st_size)
        self.assertEqual(first[0][4], script.stat().st_size)

        old_stat = script.stat()
        script.write_text(script.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        os.utime(
            script,
            ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000_000),
        )

        second = catalog.signature()
        self.assertNotEqual(first, second)
        self.assertEqual(catalog.loaded_signature, first)

    def test_invalid_json_utf8_duplicates_and_nonfinite_numbers_are_rejected(self) -> None:
        invalid = (
            b'not json', b'[]', b'\xff', b'',
            b'{"protocol_version":1,"id":"bad","id":"bad","name":"Bad"}',
            b'{"protocol_version":1,"id":"bad","name":"Bad","description":NaN}',
            b'{"protocol_version":1,"id":"bad","name":"Bad"} {}',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                self._replace_root()
                script = self._write_raw_task('bad', 'raise AssertionError("must not run")')
                skill_manifest_path(script).write_bytes(raw)
                with self.assertRaises(TaskCatalogError):
                    self._catalog().reload()

    def test_invalid_protocol_fields_are_rejected(self) -> None:
        invalid_payloads = (
            {"id": "bad", "name": "Bad"},
            {"protocol_version": 2, "id": "bad", "name": "Bad"},
            {"protocol_version": True, "id": "bad", "name": "Bad"},
            {"protocol_version": 1, "id": "bad", "name": "   "},
            {"protocol_version": 1, "id": "bad", "name": "Bad", "description": 1},
            {"protocol_version": 1, "id": "bad", "name": "Bad", "presentation": "invalid"},
            {"protocol_version": 1, "id": "bad", "name": "Bad", "extra": True},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self._replace_root()
                self._write_payload_task("bad", payload)
                with self.assertRaises(TaskCatalogError):
                    self._catalog().reload()

    def test_missing_manifest_is_an_error_without_executing_entry(self) -> None:
        script = self._write_raw_task('missing', 'raise AssertionError("must not run")')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('executed code')):
            with self.assertRaisesRegex(TaskCatalogError, 'skill.json'):
                self._catalog().reload()
        write_skill_manifest(script)
        self.assertEqual(self._catalog().reload()[0].id, 'missing')

    def test_manifest_without_entry_is_rejected(self) -> None:
        script = self._write_descriptor_task('missing', name='missing')
        script.unlink()
        with self.assertRaisesRegex(TaskCatalogError, '缺少对应入口'):
            self._catalog().reload()

    def test_manifest_edits_refresh_metadata_without_running_broken_python(self) -> None:
        script = self._write_descriptor_task('sample', name='Before')
        script.write_text('not valid python !', encoding='utf-8')
        catalog = self._catalog()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('executed code')):
            catalog.reload()
            write_skill_manifest(script, name='After', parameters=[
                dict(key='count', label='次数', default=5, minimum=1, maximum=10)])
            self.assertNotEqual(catalog.signature(), catalog.loaded_signature)
            task = catalog.reload()[0]
        self.assertEqual(task.name, 'sample')
        self.assertEqual(task.parameters[0].default, 5)

    def test_failed_reload_keeps_the_previous_successful_snapshot(self) -> None:
        script = self._write_descriptor_task("stable", name="稳定任务")
        catalog = self._catalog()
        original = catalog.reload()
        original_signature = catalog.loaded_signature
        skill_manifest_path(script).write_text('broken json', encoding='utf-8')

        with self.assertRaises(TaskCatalogError):
            catalog.reload()

        self.assertEqual(catalog.tasks(), original)
        self.assertEqual(catalog.loaded_signature, original_signature)

    def test_duplicate_ids_are_rejected_defensively(self) -> None:
        catalog = self._catalog()
        first = self.root / "first" / "task.py"
        second = self.root / "second" / "task.py"
        descriptor = TaskDescriptor("duplicate", "Duplicate", "", first)
        with mock.patch.object(
            catalog,
            "_scan_task_scripts",
            return_value=((('first', first), ('second', second)), ()),
        ), mock.patch("huanzhizhi.tasks.catalog.read_skill_manifest", return_value={}), mock.patch(
            "huanzhizhi.tasks.catalog._parse_descriptor",
            side_effect=(descriptor, TaskDescriptor("duplicate", "Other", "", second)),
        ):
            with self.assertRaisesRegex(TaskCatalogError, "duplicate task id"):
                catalog.reload()

    def test_existing_non_directory_or_unreadable_root_is_an_error(self) -> None:
        self.root.parent.mkdir(parents=True, exist_ok=True)
        self.root.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(TaskCatalogError, "not a directory"):
            self._catalog().reload()

        self.root.unlink()
        self.root.mkdir()
        with mock.patch("huanzhizhi.tasks.catalog.os.scandir", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(TaskCatalogError, "cannot read tasks root"):
                self._catalog().reload()

    def test_invalid_constructor_arguments_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            TaskCatalog(str(self.root))
        with self.assertRaises(TypeError):
            TaskCatalog(self.root, test_tasks_root='not a Path')
        with self.assertRaises(ValueError):
            TaskCatalog(self.root, test_tasks_root=self.root)

    def _catalog(self) -> TaskCatalog:
        return TaskCatalog(self.root)

    def _write_descriptor_task(
        self, task_id: str, *, name: str, description: str | None = ""
    ) -> Path:
        payload: dict[str, object] = {
            "protocol_version": 1,
            "id": task_id,
            "name": name,
        }
        if description is not None:
            payload["description"] = description
        return self._write_payload_task(task_id, payload)

    def _write_payload_task(self, task_id: str, payload: object) -> Path:
        script = self._write_raw_task(task_id, 'raise AssertionError("discovery must not execute")')
        skill_manifest_path(script).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return script

    def _write_raw_task(self, task_id: str, source: str) -> Path:
        package = self.root / task_id
        package.mkdir(parents=True, exist_ok=True)
        script = package / "task.py"
        script.write_text(source, encoding="utf-8")
        return script

    def _replace_root(self) -> None:
        if self.root.exists():
            for path in sorted(self.root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            self.root.rmdir()


if __name__ == "__main__":
    unittest.main()
