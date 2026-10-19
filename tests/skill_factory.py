"""Write explicit metadata for task fixtures without executing their code."""
import json
from pathlib import Path

from huanzhizhi.tasks.catalog import skill_manifest_path


def write_skill_manifest(entry: Path, *, task_id=None, name=None, description='', **fields) -> Path:
    task_id = task_id or (entry.stem.removeprefix('task-') if entry.suffix == '.exe' else entry.parent.name)
    payload = dict(protocol_version=1, id=task_id, name=name or task_id,
                   description=description, presentation='persistent')
    payload.update(fields)
    manifest = skill_manifest_path(entry)
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest
