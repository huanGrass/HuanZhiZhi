"""Strict Windows target-window discovery and background input primitives."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from dataclasses import dataclass
from huanzhizhi.platform.window_capture import WgcWindowCapture


WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
SW_SHOW = 5
SW_RESTORE = 9
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MAX_CLIENT_COORDINATE = 0x7FFF
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class DesktopWindowsError(RuntimeError):
    """Raised when a strict desktop-window operation cannot be verified."""


class WindowUnavailableError(DesktopWindowsError):
    """Raised when the requested target window is not ready yet."""


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A top-level window's client rectangle in screen coordinates."""

    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _positive_integer(self.hwnd, "hwnd")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("window title must be non-empty text")
        for name, value in (("left", self.left), ("top", self.top)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        _positive_integer(self.width, "width")
        _positive_integer(self.height, "height")

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def label(self) -> str:
        return f"{self.title}  [{self.width}x{self.height}]  hwnd={self.hwnd}"


@dataclass(frozen=True, slots=True)
class WindowRule:
    title_contains: str
    client_width: int
    client_height: int

    def __post_init__(self) -> None:
        if not isinstance(self.title_contains, str) or not self.title_contains.strip():
            raise ValueError("title_contains must be non-empty text")
        _positive_integer(self.client_width, "client_width")
        _positive_integer(self.client_height, "client_height")

    def matches(self, window: WindowInfo) -> bool:
        if not isinstance(window, WindowInfo):
            raise TypeError("window must be WindowInfo")
        return (
            self.title_contains in window.title
            and window.width == self.client_width
            and window.height == self.client_height
        )


class Win32DesktopApi:
    """Verified operations used by window-based tasks."""

    def __init__(
        self,
        native: object | None = None,
        *,
        capture: object | None = None,
        configure_dpi_awareness: bool = True,
    ) -> None:
        if not isinstance(configure_dpi_awareness, bool):
            raise TypeError("configure_dpi_awareness must be bool")
        self._native = native or _CtypesDesktopNative()
        self._capture = capture or WgcWindowCapture()
        if configure_dpi_awareness:
            self._call("configure DPI awareness", "enable_dpi_awareness")

    def list_windows(self) -> tuple[WindowInfo, ...]:
        handles = self._call("enumerate top-level windows", "enum_windows")
        try:
            handle_list = list(handles)
        except TypeError as error:
            raise DesktopWindowsError("native window enumeration returned a non-iterable") from error
        windows: list[WindowInfo] = []
        seen: set[int] = set()
        for hwnd in handle_list:
            _positive_integer(hwnd, "enumerated hwnd")
            if hwnd in seen:
                raise DesktopWindowsError(f"native enumeration returned duplicate hwnd {hwnd}")
            seen.add(hwnd)
            if not self._call(f"check window {hwnd}", "is_window", hwnd):
                raise DesktopWindowsError(f"enumerated hwnd no longer exists: {hwnd}")
            if not self._call(f"check visibility for window {hwnd}", "is_window_visible", hwnd):
                continue
            title = self._read_title(hwnd)
            if not title:
                continue
            client_rect = self._checked_rect(
                self._call(
                    f"read client rectangle for window {hwnd}",
                    "get_client_rect",
                    hwnd,
                ),
                "client rectangle",
            )
            client_width = client_rect[2] - client_rect[0]
            client_height = client_rect[3] - client_rect[1]
            # Match the original selector: tiny or zero-client utility windows
            # are not user-selectable targets and must not poison enumeration.
            if client_width < 240 or client_height < 240:
                continue
            windows.append(self._read_window(hwnd, known_title=title))
        windows.sort(key=lambda item: (item.title.casefold(), item.hwnd))
        return tuple(windows)

    def resolve(
        self, rule: WindowRule, selected_hwnd: int | None = None
    ) -> WindowInfo:
        _require_rule(rule)
        windows = self.list_windows()
        if selected_hwnd is not None:
            _positive_integer(selected_hwnd, "selected_hwnd")
            selected = next(
                (window for window in windows if window.hwnd == selected_hwnd), None
            )
            if selected is None:
                raise WindowUnavailableError(
                    f"selected target window does not exist or is not visible: {selected_hwnd}"
                )
            if not rule.matches(selected):
                raise WindowUnavailableError(
                    f"selected target window does not match rule: {selected.label}"
                )
            return selected
        matches = tuple(window for window in windows if rule.matches(window))
        if not matches:
            raise WindowUnavailableError(
                "no window matches title and client size: "
                f"{rule.title_contains!r} {rule.client_width}x{rule.client_height}"
            )
        if len(matches) > 1:
            labels = "; ".join(window.label for window in matches)
            raise DesktopWindowsError(f"multiple windows match the target rule: {labels}")
        return matches[0]

    def refresh(self, info: WindowInfo, rule: WindowRule) -> WindowInfo:
        _require_info(info)
        _require_rule(rule)
        fresh = self._read_window(info.hwnd, require_visible=True)
        if not rule.matches(fresh):
            raise DesktopWindowsError(
                f"refreshed target window no longer matches rule: {fresh.label}"
            )
        return fresh

    def capture_client(self, info: WindowInfo, *, region: tuple[int, int, int, int] | None = None):
        fresh = self._require_unchanged(info)
        if region is not None and (not isinstance(region, tuple) or len(region) != 4
                or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
                or not (0 <= region[0] < region[2] <= fresh.width and 0 <= region[1] < region[3] <= fresh.height)):
            raise DesktopWindowsError('capture region must be inside the client rectangle')
        capture_bounds = self._checked_rect(
            self._call(
                f"read extended frame bounds for window {fresh.hwnd}",
                "get_extended_frame_bounds",
                fresh.hwnd,
            ),
            "extended frame bounds",
        )
        try:
            capture = getattr(self._capture, "capture_client")
            image = capture(
                fresh,
                crop_left=fresh.left - capture_bounds[0],
                crop_top=fresh.top - capture_bounds[1],
                **({'region': region} if region is not None else {}),
            )
        except Exception as error:
            raise DesktopWindowsError(f"client capture failed for {fresh.label}: {error}") from error
        expected_size = (fresh.width, fresh.height) if region is None else (region[2]-region[0], region[3]-region[1])
        if getattr(image, "mode", None) != "RGB" or getattr(image, "size", None) != expected_size:
            raise DesktopWindowsError("captured image conversion produced unexpected mode or size")
        return image

    def post_left_click(self, info: WindowInfo, x: int, y: int) -> None:
        fresh = self._require_unchanged(info)
        x = _client_coordinate(x, "x", fresh.width)
        y = _client_coordinate(y, "y", fresh.height)
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        messages = (
            ("WM_MOUSEMOVE", WM_MOUSEMOVE, 0),
            ("WM_LBUTTONDOWN", WM_LBUTTONDOWN, MK_LBUTTON),
            ("WM_LBUTTONUP", WM_LBUTTONUP, 0),
        )
        for name, message, wparam in messages:
            result = self._call(
                f"post {name} to window {fresh.hwnd}",
                "post_message",
                fresh.hwnd,
                message,
                wparam,
                lparam,
            )
            if result is not True:
                raise DesktopWindowsError(
                    f"post {name} to window {fresh.hwnd} returned {result!r}"
                )

    def is_foreground(self, info: WindowInfo) -> bool:
        _require_info(info)
        return self._call("read foreground window", "get_foreground_window") == info.hwnd

    def focus(self, info: WindowInfo) -> WindowInfo:
        fresh = self._require_unchanged(info)
        self._call(f"show window {fresh.hwnd}", "show_window", fresh.hwnd, SW_SHOW)
        result = self._call(
            f"focus window {fresh.hwnd}", "set_foreground_window", fresh.hwnd
        )
        if result is not True:
            raise DesktopWindowsError(
                f"SetForegroundWindow returned {result!r} for hwnd {fresh.hwnd}"
            )
        foreground = self._call(
            "verify foreground window", "get_foreground_window"
        )
        if foreground != fresh.hwnd:
            raise DesktopWindowsError(
                f"foreground verification failed: expected {fresh.hwnd}, got {foreground}"
            )
        return self._read_window(fresh.hwnd, require_visible=True)

    def set_client_size(
        self, info: WindowInfo, client_width: int, client_height: int
    ) -> WindowInfo:
        fresh = self._require_unchanged(info)
        target_width = _positive_integer(client_width, "client_width")
        target_height = _positive_integer(client_height, "client_height")
        self._call(
            f"restore window {fresh.hwnd}", "show_window", fresh.hwnd, SW_RESTORE
        )
        for _attempt in range(4):
            current = self._read_window(fresh.hwnd, require_visible=True)
            if current.width == target_width and current.height == target_height:
                return current
            outer = self._checked_rect(
                self._call(
                    f"read outer rectangle for window {fresh.hwnd}",
                    "get_window_rect",
                    fresh.hwnd,
                ),
                "outer window rectangle",
            )
            outer_width = outer[2] - outer[0]
            outer_height = outer[3] - outer[1]
            new_width = outer_width + target_width - current.width
            new_height = outer_height + target_height - current.height
            if new_width <= 0 or new_height <= 0:
                raise DesktopWindowsError("calculated outer window size is not positive")
            result = self._call(
                f"resize window {fresh.hwnd}",
                "set_window_size",
                fresh.hwnd,
                new_width,
                new_height,
            )
            if result is not True:
                raise DesktopWindowsError(
                    f"SetWindowPos returned {result!r} for hwnd {fresh.hwnd}"
                )
        result = self._read_window(fresh.hwnd, require_visible=True)
        if result.width != target_width or result.height != target_height:
            raise DesktopWindowsError(
                f"client resize verification failed: expected {target_width}x{target_height}, "
                f"got {result.width}x{result.height}"
            )
        return result

    def _require_unchanged(self, info: WindowInfo) -> WindowInfo:
        _require_info(info)
        fresh = self._read_window(info.hwnd, require_visible=True)
        if fresh != info:
            raise DesktopWindowsError(
                f"window information is stale; refresh is required: old={info.label}, "
                f"current={fresh.label}"
            )
        return fresh

    def _read_window(
        self,
        hwnd: int,
        *,
        known_title: str | None = None,
        require_visible: bool = False,
    ) -> WindowInfo:
        _positive_integer(hwnd, "hwnd")
        if not self._call(f"check window {hwnd}", "is_window", hwnd):
            raise DesktopWindowsError(f"window does not exist: {hwnd}")
        if require_visible and not self._call(
            f"check visibility for window {hwnd}", "is_window_visible", hwnd
        ):
            raise DesktopWindowsError(f"window is not visible: {hwnd}")
        title = known_title if known_title is not None else self._read_title(hwnd)
        if not title:
            raise DesktopWindowsError(f"window has no title: {hwnd}")
        client_rect = self._checked_rect(
            self._call(f"read client rectangle for window {hwnd}", "get_client_rect", hwnd),
            "client rectangle",
        )
        local_width = client_rect[2] - client_rect[0]
        local_height = client_rect[3] - client_rect[1]
        if local_width <= 0 or local_height <= 0:
            raise DesktopWindowsError(
                f"window {hwnd} client size is not positive: {local_width}x{local_height}"
            )
        top_left = self._checked_point(
            self._call(
                f"convert client top-left for window {hwnd}",
                "client_to_screen",
                hwnd,
                client_rect[0],
                client_rect[1],
            ),
            "client top-left",
        )
        bottom_right = self._checked_point(
            self._call(
                f"convert client bottom-right for window {hwnd}",
                "client_to_screen",
                hwnd,
                client_rect[2],
                client_rect[3],
            ),
            "client bottom-right",
        )
        width = bottom_right[0] - top_left[0]
        height = bottom_right[1] - top_left[1]
        if (width, height) != (local_width, local_height):
            raise DesktopWindowsError(
                f"client coordinate conversion changed size for hwnd {hwnd}: "
                f"local={local_width}x{local_height}, screen={width}x{height}"
            )
        return WindowInfo(hwnd, title, top_left[0], top_left[1], width, height)

    def _read_title(self, hwnd: int) -> str:
        title = self._call(f"read title for window {hwnd}", "get_window_title", hwnd)
        if not isinstance(title, str):
            raise DesktopWindowsError(f"native title for hwnd {hwnd} is not text")
        return title.strip()

    @staticmethod
    def _checked_rect(value: object, label: str) -> tuple[int, int, int, int]:
        if (
            not isinstance(value, tuple)
            or len(value) != 4
            or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        ):
            raise DesktopWindowsError(f"{label} must be four integers")
        left, top, right, bottom = value
        if right < left or bottom < top:
            raise DesktopWindowsError(f"{label} has inverted edges: {value}")
        return value

    @staticmethod
    def _checked_point(value: object, label: str) -> tuple[int, int]:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        ):
            raise DesktopWindowsError(f"{label} must be two integers")
        return value

    def _call(self, operation: str, method_name: str, *args: object):
        method = getattr(self._native, method_name, None)
        if not callable(method):
            raise DesktopWindowsError(f"native adapter has no {method_name} method")
        try:
            return method(*args)
        except DesktopWindowsError:
            raise
        except Exception as error:
            raise DesktopWindowsError(f"cannot {operation}: {error}") from error


