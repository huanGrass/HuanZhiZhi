"""Strict versioned JSONL protocol shared by task subprocesses and the host."""

from __future__ import annotations

import json
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


TaskEvent: TypeAlias = StatusEvent | LogEvent | SpeakEvent | PointEvent | HintEvent | PreviewEvent | FinishedEvent


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
    "PointEvent",
    "HintEvent",
    "PreviewEvent",
    "ProtocolError",
    "SpeakEvent",
    "StatusEvent",
    "TaskEvent",
    "encode_start_message",
    "encode_step_message",
    "encode_stop_message",
    "parse_event_line",
    "validate_options",
)
