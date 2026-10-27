import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from unittest.mock import Mock, patch

from huanzhizhi.agents import runtime as ai_runtime
from huanzhizhi.task_sdk import input_result_path
from huanzhizhi.tasks.protocol import HintEvent, InputRequestEvent, ProtocolError


REQUEST_ID = 'a' * 32
REQUEST = dict(protocol_version=1, type='input_request', request_id=REQUEST_ID,
               kind='click', window=dict(hwnd=123, title='fixture', left=-200,
                                        top=100, width=320, height=200),
               x=30, y=40, end_x=None, end_y=None, duration=0)
CANCEL = dict(protocol_version=1, type='input_cancel', request_id=REQUEST_ID)


class AgentInputLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.turn = Path(temporary.name)
        (self.turn / 'prompt.txt').write_text('fixture', encoding='utf-8')
        self.clicks = self.turn / 'clicks.jsonl'
        self.reply = input_result_path(self.clicks, REQUEST_ID)
        self.worker = Mock(directory=str(self.turn), input_mode='foreground')
        self.worker.isInterruptionRequested.return_value = False
        self.process = Mock()
        self.process.wait.return_value = 0
        self.events = []
        self.callbacks = []
        self.worker.input_request.emit.side_effect = lambda event, reply: self.callbacks.append(reply)
        popen = patch.object(ai_runtime.subprocess, 'Popen', side_effect=self.spawn)
        self.popen = popen.start()
        self.addCleanup(popen.stop)
        stop = patch.object(ai_runtime, '_stop_process')
        self.stop = stop.start()
        self.addCleanup(stop.stop)

    def append(self, *events):
        with self.clicks.open('ab') as stream:
            for event in events:
                stream.write((json.dumps(event) + '\n').encode('utf-8'))

    def spawn(self, *args, **kwargs):
        self.append(*self.events)
        return self.process

    def run_agent(self):
        return ai_runtime.run_agent(self.worker, ['fixture-agent'], self.turn,
                                    stdout_name='stdout.txt',
                                    environment={'HUANZHIZHI_INPUT_MODE': 'background'})

    def test_observation_never_forwards_input_and_removes_inherited_click_channel(self):
        self.events = [REQUEST]
        with self.assertRaisesRegex(ai_runtime.AgentTaskError, '不允许的输入'):
            ai_runtime.run_agent(self.worker, ['fixture-agent'], self.turn,
                                 stdout_name='stdout.txt', allow_input=False,
                                 environment={'HUANZHIZHI_CLICK_EVENTS': 'inherited-channel'})
        self.worker.input_request.emit.assert_not_called()
        self.worker.click_point.emit.assert_not_called()
        environment = self.popen.call_args.kwargs['env']
        self.assertNotIn('HUANZHIZHI_CLICK_EVENTS', environment)
        self.assertNotIn('HUANZHIZHI_INPUT_MODE', environment)
        self.stop.assert_called_once()

    def test_observation_timeout_stops_child_and_preserves_diagnostics(self):
        with patch.object(ai_runtime.time, 'monotonic', side_effect=[0, 121]), \
                self.assertRaisesRegex(ai_runtime.AgentTaskError, '查看窗口超时'):
            ai_runtime.run_agent(self.worker, ['fixture-agent'], self.turn,
                                 stdout_name='stdout.txt', allow_input=False, timeout=120)
        self.stop.assert_called_once()
        self.assertTrue((self.turn / 'stderr.txt').exists())

    def test_foreground_environment_and_acknowledgement_use_the_same_channel(self):
        self.events = [REQUEST]

        def acknowledge(event, reply):
            self.assertIsInstance(event, InputRequestEvent)
            self.callbacks.append(reply)
            reply()

        self.worker.input_request.emit.side_effect = acknowledge

        def finish(**kwargs):
            self.assertEqual(json.loads(self.reply.read_bytes()), dict(
                protocol_version=1, type='input_result', request_id=REQUEST_ID, error=None))
            self.assertFalse(self.reply.with_suffix('.tmp').exists())
            self.reply.unlink()  # The child consumes its reply before it exits.
            return 0

        self.process.wait.side_effect = finish
        self.assertEqual(self.run_agent(), 0)
        environment = self.popen.call_args.kwargs['env']
        self.assertEqual(environment['HUANZHIZHI_INPUT_MODE'], 'foreground')
        self.assertEqual(environment['HUANZHIZHI_CLICK_EVENTS'], str(self.clicks.resolve()))
        self.worker.input_cancel.emit.assert_not_called()
        self.assertFalse(self.clicks.exists())
        self.assertTrue((self.turn / 'clicks.finished.jsonl').is_file())
        self.callbacks[0]()
        self.assertFalse(self.reply.exists())

    def test_background_mode_rejects_request_without_starting_an_action(self):
        self.events = [REQUEST]
        self.worker.input_mode = 'background'

        def finish(**kwargs):
            self.assertIn('没有启用前台操作', json.loads(self.reply.read_bytes())['error'])
            return 0

        self.process.wait.side_effect = finish
        self.assertEqual(self.run_agent(), 0)
        self.worker.input_request.emit.assert_not_called()

    def test_smooth_background_environment_and_click_feedback_are_forwarded(self):
        self.worker.input_mode = 'background_smooth'
        self.events = [dict(protocol_version=1, type='point', x=400, y=300)]
        self.assertEqual(self.run_agent(), 0)
        self.assertEqual(self.popen.call_args.kwargs['env']['HUANZHIZHI_INPUT_MODE'], 'background_smooth')
        self.worker.click_point.emit.assert_called_once_with(400, 300)
        self.worker.input_request.emit.assert_not_called()

    def test_trial_hint_reaches_host_without_sending_input(self):
        self.events = [dict(protocol_version=1, type='hint', kind='line',
                            points=[[100, 200], [300, 400]], label='走法')]
        self.assertEqual(self.run_agent(), 0)
        self.assertEqual(self.worker.hint_events, self.turn / 'host_hints.jsonl')
        self.assertTrue(self.worker.hint_events.is_file())
        self.worker.hint.emit.assert_called_once_with(HintEvent('line', ((100, 200), (300, 400)), '走法'))
        self.worker.input_request.emit.assert_not_called()
        self.worker.click_point.emit.assert_not_called()

    def test_final_drain_discards_new_hint_but_allows_clear(self):
        def finish(**kwargs):
            self.append(dict(protocol_version=1, type='hint', kind='line',
                             points=[[100, 200], [300, 400]], label='过期'),
                        dict(protocol_version=1, type='hint', kind='clear', points=[], label='结束'))
            return 0
        self.process.wait.side_effect = finish
        self.assertEqual(self.run_agent(), 0)
        self.worker.hint.emit.assert_called_once_with(HintEvent('clear', (), '结束'))

    def test_read_only_observation_cannot_display_hint(self):
        self.events = [dict(protocol_version=1, type='hint', kind='line',
                            points=[[100, 200], [300, 400]], label='走法')]
        with self.assertRaises(ai_runtime.AgentTaskError):
            ai_runtime.run_agent(self.worker, ['fixture-agent'], self.turn,
                                 stdout_name='stdout.txt', allow_input=False)
        self.worker.hint.emit.assert_not_called()

    def test_smooth_background_request_waits_for_host_acknowledgement(self):
        self.worker.input_mode = 'background_smooth'
        self.events = [REQUEST]

        def acknowledge(event, reply):
            self.assertFalse(self.reply.exists())
            reply()
            self.assertIsNone(json.loads(self.reply.read_bytes())['error'])

        self.worker.input_request.emit.side_effect = acknowledge
        self.assertEqual(self.run_agent(), 0)
        self.worker.input_request.emit.assert_called_once()

    def test_process_exit_cancels_pending_action_and_ignores_late_reply(self):
        self.events = [REQUEST]
        self.assertEqual(self.run_agent(), 0)
        self.worker.input_cancel.emit.assert_called_once_with(REQUEST_ID)
        self.callbacks[0]()
        self.callbacks[0](InterruptedError('cancelled'))
        self.assertFalse(self.reply.exists())
        self.assertFalse(self.reply.parent.exists())

    def test_child_cancel_retires_reply_before_ui_cancellation_callback(self):
        self.events = [REQUEST, CANCEL]
        self.worker.input_cancel.emit.side_effect = lambda request_id: self.callbacks[0](
            InterruptedError('cancelled'))

        def finish(**kwargs):
            self.assertFalse(self.reply.exists(), '取消后的 UI 完成通知不应重新生成响应文件')
            return 0

        self.process.wait.side_effect = finish
        self.assertEqual(self.run_agent(), 0)
        self.worker.input_cancel.emit.assert_called_once_with(REQUEST_ID)

    def test_final_drain_rejects_new_request_without_starting_an_action(self):
        def finish(**kwargs):
            self.append(REQUEST)
            return 0

        self.process.wait.side_effect = finish
        self.assertEqual(self.run_agent(), 0)
        self.worker.input_request.emit.assert_not_called()
        self.assertIn('已停止', json.loads(self.reply.read_bytes())['error'])

    def test_interruption_before_spawn_closes_the_channel(self):
        self.worker.check_stop.side_effect = InterruptedError('stopped')
        with self.assertRaisesRegex(InterruptedError, 'stopped'):
            self.run_agent()
        self.popen.assert_not_called()
        self.assertFalse(self.clicks.exists())
        self.assertTrue((self.turn / 'clicks.finished.jsonl').is_file())

    def test_invalid_followup_event_cancels_previously_pending_action(self):
        self.events = [REQUEST, dict(protocol_version=1, type='invalid')]
        with self.assertRaises(ProtocolError):
            self.run_agent()
        self.stop.assert_called_once_with(self.process)
        self.worker.input_cancel.emit.assert_called_once_with(REQUEST_ID)
        self.callbacks[0]()
        self.assertFalse(self.reply.exists())

    def test_reply_publication_cannot_continue_after_channel_closes(self):
        self.events = [REQUEST]
        action_ready = threading.Event()
        reply_started = threading.Event()
        release_reply = threading.Event()
        finish_process = threading.Event()
        writes_after_close = []
        original_write_bytes = Path.write_bytes

        def queued_action(event, reply):
            self.callbacks.append(reply)
            action_ready.set()

        def reply_path(*args):
            reply_started.set()
            if not release_reply.wait(3):
                raise TimeoutError('reply was not released')
            return input_result_path(*args)

        def write_bytes(path, data):
            if path.suffix == '.tmp' and not self.clicks.exists():
                writes_after_close.append(path)
            return original_write_bytes(path, data)

        def finish(**kwargs):
            if not finish_process.wait(3):
                raise TimeoutError('process was not released')
            return 0

        self.worker.input_request.emit.side_effect = queued_action
        self.process.wait.side_effect = finish
        with patch.object(ai_runtime, 'input_result_path', side_effect=reply_path), \
                patch.object(Path, 'write_bytes', write_bytes), \
                ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(self.run_agent)
            self.assertTrue(action_ready.wait(2))
            replying = pool.submit(self.callbacks[0])
            try:
                self.assertTrue(reply_started.wait(2))
                finish_process.set()
                try:
                    running.result(timeout=.3)
                except FutureTimeoutError:
                    pass  # Closing may correctly wait for the in-flight reply.
            finally:
                finish_process.set()
                release_reply.set()
            replying.result(timeout=2)
            self.assertEqual(running.result(timeout=2), 0)
        self.assertEqual(writes_after_close, [])
