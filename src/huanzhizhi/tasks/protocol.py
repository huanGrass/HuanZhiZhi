"""Strict versioned JSONL protocol shared by task subprocesses and the host."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TypeAlias


PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a task message violates the process protocol."""


@dataclass(frozen=True, slots=True)
class StatusEvent:
    status_title: str
    title: str
    detail: str
    type: str = field(default="status", init=False)


@dataclass(frozen=True, slots=True)
class LogEvent:
    message: str
    type: str = field(default="log", init=False)


@dataclass(frozen=True, slots=True)
class ParametersAppliedEvent:
    request_id: int
    values: dict[str, int | str]
    type: str = field(default="parameters_applied", init=False)


@dataclass(frozen=True, slots=True)
class PauseStateEvent:
    paused: bool
    type: str = field(default="pause_state", init=False)


@dataclass(frozen=True, slots=True)
class SpeakEvent:
    text: str
    audio_path: str | None
    action: str
    type: str = field(default="speak", init=False)


@dataclass(frozen=True, slots=True)
class PointEvent:
    x: int
    y: int
    type: str = field(default="point", init=False)


@dataclass(frozen=True, slots=True)
class InputRequestEvent:
    request_id: str
    kind: str
    window: dict[str, int | str]
    x: int
    y: int
    end_x: int | None
    end_y: int | None
    duration: float
    type: str = field(default="input_request", init=False)


@dataclass(frozen=True, slots=True)
class InputCancelEvent:
    request_id: str
    type: str = field(default="input_cancel", init=False)


@dataclass(frozen=True, slots=True)
class HintEvent:
    kind: str
    points: tuple[tuple[int, int], ...]
    label: str
    type: str = field(default="hint", init=False)


@dataclass(frozen=True, slots=True)
class PreviewEvent:
    image_path: str
    width: int
    height: int
    analysis: dict[str, object]
    grid_overlay: dict[str, list[int]]
    type: str = field(default="preview", init=False)


@dataclass(frozen=True, slots=True)
class FinishedEvent:
    status_title: str
    title: str
    detail: str
    error: str | None
    stopped_by_request: bool
    type: str = field(default="finished", init=False)


@dataclass(frozen=True, slots=True)
class PetAcquireEvent:
    lease: str
    type: str = field(default='pet_acquire', init=False)


@dataclass(frozen=True, slots=True)
class PetUpdateEvent:
    lease: str
    x: float
    y: float
    facing: str
    pose: str
    type: str = field(default='pet_update', init=False)


@dataclass(frozen=True, slots=True)
class PetReleaseEvent:
    lease: str
    type: str = field(default='pet_release', init=False)


TaskEvent: TypeAlias = StatusEvent | LogEvent | ParametersAppliedEvent | PauseStateEvent | SpeakEvent | PointEvent | InputRequestEvent | InputCancelEvent | HintEvent | PreviewEvent | FinishedEvent | PetAcquireEvent | PetUpdateEvent | PetReleaseEvent


def validate_pet_state(x, y, facing, pose) -> None:
    if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1_000_000 for v in (x, y)):
        raise ProtocolError('桌宠坐标必须是有效的 Qt 全局逻辑坐标')
    if not isinstance(facing, str) or facing not in {'left', 'right'}:
        raise ProtocolError('桌宠朝向必须是 left 或 right')
    if not isinstance(pose, str) or pose not in {'idle', 'move', 'reach'}:
        raise ProtocolError('桌宠姿态必须是 idle、move 或 reach')


def encode_pet_result(lease: str, geometry: dict | None, error: str | None = None) -> bytes:
    return _encode_message(dict(protocol_version=PROTOCOL_VERSION, type='pet_result',
                                lease=_input_request_id(lease), geometry=geometry, error=error))


def encode_start_message(task_id: str, options: dict[str, object]) -> bytes:
    return _encode_message(
        {
            "protocol_version": PROTOCOL_VERSION,
            "type": "start",
            "task_id": task_id,
            "options": options,
        }
    )


def encode_stop_message() -> bytes:
    return _encode_message({"protocol_version": PROTOCOL_VERSION, "type": "stop"})


def encode_step_message() -> bytes:
    return _encode_message({"protocol_version": PROTOCOL_VERSION, "type": "step"})


