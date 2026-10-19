"""Foreground mouse input driven by the pet's rendered animation phases."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math

from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [('mouse', _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', wt.DWORD), ('data', _InputUnion)]


class ForegroundMouse:
    """One operation owns its pressed button and releases it on every exit."""

    def __init__(self, info: WindowInfo, kind: str, *, api=None, user32=None):
        if kind not in ('click', 'right_click', 'double_click', 'drag'):
            raise ValueError('不支持的前台鼠标操作')
        self.info, self.kind = info, kind
        self.api = api if api is not None else Win32DesktopApi(configure_dpi_awareness=False)
        self.user32 = user32 if user32 is not None else ctypes.WinDLL('user32', use_last_error=True)
        if user32 is None:
            self.user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_Input), ctypes.c_int]
            self.user32.SendInput.restype = wt.UINT
            self.user32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
            self.user32.GetPhysicalCursorPos.restype = wt.BOOL
            self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self.user32.GetAsyncKeyState.restype = ctypes.c_short
            self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
            self.user32.GetSystemMetrics.restype = ctypes.c_int
            self.user32.WindowFromPhysicalPoint.argtypes = [wt.POINT]
            self.user32.WindowFromPhysicalPoint.restype = wt.HWND
            self.user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
            self.user32.GetAncestor.restype = wt.HWND
        self.pressed = False
        self._started = False
        self._last_position = None
        self._button_vk = 2 if kind == 'right_click' else 1
        self._down_flag, self._up_flag = (0x0008, 0x0010) if kind == 'right_click' else (0x0002, 0x0004)

    def begin(self) -> None:
        self._check_keys()
        self.api.focus(self.info)
        self.api._require_unchanged(self.info)
        self._last_position = self.cursor_position()
        self._started = True

    def cursor_position(self) -> tuple[int, int]:
        point = wt.POINT()
        if not self.user32.GetPhysicalCursorPos(ctypes.byref(point)):
            raise OSError(ctypes.get_last_error(), '无法读取鼠标位置')
        return point.x, point.y

    def _check_keys(self) -> None:
        if self.user32.GetAsyncKeyState(0x1B) & 0x8000:
            raise InterruptedError('已按 Esc 停止前台操作')
        for key in (1, 2, 4, 5, 6, 0x10, 0x11, 0x12, 0x5B, 0x5C):
            if key == self._button_vk and self.pressed:
                continue
            if self.user32.GetAsyncKeyState(key) & 0x8000:
                raise InterruptedError('检测到用户输入，已停止前台操作')

    def check(self) -> None:
        if not self._started:
            raise RuntimeError('前台鼠标操作尚未开始')
        self._check_keys()
        self.api._require_unchanged(self.info)
        if not self.api.is_foreground(self.info):
            raise InterruptedError('目标窗口已失去焦点，已停止前台操作')
        if self._last_position is not None:
            point = self.cursor_position()
            if math.dist(point, self._last_position) > 10:
                raise InterruptedError('鼠标位置被改变，已停止前台操作')

    def _send(self, flags: int, point: tuple[int, int] | None = None) -> None:
        dx = dy = 0
        if point is not None:
            left, top, width, height = (self.user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
            if width < 2 or height < 2 or not (left <= point[0] < left+width and top <= point[1] < top+height):
                raise ValueError('鼠标目标不在桌面范围内')
            dx, dy = round((point[0]-left)*65535/(width-1)), round((point[1]-top)*65535/(height-1))
            flags |= 0x0001 | 0x8000 | 0x4000  # MOVE | ABSOLUTE | VIRTUALDESK
        packet = _Input(type=0, data=_InputUnion(mouse=_MouseInput(dx, dy, 0, flags, 0, 0)))
        if self.user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(_Input)) != 1:
            raise OSError(ctypes.get_last_error(), '发送前台鼠标输入失败；请确认目标与本程序权限一致')
        if point is not None:
            self._last_position = point

    def step(self, phase: str, point: tuple[int, int]) -> None:
        """Called after rendering the matching pose, never from a separate timer."""
        self.check()
        if phase not in ('move', 'press', 'drag', 'release'):
            raise ValueError('未知前台输入阶段')
        if phase == 'drag' and not self.pressed:
            raise RuntimeError('鼠标尚未按下，不能拖拽')
        if phase == 'press':
            if self.pressed:
                raise RuntimeError('不能重复按下鼠标')
            handle = self.user32.WindowFromPhysicalPoint(wt.POINT(*point))
            root = self.user32.GetAncestor(handle, 2) if handle else 0
            if root != self.info.hwnd:
                raise InterruptedError('目标位置被其他窗口遮挡，已取消点击')
            # Mark before SendInput so any exception still takes the release path.
            self.pressed = True
            self._send(self._down_flag, point)
        elif phase == 'release':
            if not self.pressed:
                raise RuntimeError('鼠标未按下，不能松开')
            self._send(self._up_flag, point)
            self.pressed = False
        else:
            self._send(0, point)

    def close(self) -> None:
        # Release even if the target moved, lost focus, or the operation failed.
        if self.pressed:
            self._send(self._up_flag)
            self.pressed = False
        self._started = False
