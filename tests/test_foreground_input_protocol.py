from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tests.skill_factory import write_skill_manifest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets

from huanzhizhi import task_sdk
from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import (
    FinishedEvent, InputBlockedEvent, InputCancelEvent, InputRequestEvent, ProtocolError,
    encode_input_result, parse_event_line,
)


WINDOW = dict(hwnd=123, title='fixture', left=-200, top=100, width=320, height=200)


def request_payload(**changes):
    return dict(protocol_version=1, type='input_request', request_id='a' * 32,
                kind='click', window=WINDOW, x=30, y=40,
                end_x=None, end_y=None, duration=0) | changes


class InputProtocolTests(unittest.TestCase):
    def test_blocked_input_is_a_notification_without_executable_payload(self):
        payload = dict(protocol_version=1, type='input_blocked')
        self.assertEqual(parse_event_line(json.dumps(payload)), InputBlockedEvent())
        with self.assertRaises(ProtocolError):
            parse_event_line(json.dumps(payload | {'kind': 'click'}))

    def test_request_and_cancel_parse_complete_window_identity_and_client_coordinates(self):
        event = parse_event_line(json.dumps(request_payload()))
        self.assertIsInstance(event, InputRequestEvent)
        self.assertEqual((event.window, event.x, event.y), (WINDOW, 30, 40))
        drag = parse_event_line(json.dumps(request_payload(kind='drag', end_x=100, end_y=150, duration=.3)))
        self.assertEqual((drag.end_x, drag.end_y, drag.duration), (100, 150, .3))
        self.assertEqual(parse_event_line(json.dumps(dict(protocol_version=1, type='input_cancel',
                                                         request_id='a' * 32))), InputCancelEvent('a' * 32))

    def test_invalid_requests_cannot_escape_window_or_choose_reply_paths(self):
        invalid = [
            {'request_id': '../reply'}, {'request_id': 'A' * 32}, {'request_id': 1},
            {'window': WINDOW | {'reply_path': 'elsewhere'}},
            {'window': WINDOW | {'hwnd': True}}, {'window': WINDOW | {'width': 0}},
            {'window': WINDOW | {'title': ' '}}, {'window': WINDOW | {'left': False}},
            {'x': True}, {'x': -1}, {'x': 320}, {'y': 200},
            {'kind': 'scroll'}, {'duration': .1}, {'duration': True}, {'duration': float('nan')},
            {'duration': 10**400},
            {'end_x': 20}, {'kind': 'drag'},
            {'kind': 'drag', 'end_x': 100, 'end_y': 150, 'duration': 6},
            {'kind': 'drag', 'end_x': 320, 'end_y': 150, 'duration': .5},
            {'kind': 'drag', 'end_x': 100, 'end_y': False, 'duration': .5},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ProtocolError):
                parse_event_line(json.dumps(request_payload(**changes)))
        with self.assertRaises(ProtocolError):
            encode_input_result('../reply', None)
        with self.assertRaises(ProtocolError):
            encode_input_result('a' * 32, False)
        with self.assertRaises(task_sdk.TaskSdkError):
            task_sdk.input_result_path('events.jsonl', '../reply')


