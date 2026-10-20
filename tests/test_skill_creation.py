import json
from pathlib import Path
from tests.skill_factory import write_skill_manifest
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

from huanzhizhi.platform.target_window import WindowInfo
from huanzhizhi.python_runtime import python_executable
from huanzhizhi.skill_creation import CreationWorker, develop, _skill_files


class EditSkillTests(unittest.TestCase):
    def setUp(self):
        settings = patch.object(CreationWorker, 'read_ai_settings')
        settings.start()
        self.addCleanup(settings.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'tasks/legacy_skill'
        self.source.mkdir(parents=True)
        self.original = ('from huanzhizhi.task_sdk import describe, TaskSession\n'
                         'if not describe(__file__):\n'
                         '    TaskSession(__file__).emit_finished("完成", "原功能", "保留功能")\n')
        (self.source/'task.py').write_text(self.original, encoding='utf-8')
        write_skill_manifest(self.source/'task.py', name='已有技能')
        (self.source/'asset.txt').write_text('existing asset', encoding='utf-8')
        self.worker = CreationWorker(None, '修改完成提示', tasks_root=self.source.parent, source_skill=self.source)
        self.errors, self.saved, self.observed = [], [], []
        self.logs = []
        self.worker.log_message.connect(self.logs.append)
        self.worker.failed.connect(self.errors.append)
        self.worker.saved.connect(self.saved.append)
        self.worker.observed.connect(self.observed.append)

    def complete(self, worker):
        working = worker.directory / 'working' / self.source.name
        self.assertEqual((working/'asset.txt').read_text(), 'existing asset')
        output = worker.directory / 'output' / self.source.name
        shutil.copytree(working, output, dirs_exist_ok=True)
        (output/'task.py').write_text(self.original.replace('原功能', '修改成功'), encoding='utf-8')
        worker.thread_id = 'edit-thread'
        return '已完成修改。'

    def assert_original(self):
        self.assertEqual((self.source/'task.py').read_text(encoding='utf-8'), self.original)
        self.assertFalse(self.saved)

    def test_edit_preserves_identity_assets_and_backup_and_runs_modified_entry(self):
        neighbor = self.source.parent/'other'
        neighbor.mkdir()
        (neighbor/'keep.txt').write_text('unchanged')
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.saved, [str(self.source.resolve())])
        self.assertIn('修改成功', (self.source/'task.py').read_text(encoding='utf-8'))
        self.assertEqual(_skill_files(self.worker.directory/'backup/legacy_skill'),
                         json.loads((self.worker.directory/'edit.json').read_text(encoding='utf-8'))['files'])
        self.assertFalse(self.worker.test_tasks_root.exists())
        self.assertEqual((self.source/'asset.txt').read_text(), 'existing asset')
        self.assertEqual((neighbor/'keep.txt').read_text(), 'unchanged')
        result = subprocess.run(
            [str(python_executable()), '-I', '-X', 'utf8', '-m', 'huanzhizhi.script_runner', str(Path(self.saved[0])/'task.py'), '--run'],
            input=json.dumps(dict(protocol_version=1, type='start', task_id='legacy_skill', options={}))+'\n',
            text=True, encoding='utf-8', capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('修改成功', result.stdout)

    def test_unfinished_edit_continues_from_same_copy_without_publishing_original(self):
        def unfinished(worker):
            (worker.directory/'working/legacy_skill/notes.txt').write_text('pending')
            worker.thread_id = 'edit-thread'
            return '请说明新提示内容。'
        with patch('huanzhizhi.skill_creation.develop', side_effect=unfinished):
            self.worker.run()
        self.assert_original()
        self.assertEqual(self.observed, ['请说明新提示内容。'])
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual((Path(self.saved[0])/'notes.txt').read_text(), 'pending')

    def test_edit_preserves_original_runtime_and_backup_without_copying_draft_runtime(self):
        runtime = self.source / '.runtime'
        (runtime / 'state').mkdir(parents=True)
        (runtime / 'state/checkpoint.json').write_bytes(b'original state')
        (runtime / 'runs').mkdir()
        (runtime / 'runs/frame.png').write_bytes(b'original evidence')

        def complete(worker):
            working = worker.directory / 'working' / self.source.name
            self.assertFalse((working / '.runtime').exists())
            result = self.complete(worker)
            draft = worker.directory / 'output' / self.source.name / '.runtime'
            (draft / 'state').mkdir(parents=True)
            (draft / 'state/checkpoint.json').write_bytes(b'draft state')
            (draft / 'draft-only.txt').write_text('discard on publication')
            return result

        with patch('huanzhizhi.skill_creation.develop', side_effect=complete):
            self.worker.run()
        self.assertEqual(self.errors, [], self.logs)
        self.assertTrue(self.saved)
        self.assertEqual((runtime / 'state/checkpoint.json').read_bytes(), b'original state')
        self.assertEqual((runtime / 'runs/frame.png').read_bytes(), b'original evidence')
        self.assertFalse((runtime / 'draft-only.txt').exists())
        backup = self.worker.directory / 'backup' / self.source.name / '.runtime'
        self.assertEqual(_skill_files(runtime), _skill_files(backup))
        self.assertEqual((self.worker.directory / 'output' / self.source.name /
                          '.runtime/state/checkpoint.json').read_bytes(), b'draft state')

    def test_runtime_copy_failure_leaves_original_untouched(self):
        runtime = self.source / '.runtime'
        runtime.mkdir()
        (runtime / 'state.json').write_bytes(b'original state')
        original_copy = shutil.copytree

        def copy(source, destination, *args, **kwargs):
            if Path(source) == runtime:
                raise OSError('runtime copy failed')
            return original_copy(source, destination, *args, **kwargs)

        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete), \
                patch('shutil.copytree', side_effect=copy):
            self.worker.run()
        self.assert_original()
        self.assertIn('runtime copy failed', self.logs[-1])
        self.assertEqual((runtime / 'state.json').read_bytes(), b'original state')

    def test_invalid_output_does_not_replace_original(self):
        def invalid(worker):
            result = self.complete(worker)
            (worker.directory/'output/legacy_skill/skill.json').write_text('invalid metadata')
            return result
        with patch('huanzhizhi.skill_creation.develop', side_effect=invalid):
            self.worker.run()
        self.assertTrue(self.errors)
        self.assert_original()

    def test_missing_manifest_or_broken_entry_never_replaces_original(self):
        def missing(worker):
            self.complete(worker)
            (worker.directory/'output/legacy_skill/skill.json').unlink()
            return 'Missing manifest'
        with patch('huanzhizhi.skill_creation.develop', side_effect=missing):
            self.worker.run()
        self.assertTrue(self.errors)
        self.assert_original()
        self.errors.clear()
        def syntax_error(worker):
            self.complete(worker)
            (worker.directory/'output/legacy_skill/task.py').write_text('def broken(:')
            return 'Invalid Python'
        with patch('huanzhizhi.skill_creation.develop', side_effect=syntax_error):
            self.worker.run()
        self.assertTrue(self.errors)
        self.assert_original()

    def test_publication_does_not_execute_the_generated_entry(self):
        marker = self.root/'entry-executed'
        def complete(worker):
            result = self.complete(worker)
            entry = worker.directory/'output/legacy_skill/task.py'
            entry.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
                             + self.original, encoding='utf-8')
            return result
        with patch('huanzhizhi.skill_creation.develop', side_effect=complete):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertTrue(self.saved)
        self.assertFalse(marker.exists())

    def test_formal_edit_preserves_existing_test(self):
        destination = self.root/'test_tasks/legacy_skill'
        destination.mkdir(parents=True)
        (destination/'keep.txt').write_text('existing test')
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.saved, [str(self.source.resolve())])
        self.assertIn('修改成功', (self.source/'task.py').read_text(encoding='utf-8'))
        self.assertEqual((destination/'keep.txt').read_text(), 'existing test')

    def test_edit_test_updates_only_test_and_backs_up_previous(self):
        formal = self.source
        destination = self.root/'test_tasks/legacy_skill'
        shutil.copytree(formal, destination)
        self.source = destination
        self.worker.source_skill = destination.resolve()
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual((formal/'task.py').read_text(encoding='utf-8'), self.original)
        self.assertEqual(self.saved, [str(destination.resolve())])
        self.assertIn('修改成功', (destination/'task.py').read_text(encoding='utf-8'))
        self.assertEqual((self.worker.directory/'backup/legacy_skill/task.py').read_text(encoding='utf-8'), self.original)

    def test_renamed_output_does_not_replace_original(self):
        def renamed(worker):
            result = self.complete(worker)
            entry = worker.directory/'output/legacy_skill/task.py'
            write_skill_manifest(entry, name='新名称')
            return result
        with patch('huanzhizhi.skill_creation.develop', side_effect=renamed):
            self.worker.run()
        self.assertIn('保留原 ID 和名称', self.logs[-1])
        self.assert_original()

    def test_external_change_is_not_overwritten(self):
        def external_change(worker):
            result = self.complete(worker)
            (self.source/'asset.txt').write_text('external change')
            return result
        with patch('huanzhizhi.skill_creation.develop', side_effect=external_change):
            self.worker.run()
        self.assertIn('发生变化', self.logs[-1])
        self.assert_original()
        self.assertEqual((self.source/'asset.txt').read_text(), 'external change')

    def test_stop_during_final_comparison_does_not_publish(self):
        cancelled, calls = False, 0
        def compare(directory):
            nonlocal cancelled, calls
            result = _skill_files(directory)
            if directory.resolve() == self.source.resolve():
                calls += 1
                cancelled = calls == 2
            return result
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete), \
                patch('huanzhizhi.skill_creation._skill_files', side_effect=compare), \
                patch.object(self.worker, 'isInterruptionRequested', side_effect=lambda: cancelled):
            self.worker.run()
        self.assert_original()
        self.assertFalse(self.errors)

    def test_failed_replacement_restores_original_and_can_retry(self):
        runtime = self.source / '.runtime'
        runtime.mkdir()
        (runtime / 'state.json').write_bytes(b'original state')
        real_rename = Path.rename
        def rename(path, target):
            if path.name.startswith('publish_'):
                raise PermissionError('simulated replacement failure')
            return real_rename(path, target)
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.complete), patch.object(Path, 'rename', rename):
            self.worker.run()
        self.assert_original()
        self.assertEqual((runtime / 'state.json').read_bytes(), b'original state')
        self.assertIn('simulated replacement failure', self.logs[-1])
        self.assertNotIn('simulated replacement failure', self.errors[-1])
        self.errors.clear()
        with patch('huanzhizhi.skill_creation.develop', return_value='重试保存'):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.saved, [str(self.source.resolve())])
        self.assertEqual((runtime / 'state.json').read_bytes(), b'original state')


