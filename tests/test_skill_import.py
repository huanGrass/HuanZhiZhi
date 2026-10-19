import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtWidgets, QtTest
from huanzhizhi.tasks.storage import prepare_local_skill, export_skill
from huanzhizhi.tasks.catalog import TaskCatalog
from huanzhizhi.cloud_skills import install_prepared
from huanzhizhi.settings.import_skills import ImportSkillsDialog


class SkillImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / 'skill.zip'
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.manifest = json.dumps({'protocol_version': 1, 'id': 'demo', 'description': '介绍'})

    def package(self, entries=None):
        with zipfile.ZipFile(self.archive, 'w') as package:
            for name, content in (entries or {'demo/task.py': 'raise RuntimeError("must not execute")', 'demo/skill.json': self.manifest}).items():
                package.writestr(name, content)
        return self.archive

    def test_export_round_trip_to_formal(self):
        source = prepare_local_skill(self.package(), self.stage)
        task = TaskCatalog(source.parent).reload()[0]
        exported = self.root / 'exported.zip'
        export_skill(task, exported, description='导出介绍')
        second = self.root / 'second'; second.mkdir()
        prepared = prepare_local_skill(exported, second)
        formal = self.root / 'tasks'
        catalog = TaskCatalog(formal)
        install_prepared(prepared, formal, catalog.reload)
        self.assertEqual(catalog.tasks()[0].group, 'formal')
        self.assertEqual(catalog.tasks()[0].description, '导出介绍')

    def test_flat_package_normalizes_by_id(self):
        self.package({'task.py': '# no execution', 'skill.json': self.manifest})
        source = prepare_local_skill(self.archive, self.stage)
        self.assertEqual(source.name, 'demo')
        self.assertTrue((source / 'task.py').is_file())

    def test_invalid_packages_rejected(self):
        cases = [
            {'../escaped': 'bad'},
            {'C:/escaped': 'bad'},
            {'demo/task.py': 'def invalid(', 'demo/skill.json': self.manifest},
            {'demo/task.py': '', 'demo/skill.json': '{}'},
            {'other/task.py': '', 'other/skill.json': self.manifest},
            {'demo/task.py': ''},
            {'demo/task.py': '', 'demo/skill.json': self.manifest, 'unrelated.txt': 'bad'},
            {'demo/task.py': '', 'demo/skill.json': self.manifest, 'demo/.codex/config': 'bad'},
            {'demo/task.py': '', 'demo/skill.json': self.manifest, 'demo/A.txt': 'a', 'demo/a.txt': 'b'},
            {'demo/task.py': '', 'demo/skill.json': self.manifest, 'demo/CON.txt': 'bad'},
        ]
        for index, entries in enumerate(cases):
            with self.subTest(index=index):
                stage = self.root / str(index); stage.mkdir()
                with self.assertRaises(Exception):
                    prepare_local_skill(self.package(entries), stage)
        self.assertFalse((self.root / 'escaped').exists())

    def test_symlink_and_size_limits(self):
        with zipfile.ZipFile(self.archive, 'w') as package:
            info = zipfile.ZipInfo('demo/link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(info, '../outside')
        with self.assertRaisesRegex(ValueError, '链接'):
            prepare_local_skill(self.archive, self.stage)
        bigstage = self.root / 'big'; bigstage.mkdir()
        info = zipfile.ZipInfo('demo/large'); info.file_size = 81 * 1024 * 1024
        with mock.patch('zipfile.ZipFile.infolist', return_value=[info]):
            with self.assertRaisesRegex(ValueError, '80 MB'):
                prepare_local_skill(self.archive, bigstage)

    def test_duplicate_and_reload_failure_preserve_files(self):
        source = prepare_local_skill(self.package(), self.stage)
        formal = self.root / 'tasks'; formal.mkdir()
        (formal / 'demo').mkdir()
        (formal / 'demo' / 'original').write_text('keep')
        with self.assertRaises(FileExistsError):
            install_prepared(source, formal, lambda: None)
        self.assertEqual((formal / 'demo' / 'original').read_text(), 'keep')
        other = self.root / 'other'
        with self.assertRaisesRegex(RuntimeError, 'reload failed'):
            install_prepared(source, other, mock.Mock(side_effect=RuntimeError('reload failed')))
        self.assertTrue(source.exists())
        self.assertFalse((other / 'demo').exists())

    def test_legacy_executable_round_trip(self):
        self.package({'task-demo.exe': b'exe', 'task-demo.skill.json': self.manifest})
        source = prepare_local_skill(self.archive, self.stage)
        formal = self.root / 'tasks'
        catalog = TaskCatalog(formal)
        install_prepared(source, formal, catalog.reload)
        self.assertEqual(catalog.tasks()[0].id, 'demo')
        self.assertTrue((formal / 'task-demo.skill.json').exists())

    def test_batch_partial_success_and_duplicates(self):
        good = self.package()
        broken = self.root / 'broken.zip'; broken.write_bytes(b'not zip')
        tasks = mock.Mock()
        tasks.tasks_root = self.root / 'tasks'
        tasks.process.state = 'idle'
        tasks.external_work_active = False
        tasks.reload_tasks.side_effect = TaskCatalog(tasks.tasks_root).reload
        dialog = ImportSkillsDialog(tasks, [str(good), str(broken), str(good)], None)
        try:
            deadline = QtCore.QDeadlineTimer(5000)
            while not dialog.done_importing and not deadline.hasExpired():
                self.app.processEvents()
                QtTest.QTest.qWait(5)
            self.assertTrue(dialog.done_importing)
            self.assertEqual(dialog.count, 1)
            self.assertEqual(dialog.progress.value(), 3)
            self.assertIn('已有同名技能', dialog.results.toPlainText())
            self.assertTrue((tasks.tasks_root / 'demo' / 'task.py').is_file())
            self.assertFalse(Path(dialog.temporary.name).exists())
        finally:
            dialog.worker.wait()
            dialog.deleteLater()

    def test_cancel_does_not_publish_queued_result(self):
        tasks = mock.Mock()
        tasks.tasks_root = self.root / 'tasks'
        dialog = ImportSkillsDialog(tasks, [str(self.package())], None)
        dialog.reject()
        dialog.worker.wait()
        self.app.processEvents()
        self.assertEqual(dialog.count, 0)
        tasks.reload_tasks.assert_not_called()
        self.assertFalse(tasks.tasks_root.exists())
        dialog.deleteLater()