class ForegroundRoutingTests(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        self.native.post_message.return_value = True
        self.api = Win32DesktopApi(native=self.native, configure_dpi_awareness=False)
        self.window = WindowInfo(**WINDOW)
        self.api._require_unchanged = Mock(return_value=self.window)

    def test_front_clicks_and_drag_only_request_the_host(self):
        with patch.dict(os.environ, HUANZHIZHI_INPUT_MODE='foreground'), \
                patch('huanzhizhi.platform.target_window._request_foreground_input') as request, \
                patch('huanzhizhi.platform.target_window._emit_click_point') as point:
            for kind in ('click', 'right_click', 'double_click'):
                self.api.post_click(self.window, 30, 40, kind)
                request.assert_called_with(WINDOW, kind, 30, 40)
            self.api.post_drag(self.window, 30, 40, 100, 150, .3)
            request.assert_called_with(WINDOW, 'drag', 30, 40, 100, 150, .3)
            point.assert_not_called()
        self.native.post_message.assert_not_called()
        self.native.set_foreground_window.assert_not_called()

    def test_front_errors_and_invalid_inputs_never_fall_back_to_background(self):
        with patch.dict(os.environ, HUANZHIZHI_INPUT_MODE='foreground'), \
                patch('huanzhizhi.platform.target_window._request_foreground_input',
                      side_effect=RuntimeError('host failed')) as request:
            for operation in (lambda: self.api.post_click(self.window, 30, 40),
                              lambda: self.api.post_drag(self.window, 30, 40, 100, 150)):
                with self.assertRaisesRegex(RuntimeError, 'host failed'):
                    operation()
            self.assertEqual(request.call_count, 2)
            for operation in (lambda: self.api.post_click(self.window, 320, 40),
                              lambda: self.api.post_click(self.window, 30, 40, 'unknown'),
                              lambda: self.api.post_drag(self.window, 30, 40, 320, 150),
                              lambda: self.api.post_drag(self.window, 30, 40, 100, 150, True)):
                with self.assertRaises((ValueError, TypeError)):
                    operation()
            self.assertEqual(request.call_count, 2)
        self.native.post_message.assert_not_called()

    def test_invalid_input_mode_fails_before_any_native_message(self):
        with patch.dict(os.environ, HUANZHIZHI_INPUT_MODE='invalid'):
            for operation in (lambda: self.api.post_click(self.window, 30, 40),
                              lambda: self.api.post_drag(self.window, 30, 40, 100, 150)):
                with self.assertRaisesRegex(ValueError, 'HUANZHIZHI_INPUT_MODE'):
                    operation()
        self.native.post_message.assert_not_called()

    def test_default_background_drag_preserves_post_message_sequence_and_release(self):
        with patch.dict(os.environ, {}, clear=True):
            self.api.post_drag(self.window, 30, 40, 100, 150, .1, wait=lambda _: None)
        messages = [call.args[1] for call in self.native.post_message.call_args_list]
        self.assertEqual(messages, [0x200, 0x201] + [0x200] * 5 + [0x202])
        self.assertEqual(self.native.post_message.call_args.args[3], (150 << 16) | 100)

    def test_smooth_background_waits_for_host_instead_of_posting_early(self):
        with patch.dict(os.environ, HUANZHIZHI_INPUT_MODE='background_smooth'), \
                patch('huanzhizhi.platform.target_window._request_foreground_input') as request, \
                patch('huanzhizhi.platform.target_window._emit_click_point') as point:
            for kind in ('click', 'right_click', 'double_click'):
                self.api.post_click(self.window, 30, 40, kind, wait=lambda _: None)
                request.assert_called_with(WINDOW, kind, 30, 40)
            self.api.post_drag(self.window, 30, 40, 100, 150, .3, wait=lambda _: None)
            request.assert_called_with(WINDOW, 'drag', 30, 40, 100, 150, .3)
            point.assert_not_called()
        self.native.post_message.assert_not_called()
        self.native.set_foreground_window.assert_not_called()


class InputFileChannelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.events = Path(temporary.name) / 'events.jsonl'
        self.events.touch()
        environment = patch.dict(os.environ, HUANZHIZHI_CLICK_EVENTS=str(self.events))
        environment.start()
        self.addCleanup(environment.stop)
        session = patch('huanzhizhi.task_sdk._active_session', None)
        session.start()
        self.addCleanup(session.stop)

    def _request(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            lines = self.events.read_bytes().splitlines()
            if lines:
                return parse_event_line(lines[0])
            time.sleep(.005)
        self.fail('文件通道未收到输入请求')

    def _reply(self, request_id, error=None):
        path = task_sdk.input_result_path(self.events, request_id)
        self.assertEqual(path.parent, Path(str(self.events) + '.replies'))
        path.parent.mkdir(exist_ok=True)
        pending = path.with_suffix('.tmp')
        pending.write_bytes(encode_input_result(request_id, error))
        pending.replace(path)
        return path

    def test_file_request_blocks_until_matching_atomic_reply_and_consumes_it(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(task_sdk._request_foreground_input, WINDOW, 'click', 30, 40, timeout=2)
            request = self._request()
            self.assertIsInstance(request, InputRequestEvent)
            self.assertFalse(result.done())
            reply = self._reply(request.request_id)
            self.assertIsNone(result.result(timeout=2))
        self.assertFalse(reply.exists())
        self.assertEqual(len(self.events.read_bytes().splitlines()), 1)

    def test_file_host_error_is_reported_without_retry(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(task_sdk._request_foreground_input, WINDOW, 'click', 30, 40, timeout=2)
            request = self._request()
            self._reply(request.request_id, 'target moved')
            with self.assertRaisesRegex(RuntimeError, 'target moved'):
                result.result(timeout=2)
        self.assertEqual(len(self.events.read_bytes().splitlines()), 1)

    def test_temporarily_unreadable_reply_waits_without_resending_input(self):
        read_bytes = Path.read_bytes
        denied = []

        def read_reply(path):
            if path.parent.name.endswith('.replies') and path.exists() and not denied:
                denied.append(path)
                raise PermissionError(13, 'Permission denied', str(path))
            return read_bytes(path)

        with patch.object(Path, 'read_bytes', read_reply), ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(task_sdk._request_foreground_input, WINDOW, 'click', 30, 40, timeout=2)
            request = self._request()
            reply = self._reply(request.request_id)
            self.assertIsNone(result.result(timeout=2))
        self.assertEqual(denied, [reply])
        self.assertFalse(reply.exists())
        self.assertEqual(len(self.events.read_bytes().splitlines()), 1)

    def test_unreadable_reply_remains_bounded_and_cancels(self):
        with patch.object(Path, 'read_bytes', side_effect=PermissionError(13, 'Permission denied')):
            with self.assertRaises(TimeoutError):
                task_sdk._request_foreground_input(WINDOW, 'click', 30, 40, timeout=.04)
        events = [parse_event_line(line) for line in self.events.read_bytes().splitlines()]
        self.assertEqual([event.type for event in events], ['input_request', 'input_cancel'])
        self.assertEqual(events[0].request_id, events[1].request_id)

    def test_file_timeout_sends_cancel(self):
        with self.assertRaises(TimeoutError):
            task_sdk._request_foreground_input(WINDOW, 'click', 30, 40, timeout=.04)
        events = [parse_event_line(line) for line in self.events.read_bytes().splitlines()]
        self.assertEqual([event.type for event in events], ['input_request', 'input_cancel'])
        self.assertEqual(events[0].request_id, events[1].request_id)

    def test_removed_channel_interrupts_without_recreating_it(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(task_sdk._request_foreground_input, WINDOW, 'click', 30, 40, timeout=2)
            self._request()
            self.events.unlink()
            with self.assertRaises(InterruptedError):
                result.result(timeout=2)
        self.assertFalse(self.events.exists())

    def test_wrong_reply_id_fails_and_cancels_without_accepting_reply_path_input(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(task_sdk._request_foreground_input, WINDOW, 'click', 30, 40, timeout=2)
            request = self._request()
            path = task_sdk.input_result_path(self.events, request.request_id)
            path.parent.mkdir()
            path.write_bytes(encode_input_result('0' * 32))
            with self.assertRaisesRegex(RuntimeError, 'request_id'):
                result.result(timeout=2)
        events = [parse_event_line(line) for line in self.events.read_bytes().splitlines()]
        self.assertEqual([event.type for event in events], ['input_request', 'input_cancel'])


class InputProcessChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manager = TaskProcessManager(terminate_timeout_ms=300, kill_timeout_ms=120)
        self.events, self.errors = [], []
        self.manager.event_received.connect(self.events.append)
        self.manager.error.connect(self.errors.append)

    def tearDown(self):
        self.manager.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def _start(self, body, *, mode='foreground'):
        path = self.root / 'input' / 'task.py'
        path.parent.mkdir(exist_ok=True)
        write_skill_manifest(path)
        prefix = ('import os\nfrom huanzhizhi.task_sdk import TaskSession, _request_foreground_input\n'
                  f'window = {WINDOW!r}\nsession = TaskSession(__file__)\n')
        path.write_text(prefix + textwrap.dedent(body), encoding='utf-8')
        self.manager.start('input', path, input_mode=mode)

    def _wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(f'输入通信超时：{self.manager.state} {self.errors}')
            self.app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
            QtCore.QThread.msleep(5)

    def test_jsonl_requests_wait_for_host_and_receive_unique_ids(self):
        self._start('''
            assert os.environ['HUANZHIZHI_INPUT_MODE'] == 'foreground'
            _request_foreground_input(window, 'double_click', 30, 40)
            _request_foreground_input(window, 'drag', 30, 40, 100, 150, .3)
            session.emit_finished('完成', '测试', '输入完成')
        ''')
        self._wait(lambda: bool(self.events))
        first = self.events[0]
        self.assertIsInstance(first, InputRequestEvent)
        self.assertEqual(first.kind, 'double_click')
        self.assertEqual(self.manager.state, 'running')
        self.assertEqual(len(self.events), 1)
        self.manager.send_input_result(first.request_id)
        self._wait(lambda: len(self.events) >= 2)
        second = self.events[1]
        self.assertIsInstance(second, InputRequestEvent)
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertEqual((second.kind, second.end_x, second.end_y, second.duration), ('drag', 100, 150, .3))
        self.manager.send_input_result(second.request_id)
        self._wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        self.assertIsInstance(self.events[-1], FinishedEvent)

    def test_jsonl_host_error_returns_runtime_error_to_task(self):
        self._start('''
            try:
                _request_foreground_input(window, 'click', 30, 40)
                raise AssertionError('host failure was ignored')
            except RuntimeError as error:
                assert 'target moved' in str(error)
            session.emit_finished('完成', '测试', '错误已处理')
        ''')
        self._wait(lambda: bool(self.events))
        self.manager.send_input_result(self.events[0].request_id, 'target moved')
        self._wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        self.assertEqual([event.type for event in self.events], ['input_request', 'finished'])

    def test_stop_interrupts_pending_input_and_sends_cancel(self):
        self._start('''
            try:
                _request_foreground_input(window, 'click', 30, 40)
                raise AssertionError('stop was ignored')
            except InterruptedError:
                pass
            session.emit_finished('停止', '测试', '已取消', stopped_by_request=True)
        ''')
        self._wait(lambda: bool(self.events))
        self.manager.stop()
        self._wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        self.assertEqual([event.type for event in self.events], ['input_request', 'input_cancel', 'finished'])
        self.assertEqual(self.events[0].request_id, self.events[1].request_id)
        self.assertTrue(self.events[-1].stopped_by_request)

    def test_jsonl_timeout_cancels_and_late_result_is_not_a_new_action(self):
        self._start('''
            import time
            try:
                _request_foreground_input(window, 'click', 30, 40, timeout=.1)
                raise AssertionError('timeout was ignored')
            except TimeoutError:
                pass
            time.sleep(.1)
            session.emit_finished('完成', '测试', '超时已处理')
        ''')
        self._wait(lambda: any(isinstance(event, InputCancelEvent) for event in self.events))
        self.manager.send_input_result(self.events[0].request_id, 'cancelled')
        self._wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        self.assertEqual([event.type for event in self.events], ['input_request', 'input_cancel', 'finished'])

    def test_default_mode_overrides_inherited_foreground_and_invalid_mode_fails(self):
        path = self.root / 'input' / 'task.py'
        path.parent.mkdir(exist_ok=True)
        write_skill_manifest(path)
        path.write_text("import os\nfrom huanzhizhi.task_sdk import TaskSession\n"
                        "s=TaskSession(__file__)\nassert os.environ['HUANZHIZHI_INPUT_MODE']=='background'\n"
                        "s.emit_finished('完成','测试','默认后台')\n", encoding='utf-8')
        with patch.dict(os.environ, HUANZHIZHI_INPUT_MODE='foreground'):
            self.manager.start('input', path)
        self._wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        with self.assertRaisesRegex(ValueError, 'input_mode'):
            self.manager.start('input', path, input_mode='invalid')

    def test_smooth_background_mode_reaches_subprocess_sdk(self):
        self._start('''
            from huanzhizhi.task_sdk import _input_mode
            assert _input_mode() == 'background_smooth'
            session.emit_finished('完成', '测试', '平滑后台')
        ''', mode='background_smooth')
        self._wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        self.assertEqual([event.type for event in self.events], ['finished'])


if __name__ == '__main__':
    unittest.main()
