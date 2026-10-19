"""Install a verified official OpenCode binary without changing PATH or accounts."""
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile

from huanzhizhi.component_install import _download

VERSION = '2.0.32'
URL = 'https://registry.npmjs.org/@opencode/cli-windows-x64-baseline/-/cli-windows-x64-baseline-2.0.32.tgz'
SIZE = 100022258
SHA256 = '64fc191d29cffbf3a0d7a70f5631ea3f70aeef000c5ec5c73465f3665aa09047'


def managed_executable():
    return Path(os.environ['LOCALAPPDATA']) / 'HuanZhiZhi/agents/opencode' / VERSION / 'opencode.exe'


def install(report, check_stop):
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('当前引导安装包支持 Windows x64；其他架构请安装官方 OpenCode 2 后选择程序。')
    target = managed_executable()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return str(target)
    if shutil.disk_usage(target.parent).free < SIZE + 230_000_000:
        raise OSError('安装 OpenCode 至少需要 330 MB 可用空间。')
    with tempfile.TemporaryDirectory(prefix='install-', dir=target.parent) as temporary:
        stage = Path(temporary)
        def progress(count):
            check_stop()
            report(f'正在下载 OpenCode：{count * 100 // SIZE}%')
        _download(URL, stage / 'package.tgz', progress, size=SIZE, sha256=SHA256)
        check_stop()
        report('校验通过，正在安装 OpenCode。')
        with tarfile.open(stage / 'package.tgz') as archive:
            member = archive.getmember('package/bin/opencode.exe')
            if not member.isfile() or member.size > 230_000_000:
                raise ValueError('OpenCode 安装包内容无效。')
            # Read only the verified executable; never extract archive paths or links.
            with archive.extractfile(member) as source, (stage / 'opencode.exe').open('wb') as output:
                while chunk := source.read(1024 * 1024):
                    check_stop()
                    output.write(chunk)
        check_stop()
        os.replace(stage / 'opencode.exe', target)
    return str(target)
