import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from huanzhizhi.agents.runtime import AgentTaskError
from huanzhizhi.skill_creation import CreationWorker
from huanzhizhi.skill_intake import load_flow, validate_report, development_brief
from tests.skill_factory import write_skill_manifest


REQUEST = '新增一个桌宠投篮玩法'
CONFIRMATION = '就按拖球瞄准、松手投篮的方案做，保留桌面悬浮风格。'


def report(status='ready'):
    return dict(version=1, status=status, types=['interactive'], summary='桌宠投篮',
                user_requirements=[dict(text='新增桌宠投篮', user_turn=1, quote=REQUEST)],
                references=[dict(path='src/huanzhizhi/task_sdk.py', finding='已有角色接管接口')],
                plan=['拖球瞄准，松手投篮，角色平滑出手。'],
                acceptance=['连续投篮并检查操作、合成画面和停止。'], unverified=['尚未实机验证'],
                questions=[] if status == 'ready' else [dict(id='play', text='建议拖球瞄准、松手投篮，保留桌面悬浮风格，是否采用？')],
                resolved_questions=[], message='需求已明确。' if status == 'ready' else '建议拖球瞄准、松手投篮，保留桌面悬浮风格，是否采用？')


class IntakeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = Path(__file__).resolve().parents[1]
        self.settings = patch.object(CreationWorker, 'read_ai_settings')
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def worker(self, goal=REQUEST, previous=None):
        worker = CreationWorker(None, goal, tasks_root=self.root / 'tasks',
                                directory=previous.directory if previous else None,
                                thread_id=previous.thread_id if previous else None)
        worker.messages, worker.failures = [], []
        worker.observed.connect(worker.messages.append)
        worker.failed.connect(worker.failures.append)
        return worker

    def test_questions_wait_then_new_development_session_publishes(self):
        commands, prompts = [], []
        first = self.worker()

        def backend(worker, command, turn, **kwargs):
            commands.append(command)
            prompts.append((turn / 'prompt.txt').read_text(encoding='utf-8'))
            if worker.creation_flow['phase'] == 'intake':
                value = report('needs_input' if worker.goal == REQUEST else 'ready')
                if worker.goal != REQUEST:
                    value['resolved_questions'] = [dict(id='play', user_turn=2, quote=CONFIRMATION)]
                (turn / 'intake.json').write_text(json.dumps(value), encoding='utf-8')
                worker.thread_id = 'intake-thread'
            else:
                worker.thread_id = 'development-thread'
                entry = worker.directory / 'output/投篮验证/task.py'
                entry.parent.mkdir(parents=True)
                entry.write_text('# verified test fixture', encoding='utf-8')
                write_skill_manifest(entry, task_id='投篮验证')
            return 0

        with patch('huanzhizhi.skill_creation.ai_codex.check_codex_configuration', return_value='codex'), \
                patch('huanzhizhi.skill_creation.run_agent', side_effect=backend), \
                patch('huanzhizhi.skill_creation.ai_codex.result', return_value='已完成本轮。'):
            first.run()
            self.assertFalse(first.failures)
            self.assertEqual(len(commands), 1)
            self.assertEqual(first.messages, [report('needs_input')['message']])
            self.assertFalse(first.test_tasks_root.exists())
            self.assertFalse((first.directory / 'output').exists())
            second = self.worker(CONFIRMATION, first)
            second.run()
        self.assertFalse(second.failures)
        self.assertEqual(len(commands), 3)
        self.assertNotIn('resume', commands[0])
        self.assertEqual(commands[1][commands[1].index('resume')+1], 'intake-thread')
        self.assertNotIn('resume', commands[2])
        self.assertIn(REQUEST, prompts[2])
        self.assertIn(CONFIRMATION, prompts[2])
        self.assertIn('角色与场景合成后的实际画面', prompts[2])
        self.assertTrue((second.test_tasks_root / '投篮验证/task.py').exists())
        saved = load_flow(second.directory)
        self.assertEqual(saved['phase'], 'development')
        self.assertEqual(saved['intake_thread_id'], 'intake-thread')
        self.assertEqual(saved['development_thread_id'], 'development-thread')

    def test_clear_request_hands_off_without_extra_confirmation(self):
        request = '将指定文件夹的 CSV 合并为新文件，保留源文件，列名不同时报错。'
        worker = self.worker(request)
        def investigate(current, turn, prompt):
            value = report()
            value['types'] = ['data']
            value['user_requirements'] = [dict(text=request, user_turn=1, quote=request)]
            (turn / 'intake.json').write_text(json.dumps(value), encoding='utf-8')
            current.thread_id = 'intake-thread'
            return '调查完成'
        def development(current):
            self.assertIsNone(current.thread_id)
            current.thread_id = 'development-thread'
            return '开发中'
        with patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                patch('huanzhizhi.skill_creation.develop', side_effect=development) as develop:
            worker.run()
            develop.assert_called_once()
        resumed = self.worker('继续', worker)
        with patch('huanzhizhi.skill_creation.run_prompt') as intake, \
                patch('huanzhizhi.skill_creation.develop', return_value='完成'):
            resumed.run()
            intake.assert_not_called()
        self.assertEqual(resumed.thread_id, 'development-thread')

    def test_opencode_also_starts_fresh_development_session(self):
        worker = self.worker()
        worker.agent_tool = 'opencode'
        worker.opencode_options['model'] = 'opencode/test-free'
        commands = []
        def backend(current, command, turn, **kwargs):
            commands.append(command)
            if current.creation_flow['phase'] == 'intake':
                (turn / 'intake.json').write_text(json.dumps(report()), encoding='utf-8')
                current.thread_id = 'ses_intake'
            else:
                current.thread_id = 'ses_development'
            return 0
        with patch('huanzhizhi.skill_creation.ai_opencode.check_configuration', return_value='opencode'), \
                patch('huanzhizhi.skill_creation.ai_opencode.require_free_model'), \
                patch('huanzhizhi.skill_creation.ai_opencode.result', return_value='完成本轮'), \
                patch('huanzhizhi.skill_creation.run_agent', side_effect=backend):
            worker.run()
        self.assertFalse(worker.failures)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all('--session' not in command for command in commands))
        self.assertEqual(worker.thread_id, 'ses_development')

    def test_mixed_types_receive_only_their_special_requirements(self):
        value = report()
        value['types'] = ['automation', 'data']
        brief = development_brief(dict(report=value, messages=[dict(role='user', turn=1, text=REQUEST)]))
        self.assertIn('应用自动化：', brief)
        self.assertIn('文件与数据处理：', brief)
        self.assertNotIn('互动玩法：', brief)
        self.assertNotIn('信息与提醒：', brief)
        self.assertIn(REQUEST, brief)

    def test_missing_or_malformed_report_never_develops_or_publishes(self):
        for content in (None, '{bad json', '{}'):
            with self.subTest(content=content):
                worker = self.worker()
                def investigate(current, turn, prompt):
                    if content is not None:
                        (turn / 'intake.json').write_text(content, encoding='utf-8')
                    return '准备好了'
                with patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                        patch('huanzhizhi.skill_creation.develop') as develop:
                    worker.run()
                develop.assert_not_called()
                self.assertTrue(worker.failures)
                self.assertEqual(load_flow(worker.directory)['phase'], 'intake')
                self.assertFalse(worker.test_tasks_root.exists())

    def test_pending_question_cannot_be_cleared_by_flag_or_old_or_fabricated_reply(self):
        state = dict(messages=[dict(role='user', turn=1, text=REQUEST),
                               dict(role='user', turn=2, text='继续调查')],
                     pending=[dict(id='play', text='采用吗？', asked_after_turn=1)])
        cases = [[], [dict(id='play', user_turn=1, quote=REQUEST)],
                 [dict(id='play', user_turn=2, quote='我确认方案')]]
        for answers in cases:
            with self.subTest(answers=answers):
                value = report()
                value['resolved_questions'] = answers
                with self.assertRaises(AgentTaskError):
                    validate_report(value, state, self.project)

    def test_partial_answers_keep_remaining_question_pending(self):
        state = dict(messages=[dict(role='user', turn=1, text=REQUEST),
                               dict(role='user', turn=2, text=CONFIRMATION)],
                     pending=[dict(id='play', text='采用吗？', asked_after_turn=1),
                              dict(id='style', text='画面？', asked_after_turn=1)])
        value = report('needs_input')
        value['resolved_questions'] = [dict(id='play', user_turn=2, quote=CONFIRMATION)]
        value['questions'] = [dict(id='style', text='画面？')]
        self.assertEqual(validate_report(value, state, self.project), [state['pending'][1]])

    def test_invalid_category_reference_and_empty_acceptance_are_rejected(self):
        state = dict(messages=[dict(role='user', turn=1, text=REQUEST)], pending=[])
        for change in ({'types': ['unknown']}, {'acceptance': []},
                       {'references': [dict(path='missing-file.py', finding='猜测')]},
                       {'user_requirements': []}):
            with self.subTest(change=change), self.assertRaises(AgentTaskError):
                validate_report(report() | change, state, self.project)

    def test_stop_after_investigation_does_not_handoff(self):
        worker = self.worker()
        cancelled = False
        def investigate(current, turn, prompt):
            nonlocal cancelled
            (turn / 'intake.json').write_text(json.dumps(report()), encoding='utf-8')
            current.thread_id = 'intake-thread'
            cancelled = True
            return '调查完成'
        with patch.object(worker, 'isInterruptionRequested', side_effect=lambda: cancelled), \
                patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                patch('huanzhizhi.skill_creation.develop') as develop:
            worker.run()
        develop.assert_not_called()
        self.assertEqual(load_flow(worker.directory)['phase'], 'intake')
        self.assertFalse(worker.messages)

    def test_failed_development_launch_retains_handoff_and_retries_new_session(self):
        worker = self.worker()
        def investigate(current, turn, prompt):
            (turn / 'intake.json').write_text(json.dumps(report()), encoding='utf-8')
            current.thread_id = 'intake-thread'
            return '调查完成'
        with patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                patch('huanzhizhi.skill_creation.develop', side_effect=RuntimeError('launch failed')):
            worker.run()
        self.assertEqual(load_flow(worker.directory)['phase'], 'development')
        self.assertIsNone(worker.thread_id)
        resumed = self.worker('重试', worker)
        with patch('huanzhizhi.skill_creation.run_prompt') as intake, \
                patch('huanzhizhi.skill_creation.develop', return_value='继续开发'):
            resumed.run()
        intake.assert_not_called()
        self.assertFalse(resumed.failures)


if __name__ == '__main__':
    unittest.main()
