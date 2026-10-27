"""Exercise foreground protocol routing through the real task-host lifecycle."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from huanzhizhi.settings import SettingsComponent
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.tasks.protocol import FinishedEvent, InputCancelEvent, InputRequestEvent
from tests.pet_factory import create_test_pet
from tests.test_task_host import TASK_SCRIPT
from tests.skill_factory import write_skill_manifest


class PendingPetInputs:
    """Explicitly complete/cancel animations without running native input."""

    def __init__(self):
        self.started = []
        self.cancelled = []
        self.active = None

    def start(self, request, done):
        if self.active is not None:
            raise RuntimeError("已有前台操作")
        self.started.append((request, done))
        self.active = (request, done)

    def complete(self, error=None):
        request, done = self.active
        self.active = None
        done(error)

    def cancel(self, request_id=None, reason="前台操作已停止"):
        self.cancelled.append((request_id, reason))
        if self.active is not None and (request_id is None or request_id == self.active[0].request_id):
            self.complete(InterruptedError(reason))


class ForegroundTaskHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.assets = Path(__file__).parents[1] / "src/huanzhizhi/assets"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "tasks"
        task_dir = root / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "task.py").write_text(TASK_SCRIPT, encoding="utf-8")
        write_skill_manifest(task_dir / "task.py", name="演示任务")
        self.settings = SettingsComponent(Path(self.temp.name) / "settings.ini")
        self.settings.set_input_mode("foreground")
        self.pet = create_test_pet()
        self.host = TaskHostComponent(self.pet, root, settings=self.settings)
        self.inputs = PendingPetInputs()
        self.errors = []
        self.host.error.connect(self.errors.append)
        self.patches = ExitStack()
        self.patches.enter_context(mock.patch.object(self.pet, "start_foreground_input", side_effect=self.inputs.start))
        self.patches.enter_context(mock.patch.object(self.pet, "cancel_foreground_input", side_effect=self.inputs.cancel))

        def start(*args, **kwargs):
            self.host.process._set_state("starting")
            self.host.process._set_state("running")

        self.start = self.patches.enter_context(mock.patch.object(self.host.process, "start", side_effect=start))
        self.stop = self.patches.enter_context(mock.patch.object(
            self.host.process, "stop", side_effect=lambda: self.host.process._set_state("stopping")))
        self.reply = self.patches.enter_context(mock.patch.object(self.host.process, "send_input_result"))

    def tearDown(self):
        self.host.close()
        self.patches.close()
        self.pet.close()
        self.settings.close()
        self.temp.cleanup()

    @staticmethod
    def request(request_id="request-1", *, kind="click"):
        return InputRequestEvent(
            request_id, kind,
            {"hwnd": 101, "title": "目标", "left": -100, "top": 50, "width": 800, "height": 600},
            20, 30, 200 if kind == "drag" else None, 100 if kind == "drag" else None,
            0.4 if kind == "drag" else 0.0,
        )

    def dispatch(self, event):
        self.host.process.event_received.emit(event)

    def test_foreground_request_routes_the_event_and_replies_only_after_completion(self):
        self.host.start_task("demo")
        self.start.assert_called_once_with("demo", self.host.catalog.get("demo").script_path,
                                           None, input_mode="foreground")
        request = self.request(kind="drag")
        self.dispatch(request)
        self.assertIs(self.inputs.started[0][0], request)
        self.reply.assert_not_called()
        self.inputs.complete()
        self.reply.assert_called_once_with(request.request_id, None)
        self.stop.assert_not_called()
        self.assertEqual(self.errors, [])

    def test_mode_is_snapshotted_per_run_and_background_remains_default_compatible(self):
        self.settings.set_input_mode("background")
        self.host.start_task("demo")
        self.start.assert_called_once_with("demo", self.host.catalog.get("demo").script_path, None)
        self.settings.set_input_mode("foreground")
        self.dispatch(self.request("background-run"))
        self.assertEqual(self.inputs.started, [])
        self.reply.assert_called_once_with("background-run", "当前任务没有启用前台操作")

        self.host.process._set_state("idle")
        self.host.start_task("demo")
        self.settings.set_input_mode("background")
        self.reply.reset_mock()
        self.dispatch(self.request("foreground-run"))
        self.assertEqual(len(self.inputs.started), 1)
        self.inputs.complete()
        self.reply.assert_called_once_with("foreground-run", None)

    def test_request_before_running_is_rejected_without_starting_animation(self):
        with mock.patch.object(self.host.process, "start",
                               side_effect=lambda *args, **kwargs: self.host.process._set_state("starting")):
            self.host.start_task("demo")
        self.dispatch(self.request())
        self.assertEqual(self.inputs.started, [])
        self.reply.assert_called_once_with("request-1", "当前任务没有启用前台操作")

    def test_smooth_background_routes_task_and_ai_clicks_without_foreground_input(self):
        self.settings.set_input_mode('background_smooth')
        self.pet.show()
        actor = self.pet._ensure_animation()
        with mock.patch.object(actor, 'show_feedback') as feedback:
            # AI clicks use this same slot while no task subprocess is running.
            self.host.show_click_point(400, 300)
            self.assertTrue(feedback.call_args.kwargs['smooth'])
            self.host.start_task('demo')
            self.start.assert_called_once_with('demo', self.host.catalog.get('demo').script_path,
                                               None, input_mode='background_smooth')
            self.settings.set_input_mode('background')
            self.host.show_click_point(420, 320)
            self.assertTrue(feedback.call_args.kwargs['smooth'])
            self.assertEqual(self.inputs.started, [])

    def test_animation_start_failure_is_returned_to_the_requester(self):
        self.host.start_task("demo")
        with mock.patch.object(self.pet, "start_foreground_input", side_effect=OSError("输入无法启动")):
            self.dispatch(self.request())
        self.reply.assert_called_once_with("request-1", "输入无法启动")
        self.stop.assert_not_called()
        self.assertEqual(self.errors, [])

    def test_smooth_background_request_waits_for_animation_and_uses_run_mode(self):
        self.settings.set_input_mode('background_smooth')
        self.host.start_task('demo')
        self.settings.set_input_mode('foreground')
        request = self.request()
        with mock.patch.object(self.pet, 'start_foreground_input') as animate:
            self.dispatch(request)
            self.assertEqual(animate.call_args.args[0], request)
            self.assertEqual(animate.call_args.kwargs, {'background': True})
            self.reply.assert_not_called()
            animate.call_args.args[1](None)
            self.reply.assert_called_once_with(request.request_id, None)

    def test_interruption_stops_without_sending_an_error_reply(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        order = []
        self.reply.side_effect = lambda *args: order.append(("reply", args))

        def stop():
            order.append(("stop",))
            self.host.process._set_state("stopping")

        self.stop.side_effect = stop
        self.inputs.complete(InterruptedError("用户已接管鼠标"))
        self.assertEqual(order, [("stop",)])
        self.reply.assert_not_called()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.host.presentation.status[-1], '用户已接管鼠标')
        self.assertEqual(self.host.process.state, "stopping")
        self.assertIsNone(self.inputs.active)

    def test_reply_channel_failure_reports_error_and_still_stops(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        self.reply.side_effect = RuntimeError("channel closed")
        self.inputs.complete(OSError("输入失败"))
        self.reply.assert_called_once_with("request-1", "输入失败")
        self.assertEqual(self.errors, ["前台操作结果发送失败：channel closed"])
        self.stop.assert_called_once()
        self.assertEqual(self.host.process.state, "stopping")
        self.assertIsNone(self.inputs.active)

    def test_protection_does_not_hide_a_cleanup_failure_or_leak_into_next_run(self):
        self.host.start_task('demo')
        self.dispatch(self.request())
        self.inputs.complete(InterruptedError('已按 Esc 停止前台操作'))
        self.dispatch(FinishedEvent('失败', '演示任务', '清理失败', '资源释放失败', False))
        self.assertEqual(self.errors, ['任务失败：资源释放失败'])
        self.assertEqual(self.host.presentation.status[0], '失败')
        self.host.process._set_state('idle')
        self.host.start_task('demo')
        self.dispatch(FinishedEvent('已停止', '演示任务', '本次主动停止', None, True))
        self.assertEqual(self.host.presentation.status[-1], '本次主动停止')

    def test_cancel_routes_request_identity_and_cancels_only_the_matching_operation(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        self.dispatch(InputCancelEvent("other-request"))
        self.assertEqual(self.inputs.cancelled[-1], ("other-request", "任务已取消该操作"))
        self.assertIsNotNone(self.inputs.active)
        self.reply.assert_not_called()
        self.dispatch(InputCancelEvent("request-1"))
        self.assertIn(("request-1", "任务已取消该操作"), self.inputs.cancelled)
        self.assertIsNone(self.inputs.active)
        self.reply.assert_not_called()
        self.stop.assert_called_once()

    def test_completed_request_id_cannot_be_replayed_in_the_same_run(self):
        self.host.start_task("demo")
        request = self.request()
        self.dispatch(request)
        self.inputs.complete()
        self.dispatch(request)
        self.assertEqual(len(self.inputs.started), 1)
        self.reply.assert_called_once_with("request-1", None)
        self.stop.assert_called_once()
        self.assertEqual(len(self.errors), 1)
        self.assertIn("重复的前台输入请求", self.errors[0])

    def test_pending_duplicate_stops_and_cleans_the_original_operation(self):
        self.host.start_task("demo")
        request = self.request()
        self.dispatch(request)
        self.dispatch(request)
        self.assertEqual(len(self.inputs.started), 1)
        self.assertIsNone(self.inputs.active)
        self.assertEqual(self.host.process.state, "stopping")
        self.reply.assert_not_called()
        self.assertIn("重复的前台输入请求", self.errors[0])

    def test_old_epoch_completion_cannot_reply_to_or_stop_the_next_task(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        stale_callback = self.inputs.active[1]
        self.host.process._set_state("idle")
        self.assertIsNone(self.inputs.active)
        self.reply.assert_not_called()
        self.host.start_task("demo")
        # Request IDs are scoped to the current run and may be reused by a new child.
        self.dispatch(self.request())
        stale_callback(None)
        stale_callback(InterruptedError("上一任务的迟到取消"))
        self.reply.assert_not_called()
        self.stop.assert_not_called()
        self.assertEqual(self.host.process.state, "running")
        self.assertIsNotNone(self.inputs.active)
        self.inputs.complete()
        self.reply.assert_called_once_with("request-1", None)

    def test_stopping_state_cancels_and_ignores_late_completion(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        done = self.inputs.active[1]
        self.host.process._set_state("stopping")
        self.assertIsNone(self.inputs.active)
        self.assertIn((None, "前台操作已停止"), self.inputs.cancelled)
        done(None)
        self.reply.assert_not_called()
        self.stop.assert_not_called()

    def test_idle_state_cancels_and_clears_the_active_task(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        done = self.inputs.active[1]
        self.host.process._set_state("idle")
        self.assertIsNone(self.inputs.active)
        self.assertIsNone(self.host._active_descriptor)
        self.assertIn((None, "任务已结束"), self.inputs.cancelled)
        done(None)
        self.reply.assert_not_called()

    def test_stop_task_cancels_before_returning_and_does_not_send_success(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        self.host.stop_task()
        self.stop.assert_called_once()
        self.assertIsNone(self.inputs.active)
        self.assertEqual(self.host.process.state, "stopping")
        self.reply.assert_not_called()
        self.assertEqual(self.host.presentation.status[0], "停止中")

    def test_close_cleans_pending_input_and_ignores_a_late_callback(self):
        self.host.start_task("demo")
        self.dispatch(self.request())
        done = self.inputs.active[1]
        self.host.close()
        self.assertIsNone(self.inputs.active)
        self.assertIn((None, "程序正在关闭"), self.inputs.cancelled)
        self.assertEqual(self.host.process.state, "idle")
        self.reply.reset_mock()
        done(None)
        done(InterruptedError("已关闭"))
        self.reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
