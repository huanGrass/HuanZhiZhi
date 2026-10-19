import json
from pathlib import Path
import tempfile
import unittest

from huanzhizhi.agents.codex import adapter as ai_codex
from huanzhizhi.agents.runtime import AgentTaskError


class CodexFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.turn = Path(temporary.name)
        self.cases = json.loads((Path(__file__).parent / 'fixtures/codex_failure_events.json')
                                .read_text(encoding='utf-8'))['cases']
        (self.turn / 'stderr.txt').write_text('unrelated warning: secret-token', encoding='utf-8')

    def events(self, events):
        (self.turn / 'events.jsonl').write_text(
            '\n'.join(json.dumps(event, ensure_ascii=False) for event in events), encoding='utf-8')

    def failure(self, code=1):
        with self.assertRaises(AgentTaskError) as caught:
            ai_codex.result(self.turn, code)
        for hidden in ('secret-token', 'test-key', 'http://', '草稿', str(self.turn)):
            self.assertNotIn(hidden, caught.exception.user_message)
        self.assertNotIn('secret-token', str(caught.exception))
        self.assertNotIn('test-key', str(caught.exception))
        return caught.exception.user_message

    def test_captured_cli_failures_have_specific_safe_messages(self):
        expected = {'quota': '额度不足', 'usage_limit': '用量上限',
                    'unauthorized': '身份验证未通过', 'forbidden': '被服务端拒绝',
                    'rate_limit': '请求受到限制', 'context': '超过模型上下文限制',
                    'model': '资源未找到', 'server': '服务端错误',
                    'stream_disconnect': '响应连接在完成前中断'}
        for name, fragment in expected.items():
            with self.subTest(case=name):
                self.events(self.cases[name]['events'])
                message = self.failure(self.cases[name]['exit_code'])
                self.assertIn(fragment, message)
                if name != 'quota':
                    self.assertNotIn('额度不足', message)

    def test_final_failure_takes_priority_over_earlier_error_and_stderr(self):
        self.events([self.cases['quota']['events'][0],
                     self.cases['unauthorized']['events'][-1]])
        (self.turn / 'stderr.txt').write_text('Quota exceeded. Check your plan and billing details.')
        self.assertIn('身份验证未通过', self.failure())

    def test_unknown_terminal_failure_does_not_reuse_previous_cause(self):
        for payload in ({'message': 'Something failed: quota maybe? secret-token'},
                        {'message': None}, 'malformed', None):
            with self.subTest(payload=payload):
                self.events([self.cases['quota']['events'][0], {'type': 'turn.failed', 'error': payload}])
                self.assertIn('无法确认', self.failure())

    def test_error_event_is_used_only_if_process_failed(self):
        self.events([self.cases['quota']['events'][0]])
        self.assertIn('额度不足', self.failure())
        (self.turn / 'answer.txt').write_text('最终答复', encoding='utf-8')
        self.assertEqual(ai_codex.result(self.turn, 0), '最终答复')

    def test_recovered_error_does_not_override_completed_turn(self):
        for event_type in ('error', 'turn.failed'):
            with self.subTest(event_type=event_type):
                event = next(e for e in self.cases['quota']['events'] if e['type'] == event_type)
                self.events([event, {'type': 'turn.completed'}])
                (self.turn / 'answer.txt').write_text('最终答复', encoding='utf-8')
                self.assertEqual(ai_codex.result(self.turn, 0), '最终答复')
                self.assertIn('无法确认', self.failure(1))

    def test_new_turn_does_not_reuse_prior_failure(self):
        self.events([*self.cases['quota']['events'], {'type': 'turn.started'}])
        self.assertIn('无法确认', self.failure())

    def test_failed_event_overrides_zero_exit_and_partial_answer(self):
        self.events(self.cases['quota']['events'])
        (self.turn / 'answer.txt').write_text('未完成的答复', encoding='utf-8')
        self.assertIn('额度不足', self.failure(0))

    def test_invalid_or_missing_events_do_not_guess_from_stderr(self):
        (self.turn / 'stderr.txt').write_text('Quota exceeded. Check your plan and billing details.')
        self.assertIn('无法确认', self.failure())
        (self.turn / 'events.jsonl').write_bytes(b'not json\n[]\nnull\n{"type":"turn.failed"')
        self.assertIn('无法确认', self.failure())

    def test_tool_failures_and_assistant_text_are_not_agent_failures(self):
        self.events([{'type': 'item.completed', 'item': {'type': 'command_execution',
                     'status': 'failed', 'exit_code': 1, 'aggregated_output': 'Quota exceeded.'}},
                     {'type': 'item.completed', 'item': {'type': 'agent_message',
                      'text': 'Quota exceeded. Check your plan and billing details.'}}])
        self.assertIn('无法确认', self.failure())

    def test_missing_or_empty_final_answer_is_reported_without_claiming_cause(self):
        self.events([{'type': 'turn.completed'}])
        self.assertIn('未返回有效答复', self.failure(0))
        (self.turn / 'answer.txt').write_text('\n', encoding='utf-8')
        self.assertIn('未返回有效答复', self.failure(0))