class CreationTests(unittest.TestCase):
    def setUp(self):
        # These tests cover development/publication; intake has its own integration tests.
        intake = patch.object(CreationWorker, '_prepare_creation', return_value=True)
        intake.start()
        self.addCleanup(intake.stop)
        settings = patch.object(CreationWorker, 'read_ai_settings')
        settings.start()
        self.addCleanup(settings.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = CreationWorker(WindowInfo(123, 'Test window', 0, 0, 800, 600),
                                     'Create a deterministic local task', tasks_root=self.root / 'tasks')
        self.errors, self.saved, self.results = [], [], []
        self.logs = []
        self.worker.log_message.connect(self.logs.append)
        self.worker.failed.connect(self.errors.append)
        self.worker.saved.connect(self.saved.append)
        self.worker.observed.connect(self.results.append)

    def prepare_task(self, worker):
        task_id = '确定性测试技能'
        output = worker.directory / 'output' / task_id
        output.mkdir(parents=True, exist_ok=True)
        (output / 'task.py').write_text(
            'from huanzhizhi.task_sdk import describe, TaskSession\n'
            'if not describe(__file__):\n'
            '    session = TaskSession(__file__)\n'
            '    session.emit_finished("完成", "Deterministic task", "Local test completed")\n',
            encoding='utf-8')
        write_skill_manifest(output / 'task.py', task_id=task_id)
        worker.thread_id = 'test-thread'
        return 'Local deterministic test; no game tested.'

    def test_publish_name_with_spaces_and_parentheses(self):
        def develop(worker):
            result = self.prepare_task(worker)
            source = worker.directory / 'output' / '确定性测试技能'
            destination = source.with_name('整理 文件（测试）')
            source.rename(destination)
            write_skill_manifest(destination / 'task.py', task_id=destination.name)
            return result
        with patch('huanzhizhi.skill_creation.develop', side_effect=develop):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(Path(self.saved[0]).name, '整理 文件（测试）')

    def test_publish_ordinary_task_and_run_through_existing_sdk(self):
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.prepare_task):
            self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.saved), 1)
        task = Path(self.saved[0]) / 'task.py'
        self.assertEqual(task.parent.name, '确定性测试技能')
        self.assertEqual(task.parent.parent, self.worker.test_tasks_root)
        self.assertFalse(self.worker.tasks_root.exists())
        result = subprocess.run(
            [str(python_executable()), '-I', '-X', 'utf8', '-m', 'huanzhizhi.script_runner', str(task), '--run'],
            input=json.dumps(dict(protocol_version=1, type='start', task_id=task.parent.name, options={}))+'\n',
            text=True, encoding='utf-8', capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['type'], 'finished')
        self.assertFalse((task.parent / 'flow.json').exists())
        original = task.read_bytes()
        with patch('huanzhizhi.skill_creation.develop') as model:
            self.worker.run()
        model.assert_not_called()
        self.assertEqual(task.read_bytes(), original)
        resumed = CreationWorker(self.worker.target, 'Follow up', tasks_root=self.worker.tasks_root,
                                 directory=self.worker.directory, thread_id=self.worker.thread_id)
        with patch('huanzhizhi.skill_creation.develop') as model:
            resumed.run()
        model.assert_not_called()

    def test_new_skill_does_not_publish_draft_runtime(self):
        def prepare(worker):
            result = self.prepare_task(worker)
            runtime = worker.directory / 'output/确定性测试技能/.runtime'
            runtime.mkdir()
            (runtime / 'private.png').write_bytes(b'draft evidence')
            return result

        with patch('huanzhizhi.skill_creation.develop', side_effect=prepare):
            self.worker.run()
        self.assertEqual(self.errors, [], self.logs)
        self.assertFalse((Path(self.saved[0]) / '.runtime').exists())
        self.assertTrue((self.worker.directory / 'output/确定性测试技能/.runtime/private.png').is_file())

    def test_existing_chinese_skill_is_preserved_and_draft_can_be_renamed(self):
        existing = self.worker.tasks_root / '确定性测试技能' / 'task.py'
        existing.parent.mkdir(parents=True)
        existing.write_text('# existing skill', encoding='utf-8')
        with patch('huanzhizhi.skill_creation.develop', side_effect=self.prepare_task):
            self.worker.run()
        self.assertIn('同名', self.logs[-1])
        self.assertFalse(self.saved)
        self.assertEqual(existing.read_text(encoding='utf-8'), '# existing skill')
        draft = self.worker.directory / 'output' / '确定性测试技能'
        entry = draft / 'task.py'
        write_skill_manifest(entry, task_id='另一个测试技能')
        draft.rename(draft.with_name('另一个测试技能'))
        with patch('huanzhizhi.skill_creation.develop', return_value='Renamed'):
            self.worker.run()
        self.assertEqual(Path(self.saved[0]).name, '另一个测试技能')

    def test_name_must_match_directory_and_be_chinese(self):
        for task_id, name in (('中文技能', '另一个名称'), ('english_name', 'english_name')):
            with self.subTest(task_id=task_id):
                worker = CreationWorker(self.worker.target, 'Test', tasks_root=self.root / task_id / 'tasks')
                errors, saved = [], []
                worker.failed.connect(errors.append)
                worker.log_message.connect(self.logs.append)
                worker.saved.connect(saved.append)
                def prepare(current):
                    entry = current.directory / 'output' / task_id / 'task.py'
                    entry.parent.mkdir(parents=True)
                    entry.write_text('from huanzhizhi.task_sdk import describe\ndescribe(__file__)', encoding='utf-8')
                    write_skill_manifest(entry, task_id=task_id, name=name)
                    return 'Done'
                with patch('huanzhizhi.skill_creation.develop', side_effect=prepare):
                    worker.run()
                self.assertIn('中文名称', self.logs[-1])
                self.assertTrue(errors)
                self.assertFalse(saved)

    def test_incomplete_and_invalid_drafts_are_not_published(self):
        def incomplete(worker):
            worker.thread_id = 'test-thread'
            (worker.directory / 'draft.py').write_text('# unfinished', encoding='utf-8')
            return 'Need one clarification.'
        with patch('huanzhizhi.skill_creation.develop', side_effect=incomplete):
            self.worker.run()
        self.assertFalse(self.worker.tasks_root.exists())
        self.assertEqual(self.results, ['Need one clarification.'])
        self.prepare_task(self.worker)
        entry = self.worker.directory / 'output' / '确定性测试技能' / 'task.py'
        entry.with_name('skill.json').write_text('invalid metadata', encoding='utf-8')
        with patch('huanzhizhi.skill_creation.develop', return_value='Done'):
            self.worker.run()
        self.assertTrue(self.errors)
        self.assertFalse(self.saved)
        self.assertTrue(entry.exists())

    def test_stop_after_development_does_not_publish(self):
        self.cancelled = False
        def stopped(worker):
            result = self.prepare_task(worker)
            self.cancelled = True
            return result
        with patch.object(self.worker, 'isInterruptionRequested', side_effect=lambda: self.cancelled), \
                patch('huanzhizhi.skill_creation.develop', side_effect=stopped):
            self.worker.run()
        self.assertFalse(self.saved)
        self.assertFalse(self.results)
        self.assertFalse(self.worker.tasks_root.exists())

    def cli_stub(self, command, **kwargs):
        self.commands.append(command)
        answer = command[command.index('--output-last-message')+1]
        code = ('import sys,json,time\n'
                'sys.stdin.read()\n'
                'print(json.dumps({"type":"thread.started","thread_id":"real-event-id"}), flush=True)\n'
                + self.stub_body.format(answer=repr(answer)))
        process = self.real_popen([str(python_executable()), '-I', '-c', code], **kwargs)
        self.processes.append(process)
        return process

    def prepare_cli(self, body):
        self.worker.directory = self.root / 'draft'
        self.worker.directory.mkdir()
        self.commands, self.processes = [], []
        self.real_popen = subprocess.Popen
        self.stub_body = body

    def test_cli_events_result_and_resume_use_same_working_directory(self):
        self.prepare_cli('open({answer},"w",encoding="utf-8").write("Need details")\n')
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub):
            self.assertEqual(develop(self.worker), 'Need details')
            self.assertEqual(self.worker.thread_id, 'real-event-id')
            self.worker.goal = 'Follow-up details'
            develop(self.worker)
        self.assertNotIn('resume', self.commands[0])
        self.assertIn('resume', self.commands[1])
        self.assertIn('real-event-id', self.commands[1])
        for command in self.commands:
            self.assertEqual(command[command.index('--sandbox')+1], 'danger-full-access')
            self.assertIn('approval_policy="never"', command)
            self.assertFalse(any(part.startswith('mcp_servers.') for part in command))
        self.assertIn('--ignore-user-config', self.commands[0])
        self.assertIn('approval_policy="never"', self.commands[0])
        for feature in ('shell_tool', 'unified_exec', 'code_mode'):
            self.assertNotIn(feature, self.commands[0])
        self.assertNotIn('--ephemeral', self.commands[0])
        self.assertNotIn('--output-schema', self.commands[0])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', self.commands[0])
        prompts = list(self.worker.directory.glob('turn_*/prompt.txt'))
        self.assertEqual(len(prompts), 2)
        for path in prompts:
            prompt = path.read_text(encoding='utf-8')
            self.assertIn(str(self.worker.directory.resolve()), prompt)
            self.assertIn('开发文件的创建、修改、删除和移动仅限此目录', prompt)

    def test_prompt_distinguishes_windowless_creation_editing_and_selected_window(self):
        self.prepare_cli('')
        original_target = self.worker.target
        for target, source, expected in (
            (None, None, '无需窗口，直接创建'),
            (None, self.root / 'tasks' / '桌宠排球', '先查看原技能是否依赖目标窗口'),
            (original_target, None, original_target.label),
        ):
            with self.subTest(target=target, source=source):
                self.worker.target, self.worker.source_skill = target, source
                with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                        patch('huanzhizhi.skill_creation.run_agent', return_value=0), \
                        patch('huanzhizhi.agents.codex.adapter.result', return_value='Done'):
                    develop(self.worker)
                prompt = sorted(self.worker.directory.glob('turn_*/prompt.txt'))[-1].read_text(encoding='utf-8')
                self.assertIn('所选窗口：'+expected, prompt) if source is None else self.assertIn(expected, prompt)
                if target is None and source is None:
                    self.assertNotIn('先查看原技能', prompt)
                    self.assertIn('不把选择应用窗口作为创建或验证的前提', prompt)

    def test_cli_failure_preserves_thread_and_error(self):
        self.prepare_cli('sys.stderr.write("policy denied")\nsys.exit(1)\n')
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub):
            with self.assertRaisesRegex(RuntimeError, '无法确认'):
                develop(self.worker)
        self.assertEqual(self.worker.thread_id, 'real-event-id')
        self.assertIn('policy denied', next(self.worker.directory.glob('turn_*/stderr.txt')).read_text())

    def test_codex_failed_event_reaches_chat_signal_and_prevents_publication(self):
        self.prepare_cli(
            'print(json.dumps(dict(type="turn.failed", error=dict(message="Quota exceeded. Check your plan and billing details."))), flush=True)\n'
            'open({answer},"w",encoding="utf-8").write("Partial answer")\n')
        self.prepare_task(self.worker)
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub):
            self.worker.run()
        self.assertEqual(self.errors, ['Codex 可用额度不足，请检查账户额度和账单设置后重试。'])
        self.assertFalse(self.saved)
        self.assertFalse(self.results)
        self.assertFalse(self.worker.test_tasks_root.exists())
        self.assertIn('退出码 0', self.logs[-1])

    def test_unknown_failure_keeps_diagnostics_out_of_user_message(self):
        with patch('huanzhizhi.skill_creation.develop', side_effect=RuntimeError('private internal path')):
            self.worker.run()
        self.assertIn('无法确认', self.errors[-1])
        self.assertNotIn('private internal path', self.errors[-1])
        self.assertNotIn('草稿', self.errors[-1])
        self.assertEqual(self.logs[-1], 'private internal path')

    def test_child_click_arrives_before_analysis_exits_without_stdout_point(self):
        acknowledgement = self.root / 'click_received'
        self.prepare_cli(
            'import os\n'
            'from pathlib import Path\n'
            'child="from unittest.mock import Mock\\n"'
            '+"from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo\\n"'
            '+"native=Mock(); native.post_message.return_value=True\\n"'
            '+"api=Win32DesktopApi(native=native, configure_dpi_awareness=False)\\n"'
            '+"window=WindowInfo(123, \'fixture\', -200, 100, 320, 200)\\n"'
            '+"api._require_unchanged=Mock(return_value=window)\\n"'
            '+"api.post_click(window, 30, 40)\\n"\n'
            'import subprocess\n'
            'result=subprocess.run([sys.executable,"-I","-c",child],capture_output=True)\n'
            'assert result.returncode == 0, result.stderr\n'
            'assert not result.stdout, result.stdout\n'
            f'ack=Path({str(acknowledgement)!r})\n'
            'deadline=time.monotonic()+3\n'
            'while not ack.exists() and time.monotonic()<deadline: time.sleep(.02)\n'
            'assert ack.exists(), "click not delivered live"\n'
            'open({answer},"w",encoding="utf-8").write("Done")\n')
        received = []
        def receive(x, y):
            received.append((x, y, self.processes[0].poll()))
            acknowledgement.write_text('received')
        self.worker.click_point.connect(receive)
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub):
            self.assertEqual(develop(self.worker), 'Done')
        self.assertEqual(received, [(-170, 140, None)])
        self.assertFalse(list(self.worker.directory.glob('turn_*/clicks.jsonl')))
        self.assertEqual(len(list(self.worker.directory.glob('turn_*/clicks.finished.jsonl'))), 1)
        self.assertTrue(any(part.startswith('shell_environment_policy.set=') for part in self.commands[0]))

    def test_progress_arrives_before_process_exit_and_handles_partial_utf8(self):
        acknowledgement = self.root / 'progress_received'
        self.prepare_cli(
            'from pathlib import Path\n'
            'event=json.dumps(dict(type="item.completed", item=dict(type="agent_message", '
            'text="进度：正在查看扫雷棋盘。")),ensure_ascii=False).encode("utf-8")\n'
            'cut=event.index("进".encode("utf-8"))+1\n'
            'sys.stdout.buffer.write(event[:cut]);sys.stdout.buffer.flush()\n'
            'time.sleep(.3)\n'
            'sys.stdout.buffer.write(event[cut:]+b"\\n");sys.stdout.buffer.flush()\n'
            f'ack=Path({str(acknowledgement)!r})\n'
            'deadline=time.monotonic()+3\n'
            'while not ack.exists() and time.monotonic()<deadline: time.sleep(.02)\n'
            'if not ack.exists(): sys.exit(2)\n'
            'open({answer},"w",encoding="utf-8").write("Need details")\n')
        received = []
        def receive(text):
            received.append((text, self.processes[0].poll()))
            acknowledgement.write_text('received')
        self.worker.progress.connect(receive)
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub):
            self.assertEqual(develop(self.worker), 'Need details')
        self.assertEqual(received, [('正在查看扫雷棋盘。', None)])

    def test_progress_ignores_tool_output_final_report_and_consecutive_duplicates(self):
        self.prepare_cli(
            'print("invalid json",flush=True)\n'
            'for kind,text in [("agent_message","开发报告：E:/draft/task.py"),'
            '("command_execution","进度：不要展示工具输出"),'
            '("agent_message","进度：正在检查操作结果。"),'
            '("agent_message","进度：正在检查操作结果。"),'
            '("agent_message","进度：第一行\\n内部日志"),'
            '("agent_message","进度："),'
            '("agent_message","进度："+"长"*121)]:\n'
            ' print(json.dumps(dict(type="item.completed",item=dict(type=kind,text=text))),flush=True)\n'
            'open({answer},"w",encoding="utf-8").write("Done")\n')
        received = []
        self.worker.progress.connect(received.append)
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub):
            develop(self.worker)
        self.assertEqual(received, ['正在检查操作结果。'])

    def test_cancel_terminates_process_and_keeps_thread(self):
        self.prepare_cli('time.sleep(30)\n')
        self.cancelled = False
        original_check = self.worker.check_stop
        def cancel_when_started():
            if self.processes:
                events = list(self.worker.directory.glob('turn_*/events.jsonl'))
                if events and events[0].stat().st_size:
                    self.cancelled = True
            original_check()
        with patch('huanzhizhi.agents.codex.adapter.check_codex_configuration', return_value='codex.exe'), \
                patch('huanzhizhi.agents.runtime.subprocess.Popen', side_effect=self.cli_stub), \
                patch.object(self.worker, 'check_stop', side_effect=cancel_when_started), \
                patch.object(self.worker, 'isInterruptionRequested', side_effect=lambda: self.cancelled):
            def denied_tree_stop(process):
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                    raise RuntimeError('tree stop denied')
            with patch('huanzhizhi.agents.runtime._stop_process', side_effect=denied_tree_stop):
                with self.assertRaisesRegex(RuntimeError, 'tree stop denied'):
                    develop(self.worker)
        self.assertIsNotNone(self.processes[0].poll())
        self.assertEqual(self.worker.thread_id, 'real-event-id')


if __name__ == '__main__':
    unittest.main()
