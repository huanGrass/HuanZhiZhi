import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtTest, QtWidgets
from huanzhizhi.settings.cloud import CloudSkillsPage
from huanzhizhi.tasks.catalog import TaskCatalog
from tests.test_cloud_skills import entry


class Process(QtCore.QObject):
    state_changed = QtCore.Signal(str)
    state = 'idle'


class Host(QtCore.QObject):
    tasks_changed = QtCore.Signal()
    def __init__(self, root):
        super().__init__()
        self.tasks_root = root
        self.process = Process()
        self.external_work_active = False
        self.catalog = TaskCatalog(root)
    def reload_tasks(self):
        self.catalog.reload()
        self.tasks_changed.emit()


class CloudPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = Host(Path(self.temp.name) / 'tasks')
        self.page = CloudSkillsPage(self.host)
        self.items = [entry(), {**entry(), 'id': '砖了个砖提示', 'category': '游戏助手'}]

    def tearDown(self):
        self.page.stop(); self.page.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self):
        deadline = time.monotonic() + 5
        while self.page.worker is not None and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.page.worker)

    def load(self):
        with patch('huanzhizhi.settings.cloud.fetch_catalog', return_value=self.items):
            self.page.refresh()
            self.wait()

    def test_categories_and_installed_state(self):
        self.host.tasks_root.mkdir()
        (self.host.tasks_root / '测试技能').mkdir()
        self.load()
        self.assertEqual(self.page.category.count(), 3)
        self.assertEqual(self.page._buttons['测试技能'].text(), '已安装')
        self.assertFalse(self.page._buttons['测试技能'].isEnabled())
        self.page.category.setCurrentText('游戏助手')
        self.assertEqual(list(self.page._buttons), ['砖了个砖提示'])

    def test_busy_host_prevents_install(self):
        self.load()
        self.host.external_work_active = True
        self.page.install(self.items[0])
        self.assertIsNone(self.page.worker)
        self.assertIn('请先停止', self.page.status.text())

    def test_network_failure_preserves_previous_catalog_and_allows_retry(self):
        self.load()
        with patch('huanzhizhi.settings.cloud.fetch_catalog', side_effect=OSError('offline')):
            self.page.refresh(); self.wait()
        self.assertEqual(len(self.page._items), 2)
        self.assertIn('offline', self.page.status.text())
        self.assertTrue(self.page.refresh_button.isEnabled())

    def test_worker_install_reloads_formal_skills_without_running_them(self):
        import json
        def prepare(item, directory, progress):
            source = directory / 'stage' / item['id']; source.mkdir(parents=True)
            (source / 'task.py').write_text('raise RuntimeError("not executed")', encoding='utf-8')
            (source / 'skill.json').write_text(json.dumps({'protocol_version': 1, 'id': item['id']}), encoding='utf-8')
            return source
        self.load()
        with patch('huanzhizhi.settings.cloud.prepare_skill', side_effect=prepare):
            self.page.install(self.items[0]); self.wait()
        self.assertEqual(self.host.catalog.tasks()[0].group, 'formal')
        self.assertEqual(self.page._buttons['测试技能'].text(), '已安装')
        self.assertIsNone(self.page._temporary)

    def test_running_skill_at_download_completion_blocks_publication(self):
        def prepare(item, directory, progress):
            self.host.process.state = 'running'
            return directory / item['id']
        self.load()
        with patch('huanzhizhi.settings.cloud.prepare_skill', side_effect=prepare):
            self.page.install(self.items[0]); self.wait()
        self.assertFalse(self.host.tasks_root.exists())
        self.assertIn('本次未安装', self.page.status.text())

    def test_cancellation_prevents_result_publication(self):
        def fetch(progress):
            time.sleep(0.05)
            progress(1)
            return self.items
        with patch('huanzhizhi.settings.cloud.fetch_catalog', side_effect=fetch):
            self.page.refresh(); self.page.cancel(); self.wait()
        self.assertEqual(self.page._items, [])
        self.assertIn('取消', self.page.status.text())


if __name__ == '__main__':
    unittest.main()
