"""Pure-stdlib SDK for implementing a HuanZhiZhi task subprocess."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

from huanzhizhi.parameters import parse_parameters


PROTOCOL_VERSION = 1
_VALID_MODES = {"--describe": "describe", "--run": "run"}
_stdout_lock = threading.Lock()
_describe_lock = threading.Lock()
_description_emitted = False

if os.name == "nt":
    import ctypes
    import ctypes.wintypes
    import msvcrt

    _peek_named_pipe = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    _peek_named_pipe.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.c_void_p,
    )
    _peek_named_pipe.restype = ctypes.wintypes.BOOL


class TaskSdkError(ValueError):
    """Internal strict-protocol validation error."""


def describe(
    task_id: str,
    name: str,
    description: str = "",
    *,
    presentation: str = "persistent",
    debug_steps: tuple[str, ...] = (),
    parameters: tuple[dict, ...] = (),
) -> bool:
    """Emit the sole description object in ``--describe`` mode.

    Returns ``False`` in ``--run`` mode so one task entry point can use::

        if describe("demo", "Demo task"):
            raise SystemExit(0)
        session = TaskSession("demo")
    """

    task_id = _validated_identity(task_id, "task_id")
    name = _validated_identity(name, "name")
    if not isinstance(description, str):
        _fatal("description 必须是字符串")
    if presentation not in {"persistent", "launch_only"}:
        _fatal("presentation 必须是 persistent 或 launch_only")
    if not isinstance(debug_steps, tuple) or any(
        not isinstance(step, str) or not step.strip() for step in debug_steps
    ):
        _fatal("debug_steps 必须是非空字符串元组")
    try:
        parse_parameters(list(parameters))
    except ValueError as error:
        _fatal(str(error))
    if _entry_mode() == "run":
        return False
    global _description_emitted
    with _describe_lock:
        if _description_emitted:
            _fatal("--describe 模式只能输出一个描述对象")
        _description_emitted = True
        payload = {
                "protocol_version": PROTOCOL_VERSION,
                "id": task_id,
                "name": name,
                "description": description,
                "presentation": presentation,
            }
        if parameters:
            payload["parameters"] = list(parameters)
        if debug_steps:
            payload["debug_steps"] = list(debug_steps)
        _emit_json(payload)
    return True


class TaskSession:
    """Validated host session with asynchronous cooperative-stop reception."""

    def __init__(self, task_id: str) -> None:
        self.task_id = _validated_identity(task_id, "task_id")
        if _entry_mode() != "run":
            _fatal("TaskSession 只能在 --run 模式创建")
        self._reader = _InputReader(sys.stdin.buffer)
        try:
            start = _parse_json_line(self._reader.read_line(), "start")
            _require_exact_fields(start, {"protocol_version", "type", "task_id", "options"}, "start")
            _require_version(start)
            if start["type"] != "start":
                raise TaskSdkError("首条消息 type 必须是 start")
            if start["task_id"] != self.task_id:
                raise TaskSdkError(
                    f"start.task_id 与任务不匹配：期望 {self.task_id!r}，收到 {start['task_id']!r}"
                )
            if not isinstance(start["options"], dict):
                raise TaskSdkError("start.options 必须是 JSON 对象")
        except TaskSdkError as error:
            _fatal(f"非法 start 消息：{error}")

        self._options = start["options"]
        self._stop_event = threading.Event()
        self._step_event = threading.Event()
        self._state_lock = threading.Lock()
        self._input_error: str | None = None
        self._finished = False
        self._input_thread = threading.Thread(
            target=self._read_control_messages,
            name=f"huanzhizhi-task-control-{self.task_id}",
            daemon=True,
        )
        self._input_thread.start()

    @property
    def options(self) -> dict[str, object]:
        return dict(self._options)

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        return self._stop_event.wait(timeout)

    def wait_for_step(self) -> bool:
        while not self._stop_event.is_set():
            if self._step_event.wait(0.05):
                self._step_event.clear()
                return True
        return False

    def emit_status(self, status_title: str, title: str, detail: str) -> None:
        self._emit_event(
            "status",
            status_title=_required_string(status_title, "status_title"),
            title=_required_string(title, "title"),
            detail=_required_string(detail, "detail"),
        )

    def emit_log(self, message: str) -> None:
        self._emit_event("log", message=_required_string(message, "message"))

    def emit_speak(self, text: str, audio_path: str | Path | None, action: str) -> None:
        if isinstance(audio_path, Path):
            audio_path = str(audio_path)
        if audio_path is not None and not isinstance(audio_path, str):
            _fatal("audio_path 必须是字符串、Path 或 null")
        self._emit_event(
            "speak",
            text=_required_string(text, "text"),
            audio_path=audio_path,
            action=_required_string(action, "action"),
        )

    def emit_point(self, x: int, y: int) -> None:
        if isinstance(x, bool) or not isinstance(x, int) or isinstance(y, bool) or not isinstance(y, int):
            _fatal("point.x 和 point.y 必须是整数")
        self._emit_event("point", x=x, y=y)

    def emit_hint(self, points: list[tuple[int, int]], kind: str, label: str) -> None:
        """Request a visual-only hint in physical screen coordinates."""
        expected = {"clear": {0}, "click": {1, 2}, "drag": {3}, "line": {2}}
        if kind not in expected or not isinstance(points, list) or len(points) not in expected[kind]:
            _fatal("hint 的类型或坐标数量无效")
        for point in points:
            if not isinstance(point, (tuple, list)) or len(point) != 2 or any(
                isinstance(value, bool) or not isinstance(value, int) for value in point
            ):
                _fatal("hint 坐标必须是两个整数")
        if kind == "line" and tuple(points[0]) == tuple(points[1]):
            _fatal("hint 直线的起点和终点不能相同")
        self._emit_event("hint", kind=kind, points=[list(point) for point in points],
                         label=_required_string(label, "hint.label"))

    def emit_preview(
        self,
        image_path: str,
        width: int,
        height: int,
        analysis: dict[str, object],
        grid_overlay: dict[str, list[int]],
    ) -> None:
        image_path = _required_string(image_path, "preview.image_path")
        width = _positive_integer(width, "preview.width")
        height = _positive_integer(height, "preview.height")
        if not isinstance(analysis, dict):
            _fatal("preview.analysis 必须是 JSON 对象")
        _validate_json_value(analysis, "preview.analysis", set())
        validated_grid = _validated_grid_overlay(grid_overlay, width, height)
        self._emit_event(
            "preview",
            image_path=image_path,
            width=width,
            height=height,
            analysis=analysis,
            grid_overlay=validated_grid,
        )

    def emit_finished(
        self,
        status_title: str,
        title: str,
        detail: str,
        *,
        error: str | None = None,
        stopped_by_request: bool = False,
    ) -> None:
        if error is not None and not isinstance(error, str):
            _fatal("finished.error 必须是字符串或 null")
        if not isinstance(stopped_by_request, bool):
            _fatal("finished.stopped_by_request 必须是 bool")
        self._ensure_input_valid()
        with self._state_lock:
            if self._finished:
                _fatal("finished 事件最多只能发送一次")
            self._finished = True
        _emit_json(
            {
                "protocol_version": PROTOCOL_VERSION,
                "type": "finished",
                "status_title": _required_string(status_title, "status_title"),
                "title": _required_string(title, "title"),
                "detail": _required_string(detail, "detail"),
                "error": error,
                "stopped_by_request": stopped_by_request,
            }
        )

    def _emit_event(self, event_type: str, **fields: object) -> None:
        self._ensure_input_valid()
        with self._state_lock:
            if self._finished:
                _fatal("finished 之后不能继续发送事件")
        _emit_json({"protocol_version": PROTOCOL_VERSION, "type": event_type, **fields})

    def _ensure_input_valid(self) -> None:
        with self._state_lock:
            input_error = self._input_error
        if input_error is not None:
            raise SystemExit(2)

    def _read_control_messages(self) -> None:
        while True:
            try:
                while not self._reader.wait_until_readable(0.02):
                    pass
                line = self._reader.read_line()
            except TaskSdkError as error:
                with self._state_lock:
                    already_finished = self._finished
                if not already_finished:
                    self._record_input_error(str(error))
                return
            try:
                message = _parse_json_line(line, "control")
                _require_exact_fields(message, {"protocol_version", "type"}, "control")
                _require_version(message)
                if message["type"] == "stop":
                    self._stop_event.set()
                elif message["type"] == "step":
                    self._step_event.set()
                else:
                    raise TaskSdkError("控制消息 type 必须是 stop 或 step")
            except TaskSdkError as error:
                self._record_input_error(f"非法 stop 消息：{error}")
                return

    def _record_input_error(self, message: str) -> None:
        with self._state_lock:
            if self._input_error is not None:
                return
            self._input_error = message
        _write_stderr(f"幻之之任务 SDK 错误：{message}")
        self._stop_event.set()


def run_session(task_id: str) -> TaskSession:
    return TaskSession(task_id)


class _InputReader:
    def __init__(self, stream: BinaryIO) -> None:
        try:
            self._fd = stream.fileno()
        except (AttributeError, OSError) as error:
            raise TaskSdkError(f"stdin 没有可读取的文件描述符：{error}") from error
        self._buffer = bytearray()

    def read_line(self) -> bytes:
        while b"\n" not in self._buffer:
            chunk = os.read(self._fd, 4096)
            if not chunk:
                if self._buffer:
                    raise TaskSdkError("stdin 最后一条 JSONL 消息缺少换行")
                raise TaskSdkError("stdin 在收到下一条协议消息前关闭")
            self._buffer.extend(chunk)
        line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        return bytes(line.rstrip(b"\r"))

    def wait_until_readable(self, timeout_seconds: float) -> bool:
        """Avoid a blocking Windows pipe read while native modules are loading."""
        if os.name != "nt" or self._buffer:
            return True
        available = ctypes.wintypes.DWORD()
        handle = msvcrt.get_osfhandle(self._fd)
        if not _peek_named_pipe(handle, None, 0, None, ctypes.byref(available), None):
            return True
        if available.value:
            return True
        time.sleep(timeout_seconds)
        return False


def _entry_mode() -> str:
    arguments = sys.argv[1:]
    if len(arguments) != 1 or arguments[0] not in _VALID_MODES:
        _fatal("入口参数必须且只能是 --describe 或 --run")
    return _VALID_MODES[arguments[0]]


def _validated_identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fatal(f"{field} 必须是无首尾空白的非空字符串")
    return value


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fatal(f"{field} 必须是非空字符串")
    return value


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fatal(f"{field} 必须是正整数")
    return value


def _validated_grid_overlay(
    value: object,
    width: int,
    height: int,
) -> dict[str, list[int]]:
    if not isinstance(value, dict):
        _fatal("preview.grid_overlay 必须是 JSON 对象")
    expected = {"rows", "columns"}
    actual = set(value)
    if actual != expected:
        _fatal(
            "preview.grid_overlay 字段不匹配，"
            f"缺失={sorted(expected - actual)}，多余={sorted(actual - expected)}"
        )
    rows = _validated_coordinates(value["rows"], height, "preview.grid_overlay.rows")
    columns = _validated_coordinates(value["columns"], width, "preview.grid_overlay.columns")
    return {"rows": rows, "columns": columns}


def _validated_coordinates(value: object, limit: int, field: str) -> list[int]:
    if not isinstance(value, list):
        _fatal(f"{field} 必须是整数数组")
    previous = -1
    result: list[int] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            _fatal(f"{field} 必须只包含整数")
        if coordinate < 0 or coordinate >= limit:
            _fatal(f"{field} 坐标必须在 [0, {limit}) 内")
        if coordinate <= previous:
            _fatal(f"{field} 坐标必须严格递增")
        result.append(coordinate)
        previous = coordinate
    return result


def _validate_json_value(value: object, path: str, ancestors: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fatal(f"{path} 包含 NaN 或 Infinity")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in ancestors:
            _fatal(f"{path} 包含循环引用")
        ancestors.add(identity)
        try:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    _validate_json_value(item, f"{path}[{index}]", ancestors)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        _fatal(f"{path} 的对象键必须是字符串")
                    _validate_json_value(item, f"{path}.{key}", ancestors)
        finally:
            ancestors.remove(identity)
        return
    _fatal(f"{path} 包含非 JSON 类型：{type(value).__name__}")


def _parse_json_line(line: bytes, label: str) -> dict[str, object]:
    if not line.strip():
        raise TaskSdkError(f"{label} 消息不能为空")
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TaskSdkError(f"{label} 消息不是有效 UTF-8：{error}") from error
    try:
        payload = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except (json.JSONDecodeError, TaskSdkError) as error:
        raise TaskSdkError(f"{label} 消息不是严格 JSON：{error}") from error
    if not isinstance(payload, dict):
        raise TaskSdkError(f"{label} 消息必须是单个 JSON 对象")
    return payload


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TaskSdkError(f"JSON 对象存在重复字段：{key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise TaskSdkError(f"JSON 不允许常量 {value}")


def _require_version(payload: dict[str, object]) -> None:
    version = payload.get("protocol_version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise TaskSdkError(f"不支持的 protocol_version：{version}")


def _require_exact_fields(payload: dict[str, object], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        raise TaskSdkError(
            f"{label} 字段不匹配，缺失={sorted(expected - actual)}，多余={sorted(actual - expected)}"
        )


def _emit_json(payload: dict[str, object]) -> None:
    try:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        _fatal(f"事件不是严格 JSON：{error}")
    with _stdout_lock:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()


def _write_stderr(message: str) -> None:
    sys.stderr.buffer.write((message + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()


def _fatal(message: str) -> None:
    _write_stderr(f"幻之之任务 SDK 错误：{message}")
    raise SystemExit(2)


__all__ = (
    "PROTOCOL_VERSION",
    "TaskSdkError",
    "TaskSession",
    "describe",
    "run_session",
)
