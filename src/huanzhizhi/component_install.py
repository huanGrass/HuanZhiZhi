"""Install pinned official component files without changing the system environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile

from huanzhizhi.python_runtime import python_executable
from huanzhizhi.shared_components import supported_components


def install_plan(components, failed):
    return [asset for component in components for asset in component.get('downloads', [])
            if any(component['id'] + '.' + check in failed for check in asset['checks'])]


def _relative(value):
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or PureWindowsPath(value).drive or
            '..' in path.parts or any(':' in part or PureWindowsPath(part).is_reserved() for part in path.parts)):
        raise ValueError(f'无效组件文件路径：{value}')
    return Path(*path.parts)


def _download(url, destination, progress, *, size=None, sha256=None):
    if not url.startswith('https://'):
        raise ValueError('组件下载必须使用 HTTPS')
    curl = Path(os.environ['SystemRoot']) / 'System32/curl.exe'
    maximum = size if size is not None else 1024 * 1024
    command = [str(curl), '--location', '--fail', '--silent', '--show-error',
               '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15',
               '--speed-limit', '1024', '--speed-time', '30', '--retry', '2',
               '--max-filesize', str(maximum), '--output', str(destination), url]
    # A file keeps curl's diagnostics from blocking a long download on a full pipe.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while process.poll() is None:
                progress(destination.stat().st_size if destination.exists() else 0)
                time.sleep(0.25)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        if process.returncode:
            errors.seek(0)
            raise RuntimeError('下载失败：' + errors.read().decode('utf-8', errors='replace')[-1500:])
    actual = destination.stat().st_size
    if size is not None and actual != size:
        raise ValueError(f'下载不完整：需要 {size} 字节，实际 {actual} 字节')
    if sha256:
        with destination.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != sha256:
                raise ValueError('下载文件校验失败，未安装')
    progress(actual)


def _unpack(asset, archive, stage):
    destination = _relative(asset['destination'])
    prefix = asset['archive_prefix']
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if sum(entry.file_size for entry in entries) > shutil.disk_usage(stage).free:
            raise OSError('磁盘空间不足，无法解压组件')
        for entry in entries:
            # Reject traversal, symlinks and wrong archive roots before selecting files.
            _relative(entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16) or not entry.filename.startswith(prefix):
                raise ValueError('组件压缩包包含无效文件')
            if entry.is_dir():
                continue
            relative = _relative(entry.filename[len(prefix):])
            is_license = relative.name.lower().startswith(('license', 'notice', 'copying'))
            if asset.get('runtime_only'):
                if is_license:
                    relative = Path('licenses') / asset['id'] / relative.name
                elif relative.parts[0] != 'bin':
                    continue
            target = stage / destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


def _unpack_7z(asset, archive, stage):
    # Stream only catalog-listed files to our own paths. tar never creates paths
    # or links from archive metadata, and unrelated platform binaries are skipped.
    destination = _relative(asset['destination'])
    members = [_relative(name) for name in asset['archive_files']]
    tar = Path(os.environ['SystemRoot']) / 'System32/tar.exe'
    if not tar.is_file():
        raise FileNotFoundError('缺少 Windows 自带的 tar.exe，无法解包 7z 组件')
    for member in members:
        target = stage / destination / member
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('wb') as output:
            result = subprocess.run([str(tar), '-xOf', str(archive), '--', member.as_posix()],
                                    stdout=output, stderr=subprocess.PIPE, timeout=60,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or target.stat().st_size == 0:
            detail = result.stderr.decode('utf-8', errors='replace')[-1500:]
            raise RuntimeError(f'7z 组件解包失败：{member}；请确认 Windows tar 支持 7z。{detail}')


def _publish(stage, dependencies):
    base = dependencies.resolve()
    files = sorted(path for path in stage.rglob('*') if path.is_file())
    # Validate every destination before publishing any file. Do not follow links out
    # of the shared directory, and preserve user-edited example configurations.
    for source in files:
        target = dependencies / source.relative_to(stage)
        if not target.resolve().is_relative_to(base):
            raise ValueError('组件目标路径超出共享目录')
        if target.exists() and not target.is_file():
            raise ValueError(f'组件文件位置被目录占用：{target}')
    for source in files:
        target = dependencies / source.relative_to(stage)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == '.cfg' and target.exists():
            continue
        # Staging is on the same volume. A cancelled operation can leave complete,
        # verified files in place, never a partially written runtime DLL or model.
        os.replace(source, target)


def install(assets, dependencies, workdir, report):
    dependencies, workdir = Path(dependencies).resolve(), Path(workdir).resolve()
    if not assets:
        raise ValueError('没有可安装的缺失组件')
    if not workdir.is_relative_to(dependencies) or workdir == dependencies:
        raise ValueError('安装暂存目录必须位于共享目录内')
    total = sum(asset['size'] for asset in assets)
    if shutil.disk_usage(workdir).free < total * 3 + 64 * 1024 * 1024:
        raise OSError('磁盘可用空间不足，请释放空间后重试')
    stage = workdir / 'staged'
    stage.mkdir()
    downloaded = 0
    for index, asset in enumerate(assets):
        archive = workdir / f'download-{index}'
        report('progress', f'正在下载 {asset["name"]}', downloaded, total)
        _download(asset['url'], archive,
                  lambda count: report('progress', f'正在下载 {asset["name"]}', downloaded + count, total),
                  size=asset['size'], sha256=asset['sha256'])
        downloaded += asset['size']
        report('progress', f'正在准备 {asset["name"]}', downloaded, total)
        destination = _relative(asset['destination'])
        if asset.get('archive_format') == '7z':
            _unpack_7z(asset, archive, stage)
        elif 'archive_prefix' in asset:
            _unpack(asset, archive, stage)
        else:
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            archive.replace(target)
        if asset.get('license_url'):
            license_file = stage / (destination if 'archive_prefix' in asset else destination.parent) / (
                'LICENSE.txt' if 'archive_prefix' in asset else 'LICENSE.html')
            _download(asset['license_url'], license_file, lambda _: None)
    report('progress', '正在安装已校验的组件', total, total)
    _publish(stage, dependencies)
    report('complete', '组件安装完成，正在重新检测', total, total)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--failed', nargs='+', required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    arguments = parser.parse_args()

    def report(event, message, received=0, total=0):
        print(json.dumps(dict(event=event, message=message, received=received, total=total), ensure_ascii=False), flush=True)

    try:
        install(install_plan(supported_components(), set(arguments.failed)),
                python_executable().parent.parent / 'dependencies', arguments.workdir, report)
    except Exception as exc:
        report('error', str(exc))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