def encode_pause_message(paused: bool) -> bytes:
    if type(paused) is not bool:
        raise TypeError('暂停状态必须是布尔值')
    return _encode_message({'protocol_version': PROTOCOL_VERSION, 'type': 'pause' if paused else 'resume'})


def encode_parameter_update(request_id: int, values: dict) -> bytes:
    if type(request_id) is not int or request_id <= 0:
        raise ValueError("参数更新 request_id 必须是正整数")
    return _encode_message({"protocol_version": PROTOCOL_VERSION, "type": "update_parameters",
                            "request_id": request_id, "values": _parameter_values(values)})


def encode_input_result(request_id: str, error: str | None = None) -> bytes:
    request_id = _input_request_id(request_id)
    if error is not None and not isinstance(error, str):
        raise ProtocolError('input_result.error 必须是字符串或 null')
    return _encode_message(dict(protocol_version=PROTOCOL_VERSION, type='input_result',
                                request_id=request_id, error=error))


def _input_request_id(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 32
            or any(character not in '0123456789abcdef' for character in value)):
        raise ProtocolError('input request_id 必须是 32 位小写十六进制字符串')
    return value


def _parse_input_request(payload: dict) -> InputRequestEvent:
    _require_exact_fields(payload, {'protocol_version', 'type', 'request_id', 'kind',
                                    'window', 'x', 'y', 'end_x', 'end_y', 'duration'})
    request_id = _input_request_id(payload['request_id'])
    kind, window = payload['kind'], payload['window']
    if not isinstance(kind, str) or kind not in {'click', 'right_click', 'double_click', 'drag'}:
        raise ProtocolError('input_request.kind 无效')
    if not isinstance(window, dict) or set(window) != {'hwnd', 'title', 'left', 'top', 'width', 'height'}:
        raise ProtocolError('input_request.window 必须包含完整的窗口身份和客户区位置')
    for field_name in ('hwnd', 'left', 'top', 'width', 'height'):
        if type(window[field_name]) is not int:
            raise ProtocolError(f'input_request.window.{field_name} 必须是整数')
    if any(window[name] <= 0 for name in ('hwnd', 'width', 'height')):
        raise ProtocolError('input_request 窗口句柄和大小必须是正整数')
    if not isinstance(window['title'], str) or not window['title'].strip():
        raise ProtocolError('input_request.window.title 必须是非空字符串')
    x, y = _required_integer(payload, 'x'), _required_integer(payload, 'y')
    if not (0 <= x < window['width'] and 0 <= y < window['height']
            and x <= 0x7fff and y <= 0x7fff):
        raise ProtocolError('input_request 点击坐标超出客户区或 Win32 范围')
    end_x, end_y, duration = payload['end_x'], payload['end_y'], payload['duration']
    if type(duration) not in (int, float) or (type(duration) is float and not math.isfinite(duration)):
        raise ProtocolError('input_request.duration 必须是有限秒数')
    if kind == 'drag':
        end_x, end_y = _required_integer(payload, 'end_x'), _required_integer(payload, 'end_y')
        if not (0 <= end_x < window['width'] and 0 <= end_y < window['height']
                and end_x <= 0x7fff and end_y <= 0x7fff and .1 <= duration <= 5):
            raise ProtocolError('input_request 拖动终点或时长无效')
    elif end_x is not None or end_y is not None or duration != 0:
        raise ProtocolError('非拖动输入的 end_x/end_y 必须为 null，duration 必须为零')
    return InputRequestEvent(request_id, kind, dict(window), x, y, end_x, end_y, float(duration))


def _parameter_values(values: object) -> dict[str, int | str]:
    if (not isinstance(values, dict) or not values
            or any(not isinstance(key, str) or not key or type(value) not in (int, str)
                   for key, value in values.items())):
        raise ProtocolError("参数更新 values 必须是非空的整数或字符串参数对象")
    return dict(values)


def validate_options(options: dict[object, object]) -> dict[str, object]:
    if not isinstance(options, dict):
        raise TypeError("任务 options 必须是 dict")
    _validate_json_value(options, "options")
    return dict(options)


