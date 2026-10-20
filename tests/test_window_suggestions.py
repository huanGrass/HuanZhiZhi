import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from huanzhizhi.agents.runtime import AgentTaskError
from huanzhizhi.agents.codex import adapter as codex
from huanzhizhi.agents.opencode import adapter as opencode
from huanzhizhi.platform.target_window import WindowInfo
from huanzhizhi.skill_creation import CreationWorker
from huanzhizhi.skill_intake import intake_prompt, load_flow, parse_suggestions
from tests.skill_factory import write_skill_manifest


SUGGESTIONS = dict(summary='这是一个棋盘，可创建提示助手。',
                   suggestions=[dict(title='走法提示', brief='推荐走法，由你落子。',
                                     goal='创建走法提示助手，只显示建议，由我落子。')])


class WindowSuggestionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = WindowInfo(123, '棋盘', 0, 0, 800, 600)
        self.worker = CreationWorker(self.target, '', tasks_root=self.root / 'tasks', observe_only=True)
        self.failures, self.suggestions = [], []
        self.worker.failed.connect(self.failures.append)
        self.worker.suggestions_ready.connect(self.suggestions.append)
        settings = patch.object(CreationWorker, 'read_ai_settings')
        settings.start()
        self.addCleanup(settings.stop)

    def test_observation_captures_selected_window_and_retains_context_without_creation(self):
        entry = self.root / 'test_tasks/围棋对弈/task.py'
        entry.parent.mkdir(parents=True)
        entry.write_text('# not executed')
        write_skill_manifest(entry)
        def observe(worker, turn, prompt, *, observation_image):
            self.assertEqual(observation_image, turn / 'window.png')
            self.assertTrue(observation_image.is_file())
            self.assertIn('围棋对弈', prompt)
            self.assertIn('files_present', prompt)
            self.assertIn('不操作应用', prompt)
            worker.thread_id = 'observation-thread'
            return json.dumps(SUGGESTIONS)
        with patch('huanzhizhi.skill_creation.Win32DesktopApi') as api, \
                patch('huanzhizhi.skill_creation.WgcWindowCapture') as capture, \
                patch('huanzhizhi.skill_creation.run_prompt', side_effect=observe), \
                patch('huanzhizhi.skill_creation.develop') as develop:
            api.return_value.refresh.return_value = self.target
            api.return_value.capture_client.return_value = Image.new('RGB', (800, 600))
            self.worker.run()
            api.return_value.capture_client.assert_called_once_with(self.target)
            capture.return_value.close.assert_called_once()
            api.return_value.post_click.assert_not_called()
            develop.assert_not_called()
        self.assertEqual(self.failures, [])
        self.assertEqual(self.suggestions, [SUGGESTIONS])
        self.assertIsNone(self.worker.thread_id)
        self.assertFalse((self.worker.directory / 'output').exists())
        self.assertFalse((self.worker.directory / 'creation_flow.json').exists())
        self.worker.goal = SUGGESTIONS['suggestions'][0]['goal']
        state = load_flow(self.worker.directory)
        state['messages'].append(dict(role='user', turn=1, text=self.worker.goal))
        prompt = intake_prompt(self.worker, state, self.worker.directory / 'intake.json')
        self.assertIn(SUGGESTIONS['summary'], prompt)
        self.assertIn('不是用户要求', prompt)
        self.assertIn(self.worker.goal, prompt)
        from tests.test_skill_intake import report
        selected = CreationWorker(self.target, self.worker.goal, tasks_root=self.worker.tasks_root,
                                  directory=self.worker.directory)
        def investigate(worker, turn, prompt):
            self.assertIn(SUGGESTIONS['summary'], prompt)
            value = report()
            value['user_requirements'] = [dict(text=worker.goal, user_turn=1, quote=worker.goal)]
            (turn / 'intake.json').write_text(json.dumps(value), encoding='utf-8')
            worker.thread_id = 'intake-thread'
            return '需求已明确'
        with patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                patch('huanzhizhi.skill_creation.develop', return_value='开始创建') as develop:
            selected.run()
            develop.assert_called_once_with(selected)
        state = load_flow(selected.directory)
        self.assertEqual(state['phase'], 'development')
        self.assertEqual([m['text'] for m in state['messages'] if m['role'] == 'user'], [selected.goal])

    def test_invalid_suggestions_are_rejected_but_unclear_window_can_return_no_choices(self):
        self.assertEqual(parse_suggestions(json.dumps(dict(summary='画面不清楚，请输入目标。', suggestions=[])))['suggestions'], [])
        for value in ('not JSON', '{}', '[]', json.dumps(SUGGESTIONS | {'suggestions': [{}]}),
                      json.dumps(SUGGESTIONS | {'suggestions': SUGGESTIONS['suggestions'] * 4})):
            with self.subTest(value=value), self.assertRaises(AgentTaskError):
                parse_suggestions(value)

    def test_brief_is_required_and_bounded_separately_from_full_goal(self):
        self.assertEqual(parse_suggestions(json.dumps(SUGGESTIONS)), SUGGESTIONS)
        suggestion = SUGGESTIONS['suggestions'][0]
        for brief in ('', '字' * 33, '一行\n二行', '一行\r二行', None):
            with self.subTest(brief=brief), self.assertRaises(AgentTaskError):
                parse_suggestions(json.dumps(SUGGESTIONS | {'suggestions': [suggestion | {'brief': brief}]}))
        with self.assertRaises(AgentTaskError):
            parse_suggestions(json.dumps(SUGGESTIONS | {
                'suggestions': [dict(title=suggestion['title'], goal=suggestion['goal'])]}))

    def test_codex_observation_attaches_image_without_development_tools_or_session(self):
        image = self.root / 'window.png'
        command = codex.build_command('codex', codex.DEFAULT_CODEX_OPTIONS, 'old-thread',
                                      self.root / 'answer.txt', self.root / 'clicks.jsonl',
                                      observation_image=image)
        self.assertEqual(command[command.index('--image') + 1], str(image.resolve()))
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('resume', command)
        self.assertIn('--ephemeral', command)
        disabled = [command[i+1] for i, arg in enumerate(command[:-1]) if arg == '--disable']
        for tool in ('shell_tool', 'unified_exec', 'code_mode', 'browser_use', 'computer_use',
                     'apps', 'plugins', 'image_generation', 'skill_search'):
            self.assertIn(tool, disabled)
        self.assertFalse(any('HUANZHIZHI_CLICK_EVENTS' in arg for arg in command))

    def test_opencode_observation_attaches_image_and_denies_all_tools(self):
        command = opencode.build_command('opencode', dict(executable='', model='opencode/test-free'),
                                         'ses_previous', observation_image=self.root / 'window.png')
        self.assertEqual(command[command.index('--file') + 1], str((self.root / 'window.png').resolve()))
        self.assertNotIn('--session', command)
        permissions = json.loads(opencode.environment(observe_only=True)['OPENCODE_CONFIG_CONTENT'])['permissions']
        self.assertIn(dict(action='*', resource='*', effect='deny'), permissions)
