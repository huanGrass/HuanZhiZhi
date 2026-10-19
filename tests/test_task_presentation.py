from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from huanzhizhi.tasks.presentation import (
    TASK_BUBBLE_HIDE_MS,
    TASK_BUBBLE_POP_STAGGER_MS,
    TaskListItem,
    TaskPresentationCoordinator,
)


class TaskPresentationCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        area = self.app.primaryScreen().availableGeometry()
        self.pet_rect = QtCore.QRect(
            area.center().x() - 70,
            area.center().y() - 110,
            140,
            220,
        )
        self.scale = 0.32
        self.speech_rect: QtCore.QRect | None = None
        self.coordinator = TaskPresentationCoordinator(
            pet_rect_provider=lambda: self.pet_rect,
            display_scale_provider=lambda: self.scale,
            speech_rect_provider=lambda: self.speech_rect,
        )

    def tearDown(self) -> None:
        self.coordinator.close()
        self.app.processEvents()

    def test_runtime_panel_stays_home_while_pet_moves_and_pause_overrides_logs(self):
        home = QtCore.QRect(self.pet_rect)
        self.coordinator._runtime_rect_provider = lambda: home
        self.coordinator.set_running(True, 'demo', '识别中', '测试', '正在识别')
        position = self.coordinator._strip.pos()
        self.pet_rect.translate(150, -120)
        self.coordinator.reposition()
        self.assertEqual(self.coordinator._strip.pos(), position)
        self.coordinator.set_pause_state('paused')
        self.coordinator.set_running(True, 'demo', '计算中', '测试', '正在计算')
        self.assertEqual(self.coordinator._strip.status.text(), '已暂停')
        self.coordinator.set_pause_state('running')
        self.assertEqual(self.coordinator._strip.status.text(), '计算中')
        self.coordinator.set_running(False, 'demo', '已完成', '测试', '完成')
        self.assertFalse(self.coordinator._strip.pause.isEnabled())

    def test_idle_click_shows_at_most_ten_staggered_compact_tasks(self) -> None:
        self.coordinator.set_tasks(
            TaskListItem(f"task-{index}", f"任务 {index}") for index in range(12)
        )
        delays: list[int] = []
        original_new = self.coordinator._new_bubble

        def new_bubble():
            bubble = original_new()
            original_show = bubble.show_pop_in
            bubble.show_pop_in = lambda delay_ms=0, original=original_show: (
                delays.append(delay_ms),
                original(delay_ms),
            )[-1]
            return bubble

        self.coordinator._new_bubble = new_bubble
        self.coordinator.handle_pet_clicked()

        self.assertEqual(len(self.coordinator._bubbles), 10)
        self.assertTrue(all(b.layout_mode == "compact" for b in self.coordinator._bubbles))
        self.assertEqual(delays, [TASK_BUBBLE_POP_STAGGER_MS * i for i in range(10)])
        self.assertTrue(self.coordinator._auto_hide_timer.isActive())
        self.assertEqual(self.coordinator._auto_hide_timer.interval(), TASK_BUBBLE_HIDE_MS)

    def test_empty_list_shows_settings_guidance(self) -> None:
        self.coordinator.handle_pet_clicked()
        bubble = self.coordinator._bubbles[0]
        self.assertEqual(bubble.title_label.text(), "没有可显示的任务")
        self.assertEqual(bubble.detail_label.text(), "可在设置中开启任务显示。")
        self.assertIsNone(bubble.findChild(QtWidgets.QPushButton))

    def test_task_list_keeps_compact_columns_centered_beside_pet(self) -> None:
        for count in (3, 5):
            with self.subTest(count=count):
                self.coordinator.close_bubbles()
                self.coordinator.set_tasks(
                    TaskListItem(str(index), f"任务 {index}") for index in range(count)
                )
                self.coordinator.handle_pet_clicked()
                QtTest.QTest.qWait(320)
                for offset in (0, 1):
                    column = self.coordinator._bubbles[offset::2]
                    rect = QtCore.QRect(column[0].geometry())
                    for previous, bubble in zip(column, column[1:]):
                        self.assertEqual(bubble.y() - previous.geometry().bottom() - 1, 8)
                        rect = rect.united(bubble.geometry())
                    self.assertLessEqual(abs(rect.center().y() - self.pet_rect.center().y()), 1)
                    if offset == 0:
                        self.assertLess(rect.right(), self.pet_rect.left())
                        self.assertEqual(len({b.geometry().right() for b in column}), 1)
                    else:
                        self.assertGreater(rect.left(), self.pet_rect.right())
                        self.assertEqual(len({b.x() for b in column}), 1)

    def test_idle_pet_click_toggles_the_existing_task_menu(self):
        self.coordinator.set_tasks([TaskListItem('alpha', '任务甲')])
        self.coordinator.handle_pet_clicked()
        self.assertTrue(self.coordinator.has_bubbles)
        self.coordinator.handle_pet_clicked()
        self.assertFalse(self.coordinator.has_bubbles)
        self.coordinator.handle_pet_clicked()
        self.assertTrue(self.coordinator.has_bubbles)

    def test_chat_blocks_task_details_but_preserves_the_running_stop_control(self):
        self.coordinator.set_running(True, 'alpha', '运行中', '任务甲', '正在执行')
        self.coordinator.handle_pet_clicked()
        self.coordinator.set_chat_open(True)
        self.assertFalse(self.coordinator.has_bubbles)
        self.coordinator.handle_pet_clicked()
        self.coordinator._strip.details_requested.emit()
        self.assertFalse(self.coordinator.has_bubbles)
        self.assertTrue(self.coordinator._strip.isVisible())
        stops = []
        self.coordinator.stop_task_requested.connect(lambda: stops.append(True))
        self.coordinator._strip.stop_requested.emit()
        self.assertEqual(stops, [True])
        self.coordinator.set_chat_open(False)
        self.coordinator.handle_pet_clicked()
        self.assertTrue(self.coordinator.has_bubbles)

    def test_selection_check_start_status_and_stop_flow(self) -> None:
        self.coordinator.set_tasks([TaskListItem("alpha", "任务甲", "任务甲的说明")])
        starts: list[str] = []
        stops: list[bool] = []
        self.coordinator.start_task_requested.connect(starts.append)
        self.coordinator.stop_task_requested.connect(lambda: stops.append(True))
        self.coordinator.handle_pet_clicked()
        self.coordinator._bubbles[0].clicked.emit("select:alpha")

        check = self.coordinator._bubbles[0]
        self.assertEqual(check.title_label.text(), "任务甲")
        self.assertIn("任务甲的说明", check.detail_label.text())
        self.assertFalse(self.coordinator._auto_hide_timer.isActive())
        check.clicked.emit("start_task")

        self.assertEqual(starts, ["alpha"])
        self.assertFalse(self.coordinator.has_bubbles)
        self.assertTrue(self.coordinator._strip.isVisible())
        self.coordinator.handle_pet_clicked()
        status = self.coordinator._bubbles[0]
        self.assertEqual(status.title_label.text(), "运行中")
        self.assertIn("任务甲\n任务运行中", status.detail_label.text())
        self.assertEqual(
            [button.text() for button in status.findChildren(QtWidgets.QPushButton)],
            ["停止任务"],
        )
        self.assertFalse(self.coordinator._auto_hide_timer.isActive())
        status.clicked.emit("stop_task")
        self.assertEqual(stops, [True])
        self.assertFalse(self.coordinator._running)

    def test_launch_only_task_closes_bubbles_after_starting(self) -> None:
        self.coordinator.set_tasks(
            [TaskListItem("monitor", "窗口监控", "打开独立面板", "launch_only")]
        )
        starts: list[str] = []
        self.coordinator.start_task_requested.connect(starts.append)
        self.coordinator.handle_pet_clicked()
        self.coordinator._bubbles[0].clicked.emit("select:monitor")

        check = self.coordinator._bubbles[0]
        self.assertIn("打开独立面板", check.detail_label.text())
        check.clicked.emit("start_task")

        self.assertEqual(starts, ["monitor"])
        self.assertFalse(self.coordinator._bubbles)

    def test_signal_receiver_can_supply_authoritative_run_state(self) -> None:
        self.coordinator.set_tasks([TaskListItem("alpha", "任务甲")])
        self.coordinator.start_task_requested.connect(
            lambda task_id: self.coordinator.set_running(
                True,
                task_id,
                "识别中",
                "任务甲",
                "正在寻找目标窗口",
            )
        )
        self.coordinator.handle_pet_clicked()
        self.coordinator._bubbles[0].clicked.emit("select:alpha")
        self.coordinator._bubbles[0].clicked.emit("start_task")
        self.assertFalse(self.coordinator.has_bubbles)
        self.coordinator.handle_pet_clicked()
        bubble = self.coordinator._bubbles[0]
        self.assertEqual(bubble.title_label.text(), "识别中")
        self.assertIn("正在寻找目标窗口", bubble.detail_label.text())

    def test_running_pet_click_shows_persistent_status_and_stop_button(self) -> None:
        self.coordinator.set_running(
            True, "alpha", "运行中", "任务甲", "正在执行"
        )
        self.coordinator.handle_pet_clicked()
        bubble = self.coordinator._bubbles[0]
        self.assertEqual(bubble.title_label.text(), "运行中")
        self.assertEqual(
            [button.text() for button in bubble.findChildren(QtWidgets.QPushButton)],
            ["停止任务"],
        )
        self.assertFalse(self.coordinator._auto_hide_timer.isActive())

    def test_completed_result_expires_even_after_opening_details(self) -> None:
        self.coordinator.set_running(True, "alpha", "运行中", "任务甲", "正在执行")
        self.coordinator.set_running(False, "alpha", "已完成", "任务甲", "处理完成")
        self.assertTrue(self.coordinator._result_hide_timer.isActive())
        self.assertEqual(self.coordinator._result_hide_timer.interval(), 3000)
        self.assertFalse(self.coordinator._strip.stop.isVisible())
        self.coordinator.handle_pet_clicked()
        self.coordinator._result_hide_timer.start(1)
        QtTest.QTest.qWait(30)
        self.assertFalse(self.coordinator.has_bubbles)
        self.assertFalse(self.coordinator._strip.isVisible())
        self.assertEqual(self.coordinator.status, ("已完成", "任务甲", "处理完成"))

    def test_new_run_cancels_previous_result_expiry(self) -> None:
        self.coordinator.set_running(True, "alpha")
        self.coordinator.set_running(False, "alpha", "已完成")
        self.coordinator._result_hide_timer.start(1)
        self.coordinator.set_running(True, "alpha", "运行中")
        self.coordinator.handle_pet_clicked()
        QtTest.QTest.qWait(30)
        self.assertFalse(self.coordinator._result_hide_timer.isActive())
        self.assertTrue(self.coordinator._strip.isVisible())
        self.assertTrue(self.coordinator.has_bubbles)

    def test_detail_stop_button_disables_after_request(self) -> None:
        requests = []
        def stop():
            requests.append(True)
            self.coordinator.set_running(True, "alpha", "停止中", "任务甲", "正在停止任务")
        self.coordinator.stop_task_requested.connect(stop)
        self.coordinator.set_running(True, "alpha", "运行中")
        self.coordinator.handle_pet_clicked()
        button = self.coordinator._bubbles[0].button_row.itemAt(0).widget()
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        button = self.coordinator._bubbles[0].button_row.itemAt(0).widget()
        self.assertEqual(button.text(), "停止中…")
        self.assertFalse(button.isEnabled())
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [True])

    def test_outside_click_only_closes_nonrunning_bubbles(self) -> None:
        self.coordinator.set_tasks([TaskListItem("alpha", "任务甲")])
        self.coordinator.handle_pet_clicked()
        QtTest.QTest.qWait(40)
        bubble_point = self.coordinator._bubbles[0].geometry().center()
        self.assertFalse(self.coordinator.handle_outside_click(bubble_point))
        self.assertTrue(self.coordinator._bubbles)
        self.assertTrue(self.coordinator.handle_outside_click(QtCore.QPoint(-10_000, -10_000)))
        self.assertFalse(self.coordinator._bubbles)

        self.coordinator.set_running(True, "alpha", task_name="任务甲")
        self.coordinator.handle_pet_clicked()
        self.assertFalse(self.coordinator.handle_outside_click(QtCore.QPoint(-10_000, -10_000)))
        self.assertTrue(self.coordinator._bubbles)

    def test_layout_avoids_visible_speech_and_updates_settings(self) -> None:
        self.coordinator.set_tasks([TaskListItem("alpha", "任务甲")])
        self.speech_rect = QtCore.QRect(
            self.pet_rect.right() + 1,
            self.pet_rect.center().y() - 80,
            220,
            160,
        )
        self.coordinator.handle_pet_clicked()
        for bubble in self.coordinator._bubbles:
            self.assertFalse(bubble.geometry().intersects(self.speech_rect))

        old_width = self.coordinator._bubbles[0].title_label.width()
        self.coordinator.set_display_scale(0.56)
        self.assertGreater(self.coordinator._bubbles[0].title_label.width(), old_width)

    def test_invalid_items_and_provider_results_are_explicit_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "任务 ID"):
            TaskListItem("", "任务")
        with self.assertRaisesRegex(ValueError, "展示模式"):
            TaskListItem("alpha", "任务甲", presentation="unknown")
        with self.assertRaisesRegex(ValueError, "重复 ID"):
            self.coordinator.set_tasks(
                [TaskListItem("same", "甲"), TaskListItem("same", "乙")]
            )

        bad = TaskPresentationCoordinator(
            pet_rect_provider=lambda: None,
            display_scale_provider=lambda: 0.32,
            speech_rect_provider=lambda: None,
        )
        bad.set_tasks([TaskListItem("alpha", "任务甲")])
        with self.assertRaisesRegex(ValueError, "pet_rect_provider"):
            bad.handle_pet_clicked()
        bad.close()

        provider_cases = (
            (
                dict(
                    pet_rect_provider=lambda: self.pet_rect,
                    display_scale_provider=lambda: "large",
                    speech_rect_provider=lambda: None,
                ),
                (TypeError, "display_scale_provider"),
            ),
            (
                dict(
                    pet_rect_provider=lambda: self.pet_rect,
                    display_scale_provider=lambda: 0.32,
                    speech_rect_provider=lambda: "visible",
                ),
                (ValueError, "speech_rect_provider"),
            ),
        )
        for providers, (error_type, message) in provider_cases:
            with self.subTest(message=message):
                coordinator = TaskPresentationCoordinator(**providers)
                coordinator.set_tasks([TaskListItem("alpha", "任务甲")])
                with self.assertRaisesRegex(error_type, message):
                    coordinator.handle_pet_clicked()
                coordinator.close()

    def test_close_rejects_future_operations(self) -> None:
        self.coordinator.close()
        with self.assertRaisesRegex(RuntimeError, "已经关闭"):
            self.coordinator.handle_pet_clicked()


if __name__ == "__main__":
    unittest.main()
