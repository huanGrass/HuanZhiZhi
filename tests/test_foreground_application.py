"""Application routing checks without constructing UI or sending native input."""
from types import SimpleNamespace
import unittest
from unittest import mock

from huanzhizhi.application import HuanZhiZhiApplication


class ForegroundApplicationTests(unittest.TestCase):
    def setUp(self):
        self.host = SimpleNamespace(
            _shutting_down=False,
            chat=SimpleNamespace(_exploration=object(), _exploration_stopping=False,
                                 _stop_exploration=mock.Mock()),
            tasks=SimpleNamespace(process=SimpleNamespace(state="idle")),
            settings=SimpleNamespace(get_input_mode=mock.Mock(return_value="foreground")),
            pet=SimpleNamespace(start_foreground_input=mock.Mock()),
            _show_runtime_error=mock.Mock(),
        )
        self.request = object()
        self.reply = mock.Mock()

    def start(self):
        HuanZhiZhiApplication._start_agent_input(self.host, self.request, self.reply)

    def test_mode_task_busy_and_ai_lifecycle_gate_native_input(self):
        for mode, task_state, has_worker, stopping, shutting_down in (
            ("background", "idle", True, False, False),
            ("foreground", "running", True, False, False),
            ("foreground", "idle", False, False, False),
            ("foreground", "idle", True, True, False),
            ("foreground", "idle", True, False, True),
        ):
            with self.subTest(mode=mode, state=task_state, has_worker=has_worker,
                              stopping=stopping, shutting_down=shutting_down):
                self.host.settings.get_input_mode.return_value = mode
                self.host.tasks.process.state = task_state
                self.host.chat._exploration = object() if has_worker else None
                self.host.chat._exploration_stopping = stopping
                self.host._shutting_down = shutting_down
                self.reply.reset_mock()
                self.start()
                self.host.pet.start_foreground_input.assert_not_called()
                self.reply.assert_called_once()
                self.assertIsInstance(self.reply.call_args.args[0], InterruptedError)

    def test_pet_completion_replies_only_after_animation_finishes(self):
        self.start()
        request, done = self.host.pet.start_foreground_input.call_args.args
        self.assertIs(request, self.request)
        self.reply.assert_not_called()
        done(None)
        self.reply.assert_called_once_with(None)
        self.host.chat._stop_exploration.assert_not_called()

    def test_smooth_background_uses_background_backend_and_waits_for_completion(self):
        self.host.settings.get_input_mode.return_value = 'background_smooth'
        self.start()
        animate = self.host.pet.start_foreground_input
        self.assertEqual(animate.call_args.kwargs, {'background': True})
        self.reply.assert_not_called()
        animate.call_args.args[1](None)
        self.reply.assert_called_once_with(None)

    def test_interrupted_input_replies_then_stops_the_same_worker(self):
        order = []
        self.reply.side_effect = lambda error: order.append(("reply", error))
        self.host.chat._stop_exploration.side_effect = lambda: order.append(("stop",))
        self.start()
        error = InterruptedError("用户接管")
        self.host.pet.start_foreground_input.call_args.args[1](error)
        self.assertEqual(order, [("reply", error), ("stop",)])

    def test_reply_channel_error_is_reported_and_stops_the_worker(self):
        self.reply.side_effect = RuntimeError("channel closed")
        self.start()
        self.host.pet.start_foreground_input.call_args.args[1](None)
        self.host._show_runtime_error.assert_called_once_with("前台操作结果发送失败：channel closed")
        self.host.chat._stop_exploration.assert_called_once()

    def test_old_worker_completion_cannot_stop_a_new_worker_even_if_reply_fails(self):
        for channel_error in (False, True):
            with self.subTest(channel_error=channel_error):
                self.reply.reset_mock()
                self.reply.side_effect = RuntimeError("old channel closed") if channel_error else None
                self.start()
                done = self.host.pet.start_foreground_input.call_args.args[1]
                self.host.chat._exploration = object()
                error = InterruptedError("旧操作已取消")
                done(error)
                self.reply.assert_called_once_with(error)
                self.host.chat._stop_exploration.assert_not_called()
        self.host._show_runtime_error.assert_called_once_with("前台操作结果发送失败：old channel closed")


if __name__ == "__main__":
    unittest.main()
