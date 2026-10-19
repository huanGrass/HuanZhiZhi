from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from huanzhizhi.platform.window_policy import (
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TRANSPARENT,
    WindowPolicy,
)


class FakeWindowApi:
    def __init__(self) -> None:
        self.style = 0x100
        self.style_writes: list[int] = []
        self.refreshes = 0
        self.fail_operation: str | None = None

    def get_extended_style(self, hwnd: int) -> int:
        del hwnd
        if self.fail_operation == "get":
            raise RuntimeError("读取失败")
        return self.style

    def set_extended_style(self, hwnd: int, style: int) -> None:
        del hwnd
        if self.fail_operation == "set":
            raise RuntimeError("写入失败")
        self.style = style
        self.style_writes.append(style)

    def refresh_extended_style(self, hwnd: int) -> None:
        del hwnd
        if self.fail_operation == "refresh":
            raise RuntimeError("刷新失败")
        self.refreshes += 1

class WindowPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.widget = QtWidgets.QWidget(
            None,
            QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint,
        )
        self.api = FakeWindowApi()
        self.policy = WindowPolicy(self.widget, api=self.api)

    def tearDown(self) -> None:
        self.widget.close()

    def test_input_policy_sets_verified_bits_and_refreshes_frame(self) -> None:
        self.policy.set_input_passthrough(True, layered=True, no_activate=True)
        expected = WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
        self.assertEqual(self.api.style & expected, expected)
        self.assertEqual(self.api.refreshes, 1)
        self.assertTrue(
            self.widget.testAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

        self.policy.set_input_passthrough(False)
        self.assertFalse(self.api.style & WS_EX_TRANSPARENT)
        self.assertTrue(self.api.style & WS_EX_LAYERED)
        self.assertTrue(self.api.style & WS_EX_NOACTIVATE)
        self.assertEqual(self.api.refreshes, 2)

    def test_native_failures_are_propagated(self) -> None:
        self.api.fail_operation = "set"
        with self.assertRaisesRegex(RuntimeError, "写入失败"):
            self.policy.set_input_passthrough(True)

    def test_readback_mismatch_is_an_error(self) -> None:
        class IgnoringApi(FakeWindowApi):
            def set_extended_style(self, hwnd: int, style: int) -> None:
                del hwnd, style

        policy = WindowPolicy(self.widget, api=IgnoringApi())
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            policy.set_input_passthrough(True)


if __name__ == "__main__":
    unittest.main()
