import io
import json
import os
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtWidgets, QtTest

from huanzhizhi.agents.opencode import adapter
from huanzhizhi.agents.opencode.guide import OpenCodeGuide
from huanzhizhi.agents.opencode import install as installer
from huanzhizhi.agents.runtime import AgentTaskError, SettingsError
from huanzhizhi.settings import SettingsComponent


def records(*items):
    return ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in items)


class OpenCodeAdapterTests(unittest.TestCase):
    def test_zero_exit_with_provider_error_is_not_success(self):
        text = records({'type': 'error', 'error': {'type': 'provider.auth', 'status': 403,
                        'message': "OpenCode's free tier can only be used from within OpenCode."}})
        with self.assertRaisesRegex(AgentTaskError, '免费服务拒绝'):
            adapter.read_result(text, 0)

    def test_partial_reply_and_nonzero_cost_cannot_be_reported_as_free_success(self):
        text = records({'type': 'text', 'part': {'text': '部分回复'}})
        with self.assertRaisesRegex(AgentTaskError, '完整答复'):
            adapter.read_result(text)
        text += records({'type': 'step_finish', 'part': {'reason': 'stop', 'cost': .001}})
        with self.assertRaisesRegex(AgentTaskError, '非零模型费用'):
            adapter.read_result(text)

    def test_final_reply_does_not_repeat_intermediate_agent_messages(self):
        text = records({'type': 'text', 'part': {'messageID': 'a', 'text': '正在验证'}},
                       {'type': 'text', 'part': {'messageID': 'b', 'text': '验证完成。'}},
                       {'type': 'step_finish', 'part': {'reason': 'stop', 'cost': 0}})
        self.assertEqual(adapter.read_result(text), '验证完成。')

    def test_incremental_event_reader_preserves_session_and_waits_for_complete_utf8_line(self):
        worker = Mock()
        worker.isInterruptionRequested.return_value = False
        reader = adapter.output_reader()
        raw = records({'type': 'text', 'sessionID': 'ses_123', 'part': {'text': '进度：正在验证技能。'}}).encode()
        stream = io.BytesIO(raw[:-2])
        reader(stream, worker)
        worker.progress.emit.assert_not_called()
        stream.seek(0, 2)
        stream.write(raw[-2:])
        stream.seek(0)
        reader(stream, worker)
        self.assertEqual(worker.thread_id, 'ses_123')
        worker.progress.emit.assert_called_once_with('正在验证技能。')

    def test_free_catalog_filters_paid_unknown_cost_and_disabled_models_and_drops_secrets(self):
        base = {'providerID': 'opencode', 'enabled': True, 'status': 'active',
                'capabilities': {'tools': True, 'input': ['text', 'image']},
                'cost': [{'input': 0, 'output': 0}], 'settings': {'apiKey': 'secret'}}
        items = [dict(base, id='free', name='Free'), dict(base, id='paid', cost=[{'input': 0, 'output': 1}]),
                 dict(base, id='unknown', cost=[]), dict(base, id='disabled', enabled=False),
                 dict(base, id='text-only', capabilities={'tools': True, 'input': ['text']})]
        with patch.object(adapter, 'invoke', return_value=json.dumps({'data': items})):
            self.assertEqual(adapter.free_models('opencode.exe'), [{'id': 'opencode/free', 'name': 'Free', 'image': True}])
        with patch.object(adapter, 'free_models', return_value=[]):
            with self.assertRaisesRegex(SettingsError, '不会切换到付费模型'):
                adapter.require_free_model('opencode.exe', 'opencode/free')

    def test_resume_uses_specific_session_and_private_server(self):
        command = adapter.build_command('opencode.exe', {'executable': '', 'model': 'opencode/free'}, 'ses_existing')
        self.assertIn('--standalone', command)
        self.assertEqual(command[command.index('--session') + 1], 'ses_existing')
        self.assertNotIn('--continue', command)
        self.assertNotIn('--auto', adapter.build_command('opencode.exe', {'executable': '', 'model': 'opencode/free'}, probe=True))

    def test_existing_environment_provider_and_denials_are_preserved(self):
        original = {'providers': {'custom': {'enabled': True}},
                    'permissions': [{'action': 'edit', 'resource': 'protected/*', 'effect': 'deny'}]}
        with patch.dict(os.environ, {'OPENCODE_CONFIG_CONTENT': json.dumps(original)}):
            merged = json.loads(adapter.environment()['OPENCODE_CONFIG_CONTENT'])
            self.assertEqual(merged['providers'], original['providers'])
            self.assertEqual(merged['permissions'][0], original['permissions'][0])
            self.assertEqual(json.loads(os.environ['OPENCODE_CONFIG_CONTENT']), original)

    def test_v2_missing_finish_is_confirmed_from_session_not_assumed_from_text(self):
        text = records({'type': 'step_finish', 'part': {'reason': 'tool-calls', 'cost': 0}},
                       {'type': 'text', 'sessionID': 'ses_123', 'part': {'text': '连接成功'}})
        record = {'info': {'id': 'ses_123', 'outcome': 'succeeded', 'cost': 0},
                  'messages': [{'type': 'assistant', 'finish': 'stop'}]}
        with patch.object(adapter, 'invoke', return_value=json.dumps(record)) as invoke:
            self.assertEqual(adapter.complete_result(text, 0, 'opencode.exe'), '连接成功')
            self.assertIn('--standalone', invoke.call_args.args[0])
        record['info']['outcome'] = 'failed'
        with patch.object(adapter, 'invoke', return_value=json.dumps(record)):
            with self.assertRaisesRegex(AgentTaskError, '尚未成功完成'):
                adapter.complete_result(text, 0, 'opencode.exe')


class OpenCodeGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = SettingsComponent(Path(self.temporary.name) / 'settings.ini')
        self.guide = OpenCodeGuide(self.settings)

    def tearDown(self):
        self.guide.stop()
        self.guide.deleteLater()
        self.app.processEvents()
        self.settings.close()
        self.temporary.cleanup()

    def finish(self):
        for _ in range(100):
            if self.guide.worker is None:
                return
            QtTest.QTest.qWait(10)
        self.fail('guide did not finish')

    def test_install_controls_follow_executable_discovery(self):
        with patch.object(adapter, 'executable', return_value=r'C:\tools\opencode.exe'):
            self.guide.refresh()
        self.assertEqual(self.guide.install_button.text(), '已安装')
        self.assertFalse(self.guide.install_button.isEnabled())
        self.assertEqual(self.guide.location.toolTip(), r'C:\tools\opencode.exe')
        self.assertTrue(self.guide.detect_button.isEnabled())
        with patch.object(adapter, 'executable', side_effect=SettingsError('未找到程序')):
            self.guide.refresh()
        self.assertEqual(self.guide.install_button.text(), '安装并开始使用')
        self.assertTrue(self.guide.install_button.isEnabled())

    def test_open_loads_models_once_and_enable_checks_connection(self):
        self.settings.set_agent_tool('opencode')
        with patch.object(adapter, 'check_configuration', return_value=r'C:\tools\opencode.exe'), \
                patch.object(adapter, 'free_models', return_value=[{'id': 'opencode/free', 'name': 'Free', 'image': True}]) as models:
            self.guide.show()
            self.finish()
            self.guide.hide()
            self.guide.show()
            self.app.processEvents()
            models.assert_called_once()
        self.assertEqual(self.settings.get_opencode_options()['model'], 'opencode/free')
        self.assertFalse(self.settings.get_ai_enabled())
        self.assertTrue(self.guide.detect_button.isHidden())
        self.assertTrue(self.guide.program_toggle.isHidden())
        with patch.object(adapter, 'test_connection', return_value='连接成功') as connection, \
                patch.object(adapter, 'executable', return_value=r'C:\tools\opencode.exe'):
            self.guide.enable()
            self.finish()
            connection.assert_called_once()
        self.assertTrue(self.settings.get_ai_enabled())
        self.assertTrue(self.settings.is_opencode_verified())

    def test_reopened_settings_use_cached_image_models_without_process(self):
        self.settings.set_opencode_options({'executable': '', 'model': 'opencode/free'})
        self.settings.set_opencode_model_cache([
            {'id': 'opencode/free', 'name': 'Free', 'image': True},
            {'id': 'opencode/text', 'name': 'Text', 'image': False},
        ])
        self.settings.sync()
        reopened = SettingsComponent(self.settings.path)
        guide = OpenCodeGuide(reopened)
        try:
            with patch.object(adapter, 'check_configuration') as check:
                guide.show()
                self.app.processEvents()
                self.assertIsNone(guide.worker)
                check.assert_not_called()
            self.assertEqual(guide.model_combo.count(), 1)
            self.assertEqual(guide.model_combo.currentText(), 'Free')
            self.assertTrue(guide.model_combo.isEnabled())
            with patch('huanzhizhi.settings.component.time.time', return_value=0):
                self.assertEqual(reopened.get_opencode_model_cache(), [])
            with patch('huanzhizhi.settings.component.time.time', return_value=9999999999):
                self.assertEqual(reopened.get_opencode_model_cache(), [])
            reopened.set_opencode_options({'executable': r'C:\other\opencode.exe', 'model': 'opencode/free'})
            self.assertEqual(reopened.get_opencode_model_cache(), [])
        finally:
            guide.stop()
            guide.close()
            guide.deleteLater()
            reopened.close()

    def test_detect_test_enable_and_switch_preserves_codex_settings(self):
        original = self.settings.get_codex_options()
        with patch.object(self.settings, 'check_agent_configuration'):
            self.settings.set_ai_enabled(True)
        with patch.object(adapter, 'check_configuration', return_value=r'C:\tools\opencode.exe'), \
                patch.object(adapter, 'free_models', return_value=[{'id': 'opencode/free', 'name': 'Free', 'image': True}]):
            self.guide.detect_button.click()
            self.finish()
        # Preparing OpenCode must not disable an active Codex configuration.
        self.assertEqual(self.settings.get_agent_tool(), 'codex')
        self.assertTrue(self.settings.get_ai_enabled())
        with patch.object(adapter, 'test_connection', return_value='之之连接成功'):
            self.guide.start('test')
            self.finish()
        self.assertTrue(self.settings.is_opencode_verified())
        with patch.object(adapter, 'executable', return_value=r'C:\tools\opencode.exe'):
            self.settings.set_agent_tool('opencode')
            self.settings.set_ai_enabled(True)
        self.assertEqual(self.settings.get_agent_tool(), 'opencode')
        self.assertTrue(self.settings.get_ai_enabled())
        self.assertEqual(self.settings.get_codex_options(), original)
        self.settings.sync()
        reopened = SettingsComponent(self.settings.path)
        self.assertEqual(reopened.get_agent_tool(), 'opencode')
        self.assertTrue(reopened.get_ai_enabled())
        self.assertEqual(reopened.get_codex_options(), original)
        reopened.close()
        self.settings.set_opencode_options({'executable': r'C:\tools\opencode.exe', 'model': 'opencode/another'})
        self.assertFalse(self.settings.get_ai_enabled())
        with self.assertRaisesRegex(SettingsError, '连接测试'):
            self.settings.set_ai_enabled(True)
        self.settings.set_agent_tool('codex')
        self.assertEqual(self.settings.get_codex_options(), original)

    def test_failed_connection_keeps_ai_disabled_and_displays_reason(self):
        self.settings.set_agent_tool('opencode')
        self.settings.set_opencode_options({'executable': '', 'model': 'opencode/free'})
        self.settings.mark_opencode_verified(self.settings.get_opencode_options())
        with patch.object(adapter, 'executable', return_value='opencode.exe'):
            self.settings.set_ai_enabled(True)
        self.settings.set_opencode_model_cache([{'id': 'opencode/free', 'name': 'Free', 'image': True}])
        self.guide.refresh()
        with patch.object(adapter, 'test_connection', side_effect=SettingsError('免费额度暂不可用')):
            self.guide.enable()
            self.finish()
        self.assertIn('免费额度暂不可用', self.guide.status.text())
        self.assertFalse(self.settings.is_opencode_verified())
        self.assertFalse(self.settings.get_ai_enabled())
        self.assertFalse(self.settings.is_opencode_verified())

    def test_cancelled_connection_does_not_save_verification(self):
        self.settings.set_agent_tool('opencode')
        self.settings.set_opencode_options({'executable': '', 'model': 'opencode/free'})
        self.settings.set_opencode_model_cache([{'id': 'opencode/free', 'name': 'Free', 'image': True}])
        self.guide.refresh()
        def wait_for_stop(options, check_stop):
            while True:
                check_stop()
                QtTest.QTest.qSleep(5)
        with patch.object(adapter, 'test_connection', side_effect=wait_for_stop):
            self.guide.enable()
            self.guide.cancel_button.click()
            self.finish()
        self.assertIn('取消', self.guide.status.text())
        self.assertFalse(self.settings.is_opencode_verified())
        with self.assertRaises(SettingsError):
            self.settings.set_ai_enabled(True)


