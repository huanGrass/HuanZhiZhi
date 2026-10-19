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


def export_skill(task, destination: Path, *, description: str) -> None:
    """Export one saved skill, without executing it or changing its files."""
    import os
    import tempfile
    import zipfile

    executable = task.script_path.suffix.lower() == '.exe'
    source = task.script_path if executable else task.script_path.parent
    base = source.parent if executable else source
    destination = destination.resolve()
    if (not executable and destination.is_relative_to(source.resolve())):
        raise ValueError('请将导出文件保存在技能目录之外')
    files = skill_files(source)
    manifest = skill_manifest_path(task.script_path)
    if destination in {(base / name).resolve() for name in files}:
        raise ValueError('导出文件不能覆盖技能文件')
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    payload['id'] = task.id
    payload.pop('name', None)
    payload['description'] = description
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.zip', delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                relative = Path(name)
                if any(part.casefold() in {'.git', '.codex', '.agents'} for part in relative.parts):
                    continue
                path = base / relative
                member = name if executable else f'{task.id}/{name}'
                if path == manifest:
                    archive.writestr(member, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
                else:
                    archive.write(path, member)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_local_skill(archive: Path, workdir: Path) -> Path:
    """Validate and stage a local ZIP as data; never run imported code."""
    import stat
    import zipfile
    from huanzhizhi.component_install import _relative, _unpack
    from huanzhizhi.tasks.catalog import TaskCatalog

    workdir = workdir.resolve()
    raw = workdir / 'raw'
    raw.mkdir()
    seen = set()
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if not entries or len(entries) > 2000 or sum(e.file_size for e in entries) > 80 * 1024 * 1024:
            raise ValueError('技能包为空，或超过 2000 个条目 / 80 MB 解压大小限制')
        for entry in entries:
            path = _relative(entry.filename)
            if not path.parts:
                raise ValueError('技能包包含空路径')
            for part in path.parts:
                validate_skill_name(part)
                if part.casefold() in {'.git', '.codex', '.agents'}:
                    raise ValueError('技能包不能包含仓库或代理配置')
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                raise ValueError('技能包不能包含链接、特殊文件或加密文件')
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError('技能包包含重复路径')
            seen.add(key)
    _unpack({'destination': 'raw', 'archive_prefix': ''}, archive, workdir)
    if (raw / 'skill.json').is_file():
        payload = json.loads((raw / 'skill.json').read_text(encoding='utf-8'))
        name = payload.get('id') if isinstance(payload, dict) else None
        if not isinstance(name, str):
            raise ValueError('技能清单缺少有效 ID')
        validate_skill_name(name)
        container = workdir / 'normalized'
        container.mkdir()
        raw.rename(container / name)
    else:
        container = raw
    tasks = TaskCatalog(container).reload()
    if len(tasks) != 1:
        raise ValueError('每个 ZIP 必须包含一个完整技能及 skill.json 清单')
    task = tasks[0]
    executable = task.script_path.suffix.lower() == '.exe'
    source = task.script_path if executable else task.script_path.parent
    allowed = {source, skill_manifest_path(source)} if executable else {source}
    if set(container.iterdir()) != allowed:
        raise ValueError('技能包包含技能目录以外的文件或多个技能')
    payload = json.loads(skill_manifest_path(task.script_path).read_text(encoding='utf-8'))
    if payload['id'] != task.id:
        raise ValueError('技能清单 ID 与目录名称不一致')
    if not executable:
        for path in source.rglob('*.py'):
            compile(path.read_bytes(), str(path), 'exec')
    return source
