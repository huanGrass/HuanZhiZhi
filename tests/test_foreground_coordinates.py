"""Windows monitor origins must survive mixed-DPI animation/input mapping."""
from __future__ import annotations

from contextlib import ExitStack
import unittest
from unittest import mock

from PySide6 import QtCore, QtGui

from huanzhizhi.platform import screen_coordinates


class ForegroundCoordinateTests(unittest.TestCase):
    def setUp(self):
        layouts = (
            ("DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1080), 1.0),
            ("DISPLAY2", (-2560, 0, 2560, 1440), (-2560, 0, 2048, 1152), 1.25),
            ("DISPLAY3", (1920, -180, 3840, 2160), (1920, -180, 1920, 1080), 2.0),
        )
        self.screens = []
        for name, native_rect, qt_rect, ratio in layouts:
            screen = mock.Mock()
            screen.name.return_value = name
            screen.geometry.return_value = QtCore.QRect(*qt_rect)
            screen.devicePixelRatio.return_value = ratio
            self.screens.append(screen)

        def native_monitor(x, y):
            for name, native_rect, _, _ in layouts:
                if QtCore.QRect(*native_rect).contains(x, y):
                    return name, native_rect[0], native_rect[1]
            raise AssertionError(f"Unexpected native point: {(x, y)}")

        def screen_at(point):
            return next((s for s in self.screens if s.geometry().contains(point)), None)

        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(mock.patch.object(screen_coordinates, "_uses_native_screens", return_value=True))
        patches.enter_context(mock.patch.object(screen_coordinates, "_native_monitor", side_effect=native_monitor))
        patches.enter_context(mock.patch.object(QtGui.QGuiApplication, "screens", return_value=self.screens))
        patches.enter_context(mock.patch.object(QtGui.QGuiApplication, "screenAt", side_effect=screen_at))
        # Native mappings must never apply the primary monitor's ratio globally.
        patches.enter_context(mock.patch.object(QtGui.QGuiApplication, "primaryScreen",
                                                side_effect=AssertionError("primary DPI used")))

    def test_left_negative_monitor_preserves_its_origin_in_both_directions(self):
        physical = QtCore.QPoint(-1920, 360)
        logical = QtCore.QPoint(-2048, 288)
        self.assertEqual(screen_coordinates.physical_to_qt_global(physical.x(), physical.y()), logical)
        self.assertEqual(screen_coordinates.qt_to_physical_global(logical), physical)

    def test_second_monitor_scales_local_offset_not_global_coordinates(self):
        physical = QtCore.QPoint(2560, 540)
        logical = QtCore.QPoint(2240, 180)
        self.assertEqual(screen_coordinates.physical_to_qt_global(physical.x(), physical.y()), logical)
        self.assertEqual(screen_coordinates.qt_to_physical_global(logical), physical)

    def test_fractional_hotspot_converts_using_destination_monitor_dpi(self):
        self.assertEqual(screen_coordinates.qt_to_physical_global(QtCore.QPointF(2240.5, 180.5)),
                         QtCore.QPoint(2561, 541))

    def test_native_monitor_mismatch_fails_instead_of_using_stale_geometry(self):
        with mock.patch.object(screen_coordinates, "_native_monitor", return_value=("REPLACED", 1930, -180)):
            with self.assertRaisesRegex(RuntimeError, "显示器布局已变化"):
                screen_coordinates.qt_to_physical_global(QtCore.QPoint(2240, 180))
            with self.assertRaisesRegex(RuntimeError, "无法匹配显示器"):
                screen_coordinates.physical_to_qt_global(2560, 540)

    def test_qt_product_name_can_differ_from_win32_device_name(self):
        self.screens[2].name.return_value = 'T270LG'
        self.assertEqual(screen_coordinates.physical_to_qt_global(2560, 540), QtCore.QPoint(2240, 180))
        self.assertEqual(screen_coordinates.qt_to_physical_global(QtCore.QPoint(2240, 180)), QtCore.QPoint(2560, 540))


if __name__ == "__main__":
    unittest.main()
