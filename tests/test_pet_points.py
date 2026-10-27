import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets
from tests.pet_factory import create_test_pet


class PetPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.pet = create_test_pet(mouse_point_visible=True)
        self.pet.show()
        self.actor = self.pet._animation

    def tearDown(self):
        self.pet.close()
        for window in (self.pet.window, self.pet.speech_bubble, self.pet.point_overlay):
            window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_click_feedback_reuses_pet_for_all_themes(self):
        for theme in ("ufo", "broom"):
            self.pet.set_theme(theme)
            for x, y in ((300, 400), (500, 600), (700, 800)):
                self.pet.show_point_at_click(x, y)
                self.assertEqual(self.actor.fingertip, QtCore.QPointF(x, y))
                self.assertTrue(self.actor.visual_active)
                self.assertFalse(self.pet.point_overlay.isVisible())
            start = self.actor._clock()
            for step in range(1, 601):
                self.actor.advance(start + step*.02)
            self.assertFalse(self.actor.visual_active)

    def test_manual_drag_holds_moves_and_releases_the_pet(self):
        self.pet.begin_manual_point_at_click(300, 400)
        self.assertTrue(self.actor._held_feedback)
        self.pet.move_manual_point_at_click(500, 600)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(500, 600))
        self.pet.release_manual_point_at_click(550, 650)
        self.assertFalse(self.actor._held_feedback)
        self.actor.advance(self.actor._clock()+10)
        self.assertFalse(self.actor.visual_active)

    def test_hide_and_disabled_click_feedback_cancel_animation(self):
        for action in (self.pet.hide, lambda: self.pet.set_mouse_point_visible(False)):
            self.pet.show()
            self.pet.set_mouse_point_visible(True)
            self.pet.begin_manual_point_at_click(300, 400)
            action()
            self.assertFalse(self.actor._held_feedback)
            self.actor.advance(self.actor._clock()+10)
            self.assertFalse(self.actor.visual_active)
            self.assertFalse(self.pet.point_overlay.isVisible())

    def test_task_hint_suppresses_click_feedback(self):
        points = (QtCore.QPoint(300, 400),)
        self.pet.show_hint(points, "click", "click here")
        self.pet.show_point_at_click(500, 600)
        self.pet.begin_manual_point_at_click(700, 800)
        self.assertEqual(self.pet.point_overlay.hint_points, points)
        self.assertEqual(self.actor._hint_points, tuple(map(QtCore.QPointF, points)))
        self.assertFalse(self.actor._held_feedback)

    def test_invalid_click_coordinates_are_rejected(self):
        for action in (self.pet.show_point_at_click, self.pet.begin_manual_point_at_click,
                       self.pet.move_manual_point_at_click, self.pet.release_manual_point_at_click):
            for coordinates in ((True, 1), (1., 2), (1, None)):
                with self.assertRaises(TypeError):
                    action(*coordinates)
