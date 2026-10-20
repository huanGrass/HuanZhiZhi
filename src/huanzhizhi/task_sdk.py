"""Pure-stdlib SDK for implementing a HuanZhiZhi task subprocess."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from huanzhizhi.parameters import parse_parameters, validate_values
from huanzhizhi.tasks.catalog import read_skill_manifest


PROTOCOL_VERSION = 1
_VALID_MODES = {"--describe": "describe", "--run": "run", "--check-environment": "check_environment"}
_stdout_lock = threading.Lock()
_describe_lock = threading.Lock()
_description_emitted = False
_active_session = None


def _input_mode() -> str:
    mode = os.environ.get('HUANZHIZHI_INPUT_MODE', 'background')
    if mode not in {'background', 'foreground'}:
        raise ValueError('HUANZHIZHI_INPUT_MODE 必须是 background 或 foreground')
    return mode


def _write_event_file(events: str | Path, payload: dict) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    # Never recreate a channel removed when the host stops the turn.
    descriptor = os.open(events, os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'wb', buffering=0) as stream:
        stream.write(encoded)


def _validated_input_request_id(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 32
            or any(character not in '0123456789abcdef' for character in value)):
        raise TaskSdkError('input request_id 必须是 32 位小写十六进制字符串')
    return value


def input_result_path(events: str | Path, request_id: str) -> Path:
    """The host and child derive replies only from the existing event channel."""
    return Path(str(events) + '.replies') / (_validated_input_request_id(request_id) + '.json')


def _parse_input_result(message: dict) -> tuple[str, str | None]:
    _require_exact_fields(message, {'protocol_version', 'type', 'request_id', 'error'}, 'input_result')
    _require_version(message)
    if message['type'] != 'input_result':
        raise TaskSdkError('输入响应 type 必须是 input_result')
    request_id = _validated_input_request_id(message['request_id'])
    error = message['error']
    if error is not None and not isinstance(error, str):
        raise TaskSdkError('input_result.error 必须是字符串或 null')
    return request_id, error


def _request_foreground_input(window: dict, kind: str, x: int, y: int,
                              end_x: int | None = None, end_y: int | None = None,
                              duration: float = 0.0, *, timeout: float = 30.0) -> None:
    """Wait for host-owned input; errors never fall back to child-side input."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('输入等待超时必须是大于零的有限秒数')
    if _active_session is not None and _active_session.stop_requested:
        raise InterruptedError('已停止，未继续操作')
    request = dict(protocol_version=PROTOCOL_VERSION, type='input_request',
                   request_id=uuid.uuid4().hex, kind=kind, window=dict(window),
                   x=x, y=y, end_x=end_x, end_y=end_y, duration=duration)
    events = os.environ.get('HUANZHIZHI_CLICK_EVENTS')
    if not events:
        if _active_session is None:
            raise RuntimeError('前台输入需要连接桌宠：请从任务或技能分析入口运行。')
        _active_session._request_input(request, timeout)
        return
    reply = input_result_path(events, request['request_id'])
    _write_event_file(events, request)
    received = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if _active_session is not None and _active_session.stop_requested:
                raise InterruptedError('已停止，已取消等待输入')
            if not Path(events).is_file():
                raise InterruptedError('输入通道已关闭，已取消等待输入')
            try:
                response = reply.read_bytes()
            except FileNotFoundError:
                response = None
            if response is not None:
                try:
                    request_id, error = _parse_input_result(_parse_json_line(response, 'input_result'))
                    if request_id != request['request_id']:
                        raise TaskSdkError('输入响应 request_id 与请求不匹配')
                except TaskSdkError as error:
                    raise RuntimeError(f'非法输入响应：{error}') from error
                received = True
                if error is not None:
                    raise RuntimeError(f'前台输入失败：{error}')
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('等待前台输入完成超时')
            time.sleep(.02)
    finally:
        if not received:
            try:
                _write_event_file(events, dict(protocol_version=PROTOCOL_VERSION, type='input_cancel',
                                              request_id=request['request_id']))
            except OSError:
                pass  # Closing the channel already cancels its outstanding input.
        reply.unlink(missing_ok=True)


