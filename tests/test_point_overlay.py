from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets
from huanzhizhi.pet.point_overlay import PointOverlayWindow, MIN_DISPLAY_SCALE, MAX_DISPLAY_SCALE


class PointOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.overlay = PointOverlayWindow()

    def tearDown(self):
        self.overlay.close()

    def test_window_is_topmost_nonactivating_and_input_transparent(self):
        flags = self.overlay.windowFlags()
        for flag in (QtCore.Qt.WindowType.WindowStaysOnTopHint,
                     QtCore.Qt.WindowType.FramelessWindowHint,
                     QtCore.Qt.WindowType.WindowDoesNotAcceptFocus,
                     QtCore.Qt.WindowType.WindowTransparentForInput):
            self.assertTrue(flags & flag)
        self.assertTrue(self.overlay.testAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(self.overlay.testAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(self.overlay.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus)

    def test_scale_clamps_and_preserves_active_hint(self):
        points = (QtCore.QPoint(200, 200),)
        self.overlay.show_hint(points, "click", "click here")
        self.overlay.set_display_scale(.56)
        self.assertEqual(self.overlay.display_scale, .56)
        self.assertEqual(self.overlay.hint_points, points)
        self.overlay.set_display_scale(.01)
        self.assertEqual(self.overlay.display_scale, MIN_DISPLAY_SCALE)
        self.overlay.set_display_scale(9.)
        self.assertEqual(self.overlay.display_scale, MAX_DISPLAY_SCALE)
        self.overlay.hide_overlay()
        self.assertFalse(self.overlay.return_idle_timer.isActive())
        self.assertFalse(self.overlay._hint_timer.isActive())
        self.assertEqual(self.overlay.hint_points, ())

    def test_invalid_scale_and_hint_inputs_are_rejected(self):
        for invalid in (True, None, "large"):
            with self.assertRaises(TypeError):
                self.overlay.set_display_scale(invalid)
        for invalid in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.overlay.set_display_scale(invalid)
        for points, kind, label in (((), "click", "x"),
                                    ((QtCore.QPoint(),), "unknown", "x"),
                                    ((QtCore.QPoint(),), "click", ""),
                                    ((QtCore.QPoint(), QtCore.QPoint()), "line", "x")):
            with self.assertRaises(ValueError):
                self.overlay.show_hint(points, kind, label)
        with self.assertRaises(TypeError):
            self.overlay.show_hint(((1, 2),), "click", "x")
