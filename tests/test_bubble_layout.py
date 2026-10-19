from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from huanzhizhi.pet.bubble_layout import BubbleLayoutManager, bubble_scale_for_display
from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.pet.task_bubble import TaskBubbleWindow


class BubbleLayoutManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        screen = self.app.primaryScreen()
        self.assertIsNotNone(screen)
        self.area = screen.availableGeometry()
        self.pet_rect = QtCore.QRect(
            self.area.center().x() - 80,
            self.area.center().y() - 120,
            160,
            240,
        )
        self.bubbles: list[TaskBubbleWindow] = []

    def tearDown(self) -> None:
        for bubble in self.bubbles:
            bubble.close()
        self.app.processEvents()

    def make_task_bubbles(self, count: int, mode: str = "stack") -> list[TaskBubbleWindow]:
        for index in range(count):
            bubble = TaskBubbleWindow()
            bubble.set_content(f"任务 {index}", layout_mode=mode)
            self.bubbles.append(bubble)
        return self.bubbles[-count:]

    def test_bubble_scale_is_dpi_independent_logical_size(self) -> None:
        self.assertAlmostEqual(bubble_scale_for_display(0.32), 1.15)

    def test_side_columns_alternate_and_stay_inside_screen(self) -> None:
        bubbles = self.make_task_bubbles(4)
        BubbleLayoutManager.position_task_side_columns(bubbles, self.pet_rect, 0.32)
        gap = 8
        if self.pet_rect.left() >= bubbles[0].width() + gap:
            self.assertLess(bubbles[0].x(), self.pet_rect.left())
        if self.area.right() - self.pet_rect.right() >= bubbles[1].width() + gap:
            self.assertGreater(bubbles[1].x(), self.pet_rect.right())
        for bubble in bubbles:
            self.assertTrue(self.area.contains(bubble.geometry()))

    def test_stack_avoids_requested_rectangle(self) -> None:
        bubbles = self.make_task_bubbles(2)
        avoid = QtCore.QRect(
            self.pet_rect.right() + 1,
            self.pet_rect.center().y() - 100,
            300,
            200,
        )
        BubbleLayoutManager.position_task_stack(
            bubbles, self.pet_rect, 0.32, avoid_rects=(avoid,)
        )
        stack_rect = bubbles[0].geometry().united(bubbles[1].geometry())
        self.assertFalse(stack_rect.intersects(avoid))
        self.assertTrue(self.area.contains(stack_rect))

    def test_combined_layout_keeps_visible_speech_on_top(self) -> None:
        speech = SpeechBubbleWindow()
        speech.show_text("提示", 1000)
        bubbles = self.make_task_bubbles(2)
        BubbleLayoutManager.position_bubbles(
            pet_rect=self.pet_rect,
            speech_anchor_rect=self.pet_rect,
            speech_bubble=speech,
            task_bubbles=bubbles,
            display_scale=0.32,
        )
        self.assertTrue(self.area.contains(speech.geometry()))
        speech.close()


if __name__ == "__main__":
    unittest.main()
