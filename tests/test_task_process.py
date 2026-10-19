from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import (
    FinishedEvent,
    LogEvent,
    PointEvent,
    ProtocolError,
    SpeakEvent,
    StatusEvent,
    parse_event_line,
    validate_options,
)


class TaskProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.manager = TaskProcessManager(terminate_timeout_ms=120, kill_timeout_ms=120)
        self.events: list[object] = []
        self.states: list[str] = []
        self.errors: list[str] = []
        self.manager.event_received.connect(self.events.append)
        self.manager.state_changed.connect(self.states.append)
        self.manager.error.connect(self.errors.append)

    def tearDown(self) -> None:
        self.manager.close()
        self.app.processEvents()
        self.temporary_directory.cleanup()

    def test_normal_process_receives_start_and_emits_every_event_type(self) -> None:
        script = self._write_script(
            """
            import json, sys
            assert sys.argv[1:] == ["--run"]
            start = json.loads(sys.stdin.readline())
            assert start == {"protocol_version": 1, "type": "start", "task_id": "demo", "options": {"level": 3}}
            print()
            events = [
                {"protocol_version":1,"type":"status","status_title":"运行中","title":"演示","detail":"开始"},
                {"protocol_version":1,"type":"log","message":"日志"},
                {"protocol_version":1,"type":"speak","text":"你好","audio_path":None,"action":"talk"},
                {"protocol_version":1,"type":"point","x":123,"y":456},
                {"protocol_version":1,"type":"finished","status_title":"完成","title":"演示","detail":"结束","error":None,"stopped_by_request":False},
            ]
            for event in events:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            """
        )

        self.manager.start("demo", script, {"level": 3})
        self._wait_until(lambda: self.manager.state == "idle")

        self.assertEqual([type(event) for event in self.events], [StatusEvent, LogEvent, SpeakEvent, PointEvent, FinishedEvent])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.states[0], "starting")
        self.assertIn("running", self.states)
        self.assertEqual(self.states[-1], "idle")

    def test_invalid_stdout_protocol_terminates_task_and_reports_error(self) -> None:
        script = self._write_script(
            """
            import sys, time
            sys.stdin.readline()
            print('{"protocol_version":1,"type":"unknown"}', flush=True)
            time.sleep(5)
            """
        )

        self.manager.start("bad", script)
        self._wait_until(lambda: self.manager.state == "idle")

        self.assertEqual(len(self.errors), 1)
        self.assertIn("任务协议错误", self.errors[0])
        self.assertIn("未知任务事件类型", self.errors[0])
        self.assertIn("stopping", self.states)

    def test_nonzero_crash_includes_stderr_diagnostic(self) -> None:
        script = self._write_script(
            """
            import sys
            sys.stdin.readline()
            print('boom detail', file=sys.stderr, flush=True)
            raise SystemExit(7)
            """
        )

        self.manager.start("crash", script)
        self._wait_until(lambda: self.manager.state == "idle")

        self.assertEqual(len(self.errors), 1)
        self.assertIn("exit_code=7", self.errors[0])
        self.assertIn("boom detail", self.errors[0])
        self.assertIn("boom detail", self.manager.stderr_text)

    def test_zero_exit_without_finished_is_error(self) -> None:
        script = self._write_script(
            """
            import sys
            sys.stdin.readline()
            print('{"protocol_version":1,"type":"log","message":"only"}', flush=True)
            """
        )
        self.manager.start("unfinished", script)
        self._wait_until(lambda: self.manager.state == "idle")

        self.assertEqual(self.events, [LogEvent("only")])
        self.assertEqual(self.errors, ["任务进程退出前未发送 finished 事件"])

    def test_stop_sends_protocol_message_and_waits_for_finished(self) -> None:
        script = self._write_script(
            """
            import json, sys
            json.loads(sys.stdin.readline())
            print('{"protocol_version":1,"type":"status","status_title":"运行中","title":"等待","detail":"等待停止"}', flush=True)
            stop = json.loads(sys.stdin.readline())
            assert stop == {"protocol_version":1,"type":"stop"}
            print('{"protocol_version":1,"type":"finished","status_title":"已停止","title":"等待","detail":"用户停止","error":null,"stopped_by_request":true}', flush=True)
            """
        )
        self.manager.start("stop", script)
        self._wait_until(lambda: self.manager.state == "running" and bool(self.events))

        self.manager.stop()
        self._wait_until(lambda: self.manager.state == "idle")

        self.assertEqual(self.errors, [])
        self.assertIsInstance(self.events[-1], FinishedEvent)
        self.assertTrue(self.events[-1].stopped_by_request)
        self.assertIn("stopping", self.states)

    def test_second_task_cannot_start_until_first_is_idle(self) -> None:
        script = self._write_script(
            """
            import json, sys
            json.loads(sys.stdin.readline())
            print('{"protocol_version":1,"type":"status","status_title":"运行中","title":"等待","detail":"等待停止"}', flush=True)
            json.loads(sys.stdin.readline())
            print('{"protocol_version":1,"type":"finished","status_title":"已停止","title":"等待","detail":"停止","error":null,"stopped_by_request":true}', flush=True)
            """
        )
        self.manager.start("first", script)
        self._wait_until(lambda: self.manager.state == "running")

        with self.assertRaisesRegex(RuntimeError, "已有任务"):
            self.manager.start("second", script)

        self.manager.stop()
        self._wait_until(lambda: self.manager.state == "idle")

    def test_stop_timeout_terminates_unresponsive_task(self) -> None:
        script = self._write_script(
            """
            import json, sys, time
            json.loads(sys.stdin.readline())
            print('{"protocol_version":1,"type":"status","status_title":"运行中","title":"阻塞","detail":"忽略停止"}', flush=True)
            json.loads(sys.stdin.readline())
            time.sleep(10)
            """
        )
        self.manager.start("blocked", script)
        self._wait_until(lambda: self.manager.state == "running" and bool(self.events))

        self.manager.stop()
        self._wait_until(lambda: self.manager.state == "idle")

        self.assertEqual(len(self.errors), 1)
        self.assertIn("异常退出", self.errors[0])
        self.assertIn("stopping", self.states)

    def test_protocol_validator_rejects_missing_extra_wrong_and_non_object(self) -> None:
        invalid_lines = (
            '[]',
            '{"protocol_version":1,"type":"log"}',
            '{"protocol_version":1,"type":"log","message":"x","extra":1}',
            '{"protocol_version":1,"type":"log","message":"   "}',
            '{"protocol_version":true,"type":"log","message":"x"}',
            '{"protocol_version":1.0,"type":"log","message":"x"}',
            '{"protocol_version":1,"type":"point","x":true,"y":2}',
            '{"protocol_version":1,"type":"point","x":NaN,"y":2}',
            '{"protocol_version":1,"type":"log","message":"a","message":"b"}',
        )
        for line in invalid_lines:
            with self.subTest(line=line), self.assertRaises(ProtocolError):
                parse_event_line(line)

    def test_options_and_start_arguments_are_strict(self) -> None:
        with self.assertRaises(TypeError):
            validate_options({1: "bad"})
        with self.assertRaises(TypeError):
            validate_options({"nested": [{1: "bad"}]})
        with self.assertRaises(TypeError):
            validate_options({"tuple": (1, 2)})
        with self.assertRaises(ValueError):
            validate_options({"number": float("nan")})
        with self.assertRaisesRegex(ValueError, "task_id"):
            self.manager.start(" ", self.root / "missing.py")
        with self.assertRaises(FileNotFoundError):
            self.manager.start("missing", self.root / "missing.py")

    def _write_script(self, source: str) -> Path:
        path = self.root / f"task_{len(tuple(self.root.iterdir()))}.py"
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        return path

    def _wait_until(self, predicate, timeout_ms: int = 5_000) -> None:
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not predicate():
            if timer.elapsed() >= timeout_ms:
                self.fail(f"等待任务状态超时，当前状态：{self.manager.state}，错误：{self.errors}")
            self.app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
            QtCore.QThread.msleep(5)


if __name__ == "__main__":
    unittest.main()
