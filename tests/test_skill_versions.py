"""Directory-based skill versions, promotion and failure recovery."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from tests.pet_factory import create_test_pet
from tests.test_task_host import TASK_SCRIPT
from tests.skill_factory import write_skill_manifest
from huanzhizhi.settings.component import SettingsComponent
from huanzhizhi.settings.panel import DesktopPetSettingsComponent
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.tasks.storage import skill_files


class SkillVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.formal = self.root/'tasks/demo'
        self.test = self.root/'test_tasks/demo'
        for directory, version in ((self.formal, 'formal'), (self.test, 'test')):
            directory.mkdir(parents=True)
            (directory/'task.py').write_text(TASK_SCRIPT.replace(
                'emit({"type": "preview",', '# emit({"type": "preview",'), encoding='utf-8')
            write_skill_manifest(directory / 'task.py', name='演示任务')
            (directory/'version.txt').write_text(version)
        assets = Path(__file__).resolve().parents[1]/'src/huanzhizhi/assets'
        self.pet = create_test_pet()
        self.settings = SettingsComponent(self.root/'settings.ini')
        self.host = TaskHostComponent(self.pet, self.root/'tasks', settings=self.settings)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.pet.close)
        self.addCleanup(self.settings.close)
        self.addCleanup(self.host.close)

    def test_catalog_and_real_process_keep_versions_separate(self):
        self.assertEqual([t.key for t in self.host.catalog.tasks()], ['demo', 'test/demo'])
        self.assertEqual(self.host.catalog.get('test/demo').id, 'demo')
        self.assertEqual(self.host.catalog.get('test/demo').script_path, self.test/'task.py')
        errors = []
        self.host.error.connect(errors.append)
        self.host.start_task('test/demo')
        deadline = time.monotonic()+5
        while self.host.process.state != 'idle' and time.monotonic() < deadline:
            self.app.processEvents()
            QtCore.QThread.msleep(10)
        self.assertEqual(self.host.process.state, 'idle')
        self.assertEqual(errors, [])
        self.assertEqual(self.host.presentation.status[0], '已完成')

    def test_manual_replacement_backs_up_formal_and_preserves_settings(self):
        original = skill_files(self.formal)
        replacement = skill_files(self.test)
        self.host.set_task_preferences('test/demo', name='demo', description='说明', show_logs=False)
        self.host.set_task_visible('test/demo', False)
        self.settings.set_task_parameters('test/demo', {'count': 9})
        with self.assertRaisesRegex(ValueError, '确认替换'):
            self.host.promote_task('test/demo')
        self.assertEqual(skill_files(self.formal), original)
        self.host.promote_task('test/demo', replace_existing=True)
        self.assertEqual(skill_files(self.formal), replacement)
        self.assertFalse(self.test.exists())
        backup = next((self.root/'artifacts/skill_backups').glob('*/demo'))
        self.assertEqual(skill_files(backup), original)
        self.assertEqual(self.host.get_task_preferences('demo')['name'], 'demo')
        self.assertFalse(self.host.is_task_visible('demo'))
        self.assertEqual(self.settings.get_task_parameters('demo'), {'count': 9})
        self.assertEqual([t.key for t in self.host.catalog.tasks()], ['demo'])

    def test_failed_move_restores_both_versions(self):
        before = skill_files(self.formal), skill_files(self.test)
        rename = Path.rename
        def fail(source, target):
            if source == self.test:
                raise PermissionError('move denied')
            return rename(source, target)
        with patch.object(Path, 'rename', fail), self.assertRaisesRegex(PermissionError, 'move denied'):
            self.host.promote_task('test/demo', replace_existing=True)
        self.assertEqual((skill_files(self.formal), skill_files(self.test)), before)

    def test_failed_settings_write_restores_files_and_settings(self):
        self.settings.set_task_parameters('demo', {'count': 3})
        self.settings.set_task_parameters('test/demo', {'count': 7})
        before = skill_files(self.formal), skill_files(self.test)
        with patch.object(self.settings, 'sync', side_effect=[OSError('disk full'), None]), \
                self.assertRaisesRegex(OSError, 'disk full'):
            self.host.promote_task('test/demo', replace_existing=True)
        self.assertEqual((skill_files(self.formal), skill_files(self.test)), before)
        self.assertEqual(self.settings.get_task_parameters('demo'), {'count': 3})

    def test_demote_conflict_and_busy_actions_preserve_files(self):
        with self.assertRaisesRegex(ValueError, '同名测试'):
            self.host.demote_task('demo')
        self.host.external_work_active = True
        with self.assertRaisesRegex(RuntimeError, '停止'):
            self.host.promote_task('test/demo', replace_existing=True)
        self.assertTrue(self.test.exists() and self.formal.exists())

    def test_demote_then_promote_without_formal(self):
        shutil.rmtree(self.test)  # This fixture is owned by the temporary directory.
        self.host.reload_tasks()
        with patch('huanzhizhi.tasks.catalog.read_skill_manifest', side_effect=AssertionError('unexpected scan')):
            self.host.demote_task('demo')
        self.assertFalse(self.formal.exists())
        self.assertEqual(self.host.catalog.get('test/demo').group, 'test')
        with patch('huanzhizhi.tasks.catalog.read_skill_manifest', side_effect=AssertionError('unexpected scan')):
            self.host.promote_task('test/demo')
        self.assertTrue(self.formal.exists())
        self.assertFalse((self.root/'artifacts/skill_backups').exists())

    def test_replacement_updates_catalog_without_scanning_or_hiding_external_changes(self):
        descriptor = self.host.catalog.get('test/demo')
        with patch.object(self.host.catalog, '_scan_task_scripts', side_effect=AssertionError('unexpected scan')), \
                patch('huanzhizhi.tasks.catalog.read_skill_manifest', side_effect=AssertionError('unexpected subprocess')):
            self.host.promote_task('test/demo', replace_existing=True)
        current = self.host.catalog.get('demo')
        self.assertEqual(current.script_path, self.formal/'task.py')
        self.assertEqual(current.parameters, descriptor.parameters)
        self.assertEqual(self.host.catalog.loaded_signature, self.host.catalog.signature())
        with (self.formal/'task.py').open('a', encoding='utf-8') as stream:
            stream.write('\n# external edit\n')
        self.assertNotEqual(self.host.catalog.loaded_signature, self.host.catalog.signature())

    def test_new_unlisted_compiled_version_is_not_overwritten(self):
        executable = self.root/'tasks/task-demo.exe'
        executable.write_bytes(b'new external version')
        with self.assertRaisesRegex(ValueError, '出现了同名'):
            self.host.promote_task('test/demo', replace_existing=True)
        self.assertTrue(self.test.exists() and self.formal.exists())
        self.assertEqual(executable.read_bytes(), b'new external version')

    def test_ui_replacement_cancel_then_accept(self):
        panel = DesktopPetSettingsComponent(self.pet, self.root/'log.txt', tasks=self.host)
        self.addCleanup(panel.close)
        with patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.StandardButton.No):
            self.assertFalse(panel.promote_task('test/demo'))
        self.assertEqual((self.formal/'version.txt').read_text(), 'formal')
        with patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.StandardButton.Yes):
            self.assertTrue(panel.promote_task('test/demo'))
        self.assertEqual((self.formal/'version.txt').read_text(), 'test')

    def test_group_cannot_be_changed_by_preferences(self):
        with self.assertRaisesRegex(ValueError, '目录决定'):
            self.host.set_task_preferences('demo', name='demo', description='', show_logs=True, group='test')

    def test_compiled_skill_moves_and_backs_up_its_manifest_together(self):
        formal = self.root/'tasks/task-compiled.exe'
        test = self.root/'test_tasks/task-compiled.exe'
        for entry, name in ((formal, '旧版'), (test, '新版')):
            entry.write_bytes(name.encode('utf-8'))
            write_skill_manifest(entry, name=name)
        self.host.reload_tasks()
        previous = skill_files(formal)
        replacement = skill_files(test)
        self.host.promote_task('test/compiled', replace_existing=True)
        self.assertEqual(skill_files(formal), replacement)
        self.assertFalse(test.exists())
        self.assertFalse(test.with_suffix('.skill.json').exists())
        backup = next((self.root/'artifacts/skill_backups').glob('*/task-compiled.exe'))
        self.assertEqual(skill_files(backup), previous)
        self.host.demote_task('compiled')
        self.assertEqual(skill_files(test), replacement)
        self.assertEqual(self.host.catalog.get('test/compiled').name, 'compiled')
        self.assertEqual(self.host.catalog.signature(), self.host.catalog.loaded_signature)

    def test_failed_compiled_manifest_move_restores_both_versions(self):
        formal = self.root/'tasks/task-compiled.exe'
        test = self.root/'test_tasks/task-compiled.exe'
        for entry, name in ((formal, '旧版'), (test, '新版')):
            entry.write_bytes(name.encode('utf-8'))
            write_skill_manifest(entry, name=name)
        self.host.reload_tasks()
        before = skill_files(formal), skill_files(test)
        rename = Path.rename
        def fail(source, target):
            if source == test.with_suffix('.skill.json'):
                raise PermissionError('manifest move denied')
            return rename(source, target)
        with patch.object(Path, 'rename', fail), self.assertRaisesRegex(PermissionError, 'manifest move denied'):
            self.host.promote_task('test/compiled', replace_existing=True)
        self.assertEqual((skill_files(formal), skill_files(test)), before)
