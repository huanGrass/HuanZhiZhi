"""Read the official skill catalog and prepare verified, data-only installations."""
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
import zipfile

from huanzhizhi.component_install import _download, _relative, _unpack
from huanzhizhi.tasks.catalog import TaskCatalog
from huanzhizhi.tasks.storage import validate_skill_name, publish_skill

CATALOG_URL = 'https://raw.githubusercontent.com/huanGrass/HuanZhiZhi-Skills/main/catalog.json'


def parse_catalog(payload):
    if not isinstance(payload, dict) or payload.get('format') != 1:
        raise ValueError('云端技能清单格式不支持')
    items = payload.get('skills')
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('云端技能清单无效')
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('云端技能条目无效')
        for key in ('id', 'category', 'description', 'version', 'url', 'sha256'):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError(f'云端技能字段无效：{key}')
        validate_skill_name(item['id'])
        if item['id'].casefold() in seen:
            raise ValueError('云端技能名称重复')
        seen.add(item['id'].casefold())
        if not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
            raise ValueError('云端技能校验值无效')
        if type(item.get('size')) is not int or not 0 < item['size'] <= 20 * 1024 * 1024:
            raise ValueError('技能下载大小无效或超过 20 MB')
        url = urlsplit(item['url'])
        if (url.scheme != 'https' or url.netloc != 'raw.githubusercontent.com'
                or url.query or url.fragment
                or not re.fullmatch(r'/huanGrass/HuanZhiZhi-Skills/[0-9a-f]{40}/packages/[A-Za-z0-9_.-]+[.]zip', url.path)):
            raise ValueError('技能下载必须来自官方仓库的固定版本')
    return items


def fetch_catalog(progress):
    with tempfile.TemporaryDirectory(prefix='zhizhi-cloud-catalog-') as temporary:
        path = Path(temporary) / 'catalog.json'
        _download(CATALOG_URL, path, progress)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('云端技能清单过大')
        return parse_catalog(json.loads(path.read_text(encoding='utf-8')))


def prepare_skill(item, workdir, progress):
    parse_catalog({'format': 1, 'skills': [item]})
    archive = workdir / 'skill.zip'
    _download(item['url'], archive, progress, size=item['size'], sha256=item['sha256'])
    stage = workdir / 'stage'
    stage.mkdir()
    # Check all entries before extraction, including Windows aliases and collisions.
    seen = set()
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 80 * 1024 * 1024:
            raise ValueError('技能解压大小或文件数量超限')
        for entry in entries:
            path = _relative(entry.filename)
            if path.parts[0] != item['id']:
                raise ValueError('技能压缩包目录与清单不一致')
            for part in path.parts:
                validate_skill_name(part)
                if part.casefold() in {'.git', '.codex', '.agents'}:
                    raise ValueError('技能包不能包含仓库或代理配置')
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError('技能包包含重复路径')
            seen.add(key)
    _unpack({'destination': item['id'], 'archive_prefix': item['id'] + '/'}, archive, stage)
    tasks = TaskCatalog(stage).reload()
    if len(tasks) != 1 or tasks[0].id != item['id']:
        raise ValueError('技能包清单与云端条目不一致')
    if json.loads(tasks[0].script_path.with_name('skill.json').read_text(encoding='utf-8'))['id'] != item['id']:
        raise ValueError('技能清单 ID 与目录不一致')
    for path in (stage / item['id']).rglob('*.py'):
        compile(path.read_bytes(), str(path), 'exec')
    progress(item['size'])
    return stage / item['id']


def install_prepared(source, tasks_root, reload_tasks):
    """Publish on the UI thread; never replace a user's installed skill."""
    tasks_root.mkdir(parents=True, exist_ok=True)
    destination = tasks_root / source.name
    name = source.stem.removeprefix('task-') if source.is_file() else source.name
    if destination.exists() or (tasks_root / name).exists() or (tasks_root / f'task-{name}.exe').exists():
        raise FileExistsError('已有同名技能，未覆盖。请先在技能列表检查。')
    publish_skill(source, destination)
    try:
        reload_tasks()
    except Exception:
        publish_skill(destination, source)
        raise
    return destination