def parse_event_line(line: bytes | str) -> TaskEvent:
    if isinstance(line, bytes):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProtocolError(f"任务事件不是有效 UTF-8：{error}") from error
    elif isinstance(line, str):
        text = line
    else:
        raise TypeError("协议行必须是 bytes 或 str")
    if not text.strip():
        raise ProtocolError("任务事件行不能为空")
    try:
        payload = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except (json.JSONDecodeError, ProtocolError) as error:
        raise ProtocolError(f"任务事件不是严格 JSON：{error}") from error
    if not isinstance(payload, dict):
        raise ProtocolError("任务事件必须是单个 JSON 对象")
    _require_protocol_header(payload)
    event_type = payload["type"]
    if event_type in {'pet_acquire', 'pet_release', 'pet_update'}:
        fields = {'protocol_version', 'type', 'lease'}
        if event_type == 'pet_update':
            fields |= {'x', 'y', 'facing', 'pose'}
        _require_exact_fields(payload, fields)
        lease = _input_request_id(payload['lease'])
        if event_type == 'pet_acquire':
            return PetAcquireEvent(lease)
        if event_type == 'pet_release':
            return PetReleaseEvent(lease)
        validate_pet_state(payload['x'], payload['y'], payload['facing'], payload['pose'])
        return PetUpdateEvent(lease, payload['x'], payload['y'], payload['facing'], payload['pose'])
    if event_type == 'pause_state':
        _require_exact_fields(payload, {'protocol_version', 'type', 'paused'})
        if type(payload['paused']) is not bool:
            raise ProtocolError('pause_state.paused 必须是布尔值')
        return PauseStateEvent(payload['paused'])
    if event_type == "status":
        _require_exact_fields(payload, {"protocol_version", "type", "status_title", "title", "detail"})
        return StatusEvent(
            _required_string(payload, "status_title"),
            _required_string(payload, "title"),
            _required_string(payload, "detail"),
        )
    if event_type == "log":
        _require_exact_fields(payload, {"protocol_version", "type", "message"})
        return LogEvent(_required_string(payload, "message"))
    if event_type == "parameters_applied":
        _require_exact_fields(payload, {"protocol_version", "type", "request_id", "values"})
        return ParametersAppliedEvent(_required_positive_integer(payload, "request_id"),
                                      _parameter_values(payload["values"]))
    if event_type == "speak":
        _require_exact_fields(payload, {"protocol_version", "type", "text", "audio_path", "action"})
        return SpeakEvent(
            _required_string(payload, "text"),
            _optional_string(payload, "audio_path"),
            _required_string(payload, "action"),
        )
    if event_type == "point":
        _require_exact_fields(payload, {"protocol_version", "type", "x", "y"})
        return PointEvent(_required_integer(payload, "x"), _required_integer(payload, "y"))
    if event_type == 'input_request':
        return _parse_input_request(payload)
    if event_type == 'input_cancel':
        _require_exact_fields(payload, {'protocol_version', 'type', 'request_id'})
        return InputCancelEvent(_input_request_id(payload['request_id']))
    if event_type == "hint":
        _require_exact_fields(payload, {"protocol_version", "type", "kind", "points", "label"})
        kind, points = payload["kind"], payload["points"]
        expected = {"clear": {0}, "click": {1, 2}, "drag": {3}, "line": {2}}
        if not isinstance(kind, str) or kind not in expected or not isinstance(points, list) or len(points) not in expected[kind]:
            raise ProtocolError("hint 的类型或坐标数量无效")
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in point):
                raise ProtocolError("hint 坐标必须是两个整数")
        if kind == "drag" and (points[0] == points[1] or (points[0][0] != points[1][0] and points[0][1] != points[1][1])):
            raise ProtocolError("hint 拖动路径必须是非零直线")
        if kind == "line" and points[0] == points[1]:
            raise ProtocolError("hint 直线的起点和终点不能相同")
        return HintEvent(kind, tuple(tuple(point) for point in points), _required_string(payload, "label"))
    if event_type == "preview":
        _require_exact_fields(
            payload,
            {
                "protocol_version",
                "type",
                "image_path",
                "width",
                "height",
                "analysis",
                "grid_overlay",
            },
        )
        image_path = _required_string(payload, "image_path")
        width = _required_positive_integer(payload, "width")
        height = _required_positive_integer(payload, "height")
        analysis = payload["analysis"]
        if not isinstance(analysis, dict):
            raise ProtocolError("preview.analysis 必须是 JSON 对象")
        grid_overlay = _validated_grid_overlay(payload["grid_overlay"], width, height)
        return PreviewEvent(image_path, width, height, analysis, grid_overlay)
    if event_type == "finished":
        _require_exact_fields(
            payload,
            {
                "protocol_version",
                "type",
                "status_title",
                "title",
                "detail",
                "error",
                "stopped_by_request",
            },
        )
        stopped = payload["stopped_by_request"]
        if not isinstance(stopped, bool):
            raise ProtocolError("finished.stopped_by_request 必须是 bool")
        return FinishedEvent(
            _required_string(payload, "status_title"),
            _required_string(payload, "title"),
            _required_string(payload, "detail"),
            _optional_string(payload, "error"),
            stopped,
        )
    raise ProtocolError(f"未知任务事件类型：{event_type}")


