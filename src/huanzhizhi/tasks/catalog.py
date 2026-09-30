"""Strict subprocess-based discovery of external task packages."""

from __future__ import annotations

import json
import os
import stat as stat_module
import subprocess
from dataclasses import dataclass
from pathlib import Path

from huanzhizhi.parameters import IntegerParameter, parse_parameters


PROTOCOL_VERSION = 1
_REQUIRED_FIELDS = frozenset({"protocol_version", "id", "name"})
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"description", "presentation", "debug_steps", "parameters"}
_PRESENTATION_MODES = frozenset({"persistent", "launch_only"})
TaskSignature = tuple[tuple[str, int, int], ...]


class TaskCatalogError(RuntimeError):
    """Raised when the task directory or a task description is invalid."""


@dataclass(frozen=True, slots=True)
class TaskDescriptor:
    id: str
    name: str
    description: str
    script_path: Path
    presentation: str = "persistent"
    debug_steps: tuple[str, ...] = ()
    parameters: tuple[IntegerParameter, ...] = ()


class TaskCatalog:
    """Discover task metadata by executing, never importing, each task entry."""

    def __init__(
        self,
        tasks_root: Path,
        python_executable: Path,
        describe_timeout_ms: int,
    ) -> None:
        if not isinstance(tasks_root, Path):
            raise TypeError("tasks_root must be a pathlib.Path")
        if not isinstance(python_executable, Path):
            raise TypeError("python_executable must be a pathlib.Path")
        if (
            isinstance(describe_timeout_ms, bool)
            or not isinstance(describe_timeout_ms, int)
            or describe_timeout_ms <= 0
        ):
            raise ValueError("describe_timeout_ms must be a positive integer")
        executable = python_executable.resolve()
        if not executable.is_file():
            raise TaskCatalogError(f"Python executable does not exist: {executable}")
        self._tasks_root = tasks_root.resolve()
        self._python_executable = executable
        self._describe_timeout_seconds = describe_timeout_ms / 1000.0
        self._tasks: tuple[TaskDescriptor, ...] = ()
        self._by_id: dict[str, TaskDescriptor] = {}
        self._loaded_signature: TaskSignature = ()

    def reload(self) -> tuple[TaskDescriptor, ...]:
        scripts, signature = self._scan_task_scripts()
        descriptors: list[TaskDescriptor] = []
        by_id: dict[str, TaskDescriptor] = {}
        for task_id, script_path in scripts:
            descriptor = self._describe_task(task_id, script_path)
            if descriptor.id in by_id:
                raise TaskCatalogError(f"duplicate task id: {descriptor.id}")
            by_id[descriptor.id] = descriptor
            descriptors.append(descriptor)
        snapshot = tuple(descriptors)
        self._tasks = snapshot
        self._by_id = by_id
        self._loaded_signature = signature
        return snapshot

    def tasks(self) -> tuple[TaskDescriptor, ...]:
        return self._tasks

    def get(self, task_id: str) -> TaskDescriptor:
        if not isinstance(task_id, str) or not task_id:
            raise TypeError("task_id must be non-empty text")
        try:
            return self._by_id[task_id]
        except KeyError as error:
            raise KeyError(f"unknown task id: {task_id}") from error

    def signature(self) -> TaskSignature:
        _scripts, signature = self._scan_task_scripts()
        return signature

    @property
    def loaded_signature(self) -> TaskSignature:
        return self._loaded_signature

    def _scan_task_scripts(
        self,
    ) -> tuple[tuple[tuple[str, Path], ...], TaskSignature]:
        root = self._tasks_root
        try:
            root_stat = root.stat()
        except FileNotFoundError:
            return (), ()
        except OSError as error:
            raise TaskCatalogError(f"cannot inspect tasks root {root}: {error}") from error
        if not stat_module.S_ISDIR(root_stat.st_mode):
            raise TaskCatalogError(f"tasks root is not a directory: {root}")
        try:
            with os.scandir(root) as entries:
                directory_entries = sorted(entries, key=lambda entry: entry.name)
        except OSError as error:
            raise TaskCatalogError(f"cannot read tasks root {root}: {error}") from error

        scripts: list[tuple[str, Path]] = []
        signature: list[tuple[str, int, int]] = []
        seen_directory_ids: set[str] = set()
        for entry in directory_entries:
            if entry.is_dir(follow_symlinks=True):
                task_id = entry.name
                script_path = (Path(entry.path) / "task.py").resolve()
            elif entry.is_file() and entry.name.startswith("task-") and entry.name.endswith(".exe"):
                task_id = entry.name[5:-4]
                script_path = Path(entry.path).resolve()
            else:
                continue
            if not task_id:
                raise TaskCatalogError(f"task directory has an empty id under {root}")
            if task_id in seen_directory_ids:
                raise TaskCatalogError(f"duplicate task directory id: {task_id}")
            seen_directory_ids.add(task_id)
            try:
                stat = script_path.stat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise TaskCatalogError(f"cannot inspect task script {script_path}: {error}") from error
            if not stat_module.S_ISREG(stat.st_mode):
                raise TaskCatalogError(f"task entry is not a regular file: {script_path}")
            scripts.append((task_id, script_path))
            signature.append((task_id, stat.st_mtime_ns, stat.st_size))
        return tuple(scripts), tuple(signature)

    def _describe_task(self, directory_id: str, script_path: Path) -> TaskDescriptor:
        try:
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                ([str(script_path), "--describe"] if script_path.suffix.lower() == ".exe"
                 else [str(self._python_executable), str(script_path), "--describe"]),
                cwd=str(script_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=self._describe_timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise TaskCatalogError(
                f"task {directory_id!r} description timed out after "
                f"{self._describe_timeout_seconds * 1000:g} ms"
            ) from error
        except OSError as error:
            raise TaskCatalogError(
                f"cannot start task {directory_id!r} description: {error}"
            ) from error
        if result.returncode != 0:
            stderr = _decode_diagnostic(result.stderr)
            detail = f": {stderr}" if stderr else ""
            raise TaskCatalogError(
                f"task {directory_id!r} description exited with code "
                f"{result.returncode}{detail}"
            )
        try:
            stdout = result.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise TaskCatalogError(
                f"task {directory_id!r} description stdout is not valid UTF-8"
            ) from error
        stripped = stdout.strip()
        if not stripped:
            raise TaskCatalogError(f"task {directory_id!r} description stdout is empty")
        try:
            payload = json.loads(
                stripped,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_strict_json_object,
            )
        except (json.JSONDecodeError, TaskCatalogError) as error:
            raise TaskCatalogError(
                f"task {directory_id!r} description stdout must contain only one JSON object: {error}"
            ) from error
        return _parse_descriptor(directory_id, script_path, payload)


def _parse_descriptor(
    directory_id: str, script_path: Path, payload: object
) -> TaskDescriptor:
    if not isinstance(payload, dict):
        raise TaskCatalogError(f"task {directory_id!r} description must be a JSON object")
    fields = set(payload)
    missing = _REQUIRED_FIELDS - fields
    extra = fields - _ALLOWED_FIELDS
    if missing:
        raise TaskCatalogError(
            f"task {directory_id!r} description is missing fields: {sorted(missing)}"
        )
    if extra:
        raise TaskCatalogError(
            f"task {directory_id!r} description has unknown fields: {sorted(extra)}"
        )
    version = payload["protocol_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != PROTOCOL_VERSION:
        raise TaskCatalogError(
            f"task {directory_id!r} protocol_version must be {PROTOCOL_VERSION}"
        )
    task_id = payload["id"]
    if not isinstance(task_id, str) or not task_id.strip():
        raise TaskCatalogError(f"task {directory_id!r} id must be non-empty text")
    if task_id != directory_id:
        raise TaskCatalogError(
            f"task id {task_id!r} does not match directory name {directory_id!r}"
        )
    name = payload["name"]
    if not isinstance(name, str) or not name.strip():
        raise TaskCatalogError(f"task {directory_id!r} name must be non-empty text")
    description = payload.get("description", "")
    if not isinstance(description, str):
        raise TaskCatalogError(f"task {directory_id!r} description must be text")
    presentation = payload.get("presentation", "persistent")
    if presentation not in _PRESENTATION_MODES:
        raise TaskCatalogError(
            f"task {directory_id!r} presentation must be one of: "
            f"{sorted(_PRESENTATION_MODES)}"
        )
    debug_raw = payload.get("debug_steps", [])
    if not isinstance(debug_raw, list) or any(
        not isinstance(step, str) or not step.strip() for step in debug_raw
    ):
        raise TaskCatalogError(f"task {directory_id!r} debug_steps must be a list of non-empty text")
    try:
        parameters = parse_parameters(payload.get("parameters", []))
    except ValueError as error:
        raise TaskCatalogError(f"task {directory_id!r}: {error}") from error
    return TaskDescriptor(task_id, name, description, script_path, presentation, tuple(debug_raw), parameters)


def _decode_diagnostic(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _reject_json_constant(value: str) -> None:
    raise TaskCatalogError(f"JSON does not allow constant {value}")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TaskCatalogError(f"JSON object has duplicate field: {key}")
        result[key] = value
    return result


__all__ = ["TaskCatalog", "TaskCatalogError", "TaskDescriptor", "TaskSignature"]
