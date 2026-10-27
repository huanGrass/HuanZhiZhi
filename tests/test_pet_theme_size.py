"""Themes share a consistent visible footprint at each display scale."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtGui, QtWidgets
from huanzhizhi.pet.animation import PetAnimationController
from huanzhizhi.pet.window import PetWindow


def visible_area(pixmap):
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    return sum(bytes(image.constBits())[3::4]) / (255 * pixmap.devicePixelRatio()**2)


class PetThemeSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_atlas_artwork_uses_the_same_scale_across_poses(self):
        window = PetWindow()
        actor = PetAnimationController(window)
        try:
            for theme in ('ufo', 'broom'):
                actor.set_theme(theme)
                reference = None
                for number, frame in actor._metadata[theme]['frames'].items():
                    source = actor._sheet(actor._assets / frame['sheet']).copy(*frame['rect'])
                    rendered = actor._prepared(int(number), False)[0]
                    density = (visible_area(source) / visible_area(rendered)) ** .5
                    if reference is None:
                        reference = density
                    with self.subTest(theme=theme, frame=number):
                        self.assertAlmostEqual(density/reference, 1., delta=.01)
        finally:
            actor.close()
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_visible_area_matches_across_scale_and_dpi(self):
        window = PetWindow()
        actor = PetAnimationController(window)
        try:
            for ratio in (1., 2.):
                with patch.object(window, 'devicePixelRatioF', return_value=ratio):
                    for scale in (.19, .32, .56, 1.25):
                        actor.set_display_scale(scale)
                        actor.set_theme("ufo")
                        reference = visible_area(window._pixmap)
                        for theme in ('ufo', 'broom'):
                            actor.set_theme(theme)
                            actual = visible_area(window._pixmap)
                            with self.subTest(theme=theme, scale=scale, dpi=ratio):
                                self.assertAlmostEqual(actual/reference, 1., delta=.05)
        finally:
            actor.close()
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
