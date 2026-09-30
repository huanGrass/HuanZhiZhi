"""Window Graphics Capture frames normalized to a target client rectangle."""

from __future__ import annotations

from threading import Event, Lock
from time import monotonic
from typing import Protocol

from PIL import Image


class WindowCaptureError(RuntimeError):
    """Raised when the WGC session cannot provide a verified client frame."""


class _WindowInfo(Protocol):
    hwnd: int
    width: int
    height: int


class WgcWindowCapture:
    """One rate-limited WGC session, recreated only when its HWND changes."""

    def __init__(self, *, minimum_update_interval_ms: int = 100) -> None:
        if isinstance(minimum_update_interval_ms, bool) or minimum_update_interval_ms <= 0:
            raise ValueError("minimum_update_interval_ms must be positive")
        self._interval = minimum_update_interval_ms
        self._hwnd: int | None = None
        self._control = None
        self._frame: Image.Image | None = None
        self._requested_box: tuple[int, int, int, int] | None = None
        self._frame_box: tuple[int, int, int, int] | None = None
        self._failure: BaseException | None = None
        self._last_copy_at = 0.0
        self._ready = Event()
        self._lock = Lock()

    def capture_client(self, info: _WindowInfo, *, crop_left: int, crop_top: int,
                       region: tuple[int, int, int, int] | None = None) -> Image.Image:
        left, top, right, bottom = region if region is not None else (0, 0, info.width, info.height)
        if not (0 <= left < right <= info.width and 0 <= top < bottom <= info.height):
            raise WindowCaptureError('requested region is outside the client rectangle')
        box = (crop_left+left, crop_top+top, crop_left+right, crop_top+bottom)
        with self._lock:
            self._requested_box = box
            if self._frame_box != box:
                self._ready.clear()
        self._ensure_session(info.hwnd)
        deadline = monotonic()+2
        while True:
            if not self._ready.wait(max(0, deadline-monotonic())):
                raise WindowCaptureError("WGC did not deliver the requested region within 2 seconds")
            with self._lock:
                if self._failure is not None:
                    raise WindowCaptureError(f"WGC capture failed: {self._failure}") from self._failure
                if self._frame is not None and self._frame_box == box:
                    return self._frame.copy()
                self._ready.clear()

    def close(self) -> None:
        control, self._control = self._control, None
        self._hwnd = None
        if control is not None:
            control.stop()

    def _ensure_session(self, hwnd: int) -> None:
        if hwnd == self._hwnd:
            return
        self.close()
        self._ready.clear()
        self._failure = None
        self._frame = None
        self._frame_box = None
        self._last_copy_at = 0.0
        try:
            from windows_capture import Frame, InternalCaptureControl, WindowsCapture

            capture = WindowsCapture(
                cursor_capture=False,
                window_hwnd=hwnd,
            )

            @capture.event
            def on_frame_arrived(frame: Frame, _control: InternalCaptureControl) -> None:
                # The native mapped buffer expires after this callback, so retain an RGB copy.
                now = monotonic()
                if now - self._last_copy_at < self._interval / 1000:
                    return
                with self._lock:
                    box = self._requested_box
                if box is None:
                    return
                left, top, right, bottom = box
                if left < 0 or top < 0 or right > frame.width or bottom > frame.height:
                    with self._lock:
                        self._failure = WindowCaptureError(f'WGC frame {(frame.width, frame.height)} cannot contain client crop {box}')
                    self._ready.set()
                    return
                pixels = frame.frame_buffer[top:bottom, left:right]
                image = Image.frombytes("RGB", (right-left, bottom-top), pixels.tobytes(), "raw", "BGRX")
                image.info["capture_frame_time"] = frame.timespan
                image.info["capture_received_at"] = now
                # WGC SystemRelativeTime is QPC time in 100 ns units. On
                # Windows perf_counter uses the same system-wide clock.
                image.info["capture_source_at"] = frame.timespan / 10_000_000
                with self._lock:
                    if box != self._requested_box:
                        return
                    self._failure = None
                    self._frame = image
                    self._frame_box = box
                    self._last_copy_at = now
                self._ready.set()

            @capture.event
            def on_closed() -> None:
                with self._lock:
                    self._failure = WindowCaptureError('WGC session closed')
                self._ready.set()

            self._control = capture.start_free_threaded()
            self._hwnd = hwnd
        except Exception as error:
            self._failure = error
            self._ready.set()
            raise WindowCaptureError(f"cannot start WGC for hwnd {hwnd}: {error}") from error


__all__ = ("WgcWindowCapture", "WindowCaptureError")
