from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow


class SpeechBubbleWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.bubble = SpeechBubbleWindow()

    def tearDown(self) -> None:
        self.bubble.close()
        self.app.processEvents()

    def test_show_text_rejects_empty_text_and_uses_auto_hide_timer(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能为空"):
            self.bubble.show_text("  ", 600)

        self.bubble.show_text("我在这里。", 600)
        self.app.processEvents()
        self.assertEqual(self.bubble.label.text(), "我在这里。")
        self.assertTrue(self.bubble.isVisible())
        self.assertTrue(self.bubble.hide_timer.isActive())

        QtTest.QTest.qWait(650)
        self.assertFalse(self.bubble.isVisible())
        self.assertFalse(self.bubble.hide_timer.isActive())

    def test_set_display_scale_changes_visual_size(self) -> None:
        self.bubble.show_text("缩放测试", 1000)
        self.app.processEvents()
        initial_width = self.bubble.label.width()
        initial_height = self.bubble.sizeHint().height()

        self.bubble.set_display_scale(0.56)
        self.bubble.adjustSize()
        self.app.processEvents()

        self.assertGreater(self.bubble.label.width(), initial_width)
        self.assertGreater(self.bubble.sizeHint().height(), initial_height)

    def test_repair_buttons_accept_clicks_once_and_normal_speech_restores_passthrough(self) -> None:
        calls = []
        self.bubble.show_text('出现未知情况', on_repair=lambda: calls.append('repair'))
        self.assertFalse(self.bubble.testAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertFalse(self.bubble._window_policy.input_passthrough)
        QtTest.QTest.mouseClick(self.bubble.repair_button, QtCore.Qt.MouseButton.LeftButton)
        self.bubble.repair_button.click()
        self.assertEqual(calls, ['repair'])
        self.bubble.show_text('出现未知情况', on_repair=lambda: calls.append('stale'))
        self.bubble.show_text('来了')
        self.assertFalse(self.bubble.repair_button.isVisible())
        self.assertFalse(self.bubble.dismiss_button.isVisible())
        self.assertTrue(self.bubble._window_policy.input_passthrough)
        self.assertTrue(self.bubble.hide_timer.isActive())
        self.bubble.repair_button.click()
        self.assertEqual(calls, ['repair'])

    def test_short_text_shrinks_and_long_text_wraps(self) -> None:
        self.bubble.show_text("来了")
        short_size = self.bubble.size()
        self.bubble.show_text("任务已经完成，可以查看结果")
        self.assertGreater(self.bubble.width(), short_size.width())
        self.bubble.show_text("这是一段很长的运行情况说明。" * 20)
        self.assertGreater(self.bubble.height(), short_size.height())
        self.assertLessEqual(self.bubble.label.width(), round(240 * self.bubble._bubble_scale))
        self.bubble.show_text("来了")
        self.assertEqual(self.bubble.size(), short_size)

    def test_position_prefers_above_and_stays_inside_screen(self) -> None:
        screen = self.app.primaryScreen()
        self.assertIsNotNone(screen)
        area = screen.availableGeometry()
        self.bubble.show_text("位置测试", 1000)

        centered_pet = QtCore.QRect(
            area.center().x() - 50,
            area.center().y() - 50,
            100,
            100,
        )
        self.bubble.position_around(centered_pet)
        self.assertEqual(self.bubble.tail_edge, "down")
        self.assertLess(self.bubble.geometry().bottom(), centered_pet.top())
        self.assertTrue(area.contains(self.bubble.geometry()))

        corner_pet = QtCore.QRect(area.left(), area.top(), 100, 100)
        self.bubble.position_around(corner_pet)
        self.assertTrue(area.contains(self.bubble.geometry()))
        self.assertNotEqual(self.bubble.tail_edge, "down")

    def test_window_is_non_activating_and_input_transparent(self) -> None:
        self.assertTrue(
            self.bubble.testAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        )
        self.assertTrue(
            self.bubble.testAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )
        flags = self.bubble.windowFlags()
        self.assertTrue(flags & QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual(self.bubble.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus)


if __name__ == "__main__":
    unittest.main()
