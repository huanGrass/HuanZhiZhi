"""Atomic, strict diagnostic bundles for task failures and stops."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import uuid as uuid_module
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

from PIL import Image


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCHEMA_VERSION = 1


class TaskDiagnosticsError(RuntimeError):
    """Raised when a diagnostic bundle cannot be validated or published."""


@dataclass(frozen=True, slots=True)
class TaskDiagnosticsManifest:
    """Immutable description of one completely published diagnostic bundle."""

    directory: Path
    manifest_path: Path
    task_id: str
    event_type: str
    event_id: str
    created_at: str
    reason: str
    frame_path: Path | None
    analysis_path: Path | None
    context_path: Path
    transcript_path: Path | None


class TaskDiagnosticsWriter:
    """Write self-contained task diagnostics and publish them with one rename."""

    def __init__(
        self,
        root: Path,
        task_id: str,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], object] | None = None,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("诊断 root 必须是 pathlib.Path")
        self._root = root.resolve(strict=False)
        self._task_id = _validate_safe_id(task_id, "task_id")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory or uuid_module.uuid4

    def write_failure(
        self,
        *,
        reason: str,
        frame: Image.Image | None = None,
        analysis: JsonValue | None = None,
        context: dict[str, JsonValue],
        transcript: Iterable[dict[str, JsonValue]] | None = None,
    ) -> TaskDiagnosticsManifest:
        return self._write(
            "failure",
            reason=reason,
            frame=frame,
            analysis=analysis,
            context=context,
            transcript=transcript,
        )

    def write_stop(
        self,
        *,
        reason: str,
        frame: Image.Image | None = None,
        analysis: JsonValue | None = None,
        context: dict[str, JsonValue],
        transcript: Iterable[dict[str, JsonValue]] | None = None,
    ) -> TaskDiagnosticsManifest:
        return self._write(
            "stop",
            reason=reason,
            frame=frame,
            analysis=analysis,
            context=context,
            transcript=transcript,
        )

    def _write(
        self,
        event_type: str,
        *,
        reason: str,
        frame: Image.Image | None,
        analysis: JsonValue | None,
        context: dict[str, JsonValue],
        transcript: Iterable[dict[str, JsonValue]] | None,
    ) -> TaskDiagnosticsManifest:
        reason = _validate_reason(reason)
        if frame is not None and not isinstance(frame, Image.Image):
            raise TaskDiagnosticsError("frame 必须是 PIL.Image.Image 或 None")
        if not isinstance(context, dict):
            raise TaskDiagnosticsError("context 必须是 JSON 对象")

        try:
            context_bytes = _json_bytes(context, "context")
            analysis_bytes = None if analysis is None else _json_bytes(analysis, "analysis")
            transcript_records = _prepare_transcript(transcript)
            transcript_bytes = _jsonl_bytes(transcript_records)
            moment = self._clock()
            if not isinstance(moment, datetime):
                raise TypeError("clock 必须返回 datetime")
            event_id = _validate_safe_id(str(self._uuid_factory()), "event_id")
        except TaskDiagnosticsError:
            raise
        except Exception as error:
            raise TaskDiagnosticsError(f"诊断输入无效：{error}") from error

        created_at = moment.isoformat()
        timestamp = moment.strftime("%Y%m%dT%H%M%S.%f")
        final_name = f"{timestamp}_{event_id}"
        task_directory = self._root / self._task_id
        temporary_directory = task_directory / f".{final_name}.tmp"
        final_directory = task_directory / final_name
        temporary_created = False

        try:
            self._create_task_directory(task_directory)
            if final_directory.exists():
                raise FileExistsError(f"诊断目录已存在：{final_directory}")
            temporary_directory.mkdir(exist_ok=False)
            temporary_created = True

            context_path = temporary_directory / "context.json"
            _write_synced(context_path, context_bytes)

            analysis_name: str | None = None
            if analysis_bytes is not None:
                analysis_name = "analysis.json"
                _write_synced(temporary_directory / analysis_name, analysis_bytes)

            transcript_name: str | None = None
            if transcript_bytes is not None:
                transcript_name = "transcript.jsonl"
                _write_synced(temporary_directory / transcript_name, transcript_bytes)

            frame_name: str | None = None
            if frame is not None:
                frame_name = "frame.png"
                _write_png_synced(temporary_directory / frame_name, frame)

            manifest_data: dict[str, JsonValue] = {
                "schema_version": _SCHEMA_VERSION,
                "task_id": self._task_id,
                "event_type": event_type,
                "event_id": event_id,
                "created_at": created_at,
                "reason": reason,
                "files": {
                    "frame": frame_name,
                    "analysis": analysis_name,
                    "context": "context.json",
                    "transcript": transcript_name,
                },
            }
            _write_synced(
                temporary_directory / "manifest.json",
                _json_bytes(manifest_data, "manifest"),
            )

            os.rename(temporary_directory, final_directory)
            temporary_created = False
        except Exception as error:
            cleanup_error: Exception | None = None
            if temporary_created:
                try:
                    shutil.rmtree(temporary_directory)
                except Exception as caught:
                    cleanup_error = caught
            if cleanup_error is not None:
                raise TaskDiagnosticsError(
                    f"诊断写入失败且临时目录清理失败：{error}；清理错误：{cleanup_error}"
                ) from error
            raise TaskDiagnosticsError(f"诊断写入失败：{error}") from error

        return TaskDiagnosticsManifest(
            directory=final_directory,
            manifest_path=final_directory / "manifest.json",
            task_id=self._task_id,
            event_type=event_type,
            event_id=event_id,
            created_at=created_at,
            reason=reason,
            frame_path=None if frame is None else final_directory / "frame.png",
            analysis_path=None if analysis is None else final_directory / "analysis.json",
            context_path=final_directory / "context.json",
            transcript_path=None if transcript_bytes is None else final_directory / "transcript.jsonl",
        )

    def _create_task_directory(self, task_directory: Path) -> None:
        try:
            task_directory.mkdir(parents=True, exist_ok=True)
            resolved = task_directory.resolve(strict=True)
            resolved.relative_to(self._root)
        except Exception as error:
            raise TaskDiagnosticsError(f"无法创建安全的任务诊断目录：{error}") from error


def _validate_safe_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise TaskDiagnosticsError(f"{field} 只能包含安全字符 A-Z、a-z、0-9、点、下划线和连字符")
    return value


def _validate_reason(reason: object) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise TaskDiagnosticsError("reason 必须是非空字符串")
    return reason


def _prepare_transcript(
    transcript: Iterable[dict[str, JsonValue]] | None,
) -> tuple[dict[str, JsonValue], ...] | None:
    if transcript is None:
        return None
    if isinstance(transcript, (str, bytes, bytearray, dict)):
        raise TaskDiagnosticsError("transcript 必须是 JSON 对象的可迭代序列")
    try:
        records = tuple(transcript)
    except Exception as error:
        raise TaskDiagnosticsError(f"无法读取 transcript：{error}") from error
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TaskDiagnosticsError(f"transcript[{index}] 必须是 JSON 对象")
        _validate_json_value(record, f"transcript[{index}]", set())
    return records


def _jsonl_bytes(records: tuple[dict[str, JsonValue], ...] | None) -> bytes | None:
    if records is None:
        return None
    lines = [json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for record in records]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _json_bytes(value: JsonValue, field: str) -> bytes:
    try:
        _validate_json_value(value, field, set())
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    except TaskDiagnosticsError:
        raise
    except Exception as error:
        raise TaskDiagnosticsError(f"{field} 不是严格 JSON：{error}") from error
    return (text + "\n").encode("utf-8")


def _validate_json_value(value: object, path: str, ancestors: set[int]) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TaskDiagnosticsError(f"{path} 包含 NaN 或 Infinity")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in ancestors:
            raise TaskDiagnosticsError(f"{path} 包含循环引用")
        ancestors.add(identity)
        try:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    _validate_json_value(item, f"{path}[{index}]", ancestors)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TaskDiagnosticsError(f"{path} 的对象键必须是字符串")
                    _validate_json_value(item, f"{path}.{key}", ancestors)
        finally:
            ancestors.remove(identity)
        return
    raise TaskDiagnosticsError(f"{path} 包含不可序列化类型：{type(value).__name__}")


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _write_png_synced(path: Path, frame: Image.Image) -> None:
    with path.open("xb") as file:
        frame.save(file, format="PNG")
        file.flush()
        os.fsync(file.fileno())


__all__ = (
    "JsonValue",
    "TaskDiagnosticsError",
    "TaskDiagnosticsManifest",
    "TaskDiagnosticsWriter",
)
