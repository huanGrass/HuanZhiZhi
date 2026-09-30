"""Operating-system integration used by HuanZhiZhi components."""

from huanzhizhi.platform.global_mouse import GlobalMouseWatcher
from huanzhizhi.platform.screen_coordinates import physical_to_qt_global
from huanzhizhi.platform.target_window import (
    DesktopWindowsError,
    WindowUnavailableError,
    Win32DesktopApi,
    WindowInfo,
    WindowRule,
)
from huanzhizhi.platform.window_policy import (
    WindowPolicy,
    Win32WindowApi,
)

__all__ = (
    "GlobalMouseWatcher",
    "physical_to_qt_global",
    "DesktopWindowsError",
    "WindowUnavailableError",
    "Win32DesktopApi",
    "WindowInfo",
    "WindowRule",
    "WindowPolicy",
    "Win32WindowApi",
)
