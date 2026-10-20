from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from huanzhizhi.tasks.catalog import TaskCatalog
from tests.skill_factory import write_skill_manifest
from huanzhizhi.tasks.protocol import parse_event_line


class TaskSdkTests(unittest.TestCase):
    def test_runtime_directory_is_local_lazy_and_survives_rename(self):
        from huanzhizhi.task_sdk import runtime_directory
        script = self._write_script('''
            from huanzhizhi.task_sdk import TaskSession, runtime_directory
            session = TaskSession(__file__)
            assert session.runtime_dir == runtime_directory(__file__)
            state = session.runtime_dir / 'state'
            state.mkdir(parents=True, exist_ok=True)
            checkpoint = state / 'checkpoint.txt'
            if not checkpoint.exists():
                checkpoint.write_text('saved state')
            session.emit_finished('完成', checkpoint.read_text(), str(session.runtime_dir))
        ''', task_id='runtime')
        expected = script.resolve().parent / '.runtime'
        self.assertEqual(runtime_directory(script), expected)
        self.assertFalse(expected.exists())
        for entry in (script, script.parent.with_name('新名称') / 'task.py'):
            if entry != script:
                script.parent.rename(entry.parent)
            start = self._json_line(dict(protocol_version=1, type='start', task_id=entry.parent.name, options={}))
            result = self._run_with_open_stdin(entry, start)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['title'], 'saved state')
            self.assertEqual(Path(json.loads(result.stdout)['detail']), entry.resolve().parent / '.runtime')

    def test_legacy_executables_have_separate_runtime_directories(self):
        from huanzhizhi.task_sdk import runtime_directory
        executable = self.root / 'task-first.exe'
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)):
            self.assertEqual(runtime_directory(self.root / 'unpacked/task.py'),
                             self.root.resolve() / '.runtime/task-first')
        self.assertNotEqual(runtime_directory(executable), runtime_directory(self.root / 'task-second.exe'))
        self.assertFalse((self.root / '.runtime').exists())

    def test_renamed_directory_starts_with_new_protocol_identity(self):
        script = self._write_script('''
            from huanzhizhi.task_sdk import TaskSession
            session = TaskSession(__file__)
            session.emit_finished('完成', session.skill['name'], session.task_id)
        ''', task_id='before')
        renamed = script.parent.with_name('改名后的技能')
        script.parent.rename(renamed)
        start = self._json_line(dict(protocol_version=1, type='start', task_id=renamed.name, options={}))
        result = self._run_with_open_stdin(renamed / 'task.py', start)
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads(result.stdout)
        self.assertEqual((event['title'], event['detail']), (renamed.name, renamed.name))

    def test_frozen_entry_reads_manifest_beside_executable(self):
        from huanzhizhi.task_sdk import load_skill
        executable = self.root/'task-frozen.exe'
        executable.write_bytes(b'fixture')
        write_skill_manifest(executable, name='独立技能')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)):
            self.assertEqual(load_skill(self.root/'unpacked/task.py')['name'], 'frozen')

    def test_manifest_alone_controls_display_and_runtime_defaults_and_limits(self):
        script = self._write_script('''
            from huanzhizhi.task_sdk import TaskSession
            session = TaskSession(__file__)
            session.emit_finished('完成', session.skill['name'], str(session.options['count']))
        ''', task_id='manifest')
        unchanged_entry = script.read_bytes()
        for name, default, maximum in (('初版', 2, 10), ('新版', 5, 6)):
            write_skill_manifest(script, name=name, parameters=[
                dict(key='count', label='次数', default=default, minimum=1, maximum=maximum)])
            task = TaskCatalog(script.parent.parent).reload()[0]
            self.assertEqual((task.name, task.parameters[0].default), ('manifest', default))
            start = self._json_line(dict(protocol_version=1, type='start', task_id='manifest', options={}))
            result = self._run_with_open_stdin(script, start)
            self.assertEqual(result.returncode, 0, result.stderr)
            event = json.loads(result.stdout)
            self.assertEqual((event['title'], event['detail']), ('manifest', str(default)))
        invalid = self._json_line(dict(protocol_version=1, type='start', task_id='manifest', options={'count':7}))
        result = self._run_with_open_stdin(script, invalid)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(script.read_bytes(), unchanged_entry)

    def test_runtime_values_change_only_at_safe_point_and_are_acknowledged(self):
        script = self._write_script('''
            import time
            from huanzhizhi.task_sdk import TaskSession

            s = TaskSession(__file__)
            deadline = time.monotonic() + 2
            while s._pending_parameters is None and time.monotonic() < deadline:
                time.sleep(.01)
            assert s.options == {'count':3, 'hwnd':123}
            assert s.apply_parameter_updates() == {'count':7, 'hwnd':123}
            assert s.apply_parameter_updates() == s.options
            s.emit_finished('完成','测试','已应用')
        ''', task_id='runtime', parameters=[{'key': 'count', 'label': '次数', 'default': 3, 'minimum': 1, 'maximum': 10, 'runtime': True}])
        messages = self._json_line(dict(protocol_version=1, type='start', task_id='runtime', options={'hwnd':123}))
        messages += self._json_line(dict(protocol_version=1, type='update_parameters', request_id=1, values={'count':7}))
        result = self._run_with_open_stdin(script, messages)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        events = [parse_event_line(line) for line in result.stdout.splitlines()]
        self.assertEqual([event.type for event in events], ['parameters_applied', 'finished'])
        self.assertEqual((events[0].request_id, events[0].values), (1, {'count':7}))

    def test_runtime_update_rejects_unknown_static_and_out_of_range_values(self):
        script = self._write_script('''
            from huanzhizhi.task_sdk import TaskSession

                          {'key':'fixed','label':'固定','default':3,'minimum':1,'maximum':10})
            s = TaskSession(__file__)
            s.wait_for_stop(2)
            s.emit_finished('完成','测试','结束')
        ''', task_id='runtime', parameters=[{'key': 'count', 'label': '次数', 'default': 3, 'minimum': 1, 'maximum': 10, 'runtime': True}, {'key': 'fixed', 'label': '固定', 'default': 3, 'minimum': 1, 'maximum': 10}])
        for values in ({'unknown':1}, {'fixed':4}, {'count':11}, {'count':True}, {}):
            with self.subTest(values=values):
                messages = self._json_line(dict(protocol_version=1, type='start', task_id='runtime', options={}))
                messages += self._json_line(dict(protocol_version=1, type='update_parameters', request_id=1, values=values))
                result = self._run_with_open_stdin(script, messages)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'parameters_applied', result.stdout)

    def test_stop_takes_priority_over_queued_parameter_update(self):
        script = self._write_script('''
            from huanzhizhi.task_sdk import TaskSession

            s = TaskSession(__file__)
            assert s.wait_for_stop(2)
            assert s.apply_parameter_updates() == {'count':3}
            s.emit_finished('已停止','测试','结束',stopped_by_request=True)
        ''', task_id='runtime', parameters=[{'key': 'count', 'label': '次数', 'default': 3, 'minimum': 1, 'maximum': 10, 'runtime': True}])
        messages = self._json_line(dict(protocol_version=1, type='start', task_id='runtime', options={}))
        messages += self._json_line(dict(protocol_version=1, type='update_parameters', request_id=1, values={'count':7}))
        messages += self._json_line(dict(protocol_version=1, type='stop'))
        result = self._run_with_open_stdin(script, messages)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual([parse_event_line(line).type for line in result.stdout.splitlines()], ['finished'])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_describe_outputs_exactly_one_json_without_gui_imports(self) -> None:
        script = self._write_script(
            """
            import sys
            from huanzhizhi.task_sdk import describe
            assert "PySide6" not in sys.modules
            assert "PySide6.QtCore" not in sys.modules
            if describe(__file__):
                raise SystemExit(0)
            raise AssertionError("describe mode was not selected")
            """, task_id='demo', name='演示任务', description='说明'
        )

        result = subprocess.run(
            [sys.executable, str(script), "--describe"],
            capture_output=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(result.stderr, b"")
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            json.loads(lines[0]),
            {
                "protocol_version": 1,
                "id": "demo",
                "name": "demo",
                "description": "说明",
                "presentation": "persistent",
            },
        )

    def test_real_task_catalog_discovers_sdk_described_task(self) -> None:
        tasks_root = self.root / "tasks"
        package = tasks_root / "sdk_demo"
        package.mkdir(parents=True)
        script = package / "task.py"
        script.write_text(
            textwrap.dedent(
                """
                from huanzhizhi.task_sdk import TaskSession, describe
                if describe(__file__):
                    raise SystemExit(0)
                session = TaskSession(__file__)
                session.emit_finished("完成", "SDK 演示", "结束")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        write_skill_manifest(script, name="SDK 演示", description="由公共 SDK 描述")
        catalog = TaskCatalog(tasks_root)

        tasks = catalog.reload()

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, "sdk_demo")
        self.assertEqual(tasks[0].name, "sdk_demo")
        self.assertEqual(tasks[0].description, "由公共 SDK 描述")
        self.assertEqual(tasks[0].presentation, "persistent")
        self.assertEqual(tasks[0].script_path, script.resolve())

    def test_describe_supports_launch_only_presentation(self) -> None:
        script = self._write_script(
            """
            from huanzhizhi.task_sdk import describe
            if describe(__file__):
                raise SystemExit(0)
            """, task_id='launcher', name='启动器', description='启动独立程序', presentation='launch_only'
        )

        result = subprocess.run(
            [sys.executable, str(script), "--describe"], capture_output=True, timeout=5
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(json.loads(result.stdout), {
            "protocol_version": 1,
            "id": "launcher",
            "name": "launcher",
            "description": "启动独立程序",
            "presentation": "launch_only",
        })

    def test_run_session_emits_host_compatible_exact_events(self) -> None:
        script = self._write_script(
            """
            from pathlib import Path
            from huanzhizhi.task_sdk import TaskSession, describe
            if describe(__file__):
                raise SystemExit(0)
            session = TaskSession(__file__)
            assert session.options == {"level": 3}
            session.emit_status("运行中", "演示", "开始")
            session.emit_log("日志")
            session.emit_speak("你好", None, "talk")
            session.emit_speak("文件", Path("voice.wav"), "talk_v")
            session.emit_point(123, 456)
            session.emit_finished("完成", "演示", "结束", error=None, stopped_by_request=False)
            """, task_id='demo', name='演示'
        )
        start = self._json_line(
            {"protocol_version": 1, "type": "start", "task_id": "demo", "options": {"level": 3}}
        )

        result = self._run_with_open_stdin(script, start)

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        events = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
        validated_events = [parse_event_line(line) for line in result.stdout.splitlines()]
        self.assertEqual([event["type"] for event in events], ["status", "log", "speak", "speak", "point", "finished"])
        self.assertEqual([event.type for event in validated_events], ["status", "log", "speak", "speak", "point", "finished"])
        self.assertTrue(all(event["protocol_version"] == 1 for event in events))
        self.assertEqual(events[2], {"protocol_version": 1, "type": "speak", "text": "你好", "audio_path": None, "action": "talk"})
        self.assertEqual(events[3]["audio_path"], "voice.wav")
        self.assertEqual(set(events[-1]), {"protocol_version", "type", "status_title", "title", "detail", "error", "stopped_by_request"})

    def test_desktop_click_automatically_emits_session_point(self) -> None:
        script = self._write_script(
            """
            from unittest.mock import Mock
            from huanzhizhi.task_sdk import TaskSession
            from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo
            session = TaskSession(__file__)
            native = Mock()
            native.post_message.return_value = True
            api = Win32DesktopApi(native=native, configure_dpi_awareness=False)
            window = WindowInfo(123, 'fixture', -200, 100, 320, 200)
            api._require_unchanged = Mock(return_value=window)
            api.post_click(window, 30, 40)
            assert native.post_message.call_count == 3
            session._stop_event.set()
            try:
                api.post_click(window, 30, 40)
            except InterruptedError:
                pass
            else:
                raise AssertionError('stopped task clicked')
            assert native.post_message.call_count == 3
            session.emit_finished('完成', '演示', '结束')
            """, task_id='demo'
        )
        start = self._json_line(dict(protocol_version=1, type='start', task_id='demo', options={}))
        result = self._run_with_open_stdin(script, start)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([event['type'] for event in events], ['point', 'finished'])
        self.assertEqual((events[0]['x'], events[0]['y']), (-170, 140))

    def test_stop_is_received_while_task_work_loop_is_running(self) -> None:
        script = self._write_script(
            """
            import time
            from huanzhizhi.task_sdk import TaskSession, describe
            if describe(__file__):
                raise SystemExit(0)
            session = TaskSession(__file__)
            session.emit_status("运行中", "工作", "等待停止")
            deadline = time.monotonic() + 4
            while not session.stop_requested and time.monotonic() < deadline:
                time.sleep(0.01)
            if not session.stop_requested:
                raise RuntimeError("stop was not received")
            session.emit_finished("已停止", "工作", "用户停止", stopped_by_request=True)
            """, task_id='worker', name='工作任务'
        )
        messages = self._json_line(
            {"protocol_version": 1, "type": "start", "task_id": "worker", "options": {}}
        ) + self._json_line({"protocol_version": 1, "type": "stop"})

        result = self._run_with_open_stdin(script, messages)

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        events = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
        self.assertEqual(events[-1]["type"], "finished")
        self.assertTrue(events[-1]["stopped_by_request"])

    def test_invalid_background_control_message_stops_work_and_exits_nonzero(self) -> None:
        script = self._write_script(
            """
            import time
            from huanzhizhi.task_sdk import TaskSession, describe
            if describe(__file__):
                raise SystemExit(0)
            session = TaskSession(__file__)
            while not session.stop_requested:
                time.sleep(0.01)
            session.emit_finished("停止", "控制", "不会发出")
            """, task_id='control', name='控制'
        )
        messages = self._json_line(
            {"protocol_version": 1, "type": "start", "task_id": "control", "options": {}}
        ) + self._json_line({"protocol_version": 1, "type": "stop", "extra": True})

        result = self._run_with_open_stdin(script, messages)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn("非法 stop 消息".encode("utf-8"), result.stderr)

    def test_invalid_start_variants_exit_nonzero_on_stderr(self) -> None:
        script = self._session_script("expected")
        invalid_messages = (
            b'\xff\n',
            b'{"protocol_version":1,"type":"start","task_id":"expected","options":{},"extra":1}\n',
            b'{"protocol_version":1,"protocol_version":1,"type":"start","task_id":"expected","options":{}}\n',
            b'{"protocol_version":NaN,"type":"start","task_id":"expected","options":{}}\n',
            b'{"protocol_version":1,"type":"start","task_id":"wrong","options":{}}\n',
            b'{"protocol_version":1,"type":"start","task_id":"expected"}\n',
        )
        for message in invalid_messages:
            with self.subTest(message=message):
                result = subprocess.run(
                    [sys.executable, str(script), "--run"],
                    input=message,
                    capture_output=True,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn("非法 start 消息".encode("utf-8"), result.stderr)

    def test_entry_arguments_are_exclusive_and_finished_is_single_use(self) -> None:
        script = self._session_script("expected")
        for arguments in ([], ["--bad"], ["--run", "extra"], ["--check-environment", "--run"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, str(script), *arguments],
                    capture_output=True,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--describe、--run 或 --check-environment".encode("utf-8"), result.stderr)

        duplicate_script = self._write_script(
            """
            from huanzhizhi.task_sdk import TaskSession, describe
            if describe(__file__):
                raise SystemExit(0)
            session = TaskSession(__file__)
            session.emit_finished("完成", "重复", "一次")
            session.emit_finished("完成", "重复", "两次")
            """, task_id='duplicate', name='重复'
        )
        start = self._json_line(
            {"protocol_version": 1, "type": "start", "task_id": "duplicate", "options": {}}
        )
        result = self._run_with_open_stdin(duplicate_script, start)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.decode("utf-8").splitlines()), 1)
        self.assertIn("最多只能发送一次".encode("utf-8"), result.stderr)

    def test_emitters_reject_required_strings_that_are_empty_after_strip(self) -> None:
        invalid_calls = (
            'session.emit_status(" ", "标题", "详情")',
            'session.emit_log("")',
            'session.emit_speak("文本", None, "  ")',
            'session.emit_finished("完成", "", "详情")',
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                script = self._write_script(
                    f"""
                    from huanzhizhi.task_sdk import TaskSession, describe
                    if describe(__file__):
                        raise SystemExit(0)
                    session = TaskSession(__file__)
                    {call}
                    """, task_id='strings'
                )
                start = self._json_line(
                    {"protocol_version": 1, "type": "start", "task_id": "strings", "options": {}}
                )

                result = self._run_with_open_stdin(script, start)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn("必须是非空字符串".encode("utf-8"), result.stderr)

    def _session_script(self, task_id: str) -> Path:
        return self._write_script(
            f"""
            from huanzhizhi.task_sdk import TaskSession, describe
            if describe(__file__):
                raise SystemExit(0)
            session = TaskSession(__file__)
            session.emit_finished("完成", "测试", "结束")
            """, task_id=task_id
        )

    def _write_script(self, source: str, *, task_id: str, **metadata) -> Path:
        path = self.root / f"case_{len(tuple(self.root.iterdir()))}" / task_id / "task.py"
        path.parent.mkdir(parents=True)
        write_skill_manifest(path, **metadata)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        return path

    @staticmethod
    def _json_line(payload: dict[str, object]) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    @staticmethod
    def _run_with_open_stdin(script: Path, input_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(script), "--run"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        process.stdin.write(input_bytes)
        process.stdin.flush()
        try:
            return_code = process.wait(timeout=5)
        finally:
            process.stdin.close()
        assert process.stdout is not None and process.stderr is not None
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        return subprocess.CompletedProcess(
            process.args,
            return_code,
            stdout,
            stderr,
        )


if __name__ == "__main__":
    unittest.main()
