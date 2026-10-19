"""Skill-file validation and rollback for publication and manual promotion."""
import hashlib
import json
import re
from pathlib import Path

from PySide6 import QtCore

from huanzhizhi.tasks.catalog import skill_manifest_path


def validate_skill_name(name: str) -> None:
    if (not name or name != name.strip() or name.endswith('.')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name in {'.', '..'}
            or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', name)):
        raise ValueError('任务名称不能包含路径、Windows 保留名称或非法文件名字符')


def _write_manifest(path: Path, content: bytes) -> None:
    target = QtCore.QSaveFile(str(path))
    if not target.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(target.errorString())
    if target.write(content) != len(content) or not target.commit():
        target.cancelWriting()
        raise OSError(target.errorString())


def synchronize_skill_names(tasks, settings) -> dict[str, str]:
    """The directory wins; preserve old IDs long enough to migrate preferences."""
    existing = {task.key for task in tasks}
    updates, candidates = [], {}
    for task in tasks:
        path = skill_manifest_path(task.script_path)
        original = path.read_bytes()
        payload = json.loads(original)
        old = ('test/' if task.group == 'test' else '') + payload['id']
        if old != task.key and old not in existing:
            candidates.setdefault(old, []).append(task.key)
        if payload['id'] != task.id or 'name' in payload:
            payload['id'] = task.id
            payload.pop('name', None)
            updates.append((path, original, (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
    # A copied package is not an unambiguous rename.
    renames = {old: targets[0] for old, targets in candidates.items() if len(targets) == 1}
    written = []
    try:
        for path, original, content in updates:
            _write_manifest(path, content)
            written.append((path, original))
        if settings is not None and updates:
            settings.rename_task_settings(renames)
    except Exception:
        for path, original in reversed(written):
            _write_manifest(path, original)
        raise
    return renames


def skill_files(directory: Path) -> dict[str, str]:
    """Fingerprint contents without following links or runtime bytecode."""
    def paths():
        yield directory
        if directory.is_dir():
            yield from directory.rglob('*')
        elif directory.suffix.lower() == '.exe':
            yield skill_manifest_path(directory)
    base = directory if directory.is_dir() else directory.parent
    files = {}
    for path in paths():
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'技能目录包含链接，无法安全复制：{path}')
        relative = path.relative_to(base) if path != directory else Path(path.name)
        if '__pycache__' in relative.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        if path.is_file():
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _move_skill(source: Path, destination: Path) -> None:
    pairs = [(source, destination)]
    if source.is_file() and source.suffix.lower() == '.exe':
        pairs.append((skill_manifest_path(source), skill_manifest_path(destination)))
    for original, target in pairs:
        if not original.exists():
            raise FileNotFoundError(original)
        if target.exists():
            raise FileExistsError(target)
    moved = []
    try:
        for original, target in pairs:
            original.rename(target)
            moved.append((original, target))
    except Exception:
        for original, target in reversed(moved):
            target.rename(original)
        raise


def publish_skill(source: Path, destination: Path, *, previous: Path | None = None,
                  backup: Path | None = None) -> None:
    """Move a prepared skill; restore the previous version if the move fails."""
    if destination.exists() and destination != previous:
        raise FileExistsError(f'目标技能已存在：{destination.name}')
    if previous is not None:
        if backup is None or backup.exists():
            raise ValueError('替换技能需要一个未占用的备份位置')
        backup.parent.mkdir(parents=True, exist_ok=True)
        _move_skill(previous, backup)
    try:
        _move_skill(source, destination)
    except Exception:
        if previous is not None:
            _move_skill(backup, previous)
        raise
