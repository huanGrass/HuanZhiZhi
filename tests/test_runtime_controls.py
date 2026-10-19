"""Runtime popover layout and interaction regressions."""
import os
import ctypes
from ctypes import wintypes
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtTest, QtWidgets
from huanzhizhi.parameters import parse_parameters, validate_values
from huanzhizhi.pet.views import StatusStrip
from tests.test_parameters import CHOICE, INTEGER


class RuntimeControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.strip = StatusStrip()
        self.addCleanup(self.strip.close)

    def open_controls(self, definitions):
        parameters = parse_parameters(definitions)
        self.strip.set_status('围棋对弈', '等待你落子', True)
        self.strip.set_runtime_parameters(parameters, validate_values(parameters, {}))
        self.strip.set_runtime_enabled(True)
        self.strip.move(100, 400)
        self.strip.show()
        self.strip.controls.click()
        self.app.processEvents()
        return self.strip.runtime_popup

    def test_compact_left_anchor_and_escape_keep_running_strip_visible(self):
        popup = self.open_controls([CHOICE])
        self.assertEqual(popup.width(), 280)
        self.assertEqual(popup.x(), self.strip.x())
        self.assertLess(popup.geometry().bottom(), self.strip.y())
        self.assertLessEqual(self.strip.width(), 310)
        self.assertEqual(self.strip.task.text(), '围棋对弈')
        self.assertTrue(self.strip.runtime_contains(popup.geometry().center()))
        self.assertFalse(popup.findChild(QtWidgets.QScrollArea).verticalScrollBar().isVisible())
        self.assertEqual([label.text() for label in popup.findChildren(QtWidgets.QLabel)], ['对弈模式'])
        QtTest.QTest.mouseClick(self.strip.controls, QtCore.Qt.MouseButton.LeftButton)
        self.assertFalse(popup.isVisible())
        QtTest.QTest.mouseClick(self.strip.controls, QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(popup.isVisible())
        QtTest.QTest.keyClick(popup, QtCore.Qt.Key.Key_Escape)
        self.assertFalse(popup.isVisible())
        self.assertTrue(self.strip.isVisible())

    def test_many_parameters_scroll_and_bottom_editor_is_reachable(self):
        popup = self.open_controls([CHOICE] + [{**INTEGER, 'key':f'count_{i}', 'label':f'次数 {i}'} for i in range(10)])
        scroll = popup.findChild(QtWidgets.QScrollArea)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(popup.width(), 280)
        editor = popup.editors['count_9']
        scroll.ensureWidgetVisible(editor)
        self.app.processEvents()
        rect = QtCore.QRect(editor.mapTo(scroll.viewport(), QtCore.QPoint()), editor.size())
        self.assertTrue(scroll.viewport().rect().contains(rect))
        changes = []
        popup.parameters_changed.connect(changes.append)
        editor.setFocus()
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Up)
        self.assertEqual(changes, [{'count_9':4}])
        popup.set_pending()
        popup.set_result({})
        self.assertEqual(editor.value(), 3)
        self.assertTrue(editor.isEnabled())

    def test_long_name_elides_and_short_name_shrinks_again(self):
        self.strip.set_status('很长的任务名称' * 10, '运行中', True)
        self.assertLessEqual(self.strip.task.width(), 112)
        self.assertNotEqual(self.strip.task.text(), self.strip.task.toolTip())
        self.strip.set_status('围棋对弈', '运行中', True)
        self.assertEqual(self.strip.task.width(), 76)
        self.assertTrue(self.strip.controls.isHidden())

    def test_pause_keeps_parameters_and_stop_available_and_blocks_repeat_requests(self):
        popup = self.open_controls([CHOICE])
        requested = []
        self.strip.pause_requested.connect(lambda: requested.append(True))
        self.strip.set_pause_state('running')
        self.strip.pause.click()
        self.strip.set_pause_state('pausing')
        self.strip.pause.click()
        self.assertEqual(requested, [True])
        self.strip.set_pause_state('paused')
        self.assertIn('继续', self.strip.pause.text())
        self.assertTrue(self.strip.controls.isEnabled())
        self.assertTrue(self.strip.stop.isEnabled())
        self.assertTrue(popup.isVisible())
        self.strip.pause.click()
        self.assertEqual(requested, [True, True])
        self.strip.set_pause_state('unavailable')
        self.assertFalse(self.strip.pause.isEnabled())

    def test_f8_registration_dispatch_and_cleanup_follow_the_task_lifecycle(self):
        user32 = Mock()
        user32.RegisterHotKey.return_value = True
        self.strip._hotkey_user32 = user32
        requested = []
        self.strip.pause_requested.connect(lambda: requested.append(True))
        self.strip.set_pause_state('running')
        self.strip.set_pause_state('running')
        user32.RegisterHotKey.assert_called_once_with(int(self.strip.winId()), 1, 0x4000, 0x77)
        message = wintypes.MSG(message=0x0312, wParam=1)
        self.strip.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
        self.assertEqual(requested, [True])
        self.strip.set_pause_state('pausing')
        self.strip.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
        self.assertEqual(requested, [True])
        self.strip.set_pause_state('paused')
        self.strip.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
        self.assertEqual(requested, [True, True])
        self.strip.set_pause_state('unavailable')
        user32.UnregisterHotKey.assert_called_once()
        self.assertFalse(self.strip._hotkey_registered)
        user32.RegisterHotKey.return_value = False
        self.strip.set_pause_state('running')
        self.assertIn('占用', self.strip.pause.toolTip())
        self.assertTrue(self.strip.pause.isEnabled())

    def test_escape_stop_is_global_even_before_pause_is_available(self):
        user32 = Mock()
        user32.RegisterHotKey.return_value = True
        self.strip._hotkey_user32 = user32
        requested = []
        self.strip.stop_requested.connect(lambda: requested.append(True))
        self.strip.set_status('测试任务', '启动中', True)
        self.strip.set_pause_state('unavailable')
        self.strip.set_status('测试任务', '运行中', True)
        user32.RegisterHotKey.assert_called_once_with(int(self.strip.winId()), 2, 0x4000, 0x1B)
        message = wintypes.MSG(message=0x0312, wParam=2)
        self.strip.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
        self.assertEqual(requested, [True])
        self.strip.set_status('测试任务', '停止中', False)
        with patch.object(QtWidgets.QWidget, 'nativeEvent', return_value=(False, 0)):
            self.strip.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
        self.assertEqual(requested, [True])
        user32.UnregisterHotKey.assert_called_once_with(int(self.strip.winId()), 2)
        self.strip.set_status('测试任务', '运行中', True)
        self.strip.close()
        self.assertEqual(user32.UnregisterHotKey.call_count, 2)

    def test_hotkey_hints_show_bindings_and_registration_failures(self):
        user32 = Mock()
        user32.RegisterHotKey.side_effect = lambda hwnd, key, modifiers, vk: key == 1
        self.strip._hotkey_user32 = user32
        self.strip.set_status('测试任务', '运行中', True)
        self.strip.set_pause_state('running')
        self.assertIn('Esc 不可用', self.strip.hotkey_hint.text())
        self.assertIn('F8 暂停 / 继续', self.strip.hotkey_hint.text())
        self.assertIn('占用', self.strip.stop.toolTip())
        self.assertTrue(self.strip.stop.isEnabled())


if __name__ == '__main__':
    unittest.main()
