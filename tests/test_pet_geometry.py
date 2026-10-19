import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from huanzhizhi.pet.window import PetWindow
from huanzhizhi.pet.animation import PetAnimationController


class PetGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = PetWindow()
        self.actor = PetAnimationController(self.window)

    def tearDown(self):
        self.actor.close()
        self.window.close()

    def test_sizes_scale_the_theme_proportionally(self):
        base = self.window.size() - QtCore.QSize(4, 4)
        for scale in (.32, .20, .56, 1.25):
            with self.subTest(scale=scale):
                if scale != .32:
                    self.actor.set_display_scale(scale)
                self.assertAlmostEqual((self.window.width()-4) / base.width(), scale / .32, delta=.02)
                self.assertAlmostEqual((self.window.height()-4) / base.height(), scale / .32, delta=.02)

    def test_visible_geometry_excludes_padding_and_follows_move_and_scale(self):
        for scale in (.32, .56):
            self.actor.set_display_scale(scale)
            self.window.move(240, 180)
            visible = self.window.visible_geometry()
            self.assertGreater(visible.left(), self.window.geometry().left())
            self.assertLess(visible.right(), self.window.geometry().right())
            self.assertEqual(visible, self.window._hit_mask.boundingRect().translated(self.window.pos()))
            self.window.move(300, 220)
            self.assertEqual(self.window.visible_geometry(), visible.translated(60, 40))

    def test_direct_pet_click_and_drag_keep_distinct_events(self):
        self.window.move(240, 180)
        self.window.show()
        self.app.processEvents()
        clicks, moves = [], []
        self.window.clicked.connect(lambda: clicks.append(True))
        self.window.moved.connect(moves.append)
        point = self.window.rect().center()
        QtTest.QTest.mouseClick(self.window, QtCore.Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(clicks, [True])
        self.assertEqual(moves, [])
        start = self.window.mapToGlobal(point)
        button = QtCore.Qt.MouseButton.LeftButton
        for kind, global_pos, changed, held in (
            (QtCore.QEvent.Type.MouseButtonPress, start, button, button),
            (QtCore.QEvent.Type.MouseMove, start + QtCore.QPoint(30, 20), QtCore.Qt.MouseButton.NoButton, button),
            (QtCore.QEvent.Type.MouseButtonRelease, start + QtCore.QPoint(30, 20), button, QtCore.Qt.MouseButton.NoButton),
        ):
            self.app.sendEvent(self.window, QtGui.QMouseEvent(
                kind, self.window.mapFromGlobal(global_pos), global_pos, changed, held,
                QtCore.Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.window.pos(), QtCore.QPoint(270, 200))
        self.assertEqual(moves, [QtCore.QPoint(270, 200)])
        self.assertEqual(clicks, [True])


if __name__ == '__main__':
    unittest.main()
