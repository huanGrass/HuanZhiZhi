from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from huanzhizhi.widgets import MENU_STYLE
from huanzhizhi.pet.context_menu import (
    PetContextMenu,
    display_scale_label,
    display_scale_options,
)


class PetContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_scale_options_match_original_steps_and_ultra_algorithm(self) -> None:
        self.assertEqual(
            display_scale_options(880, 1080),
            (0.20, 0.24, 0.28, 0.32, 0.36, 0.40, 0.44, 0.48, 0.52, 0.56, 0.82),
        )
        self.assertEqual(display_scale_options(880, 600)[-1], 0.56)
        self.assertEqual(display_scale_options(100, 1000)[-1], 1.25)

    def test_scale_labels_match_original_text(self) -> None:
        self.assertEqual(display_scale_label(0.20), "63%")
        self.assertEqual(display_scale_label(0.32), "100%（默认）")
        self.assertEqual(display_scale_label(0.82), "超大（约256%）")

    def test_menu_items_checks_and_order_match_existing_experience(self) -> None:
        menu = self._menu()

        self.assertEqual(
            [action.text() for action in menu.actions()],
            [
                "设置",
                "收起之之",
                "",
                "大小",
                "",
                "重启",
                "退出",
            ],
        )
        checked_scales = [
            scale for action, scale in menu.scale_actions.items() if action.isChecked()
        ]
        self.assertEqual(checked_scales, [0.32])
        menu.close_view()

    def test_every_action_only_emits_view_intent(self) -> None:
        menu = self._menu()
        requests: list[object] = []
        menu.show_settings_requested.connect(lambda: requests.append("settings"))
        menu.hide_requested.connect(lambda: requests.append("hide"))
        menu.restart_requested.connect(lambda: requests.append("restart"))
        menu.quit_requested.connect(lambda: requests.append("quit"))
        menu.scale_requested.connect(lambda scale: requests.append(("scale", scale)))

        menu.settings_action.trigger()
        menu.hide_action.trigger()
        next(
            action
            for action, scale in menu.scale_actions.items()
            if scale == 0.40
        ).trigger()
        menu.restart_action.trigger()
        menu.quit_action.trigger()

        self.assertEqual(
            requests,
            [
                "settings",
                "hide",
                ("scale", 0.40),
                "restart",
                "quit",
            ],
        )
        menu.close_view()

    def test_open_changed_tracks_popup_lifetime(self) -> None:
        menu = self._menu()
        states: list[bool] = []
        menu.open_changed.connect(states.append)
        screen = self.app.primaryScreen()
        self.assertIsNotNone(screen)

        menu.popup(screen.availableGeometry().center())
        self.app.processEvents()
        menu.close_view()
        self.app.processEvents()

        self.assertEqual(states, [True, False])

    def test_style_uses_original_physical_pixel_dimensions(self) -> None:
        style = MENU_STYLE

        self.assertIn("font-size: 14px", style)
        self.assertIn("min-width: 168px", style)
        self.assertIn("min-height: 30px", style)
        self.assertIn("padding: 6px 30px 6px 14px", style)

    def test_invalid_constructor_state_is_rejected(self) -> None:
        invalid_calls = (
            lambda: self._menu(current_scale=0.18),
            lambda: self._menu(current_scale=float("nan")),
            lambda: self._menu(source_image_height=0),
            lambda: self._menu(screen_available_height=-1),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()

    @unittest.skipUnless(sys.platform == "win32", "Windows menu sizing smoke test")
    def test_windows_menu_physical_size_smoke(self) -> None:
        menu = self._menu()
        menu.ensurePolished()
        size = menu.sizeHint()

        self.assertGreaterEqual(size.width(), 168)
        self.assertLess(size.width(), 600)
        self.assertGreater(size.height(), 100)
        self.assertEqual(menu.font().pixelSize(), 14)
        menu.close_view()

    @staticmethod
    def _menu(
        *,
        current_scale: float = 0.32,
        source_image_height: int = 880,
        screen_available_height: int = 1080,
    ) -> PetContextMenu:
        return PetContextMenu(
            current_scale,
            source_image_height,
            screen_available_height,
        )


if __name__ == "__main__":
    unittest.main()
