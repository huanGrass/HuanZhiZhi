from __future__ import annotations

import json
import ctypes
from ctypes import wintypes
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets
from PIL import Image

from tests.pet_factory import create_test_pet
from tests.skill_factory import write_skill_manifest
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.settings.component import SettingsComponent
from huanzhizhi.tasks.protocol import FinishedEvent, HintEvent, LogEvent, StatusEvent


TASK_SCRIPT = r'''from __future__ import annotations
import json
import sys

if sys.argv[1:] != ["--run"]:
    raise SystemExit(2)
start = json.loads(sys.stdin.readline())
if start["type"] != "start" or start["task_id"] != "demo":
    raise SystemExit(3)
def emit(payload):
    payload = {"protocol_version": 1, **payload}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
emit({"type": "status", "status_title": "运行中", "title": "演示任务", "detail": "正在处理"})
emit({"type": "log", "message": "演示日志"})
emit({"type": "point", "x": 120, "y": 140})
emit({"type": "preview", "image_path": "preview.png", "width": 16, "height": 12, "analysis": {"state": "ready"}, "grid_overlay": {"rows": [6], "columns": [8]}})
emit({"type": "finished", "status_title": "已完成", "title": "演示任务", "detail": "处理完成", "error": None, "stopped_by_request": False})
'''


class TaskHostComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        assets = Path(__file__).parents[1] / "src" / "huanzhizhi" / "assets"
        cls.pet_image = assets / "pet.png"
        cls.animation_root = assets / "animation"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "tasks"
        task_dir = root / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "task.py").write_text(TASK_SCRIPT, encoding="utf-8")
        write_skill_manifest(task_dir / "task.py", name="演示任务")
        Image.new("RGB", (16, 12), (20, 30, 40)).save(task_dir / "preview.png")
        self.pet = create_test_pet()
        self.host = TaskHostComponent(self.pet, root)

    def test_runtime_strip_controls_pet_transparency_through_task_lifecycle(self):
        presentation = self.host.presentation
        presentation.set_running(True, 'demo', '运行中', '演示任务', '正在处理')
        toggle = presentation._strip.transparency
        self.assertTrue(toggle.isChecked())
        self.assertTrue(toggle.isEnabled())
        self.assertAlmostEqual(self.pet.window.windowOpacity(), .6, delta=.01)
        presentation.set_pause_state('paused')
        self.assertAlmostEqual(self.pet.window.windowOpacity(), .6, delta=.01)
        toggle.click()
        self.pet._animation.show_hint([QtCore.QPoint(450, 350)])
        self.assertAlmostEqual(self.pet.window.windowOpacity(), 1., delta=.01)
        toggle.click()
        self.pet._animation.cancel(return_home=False)
        self.assertAlmostEqual(self.pet.window.windowOpacity(), .6, delta=.01)
        presentation.set_running(True, 'demo', '停止中', '演示任务', '正在停止')
        self.assertAlmostEqual(self.pet.window.windowOpacity(), .6, delta=.01)
        presentation.set_running(False, 'demo', '已完成', '演示任务', '完成')
        self.assertFalse(toggle.isEnabled())
        self.assertAlmostEqual(self.pet.window.windowOpacity(), 1., delta=.01)
        presentation.set_running(True, 'demo', '运行中', '演示任务', '正在处理')
        toggle.click()
        presentation.set_running(False, 'demo', '失败', '演示任务', '失败')
        presentation.set_running(True, 'demo', '运行中', '演示任务', '正在处理')
        self.assertFalse(toggle.isChecked())
        self.assertAlmostEqual(self.pet.window.windowOpacity(), 1., delta=.01)

    def tearDown(self) -> None:
        self.host.close()
        self.pet.close()
        if self.host._settings is not None:
            self.host._settings.close()
        self.temp.cleanup()

    def _wait_until_idle(self) -> None:
        deadline = time.monotonic() + 4
        while self.host.process.state != "idle" and time.monotonic() < deadline:
            self.app.processEvents()
            QtCore.QThread.msleep(10)
        self.app.processEvents()
        self.assertEqual(self.host.process.state, "idle")

    def test_real_task_runs_out_of_process_and_routes_events(self) -> None:
        logs: list[str] = []
        errors: list[str] = []
        previews = []
        self.host.log_message.connect(logs.append)
        self.host.error.connect(errors.append)
        self.host.preview_received.connect(previews.append)
        self.pet.show()

        self.host.start_task("demo")
        self._wait_until_idle()

        self.assertEqual(errors, [])
        self.assertEqual(
            logs,
            [
                "任务启动：demo",
                "任务状态：运行中 · 演示任务 · 正在处理",
                "演示日志",
                "任务状态：已完成 · 演示任务 · 处理完成",
            ],
        )
        self.assertFalse(self.host.presentation.running)
        self.assertEqual(self.host.presentation.selected_id, "demo")
        self.assertEqual(self.host.presentation.status, ("已完成", "演示任务", "处理完成"))
        self.assertFalse(self.pet.point_overlay.isVisible())
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0].rows, (6,))
        self.assertIn('"ready"', previews[0].analysis_json)

    def test_panel_pause_resume_parameters_and_stop_reach_the_sdk(self):
        self.host._settings = SettingsComponent(Path(self.temp.name) / 'settings.ini')
        entry = self.host.catalog.get('demo').script_path
        entry.write_text('''from huanzhizhi.task_sdk import TaskSession
s = TaskSession(__file__)
s.emit_status('运行中', '测试', '等待操作')
while not s.stop_requested:
    s.wait_if_paused()
    if s.stop_requested:
        break
    s.apply_parameter_updates()
    s.emit_log('count=' + str(s.options['count']))
    s.wait_for_stop(.02)
s.emit_finished('已停止', '测试', '结束', stopped_by_request=True)
''', encoding='utf-8')
        write_skill_manifest(entry, name='演示任务', parameters=[
            dict(key='count', label='次数', default=3, minimum=1, maximum=10, runtime=True)])
        self.host.reload_tasks()
        logs, errors = [], []
        self.host.log_message.connect(logs.append)
        self.host.error.connect(errors.append)
        def wait(predicate):
            deadline = time.monotonic() + 4
            while not predicate() and time.monotonic() < deadline:
                QtTest.QTest.qWait(5)
            self.assertTrue(predicate(), errors)
        self.pet.show()
        panel = self.host.presentation._strip
        panel._hotkey_user32 = mock.Mock()
        panel._hotkey_user32.RegisterHotKey.return_value = True
        def hotkey(key):
            message = wintypes.MSG(message=0x0312, wParam=key)
            panel.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
        self.host.start_task('demo')
        wait(lambda: 'count=3' in logs)
        hotkey(1)
        wait(lambda: self.host.process.pause_state == 'paused')
        self.assertEqual(panel.status.text(), '已暂停')
        self.assertTrue(panel.controls.isEnabled())
        self.host.update_runtime_parameters({'count': 7})
        wait(lambda: self.host._pending_parameters is None)
        self.assertEqual(self.host._settings.get_task_parameters('demo'), {'count': 7})
        self.assertEqual(panel.status.text(), '已暂停')
        hotkey(1)
        wait(lambda: 'count=7' in logs)
        panel.pause.click()
        wait(lambda: self.host.process.pause_state == 'paused')
        hotkey(2)
        self._wait_until_idle()
        self.assertEqual(errors, [])
        self.assertFalse(panel.pause.isEnabled())
        self.assertFalse(self.host.presentation.running)

    def test_hint_event_routes_visual_coordinates_and_clears_on_finish(self) -> None:
        self.pet.show()
        self.host._active_descriptor = self.host.catalog.get('demo')
        event = HintEvent('drag', ((10,20),(30,20),(30,40)), '向右拖')
        with mock.patch('huanzhizhi.tasks.component.physical_to_qt_global', side_effect=lambda x,y:QtCore.QPoint(x//2,y//2)):
            with mock.patch.object(self.pet.point_overlay,'show_hint') as show:
                self.host._dispatch_task_event(event)
                show.assert_called_once_with((QtCore.QPoint(5,10),QtCore.QPoint(15,10),QtCore.QPoint(15,20)), 'drag','向右拖')
        with mock.patch.object(self.pet.point_overlay,'hide_hint') as hide:
            self.host._dispatch_task_event(FinishedEvent('已停止','任务','停止',None,True))
            hide.assert_called_once()

    def test_real_task_motion_tracks_lifecycle_without_showing_hidden_pet(self) -> None:
        self.assertFalse(self.pet.window.isVisible())
        with mock.patch.object(self.pet, "set_task_state", wraps=self.pet.set_task_state) as motion:
            self.host.start_task("demo")
            with self.assertRaisesRegex(RuntimeError, "已有任务正在运行"):
                self.host.start_task("demo")
            self._wait_until_idle()
            self.assertEqual(
                motion.call_args_list,
                [mock.call("thinking"), mock.call("working"), mock.call("complete")],
            )
            self.assertFalse(self.pet.window.isVisible())

            motion.reset_mock()
            self.host.start_task("demo")
            self._wait_until_idle()
            self.assertEqual(
                motion.call_args_list,
                [mock.call("thinking"), mock.call("working"), mock.call("complete")],
            )
            self.assertFalse(self.pet.window.isVisible())

    def test_hint_clear_waits_nearby_and_next_hint_continues_without_going_home(self) -> None:
        self.pet.show()
        self.pet.set_theme('broom')
        actor = self.pet._animation
        now = [10.]
        actor._clock = lambda: now[0]
        actor._timer.stop()
        home = QtCore.QPoint(self.pet.window.pos())
        self.host._active_descriptor = self.host.catalog.get('demo')
        self.pet.show_hint((QtCore.QPoint(550, 400),), 'click', '第一步')
        now[0] += .6
        actor.advance()
        self.host._dispatch_task_event(HintEvent('clear', (), '等待画面确认'))
        self.assertFalse(self.pet.point_overlay.hint_points)
        now[0] += 1.
        actor.advance()
        self.assertTrue(actor.visual_active)
        self.assertNotEqual(self.pet.window.pos(), home)
        waiting = QtCore.QPointF(actor._pivot)
        self.pet.show_hint((QtCore.QPoint(650, 400),), 'click', '下一步')
        self.assertLessEqual((actor._pivot-waiting).manhattanLength(), 1.)
        now[0] += .6
        actor.advance()
        self.assertLessEqual((actor.fingertip-QtCore.QPointF(650, 400)).manhattanLength(), 2.)

    def test_process_creation_does_not_claim_skill_activity_before_its_first_status(self) -> None:
        self.host._active_descriptor = self.host.catalog.get('demo')
        self.host.process._set_state('starting')
        self.assertEqual(self.pet.window.task_state, 'thinking')
        self.host.process._set_state('running')
        self.assertEqual(self.pet.window.task_state, 'thinking')
        self.host._dispatch_task_event(StatusEvent('等待游戏窗口', '演示任务', '请打开游戏'))
        self.assertEqual(self.pet.window.task_state, 'working')
        self.assertEqual(self.host.presentation.status, ('等待游戏窗口', '演示任务', '请打开游戏'))
        self.host.process._set_state('stopping')
        self.host._dispatch_task_event(StatusEvent('等待游戏窗口', '演示任务', '迟到的状态'))
        self.assertEqual(self.pet.window.task_state, 'idle')

    def test_terminal_task_events_end_hint_or_click_wait_without_two_second_delay(self) -> None:
        self.pet.show()
        self.pet.set_theme('ufo')
        actor = self.pet._animation
        now = [10.]
        actor._clock = lambda: now[0]
        actor._timer.stop()
        home = QtCore.QPoint(self.pet.window.pos())
        for source in ('hint', 'click'):
            for terminal in ('stopping', 'idle', 'finished', 'error'):
                with self.subTest(source=source, terminal=terminal):
                    self.host._active_descriptor = self.host.catalog.get('demo')
                    if source == 'hint':
                        self.pet.show_hint((QtCore.QPoint(550, 400),), 'click', '等待操作')
                    else:
                        actor.show_feedback(QtCore.QPoint(550, 400))
                    now[0] += .6
                    actor.advance()
                    if terminal == 'finished':
                        self.host._dispatch_task_event(FinishedEvent('已完成', '任务', '结束', None, False))
                    elif terminal == 'error':
                        self.host._handle_process_error('测试任务失败')
                    else:
                        self.host._handle_process_state(terminal)
                    self.assertFalse(self.pet.point_overlay.hint_points)
                    now[0] += 1.3
                    actor.advance()
                    self.assertFalse(actor.visual_active)
                    self.assertEqual(self.pet.window.pos(), home)

    def test_finished_event_does_not_celebrate_when_process_then_fails(self) -> None:
        script = self.host.catalog.get("demo").script_path
        script.write_text(TASK_SCRIPT + "\nraise SystemExit(7)\n", encoding="utf-8")
        errors: list[str] = []
        self.host.error.connect(errors.append)
        with mock.patch.object(self.pet, "set_task_state") as motion:
            self.host.start_task("demo")
            self._wait_until_idle()
            self.assertNotIn(mock.call("complete"), motion.call_args_list)
            self.assertEqual(motion.call_args, mock.call("idle"))
        self.assertTrue(any("exit_code=7" in message for message in errors))

    def test_stopped_task_does_not_celebrate_even_if_child_reports_success(self) -> None:
        script = self.host.catalog.get("demo").script_path
        script.write_text(
            TASK_SCRIPT.replace(
                'emit({"type": "finished",', 'sys.stdin.readline()\nemit({"type": "finished",'
            ),
            encoding="utf-8",
        )
        with mock.patch.object(self.pet, "set_task_state") as motion:
            self.host.start_task("demo")
            deadline = time.monotonic() + 4
            while self.host.process.state == "starting" and time.monotonic() < deadline:
                self.app.processEvents()
                QtCore.QThread.msleep(10)
            self.assertEqual(self.host.process.state, "running")
            self.host.stop_task()
            self._wait_until_idle()
            self.assertNotIn(mock.call("complete"), motion.call_args_list)
            self.assertEqual(motion.call_args, mock.call("idle"))

    def test_task_preferences_rename_directory_and_execution_identity(self) -> None:
        self.host.set_task_preferences("demo", name="自定义任务", description="新的介绍", show_logs=True)
        task = self.host.catalog.get("自定义任务")
        self.assertEqual(task.name, "自定义任务")
        self.assertEqual(task.script_path.parent.name, "自定义任务")
        self.assertFalse(task.script_path.parent.with_name('demo').exists())
        manifest = json.loads(task.script_path.with_name('skill.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['id'], '自定义任务')
        self.assertNotIn('name', manifest)
        self.host.reload_tasks()
        with mock.patch.object(self.host.process, "start") as start:
            self.host.start_task("自定义任务")
            start.assert_called_once_with("自定义任务", task.script_path, None)

    def test_click_refreshes_changed_manifest_without_starting_any_task(self) -> None:
        entry = self.host.catalog.get('demo').script_path
        entry.write_text('raise AssertionError("metadata must not run task code")', encoding='utf-8')
        write_skill_manifest(entry, name='清单中的新名称', parameters=[
            dict(key='count', label='次数', default=4, minimum=1, maximum=10)])
        errors = []
        self.host.error.connect(errors.append)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('launched a task')):
            self.host.handle_pet_clicked()
        self.assertEqual(errors, [])
        self.assertEqual(self.host.presentation._bubbles[0].title_label.text(), 'demo')
        self.assertEqual(self.host.task_parameters('demo'), {'count':4})
        self.assertEqual(self.host.process.state, 'idle')

    def test_manual_directory_rename_migrates_settings_and_sdk_identity(self):
        from huanzhizhi.settings.component import SettingsComponent
        from huanzhizhi.task_sdk import load_skill
        settings = SettingsComponent(Path(self.temp.name) / 'rename.ini')
        self.addCleanup(settings.close)
        self.host._settings = settings
        settings.set_task_preferences({'demo': {'name': '旧显示名', 'description': '说明', 'show_logs': False}})
        settings.set_task_parameters('demo', {'count': 7})
        settings.set_hidden_task_ids(('demo',))
        old = self.host.catalog.get('demo').script_path.parent
        new = old.with_name('新名称')
        old.rename(new)
        self.host.reload_tasks()
        self.assertEqual(settings.get_task_parameters('新名称'), {'count': 7})
        self.assertEqual(settings.get_task_parameters('demo'), {})
        self.assertEqual(settings.get_hidden_task_ids(), ('新名称',))
        self.assertEqual(self.host.get_task_preferences('新名称')['name'], '新名称')
        self.assertFalse(self.host.get_task_preferences('新名称')['show_logs'])
        self.assertEqual(load_skill(new / 'task.py')['id'], '新名称')
        self.assertEqual(load_skill(new / 'task.py')['name'], '新名称')
        self.assertNotIn('name', json.loads((new / 'skill.json').read_text(encoding='utf-8')))

    def test_rename_rejects_collision_invalid_path_and_active_work(self):
        old = self.host.catalog.get('demo').script_path.parent
        old.with_name('occupied').mkdir()
        for name in ('occupied', '../outside', 'NUL', 'a/b', 'bad.'):
            with self.subTest(name=name), self.assertRaises((ValueError, FileExistsError)):
                self.host.set_task_preferences('demo', name=name, description='', show_logs=True)
        self.host.external_work_active = True
        with self.assertRaisesRegex(RuntimeError, '先停止'):
            self.host.set_task_preferences('demo', name='new', description='', show_logs=True)
        self.host.external_work_active = False
        self.assertTrue(old.is_dir())

    def test_rename_write_failure_restores_directory_manifest_and_settings(self):
        from huanzhizhi.settings.component import SettingsComponent
        settings = SettingsComponent(Path(self.temp.name) / 'rename.ini')
        self.addCleanup(settings.close)
        self.host._settings = settings
        settings.set_task_parameters('demo', {'count': 9})
        settings.sync()
        entry = self.host.catalog.get('demo').script_path
        original = entry.with_name('skill.json').read_bytes()
        with mock.patch.object(settings, 'sync', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(OSError, 'write failed'):
                self.host.set_task_preferences('demo', name='new', description='', show_logs=True)
        self.assertTrue(entry.exists())
        self.assertEqual(entry.with_name('skill.json').read_bytes(), original)
        self.assertEqual(settings.get_task_parameters('demo'), {'count': 9})
        self.assertEqual(settings.get_task_parameters('new'), {})

    def test_quiet_running_display_preserves_logs_debug_events_and_final_errors(self) -> None:
        logs, statuses, errors = [], [], []
        self.host.log_message.connect(logs.append)
        self.host.status_received.connect(lambda *args: statuses.append(args))
        self.host.error.connect(errors.append)
        with mock.patch.object(self.host.process, "start"):
            self.host.start_task("demo")
        self.host.presentation.handle_pet_clicked()
        self.host.set_task_preferences("demo", name="demo", description="", show_logs=False)
        self.host.set_task_visible("demo", False)
        self.host._handle_task_event(StatusEvent("计算中", "阶段一", "已处理 12 项"))
        self.host._handle_task_event(LogEvent("详细日志"))
        bubble = self.host.presentation._bubbles[0]
        self.assertEqual(bubble.title_label.text(), "运行中")
        self.assertEqual(bubble.detail_label.text(), "")
        self.assertEqual(bubble.button_row.itemAt(0).widget().text(), "停止任务")
        self.assertIn("详细日志", logs)
        self.assertEqual(statuses, [("计算中", "阶段一", "已处理 12 项")])
        self.host.set_task_preferences("demo", name="demo", description="", show_logs=True)
        self.assertEqual(bubble.title_label.text(), "计算中")
        self.assertEqual(bubble.detail_label.text(), "demo\n已处理 12 项")
        self.host.set_task_preferences("demo", name="demo", description="", show_logs=False)
        self.host._handle_task_event(FinishedEvent("失败", "阶段一", "无法继续", "连接失败", False))
        self.assertEqual(bubble.title_label.text(), "失败")
        self.assertEqual(bubble.detail_label.text(), "demo\n无法继续")
        self.assertEqual(errors, ["任务失败：连接失败"])

    def test_task_bubbles_and_global_watcher_form_one_lifecycle(self) -> None:
        self.pet.show()
        self.app.processEvents()

        self.host.presentation.handle_pet_clicked()
        self.app.processEvents()
        self.assertTrue(self.host.presentation.has_bubbles)
        self.assertTrue(self.host.mouse_watcher.enabled)

        self.host.presentation.close_bubbles()
        self.app.processEvents()
        self.assertFalse(self.host.mouse_watcher.enabled)

    def test_task_visibility_filters_live_list_and_survives_catalog_reload(self) -> None:
        self.host.presentation.handle_pet_clicked()
        self.assertEqual(self.host.presentation._bubbles[0].title_label.text(), "demo")
        self.host.set_task_visible("demo", False)
        self.assertEqual(self.host.presentation._tasks, ())
        self.assertNotEqual(self.host.presentation._bubbles[0].title_label.text(), "demo")
        root = Path(self.temp.name) / "tasks"
        added = root / "new_task"
        added.mkdir()
        (added / "task.py").write_text(TASK_SCRIPT.replace("demo", "new_task"), encoding="utf-8")
        write_skill_manifest(added / "task.py", name="演示任务")
        self.host.reload_tasks()
        self.assertFalse(self.host.is_task_visible("demo"))
        self.assertTrue(self.host.is_task_visible("new_task"))
        self.assertEqual([item.id for item in self.host.presentation._tasks], ["new_task"])
        self.host.set_task_visible("demo", True)
        self.assertEqual([item.id for item in self.host.presentation._tasks], ["demo", "new_task"])

    def test_hiding_running_task_preserves_status_and_stop_control(self) -> None:
        with mock.patch.object(self.host.process, "start"):
            self.host.start_task("demo")
        self.host.presentation.handle_pet_clicked()
        bubble = self.host.presentation._bubbles[0]
        status = self.host.presentation.status
        self.host.set_task_visible("demo", False)
        self.assertTrue(self.host.presentation.running)
        self.assertEqual(self.host.presentation.status, status)
        self.assertIs(self.host.presentation._bubbles[0], bubble)
        self.assertEqual(self.host.presentation.selected_id, "demo")
        self.assertEqual(self.host.catalog.get("demo").name, "demo")
        self.assertEqual(bubble.button_row.itemAt(0).widget().text(), "停止任务")
        self.host.presentation.set_running(False, "demo", "已完成", "演示任务", "处理完成")
        self.assertEqual(bubble.button_row.itemAt(0).widget().text(), "收起结果")

    def test_hiding_task_closes_its_pending_start_confirmation(self) -> None:
        self.host.presentation.handle_pet_clicked()
        self.host.presentation._handle_bubble_clicked("select:demo")
        self.assertEqual(self.host.presentation._mode, "check")
        self.host.set_task_visible("demo", False)
        self.assertEqual(self.host.presentation._mode, "list")
        self.assertEqual(self.host.presentation.selected_id, "")

    def test_mouse_point_setting_routes_press_move_release(self) -> None:
        self.pet.show()
        self.pet.set_mouse_point_visible(True)
        self.host._handle_mouse_pressed(100, 110)
        self.host._handle_mouse_moved(120, 130)
        self.host._handle_mouse_released(140, 150)

        self.assertTrue(self.pet._animation.visual_active)
        self.assertFalse(self.pet._animation._held_feedback)

    def test_start_task_forwards_optional_options_and_keeps_single_argument_compatible(self) -> None:
        with mock.patch.object(self.host.process, "start") as start:
            self.host.start_task("demo", {"interval_ms": 250, "selected_hwnd": 123})
            start.assert_called_once_with(
                "demo",
                self.host.catalog.get("demo").script_path,
                {"interval_ms": 250, "selected_hwnd": 123},
            )

        self.host._active_descriptor = None
        self.host.presentation.set_running(False, "demo", "未开始", "演示任务", "等待启动")
        with mock.patch.object(self.host.process, "start") as start:
            self.host.start_task("demo")
            start.assert_called_once_with(
                "demo",
                self.host.catalog.get("demo").script_path,
                None,
            )

    def test_start_task_leaves_option_validation_to_process_manager(self) -> None:
        errors: list[str] = []
        self.host.error.connect(errors.append)

        self.host.start_task("demo", {"bad": float("nan")})

        self.assertEqual(self.host.process.state, "idle")
        self.assertEqual(len(errors), 1)
        self.assertIn("非有限数值", errors[0])
        self.assertFalse(self.host.presentation.running)


if __name__ == "__main__":
    unittest.main()
