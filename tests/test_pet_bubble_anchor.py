"""Attached UI follows pet travel without following individual sprite poses."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets

from huanzhizhi.application import HuanZhiZhiApplication
from huanzhizhi.tasks.presentation import TaskListItem


class PetBubbleAnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'tasks').mkdir()
        watcher = patch('huanzhizhi.tasks.component.GlobalMouseWatcher')
        watcher.start()
        self.addCleanup(watcher.stop)
        self.host = HuanZhiZhiApplication(self.app, project_root=root, settings_path=root / 'settings.ini')
        with patch.object(self.host.settings, 'check_agent_configuration'):
            self.host.settings.set_ai_enabled(True)
        self.pet = self.host.pet
        self.presentation = self.host.tasks.presentation
        self.pet.show()

    def tearDown(self):
        self.host._close_components()
        self.app.processEvents()
        self.temp.cleanup()

    def prepare_theme(self, theme, position=QtCore.QPoint(280, 180)):
        self.pet.set_theme(theme)
        actor = self.pet._animation
        actor.cancel(return_home=False)
        actor._timer.stop()
        self.pet.window.move(position)
        actor.set_home(position)
        actor._render(0, actor._home_pivot(), False)
        self.host._reposition_chat()
        return actor

    def snapshot(self):
        return (self.pet.speech_bubble.geometry(), self.pet.speech_bubble.tail_edge,
                self.presentation._strip.geometry(),
                tuple(bubble.geometry() for bubble in self.presentation._bubbles),
                self.host.chat.toggle_button.geometry(), self.host.chat._pet_rect)

    def test_stationary_pose_and_turn_keep_speech_status_and_task_bubbles_fixed(self):
        for theme in ('ufo', 'broom'):
            with self.subTest(theme=theme):
                actor = self.prepare_theme(theme)
                self.pet.show_notification('气泡位置应保持稳定', duration_ms=60000)
                self.presentation.set_running(True, 'fixture', '运行中', '定位测试', '等待')
                self.presentation.close_bubbles()
                self.presentation.handle_pet_clicked()
                for bubble in self.presentation._bubbles:
                    bubble._stop_pop_animation()
                self.assertTrue(self.presentation.has_bubbles)
                self.assertTrue(self.pet.speech_bubble.isVisible())
                pivot = QtCore.QPointF(actor._pivot)
                baseline = self.snapshot()
                for frame, mirrored in ((1, False), (2, False), (5, False), (9, False),
                                        (10, False), (12, False), (13, False), (22, False),
                                        (0, True), (1, True), (0, False)):
                    with self.subTest(frame=frame, mirrored=mirrored):
                        actor._render(frame, pivot, mirrored)
                        self.assertEqual(self.snapshot(), baseline)

    def test_stationary_blink_near_top_edge_does_not_flip_speech_tail(self):
        actor = self.prepare_theme('broom', QtCore.QPoint(200, 78))
        self.pet.show_notification('气泡定位复现：姿势变化时应保持稳定', duration_ms=60000)
        baseline = (self.pet.speech_bubble.geometry(), self.pet.speech_bubble.tail_edge)
        pivot = QtCore.QPointF(actor._pivot)
        for frame in (1, 2, 0):
            actor._render(frame, pivot, False)
            self.assertEqual((self.pet.speech_bubble.geometry(), self.pet.speech_bubble.tail_edge), baseline)

    def test_chat_composer_and_message_bubbles_keep_the_same_pose_anchor(self):
        actor = self.prepare_theme('broom')
        self.host.chat._work_view = 'conversation'
        conversation = self.host.chat.conversation
        conversation.add_message('assistant', '聊天气泡应保持稳定')
        self.host._sync_chat_visibility()
        conversation._motion.stop()
        conversation._advance_layout(1.)
        self.assertTrue(conversation.isVisible())

        def state():
            return (self.host.chat._pet_rect, conversation.geometry(),
                    tuple((bubble.geometry(), bubble.tail_edge) for bubble in conversation.bubbles
                          if bubble.isVisible()), self.host.chat.toggle_button.geometry())

        baseline = state()
        pivot = QtCore.QPointF(actor._pivot)
        for frame, mirrored in ((1, False), (2, False), (12, False), (22, True), (0, True)):
            actor._render(frame, pivot, mirrored)
            # Visibility refresh also uses the same anchor as the move signal.
            self.host._sync_chat_visibility()
            self.assertEqual(state(), baseline)

    def test_broom_task_menu_sits_beside_body_center_and_stays_fixed(self):
        actor = self.prepare_theme('broom')
        self.presentation.set_tasks(TaskListItem(str(index), name) for index, name in
                                    enumerate(('围棋对弈', '砖了个砖提示', '通关羊了个羊')))
        self.presentation.handle_pet_clicked()
        for bubble in self.presentation._bubbles:
            bubble._stop_pop_animation()
        bubbles = self.presentation._bubbles
        full_rect = self.pet.bubble_geometry()
        # Keep the columns close to the character and vertically centered;
        # covering a little of the broom is intentional.
        self.assertGreater(bubbles[0].geometry().right(), full_rect.left()+20)
        self.assertLess(bubbles[1].x(), full_rect.right()-20)
        self.assertLessEqual(abs(bubbles[1].geometry().center().y()-full_rect.center().y()), 1)
        baseline = tuple(b.geometry() for b in bubbles)
        pivot = QtCore.QPointF(actor._pivot)
        for frame, mirrored in ((0, False), (1, False), (0, True), (1, True)):
            actor._render(frame, pivot, mirrored)
            self.assertEqual(tuple(b.geometry() for b in bubbles), baseline)

        delta = QtCore.QPoint(30, 25)
        start = self.pet.window.geometry().center()
        self.pet.window.begin_drag(start)
        self.pet.window.drag_to(start+delta)
        self.assertEqual(tuple(b.geometry() for b in bubbles),
                         tuple(rect.translated(delta) for rect in baseline))
        self.pet.window.end_drag()

    def test_crowded_small_or_offscreen_broom_menus_stay_close_and_on_screen(self):
        for scale, count, top in ((.32, 10, 180), (.19, 3, 180),
                                  (.32, 3, -50), (.32, 3, -92)):
            with self.subTest(scale=scale, count=count, top=top):
                self.presentation.close_bubbles()
                self.pet.set_display_scale(scale)
                self.prepare_theme('broom', QtCore.QPoint(280, top))
                self.presentation.set_tasks(TaskListItem(str(index), f'任务 {index}')
                                            for index in range(count))
                self.presentation.handle_pet_clicked()
                full_rect = self.pet.bubble_geometry()
                area = self.app.primaryScreen().availableGeometry()
                for index, bubble in enumerate(self.presentation._bubbles):
                    bubble._stop_pop_animation()
                    self.assertTrue(area.contains(bubble.geometry()))
                    if index % 2 == 0:
                        self.assertGreater(bubble.geometry().right(), full_rect.left())
                        self.assertLess(bubble.geometry().right(), full_rect.center().x())
                    else:
                        self.assertLess(bubble.x(), full_rect.right())
                        self.assertGreater(bubble.x(), full_rect.center().x())

    def test_travel_keeps_runtime_panel_home_and_manual_drag_relocates_it(self):
        actor = self.prepare_theme('ufo')
        self.pet.show_notification('跟随移动', duration_ms=60000)
        self.presentation.set_running(True, 'fixture', '运行中', '定位测试', '等待')
        initial = self.presentation._provided_pet_rect()
        speech = self.pet.speech_bubble.pos()
        strip = self.presentation._strip.pos()
        delta = QtCore.QPoint(30, 25)
        actor._render(0, actor._pivot+QtCore.QPointF(delta), False)
        self.assertEqual(self.presentation._provided_pet_rect(), initial.translated(delta))
        self.assertEqual(self.host.chat._pet_rect, initial.translated(delta))
        self.assertEqual(self.pet.speech_bubble.pos(), speech+delta)
        self.assertEqual(self.presentation._strip.pos(), strip)

        start = self.pet.window.geometry().center()
        initial = self.presentation._provided_pet_rect()
        speech = self.pet.speech_bubble.pos()
        self.pet.window.begin_drag(start)
        self.pet.window.drag_to(start+delta)
        self.assertEqual(self.presentation._provided_pet_rect(), initial.translated(delta))
        self.assertEqual(self.host.chat._pet_rect, initial.translated(delta))
        self.assertEqual(self.pet.speech_bubble.pos(), speech+delta)
        self.pet.window.end_drag()
        self.assertNotEqual(self.presentation._strip.pos(), strip)


if __name__ == '__main__':
    unittest.main()
