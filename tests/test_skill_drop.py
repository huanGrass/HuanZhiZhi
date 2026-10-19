import os
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from tests.pet_factory import create_test_pet
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.settings import DesktopPetSettingsComponent


class SkillDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pet = create_test_pet()
        self.host = TaskHostComponent(self.pet, self.root / 'tasks')
        self.panel = DesktopPetSettingsComponent(self.pet, self.root / 'run.log', tasks=self.host)
        frame = QtGui.QPixmap(100, 100); frame.fill(QtCore.Qt.GlobalColor.white)
        self.pet.window.set_frame(frame, QtGui.QRegion(10, 10, 80, 80))
        self.good = self.root / 'demo.zip'
        with zipfile.ZipFile(self.good, 'w') as z:
            z.writestr('demo/task.py', 'raise RuntimeError("not executed")')
            z.writestr('demo/skill.json', json.dumps({'protocol_version': 1, 'id': 'demo'}))
        self.bad = self.root / 'bad.zip'; self.bad.write_bytes(b'broken')

    def tearDown(self):
        self.panel.close(); self.host.close(); self.pet.close()
        self.app.processEvents()
        self.temp.cleanup()

    def mime(self, files):
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(str(p)) for p in files])
        return mime

    def drop(self, mime, point=QtCore.QPointF(40, 40)):
        event = QtGui.QDropEvent(point, QtCore.Qt.DropAction.CopyAction | QtCore.Qt.DropAction.MoveAction,
                                mime, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        self.pet.window.dropEvent(event)
        return event

    def wait_import(self):
        deadline = QtCore.QDeadlineTimer(5000)
        while self.panel._skill_import is not None and not deadline.hasExpired():
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.panel._skill_import)

    def test_drop_batch_uses_bubble_and_formal_without_showing_dialog(self):
        with mock.patch.object(self.pet, 'show_notification') as messages:
            mime = self.mime([self.good, self.bad])
            enter = QtGui.QDragEnterEvent(QtCore.QPoint(40, 40), QtCore.Qt.DropAction.CopyAction,
                                         mime, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
            self.pet.window.dragEnterEvent(enter)
            self.assertTrue(enter.isAccepted())
            event = self.drop(mime)
            self.assertTrue(event.isAccepted())
            self.assertEqual(event.dropAction(), QtCore.Qt.DropAction.CopyAction)
            self.assertFalse(self.panel._skill_import.isVisible())
            self.assertFalse(self.panel.window.isVisible())
            self.wait_import()
            final = messages.call_args.args[0]
            self.assertIn('成功 1 个，失败 1 个', final)
            self.assertIn('bad.zip', final)
            self.assertIn('ZIP 文件损坏', final)
            self.assertEqual(self.host.catalog.tasks()[0].group, 'formal')
            self.assertTrue(self.good.exists())

    def test_reject_non_zip_remote_and_transparent_area(self):
        text = self.root / 'note.txt'; text.write_text('text')
        for mime, point in [(self.mime([text]), QtCore.QPointF(40, 40)),
                            (self.mime([self.good]), QtCore.QPointF(0, 0))]:
            self.assertFalse(self.drop(mime, point).isAccepted())
        remote = QtCore.QMimeData(); remote.setUrls([QtCore.QUrl('https://example.com/skill.zip')])
        self.assertFalse(self.drop(remote).isAccepted())
        self.assertIsNone(self.panel._skill_import)

    def test_busy_and_repeated_drop_are_reported(self):
        with mock.patch.object(self.pet, 'show_notification') as messages:
            self.host.external_work_active = True
            self.drop(self.mime([self.good]))
            self.assertIsNone(self.panel._skill_import)
            self.assertIn('请先停止', messages.call_args.args[0])
            self.host.external_work_active = False
            self.drop(self.mime([self.good]))
            current = self.panel._skill_import
            self.drop(self.mime([self.good]))
            self.assertIs(self.panel._skill_import, current)
            self.assertIn('等这批完成', messages.call_args.args[0])
            self.wait_import()

    def test_close_during_import_cleans_staging_and_thread(self):
        with mock.patch.object(self.pet, 'show_notification'):
            self.drop(self.mime([self.good]))
            dialog = self.panel._skill_import
            stage = Path(dialog.temporary.name)
            self.panel.close()
            self.assertFalse(dialog.worker.isRunning())
            self.assertFalse(stage.exists())
            self.app.processEvents()
            self.assertFalse((self.host.tasks_root / 'demo').exists())