class OpenCodeInstallTests(unittest.TestCase):
    def test_cancelled_download_leaves_no_partial_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'managed/opencode.exe'
            def download(url, destination, progress, **kwargs):
                destination.write_bytes(b'partial package')
                progress(10)
            def stopped():
                raise InterruptedError('cancelled')
            with patch.object(installer, 'managed_executable', return_value=target), \
                    patch.object(installer.platform, 'machine', return_value='AMD64'), \
                    patch.object(installer, '_download', side_effect=download):
                with self.assertRaises(InterruptedError):
                    installer.install(lambda _: None, stopped)
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [])

    def test_archive_link_cannot_be_published_as_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'managed/opencode.exe'
            def download(url, destination, progress, **kwargs):
                with tarfile.open(destination, 'w:gz') as archive:
                    entry = tarfile.TarInfo('package/bin/opencode.exe')
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = '../../outside.exe'
                    archive.addfile(entry)
            with patch.object(installer, 'managed_executable', return_value=target), \
                    patch.object(installer.platform, 'machine', return_value='AMD64'), \
                    patch.object(installer, '_download', side_effect=download):
                with self.assertRaisesRegex(ValueError, '安装包内容无效'):
                    installer.install(lambda _: None, lambda: None)
            self.assertFalse(target.exists())
