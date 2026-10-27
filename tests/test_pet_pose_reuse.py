"""Compact themes reuse poses while keeping logical action stages intact."""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtWidgets
from huanzhizhi.pet.animation import PetAnimationController
from huanzhizhi.pet.window import PetWindow


class PetPoseReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_seven_pose_themes_keep_return_direction_and_reuse_transitions(self):
        window = PetWindow()
        actor = PetAnimationController(window)
        try:
            for theme in ('ufo', 'broom'):
                actor.set_theme(theme)
                self.assertEqual(set(actor._metadata[theme]['frames']),
                                 {'0', '1', '7', '10', '11', '12', '18'})
                for mirrored in (False, True):
                    for stage, source in ((9, 0), (13, 10), (19, 0), (22, 7)):
                        with self.subTest(theme=theme, stage=stage, mirrored=mirrored):
                            actual = actor._prepared(stage, mirrored)
                            expected = actor._prepared(source, mirrored)
                            self.assertIs(actual, expected)
                    for frame in actor._metadata[theme]['frames']:
                        actor._prepared(int(frame), mirrored)
                self.assertEqual(sum(key[0] == theme for key in actor._frames), 14)
            with self.assertRaises(KeyError):
                actor._prepared(999, False)
        finally:
            actor.close()
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
