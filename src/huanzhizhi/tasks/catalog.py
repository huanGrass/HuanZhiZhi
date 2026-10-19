"""Strict, data-only discovery of external task packages from skill.json."""

from __future__ import annotations

import json
import os
import stat as stat_module
from dataclasses import dataclass, replace
from pathlib import Path

from huanzhizhi.parameters import ChoiceParameter, IntegerParameter, parse_parameters


PROTOCOL_VERSION = 1
_REQUIRED_FIELDS = frozenset({"protocol_version", "id"})
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"name", "description", "presentation", "debug_steps", "parameters", "environment_checks"}
_PRESENTATION_MODES = frozenset({"persistent", "launch_only"})
TaskSignature = tuple[tuple[str, int, int, int, int], ...]


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
    parameters: tuple[IntegerParameter | ChoiceParameter, ...] = ()
    environment_checks: tuple[str, ...] = ()
    group: str = "formal"

    @property
    def key(self) -> str:
        """UI identity; the subprocess protocol keeps the original skill id."""
        return f"test/{self.id}" if self.group == "test" else self.id


class TaskCatalog:
    """Discover task metadata without executing or importing task code."""

    def __init__(
        self,
        tasks_root: Path,
        *,
        test_tasks_root: Path | None = None,
    ) -> None:
        if not isinstance(tasks_root, Path):
            raise TypeError("tasks_root must be a pathlib.Path")
        if test_tasks_root is not None and not isinstance(test_tasks_root, Path):
            raise TypeError("test_tasks_root must be a pathlib.Path")
        self._tasks_root = tasks_root.resolve()
        self._test_tasks_root = test_tasks_root.resolve() if test_tasks_root is not None else None
        if self._test_tasks_root == self._tasks_root:
            raise ValueError("正式与测试技能目录不能相同")
        self._tasks: tuple[TaskDescriptor, ...] = ()
        self._by_id: dict[str, TaskDescriptor] = {}
        self._loaded_signature: TaskSignature = ()

    def reload(self) -> tuple[TaskDescriptor, ...]:
        scripts, signature = self._scan_task_scripts()
        descriptors: list[TaskDescriptor] = []
        by_id: dict[str, TaskDescriptor] = {}
        for task_key, script_path in scripts:
            group = "test" if task_key.startswith("test/") else "formal"
            task_id = task_key.removeprefix("test/") if group == "test" else task_key
            descriptor = _parse_descriptor(task_id, script_path, read_skill_manifest(script_path))
            descriptor = replace(descriptor, group=group)
            if descriptor.key in by_id:
                raise TaskCatalogError(f"duplicate task id: {descriptor.key}")
            by_id[descriptor.key] = descriptor
            descriptors.append(descriptor)
        snapshot = tuple(descriptors)
        self._tasks = snapshot
        self._by_id = by_id
        self._loaded_signature = signature
        return snapshot

    def tasks(self) -> tuple[TaskDescriptor, ...]:
        return self._tasks

    def record_move(self, task_key: str, script_path: Path, group: str) -> None:
        """Update a successfully moved skill without executing its entry again."""
        if group not in {'formal', 'test'}:
            raise ValueError('未知技能分组')
        task = replace(self.get(task_key), script_path=script_path, group=group)
        signatures = {key: stamp for key, *stamp in self._loaded_signature}
        signatures[task.key] = signatures[task_key]
        by_id = {key: value for key, value in self._by_id.items() if key != task_key}
        by_id[task.key] = task
        snapshot = tuple(sorted(by_id.values(), key=lambda item: (
            item.group == 'test',
            item.script_path.name if item.script_path.suffix.lower() == '.exe' else item.script_path.parent.name,
        )))
        self._tasks = snapshot
        self._by_id = by_id
        # Keep other entries' signatures so external changes remain detectable.
        self._loaded_signature = tuple((item.key, *signatures[item.key]) for item in snapshot)

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
        scripts, signature = self._scan_root(self._tasks_root)
        if self._test_tasks_root is not None:
            test_scripts, test_signature = self._scan_root(self._test_tasks_root)
            scripts += tuple((f"test/{task_id}", path) for task_id, path in test_scripts)
            signature += tuple((f"test/{task_id}", *stamp)
                               for task_id, *stamp in test_signature)
        return scripts, signature

    def _scan_root(self, root: Path) -> tuple[tuple[tuple[str, Path], ...], TaskSignature]:
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
        signature: list[tuple[str, int, int, int, int]] = []
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
                if skill_manifest_path(script_path).exists():
                    raise TaskCatalogError(f"技能清单缺少对应入口：{script_path}")
                continue
            except OSError as error:
                raise TaskCatalogError(f"cannot inspect task script {script_path}: {error}") from error
            if not stat_module.S_ISREG(stat.st_mode):
                raise TaskCatalogError(f"task entry is not a regular file: {script_path}")
            manifest = skill_manifest_path(script_path)
            try:
                metadata_stat = manifest.stat()
            except OSError as error:
                raise TaskCatalogError(f"无法读取技能清单 {manifest}：{error}") from error
            if not stat_module.S_ISREG(metadata_stat.st_mode):
                raise TaskCatalogError(f"技能清单不是普通文件：{manifest}")
            scripts.append((task_id, script_path))
            signature.append((task_id, metadata_stat.st_mtime_ns, metadata_stat.st_size,
                              stat.st_mtime_ns, stat.st_size))
        return tuple(scripts), tuple(signature)


