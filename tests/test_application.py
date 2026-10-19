"""Restored application components with the agreed runtime interaction changes."""
import os
from pathlib import Path
from tests.skill_factory import write_skill_manifest
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from huanzhizhi.application import HuanZhiZhiApplication
from huanzhizhi.parameters import validate_values
from huanzhizhi.pet.views import ParameterDialog
from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo
from huanzhizhi.tasks.catalog import TaskCatalog
from huanzhizhi.tasks.protocol import StatusEvent

FIXTURE = '''from huanzhizhi.task_sdk import describe, TaskSession
if describe(__file__):
    raise SystemExit(0)
s=TaskSession(__file__)
s.emit_status('等待你操作','测试任务','收到次数：'+str(s.options['count']))
s.wait_for_stop(15)
s.emit_finished('已停止','测试任务','已清理资源',stopped_by_request=True)
'''

FIXTURE_METADATA = dict(name='测试任务', description='不访问桌面，只报告状态', parameters=[
    {'key':'count','label':'次数','default':3,'minimum':1,'maximum':10}])

class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        path=self.root/'tasks/fixture/task.py'
        path.parent.mkdir(parents=True)
        path.write_text(FIXTURE,encoding='utf-8')
        write_skill_manifest(path, **FIXTURE_METADATA)
        self.host=HuanZhiZhiApplication(self.app,project_root=self.root,settings_path=self.root/'settings.ini')
        with patch.object(self.host.settings, 'check_agent_configuration'):
            self.host.settings.set_ai_enabled(True)
        self.window_enumeration = patch.object(self.host.chat, '_available_windows', return_value=())
        self.window_enumeration.start()
        self.addCleanup(self.window_enumeration.stop)
        restoration = patch.object(Win32DesktopApi, 'restore', side_effect=lambda window: window)
        restoration.start()
        self.addCleanup(restoration.stop)
        self.tasks=self.host.tasks
        self.presentation=self.tasks.presentation
        self.host.pet.show()
        self.host.pet.window.move(160,180)
        self.host.pet.window.moved.emit(self.host.pet.window.pos())
        self.app.processEvents()

    def tearDown(self):
        self.host._close_components()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_for(self,condition):
        deadline=time.monotonic()+6
        while not condition() and time.monotonic()<deadline:
            QtTest.QTest.qWait(15)
        self.assertTrue(condition(),repr(self.presentation.status))

    def start_fixture(self):
        self.host.pet.clicked.emit()
        self.presentation._handle_bubble_clicked('select:fixture')
        self.presentation._handle_bubble_clicked('start_task')
        self.wait_for(lambda:self.presentation.status[0]=='等待你操作')

    def test_runtime_transparency_preference_survives_application_restart(self):
        self.start_fixture()
        self.assertTrue(self.presentation._strip.transparency.isChecked())
        self.presentation._strip.transparency.setChecked(False)
        self.host._close_components()
        self.host = HuanZhiZhiApplication(self.app, project_root=self.root,
                                        settings_path=self.root/'settings.ini')
        self.tasks = self.host.tasks
        self.presentation = self.tasks.presentation
        self.assertFalse(self.presentation._strip.transparency.isChecked())
        with patch.object(self.host.pet, 'set_runtime_transparency') as apply:
            self.tasks.start_task('fixture')
            self.wait_for(lambda: self.presentation.status[0] == '等待你操作')
            apply.assert_called_with(True, False)
            self.presentation._strip.transparency.setChecked(True)
            apply.assert_called_with(True, True)
            from huanzhizhi.settings import SettingsComponent
            saved = SettingsComponent(self.root/'settings.ini')
            try:
                self.assertTrue(saved.get_runtime_transparent())
            finally:
                saved.close()

    def test_newly_saved_skill_enters_test_group(self):
        self.assertEqual(self.tasks.get_task_preferences('fixture')['group'], 'formal')
        path = self.root / 'test_tasks/new_skill/task.py'
        path.parent.mkdir(parents=True)
        path.write_text(FIXTURE, encoding='utf-8')
        write_skill_manifest(path, **FIXTURE_METADATA)
        self.host.chat.skill_saved.emit(str(path.parent))
        self.assertEqual(self.tasks.get_task_preferences('test/new_skill')['group'], 'test')
        self.assertEqual(self.tasks.get_task_preferences('fixture')['group'], 'formal')
        self.assertTrue(self.tasks.is_task_visible('test/new_skill'))
        self.assertTrue(self.host.pet_settings._task_items['test/new_skill'].isHidden())
        self.host.pet_settings._task_groups.setCurrentIndex(1)
        self.assertFalse(self.host.pet_settings._task_items['test/new_skill'].isHidden())

    def test_display_recovery_updates_pet_and_followers_without_showing_hidden_pet(self):
        self.host.pet.hide()
        scale = self.host.pet.display_scale
        with patch.object(self.host.chat, 'follow_pet') as follow, \
             patch.object(self.host.pet._animation, 'restore_display', wraps=self.host.pet._animation.restore_display) as rebuild:
            self.host._display_recovery._recovered()
        rebuild.assert_called_once_with()
        follow.assert_called_once_with(self.host.pet.bubble_geometry())
        self.assertFalse(self.host.pet.window.isVisible())
        self.assertEqual(self.host.pet.display_scale, scale)

    def test_display_recovery_keeps_edge_pet_speech_anchor_on_screen(self):
        area = self.app.primaryScreen().availableGeometry()
        window = self.host.pet.window
        window.move(window.clamp_position(QtCore.QPoint(area.right(), area.center().y())))
        self.host.pet.speech_bubble.show_text('恢复位置验证', 10000)
        self.host.pet.restore_display()
        self.assertIsNotNone(self.app.screenAt(window.visible_geometry().center()))
        self.assertTrue(area.contains(self.host.pet.speech_bubble.geometry()))

    def test_start_uses_parameters_and_collapses_original_bubble(self):
        self.host.settings.set_task_parameters('fixture',{'count':7})
        self.start_fixture()
        self.assertEqual(self.presentation.status[2],'收到次数：7')
        self.assertFalse(self.presentation.has_bubbles)
        self.assertTrue(self.presentation._strip.isVisible())
        with self.assertRaisesRegex(RuntimeError,'已有任务'):
            self.tasks.start_task('fixture')

    def test_edit_button_opens_selected_skill_and_save_preserves_both_versions_settings(self):
        self.host.settings.set_task_parameters('fixture', {'count': 7})
        test_entry = self.root/'test_tasks/fixture/task.py'
        test_entry.parent.mkdir(parents=True)
        test_entry.write_text(FIXTURE, encoding='utf-8')
        write_skill_manifest(test_entry, **FIXTURE_METADATA)
        self.tasks.reload_tasks()
        self.host.settings.set_task_parameters('test/fixture', {'count': 2})
        self.tasks.set_task_preferences('test/fixture', name='fixture', description='测试说明', show_logs=False)
        self.tasks.set_task_visible('test/fixture', False)
        self.host.pet.clicked.emit()
        self.presentation._handle_bubble_clicked('select:fixture')
        bubble = self.presentation._bubbles[0]
        self.assertEqual(bubble.layout_mode, 'actions')
        primary = bubble.primary_row.itemAt(0).widget()
        parameters = bubble.button_row.itemAt(0).widget()
        edit = bubble.button_row.itemAt(1).widget()
        self.assertEqual(primary.text(), '开始执行')
        self.assertEqual((parameters.text(), edit.text()), ('参数设置', '修改技能'))
        self.assertFalse(edit.icon().isNull())
        self.app.processEvents()
        self.assertGreater(parameters.y(), primary.geometry().bottom())
        self.assertEqual(primary.width(), parameters.width()+edit.width()+bubble.button_row.spacing())
        edit.click()
        self.app.processEvents()
        self.assertFalse(self.presentation.has_bubbles)
        self.assertTrue(self.host.chat.conversation.isVisible())
        self.assertEqual(self.host.chat._editing_skill, (self.root/'tasks/fixture').resolve())
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            self.host.chat._send_message('增加说明')
            self.assertEqual(worker_type.call_args.kwargs['source_skill'], (self.root/'tasks/fixture').resolve())
            self.assertTrue(self.tasks.external_work_active)
            with self.assertRaisesRegex(RuntimeError, '正在'):
                self.tasks.start_task('fixture')
            worker_type.return_value.source_skill = (self.root/'tasks/fixture').resolve()
            self.host.chat._skill_saved(str(self.root/'tasks/fixture'))
            self.host.chat._exploration_finished()
        self.assertEqual(self.tasks.get_task_preferences('fixture')['group'], 'formal')
        self.assertEqual(self.tasks.get_task_preferences('test/fixture')['group'], 'test')
        self.assertEqual(self.host.settings.get_task_parameters('test/fixture'), {'count': 2})
        self.assertEqual(self.tasks.get_task_preferences('test/fixture')['name'], 'fixture')
        self.assertFalse(self.tasks.get_task_preferences('test/fixture')['show_logs'])
        self.assertFalse(self.tasks.is_task_visible('test/fixture'))
        self.assertEqual(self.host.settings.get_task_parameters('fixture'), {'count': 7})
        self.assertFalse(self.tasks.external_work_active)
        self.host.chat.conversation.stop_button.click()
        self.assertIsNone(self.host.chat.current_work)
        self.assertTrue(self.presentation.has_bubbles)

    def test_chat_settings_failure_shows_notification_with_settings_action(self):
        chat = self.host.chat
        chat.target_window = WindowInfo(123, '目标应用', 0, 0, 800, 600)
        chat.conversation.input.setPlainText('我的目标')
        chat._show_chat()
        with patch.object(self.host.settings, 'get_codex_options', side_effect=ValueError('测试配置错误')), \
                patch('huanzhizhi.pet.chat_window.CreationWorker') as worker, \
                patch.object(self.host.pet_settings, 'show_ai_settings') as show_settings:
            chat._send_message('我的目标')
            worker.assert_not_called()
            bubble = self.host.pet.speech_bubble
            self.assertTrue(bubble.isVisible())
            self.assertEqual(bubble.label.text(), '测试配置错误')
            self.assertEqual(bubble.repair_button.text(), '前往 AI 设置')
            self.assertTrue(bubble.repair_button.isVisible())
            self.assertEqual(chat.conversation.input.toPlainText(), '我的目标')
            self.assertIsNone(chat.current_work)
            bubble.repair_button.click()
            show_settings.assert_called_once_with()

    def test_running_skill_cannot_be_edited(self):
        self.start_fixture()
        with self.assertRaisesRegex(RuntimeError, '先停止'):
            self.host.chat.edit_skill(self.root/'tasks/fixture')

    def fail_fixture(self, *, crash=False, wait_after_failure=False, error='RuntimeError: repair test failure'):
        path = self.root/'tasks/fixture/task.py'
        ending = ("raise RuntimeError('repair test failure')\n" if crash else
                  f"s.emit_finished('失败', '测试任务', '测试诊断', error={error!r})\n")
        if wait_after_failure:
            ending += "s.wait_for_stop(15)\n"
        path.write_text(FIXTURE[:FIXTURE.index('s.emit_status')] + ending, encoding='utf-8')
        self.tasks.start_task('fixture', {'count': 8})
        self.wait_for(lambda: ('repair test failure' if crash else error) in self.host.pet_settings._log_view.toPlainText())
        return self.host.pet.speech_bubble

    def test_window_focus_failure_offers_retry_with_original_parameters_without_ai(self):
        self.host.settings.set_ai_enabled(False)
        bubble = self.fail_fixture(error='前台输入失败：foreground verification failed: expected 274927248, got 0')
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        self.assertEqual(bubble.label.text(), '请打开技能对应窗口后重试')
        self.assertEqual(bubble.repair_button.text(), '重试')
        self.assertEqual(bubble.dismiss_button.text(), '暂不处理')
        self.assertTrue(bubble.repair_button.isVisible())
        self.host.settings.set_task_parameters('fixture', {'count': 2})
        (self.root/'tasks/fixture/task.py').write_text(FIXTURE, encoding='utf-8')
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            bubble.repair_button.click()
            self.wait_for(lambda: self.presentation.status[0] == '等待你操作')
            self.assertIn('收到次数：8', self.presentation.status[2])
            worker_type.assert_not_called()

    def test_window_focus_retry_waits_for_previous_process(self):
        bubble = self.fail_fixture(wait_after_failure=True,
                                   error='前台输入失败：SetForegroundWindow returned False for hwnd 123')
        self.assertNotEqual(self.tasks.process.state, 'idle')
        (self.root/'tasks/fixture/task.py').write_text(FIXTURE, encoding='utf-8')
        bubble.repair_button.click()
        self.wait_for(lambda: self.presentation.status[0] == '等待你操作')
        self.assertIn('收到次数：8', self.presentation.status[2])

    def test_capture_timeout_offers_retry_and_restarts_skill(self):
        bubble = self.fail_fixture(error='client capture failed for Edge [288x37] hwnd=123: '
                                   'WGC did not deliver the requested frame within 2 seconds')
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        self.assertEqual(bubble.label.text(), '请打开技能对应窗口后重试')
        self.assertEqual(bubble.repair_button.text(), '重试')
        (self.root/'tasks/fixture/task.py').write_text(FIXTURE, encoding='utf-8')
        bubble.repair_button.click()
        self.wait_for(lambda: self.presentation.status[0] == '等待你操作')
        self.assertIn('收到次数：8', self.presentation.status[2])

    def test_known_window_errors_share_retry_but_invalid_arguments_still_offer_repair(self):
        task = self.tasks._catalog.get('fixture')
        retry_errors = (
            'selected target window does not exist or is not visible: 123',
            'selected target window does not match rule: Edge',
            "no window matches title and client size: 'game' 800x600",
            'multiple windows match the target rule: Edge',
            'refreshed target window no longer matches rule: Edge',
            'window is still minimized after restore: 123',
            'target window is minimized: 123',
            'window does not exist: 123',
            'enumerated hwnd no longer exists: 123',
            'window is not visible: 123',
            'window has no title: 123',
            'window information is stale; refresh is required: old=Edge, current=Edge',
            'window 123 client size is not positive: 0x0',
            'SetWindowPos returned False for hwnd 123',
            'client resize verification failed: expected 800x600, got 700x600',
            'client capture failed for Edge: WGC capture failed: WGC session closed',
            'client capture failed for Edge: WGC capture failed: WGC frame (100, 100) cannot contain client crop (0, 0, 200, 200)',
            'WGC did not deliver the requested frame within 2 seconds',
            '未找到目标 WeiqiGo.com 窗口，请保持单人围棋页面可见',
            '找到多个 WeiqiGo.com 窗口，请通过 hwnd 指定目标窗口',
        )
        repair_errors = (
            'capture region must be inside the client rectangle',
            'client capture failed for Edge: requested region is outside the client rectangle',
            'client capture failed for Edge: cannot start WGC for hwnd 123: No module named windows_capture',
            'captured image conversion produced unexpected mode or size',
            'native adapter has no get_foreground_window method',
        )
        for error in retry_errors + repair_errors:
            with self.subTest(error=error):
                self.tasks.failure_context = (task, {'count': 8}, '')
                try:
                    self.host._show_task_error('任务失败：' + error)
                finally:
                    self.tasks.failure_context = None
                bubble = self.host.pet.speech_bubble
                self.assertEqual(bubble.repair_button.text(),
                                 '重试' if error in retry_errors else '让 AI 检查并修复')

    def test_window_focus_retry_dismiss_and_new_run_clear_callback(self):
        bubble = self.fail_fixture(error='前台输入失败：foreground verification failed: expected 123, got 0')
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        with patch.object(self.tasks, 'start_task') as start:
            bubble.dismiss_button.click()
            bubble.repair_button.click()
            start.assert_not_called()
        (self.root/'tasks/fixture/task.py').write_text(FIXTURE, encoding='utf-8')
        self.tasks.start_task('fixture')
        self.assertFalse(bubble.isVisible())

    def test_failure_bubble_repair_sends_context_to_existing_edit_worker(self):
        bubble = self.fail_fixture()
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        self.assertEqual(bubble.label.text(), '出现未知情况')
        self.assertEqual(bubble.repair_button.text(), '让 AI 检查并修复')
        self.assertEqual(bubble.dismiss_button.text(), '暂不处理')
        self.assertFalse(bubble.hide_timer.isActive())
        self.host.settings.set_task_parameters('fixture', {'count': 2})
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            bubble.repair_button.click()
            worker_type.assert_called_once()
            self.assertEqual(worker_type.call_args.kwargs['source_skill'], (self.root/'tasks/fixture').resolve())
            prompt = worker_type.call_args.args[1]
            self.assertIn('检查并修复', prompt)
            self.assertIn('repair test failure', prompt)
            self.assertIn('测试诊断', prompt)
            self.assertIn('"count": 8', prompt)
            self.assertIn(str(self.host.run_log.path), prompt)
            worker_type.return_value.start.assert_called_once()
            self.assertTrue(self.host.chat.conversation.isVisible())
            self.assertFalse(bubble.isVisible())
            self.host.chat._exploration_finished()

    def test_foreground_protection_shows_reason_without_repair_and_stops_child(self):
        reason = '鼠标位置被改变，已停止前台操作'
        self.check_foreground_interruption(reason, retry=False)

    def test_foreground_focus_loss_and_obstruction_offer_retry(self):
        for reason in ('目标窗口已失去焦点，已停止前台操作', '目标位置被其他窗口遮挡，已取消点击'):
            with self.subTest(reason=reason):
                self.check_foreground_interruption(reason, retry=True)

    def check_foreground_interruption(self, reason, *, retry):
        path = self.root / 'tasks/fixture/task.py'
        path.write_text(FIXTURE[:FIXTURE.index('s.emit_status')] + '''
from huanzhizhi.task_sdk import _request_foreground_input
try:
    _request_foreground_input(dict(hwnd=123, title='fixture', left=0, top=0,
                                  width=320, height=200), 'click', 30, 40)
except InterruptedError:
    s.emit_finished('已停止', '测试任务', '已按请求停止', stopped_by_request=True)
except Exception as error:
    s.emit_finished('失败', '测试任务', str(error), error=str(error))
''', encoding='utf-8')
        self.host.settings.set_input_mode('foreground')
        self.host.pet.show_notification('旧故障', on_repair=lambda: self.fail('残留修复入口'))
        errors = []
        self.tasks.error.connect(errors.append)
        with patch.object(self.host.pet, 'start_foreground_input',
                          side_effect=lambda request, done: done(InterruptedError(reason))):
            self.tasks.start_task('fixture')
            self.wait_for(lambda: self.tasks.process.state == 'idle')
        bubble = self.host.pet.speech_bubble
        self.tasks.error.disconnect(errors.append)
        self.assertEqual(errors, [reason] if retry else [])
        self.assertEqual(bubble.label.text(), '请打开技能对应窗口后重试' if retry else reason)
        self.assertEqual(bubble.repair_button.isVisible(), retry)
        if retry:
            self.assertEqual(bubble.repair_button.text(), '重试')
            (self.root/'tasks/fixture/task.py').write_text(FIXTURE, encoding='utf-8')
            self.host.settings.set_input_mode('background')
            bubble.repair_button.click()
            self.wait_for(lambda: self.presentation.status[0] == '等待你操作')
            self.tasks.process.stop()
            self.wait_for(lambda: self.tasks.process.state == 'idle')
        else:
            bubble.repair_button.click()
            self.assertEqual(self.presentation.status, ('已停止', '测试任务', reason))
        self.assertIn(reason, self.host.pet_settings._log_view.toPlainText())

    def test_crash_bubble_dismiss_does_not_start_ai(self):
        bubble = self.fail_fixture(crash=True)
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        self.assertEqual(bubble.label.text(), '出现未知情况')
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            bubble.dismiss_button.click()
            self.assertFalse(bubble.isVisible())
            worker_type.assert_not_called()
        self.assertIn('Traceback', self.host.pet_settings._log_view.toPlainText())

    def test_repair_waits_for_failed_process_to_stop(self):
        bubble = self.fail_fixture(wait_after_failure=True)
        self.assertNotEqual(self.tasks.process.state, 'idle')
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            bubble.repair_button.click()
            self.wait_for(lambda: worker_type.called)
            self.assertEqual(self.tasks.process.state, 'idle')
            worker_type.return_value.start.assert_called_once()
            self.host.chat._exploration_finished()

    def test_new_run_clears_previous_failure_repair_action(self):
        bubble = self.fail_fixture()
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        (self.root/'tasks/fixture/task.py').write_text(FIXTURE, encoding='utf-8')
        self.tasks.start_task('fixture')
        self.assertFalse(bubble.isVisible())
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            bubble.repair_button.click()
            worker_type.assert_not_called()

    def test_non_skill_error_does_not_offer_stale_repair(self):
        bubble = self.fail_fixture()
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        self.tasks.error.emit('鼠标监听失败')
        self.assertEqual(bubble.label.text(), '出现未知情况')
        self.assertFalse(bubble.repair_button.isVisible())
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            bubble.repair_button.click()
            worker_type.assert_not_called()

    def test_bubbles_and_status_strip_anchor_to_character_not_canvas(self):
        window = self.host.pet.window
        self.assertEqual(self.presentation._provided_pet_rect(), self.host.pet.bubble_geometry())
        with patch.object(self.host.pet.speech_bubble, 'show_text') as show:
            self.host.pet.show_message('定位检查')
            self.assertEqual(show.call_args.args[2], self.host.pet.bubble_geometry())
        self.start_fixture()
        self.assertEqual(self.presentation._strip.y(), self.host.pet.runtime_geometry().bottom() + 6)
        self.host.pet.set_display_scale(.56)
        self.assertEqual(self.presentation._provided_pet_rect(), self.host.pet.bubble_geometry())
        self.assertEqual(self.presentation._strip.y(), self.host.pet.runtime_geometry().bottom() + 6)

    def test_details_toggle_stay_outside_and_refresh(self):
        self.start_fixture()
        self.host.pet.clicked.emit()
        self.assertTrue(self.presentation.has_bubbles)
        self.tasks._handle_mouse_pressed(0,0)
        self.assertTrue(self.presentation.has_bubbles)
        self.tasks._handle_task_event(StatusEvent('工作中','测试任务','已完成 2 / 3'))
        self.assertIn('已完成 2 / 3',self.presentation._bubbles[0].detail_label.text())
        self.host.pet.clicked.emit()
        self.assertFalse(self.presentation.has_bubbles)
        self.tasks._handle_task_event(StatusEvent('工作中','测试任务','已完成 3 / 3'))
        self.assertFalse(self.presentation.has_bubbles)
        self.host.pet.clicked.emit()
        self.assertIn('已完成 3 / 3',self.presentation._bubbles[0].detail_label.text())

    def test_stop_button_feedback_and_result_auto_hide(self):
        self.start_fixture()
        self.tasks.set_task_preferences('fixture', name='fixture', description='测试', show_logs=False)
        self.host.pet.clicked.emit()
        QtTest.QTest.mouseClick(self.presentation._strip.stop, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.presentation._strip.status.text(), '停止中')
        self.assertEqual(self.presentation._strip.stop.text(), '停止中…')
        self.assertFalse(self.presentation._strip.stop.isEnabled())
        self.assertFalse(self.presentation._bubbles[0].button_row.itemAt(0).widget().isEnabled())
        self.wait_for(lambda:self.tasks.process.state=='idle')
        self.assertTrue(self.presentation.has_bubbles)
        self.assertIn('已清理资源',self.presentation._bubbles[0].detail_label.text())
        self.tasks._handle_mouse_pressed(0,0)
        self.assertTrue(self.presentation.has_bubbles)
        self.wait_for(lambda:not self.presentation._strip.isVisible())
        self.assertFalse(self.presentation.has_bubbles)
        self.host.pet.clicked.emit()
        self.assertEqual(self.presentation._mode,'list')

    def test_hide_and_drag_preserve_expanded_state(self):
        self.start_fixture()
        self.host.pet.clicked.emit()
        bubble=self.presentation._bubbles[0]
        self.host.pet.hide()
        self.assertFalse(bubble.isVisible())
        self.assertFalse(self.presentation._strip.isVisible())
        self.assertNotEqual(self.tasks.process.state,'idle')
        self.host.pet.show()
        self.assertTrue(bubble.isVisible())
        old_strip=self.presentation._strip.pos()
        window = self.host.pet.window
        start = window.geometry().center()
        window.begin_drag(start)
        window.drag_to(start+QtCore.QPoint(20, -30))
        window.end_drag()
        self.assertNotEqual(old_strip,self.presentation._strip.pos())
        self.assertEqual(self.presentation._mode,'status')

    def test_close_reaps_task_process(self):
        self.start_fixture()
        self.host._close_components()
        self.assertEqual(self.tasks.process.state,'idle')
        self.assertIsNone(self.tasks.process._process)

    def test_close_cannot_restart_mouse_watcher_when_bubbles_clear(self):
        self.host.pet.set_mouse_point_visible(True)
        self.host.pet.clicked.emit()
        self.assertTrue(self.tasks.mouse_watcher.enabled)
        self.host._close_components()
        self.assertFalse(self.tasks.mouse_watcher.enabled)

    def test_parameters_save_cancel_and_next_run(self):
        self.host.pet.clicked.emit()
        self.presentation._handle_bubble_clicked('select:fixture')
        def save(dialog):
            dialog.editors['count'].setValue(9)
            return QtWidgets.QDialog.DialogCode.Accepted
        with patch.object(ParameterDialog,'exec',save):
            self.presentation._handle_bubble_clicked('parameters')
        self.assertEqual(self.tasks.task_parameters('fixture'),{'count':9})
        def cancel(dialog):
            dialog.editors['count'].setValue(2)
            return QtWidgets.QDialog.DialogCode.Rejected
        with patch.object(ParameterDialog,'exec',cancel):
            self.tasks.show_parameters('fixture')
        self.assertEqual(self.tasks.task_parameters('fixture'),{'count':9})
        self.assertEqual(self.presentation._mode,'check')
        self.start_fixture()
        self.assertEqual(self.presentation.status[2],'收到次数：9')

    def test_choice_parameter_save_cancel_and_next_run(self):
        from tests.test_parameters import CHOICE
        source = FIXTURE.replace("str(s.options['count'])", "s.options['mode']")
        (self.root / 'tasks/fixture/task.py').write_text(source, encoding='utf-8')
        write_skill_manifest(self.root / 'tasks/fixture/task.py', name='测试任务', parameters=[CHOICE])
        self.tasks.reload_tasks()
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode': 'auto'})
        def save(dialog):
            combo = dialog.editors['mode']
            self.assertIsInstance(combo, QtWidgets.QComboBox)
            self.assertEqual([combo.itemText(i) for i in range(combo.count())], ['自动对弈', '仅提示'])
            combo.setCurrentIndex(combo.findData('hint'))
            return QtWidgets.QDialog.DialogCode.Accepted
        with patch.object(ParameterDialog, 'exec', save):
            self.tasks.show_parameters('fixture')
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode': 'hint'})
        def cancel(dialog):
            self.assertEqual(dialog.editors['mode'].currentData(), 'hint')
            dialog.editors['mode'].setCurrentIndex(0)
            return QtWidgets.QDialog.DialogCode.Rejected
        with patch.object(ParameterDialog, 'exec', cancel):
            self.tasks.show_parameters('fixture')
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode': 'hint'})
        self.start_fixture()
        self.assertEqual(self.presentation.status[2], '收到次数：hint')

    def test_runtime_controls_save_after_ack_and_restore_on_next_start(self):
        from tests.test_parameters import CHOICE, INTEGER
        parameters = ({**CHOICE, 'runtime': True}, {**INTEGER, 'runtime': True})
        source = f'''from huanzhizhi.task_sdk import TaskSession, describe
if describe(__file__):
    raise SystemExit(0)
s = TaskSession(__file__)
s.emit_status('等待你操作','测试任务','已启动')
while s.wait_for_step():
    values = s.apply_parameter_updates()
    s.emit_status('等待你操作','测试任务',str(values))
s.emit_finished('已停止','测试任务','结束',stopped_by_request=True)
'''
        (self.root / 'tasks/fixture/task.py').write_text(source, encoding='utf-8')
        write_skill_manifest(self.root / 'tasks/fixture/task.py', name='测试任务', parameters=list(parameters))
        self.tasks.reload_tasks()
        self.start_fixture()
        strip = self.presentation._strip
        self.app.processEvents()
        self.assertTrue(strip.controls.isVisible())
        self.assertLess(strip.task.geometry().bottom(), strip.controls.y())
        self.assertLess(strip.task.width(), 148)
        QtTest.QTest.mouseClick(strip.controls, QtCore.Qt.MouseButton.LeftButton)
        popup = strip.runtime_popup
        self.assertTrue(popup.isVisible())
        combo = popup.editors['mode']
        combo.setCurrentIndex(combo.findData('hint'))
        combo.activated.emit(combo.currentIndex())
        self.assertFalse(combo.isEnabled())
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode':'auto', 'count':3})
        self.assertEqual(self.tasks._active_options['mode'], 'auto')
        self.tasks.process.step()
        self.wait_for(lambda: self.tasks._active_options['mode'] == 'hint')
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode':'hint', 'count':3})
        self.assertTrue(combo.isEnabled())
        popup.editors['count'].setValue(7)
        self.tasks.process.step()
        self.wait_for(lambda: self.tasks._active_options['count'] == 7)
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode':'hint', 'count':7})
        from huanzhizhi.settings import SettingsComponent
        saved = SettingsComponent(self.root / 'settings.ini')
        self.assertEqual(saved.get_task_parameters('fixture'), {'mode':'hint', 'count':7})
        saved.close()
        errors = []
        self.tasks.error.connect(errors.append)
        with patch.object(self.host.settings, 'set_task_parameters', side_effect=OSError('write failed')):
            popup.editors['count'].setValue(8)
            self.tasks.process.step()
            self.wait_for(lambda: self.tasks._active_options['count'] == 8)
        self.assertIn('已应用，但保存失败', errors[-1])
        self.assertEqual(self.tasks.task_parameters('fixture'), {'mode':'hint', 'count':7})
        with patch.object(self.tasks.process, 'update_parameters') as send:
            self.tasks.update_runtime_parameters({'unknown':1})
            send.assert_not_called()
        self.assertEqual(self.tasks.process.state, 'running')
        self.tasks.stop_task()
        self.assertFalse(popup.isVisible())
        self.wait_for(lambda: self.tasks.process.state == 'idle')
        self.assertFalse(strip.controls.isVisible())
        self.tasks.start_task('fixture')
        self.wait_for(lambda: self.presentation.status[0] == '等待你操作')
        self.assertEqual(self.tasks._active_options, {'mode':'hint', 'count':7})

    def test_original_settings_tasks_logs_mouse_and_scale(self):
        panel=self.host.pet_settings
        panel.show()
        self.assertEqual([panel._navigation.item(i).text() for i in range(panel._navigation.count())],
                         ['常规','技能列表','AI 设置','外部组件','关于幻之之'])
        panel._navigation.setCurrentRow(4)
        panel._about_page.log_button.click()
        self.assertTrue(panel._log_window.isVisible())
        self.host._append_log('恢复验证日志')
        self.assertIn('恢复验证日志',panel._log_view.toPlainText())
        self.host.pet.set_mouse_point_visible(True)
        self.assertTrue(panel._mouse_checkbox.isChecked())
        self.host.pet.set_display_scale(.64)
        self.assertEqual(panel._scale_combo.currentData(),.64)
        self.tasks.set_task_preferences('fixture',name='改名任务',description='原来可以编辑的介绍',show_logs=False)
        self.host.pet.clicked.emit()
        self.assertEqual(self.presentation._bubbles[0].title_label.text(),'改名任务')
        self.tasks.set_task_visible('改名任务',False)
        self.assertEqual(self.presentation._tasks,())
        self.assertFalse(hasattr(self.host.pet,'voice'))
        self.assertFalse(hasattr(self.host.pet,'animation'))

    def test_original_menu_signals_wired_to_real_components(self):
        from huanzhizhi.pet.context_menu import PetContextMenu
        captured=[]
        def factory(*args):
            menu=PetContextMenu(*args)
            def execute(_):
                captured.append(menu)
                menu.settings_action.trigger()
            menu.exec_at=execute
            return menu
        self.host.pet._context_menu_factory=factory
        self.host.pet._show_context_menu(self.host.pet.window.geometry().center())
        self.assertTrue(self.host.pet_settings.window.isVisible())
        self.assertGreater(len(captured[0].scale_options),3)

    def test_chat_follows_pet_and_suppresses_task_popups(self):
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.assertFalse(self.host.chat.isVisible())
        self.assertFalse(self.host.chat.expanded)
        self.host.pet.clicked.emit()
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        self.host.pet.hide()
        self.assertFalse(self.host.chat.isVisible())
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.host.pet.show()
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.host.pet.clicked.emit()
        self.host.chat.expand()
        self.assertFalse(self.presentation.has_bubbles)
        self.host.pet.window.move(300, 400)
        self.host.pet.window.moved.emit(self.host.pet.window.pos())
        self.app.processEvents()
        self.assertEqual(self.host.chat._pet_rect, self.host.pet.bubble_geometry())
        self.host.pet.clicked.emit()
        self.assertFalse(self.presentation.has_bubbles)
        self.assertFalse(self.host.chat.isVisible())
        self.host.pet.clicked.emit()
        self.assertTrue(self.host.chat.isVisible())
        self.host._close_components()
        self.assertFalse(self.host.chat.isVisible())
        self.assertFalse(self.host.chat.toggle_button.isVisible())

    def test_pet_click_toggles_tasks_and_chat_button_together(self):
        self.host.pet.clicked.emit()
        self.assertTrue(self.presentation.has_bubbles)
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        self.host.pet.clicked.emit()
        self.assertFalse(self.presentation.has_bubbles)
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.host.pet.clicked.emit()
        self.presentation._auto_hide_timer.timeout.emit()
        self.assertFalse(self.presentation.has_bubbles)
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.host.pet.clicked.emit()
        self.tasks._handle_mouse_pressed(-100, -100)
        self.assertFalse(self.presentation.has_bubbles)
        self.assertFalse(self.host.chat.toggle_button.isVisible())

    def test_chat_button_click_survives_global_outside_click_handler(self):
        self.host.pet.clicked.emit()
        point = self.host.chat.toggle_button.geometry().center()
        self.tasks._handle_mouse_pressed(point.x(), point.y())
        self.assertTrue(self.presentation.has_bubbles)
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        QtTest.QTest.mouseClick(self.host.chat.toggle_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(self.host.chat.isVisible())
        self.assertFalse(self.presentation.has_bubbles)
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        QtTest.QTest.mouseClick(self.host.chat.toggle_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertFalse(self.host.chat.isVisible())
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        self.assertFalse(self.host.chat.toggle_button.isChecked())
        self.assertFalse(self.presentation.has_bubbles)
        # Reopen directly from the retained button, without clicking the pet first.
        point = self.host.chat.toggle_button.geometry().center()
        self.tasks._handle_mouse_pressed(point.x(), point.y())
        QtTest.QTest.mouseClick(self.host.chat.toggle_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(self.host.chat.isVisible())
        self.host.chat.collapse()
        self.host.pet.hide()
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.host.pet.show()
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.host.pet.clicked.emit()
        self.host.chat.expand()
        self.host.chat.collapse()
        self.host.pet.clicked.emit()
        self.assertTrue(self.host.chat.isVisible())
        self.assertFalse(self.presentation.has_bubbles)
        self.host.pet.clicked.emit()
        self.assertFalse(self.host.chat.isVisible())
        self.assertFalse(self.presentation.has_bubbles)

    def test_outside_click_dismisses_retained_creation_button(self):
        self.host.pet.clicked.emit()
        self.host.chat.expand()
        self.host.chat.collapse()
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        button_point = self.host.chat.toggle_button.geometry().center()
        self.tasks._handle_mouse_pressed(button_point.x(), button_point.y())
        self.assertTrue(self.host.chat.toggle_button.isVisible())
        self.tasks._handle_mouse_pressed(-100, -100)
        self.assertFalse(self.host.chat.toggle_button.isVisible())
        self.assertFalse(self.host.chat.isVisible())
        self.host.pet.clicked.emit()
        self.host.chat.expand()
        self.tasks._handle_mouse_pressed(-100, -100)
        self.assertTrue(self.host.chat.isVisible())

    def test_creation_button_hides_after_returning_from_conversation(self):
        chat = self.open_chat()
        chat.conversation.add_message('user', '领取每日奖励')
        chat.conversation.add_message('assistant', '正在观察窗口')
        self.host._sync_chat_visibility()

        for dismiss in (
            lambda: self.tasks._handle_mouse_pressed(-100, -100),
            self.host.pet.clicked.emit,
            self.presentation._auto_hide_timer.timeout.emit,
        ):
            chat._return_to_tasks()
            self.assertIsNone(chat.current_work)
            self.assertTrue(self.presentation.has_bubbles)
            self.assertTrue(chat.toggle_button.isVisible())
            dismiss()
            self.assertFalse(self.presentation.has_bubbles)
            self.assertFalse(chat.toggle_button.isVisible())
            self.assertEqual(len(chat.conversation.entries), 2)

    def open_chat(self):
        chat = self.host.chat
        chat.expand()
        target = WindowInfo(123, '测试窗口', 0, 0, 800, 600)
        with patch.object(chat, '_available_windows', return_value=(target,)):
            chat._refresh_windows()
            chat.window_list.setCurrentRow(0)
            chat.select_window_button.click()
        return chat

    def test_conversation_survives_outside_click_and_follows_pet_visibility(self):
        chat = self.open_chat()
        c = chat.conversation
        c.add_message('user', '领取每日奖励')
        c.add_message('assistant', '正在观察窗口')
        self.tasks._handle_mouse_pressed(-100, -100)
        self.assertTrue(c.isVisible())
        c.toggle_history()
        self.assertTrue(c.history_window.isVisible())
        c.close_button.click()
        c.update_message(1, '观察完成')
        self.host._sync_chat_visibility()
        self.assertFalse(c.isVisible())
        self.assertFalse(c.history_window.isVisible())
        self.assertFalse(any(b.isVisible() for b in c.bubbles))
        chat.toggle_button.click()
        self.assertFalse(chat.isVisible())
        self.assertTrue(c.isVisible())
        self.assertTrue(c.history_window.isVisible())
        self.host.pet.hide()
        self.assertFalse(c.isVisible())
        self.host.pet.show()
        self.assertTrue(c.isVisible())

    def test_exploration_and_existing_tasks_cannot_operate_together(self):
        chat = self.host.chat
        chat.exploration_active.emit(True)
        with self.assertRaisesRegex(RuntimeError, '已有任务'):
            self.tasks.start_task('fixture')
        chat.exploration_active.emit(False)
        self.start_fixture()
        chat.target_window = WindowInfo(123, '目标', 0, 0, 800, 600)
        chat._show_chat()
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker:
            chat._send_message('自动完成')
            worker.assert_not_called()
        self.assertIn('先停止', chat.conversation.entries[-1]['text'])

    def test_finished_exploration_can_return_to_task_list(self):
        chat = self.open_chat()
        chat.conversation.add_message('user', '完成目标')
        chat.conversation.add_message('assistant', '目标完成，技能已保存')
        self.host._sync_chat_visibility()
        chat.return_button.click()
        self.assertIsNone(chat.current_work)
        self.assertFalse(chat.conversation.isVisible())
        self.assertTrue(self.presentation.has_bubbles)
        self.assertEqual(len(chat.conversation.entries), 2)

    def test_exploration_click_uses_themed_pet_feedback(self):
        with patch('huanzhizhi.tasks.component.physical_to_qt_global', return_value=QtCore.QPoint(120, 140)):
            self.host.chat.click_point.emit(240, 280)
        actor = self.host.pet._animation
        self.assertTrue(actor.visual_active)
        self.assertEqual(actor.fingertip, QtCore.QPointF(120, 140))
        self.assertFalse(self.host.pet.point_overlay.isVisible())

    def test_pet_click_toggles_conversation_and_preserves_messages_and_draft(self):
        chat = self.open_chat()
        c = chat.conversation
        c.add_message('user', '持续通关')
        c.add_message('assistant', '本轮观察已结束')
        c.input.setPlainText('尚未发送的补充')
        self.host.pet.clicked.emit()
        self.assertFalse(self.presentation.has_bubbles)
        self.assertFalse(c.isVisible())
        c.update_message(1, '已整理待识别元素')
        self.host._sync_chat_visibility()
        self.assertFalse(c.isVisible())
        self.host.pet.clicked.emit()
        self.assertTrue(c.isVisible())
        self.assertEqual(len(c.entries), 2)
        self.assertEqual(c.input.toPlainText(), '尚未发送的补充')
        c.stop_button.click()
        self.assertTrue(self.presentation.has_bubbles)

    def test_pet_click_restores_window_choice_before_selection(self):
        chat = self.host.chat
        chat.expand()
        self.host.pet.clicked.emit()
        self.assertFalse(chat.isVisible())
        self.host.pet.clicked.emit()
        self.assertTrue(chat.isVisible())
        self.assertFalse(self.presentation.has_bubbles)

    def test_empty_conversation_can_return_to_tasks_and_resume_draft(self):
        chat = self.open_chat()
        chat.conversation.input.setPlainText('未发送的目标')
        chat.conversation.stop_button.click()
        self.assertIsNone(chat.current_work)
        self.assertFalse(chat.conversation.isVisible())
        self.assertTrue(self.presentation.has_bubbles)
        chat.toggle_button.click()
        chat.window_list.setCurrentRow(0)
        with patch.object(chat, '_available_windows', return_value=(chat.target_window,)):
            chat._refresh_windows()
            chat.window_list.setCurrentRow(0)
            chat.select_window_button.click()
        self.assertTrue(chat.conversation.isVisible())
        self.assertEqual(chat.conversation.input.toPlainText(), '未发送的目标')

    def test_retained_button_listens_for_outside_click_with_mouse_point_disabled(self):
        self.host.pet.set_mouse_point_visible(False)
        mouse = self.tasks._mouse
        with patch.object(mouse, '_button_state_provider', return_value=False) as down, \
                patch.object(mouse, '_cursor_provider', return_value=QtCore.QPoint(-100, -100)):
            self.host.pet.clicked.emit()
            self.host.chat.expand()
            self.host.chat.collapse()
            self.assertTrue(mouse.enabled)
            down.return_value = True
            mouse.poll_once()
            self.assertFalse(self.host.chat.toggle_button.isVisible())
            self.assertFalse(mouse.enabled)

    def test_dragging_chat_header_moves_and_saves_the_pet_with_the_whole_group(self):
        area = self.app.primaryScreen().availableGeometry()
        window = self.host.pet.window
        window.move(area.right() - window.width() - 80, area.bottom() - window.height() - 80)
        window.moved.emit(window.pos())
        self.host.pet.clicked.emit()
        self.host.chat.expand()
        self.app.processEvents()
        before = (window.pos(), self.host.chat.pos(), self.host.chat.toggle_button.pos())
        delta = QtCore.QPoint(-30, -25)
        self._drag_widget(self.host.chat.header, QtCore.QPoint(20, 4), delta)
        self.assertEqual(window.pos(), before[0] + delta)
        self.assertEqual(self.host.chat.pos(), before[1] + delta)
        self.assertEqual(self.host.chat.toggle_button.pos(), before[2] + delta)
        self.assertEqual(self.host.settings.get_pet_position(), window.pos())
        self.assertTrue(self.host.chat.expanded)
        self.assertFalse(self.presentation.has_bubbles)

    def test_window_choice_blank_space_drags_but_selectable_title_does_not(self):
        self.host.chat.expand()
        self.app.processEvents()
        chat, pet = self.host.chat, self.host.pet.window
        before = pet.pos()
        self._drag_widget(chat.card, QtCore.QPoint(5, 5), QtCore.QPoint(20, 15))
        self.assertEqual(pet.pos(), before + QtCore.QPoint(20, 15))
        before = pet.pos()
        self._drag_widget(chat.title_label, QtCore.QPoint(1, 10), QtCore.QPoint(60, 0))
        self.assertEqual(pet.pos(), before)
        self.assertTrue(chat.title_label.hasSelectedText())

    def test_chat_resize_keeps_pet_still_and_no_independent_offset_is_retained(self):
        self.host.pet.clicked.emit()
        self.host.chat.expand()
        self.app.processEvents()
        chat, pet = self.host.chat, self.host.pet.window
        before_pet, before_size = pet.pos(), chat.size()
        corner = QtCore.QPoint(chat.width() - 2, chat.height() - 2)
        self._drag_widget(chat, corner, QtCore.QPoint(40, 20))
        self.assertEqual(pet.pos(), before_pet)
        self.assertEqual(chat.size(), before_size + QtCore.QSize(40, 20))
        anchored = chat.pos()
        chat.follow_pet(self.host.pet.bubble_geometry())
        self.assertEqual(chat.pos(), anchored)
        self._drag_widget(chat.header, QtCore.QPoint(20, 4), QtCore.QPoint(2, 1))
        self.assertEqual(pet.pos(), before_pet)
        self.assertFalse(self.presentation.has_bubbles)

    def _drag_widget(self, widget, local_start, delta):
        origin = widget.mapToGlobal(local_start)
        button = QtCore.Qt.MouseButton.LeftButton
        for kind, point, changed, held in (
            (QtCore.QEvent.Type.MouseButtonPress, origin, button, button),
            (QtCore.QEvent.Type.MouseMove, origin + delta, QtCore.Qt.MouseButton.NoButton, button),
            (QtCore.QEvent.Type.MouseButtonRelease, origin + delta, button, QtCore.Qt.MouseButton.NoButton),
        ):
            event = QtGui.QMouseEvent(kind, widget.mapFromGlobal(point), point, changed, held,
                                     QtCore.Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(widget, event)
        self.app.processEvents()

    def test_original_tray_and_restart_signal(self):
        self.assertEqual(self.host.tray._settings_action.text(),'设置')
        self.assertEqual([action.text() for action in self.host.tray._menu.actions()],
                         ["设置", "收起之之", "", "退出"])
        with patch.object(self.host,'shutdown') as shutdown:
            self.host.pet.restart_requested.emit()
            self.assertTrue(self.host.restart_requested)
            shutdown.assert_called_once()

class MigratedTaskTests(unittest.TestCase):
    def test_catalog_and_parameter_limits_without_installed_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(TaskCatalog(root / 'tasks').reload(), ())
            entry = root / 'tasks/测试技能/task.py'
            entry.parent.mkdir(parents=True)
            entry.write_text('# Metadata discovery must not execute this file.', encoding='utf-8')
            write_skill_manifest(entry, parameters=[dict(key='interval_ms', label='间隔',
                default=300, minimum=100, maximum=1000)])
            task, = TaskCatalog(root / 'tasks').reload()
            self.assertEqual(validate_values(task.parameters, {}), {'interval_ms': 300})
            with self.assertRaises(ValueError):
                validate_values(task.parameters, {'interval_ms': 50})


class SettingsRenameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_theme_startup_defaults_and_legacy_migration(self):
        for saved, expected in ((None, 'ufo'), ('original', 'ufo'),
                                ('ufo', 'ufo'), ('broom', 'broom')):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / 'settings.ini'
                raw = QtCore.QSettings(str(path), QtCore.QSettings.Format.IniFormat)
                if saved is not None:
                    raw.setValue('desktopPetTheme', saved)
                    raw.sync()
                host = HuanZhiZhiApplication(self.app, project_root=root, settings_path=path)
                try:
                    self.assertEqual(host.pet.theme, expected)
                    self.assertEqual(host.pet._animation.theme, expected)
                    self.assertFalse(host.pet.window._pixmap.isNull())
                    if saved is not None:
                        raw.sync()
                        self.assertEqual(raw.value('desktopPetTheme'), expected)
                finally:
                    host._close_components()
                    self.app.processEvents()

    def check_migration(self, *, existing=False, explicit=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / 'HuanKeZhi' / 'settings.ini'
            legacy.parent.mkdir()
            legacy.write_text('[General]\nmarker=legacy\n', encoding='utf-8')
            original = legacy.read_bytes()
            current = root / 'HuanZhiZhi' / 'settings.ini'
            if existing:
                current.parent.mkdir()
                current.write_text('[General]\nmarker=current\n', encoding='utf-8')
            with patch.object(QtCore.QStandardPaths, 'writableLocation', return_value=str(current.parent)):
                host = HuanZhiZhiApplication(self.app, project_root=root,
                                            settings_path=current if explicit else None)
                try:
                    marker = QtCore.QSettings(str(current), QtCore.QSettings.Format.IniFormat).value('marker')
                    self.assertEqual(marker, 'current' if existing else None if explicit else 'legacy')
                    self.assertEqual(legacy.read_bytes(), original)
                finally:
                    host._close_components()
                    self.app.processEvents()

    def test_previous_name_settings_are_copied(self):
        self.check_migration()

    def test_existing_settings_are_not_overwritten(self):
        self.check_migration(existing=True)

    def test_explicit_settings_do_not_migrate(self):
        self.check_migration(explicit=True)

if __name__=='__main__':unittest.main()
