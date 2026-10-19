"""Skill-specific checks use the same runtime and gate execution."""
import json
import os
from pathlib import Path
from tests.skill_factory import write_skill_manifest
import subprocess
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from huanzhizhi.python_runtime import python_executable
from huanzhizhi.settings.environment import EnvironmentPage
from huanzhizhi.tasks.catalog import TaskCatalog, TaskCatalogError, TaskDescriptor, _parse_descriptor
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.shared_components import check_katago, supported_components, _analyze


SCRIPT = '''from huanzhizhi.task_sdk import describe
from pathlib import Path

def present():
    import sqlite3
    return "SQLite " + sqlite3.sqlite_version

def missing():
    import huanzhizhi_missing_test_dependency
    return "unexpected"

if describe(__file__, environment_checks=CHECKS):
    raise SystemExit(0)
Path("business-ran").touch()
'''


class SkillEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "demo"
        self.directory.mkdir()
        self.script = self.directory / "task.py"
        self.script.write_text(SCRIPT.replace("CHECKS", "{'SQLite': present, '缺失包': missing}"), encoding="utf-8")
        write_skill_manifest(self.script, name="测试技能", environment_checks=["SQLite", "缺失包"])

    def tearDown(self):
        self.temp.cleanup()

    def run_mode(self, mode):
        return subprocess.run([str(python_executable()), "-I", "-B", "-X", "utf8", "-m",
                               "huanzhizhi.script_runner", str(self.script), mode],
                              cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=5)

    def load_skill(self):
        tasks = TaskCatalog(self.root).reload()
        return tasks[0]

    def test_discovery_does_not_run_checks_or_require_optional_imports(self):
        task = self.load_skill()
        self.assertEqual(task.environment_checks, ("SQLite", "缺失包"))
        self.assertFalse((self.directory / "business-ran").exists())

    def test_unsatisfied_check_prevents_business_execution(self):
        manager = TaskProcessManager()
        errors = []
        manager.error.connect(errors.append)
        try:
            manager.start("demo", self.script)
            deadline = time.monotonic() + 5
            while manager.state != "idle" and time.monotonic() < deadline:
                QtTest.QTest.qWait(20)
            self.assertEqual(manager.state, "idle")
            self.assertTrue(any("缺失包" in error for error in errors), errors)
        finally:
            manager.close()
        self.assertFalse((self.directory / "business-ran").exists())

    def test_satisfied_check_allows_execution_and_no_checks_remains_compatible(self):
        for checks in ("{'SQLite': present}", "{}"):
            with self.subTest(checks=checks):
                self.script.write_text(SCRIPT.replace("CHECKS", checks), encoding="utf-8")
                write_skill_manifest(self.script, environment_checks=["SQLite"] if checks != "{}" else [])
                result = self.run_mode("--run")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.directory / "business-ran").exists())
                (self.directory / "business-ran").unlink()

    def test_bad_metadata_rejected(self):
        for checks in (True, ["x", "x"], [""], [1]):
            with self.subTest(checks=checks), self.assertRaises(TaskCatalogError):
                _parse_descriptor("demo", self.script, {"id": "demo", "name": "demo",
                                  "protocol_version": 1, "environment_checks": checks})

    def test_check_names_cannot_be_dropped_or_added_outside_manifest(self):
        for names in ([], ['SQLite'], ['SQLite', '缺失包', '额外检查']):
            with self.subTest(names=names):
                write_skill_manifest(self.script, environment_checks=names)
                result = self.run_mode('--run')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('environment_checks 完全一致', result.stderr)
                self.assertFalse((self.directory / 'business-ran').exists())

class SharedEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.page = EnvironmentPage()

    def tearDown(self):
        self.page.stop()
        self.page.close()

    def wait_finished(self):
        deadline = time.monotonic() + 8
        while self.page._process is not None and time.monotonic() < deadline:
            QtTest.QTest.qWait(20)
        self.assertIsNone(self.page._process)

    def start_script(self, code):
        original_start = QtCore.QProcess.start
        with mock.patch.object(QtCore.QProcess, 'start', lambda process, program, arguments: original_start(
                process, program, ['-X', 'utf8', '-u', '-c', code])):
            self.page.start()

    def test_catalog_visible_without_any_skills_or_probe(self):
        self.assertEqual(self.page.results.topLevelItemCount(), len(supported_components()))
        parent = self.page.results.topLevelItem(0)
        self.assertIn('KataGo', parent.text(0))
        self.assertEqual(parent.childCount(), 7)
        self.assertIsNone(self.page._process)
        self.assertTrue(self.page.detect_button.isEnabled())
        self.assertEqual(parent.text(1), '未检测')
        self.page.results.setCurrentItem(parent.child(0))
        self.assertIn('https://github.com/lightvector/KataGo', self.page.source.text())

    def test_ui_aggregates_results_and_supports_retry_and_report(self):
        for failed in (True, False):
            rows = [dict(name=name, satisfied=not (failed and name == 'katago.model'), summary='简短结果', detail='诊断路径 C:/test/runtime')
                    for name in self.page._items]
            output = ''.join(json.dumps(row) + '\n' for row in rows)
            self.start_script('print(' + repr(output) + ', end="", flush=True)')
            self.assertFalse(self.page.detect_button.isEnabled())
            self.wait_finished()
            self.assertEqual(self.page.results.topLevelItem(0).text(1), '不可用' if failed else '可用')
            self.assertIn('1 项检查不满足' if failed else '均可用', self.page.status.text())
            self.assertEqual(self.page._items['katago.engine'].text(2), '简短结果')
        self.page.copy_button.click()
        self.assertIn('cuDNN', self.app.clipboard().text())
        self.assertIn('https://', self.app.clipboard().text())
        self.assertIn('C:/test/runtime', self.app.clipboard().text())

    def test_invalid_duplicate_and_incomplete_results_cannot_mark_available(self):
        row = json.dumps(dict(name='katago.engine', satisfied=True, summary='ok', detail='ok'))
        for output in ('bad\n', row + '\n' + row + '\n', row + '\n'):
            self.start_script('print(' + repr(output) + ', end="", flush=True)')
            self.wait_finished()
            self.assertIn('检测未完成', self.page.status.text())
            self.assertEqual(self.page.results.topLevelItem(0).text(1), '未完成')

    def test_analysis_shows_running_until_result_arrives(self):
        rows = [dict(name=name, satisfied=True, summary='ok', detail='ok') for name in self.page._items]
        completed = ''.join(json.dumps(row) + '\n' for row in rows[:-1])
        self.start_script('import time; print(' + repr(completed) + ', end="", flush=True); '
                          'time.sleep(0.5); print(' + repr(json.dumps(rows[-1])) + ', flush=True)')
        deadline = time.monotonic() + 5
        while len(self.page._received) < len(rows) - 1 and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertEqual(self.page._items['pikafish.analysis'].text(1), '检测中')
        self.wait_finished()
        self.assertEqual(self.page._items['pikafish.analysis'].text(1), '满足')

    def unavailable(self, failed):
        rows = [dict(name=name, satisfied=name not in failed, summary='result', detail='result')
                for name in self.page._items]
        self.start_script('print(' + repr(''.join(json.dumps(row) + '\n' for row in rows)) + ', end="")')
        self.wait_finished()

    def test_install_button_only_available_for_installable_failures(self):
        self.assertFalse(self.page.install_button.isEnabled())
        self.unavailable({'katago.gpu', 'katago.analysis'})
        self.assertFalse(self.page.install_button.isEnabled())
        self.assertIn('GPU 或驱动', self.page.install_hint.text())
        self.unavailable({'katago.engine', 'katago.msvc', 'katago.model', 'katago.analysis'})
        self.assertTrue(self.page.install_button.isEnabled())
        self.assertIn('可安装 2 项', self.page.install_hint.text())

    def test_pikafish_reports_and_install_action_are_independent_of_katago(self):
        self.unavailable({'pikafish.model', 'pikafish.analysis'})
        self.assertEqual(self.page.results.topLevelItem(0).text(1), '可用')
        self.assertEqual(self.page.results.topLevelItem(1).text(1), '不可用')
        self.assertTrue(self.page.install_button.isEnabled())
        self.assertIn('可安装 1 项', self.page.install_hint.text())
        self.page.results.setCurrentItem(self.page._items['pikafish.model'])
        self.assertIn('official-pikafish/Pikafish', self.page.source.text())
        self.page.copy_report()
        self.assertIn('NNUE', self.app.clipboard().text())
        self.unavailable({'pikafish.analysis'})
        self.assertFalse(self.page.install_button.isEnabled())

    def test_successful_install_cleans_staging_and_rechecks(self):
        self.unavailable({'pikafish.model'})
        original_start = QtCore.QProcess.start
        event = json.dumps(dict(event='complete', message='done', received=1, total=1))
        rows = ''.join(json.dumps(dict(name=name, satisfied=True, summary='ok', detail='ok')) + '\n'
                       for name in self.page._items)
        def launch(process, program, arguments):
            code = ('print(' + repr(event) + ')' if 'huanzhizhi.component_install' in arguments else
                    'print(' + repr(rows) + ', end="")')
            return original_start(process, program, ['-X', 'utf8', '-u', '-c', code])
        with mock.patch.object(QtCore.QProcess, 'start', launch):
            self.page.install_button.click()
            stage = Path(self.page._staging.name)
            self.wait_finished()
        self.assertFalse(stage.exists())
        self.assertEqual(self.page._mode, 'check')
        self.assertEqual(self.page.results.topLevelItem(0).text(1), '可用')
        self.assertEqual(self.page.results.topLevelItem(1).text(1), '可用')
        self.assertFalse(self.page.install_button.isEnabled())
        self.assertEqual(self.page.progress.value(), 100)

    def test_cancel_install_cleans_partial_downloads_and_allows_retry(self):
        self.unavailable({'katago.model'})
        original_start = QtCore.QProcess.start
        with mock.patch.object(QtCore.QProcess, 'start', lambda process, program, arguments:
                original_start(process, program, ['-c', 'import time; time.sleep(20)'])):
            self.page.install_button.click()
        stage = Path(self.page._staging.name)
        (stage / 'partial-download').write_bytes(b'partial')
        self.page.install_button.click()
        self.assertIsNone(self.page._process)
        self.assertFalse(stage.exists())
        self.assertIn('安装已取消', self.page.status.text())
        self.assertTrue(self.page.install_button.isEnabled())

    def test_install_failure_keeps_error_in_report_and_allows_retry(self):
        self.unavailable({'katago.model'})
        original_start = QtCore.QProcess.start
        event = json.dumps(dict(event='error', message='HTTP 503', received=0, total=0))
        with mock.patch.object(QtCore.QProcess, 'start', lambda process, program, arguments:
                original_start(process, program, ['-c', 'print(' + repr(event) + '); raise SystemExit(1)'])):
            self.page.install_button.click()
        self.wait_finished()
        self.assertTrue(self.page.install_button.isEnabled())
        self.assertIn('安装未完成', self.page.status.text())
        self.page.copy_report()
        self.assertIn('HTTP 503', self.app.clipboard().text())

    def test_missing_interpreter_and_failed_start_allow_retry(self):
        with mock.patch("huanzhizhi.settings.environment.python_executable", side_effect=FileNotFoundError("缺少 Python")):
            self.page.start()
        self.assertIn("缺少 Python", self.page.status.text())
        with mock.patch("huanzhizhi.settings.environment.python_executable", return_value=Path("Z:/missing/python.exe")):
            self.page.start()
            self.wait_finished()
        self.assertIn("检测未完成", self.page.status.text())
        self.assertTrue(self.page.detect_button.isEnabled())

    def test_timeout_and_shutdown_preserve_partial_results(self):
        code = "import time; print(" + repr(json.dumps({"name": "katago.engine", "satisfied": True, "summary": "ok", "detail": "ok"})) + ", flush=True); time.sleep(20)"
        for timeout in (True, False):
            self.start_script(code)
            deadline = time.monotonic() + 5
            while not self.page._received and time.monotonic() < deadline:
                QtTest.QTest.qWait(20)
            self.assertEqual(self.page._items['katago.engine'].text(1), "满足")
            self.assertEqual(self.page._items['katago.model'].text(1), "检测中")
            if timeout:
                self.page._timer.start(10)
                self.wait_finished()
                self.assertIn("检测超时", self.page.status.text())
            else:
                self.page.stop()
                self.assertIsNone(self.page._process)
                self.assertIn("检测已停止", self.page.status.text())
            self.assertEqual(self.page._items['katago.model'].text(1), "未完成")
            self.assertEqual(self.page._items['katago.gpu'].text(1), "未检测")

    def test_missing_component_files_block_analysis_and_keep_environment_unchanged(self):
        before = os.environ.copy()
        with tempfile.TemporaryDirectory() as root, mock.patch('huanzhizhi.shared_components._gpu', return_value=('gpu', 'gpu')), \
                mock.patch('huanzhizhi.shared_components._load_dlls', side_effect=FileNotFoundError('missing runtime')), \
                mock.patch('huanzhizhi.shared_components._analyze') as analyze:
            results = {row['name']: row for row in check_katago(supported_components()[0], Path(root))}
            self.assertFalse(results['katago.engine']['satisfied'])
            self.assertFalse(results['katago.model']['satisfied'])
            self.assertIn('前置条件不满足', results['katago.analysis']['detail'])
            analyze.assert_not_called()
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual(before, dict(os.environ))

    def test_analysis_rejects_errors_and_uses_disposable_workdir(self):
        for output in ('{"id":"environment-check","error":"bad model"}\n', ''):
            with mock.patch('huanzhizhi.shared_components.subprocess.run', return_value=
                            subprocess.CompletedProcess([], 0, output, '')) as run:
                with self.assertRaises(RuntimeError):
                    _analyze(Path('engine.exe'), Path('model.gz'), {})
                self.assertFalse(run.call_args.kwargs['cwd'].exists())
                self.assertEqual(run.call_args.kwargs['timeout'], 25)
