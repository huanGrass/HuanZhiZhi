"""Theme lifecycle regression tests without sending native mouse input."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from tests.pet_factory import create_test_pet


class PetThemeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.assets = Path(__file__).parents[1] / "src/huanzhizhi/assets"

    def setUp(self):
        self.pet = create_test_pet(initial_position=QtCore.QPoint(100, 100))
        self.pet.show()
        self.pet.set_theme("ufo")
        self.actor = self.pet._animation

    def tearDown(self):
        self.pet.close()
        self.pet.window.deleteLater()
        self.pet.speech_bubble.deleteLater()
        self.pet.point_overlay.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_default_is_ufo_and_original_cannot_be_selected(self):
        self.assertEqual(self.pet.theme, "ufo")
        self.assertEqual(self.actor.theme, "ufo")
        self.assertFalse(self.pet.window._pixmap.isNull())
        with self.assertRaises(ValueError):
            self.pet.set_theme("original")
        with self.assertRaises(ValueError):
            self.actor.set_theme("original")
        self.assertEqual(self.pet.theme, "ufo")
        self.assertEqual(self.actor.theme, "ufo")

    def test_manual_drag_keeps_pet_visible_and_freezes_animation(self):
        start = self.pet.window.geometry().center()
        self.pet.window.begin_drag(start)
        self.assertTrue(self.pet.window.isVisible())
        self.assertFalse(self.actor._timer.isActive())
        self.pet.window.drag_to(start + QtCore.QPoint(90, 60))
        destination = QtCore.QPoint(self.pet.window.pos())
        self.actor.advance(self.actor._clock() + 4.1)
        self.assertEqual(self.pet.window.pos(), destination)
        self.pet.window.end_drag()
        self.assertTrue(self.pet.window.isVisible())
        self.assertTrue(self.actor._timer.isActive())
        self.actor.show_feedback(QtCore.QPoint(600, 450))
        self.actor.advance(self.actor._clock() + 10)
        self.assertEqual(self.pet.window.pos(), destination)

    def test_scale_during_feedback_uses_home_center(self):
        home_center = self.pet.window.geometry().center()
        self.actor.show_feedback(QtCore.QPoint(600, 450), hold=True)
        self.pet.set_display_scale(.4)
        self.assertLessEqual((self.pet.window.geometry().center() - home_center).manhattanLength(), 2)
        scaled_home = QtCore.QPoint(self.pet.window.pos())
        self.actor.show_feedback(QtCore.QPoint(550, 400))
        self.actor.advance(self.actor._clock() + 10)
        self.assertEqual(self.pet.window.pos(), scaled_home)

    def test_hide_stops_timer_and_show_returns_to_original_home(self):
        home = QtCore.QPoint(self.pet.window.pos())
        self.actor.show_feedback(QtCore.QPoint(600, 450), hold=True)
        self.pet.hide()
        self.assertFalse(self.pet.window.isVisible())
        self.assertFalse(self.actor._timer.isActive())
        self.assertFalse(self.actor.visual_active)
        self.pet.show()
        self.assertEqual(self.pet.window.pos(), home)

    def test_line_hint_keeps_only_the_line_and_does_not_visit_its_endpoints(self):
        home = QtCore.QPoint(self.pet.window.pos())
        self.pet.show_hint((QtCore.QPoint(250, 250), QtCore.QPoint(600, 400)), 'line', '辅助线')
        self.actor.advance(self.actor._clock()+1.5)
        self.assertEqual(self.pet.window.pos(), home)
        self.assertFalse(self.actor.visual_active)
        self.assertTrue(self.pet.point_overlay.isVisible())
        self.assertEqual(self.pet.window.windowOpacity(), 1.)

    def test_startup_and_work_waiting_keep_a_stable_pose_with_a_brief_blink(self):
        now = [10.]
        self.actor._clock = lambda: now[0]
        for theme in ('ufo', 'broom'):
            self.pet.set_theme(theme)
            for state in ('thinking', 'working'):
                with self.subTest(theme=theme, state=state):
                    self.pet.set_task_state(state)
                    start = now[0]
                    poses = []
                    pivot = QtCore.QPointF(self.actor._pivot)
                    for step in range(1, 271):
                        now[0] = start + step*.02
                        # Repeated status reports should not restart the blink.
                        if step % 5 == 0:
                            self.pet.set_task_state(state)
                        self.actor.advance()
                        poses.append(self.actor.current_frame)
                        self.assertEqual(self.actor._pivot, pivot)
                    self.assertEqual(set(poses), {0, 1})
                    self.assertLessEqual(poses.count(1)*.02, .14)

    def test_display_recovery_rebuilds_same_scale_at_current_dpi(self):
        home = QtCore.QPoint(self.pet.window.pos())
        size = self.pet.window.size()
        self.actor.show_feedback(QtCore.QPoint(600, 450), hold=True)
        with patch.object(self.pet.window, "devicePixelRatioF", return_value=2.):
            self.pet.restore_display()
            self.assertEqual(self.pet.window._pixmap.devicePixelRatio(), 2.)
            self.assertEqual(self.pet.window.size(), size)
            self.assertEqual(self.pet.window.pos(), home)
            self.assertFalse(self.actor.visual_active)
            self.assertTrue(all(key[-1] == 2. for key in self.actor._frames))


if __name__ == "__main__":
    unittest.main()
