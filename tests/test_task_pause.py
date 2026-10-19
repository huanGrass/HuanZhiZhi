"""Pause the real SDK subprocess at action boundaries, without native input."""
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtTest, QtWidgets
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import (
    FinishedEvent, InputRequestEvent, LogEvent, ParametersAppliedEvent,
    ProtocolError, encode_pause_message, parse_event_line,
)
from tests.skill_factory import write_skill_manifest


class TaskPauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'pause'
        self.root.mkdir()
        self.manager = TaskProcessManager()
        self.events, self.errors, self.pause_states = [], [], []
        self.manager.event_received.connect(self.events.append)
        self.manager.error.connect(self.errors.append)
        self.manager.pause_state_changed.connect(self.pause_states.append)

    def tearDown(self):
        self.manager.close()
        self.app.processEvents()
        self.temp.cleanup()

    def start(self, source):
        entry = self.root / 'task.py'
        entry.write_text(textwrap.dedent(source), encoding='utf-8')
        write_skill_manifest(entry, task_id='pause', parameters=[
            dict(key='count', label='次数', default=3, minimum=1, maximum=10, runtime=True)])
        self.manager.start('pause', entry)

    def wait(self, predicate):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 5000:
            QtTest.QTest.qWait(5)
        self.assertTrue(predicate(), f'{self.manager.state}, {self.manager.pause_state}, {self.errors}')

    def test_inflight_input_finishes_before_pause_and_parameters_apply_while_paused(self):
        self.start('''
            from huanzhizhi.task_sdk import TaskSession, _request_foreground_input
            s = TaskSession(__file__)
            window = dict(hwnd=1, title='Test', left=0, top=0, width=100, height=100)
            _request_foreground_input(window, 'drag', 10, 20, 50, 60, .5)
            s.emit_log('confirmed')
            s.wait_if_paused()
            s.emit_log('fresh observation: ' + str(s.options['count']))
            while not s.stop_requested:
                s.wait_if_paused()
                s.wait_for_stop(.01)
            s.emit_finished('已停止', '测试', '结束', stopped_by_request=True)
        ''')
        self.wait(lambda: any(isinstance(e, InputRequestEvent) for e in self.events))
        request = next(e for e in self.events if isinstance(e, InputRequestEvent))
        self.manager.set_paused(True)
        QtTest.QTest.qWait(80)
        self.assertEqual(self.manager.pause_state, 'pausing')
        self.assertFalse(any(isinstance(e, LogEvent) for e in self.events))
        self.manager.send_input_result(request.request_id)
        self.wait(lambda: self.manager.pause_state == 'paused')
        self.assertEqual([e.message for e in self.events if isinstance(e, LogEvent)], ['confirmed'])
        self.manager.update_parameters(1, {'count': 7})
        self.wait(lambda: any(isinstance(e, ParametersAppliedEvent) for e in self.events))
        QtTest.QTest.qWait(80)
        self.assertEqual(self.manager.pause_state, 'paused')
        self.assertEqual([e.message for e in self.events if isinstance(e, LogEvent)], ['confirmed'])
        self.manager.set_paused(False)
        self.wait(lambda: any(isinstance(e, LogEvent) and e.message == 'fresh observation: 7' for e in self.events))
        self.manager.set_paused(True)
        self.wait(lambda: self.manager.pause_state == 'paused')
        self.manager.stop()
        self.wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])
        self.assertTrue(next(e for e in self.events if isinstance(e, FinishedEvent)).stopped_by_request)
        self.assertEqual(self.pause_states[:5], ['running', 'pausing', 'paused', 'resuming', 'running'])

    def test_stop_during_pause_wait_never_executes_next_action(self):
        self.start('''
            from huanzhizhi.task_sdk import TaskSession
            s = TaskSession(__file__)
            s.emit_log('ready')
            while not s.pause_requested and not s.stop_requested:
                s.wait_for_stop(.01)
            s.wait_if_paused()
            if not s.stop_requested:
                s.emit_log('unexpected action')
            s.emit_finished('已停止', '测试', '结束', stopped_by_request=True)
        ''')
        self.wait(lambda: bool(self.events))
        self.manager.set_paused(True)
        self.wait(lambda: self.manager.pause_state == 'paused')
        self.manager.stop()
        self.wait(lambda: self.manager.state == 'idle')
        self.assertEqual([e.message for e in self.events if isinstance(e, LogEvent)], ['ready'])
        self.assertEqual(self.errors, [])

    def test_worker_shutdown_event_exits_pause_without_resume(self):
        self.start('''
            from pathlib import Path
            from threading import Event, Thread
            import time
            from huanzhizhi.task_sdk import TaskSession
            s = TaskSession(__file__)
            halt = Event()
            def close_window():
                while not Path('close').exists():
                    time.sleep(.01)
                halt.set()
            Thread(target=close_window, daemon=True).start()
            s.emit_log('ready')
            while not s.pause_requested:
                s.wait_for_stop(.01)
            s.wait_if_paused(cancel_event=halt)
            assert halt.is_set()
            s.emit_finished('已关闭', '测试', '窗口关闭')
        ''')
        self.wait(lambda: bool(self.events))
        self.manager.set_paused(True)
        self.wait(lambda: self.manager.pause_state == 'paused')
        (self.root / 'close').touch()
        self.wait(lambda: self.manager.state == 'idle')
        self.assertEqual(self.errors, [])

    def test_pause_protocol_rejects_non_boolean_and_unsolicited_acknowledgement(self):
        for value in (1, 'true', None):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_event_line(json.dumps(dict(protocol_version=1, type='pause_state', paused=value)))
        with self.assertRaises(TypeError):
            encode_pause_message(1)
        with self.assertRaises(RuntimeError):
            self.manager.set_paused(True)
        self.start('''
            from huanzhizhi.task_sdk import TaskSession
            s = TaskSession(__file__)
            s._emit_event('pause_state', paused=True)
            s.wait_for_stop(1)
            s.emit_finished('已停止', '测试', '结束', stopped_by_request=True)
        ''')
        self.wait(lambda: self.manager.state == 'idle')
        self.assertTrue(any('暂停确认' in error for error in self.errors))


if __name__ == '__main__':
    unittest.main()
