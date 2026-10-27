"""Opt-in background input timing test, confined to its own native window."""
from ctypes import wintypes
from dataclasses import asdict
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from PySide6 import QtCore, QtTest, QtWidgets
from huanzhizhi.platform.screen_coordinates import qt_to_physical_global
from huanzhizhi.platform.target_window import Win32DesktopApi
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import FinishedEvent, InputRequestEvent
from tests.pet_factory import create_test_pet
from tests.skill_factory import write_skill_manifest


class BackgroundTarget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('幻之之：后台动画时序验证')
        self.setWindowFlags(QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.pet = None
        self.messages = []

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if self.pet is not None and msg.message in (0x201, 0x202):
            fingertip = qt_to_physical_global(self.pet._animation.fingertip.toPoint())
            self.messages.append((msg.message, msg.lParam & 0xffff, (msg.lParam >> 16) & 0xffff,
                                  self.pet._animation.current_frame, fingertip, time.monotonic()))
        return super().nativeEvent(event_type, message)


@unittest.skipUnless(sys.platform == 'win32' and os.environ.get('HUANZHIZHI_NATIVE_INPUT_TEST') == '1',
                     'opt-in test posts input only to its own fixture')
class NativeBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        if cls.app.platformName() != 'windows':
            raise unittest.SkipTest('requires QT_QPA_PLATFORM=windows')

    def test_subprocess_clicks_and_drag_wait_for_pet_contact(self):
        target = BackgroundTarget()
        target.setGeometry(30, 40, 520, 420)
        pet = create_test_pet(initial_position=QtCore.QPoint(650, 500))
        manager = TaskProcessManager()
        starts, results, errors, finished = [], [], [], []
        manager.error.connect(errors.append)
        target.pet = pet
        target.show()
        pet.show()
        QtTest.QTest.qWait(100)
        api = Win32DesktopApi(configure_dpi_awareness=False)
        info = api._read_window(int(target.winId()), require_visible=True)

        def event_received(event):
            if isinstance(event, InputRequestEvent):
                starts.append((event.kind, time.monotonic()))

                def done(error):
                    results.append(error)
                    manager.send_input_result(event.request_id, None if error is None else str(error))

                try:
                    pet.start_foreground_input(event, done, background=True)
                except Exception as error:
                    done(error)
            elif isinstance(event, FinishedEvent):
                finished.append(event)

        manager.event_received.connect(event_received)
        try:
            with tempfile.TemporaryDirectory() as directory:
                script = Path(directory)/'input'/'task.py'
                script.parent.mkdir()
                script.write_text(
                    'from huanzhizhi.task_sdk import TaskSession\n'
                    'from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo\n'
                    'session = TaskSession(__file__)\n'
                    'api = Win32DesktopApi()\n'
                    f'info = WindowInfo(**{asdict(info)!r})\n'
                    'api.post_click(info, 250, 200)\n'
                    'api.post_click(info, 350, 250)\n'
                    'api.post_drag(info, 200, 150, 350, 200, .3)\n'
                    "session.emit_finished('完成', '测试', '动画输入完成')\n",
                    encoding='utf-8')
                write_skill_manifest(script)
                manager.start('input', script, input_mode='background_smooth')
                deadline = time.monotonic()+10
                while manager.state != 'idle' and time.monotonic() < deadline:
                    QtTest.QTest.qWait(10)
                QtTest.QTest.qWait(30)
                self.assertEqual(manager.state, 'idle', errors)
                self.assertEqual(errors, [])
                self.assertEqual(results, [None, None, None])
                self.assertEqual(len(finished), 1)
                self.assertIsNone(finished[0].error)
                self.assertEqual([m[0] for m in target.messages], [0x201, 0x202]*3)
                presses = target.messages[::2]
                self.assertEqual([m[1:3] for m in presses], [(250, 200), (350, 250), (200, 150)])
                for press, (kind, started) in zip(presses, starts):
                    self.assertGreaterEqual(press[5]-started, .28 if kind == 'drag' else .48)
                    self.assertEqual(press[3], 12)
                    expected = QtCore.QPoint(info.left+press[1], info.top+press[2])
                    self.assertLessEqual((press[4]-expected).manhattanLength(), 2)
                self.assertEqual(target.messages[-1][1:3], (350, 200))
                for index in (1, 2):
                    self.assertGreaterEqual(starts[index][1], target.messages[index*2-1][5])
        finally:
            pet.cancel_foreground_input()
            manager.close()
            pet.close()
            target.close()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