def _emit_click_point(x: int, y: int) -> None:
    """Route desktop clicks to the task host, including AI development children."""
    if _active_session is not None and _active_session.stop_requested:
        raise InterruptedError('已停止，未继续点击')
    events = os.environ.get('HUANZHIZHI_CLICK_EVENTS')
    if events:
        _write_event_file(events, dict(protocol_version=PROTOCOL_VERSION, type='point', x=x, y=y))
    elif _active_session is not None:
        _active_session.emit_point(x, y)
    else:
        raise RuntimeError('点击需要连接桌宠：请从任务或技能分析入口运行。')

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


class ObservationPending(ValueError):
    """A skill's read-only check found a state that may still settle."""


def confirm_observation(session, observe, *, retries=3, interval=.2, timeout=None, records=None):
    """Return a fresh validated observation, retrying only ObservationPending.

    ``observe`` must reacquire state and check it against the same reference on
    every call. Never put clicks, submissions or other target mutations in it.
    ``retries`` counts additional checks; changing errors share the same limit.
    ``timeout`` optionally limits elapsed time before another check starts; a
    running observation must implement its own I/O timeout and cooperative stop.
    Other exceptions propagate immediately. Waiting and successful observations
    both honor cooperative stop. Optional records receive bounded attempt data.
    """
    if type(retries) is not int or retries < 0:
        raise ValueError('复检次数必须是非负整数')
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
        raise ValueError('复检间隔必须是大于零的有限秒数')
    if timeout is not None and (
            type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('复检超时必须是大于零的有限秒数')
    started = time.monotonic()
    deadline = None if timeout is None else started + timeout
    for attempt in range(retries + 1):
        if session.stop_requested:
            raise InterruptedError('用户已停止')
        try:
            result = observe()
        except ObservationPending as error:
            if session.stop_requested:
                raise InterruptedError('用户已停止') from error
            if records is not None:
                records.append(dict(attempt=attempt + 1, elapsed=time.monotonic() - started, error=str(error)))
            session.emit_log(f'状态确认 {attempt + 1}/{retries + 1}：{error}')
            if attempt == retries:
                raise
            remaining = interval if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                raise
            if session.wait_for_stop(min(interval, remaining)):
                raise InterruptedError('用户已停止') from error
            if deadline is not None and time.monotonic() >= deadline:
                raise
        else:
            if session.stop_requested:
                raise InterruptedError('用户已停止')
            if records is not None:
                records.append(dict(attempt=attempt + 1, elapsed=time.monotonic() - started, error=None))
            if attempt:
                session.emit_log(f'状态复检通过（第 {attempt + 1} 次检测）')
            return result


def load_skill(entry: str | Path | None = None) -> dict:
    """Read skill.json beside the entry; metadata is never defined in Python."""
    if getattr(sys, 'frozen', False):
        entry = sys.executable
    return read_skill_manifest(Path(entry or sys.argv[0]))


def runtime_directory(entry: str | Path | None = None) -> Path:
    """Locate local runtime data without creating directories or reading state.

    Use runs/ for evidence, cache/ for disposable data and state/ for persistent
    data. The host excludes the skill-root .runtime from distributable packages.
    Standalone legacy executables share a parent, so each uses its own subfolder.
    """
    path = Path(sys.executable if getattr(sys, 'frozen', False) else entry or sys.argv[0]).resolve()
    directory = path.parent / '.runtime'
    return directory / path.stem if path.suffix.lower() == '.exe' else directory


def describe(entry: str | Path | None = None, *, environment_checks: dict | None = None) -> bool:
    """Emit the manifest in ``--describe`` mode; discovery reads JSON directly.

    Returns ``False`` in ``--run`` mode so one task entry point can use::

        if describe(__file__):
            raise SystemExit(0)
        session = TaskSession(__file__)

    Optional ``environment_checks`` maps dependency names to read-only, no-argument
    functions. Each returns a non-empty success description or raises an exception
    explaining an unmet requirement. Checks run before ``--run`` and on explicit
    ``--check-environment``. Names must exactly match skill.json's
    ``environment_checks``. Import optional dependencies inside each check.
    """

    metadata = load_skill(entry)
    checks = {} if environment_checks is None else environment_checks
    if not isinstance(checks, dict) or any(
        not isinstance(label, str) or not label.strip() or not callable(check)
        for label, check in checks.items()
    ):
        _fatal("environment_checks 必须是依赖名称到检查函数的字典")
    if set(checks) != set(metadata.get('environment_checks', [])):
        _fatal("环境检查函数必须与 skill.json 的 environment_checks 完全一致")
    mode = _entry_mode()
    if mode in {"run", "check_environment"}:
        failures = []
        for label in metadata.get('environment_checks', []):
            try:
                detail = checks[label]()
                if not isinstance(detail, str) or not detail.strip():
                    raise ValueError("检查函数必须返回非空的通过说明，未满足要求时抛出异常")
                satisfied = True
            except Exception as error:
                satisfied = False
                detail = str(error) or type(error).__name__
                failures.append(f"{label}：{detail}")
            if mode == "check_environment":
                _emit_json({"name": label, "satisfied": satisfied, "detail": detail})
        if mode == "check_environment":
            return True
        if failures:
            _fatal("技能依赖环境不满足：" + "；".join(failures))
        return False
    global _description_emitted
    with _describe_lock:
        if _description_emitted:
            _fatal("--describe 模式只能输出一个描述对象")
        _description_emitted = True
        _emit_json(metadata)
    return True


class TaskSession:
    """Validated host session with asynchronous cooperative-stop reception.

    Parameter definitions and defaults come exclusively from skill.json.
    Only definitions with ``runtime=True`` are writable;
    the task must call ``apply_parameter_updates`` at safe action boundaries.
    """

    def __init__(self, entry: str | Path | None = None) -> None:
        self.skill = load_skill(entry)
        self.task_id = self.skill['id']
        self.runtime_dir = runtime_directory(entry)
        _input_mode()
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
        parameters = parse_parameters(self.skill.get('parameters', []))
        self._runtime_parameters = tuple(item for item in parameters if item.runtime)
        self._options.update(validate_values(parameters, {
            item.key: self._options[item.key] for item in parameters if item.key in self._options
        }))
        self._pending_parameters: tuple[int, dict] | None = None
        self._last_parameter_request = 0
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._step_event = threading.Event()
        self._state_lock = threading.Lock()
        self._input_error: str | None = None
        self._pending_input_id: str | None = None
        self._input_result_ready = threading.Event()
        self._input_result_error: str | None = None
        self._cancelled_input_ids: set[str] = set()
        self._pet_lease: str | None = None
        self._pet_result_ready = threading.Event()
        self._pet_result: dict | None = None
        self._cancelled_pet_leases: set[str] = set()
        self._pause_polled = False
        self._finished = False
        self._input_thread = threading.Thread(
            target=self._read_control_messages,
            name=f"huanzhizhi-task-control-{self.task_id}",
            daemon=True,
        )
        self._input_thread.start()
        global _active_session
        _active_session = self

    @property
    def options(self) -> dict[str, object]:
        with self._state_lock:
            return dict(self._options)

    def apply_parameter_updates(self) -> dict[str, object]:
        """Apply queued runtime values at a task-chosen safe point, then acknowledge.

        Call from the task work thread before the next action and during idle
        waits. Receiving a control message alone never mutates the task options.
        """
        self._ensure_input_valid()
        with self._state_lock:
            pending = self._pending_parameters
            if self._stop_event.is_set() or self._finished:
                return dict(self._options)
            self._pending_parameters = None
            if pending is not None:
                self._options.update(pending[1])
            options = dict(self._options)
        if pending is not None:
            self._emit_event("parameters_applied", request_id=pending[0], values=pending[1])
        return options

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    @property
    def pause_requested(self) -> bool:
        return self._pause_event.is_set()

    def wait_if_paused(self, *, cancel_event: threading.Event | None = None) -> bool:
        """Pause at an action boundary, keeping parameters and stop responsive.

        Call from the task work thread before acquiring the next observation,
        after confirming the previous action. True means a pause occurred:
        discard cached observations and revalidate before any further input.
        Long computations may finish before reaching this boundary; the host
        displays 'pausing' until this method acknowledges it.
        Workers may pass their shutdown event so closing a task-owned window
        also exits the pause wait without blocking its GUI event loop.
        """
        self._ensure_input_valid()
        if not self._pause_event.is_set() or self._stop_event.is_set():
            return False
        self._emit_event('pause_state', paused=True)
        while self._pause_event.is_set() and not self._stop_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                return True
            self.apply_parameter_updates()
            self._stop_event.wait(.05)
        self._ensure_input_valid()
        if not self._stop_event.is_set() and not (cancel_event is not None and cancel_event.is_set()):
            self.apply_parameter_updates()
            self._emit_event('pause_state', paused=False)
        return True

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        return self._stop_event.wait(timeout)

    def poll_pause(self) -> bool:
        """Nonblocking pause boundary for GUI loops (instead of wait_if_paused).

        Call before each simulation/work tick; skip work while True. GUI event
        processing and stop handling must continue while paused.
        """
        self._ensure_input_valid()
        paused = self.pause_requested
        if not self.stop_requested and paused != self._pause_polled:
            self._emit_event('pause_state', paused=paused)
            self._pause_polled = paused
        return paused

    def wait_for_step(self) -> bool:
        while not self._stop_event.is_set():
            self.wait_if_paused()
            if self._stop_event.is_set():
                return False
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

    def acquire_pet(self, *, timeout: float = 5.0) -> dict:
        """Temporarily own the real pet. Geometry uses Qt global logical pixels.

        Pose bounds/contact offsets are relative to the update_pet pivot.
        Call release_pet in finally. Host exit/stop also revokes ownership.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('接管等待时间必须大于零')
        if self._pet_lease is not None:
            raise RuntimeError('已经申请了桌宠接管')
        if self.stop_requested:
            raise InterruptedError('技能已停止')
        self._pet_lease = uuid.uuid4().hex
        self._pet_result = None
        self._pet_result_ready.clear()
        try:
            self._emit_event('pet_acquire', lease=self._pet_lease)
            deadline = time.monotonic() + timeout
            while not self._pet_result_ready.wait(.02):
                self._ensure_input_valid()
                if self.stop_requested:
                    raise InterruptedError('接管已取消')
                if time.monotonic() >= deadline:
                    raise TimeoutError('桌宠宿主未确认接管')
            if self.stop_requested:
                raise InterruptedError('接管已取消')
            if self._pet_result['error'] is not None:
                raise RuntimeError(self._pet_result['error'])
            return self._pet_result['geometry']
        except BaseException:
            self.release_pet()
            raise

    def update_pet(self, x: float, y: float, *, facing: str = 'left', pose: str = 'idle') -> None:
        """Publish the latest visual state; never moves or clicks the cursor."""
        from huanzhizhi.tasks.protocol import validate_pet_state
        validate_pet_state(x, y, facing, pose)
        if self._pet_lease is None or not self._pet_result_ready.is_set():
            raise RuntimeError('请先接管桌宠')
        if self.stop_requested:
            raise InterruptedError('技能已停止')
        self._emit_event('pet_update', lease=self._pet_lease, x=x, y=y, facing=facing, pose=pose)

    def release_pet(self) -> None:
        """Idempotent release; the host restores the saved resting position."""
        lease = self._pet_lease
        if lease is not None:
            with self._state_lock:
                if not self._pet_result_ready.is_set():
                    self._cancelled_pet_leases.add(lease)
                self._pet_lease = None
            self._emit_event('pet_release', lease=lease)

    def _request_input(self, request: dict, timeout: float) -> None:
        self._ensure_input_valid()
        with self._state_lock:
            if self._pending_input_id is not None:
                raise RuntimeError('上一项前台输入尚未完成')
            if self._stop_event.is_set():
                raise InterruptedError('已停止，未继续操作')
            self._pending_input_id = request['request_id']
            self._input_result_error = None
            self._input_result_ready.clear()
        sent = received = False
        try:
            self._emit_event('input_request', **{
                key: value for key, value in request.items() if key not in {'protocol_version', 'type'}
            })
            sent = True
            deadline = time.monotonic() + timeout
            while True:
                self._ensure_input_valid()
                if self._stop_event.is_set():
                    raise InterruptedError('已停止，已取消等待输入')
                if self._input_result_ready.wait(.02):
                    received = True
                    with self._state_lock:
                        error = self._input_result_error
                    if error is not None:
                        raise RuntimeError(f'前台输入失败：{error}')
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError('等待前台输入完成超时')
        finally:
            with self._state_lock:
                if sent and not received:
                    self._cancelled_input_ids.add(request['request_id'])
                self._pending_input_id = None
            if sent and not received:
                try:
                    _emit_json(dict(protocol_version=PROTOCOL_VERSION, type='input_cancel',
                                    request_id=request['request_id']))
                except OSError:
                    pass

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
                _require_version(message)
                if message.get('type') == 'pet_result':
                    _require_exact_fields(message, {'protocol_version', 'type', 'lease', 'geometry', 'error'}, 'pet_result')
                    lease = _validated_input_request_id(message['lease'])
                    if message['error'] is not None and not isinstance(message['error'], str):
                        raise TaskSdkError('桌宠接管错误必须是字符串或 null')
                    if message['error'] is None and not isinstance(message['geometry'], dict):
                        raise TaskSdkError('桌宠接管结果缺少几何信息')
                    with self._state_lock:
                        if lease in self._cancelled_pet_leases:
                            self._cancelled_pet_leases.remove(lease)
                            continue
                        if lease != self._pet_lease or self._pet_result_ready.is_set():
                            raise TaskSdkError('桌宠接管响应不匹配或重复')
                        self._pet_result = message
                        self._pet_result_ready.set()
                    continue
                if message.get('type') == 'input_result':
                    request_id, error = _parse_input_result(message)
                    with self._state_lock:
                        if request_id in self._cancelled_input_ids:
                            self._cancelled_input_ids.remove(request_id)
                            continue
                        if request_id != self._pending_input_id or self._input_result_ready.is_set():
                            raise TaskSdkError('输入响应 request_id 与待执行请求不匹配或重复')
                        self._input_result_error = error
                        self._input_result_ready.set()
                    continue
                if message.get("type") == "update_parameters":
                    _require_exact_fields(message, {"protocol_version", "type", "request_id", "values"}, "control")
                    request_id, values = message["request_id"], message["values"]
                    if type(request_id) is not int or request_id <= self._last_parameter_request:
                        raise TaskSdkError("参数更新 request_id 必须是递增正整数")
                    if not isinstance(values, dict) or not values:
                        raise TaskSdkError("参数更新 values 必须是非空对象")
                    try:
                        values = validate_values(tuple(item for item in self._runtime_parameters if item.key in values), values)
                    except ValueError as error:
                        raise TaskSdkError(str(error)) from error
                    with self._state_lock:
                        if self._pending_parameters is not None:
                            raise TaskSdkError("上一项参数更新尚未应用")
                        self._pending_parameters = (request_id, values)
                        self._last_parameter_request = request_id
                    continue
                _require_exact_fields(message, {"protocol_version", "type"}, "control")
                if message["type"] == "stop":
                    self._stop_event.set()
                elif message['type'] == 'pause':
                    self._pause_event.set()
                elif message['type'] == 'resume':
                    self._pause_event.clear()
                elif message["type"] == "step":
                    self._step_event.set()
                else:
                    raise TaskSdkError("控制消息 type 必须是 stop、pause、resume、step、update_parameters、input_result 或 pet_result")
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


def run_session(entry: str | Path | None = None) -> TaskSession:
    return TaskSession(entry)


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
        _fatal("入口参数必须且只能是 --describe、--run 或 --check-environment")
    return _VALID_MODES[arguments[0]]


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
    "load_skill",
    "runtime_directory",
    "input_result_path",
    "run_session",
)