def skill_manifest_path(script_path: Path) -> Path:
    """Keep legacy standalone executables' manifests beside their entry files."""
    return (script_path.with_suffix('.skill.json') if script_path.suffix.lower() == '.exe'
            else script_path.with_name('skill.json'))


def read_skill_manifest(script_path: Path) -> dict:
    """Read the shared metadata contract used by discovery and the task SDK."""
    script_path = Path(script_path).resolve()
    manifest = skill_manifest_path(script_path)
    try:
        payload = json.loads(manifest.read_text(encoding='utf-8'),
                             parse_constant=_reject_json_constant,
                             object_pairs_hook=_strict_json_object)
    except (OSError, UnicodeError, ValueError, TaskCatalogError) as error:
        raise TaskCatalogError(f"无法读取技能清单 {manifest}：{error}") from error
    task_id = (script_path.stem.removeprefix('task-') if script_path.suffix.lower() == '.exe'
               else script_path.parent.name)
    _parse_descriptor(task_id, script_path, payload)
    # Older skills still read skill['name']; both runtime keys use the directory.
    return {**payload, 'id': task_id, 'name': task_id}


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
    if not isinstance(task_id, str) or not task_id.strip() or task_id != task_id.strip():
        raise TaskCatalogError(f"task {directory_id!r} id must be non-empty text")
    name = payload.get("name", task_id)
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise TaskCatalogError(f"task {directory_id!r} name must be non-empty text")
    description = payload.get("description", "")
    if not isinstance(description, str):
        raise TaskCatalogError(f"task {directory_id!r} description must be text")
    presentation = payload.get("presentation", "persistent")
    if not isinstance(presentation, str) or presentation not in _PRESENTATION_MODES:
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
    checks = payload.get("environment_checks", [])
    if (not isinstance(checks, list)
            or any(not isinstance(name, str) or not name.strip() for name in checks)
            or len(set(checks)) != len(checks)):
        raise TaskCatalogError(f"task {directory_id!r} environment_checks must be unique non-empty names")
    return TaskDescriptor(directory_id, directory_id, description, script_path, presentation, tuple(debug_raw), parameters, tuple(checks))


def _reject_json_constant(value: str) -> None:
    raise TaskCatalogError(f"JSON does not allow constant {value}")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TaskCatalogError(f"JSON object has duplicate field: {key}")
        result[key] = value
    return result


__all__ = ["TaskCatalog", "TaskCatalogError", "TaskDescriptor", "TaskSignature",
           "read_skill_manifest", "skill_manifest_path"]
