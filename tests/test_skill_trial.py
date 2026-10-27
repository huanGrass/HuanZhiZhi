import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtWidgets

from huanzhizhi.skill_trial import run_trial
from huanzhizhi.tasks.catalog import TaskCatalog
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.tasks.protocol import parse_event_line
from tests.pet_factory import create_test_pet
from tests.skill_factory import write_skill_manifest


SCRIPT = '''
from huanzhizhi.task_sdk import TaskSession
session = TaskSession(__file__)
try:
    if session.options['mode'] == 'hint':
        {hint}
    else:
        {auto}
except InterruptedError:
    pass
finally:
    session.emit_hint([], 'clear', '结束')
    session.emit_finished('停止', session.task_id, '完成', stopped_by_request=session.stop_requested)
'''


class SkillTrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.skill = self.root / 'tasks' / '验收测试'
        self.skill.mkdir(parents=True)
        (self.skill / 'task.py').touch()
        write_skill_manifest(self.skill / 'task.py', parameters=[dict(
            key='mode', label='模式', type='choice', default='hint', choices=[
                dict(value='hint', label='提示'), dict(value='auto', label='自动')])])
        self.pet = create_test_pet()
        self.host = TaskHostComponent(self.pet, self.root / 'tasks')
        self.pet.show()
        self.addCleanup(self.pet.close)
        self.addCleanup(self.host.close)
        self.channel = self.root / 'clicks.jsonl'
        self.channel.touch()
        self.host_path = self.root / 'host_hints.jsonl'
        self.host_path.touch()
        self.result_path = self.root / 'result.json'
        self.live = []
        self.offset = 0
        self.relay = QtCore.QTimer()
        self.relay.setInterval(10)
        self.relay.timeout.connect(self.forward)
        self.addCleanup(self.relay.stop)

    def forward(self):
        with self.channel.open('rb') as stream:
            stream.seek(self.offset)
            for line in stream:
                self.host.show_hint(parse_event_line(line.decode()), self.host_path)
            self.offset = stream.tell()
        if self.result_path.exists():
            self.live.append(json.loads(self.result_path.read_text(encoding='utf-8')))

    def run_case(self, hint, timeout=5, options=None,
                 auto="session.emit_log('业务已确认')\nsession.wait_for_stop(10)"):
        # Actual SDK child and process manager; only the outer agent's file relay is supplied here.
        body = hint.replace('\n', '\n        ')
        (self.skill / 'task.py').write_text(SCRIPT.format(hint=body, auto=auto.replace('\n', '\n        ')),
                                          encoding='utf-8')
        task = TaskCatalog(self.skill.parent).reload()[0]
        self.relay.start()
        report = {}
        with patch.dict(os.environ, HUANZHIZHI_CLICK_EVENTS=str(self.channel)):
            passed = run_trial(task, options or {'mode': 'auto'}, channel=str(self.channel),
                               directory=self.root, result_path=self.result_path,
                               origin=time.perf_counter(), report=report, until_log='业务已确认', timeout=timeout)
            self.assertEqual(os.environ['HUANZHIZHI_CLICK_EVENTS'], str(self.channel))
        self.relay.stop()
        return passed, report

    def test_real_overlay_is_live_before_auto_and_clear_before_next_mode(self):
        passed, report = self.run_case("while not session.stop_requested:\n    session.emit_hint([(350, 350), (450, 350)], 'click', '建议')\n    session.wait_for_stop(.5)")
        self.assertTrue(passed, report)
        self.assertEqual([run['mode'] for run in report['runs']], ['hint', 'auto'])
        hint, auto = report['runs']
        self.assertTrue(hint['host_cleared'])
        self.assertEqual(hint['input_attempts'], 0)
        self.assertLess(hint['stop_requested_at_seconds'], auto['matched_at_seconds'])
        self.assertTrue(any(r['status'] == 'running' and len(r['runs']) == 1
                            and (r['runs'][0]['host_hint'] or {}).get('visible') for r in self.live))
        self.assertEqual(json.loads(self.result_path.read_text(encoding='utf-8'))['status'], 'passed')

    def test_expired_line_and_self_report_cannot_pass_or_start_auto(self):
        passed, report = self.run_case("session.emit_hint([(350, 350), (450, 350)], 'line', '短线')\nsession.emit_log('业务已确认')\nsession.wait_for_stop(10)", timeout=2.5)
        self.assertFalse(passed)
        self.assertEqual(len(report['runs']), 1)
        self.assertTrue(report['timed_out'])
        states = [json.loads(line) for line in self.host_path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(s['visible'] for s in states))
        self.assertTrue(any(not s['visible'] and s['kind'] == 'line' for s in states))

    def test_input_is_rejected_and_auto_is_never_started(self):
        passed, report = self.run_case("from huanzhizhi.task_sdk import _request_foreground_input\n_request_foreground_input(dict(hwnd=1, title='测试窗口', left=0, top=0, width=100, height=100), 'click', 10, 10, timeout=2)")
        self.assertFalse(passed)
        self.assertEqual(len(report['runs']), 1)
        self.assertEqual(report['input_attempts'], 1, report)
        self.assertIn('提示验收期间禁止输入', report['errors'])
        self.assertNotIn('input_request', self.channel.read_text(encoding='utf-8'))

    def test_keyboard_and_scroll_are_blocked_even_when_skill_catches_the_error(self):
        for action in ("api.post_text(info, 'UNEXPECTED')",
                       "api.post_key(info, 'ENTER', wait=lambda _: None)",
                       "api.post_scroll(info, 10, 10, -120)"):
            with self.subTest(action=action):
                sent = self.root / 'native_input.txt'
                sent.unlink(missing_ok=True)
                passed, report = self.run_case(
                    "from pathlib import Path\n"
                    "from types import SimpleNamespace\n"
                    "from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo\n"
                    f"sent = Path({str(sent)!r})\n"
                    "native = SimpleNamespace(post_message=lambda *args: bool(sent.write_text('sent')))\n"
                    "api = Win32DesktopApi(native=native, configure_dpi_awareness=False)\n"
                    "info = WindowInfo(123, 'fixture', 0, 0, 100, 100)\n"
                    "api._require_unchanged = lambda _: info\n"
                    "try:\n    " + action + "\n"
                    "except RuntimeError:\n    pass\n"
                    "while not session.stop_requested:\n"
                    "    session.emit_hint([(350, 350)], 'click', '建议')\n"
                    "    session.wait_for_stop(.5)")
                self.assertFalse(sent.exists(), '提示验收期间不应发送原生输入消息')
                self.assertFalse(passed, report)
                self.assertEqual(len(report['runs']), 1)
                self.assertGreaterEqual(report['input_attempts'], 1, report['errors'])
                self.assertIn('提示验收期间禁止输入', report['errors'])

    def test_auto_trial_can_send_keyboard_and_scroll_after_hint_trial(self):
        sent = self.root / 'native_input.txt'
        passed, report = self.run_case(
            "while not session.stop_requested:\n"
            "    session.emit_hint([(350, 350)], 'click', '建议')\n"
            "    session.wait_for_stop(.5)",
            auto="from pathlib import Path\n"
                 "from types import SimpleNamespace\n"
                 "from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo\n"
                 f"sent = Path({str(sent)!r})\n"
                 "messages = []\n"
                 "native = SimpleNamespace(post_message=lambda hwnd, msg, *args: messages.append(msg) or True)\n"
                 "api = Win32DesktopApi(native=native, configure_dpi_awareness=False)\n"
                 "info = WindowInfo(123, 'fixture', 0, 0, 100, 100)\n"
                 "api._require_unchanged = lambda _: info\n"
                 "api.post_text(info, 'A', wait=lambda _: None)\n"
                 "api.post_key(info, 'ENTER', wait=lambda _: None)\n"
                 "api.post_scroll(info, 10, 10, -120)\n"
                 "sent.write_text(str(messages))\n"
                 "session.emit_log('业务已确认')\n"
                 "session.wait_for_stop(10)")
        self.assertTrue(passed, report)
        self.assertEqual([run['status'] for run in report['runs']], ['passed', 'passed'])
        self.assertEqual(json.loads(sent.read_text()), [0x102, 0x100, 0x101, 0x20A])

    def test_missing_host_records_fail_without_waiting_for_agent_completion(self):
        self.relay.timeout.disconnect(self.forward)
        passed, report = self.run_case("session.emit_hint([(350, 350)], 'click', '提示')\nsession.wait_for_stop(10)", timeout=.4)
        self.assertFalse(passed)
        self.assertEqual(len(report['runs']), 1)
        self.assertIn('未收到宿主提示清除记录', report['errors'])
        self.assertEqual(report['status'], 'failed')
