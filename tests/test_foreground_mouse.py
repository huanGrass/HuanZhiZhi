"""Foreground input safety without sending any input to the real desktop."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import unittest

from huanzhizhi.platform.foreground_input import ForegroundMouse, _Input
from huanzhizhi.platform.target_window import DesktopWindowsError, WindowInfo


MOVE_ABSOLUTE = 0x0001 | 0x8000 | 0x4000
LEFT_DOWN, LEFT_UP = 0x0002, 0x0004
RIGHT_DOWN, RIGHT_UP = 0x0008, 0x0010


class FakeUser32:
    def __init__(self):
        self.cursor = (100, 100)
        self.keys = set()
        self.desktop = (-1920, -1080, 4480, 2520)
        self.target = 101
        self.send_result = 1
        self.attempts = []
        self.delivered = []

    def GetAsyncKeyState(self, key):
        return 0x8000 if key in self.keys else 0

    def GetPhysicalCursorPos(self, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wt.POINT)).contents
        point.x, point.y = self.cursor
        return True

    def GetSystemMetrics(self, index):
        return dict(zip((76, 77, 78, 79), self.desktop))[index]

    def WindowFromPhysicalPoint(self, point):
        return self.target

    def GetAncestor(self, handle, flag):
        return handle

    def SendInput(self, count, pointer, size):
        packet = ctypes.cast(pointer, ctypes.POINTER(_Input)).contents
        flags, dx, dy = packet.mouse.dwFlags, packet.mouse.dx, packet.mouse.dy
        self.attempts.append((flags, dx, dy))
        if self.send_result != 1:
            return self.send_result
        self.delivered.append((flags, dx, dy))
        if flags & 0x0001:
            left, top, width, height = self.desktop
            self.cursor = (round(left + dx * (width - 1) / 65535),
                           round(top + dy * (height - 1) / 65535))
        for key, down, up in ((1, LEFT_DOWN, LEFT_UP), (2, RIGHT_DOWN, RIGHT_UP)):
            if flags & down:
                self.keys.add(key)
            if flags & up:
                self.keys.discard(key)
        return 1


class FakeDesktopApi:
    def __init__(self):
        self.focused = []
        self.foreground = True
        self.changed = False

    def focus(self, info):
        self.focused.append(info)

    def _require_unchanged(self, info):
        if self.changed:
            raise DesktopWindowsError("target changed")

    def is_foreground(self, info):
        return self.foreground


class ForegroundMouseTests(unittest.TestCase):
    def setUp(self):
        self.info = WindowInfo(101, "测试目标", -1200, -300, 2400, 1200)
        self.native = FakeUser32()
        self.api = FakeDesktopApi()

    def make_mouse(self, kind="click"):
        return ForegroundMouse(self.info, kind, api=self.api, user32=self.native)

    def test_held_keys_cancel_before_focus_or_input(self):
        for key in (0x1B, 1, 2, 4, 5, 6, 0x10, 0x11, 0x12, 0x5B, 0x5C):
            with self.subTest(key=key):
                self.native.keys = {key}
                mouse = self.make_mouse()
                with self.assertRaises(InterruptedError):
                    mouse.begin()
                mouse.close()
                self.assertEqual(self.api.focused, [])
                self.assertEqual(self.native.attempts, [])

    def test_step_requires_begin_and_drag_requires_a_pressed_button(self):
        mouse = self.make_mouse("drag")
        with self.assertRaisesRegex(RuntimeError, "尚未开始"):
            mouse.step("move", (200, 200))
        mouse.begin()
        for phase in ("drag", "release"):
            with self.subTest(phase=phase), self.assertRaises(RuntimeError):
                mouse.step(phase, (200, 200))
        self.assertEqual(self.native.attempts, [])
        mouse.close()

    def test_drag_moves_then_presses_then_drags_and_releases(self):
        mouse = self.make_mouse("drag")
        mouse.begin()
        self.assertEqual(self.api.focused, [self.info])
        mouse.step("move", (-1000, -100))
        self.assertEqual(self.native.cursor, (-1000, -100))
        mouse.step("press", (-1000, -100))
        self.assertIn(1, self.native.keys)
        mouse.step("drag", (400, 300))
        self.assertEqual(self.native.cursor, (400, 300))
        self.assertIn(1, self.native.keys)
        mouse.step("release", (400, 300))
        mouse.close()
        self.assertEqual([item[0] for item in self.native.delivered],
                         [MOVE_ABSOLUTE, MOVE_ABSOLUTE | LEFT_DOWN,
                          MOVE_ABSOLUTE, MOVE_ABSOLUTE | LEFT_UP])
        self.assertNotIn(1, self.native.keys)
        self.assertFalse(mouse.pressed)
        with self.assertRaisesRegex(RuntimeError, "尚未开始"):
            mouse.check()

    def test_click_variants_use_the_correct_button_and_balanced_edges(self):
        for kind, down, up, clicks in (("click", LEFT_DOWN, LEFT_UP, 1),
                                      ("right_click", RIGHT_DOWN, RIGHT_UP, 1),
                                      ("double_click", LEFT_DOWN, LEFT_UP, 2)):
            with self.subTest(kind=kind):
                self.native = FakeUser32()
                mouse = self.make_mouse(kind)
                mouse.begin()
                for _ in range(clicks):
                    mouse.step("press", (300, 200))
                    mouse.step("release", (300, 200))
                mouse.close()
                self.assertEqual([item[0] for item in self.native.delivered],
                                 [MOVE_ABSOLUTE | down, MOVE_ABSOLUTE | up] * clicks)
                self.assertFalse(self.native.keys)

    def test_user_mouse_movement_cancels_before_another_input(self):
        mouse = self.make_mouse()
        mouse.begin()
        mouse.step("move", (200, 200))
        self.native.cursor = (220, 200)
        with self.assertRaisesRegex(InterruptedError, "鼠标位置被改变"):
            mouse.step("press", (220, 200))
        mouse.close()
        self.assertEqual(len(self.native.delivered), 1)

    def test_cursor_tolerance_is_ten_physical_pixels(self):
        for offset, allowed in (((6, 8), True), ((10, 0), True),
                                ((10, 1), False), ((11, 0), False)):
            with self.subTest(offset=offset):
                self.native = FakeUser32()
                mouse = self.make_mouse()
                mouse.begin()
                self.native.cursor = (100 + offset[0], 100 + offset[1])
                if allowed:
                    mouse.check()
                else:
                    with self.assertRaisesRegex(InterruptedError, '鼠标位置被改变'):
                        mouse.check()
                mouse.close()

    def test_focus_loss_target_change_and_escape_cancel_and_close_releases(self):
        for cause in ("focus", "target", "escape", "mouse"):
            with self.subTest(cause=cause):
                self.native, self.api = FakeUser32(), FakeDesktopApi()
                mouse = self.make_mouse("drag")
                mouse.begin()
                mouse.step("press", (200, 200))
                if cause == "focus":
                    self.api.foreground = False
                elif cause == "target":
                    self.api.changed = True
                elif cause == "escape":
                    self.native.keys.add(0x1B)
                else:
                    self.native.cursor = (220, 200)
                with self.assertRaises((InterruptedError, DesktopWindowsError)):
                    mouse.step("drag", (400, 300))
                self.assertEqual(len(self.native.delivered), 1)
                mouse.close()
                self.assertEqual(self.native.delivered[-1], (LEFT_UP, 0, 0))
                self.assertNotIn(1, self.native.keys)
                self.assertFalse(mouse.pressed)
                mouse.close()
                self.assertEqual(len(self.native.delivered), 2)

    def test_occluded_target_cancels_without_pressing(self):
        mouse = self.make_mouse()
        mouse.begin()
        self.native.target = 202
        with self.assertRaisesRegex(InterruptedError, "遮挡"):
            mouse.step("press", (200, 200))
        mouse.close()
        self.assertEqual(self.native.attempts, [])

    def test_failed_move_is_not_reported_as_delivered_or_as_cursor_movement(self):
        mouse = self.make_mouse()
        mouse.begin()
        self.native.send_result = 0
        with self.assertRaisesRegex(OSError, "发送前台鼠标输入失败"):
            mouse.step("move", (200, 200))
        self.assertEqual(self.native.delivered, [])
        self.assertEqual(self.native.cursor, (100, 100))
        mouse.check()
        mouse.close()

    def test_failed_button_edges_keep_cleanup_responsibility(self):
        for phase in ("press", "release"):
            with self.subTest(phase=phase):
                self.native = FakeUser32()
                mouse = self.make_mouse()
                mouse.begin()
                if phase == "release":
                    mouse.step("press", (200, 200))
                delivered_before = len(self.native.delivered)
                self.native.send_result = 0
                with self.assertRaises(OSError):
                    mouse.step(phase, (200, 200))
                self.assertEqual(len(self.native.delivered), delivered_before)
                self.assertTrue(mouse.pressed)
                self.native.send_result = 1
                mouse.close()
                self.assertEqual(self.native.delivered[-1], (LEFT_UP, 0, 0))
                self.assertFalse(mouse.pressed)
                self.assertNotIn(1, self.native.keys)

    def test_failed_cleanup_can_be_retried(self):
        mouse = self.make_mouse()
        mouse.begin()
        mouse.step("press", (200, 200))
        self.native.send_result = 0
        with self.assertRaises(OSError):
            mouse.close()
        self.assertTrue(mouse.pressed)
        self.native.send_result = 1
        mouse.close()
        self.assertFalse(mouse.pressed)
        self.assertNotIn(1, self.native.keys)


if __name__ == "__main__":
    unittest.main()
