import base64
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QCoreApplication

from huanzhizhi.agents.deepseek import adapter
from huanzhizhi.agents.runtime import AgentTaskError
from huanzhizhi.python_runtime import python_executable
from huanzhizhi.settings.component import SettingsComponent
from huanzhizhi.skill_creation import CreationWorker, run_prompt


# A separate SDK peer tests the real duplex transport and shared process owner;
# it records requests and persists sessions across invocations, without a model.
SDK = r'''
import json,os,subprocess,sys,time
from pathlib import Path
def send(value):
    print(json.dumps(value,ensure_ascii=False),flush=True)
def event(session,kind,data):
    send(dict(jsonrpc='2.0',method='session.event',params=dict(sessionId=session,event=dict(type=kind,data=data))))
prompt=None
for line in sys.stdin:
    request=json.loads(line)
    with open('requests.jsonl','a',encoding='utf-8') as f:f.write(line)
    method,params=request['method'],request['params']
    result={}
    if method=='initialize':
        result={'serverInfo':{'name':'deepseek-harness-sdk-runtime'}}
    elif method=='session/resume':
        if not Path(params['sessionId']).is_file():
            send(dict(jsonrpc='2.0',id=request['id'],error=dict(code=-32603,message='Missing session')))
            continue
    elif method=='session/prompt':
        prompt=params
        Path(params['sessionId']).write_text('saved')
        result={'messageId':'queued'}
    elif method=='session/wait':
        session=prompt['sessionId']
        text=prompt['contentBlocks'][0]['text']
        event(session,'turn/start',{})
        if 'hang' in text:
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
            Path('pids.json').write_text(json.dumps([os.getpid(),child.pid]))
            time.sleep(120)
        event('unrelated-child','assistant/message',{'message':{'content':[{'type':'text','text':'wrong result'}]}})
        for _ in range(2):
            event(session,'assistant/message',{'message':{'content':[{'type':'text','text':'进度：正在检查技能。'}]}})
        if 'quota' in text:
            event(session,'turn/end',{'reason':{'kind':'error','error':{'code':'ACCOUNT_QUOTA','message':'private token'}}})
        else:
            event(session,'assistant/message',{'message':{'content':[{'type':'text','text':'技能已检查。'}]}})
            if 'incomplete' not in text:event(session,'turn/end',{'reason':{'kind':'completed'}})
        event('unrelated-child','turn/end',{'reason':{'kind':'error','error':{'code':'AUTH'}}})
    send(dict(jsonrpc='2.0',id=request['id'],result=result))
    if method=='shutdown':break
'''


class DeepSeekIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.draft = self.root / 'draft'
        self.draft.mkdir()
        self.sdk = self.root / 'sdk.py'
        self.sdk.write_text(SDK, encoding='utf-8')
        self.launch = [str(python_executable()), '-I', '-X', 'utf8', '-u', str(self.sdk)]
        self.settings = SettingsComponent(self.root / 'settings.ini')
        self.addCleanup(self.settings.close)
        self.settings.set_agent_tool('deepseek')
        self.settings.set_deepseek_options(adapter.DEFAULT_OPTIONS)
        self.launcher = patch.object(adapter, 'launcher', return_value=(self.launch, {}))
        self.launcher.start()
        self.addCleanup(self.launcher.stop)
        self.settings.set_ai_enabled(True)
        self.settings.sync()
        self.worker = CreationWorker(None, 'test', tasks_root=self.root / 'tasks',
                                     directory=self.draft, settings_path=self.settings.path)

    def turn(self, name, prompt='test', **options):
        path = self.draft / name
        path.mkdir()
        return run_prompt(self.worker, path, prompt, **options)

    def requests(self):
        return [json.loads(line) for line in (self.draft / 'requests.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_shared_entry_dispatches_and_resumes_same_session(self):
        progress = Mock()
        self.worker.progress.connect(progress)
        with patch('huanzhizhi.skill_creation.ai_codex.check_codex_configuration') as codex:
            self.assertEqual(self.turn('first'), '技能已检查。')
            first = self.worker.thread_id
            self.assertTrue(first.startswith('session-'))
            self.assertEqual(self.turn('second', 'follow up'), '技能已检查。')
            self.assertEqual(self.worker.thread_id, first)
            codex.assert_not_called()
        requests = self.requests()
        self.assertEqual([r['params']['sessionId'] for r in requests if r['method'] == 'session/resume'], [first])
        self.assertEqual([r['params']['cwd'] for r in requests if r['method'] == 'initialize'], [str(self.draft)] * 2)
        self.assertEqual(progress.call_count, 2)  # repeated text is suppressed within each turn
        self.assertIn('本轮补充沿用', requests[-3]['params']['contentBlocks'][0]['text'])

    def test_image_is_attached_directly_and_observation_does_not_resume(self):
        image = self.draft / 'window.png'
        Image.new('RGB', (13, 9), 'red').save(image)
        self.worker.thread_id = 'old-session'
        self.assertEqual(self.turn('observe', observation_image=image), '技能已检查。')
        requests = self.requests()
        self.assertFalse(any(r['method'] == 'session/resume' for r in requests))
        blocks = next(r['params']['contentBlocks'] for r in requests if r['method'] == 'session/prompt')
        self.assertEqual(blocks[1]['mimeType'], 'image/png')
        self.assertEqual(base64.b64decode(blocks[1]['data']), image.read_bytes())
        overlay = json.loads((self.draft / 'observe/harness.patch.json').read_text())
        self.assertEqual(next(p['config']['mode'] for p in overlay if p.get('id') == 'sandbox-policy'), 'read-only')
        self.assertNotIn('danger-full-access', json.dumps(overlay))

    def test_quota_is_classified_and_failure_does_not_lose_session(self):
        with self.assertRaises(AgentTaskError) as caught:
            self.turn('quota', 'quota')
        self.assertIn('额度不足', caught.exception.user_message)
        self.assertNotIn('private token', str(caught.exception))
        self.assertTrue(self.worker.thread_id)
        self.assertEqual(self.turn('retry'), '技能已检查。')

    def test_exit_zero_and_text_without_completion_are_not_success(self):
        with self.assertRaises(AgentTaskError):
            self.turn('incomplete', 'incomplete')

    def test_missing_session_does_not_start_an_empty_replacement(self):
        self.worker.thread_id = 'session-missing'
        with self.assertRaises(AgentTaskError):
            self.turn('resume')
        self.assertFalse(any(r['method'] == 'session/prompt' for r in self.requests()))
        self.assertEqual(self.worker.thread_id, 'session-missing')

    def test_settings_preserve_deepseek_and_switching_disables_ai(self):
        self.settings.close()
        reopened = SettingsComponent(self.root / 'settings.ini')
        try:
            self.assertEqual(reopened.get_agent_tool(), 'deepseek')
            self.assertEqual(reopened.get_deepseek_options(), adapter.DEFAULT_OPTIONS)
            self.assertTrue(reopened.get_ai_enabled())
            reopened.ai_work_active = lambda: True
            with self.assertRaisesRegex(ValueError, '正在工作'):
                reopened.set_deepseek_options(dict(adapter.DEFAULT_OPTIONS, reasoning_effort='low'))
            reopened.ai_work_active = lambda: False
            reopened.set_agent_tool('codex')
            self.assertFalse(reopened.get_ai_enabled())
        finally:
            reopened.close()

    def test_stopping_shared_runtime_terminates_sdk_and_its_child(self):
        interrupted = threading.Event()
        def check_stop():
            if interrupted.is_set():
                raise InterruptedError('stopped')
        self.worker.check_stop = check_stop
        pids = self.draft / 'pids.json'
        def interrupt():
            deadline = time.monotonic() + 15
            while not pids.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            interrupted.set()
        thread = threading.Thread(target=interrupt)
        thread.start()
        try:
            with self.assertRaises(InterruptedError):
                self.turn('stop', 'hang')
        finally:
            thread.join(timeout=16)
        self.assertTrue(pids.is_file())
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        for pid in json.loads(pids.read_text()):
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                try:
                    code = wintypes.DWORD()
                    self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                    self.assertNotEqual(code.value, 259, f'process {pid} still active')
                finally:
                    kernel.CloseHandle(handle)


class DeepSeekProtocolTests(unittest.TestCase):
    def test_catalog_retains_capabilities_but_not_private_fields(self):
        models = adapter.parse_catalog({'models': [dict(id='deepseek-account/test', name='Test',
                                                       efforts=['off', 'high'], image=True, token='private')]})
        self.assertEqual(set(models[0]), {'id', 'name', 'efforts', 'image'})
        self.assertNotIn('private', json.dumps(models))

    def test_partial_lines_are_retried_and_cancelled_progress_is_not_emitted(self):
        worker = Mock(thread_id=None)
        worker.isInterruptionRequested.return_value = False
        reader = io.BytesIO(b'{"type":"session","id":"session-a"}')
        consume = adapter.output_reader()
        consume(reader, worker)
        self.assertIsNone(worker.thread_id)
        consume(reader, worker, final=True)
        self.assertEqual(worker.thread_id, 'session-a')
        worker.isInterruptionRequested.return_value = True
        consume(io.BytesIO((json.dumps(dict(type='text', text='进度：已停止')) + '\n').encode()), worker)
        worker.progress.emit.assert_not_called()
