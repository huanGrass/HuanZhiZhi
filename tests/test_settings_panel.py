from __future__ import annotations

import os
import gc
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from huanzhizhi.settings import DesktopPetSettingsComponent, SettingsComponent
from huanzhizhi.widgets import ComboBox, ToggleSwitch
from huanzhizhi.tasks.catalog import TaskDescriptor
from huanzhizhi.tasks.component import TaskHostComponent
from tests.pet_factory import create_test_pet
from tests.test_task_host import TASK_SCRIPT
from tests.skill_factory import write_skill_manifest


class DesktopPetSettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.assets = Path(__file__).parents[1] / "src" / "huanzhizhi" / "assets"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.pet = create_test_pet()
        self.log_path = Path(self.temp.name) / "run.log"
        self.log_path.write_text("[12:00:00] 已启动\n", encoding="utf-8")
        self.panel = DesktopPetSettingsComponent(self.pet, self.log_path)

    def tearDown(self) -> None:
        self.panel.close()
        self.pet.close()
        self.temp.cleanup()

    def test_closed_panel_has_no_event_filters_during_cycle_collection(self) -> None:
        original_filter = DesktopPetSettingsComponent.eventFilter
        closed_ids = set()
        events = []

        def trace_filter(component, watched, event):
            if id(component) in closed_ids:
                events.append(event.type())
                return False
            return original_filter(component, watched, event)

        def create_closed_panel():
            pet = create_test_pet()
            host = mock.Mock()
            host.catalog.tasks.return_value = ()
            host.process.state = "idle"
            panel = DesktopPetSettingsComponent(pet, self.log_path, tasks=host)
            panel.close()
            pet.close()
            closed_ids.add(id(panel))
            # Mock signal call records retain bound slots, forming a cycle.
            return weakref.ref(panel)

        gc_enabled = gc.isenabled()
        gc.disable()
        try:
            with mock.patch.object(DesktopPetSettingsComponent, "eventFilter", trace_filter):
                panel_ref = create_closed_panel()
                gc.collect()
                self.assertIsNone(panel_ref())
                self.assertEqual(events, [])
        finally:
            if gc_enabled:
                gc.enable()

    def test_panel_uses_sidebar_and_card_layout(self) -> None:
        self.assertEqual(self.panel.window.windowTitle(), "幻之之设置")
        self.assertEqual(self.panel._navigation.count(), 5)
        self.panel._navigation.setCurrentRow(4)
        self.assertEqual(self.panel._navigation.currentItem().text(), "外部组件")
        self.assertIs(self.panel._pages.currentWidget(), self.panel._environment_page)
        self.panel._navigation.setCurrentRow(0)
        self.assertEqual(self.panel._navigation.currentItem().text(), "常规")
        self.assertEqual(self.panel._title.text(), "常规")

    def test_log_page_loads_current_log_and_appends_live_messages(self) -> None:
        self.panel.show()
        self.panel._navigation.setCurrentRow(1)
        self.assertEqual(self.panel._log_view.toPlainText(), "[12:00:00] 已启动\n")

        self.panel.append_log("实时消息")

        self.assertEqual(self.panel._log_view.toPlainText(), "[12:00:00] 已启动\n实时消息\n")
        self.assertFalse(hasattr(self.panel, "_refresh_log_button"))
        self.assertFalse(hasattr(self.panel, "_copy_log_button"))


    def test_mouse_control_updates_pet_and_follows_external_changes(self) -> None:
        QtTest.QTest.mouseClick(
            self.panel._mouse_checkbox, QtCore.Qt.MouseButton.LeftButton
        )
        self.assertTrue(self.pet.mouse_point_visible)

        self.pet.set_mouse_point_visible(False)
        self.assertFalse(self.panel._mouse_checkbox.isChecked())


    def test_scale_control_updates_pet(self) -> None:
        self.assertIsInstance(self.panel._scale_combo, ComboBox)
        index = self.panel._scale_combo.findData(0.40)
        self.assertGreaterEqual(index, 0)
        self.panel._scale_combo.setCurrentIndex(index)
        self.assertEqual(self.pet.display_scale, 0.40)

    def test_theme_changes_pet_and_follows_external_changes_without_settings(self) -> None:
        self.assertFalse(self.panel._input_mode_combo.isEnabled())
        self.assertTrue(self.panel._theme_combo.isEnabled())
        self.assertEqual(self.panel._theme_combo.count(), 2)
        self.assertEqual(self.panel._theme_combo.findData("original"), -1)
        self.pet.set_theme("broom")
        self.panel._theme_combo.setCurrentIndex(self.panel._theme_combo.findData("ufo"))
        self.assertEqual(self.pet.theme, "ufo")
        self.pet.set_theme("broom")
        self.assertEqual(self.panel._theme_combo.currentData(), "broom")

    def test_input_mode_loads_and_persists_immediately(self) -> None:
        path = Path(self.temp.name) / "settings.ini"
        settings = SettingsComponent(path)
        settings.set_input_mode("foreground")
        self.panel.close()
        self.panel = DesktopPetSettingsComponent(self.pet, self.log_path, settings=settings)
        try:
            self.assertEqual(self.panel._input_mode_combo.currentData(), "foreground")
            self.panel._input_mode_combo.setCurrentIndex(self.panel._input_mode_combo.findData("background"))
            reopened = SettingsComponent(path)
            try:
                self.assertEqual(reopened.get_input_mode(), "background")
            finally:
                reopened.close()
        finally:
            self.panel.close()
            settings.close()

    def test_input_options_cannot_change_during_task_or_ai_work(self) -> None:
        settings = SettingsComponent(Path(self.temp.name) / "settings.ini")
        host = mock.Mock()
        host.catalog.tasks.return_value = ()
        host.process.state = "idle"
        self.panel.close()
        self.panel = DesktopPetSettingsComponent(self.pet, self.log_path, tasks=host, settings=settings)
        try:
            for kind in ("task", "ai"):
                with self.subTest(kind=kind):
                    host.process.state = "running" if kind == "task" else "idle"
                    settings.ai_work_active = lambda: kind == "ai"
                    self.panel.refresh_ai_availability()
                    self.assertFalse(self.panel._input_mode_combo.isEnabled())
                    self.assertFalse(self.panel._theme_combo.isEnabled())
                    # Programmatic changes must also respect the active-work guard.
                    self.panel._input_mode_combo.setCurrentIndex(self.panel._input_mode_combo.findData("foreground"))
                    self.panel._theme_combo.setCurrentIndex(self.panel._theme_combo.findData("broom"))
                    self.assertEqual(settings.get_input_mode(), "background")
                    self.assertEqual(self.pet.theme, "ufo")
                    self.assertEqual(self.panel._input_mode_combo.currentData(), "background")
                    self.assertEqual(self.panel._theme_combo.currentData(), "ufo")
            host.process.state = "idle"
            settings.ai_work_active = lambda: False
            self.panel.refresh_ai_availability()
            self.assertTrue(self.panel._input_mode_combo.isEnabled())
            self.assertTrue(self.panel._theme_combo.isEnabled())
        finally:
            self.panel.close()
            settings.close()

    def test_switch_animates_thumb_after_mouse_toggle(self) -> None:
        switch = self.panel._mouse_checkbox
        self.assertIsInstance(switch, ToggleSwitch)
        QtTest.QTest.mouseClick(switch, QtCore.Qt.MouseButton.LeftButton)
        QtTest.QTest.qWait(70)
        self.assertGreater(switch.thumbPosition, 0.0)
        self.assertLess(switch.thumbPosition, 1.0)
        QtTest.QTest.qWait(100)
        self.assertEqual(switch.thumbPosition, 1.0)

    def test_compact_tasks_reflow_without_clipping_controls(self) -> None:
        descriptors = tuple(
            TaskDescriptor(str(index), "超长任务名称" * 20 if index == 0 else f"任务 {index}", "任务说明", Path("task.py"))
            for index in range(16)
        )
        host = mock.Mock()
        host.catalog.tasks.return_value = descriptors
        host.is_task_visible.return_value = True
        host.get_task_preferences.side_effect = lambda task_id: {
            "name": descriptors[int(task_id)].name,
            "description": "任务说明 <原文>\n第二行",
            "show_logs": True,
        }
        self.panel.close()
        self.panel = DesktopPetSettingsComponent(self.pet, self.log_path, tasks=host)
        self.panel._navigation.setCurrentRow(2)
        self.panel.window.show()
        cards = self.panel._task_cards
        for width, columns in ((1040, 2), (640, 1), (1440, 3), (1040, 2)):
            with self.subTest(width=width):
                self.panel.window.resize(width, 540)
                QtTest.QTest.qWait(50)
                rects = [cards.visualItemRect(cards.item(index)) for index in range(cards.count())]
                self.assertEqual(sum(rect.y() == rects[0].y() for rect in rects), columns)
                for index, rect in enumerate(rects):
                    self.assertLessEqual(rect.height(), 42)
                    self.assertGreaterEqual(rect.left(), 0)
                    self.assertLess(rect.right(), cards.viewport().width())
                    card = cards.itemWidget(cards.item(index))
                    for control in card.findChildren(QtWidgets.QAbstractButton):
                        self.assertTrue(card.rect().contains(control.geometry()))
        self.panel.window.resize(640, 440)
        QtTest.QTest.qWait(50)
        cards.scrollToItem(cards.item(cards.count() - 1))
        self.assertTrue(cards.viewport().rect().contains(cards.visualItemRect(cards.item(cards.count() - 1))))
        title = self.panel._task_titles["0"]
        self.assertEqual(title.accessibleName(), descriptors[0].name)
        self.assertNotEqual(title.text(), descriptors[0].name)
        cards.scrollToTop()
        detail = self.panel._task_details["0"]
        self.assertIn("&lt;原文&gt;<br>第二行", detail.toolTip())
        with mock.patch.object(QtWidgets.QToolTip, "showText") as show_tip:
            QtTest.QTest.mouseClick(detail, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(show_tip.call_args.args[1], detail.toolTip())

    def test_task_switch_updates_list_and_survives_host_recreation(self) -> None:
        root = Path(self.temp.name) / "tasks"
        task_dir = root / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "task.py").write_text(TASK_SCRIPT, encoding="utf-8")
        write_skill_manifest(task_dir / "task.py", name="演示任务")
        settings = SettingsComponent(Path(self.temp.name) / "settings.ini")
        host = TaskHostComponent(self.pet, root, settings=settings)
        self.panel.close()
        self.panel = DesktopPetSettingsComponent(self.pet, self.log_path, tasks=host)
        try:
            self.panel.show()
            self.panel._navigation.setCurrentRow(2)
            self.assertTrue(self.panel._task_switches["demo"].isChecked())
            host.presentation.handle_pet_clicked()
            QtTest.QTest.mouseClick(
                self.panel._task_switches["demo"], QtCore.Qt.MouseButton.LeftButton
            )
            self.assertFalse(host.is_task_visible("demo"))
            self.assertEqual(host.presentation._tasks, ())
            self.assertEqual(settings.get_hidden_task_ids(), ("demo",))
            self.assertFalse(self.panel._task_switches["demo"].isChecked())
            QtTest.QTest.mouseClick(
                self.panel._task_log_switches["demo"], QtCore.Qt.MouseButton.LeftButton
            )
            self.assertFalse(host.get_task_preferences("demo")["show_logs"])

            def edit_dialog(dialog):
                name = dialog.findChild(QtWidgets.QLineEdit, "taskNameEdit")
                description = dialog.findChild(QtWidgets.QPlainTextEdit, "taskDescriptionEdit")
                group = dialog.findChild(QtWidgets.QLabel, "taskGroupLabel")
                self.assertEqual(group.text(), "当前分组：正式")
                buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
                save = next(button for button in buttons.buttons() if button.text() == "保存")
                name.setText("  ")
                save.click()
                self.assertIn("任务名称不能为空", dialog.findChild(QtWidgets.QLabel, "taskEditError").text())
                self.assertEqual(host.get_task_preferences("demo")["name"], "demo")
                name.setText("我的任务")
                description.setPlainText("我的介绍\n第二行")
                save.click()
                self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
                return dialog.result()

            with mock.patch.object(QtWidgets.QDialog, "exec", edit_dialog):
                self.panel._edit_task("demo")
            def move_dialog(dialog):
                dialog.findChild(QtWidgets.QPushButton, "taskGroupMove").click()
                return dialog.result()
            with mock.patch.object(QtWidgets.QDialog, "exec", move_dialog):
                self.panel._edit_task("我的任务")
            self.panel._task_groups.setCurrentIndex(0)
            self.assertTrue(self.panel._task_items["test/我的任务"].isHidden())
            self.assertTrue(self.panel._task_empty_label.isVisible())
            self.assertEqual(self.panel._task_groups.tabText(0), "正式（0）")
            self.assertEqual(self.panel._task_groups.tabText(1), "测试（1）")
            self.panel._task_groups.setCurrentIndex(1)
            self.assertEqual(self.panel._task_titles["test/我的任务"].text(), "我的任务")
            self.assertEqual(self.panel._task_details["test/我的任务"].accessibleDescription(), "我的介绍\n第二行")
            self.assertIn("我的介绍<br>第二行", self.panel._task_details["test/我的任务"].toolTip())
            self.panel.close()
            host.close()
            settings.close()
            settings = SettingsComponent(Path(self.temp.name) / "settings.ini")
            host = TaskHostComponent(self.pet, root, settings=settings)
            self.assertFalse(host.is_task_visible("test/我的任务"))
            self.panel = DesktopPetSettingsComponent(self.pet, self.log_path, tasks=host)
            self.assertTrue(self.panel._task_items["test/我的任务"].isHidden())
            self.panel._task_groups.setCurrentIndex(1)
            self.assertFalse(self.panel._task_switches["test/我的任务"].isChecked())
            self.assertFalse(self.panel._task_log_switches["test/我的任务"].isChecked())
            self.assertEqual(host.get_task_preferences("test/我的任务"), {
                "name": "我的任务", "description": "我的介绍\n第二行", "show_logs": False,
                "group": "test",
            })
            QtTest.QTest.mouseClick(
                self.panel._task_switches["test/我的任务"], QtCore.Qt.MouseButton.LeftButton
            )
            self.assertTrue(host.is_task_visible("test/我的任务"))
            self.assertNotIn("test/我的任务", settings.get_hidden_task_ids())
            QtTest.QTest.mouseClick(
                self.panel._task_log_switches["test/我的任务"], QtCore.Qt.MouseButton.LeftButton
            )
            self.assertEqual(host.get_task_preferences("test/我的任务")["group"], "test")
        finally:
            self.panel.close()
            host.close()
            settings.close()

    def test_group_switch_reuses_widgets_and_keeps_hidden_tasks_current(self) -> None:
        descriptors = tuple(
            TaskDescriptor(str(index), f"任务 {index}", "任务说明", Path("task.py"))
            for index in range(30)
        )
        preferences = {
            descriptor.id: {
                "name": descriptor.name, "description": descriptor.description,
                "show_logs": True, "group": "formal" if index % 2 == 0 else "test",
            }
            for index, descriptor in enumerate(descriptors)
        }
        host = mock.Mock()
        host.catalog.tasks.return_value = descriptors
        host.is_task_visible.return_value = True
        host.get_task_preferences.side_effect = lambda task_id: dict(preferences[task_id])
        self.panel.close()
        self.panel = DesktopPetSettingsComponent(self.pet, self.log_path, tasks=host)
        self.panel._navigation.setCurrentRow(2)
        self.panel.window.show()
        self.app.processEvents()
        cards = self.panel._task_cards
        original_widgets = {
            task_id: cards.itemWidget(item) for task_id, item in self.panel._task_items.items()
        }
        for group_index in (1, 0, 1, 0, 1, 0):
            QtTest.QTest.mouseClick(
                self.panel._task_groups, QtCore.Qt.MouseButton.LeftButton,
                pos=self.panel._task_groups.tabRect(group_index).center(),
            )
            self.app.processEvents()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
            self.assertEqual(self.panel._task_groups.currentIndex(), group_index)
            self.assertFalse(self.panel._task_empty_label.isVisible())
            for task_id, item in self.panel._task_items.items():
                self.assertIs(cards.itemWidget(item), original_widgets[task_id])
                self.assertEqual(item.isHidden(), int(task_id) % 2 != group_index)

        # An external edit while this task is hidden must still reach its reused controls.
        preferences["1"].update(name="已更新", description="新的说明", show_logs=False)
        self.panel._sync_tasks()
        self.panel._task_groups.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(self.panel._task_titles["1"].text(), "已更新")
        self.assertEqual(self.panel._task_details["1"].accessibleDescription(), "新的说明")
        self.assertFalse(self.panel._task_log_switches["1"].isChecked())
        self.assertIs(cards.itemWidget(self.panel._task_items["1"]), original_widgets["1"])

        preferences["1"]["group"] = "formal"
        self.panel._sync_tasks()
        self.assertTrue(self.panel._task_items["1"].isHidden())
        self.assertEqual(self.panel._task_groups.tabText(0), "正式（16）")
        self.assertEqual(self.panel._task_groups.tabText(1), "测试（14）")
        self.panel._task_groups.setCurrentIndex(0)
        self.assertFalse(self.panel._task_items["1"].isHidden())
        self.assertIs(cards.itemWidget(self.panel._task_items["1"]), original_widgets["1"])


if __name__ == "__main__":
    unittest.main()