def _encode_message(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise ProtocolError(f"JSON 不允许常量 {value}")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"JSON 对象存在重复字段：{key}")
        result[key] = value
    return result


def _validate_json_value(value: object, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError(f"{path} 包含非有限数值")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} 的对象键必须是字符串")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} 包含非 JSON 类型：{type(value).__name__}")


def _require_protocol_header(payload: dict[str, object]) -> None:
    version = payload.get("protocol_version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ProtocolError(f"不支持的 protocol_version：{version}")
    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ProtocolError("任务事件 type 必须是非空字符串")


def _require_exact_fields(payload: dict[str, object], expected: set[str]) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ProtocolError(f"任务事件字段不匹配，缺失={missing}，多余={extra}")


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{payload['type']}.{field} 必须是非空字符串")
    return value


def _optional_string(payload: dict[str, object], field: str) -> str | None:
    value = payload[field]
    if value is not None and not isinstance(value, str):
        raise ProtocolError(f"{payload['type']}.{field} 必须是字符串或 null")
    return value


def _required_integer(payload: dict[str, object], field: str) -> int:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{payload['type']}.{field} 必须是整数")
    return value


def _required_positive_integer(payload: dict[str, object], field: str) -> int:
    value = _required_integer(payload, field)
    if value <= 0:
        raise ProtocolError(f"{payload['type']}.{field} 必须是正整数")
    return value


def _validated_grid_overlay(value: object, width: int, height: int) -> dict[str, list[int]]:
    if not isinstance(value, dict):
        raise ProtocolError("preview.grid_overlay 必须是 JSON 对象")
    expected = {"rows", "columns"}
    actual = set(value)
    if actual != expected:
        raise ProtocolError(
            "preview.grid_overlay 字段不匹配，"
            f"缺失={sorted(expected - actual)}，多余={sorted(actual - expected)}"
        )
    rows = _validated_coordinates(value["rows"], height, "preview.grid_overlay.rows")
    columns = _validated_coordinates(value["columns"], width, "preview.grid_overlay.columns")
    return {"rows": rows, "columns": columns}


def _validated_coordinates(value: object, limit: int, field: str) -> list[int]:
    if not isinstance(value, list):
        raise ProtocolError(f"{field} 必须是整数数组")
    previous = -1
    coordinates: list[int] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            raise ProtocolError(f"{field} 必须只包含整数")
        if coordinate < 0 or coordinate >= limit:
            raise ProtocolError(f"{field} 坐标必须在 [0, {limit}) 内")
        if coordinate <= previous:
            raise ProtocolError(f"{field} 坐标必须严格递增")
        coordinates.append(coordinate)
        previous = coordinate
    return coordinates


__all__ = (
    "PROTOCOL_VERSION",
    "FinishedEvent",
    "LogEvent",
    "ParametersAppliedEvent",
    "PauseStateEvent",
    "PointEvent",
    "PetAcquireEvent",
    "PetUpdateEvent",
    "PetReleaseEvent",
    "InputRequestEvent",
    "InputCancelEvent",
    "HintEvent",
    "PreviewEvent",
    "ProtocolError",
    "SpeakEvent",
    "StatusEvent",
    "TaskEvent",
    "encode_start_message",
    "encode_step_message",
    "encode_stop_message",
    "encode_pause_message",
    "encode_parameter_update",
    "encode_input_result",
    "encode_pet_result",
    "validate_pet_state",
    "parse_event_line",
    "validate_options",
)
