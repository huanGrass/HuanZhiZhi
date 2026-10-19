from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

from PySide6 import QtCore, QtGui

from huanzhizhi.platform.screen_coordinates import physical_to_qt_global


class ScreenCoordinateTests(unittest.TestCase):
    def test_maps_physical_pixels_using_qt_primary_screen_dpi(self) -> None:
        screen = mock.Mock()
        screen.devicePixelRatio.return_value = 2.0
        with mock.patch.object(QtGui.QGuiApplication, "primaryScreen", return_value=screen):
            point = physical_to_qt_global(1832, 1652)

        self.assertEqual(point, QtCore.QPoint(916, 826))


if __name__ == "__main__":
    unittest.main()
