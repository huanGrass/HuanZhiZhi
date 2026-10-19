"""Explicit desktop-pet assembly used by integration tests."""

from __future__ import annotations

from PySide6 import QtCore

from huanzhizhi.pet import PetComponent
from huanzhizhi.pet.constants import DEFAULT_DISPLAY_SCALE
from huanzhizhi.pet.context_menu import PetContextMenu
from huanzhizhi.pet.point_overlay import PointOverlayWindow
from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.pet.window import PetWindow


def create_test_pet(
    *,
    display_scale: float = DEFAULT_DISPLAY_SCALE,
    initial_position: QtCore.QPoint | None = None,
    mouse_point_visible: bool = False,
) -> PetComponent:
    window = PetWindow()
    speech = SpeechBubbleWindow(display_scale)
    point = PointOverlayWindow(
        display_scale=display_scale,
    )
    return PetComponent(
        window,
        speech,
        point,
        PetContextMenu,
        display_scale=display_scale,
        initial_position=initial_position,
        mouse_point_visible=mouse_point_visible,
    )


__all__ = ("create_test_pet",)
