import os
import threading
import tempfile
from pathlib import Path
from tests.skill_factory import write_skill_manifest
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from huanzhizhi.pet.chat_window import ChatWindow, _readable_icon
from huanzhizhi.platform.target_window import DesktopWindowsError, Win32DesktopApi, WindowInfo
from huanzhizhi.settings.component import SettingsComponent


class ChatWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = SettingsComponent(Path(directory.name) / 'settings.ini')
        self.addCleanup(settings.close)
        with patch.object(settings, 'check_agent_configuration'):
            settings.set_ai_enabled(True)
        self.window = ChatWindow(settings=settings)
        self.window.tasks_root = Path(directory.name) / 'tasks'
        self.window.tasks_root.mkdir()
        self.target = WindowInfo(123, '<b>目标应用</b>', 0, 0, 800, 600)
        self.enumeration = patch.object(self.window, '_available_windows', return_value=(self.target,))
        self.windows = self.enumeration.start()
        self.addCleanup(self.enumeration.stop)
        restoration = patch.object(Win32DesktopApi, 'restore', side_effect=lambda window: window)
        self.restore_window = restoration.start()
        self.addCleanup(restoration.stop)
        area = self.app.primaryScreen().availableGeometry()
        self.pet = QtCore.QRect(area.right() - 110, area.bottom() - 100, 80, 90)
        self.window.open_for_pet(self.pet)
        self.app.processEvents()

    def tearDown(self):
        self.window.stop_exploration_and_wait()
        self.window.hide_for_pet()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def select_target(self):
        self.window.expand()
        self.window.window_list.setCurrentRow(0)
        QtTest.QTest.mouseClick(self.window.select_window_button, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def test_window_selection_opens_original_conversation_before_first_message(self):
        self.assertFalse(self.window.isVisible())
        self.select_target()
        conversation = self.window.conversation
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.window.current_work, 'conversation')
        self.assertEqual(self.window.target_window, self.target)
        self.assertTrue(conversation.isVisible())
        self.assertTrue(conversation.composer.isVisible())
        self.assertTrue(conversation.input.hasFocus())
        self.assertEqual(conversation.entries, [])
        self.assertEqual(conversation.history_button.text(), '历史')
        self.assertEqual(conversation.target_label.toolTip(), self.target.title)
        self.assertFalse(conversation.send_button.isEnabled())
        self.assertFalse(any(b.isVisible() for b in conversation.bubbles))

    def test_collapse_retains_target_and_unsent_goal(self):
        self.select_target()
        conversation = self.window.conversation
        conversation.input.setPlainText('打开活动页面并领取每日奖励')
        conversation.close_button.click()
        self.assertFalse(conversation.isVisible())
        self.window.toggle_button.click()
        self.assertTrue(conversation.isVisible())
        self.assertEqual(self.window.target_window, self.target)
        self.assertEqual(conversation.input.toPlainText(), '打开活动页面并领取每日奖励')

    def test_create_without_window_opens_chat_and_submits_without_target(self):
        self.windows.return_value = ()
        self.window.expand()
        self.assertFalse(self.window.select_window_button.isEnabled())
        self.window.no_window_button.click()
        self.assertIsNone(self.window.target_window)
        self.assertTrue(self.window.conversation.isVisible())
        self.assertEqual(self.window.conversation.target_label.toolTip(), '无需窗口')
        self.restore_window.assert_not_called()
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            self.window.conversation.input.setPlainText('做一个桌宠排球')
            self.window.conversation.send_button.click()
            self.assertIsNone(worker_type.call_args.args[0])
            self.assertEqual(worker_type.call_args.args[1], '做一个桌宠排球')
            worker_type.return_value.start.assert_called_once()
            worker_type.return_value.stop_error = None
            self.window._exploration_finished()

    def test_switching_between_window_and_no_window_clears_old_context(self):
        self.select_target()
        self.window.conversation.add_message('user', '旧窗口需求')
        self.window._creation_directory = Path('old-draft')
        self.window.expand()
        self.window.no_window_button.click()
        self.assertIsNone(self.window._creation_directory)
        self.assertEqual(self.window.conversation.entries, [])
        self.window.conversation.input.setPlainText('桌面小游戏')
        self.window.conversation.close_button.click()
        self.window.toggle_button.click()
        self.assertEqual(self.window.conversation.input.toPlainText(), '桌面小游戏')
        self.window.conversation.add_message('user', '无窗口需求')
        self.select_target()
        self.assertEqual(self.window.target_window, self.target)
        self.assertEqual(self.window.conversation.entries, [])

    def test_selection_uses_restored_dimensions(self):
        restored = WindowInfo(self.target.hwnd, self.target.title, 10, 20, 1024, 768)
        self.restore_window.side_effect = lambda window: restored
        self.select_target()
        self.restore_window.assert_called_once_with(self.target)
        self.assertEqual(self.window.target_window, restored)

    def test_failed_restore_keeps_window_selection_open(self):
        self.restore_window.side_effect = DesktopWindowsError('restore failed')
        self.select_target()
        self.assertIsNone(self.window.target_window)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.conversation.isVisible())
        self.assertIn('无法恢复', self.window.window_status.text())

    def test_closed_window_and_enumeration_failure_stay_in_window_choice(self):
        self.window.expand()
        self.window.window_list.setCurrentRow(0)
        self.windows.return_value = ()
        self.window.select_window_button.click()
        self.assertIsNone(self.window.target_window)
        self.assertEqual(self.window.pages.currentWidget(), self.window.window_page)
        self.assertIn('已不可用', self.window.window_status.text())
        self.assertFalse(self.window.select_window_button.isEnabled())
        self.windows.side_effect = DesktopWindowsError('test enumeration failure')
        self.window.refresh_button.click()
        self.assertIn('无法读取', self.window.window_status.text())
        self.assertFalse(self.window.select_window_button.isEnabled())

    def test_switch_window_opens_empty_original_conversation(self):
        self.select_target()
        self.window.conversation.add_message('user', '旧目标')
        second = WindowInfo(456, '第二个应用', 10, 10, 800, 600)
        self.windows.return_value = (second,)
        self.window.expand()
        self.window.window_list.setCurrentRow(0)
        self.window.select_window_button.click()
        self.assertEqual(self.window.target_window, second)
        self.assertEqual(self.window.conversation.entries, [])
        self.assertTrue(self.window.conversation.input.isVisible())

    def test_goal_entry_does_not_execute(self):
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker:
            self.select_target()
            QtTest.QTest.keyClicks(self.window.conversation.input, 'Open rewards')
            self.assertEqual(self.window.conversation.input.toPlainText(), 'Open rewards')
            worker.assert_not_called()

    def test_dock_sends_with_enter_and_keeps_multiline_draft_while_busy(self):
        self.select_target()
        c = self.window.conversation
        sent = []
        c.submitted.disconnect()
        c.submitted.connect(sent.append)
        c.input.setPlainText('领取奖励')
        self.assertTrue(c.send_button.isEnabled())
        c.input.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        QtTest.QTest.keyClick(c.input, QtCore.Qt.Key.Key_Return, QtCore.Qt.KeyboardModifier.ShiftModifier)
        c.input.insertPlainText('只领取免费的')
        self.assertEqual(sent, [])
        QtTest.QTest.keyClick(c.input, QtCore.Qt.Key.Key_Return)
        self.assertEqual(sent, ['领取奖励\n只领取免费的'])
        collapsed = QtTest.QSignalSpy(c._motion.finished)
        c.set_busy(True)
        self.assertFalse(c.input.isVisible())
        self.assertFalse(c.send_button.isVisible())
        self.assertTrue(collapsed.wait(1000))
        self.assertEqual(c.composer.height(), 95)
        self.assertTrue(c.history_button.isVisible())
        self.assertTrue(c.stop_button.isVisible())
        self.assertEqual(c.stop_button.text(), '停止')
        self.assertFalse(c.send_button.isEnabled())
        QtTest.QTest.keyClick(c.input, QtCore.Qt.Key.Key_Return)
        self.assertEqual(len(sent), 1)
        self.assertEqual(c.input.toPlainText(), '领取奖励\n只领取免费的')
        expanded = QtTest.QSignalSpy(c._motion.finished)
        c.set_busy(False)
        self.assertTrue(c.input.isVisible())
        self.assertTrue(c.send_button.isVisible())
        self.assertTrue(expanded.wait(1000))
        self.assertEqual(c.composer.height(), 155)
        self.assertEqual(c.stop_button.text(), '返回')
        self.assertTrue(c.send_button.isEnabled())

    def test_dock_does_not_submit_while_ime_is_composing(self):
        self.select_target()
        c = self.window.conversation
        sent = []
        c.submitted.disconnect()
        c.submitted.connect(sent.append)
        c.input.setPlainText('目标')
        self.app.sendEvent(c.input, QtGui.QInputMethodEvent('jiang', []))
        QtTest.QTest.keyClick(c.input, QtCore.Qt.Key.Key_Return)
        self.assertEqual(sent, [])
        self.app.sendEvent(c.input, QtGui.QInputMethodEvent('', []))

    def test_dock_keeps_controls_inside_one_frame_with_long_target_and_text(self):
        self.select_target()
        c = self.window.conversation
        c.set_target('很长的窗口标题' * 30)
        c.input.setPlainText('多行目标\n' * 30)
        QtTest.QTest.qWait(20)
        for widget in (c.input, c.send_button, c.history_button, c.stop_button, c.close_button):
            bounds = QtCore.QRect(widget.mapTo(c.composer, QtCore.QPoint()), widget.size())
            self.assertTrue(c.composer.rect().contains(bounds))
        self.assertGreater(c.input.verticalScrollBar().maximum(), 0)
        self.assertEqual(c.composer.size(), QtCore.QSize(334, 155))
        self.assertIn('…', c.target_label.text())

    def wait_for_observation(self):
        for _ in range(600):
            if self.window._exploration is None:
                return
            QtTest.QTest.qWait(10)
        self.fail('观察任务没有结束')

    def test_intake_handoff_keeps_one_visible_conversation(self):
        import json
        from tests.test_skill_intake import report, REQUEST, CONFIRMATION
        self.window.expand()
        self.window.no_window_button.click()
        calls = []
        def investigate(worker, turn, prompt):
            calls.append(('intake', worker.thread_id))
            value = report('needs_input' if worker.goal == REQUEST else 'ready')
            if worker.goal != REQUEST:
                value['resolved_questions'] = [dict(id='play', user_turn=2, quote=CONFIRMATION)]
            (turn / 'intake.json').write_text(json.dumps(value), encoding='utf-8')
            worker.thread_id = 'intake-thread'
            return value['message']
        def develop(worker):
            calls.append(('development', worker.thread_id))
            worker.thread_id = 'development-thread'
            return '正在按确认的方案实现。'
        with patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                patch('huanzhizhi.skill_creation.develop', side_effect=develop):
            for message in (REQUEST, CONFIRMATION):
                self.window.conversation.input.setPlainText(message)
                self.window.conversation.send_button.click()
                self.wait_for_observation()
        self.assertEqual(calls, [('intake', None), ('intake', 'intake-thread'), ('development', None)])
        entries = self.window.conversation.entries
        self.assertEqual([e['text'] for e in entries if e['role'] == 'user'], [REQUEST, CONFIRMATION])
        self.assertTrue(any('是否采用' in e['text'] for e in entries))
        self.assertEqual(entries[-1]['text'], '正在按确认的方案实现。')
        self.assertTrue(self.window.conversation.isVisible())
        self.assertEqual(self.window._creation_thread_id, 'development-thread')

    def test_failed_handoff_launch_keeps_draft_without_thread_id(self):
        import json
        from tests.test_skill_intake import report
        self.window.expand()
        self.window.no_window_button.click()
        def investigate(worker, turn, prompt):
            (turn / 'intake.json').write_text(json.dumps(report()), encoding='utf-8')
            worker.thread_id = 'intake-thread'
            return '需求明确'
        with patch('huanzhizhi.skill_creation.run_prompt', side_effect=investigate), \
                patch('huanzhizhi.skill_creation.develop', side_effect=RuntimeError('failed launch')):
            self.window.conversation.input.setPlainText('新增一个桌宠投篮玩法')
            self.window.conversation.send_button.click()
            self.wait_for_observation()
        self.assertIsNone(self.window._creation_thread_id)
        self.assertTrue((self.window._creation_directory / 'creation_flow.json').is_file())

    @patch('huanzhizhi.skill_creation.CreationWorker._prepare_creation', return_value=True)
    def test_creation_followup_reuses_codex_thread_and_directory(self, _intake):
        self.select_target()
        self.window.conversation.input.setPlainText('领取奖励')
        calls = []
        def develop(worker):
            calls.append((worker.goal, worker.directory, worker.thread_id))
            worker.thread_id = 'thread-for-this-creation'
            return '需要补充。\n请说明领取范围。'
        with patch('huanzhizhi.skill_creation.develop', side_effect=develop):
            self.window.conversation.send_button.click()
            self.assertFalse(self.window.isVisible())
            self.assertTrue(self.window.conversation.isVisible())
            self.assertEqual(self.window.conversation.input.toPlainText(), '')
            self.wait_for_observation()
            self.assertIn('领取范围', self.window.conversation.entries[-1]['text'])
            self.window.conversation.input.setPlainText('只领取免费的奖励')
            self.window.conversation.send_button.click()
            self.wait_for_observation()
            self.assertEqual(calls[1], ('只领取免费的奖励', calls[0][1], 'thread-for-this-creation'))
            self.window._reset_conversation()
            self.assertIsNone(self.window._creation_directory)
            self.assertIsNone(self.window._creation_thread_id)

    def test_edit_followup_reuses_draft_and_new_save_starts_from_updated_skill(self):
        import shutil
        source = self.window.tasks_root / '测试技能'
        source.mkdir()
        (source/'task.py').write_text('from huanzhizhi.task_sdk import describe\ndescribe(__file__)\n', encoding='utf-8')
        write_skill_manifest(source/'task.py')
        test_version = self.window.tasks_root.parent / 'test_tasks/测试技能'
        shutil.copytree(source, test_version)
        self.window.edit_skill(source)
        self.assertIsNone(self.window.target_window)
        self.assertTrue(self.window.conversation.isVisible())
        self.assertIn('修改', self.window.conversation.target_label.text())
        self.assertIn('修改', self.window.conversation.input.placeholderText())
        calls = []
        def develop(worker):
            calls.append((worker.source_skill, worker.directory, worker.thread_id))
            worker.thread_id = 'edit-thread'
            if len(calls) == 2:
                shutil.copytree(worker.directory/'working/测试技能', worker.directory/'output/测试技能')
                return '保存完成'
            return '请补充要求'
        with patch('huanzhizhi.skill_creation.develop', side_effect=develop):
            for goal in ('修改提示', '改成完成了', '再增加说明'):
                self.window.conversation.input.setPlainText(goal)
                self.window.conversation.send_button.click()
                self.wait_for_observation()
        self.assertEqual(calls[1], (source.resolve(), calls[0][1], 'edit-thread'))
        self.assertNotEqual(calls[2][1], calls[1][1])
        self.assertIsNone(calls[2][2])
        self.assertEqual(calls[2][0], source.resolve())
        self.assertTrue(any('已更新并保存到任务列表的“正式”分组' in entry['text'] for entry in self.window.conversation.entries))
        self.window.conversation.input.setPlainText('未发送要求')
        self.window.conversation.close_button.click()
        self.window.toggle_button.click()
        self.assertEqual(self.window.toggle_button.text(), '修改技能')
        self.window._return_to_tasks()
        self.window.edit_skill(source)
        self.assertEqual(self.window.conversation.input.toPlainText(), '未发送要求')
        self.window._return_to_tasks()
        self.window.expand()
        self.assertIsNone(self.window._editing_skill)
        self.assertIsNone(self.window._creation_directory)
        self.assertEqual(self.window.conversation.entries, [])

    @patch('huanzhizhi.skill_creation.CreationWorker._prepare_creation', return_value=True)
    def test_stopping_observation_discards_late_results(self, _intake):
        self.select_target()
        clicks = []
        self.window.click_point.connect(lambda x, y: clicks.append((x, y)))
        self.window.conversation.input.setPlainText('领取奖励')
        entered = threading.Event()
        def develop(worker):
            entered.set()
            while not worker.isInterruptionRequested():
                threading.Event().wait(0.01)
            worker.thread_id = 'interrupted-thread'
            worker.click_point.emit(30, 40)
            return '不能显示的过期结果'
        with patch('huanzhizhi.skill_creation.develop', side_effect=develop):
            self.window.conversation.send_button.click()
            self.assertTrue(entered.wait(2))
            self.window.conversation.stop_button.click()
            self.wait_for_observation()
            self.assertNotIn('过期结果', str(self.window.conversation.entries))
            self.assertIn('已停止', self.window.conversation.entries[-1]['text'])
            self.assertTrue(self.window.conversation.input.isEnabled())
            self.assertFalse(self.window.conversation.send_button.isEnabled())
            self.assertEqual(self.window._creation_thread_id, 'interrupted-thread')
            self.assertEqual(clicks, [])

    @patch('huanzhizhi.skill_creation.CreationWorker._prepare_creation', return_value=True)
    def test_worker_click_reaches_chat_without_duplicate_notification(self, _intake):
        self.select_target()
        clicks = []
        self.window.click_point.connect(lambda x, y: clicks.append((x, y)))
        def develop(worker):
            worker.click_point.emit(-170, 140)
            return '已核对点击结果。'
        with patch('huanzhizhi.skill_creation.develop', side_effect=develop):
            self.window.conversation.input.setPlainText('测试点击')
            self.window.conversation.send_button.click()
            self.wait_for_observation()
        self.assertEqual(clicks, [(-170, 140)])

    def test_original_bubbles_history_and_pet_visibility_are_preserved(self):
        self.select_target()
        c = self.window.conversation
        c.add_message('user', '领取奖励')
        c.add_message('assistant', '正在寻找活动入口')
        c.add_message('assistant', '找到候选，需要进一步验证')
        self.assertTrue(any(b.isVisible() for b in c.bubbles))
        area = self.app.primaryScreen().availableGeometry()
        for corner in (area.topLeft(), area.topRight()-QtCore.QPoint(90, 0),
                       area.bottomLeft()-QtCore.QPoint(0, 100), area.bottomRight()-QtCore.QPoint(90, 100)):
            self.window.follow_pet(QtCore.QRect(corner, QtCore.QSize(80, 90)))
            self.assertTrue(area.contains(c.geometry()))
            for bubble in c.bubbles:
                if bubble.isVisible():
                    self.assertTrue(area.contains(bubble.geometry()))
            c.toggle_history()
            self.assertTrue(c.history_window.isVisible())
            self.assertTrue(area.contains(c.history_window.geometry()))
            c.toggle_history()
        self.window.hide_for_pet()
        self.assertFalse(c.isVisible())
        self.assertFalse(any(b.isVisible() for b in c.bubbles))
        self.window.open_for_pet(self.pet)
        self.assertTrue(c.isVisible())
        self.assertTrue(c.input.isVisible())

    def test_progress_error_and_stop_preserve_earlier_history(self):
        self.select_target()
        with patch('huanzhizhi.pet.chat_window.CreationWorker') as worker_type:
            self.window.conversation.input.setPlainText('领取奖励')
            self.window.conversation.send_button.click()
            worker = worker_type.return_value
            worker.stop_error = None
            initial = [dict(entry) for entry in self.window.conversation.entries]
            self.window._exploration_progress('正在查看窗口')
            self.assertEqual(self.window.conversation.entries[-1]['text'], '正在查看窗口')
            message = '账户额度不足，请检查账户余额或可用额度后重试。'
            self.window._exploration_failed(message)
            self.assertEqual(message, self.window.conversation.entries[-1]['text'])
            self.assertIn('重试', self.window.conversation.entries[-1]['text'])
            self.assertEqual(self.window.conversation.entries[:len(initial)], initial)
            self.assertEqual(len(self.window.conversation.entries), len(initial) + 2)
            self.assertFalse(self.window.isVisible())
            self.window.conversation.stop_button.click()
            worker.requestInterruption.assert_called_once()
            self.assertFalse(self.window.conversation.stop_button.isEnabled())
            self.window._exploration_finished()
            self.assertIn('已停止', self.window.conversation.entries[-1]['text'])
            self.assertEqual(len(self.window.conversation.entries), len(initial) + 4)

    def test_assistant_bubbles_preserve_content_when_ai_exceeds_length_guidance(self):
        self.select_target()
        c = self.window.conversation
        messages = [
            ('正在核对棋盘。', '正在核对棋盘。'),
            ('已识别棋盘。\n' + '需要核对的详细情况。' * 10, '已识别棋盘。'),
            ('当前棋盘是完整未揭开的 16×16 自定义局，我将通过任务运行器验证首步输入。',
             '当前棋盘是完整未揭开的 16×16 自定义局，我将通过任务运行器验证首步输入。'),
            ('首步输入已生效，首格展开出数字线索；我在确认识别精度和安全推理边界。',
             '首步输入已生效，首格展开出数字线索；我在确认识别精度和安全推理边界。'),
            ('已识别当前完整棋盘。当前虽然只有一个候选位置，尚未确认点击是否安全。',
             '已识别当前完整棋盘。当前虽然只有一个候选位置，尚未确认点击是否安全。'),
            ('字' * 30, '字' * 30),
        ]
        for text, summary in messages:
            with self.subTest(text=text):
                index = c.add_message('assistant', text)
                self.assertEqual(c.entries[index]['summary'], summary)
                self.assertEqual(c.entries[index]['text'], text)
                self.assertIn(summary, c.bubbles[-1].label.text() if len(c.entries) >= 3
                              else c.bubbles[index].label.text())
        text = '当前棋盘已识别，但尚未通关。\n需要核对的详细情况。'
        c.update_message(0, text, summary='棋盘已识别，尚未通关。')
        self.assertEqual(c.entries[0]['summary'], '棋盘已识别，尚未通关。')
        self.assertIn(text, c._history_labels[0].toPlainText())

    def test_history_wraps_long_tokens_without_clipping_other_messages(self):
        self.select_target()
        c = self.window.conversation
        texts = ['完整的中文历史消息。' * 30, 'long_identifier_' * 100,
                 'https://example.com/' + 'abcdef0123456789' * 100]
        for role, text in zip(('assistant', 'assistant', 'user'), texts):
            c.add_message(role, text)
        c.toggle_history()
        for _ in range(5):
            self.app.processEvents()
        scroll = c.history_scroll
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        for message, text in zip(c._history_labels, texts):
            self.assertIn(text, message.toPlainText())
            self.assertEqual(message.horizontalScrollBar().maximum(), 0)
            self.assertEqual(message.verticalScrollBar().maximum(), 0)
            self.assertGreater(message.height(), 100)

    def test_saved_skill_shows_one_user_facing_result_without_development_report(self):
        self.select_target()
        with patch('huanzhizhi.pet.chat_window.CreationWorker'):
            c = self.window.conversation
            c.input.setPlainText('通关当前扫雷')
            c.send_button.click()
            initial = [dict(entry) for entry in c.entries]
            self.window._skill_saved(str(self.window.tasks_root.parent / 'test_tasks/扫雷通关'))
            self.window._show_observation('已创建技能 [扫雷通关](E:/draft/output/task.py)，--describe 通过，__pycache__ 未清理。')
            self.assertEqual(c.entries[:len(initial)], initial)
            self.assertEqual(len(c.entries), len(initial) + 1)
            result = c.entries[-1]['text']
            self.assertIn('扫雷通关', result)
            self.assertIn('测试', result)
            self.assertIn('试运行', result)
            for internal in ('E:', 'task.py', '__pycache__', '--describe', 'Codex'):
                self.assertNotIn(internal, '\n'.join(entry['text'] for entry in c.entries))

    def test_running_conversation_cannot_switch_target(self):
        self.select_target()
        with patch('huanzhizhi.pet.chat_window.CreationWorker'):
            self.window.conversation.input.setPlainText('领取奖励')
            self.window.conversation.send_button.click()
            self.window.toggle_button.click()
            self.assertFalse(self.window.isVisible())
            self.assertTrue(self.window.conversation.isVisible())
            self.window._exploration_finished()

    def test_settings_failure_emits_notification_request_and_keeps_unsent_text(self):
        self.select_target()
        unavailable = QtTest.QSignalSpy(self.window.ai_unavailable)
        with patch.object(self.window, '_settings') as settings, \
                patch('huanzhizhi.pet.chat_window.CreationWorker') as worker:
            settings.get_codex_options.side_effect = ValueError('测试配置错误')
            self.window.conversation.input.setPlainText('我的目标')
            self.window.conversation.send_button.click()
            worker.assert_not_called()
        self.assertEqual(unavailable.count(), 1)
        self.assertEqual(unavailable.at(0), ['测试配置错误'])
        self.assertEqual(self.window.conversation.entries, [])
        self.assertEqual(self.window.conversation.input.toPlainText(), '我的目标')

    def test_white_native_icon_has_contrast_without_selection(self):
        source = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(source)
        painter.fillRect(10, 5, 12, 22, QtCore.Qt.GlobalColor.white)
        painter.end()
        result = _readable_icon(source).pixmap(32, 32).toImage()
        self.assertEqual(result.pixelColor(16, 16), QtGui.QColor('white'))
        backdrop = result.pixelColor(5, 16)
        self.assertEqual(backdrop.alpha(), 255)
        self.assertLess(backdrop.lightness(), 150)
        self.assertEqual(result.pixelColor(0, 0).alpha(), 0)

    def test_colored_native_icon_is_preserved(self):
        source = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(source)
        painter.fillRect(8, 8, 16, 16, QtGui.QColor('#0088cc'))
        painter.end()
        result = _readable_icon(source).pixmap(32, 32).toImage()
        self.assertEqual(result.pixelColor(16, 16), source.pixelColor(16, 16))
        self.assertEqual(result.pixelColor(5, 16).alpha(), 0)

    def test_resize_edges_and_minimum_size(self):
        self.window.expand()
        self.app.processEvents()
        initial = self.window.size()
        start = QtCore.QPoint(self.window.width() - 2, self.window.height() - 2)
        origin = self.window.mapToGlobal(start)
        QtTest.QTest.mousePress(self.window, QtCore.Qt.MouseButton.LeftButton, pos=start)
        target = start + QtCore.QPoint(75, 50)
        event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, target, origin + QtCore.QPoint(75, 50),
                                 QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.LeftButton,
                                 QtCore.Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.window, event)
        self.assertEqual(self.window.size(), initial + QtCore.QSize(75, 50))
        target = start - QtCore.QPoint(1000, 1000)
        event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, target, origin - QtCore.QPoint(1000, 1000),
                                 QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.LeftButton,
                                 QtCore.Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.window, event)
        self.assertEqual(self.window.size(), self.window.minimumSize())
        QtTest.QTest.mouseRelease(self.window, QtCore.Qt.MouseButton.LeftButton)

    def test_pet_following_and_screen_edges(self):
        before = self.window.toggle_button.pos()
        self.window.follow_pet(self.pet.translated(-20, -30))
        self.assertEqual(self.window.toggle_button.pos(), before + QtCore.QPoint(-20, -30))
        area = self.app.primaryScreen().availableGeometry()
        self.window.expand()
        self.window.follow_pet(QtCore.QRect(area.topLeft(), QtCore.QSize(80, 90)))
        self.assertTrue(area.contains(self.window.geometry()))

    def test_dock_visible_card_keeps_pet_gap_despite_shadow_margins(self):
        self.select_target()
        c = self.window.conversation
        area = self.app.primaryScreen().availableGeometry()
        for left_side in (True, False):
            x = area.right() - 110 if left_side else area.left() + 40
            pet = QtCore.QRect(x, area.center().y(), 80, 90)
            self.window.follow_pet(pet)
            card = QtCore.QRect(c.composer.mapToGlobal(QtCore.QPoint()), c.composer.size())
            gap = pet.left() - card.right() - 1 if left_side else card.left() - pet.right() - 1
            self.assertEqual(gap, 12)
            self.assertEqual(card.bottom(), pet.bottom())

    def test_space_around_creation_card_remains_transparent(self):
        self.window.expand()
        self.app.processEvents()
        point = QtCore.QPoint(1, self.window.height() // 2)
        image = self.window.grab().toImage()
        ratio = image.devicePixelRatio()
        color = image.pixelColor(round(point.x() * ratio), round(point.y() * ratio))
        self.assertGreater(color.alpha(), 0)
        self.assertLessEqual(color.alpha(), 2)


if __name__ == '__main__':
    unittest.main()
