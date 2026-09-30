"""Shared sizing and placement rules for desktop-pet bubbles."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6 import QtCore, QtGui, QtWidgets


REFERENCE_PET_SCALE = 0.32
SCALE_BOOST = 1.15
MIN_BUBBLE_SCALE = 0.95
MAX_BUBBLE_SCALE = 3.0
SPEECH_GAP = 6
TASK_GAP = 8
TASKS_PER_SIDE = 5


def bubble_scale_for_display(display_scale: float) -> float:
    """Return the logical-pixel bubble scale for a pet display scale."""
    if isinstance(display_scale, bool) or not isinstance(display_scale, (int, float)):
        raise TypeError("桌宠显示倍率必须是数字")
    display_scale = float(display_scale)
    if display_scale <= 0:
        raise ValueError("桌宠显示倍率必须大于零")
    scale = (display_scale / REFERENCE_PET_SCALE) * SCALE_BOOST
    return min(max(scale, MIN_BUBBLE_SCALE), MAX_BUBBLE_SCALE)


def layout_scale_for_display(display_scale: float) -> float:
    if display_scale <= 0:
        raise ValueError("桌宠显示倍率必须大于零")
    return float(display_scale) / REFERENCE_PET_SCALE


def screen_for_rect(rect: QtCore.QRect) -> QtGui.QScreen:
    if not isinstance(rect, QtCore.QRect) or not rect.isValid():
        raise ValueError("定位矩形必须是有效的 QRect")
    screen = QtGui.QGuiApplication.screenAt(rect.center())
    if screen is None:
        raise RuntimeError("无法确定气泡所在显示器")
    return screen


class BubbleLayoutManager:
    @staticmethod
    def position_bubbles(
        *,
        pet_rect: QtCore.QRect,
        speech_anchor_rect: QtCore.QRect,
        speech_bubble,
        task_bubbles: Iterable,
        display_scale: float,
    ) -> None:
        bubbles = tuple(task_bubbles)
        BubbleLayoutManager.position_task_bubbles(bubbles, pet_rect, display_scale)
        if speech_bubble is not None and speech_bubble.isVisible():
            BubbleLayoutManager.position_speech_bubble(speech_bubble, speech_anchor_rect)
            speech_bubble.raise_()

    @staticmethod
    def position_speech_bubble(bubble, pet_rect: QtCore.QRect) -> None:
        screen = screen_for_rect(pet_rect)
        geometry = screen.availableGeometry()
        gap = bubble._scaled_int(SPEECH_GAP)
        for edge in ("down", "left", "right", "up"):
            bubble._set_tail_edge(edge)
            bubble.adjustSize()
            pos = BubbleLayoutManager._speech_candidate_pos(bubble, edge, pet_rect, gap)
            if BubbleLayoutManager._fits_screen(QtCore.QRect(pos, bubble.size()), geometry):
                bubble.move(pos)
                return
        bubble._set_tail_edge("down")
        bubble.adjustSize()
        pos = BubbleLayoutManager._speech_candidate_pos(bubble, "down", pet_rect, gap)
        bubble.move(BubbleLayoutManager._clamped_pos(QtCore.QRect(pos, bubble.size()), geometry))

    @staticmethod
    def position_task_bubbles(
        bubbles: Sequence,
        pet_rect: QtCore.QRect,
        display_scale: float,
    ) -> None:
        if not bubbles:
            return
        visible = tuple(bubbles[: TASKS_PER_SIDE * 2])
        for bubble in bubbles[TASKS_PER_SIDE * 2 :]:
            bubble.hide()
        BubbleLayoutManager.position_task_side_columns(visible, pet_rect, display_scale)

    @staticmethod
    def position_task_side_columns(
        bubbles: Sequence,
        pet_rect: QtCore.QRect,
        display_scale: float,
    ) -> None:
        if not bubbles:
            return
        screen = screen_for_rect(pet_rect)
        geometry = screen.availableGeometry()
        scale = layout_scale_for_display(display_scale)
        gap = max(1, round(TASK_GAP * scale))
        sides: dict[str, list] = {"left": [], "right": []}
        for index, bubble in enumerate(bubbles):
            bubble.adjustSize()
            side = "left" if index % 2 == 0 else "right"
            if len(sides[side]) >= TASKS_PER_SIDE:
                bubble.hide()
            else:
                sides[side].append(bubble)
        for side in ("left", "right"):
            BubbleLayoutManager._position_task_column(
                sides[side], side, pet_rect, gap, geometry
            )

    @staticmethod
    def _position_task_column(
        bubbles: Sequence,
        side: str,
        pet_rect: QtCore.QRect,
        gap: int,
        geometry: QtCore.QRect,
    ) -> None:
        if not bubbles:
            return
        max_width = max(bubble.width() for bubble in bubbles)
        total_height = sum(bubble.height() for bubble in bubbles) + gap * (len(bubbles) - 1)
        y = pet_rect.center().y() - total_height // 2
        x = pet_rect.left() - gap - max_width if side == "left" else pet_rect.right() + gap
        stack_rect = QtCore.QRect(x, y, max_width, total_height)
        clamped = BubbleLayoutManager._clamped_pos(stack_rect, geometry)
        x, y = clamped.x(), clamped.y()
        align_right = side == "left"
        for bubble in bubbles:
            bubble.move(x + (max_width - bubble.width() if align_right else 0), y)
            y += bubble.height() + gap

    @staticmethod
    def position_task_stack(
        bubbles: Sequence,
        pet_rect: QtCore.QRect,
        display_scale: float,
        *,
        avoid_rects: Sequence[QtCore.QRect] = (),
    ) -> None:
        if not bubbles:
            return
        screen = screen_for_rect(pet_rect)
        geometry = screen.availableGeometry()
        gap = max(1, round(TASK_GAP * layout_scale_for_display(display_scale)))
        for bubble in bubbles:
            bubble.adjustSize()
        max_width = max(b.width() for b in bubbles)
        total_height = sum(b.height() for b in bubbles) + gap * (len(bubbles) - 1)
        if max_width > geometry.width() or total_height > geometry.height():
            raise RuntimeError("任务气泡堆栈大于显示器可用区域")
        right_x = pet_rect.right() + gap
        left_x = pet_rect.left() - max_width - gap
        bottom_limit = geometry.bottom() - total_height + 1
        base_y = BubbleLayoutManager._clamped_stack_y(
            pet_rect.center().y() - total_height // 2, geometry, bottom_limit
        )
        fallback_x = min(max(right_x, geometry.left()), geometry.right() - max_width + 1)
        candidates: list[tuple[int, int, int, int, int, bool]] = []

        def add(x: int, y: int, align_right: bool, priority: int) -> None:
            rect = QtCore.QRect(x, y, max_width, total_height)
            candidates.append(
                (
                    BubbleLayoutManager._overlap_area(rect, avoid_rects),
                    priority,
                    abs(y - base_y),
                    x,
                    y,
                    align_right,
                )
            )

        if right_x + max_width - 1 <= geometry.right():
            add(right_x, base_y, False, 0)
        if left_x >= geometry.left():
            add(left_x, base_y, True, 1)
        for avoided in avoid_rects:
            options = {
                BubbleLayoutManager._clamped_stack_y(
                    avoided.top() - total_height - gap, geometry, bottom_limit
                ),
                BubbleLayoutManager._clamped_stack_y(
                    avoided.bottom() + gap, geometry, bottom_limit
                ),
            }
            for y in options:
                if right_x + max_width - 1 <= geometry.right():
                    add(right_x, y, False, 2)
                if left_x >= geometry.left():
                    add(left_x, y, True, 3)
                add(fallback_x, y, False, 5)
        add(fallback_x, base_y, False, 4)

        _overlap, _priority, _distance, x, y, align_right = min(candidates)
        for bubble in bubbles:
            bubble.move(x + (max_width - bubble.width() if align_right else 0), y)
            y += bubble.height() + gap

    @staticmethod
    def _speech_candidate_pos(bubble, edge: str, pet_rect: QtCore.QRect, gap: int) -> QtCore.QPoint:
        if edge == "down":
            return QtCore.QPoint(pet_rect.center().x() - bubble.width() // 2, pet_rect.top() - bubble.height() - gap)
        if edge == "left":
            return QtCore.QPoint(pet_rect.right() + gap, pet_rect.center().y() - bubble.height() // 2)
        if edge == "right":
            return QtCore.QPoint(pet_rect.left() - bubble.width() - gap, pet_rect.center().y() - bubble.height() // 2)
        if edge == "up":
            return QtCore.QPoint(pet_rect.center().x() - bubble.width() // 2, pet_rect.bottom() + gap)
        raise ValueError(f"不支持的气泡尾巴方向：{edge}")

    @staticmethod
    def _fits_screen(rect: QtCore.QRect, geometry: QtCore.QRect) -> bool:
        return geometry.contains(rect)

    @staticmethod
    def _clamped_pos(rect: QtCore.QRect, geometry: QtCore.QRect) -> QtCore.QPoint:
        if rect.width() > geometry.width() or rect.height() > geometry.height():
            raise RuntimeError("气泡大于显示器可用区域")
        return QtCore.QPoint(
            min(max(rect.x(), geometry.left()), geometry.right() - rect.width() + 1),
            min(max(rect.y(), geometry.top()), geometry.bottom() - rect.height() + 1),
        )

    @staticmethod
    def _clamped_stack_y(y: int, geometry: QtCore.QRect, bottom_limit: int) -> int:
        return min(max(int(y), geometry.top()), int(bottom_limit))

    @staticmethod
    def _overlap_area(rect: QtCore.QRect, others: Sequence[QtCore.QRect]) -> int:
        area = 0
        for other in others:
            if not isinstance(other, QtCore.QRect) or not other.isValid():
                raise ValueError("避让区域必须是有效的 QRect")
            if rect.intersects(other):
                overlap = rect.intersected(other)
                area += overlap.width() * overlap.height()
        return area


__all__ = (
    "BubbleLayoutManager",
    "bubble_scale_for_display",
    "layout_scale_for_display",
    "screen_for_rect",
)