class _CtypesDesktopNative:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DesktopWindowsError("Win32 desktop API is only available on Windows")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._configure_functions()

    def _configure_functions(self) -> None:
        user32 = self._user32
        user32.IsWindow.argtypes = (ctypes.wintypes.HWND,)
        user32.IsWindow.restype = ctypes.wintypes.BOOL
        user32.IsWindowVisible.argtypes = (ctypes.wintypes.HWND,)
        user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = (ctypes.wintypes.HWND,)
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPWSTR,
            ctypes.c_int,
        )
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClientRect.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.wintypes.RECT),
        )
        user32.GetClientRect.restype = ctypes.wintypes.BOOL
        user32.GetWindowRect.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.wintypes.RECT),
        )
        user32.GetWindowRect.restype = ctypes.wintypes.BOOL
        user32.ClientToScreen.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.wintypes.POINT),
        )
        user32.ClientToScreen.restype = ctypes.wintypes.BOOL
        user32.PostMessageW.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )
        user32.PostMessageW.restype = ctypes.wintypes.BOOL
        user32.ShowWindow.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        user32.ShowWindow.restype = ctypes.wintypes.BOOL
        user32.SetForegroundWindow.argtypes = (ctypes.wintypes.HWND,)
        user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.SetWindowPos.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.wintypes.UINT,
        )
        user32.SetWindowPos.restype = ctypes.wintypes.BOOL
        self._dwmapi.DwmGetWindowAttribute.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        )
        self._dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    def enable_dpi_awareness(self) -> None:
        target = ctypes.c_void_p(-4)
        setter = self._user32.SetProcessDpiAwarenessContext
        setter.argtypes = (ctypes.c_void_p,)
        setter.restype = ctypes.wintypes.BOOL
        if setter(target):
            return
        get_context = self._user32.GetThreadDpiAwarenessContext
        get_context.argtypes = ()
        get_context.restype = ctypes.c_void_p
        contexts_equal = self._user32.AreDpiAwarenessContextsEqual
        contexts_equal.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        contexts_equal.restype = ctypes.wintypes.BOOL
        current = get_context()
        if current and contexts_equal(current, target):
            return
        self._raise_last_error("SetProcessDpiAwarenessContext")

    def enum_windows(self) -> list[int]:
        handles: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )

        def visit(hwnd, _lparam) -> bool:
            handles.append(int(hwnd))
            return True

        callback = callback_type(visit)
        enum_windows = self._user32.EnumWindows
        enum_windows.argtypes = (callback_type, ctypes.wintypes.LPARAM)
        enum_windows.restype = ctypes.wintypes.BOOL
        ctypes.set_last_error(0)
        if not enum_windows(callback, 0):
            self._raise_last_error("EnumWindows")
        return handles

    def is_window(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindow(ctypes.wintypes.HWND(hwnd)))

    def is_window_visible(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindowVisible(ctypes.wintypes.HWND(hwnd)))

    def get_window_title(self, hwnd: int) -> str:
        length = int(self._user32.GetWindowTextLengthW(ctypes.wintypes.HWND(hwnd)))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = int(
            self._user32.GetWindowTextW(
                ctypes.wintypes.HWND(hwnd), buffer, length + 1
            )
        )
        if copied <= 0:
            self._raise_last_error("GetWindowTextW")
        return buffer.value

    def get_client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = ctypes.wintypes.RECT()
        if not self._user32.GetClientRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect)):
            self._raise_last_error("GetClientRect")
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def get_window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = ctypes.wintypes.RECT()
        if not self._user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect)):
            self._raise_last_error("GetWindowRect")
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def get_extended_frame_bounds(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = ctypes.wintypes.RECT()
        result = self._dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result != 0:
            raise DesktopWindowsError(f"DwmGetWindowAttribute failed with HRESULT {result}")
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        point = ctypes.wintypes.POINT(x, y)
        if not self._user32.ClientToScreen(
            ctypes.wintypes.HWND(hwnd), ctypes.byref(point)
        ):
            self._raise_last_error("ClientToScreen")
        return int(point.x), int(point.y)

    def post_message(
        self, hwnd: int, message: int, wparam: int, lparam: int
    ) -> bool:
        return bool(
            self._user32.PostMessageW(
                ctypes.wintypes.HWND(hwnd), message, wparam, lparam
            )
        )

    def show_window(self, hwnd: int, command: int) -> None:
        self._user32.ShowWindow(ctypes.wintypes.HWND(hwnd), command)

    def set_foreground_window(self, hwnd: int) -> bool:
        return bool(self._user32.SetForegroundWindow(ctypes.wintypes.HWND(hwnd)))

    def get_foreground_window(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def set_window_size(self, hwnd: int, width: int, height: int) -> bool:
        flags = SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
        return bool(
            self._user32.SetWindowPos(
                ctypes.wintypes.HWND(hwnd),
                None,
                0,
                0,
                width,
                height,
                flags,
            )
        )

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        raise DesktopWindowsError(
            f"{operation} failed with Windows error {ctypes.get_last_error()}"
        )


def _require_info(info: WindowInfo) -> WindowInfo:
    if not isinstance(info, WindowInfo):
        raise TypeError("info must be WindowInfo")
    return info


def _require_rule(rule: WindowRule) -> WindowRule:
    if not isinstance(rule, WindowRule):
        raise TypeError("rule must be WindowRule")
    return rule


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _client_coordinate(value: int, name: str, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"client {name} coordinate must be an integer")
    if not 0 <= value < limit:
        raise ValueError(f"client {name} coordinate {value} is outside [0, {limit})")
    if value > MAX_CLIENT_COORDINATE:
        raise ValueError(
            f"client {name} coordinate {value} exceeds signed LPARAM range"
        )
    return value


__all__ = [
    "DesktopWindowsError",
    "WindowUnavailableError",
    "Win32DesktopApi",
    "WindowInfo",
    "WindowRule",
]
