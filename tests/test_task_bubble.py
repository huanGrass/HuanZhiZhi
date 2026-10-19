from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from huanzhizhi.pet.task_bubble import TaskBubbleWindow


class TaskBubbleWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.bubble = TaskBubbleWindow()

    def tearDown(self) -> None:
        self.bubble.close()
        self.app.processEvents()

    def test_content_buttons_and_signals(self) -> None:
        received: list[str] = []
        self.bubble.clicked.connect(received.append)
        self.bubble.set_content(
            "选择任务",
            "请选择一个动作",
            buttons=[
                {"key": "start", "label": "开始", "kind": "primary"},
                {"key": "stop", "label": "停止", "kind": "danger", "enabled": False},
            ],
            click_key="open",
        )
        buttons = self.bubble.findChildren(QtWidgets.QPushButton)
        self.assertEqual([button.text() for button in buttons], ["开始", "停止"])
        buttons[0].click()
        self.assertEqual(received, ["start"])
        self.assertFalse(buttons[1].isEnabled())
        self.assertEqual(self.bubble.click_key, "open")

    def test_invalid_content_is_not_silently_ignored(self) -> None:
        with self.assertRaisesRegex(ValueError, "标题不能为空"):
            self.bubble.set_content("")
        with self.assertRaisesRegex(ValueError, "布局模式"):
            self.bubble.set_content("任务", layout_mode="unknown")
        with self.assertRaisesRegex(ValueError, "key 不能为空"):
            self.bubble.set_content("任务", buttons=[{"label": "开始"}])

    def test_text_controls_width_and_normal_task_names_do_not_wrap(self) -> None:
        self.bubble.set_content("任务", layout_mode="compact")
        short_width = self.bubble.width()
        self.bubble.set_content("羊了个羊数据通关", layout_mode="compact")
        self.bubble.show()
        self.app.processEvents()
        self.assertGreater(self.bubble.width(), short_width)
        label = self.bubble.title_label
        self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        self.bubble.set_content("任务", layout_mode="compact")
        self.assertEqual(self.bubble.width(), short_width)

    def test_first_display_measures_styled_font_and_long_text_wraps(self) -> None:
        self.bubble.set_content("羊了个羊数据通关", layout_mode="compact")
        self.bubble.show()
        self.app.processEvents()
        label = self.bubble.title_label
        self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        short_height = self.bubble.height()
        self.bubble.set_content("很长的任务说明" * 20, layout_mode="compact")
        self.assertLessEqual(label.width(), round(280 * self.bubble.bubble_scale))
        self.assertGreater(self.bubble.height(), short_height)

    def test_confirmation_reserves_room_for_all_buttons(self) -> None:
        self.bubble.set_content("开始", buttons=[
            {"key": "start", "label": "开始执行"},
            {"key": "parameters", "label": "参数设置"},
            {"key": "switch", "label": "换任务"},
        ])
        self.bubble.show()
        self.app.processEvents()
        for i in range(self.bubble.button_row.count()):
            button = self.bubble.button_row.itemAt(i).widget()
            self.assertGreaterEqual(button.width(), button.sizeHint().width())

    def test_compact_mode_and_display_scale_change_visuals(self) -> None:
        self.bubble.set_content("任务入口", layout_mode="compact", accent_index=3)
        initial_width = self.bubble.title_label.width()
        self.assertEqual(self.bubble._bubble_colors(), ("#f2edff", "#d8c7ff"))
        self.assertIsNone(self.bubble.graphicsEffect())
        self.bubble.set_display_scale(0.56)
        self.assertGreater(self.bubble.title_label.width(), initial_width)

    def test_pop_animation_reaches_target(self) -> None:
        self.bubble.set_content("开始任务")
        self.bubble.setGeometry(120, 130, self.bubble.sizeHint().width(), self.bubble.sizeHint().height())
        target = QtCore.QRect(self.bubble.geometry())
        self.bubble.show_pop_in()
        self.assertTrue(self.bubble.isVisible())
        QtTest.QTest.qWait(220)
        self.assertEqual(self.bubble.geometry(), target)
        self.assertAlmostEqual(self.bubble.windowOpacity(), 1.0)

    def test_large_multiline_pop_does_not_resize_top_level_window(self) -> None:
        self.bubble.set_display_scale(0.56)
        self.bubble.set_content(
            "已停止",
            "批量文件处理\n全部文件已经处理完成，可以查看处理结果",
            buttons=[
                {"key": "task_list", "label": "选择任务", "kind": "primary"}
            ],
        )
        self.bubble.move(20, 20)
        target = QtCore.QRect(self.bubble.geometry())

        self.bubble.show_pop_in()
        self.assertEqual(self.bubble.geometry(), target)
        QtTest.QTest.qWait(80)
        self.assertEqual(self.bubble.geometry(), target)
        QtTest.QTest.qWait(140)
        self.assertEqual(self.bubble.geometry(), target)

    def test_visible_content_update_has_stable_button_layout_size(self) -> None:
        self.bubble.set_display_scale(0.56)
        self.bubble.set_content(
            "运行中",
            "批量文件处理\n任务运行中",
            buttons=[
                {"key": "stop_task", "label": "停止任务", "kind": "danger"}
            ],
        )
        self.bubble.move(20, 20)
        self.bubble.show_pop_in()
        QtTest.QTest.qWait(220)

        self.bubble.set_content(
            "已停止",
            "批量文件处理\n全部文件已经处理完成，可以查看处理结果",
            buttons=[
                {"key": "task_list", "label": "选择任务", "kind": "primary"}
            ],
        )
        target = QtCore.QRect(self.bubble.geometry())

        self.assertGreater(self.bubble.button_row.sizeHint().height(), 0)
        self.assertEqual(self.bubble.size(), self.bubble.sizeHint())
        QtTest.QTest.qWait(50)
        self.assertEqual(self.bubble.geometry(), target)

    def test_replaced_visible_buttons_are_hidden_before_deferred_deletion(self) -> None:
        self.bubble.set_content(
            "运行中",
            "正在等待",
            buttons=[{"key": "stop", "label": "停止", "kind": "danger"}],
        )
        self.bubble.show()
        old_button = self.bubble.findChild(QtWidgets.QPushButton)
        self.assertIsNotNone(old_button)

        self.bubble.set_content(
            "已停止",
            "任务已经停止",
            buttons=[{"key": "start", "label": "重新开始", "kind": "primary"}],
        )

        self.assertTrue(old_button.isHidden())


if __name__ == "__main__":
    unittest.main()
