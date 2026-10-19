import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from huanzhizhi.cloud_skills import parse_catalog, prepare_skill, install_prepared
from huanzhizhi.tasks.catalog import TaskCatalog


def entry(content=b''):
    return dict(id='测试技能', category='桌宠互动', description='本地测试', version='1.0.0',
                url='https://raw.githubusercontent.com/huanGrass/HuanZhiZhi-Skills/' + 'a'*40 + '/packages/test.zip',
                size=max(1, len(content)), sha256=hashlib.sha256(content).hexdigest())


class CloudSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, extras=(), manifest=None, script='raise RuntimeError("must not execute")'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as package:
            package.writestr('测试技能/task.py', script)
            package.writestr('测试技能/skill.json', json.dumps(manifest or {'protocol_version': 1, 'id': '测试技能'}))
            for name, content in extras:
                package.writestr(name, content)
        return buffer.getvalue()

    def download(self, content):
        def run(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_bytes(content)
            process = Mock()
            process.poll.return_value = 0
            process.returncode = 0
            return process
        return patch('huanzhizhi.component_install.subprocess.Popen', side_effect=run)

    def prepare(self, content, item=None):
        workdir = self.root / str(len(list(self.root.iterdir())))
        workdir.mkdir()
        with self.download(content):
            return prepare_skill(item or entry(content), workdir, lambda _: None)

    def test_catalog_rejects_untrusted_urls_duplicates_sizes_and_invalid_names(self):
        for changes in ({'url': 'https://example.com/test.zip'}, {'url': entry()['url'].replace('a'*40, 'main')},
                        {'id': '../技能'}, {'id': 'CON'}, {'size': True}, {'size': 21*1024*1024},
                        {'sha256': 'bad'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_catalog({'format': 1, 'skills': [{**entry(), **changes}]})
        with self.assertRaises(ValueError):
            parse_catalog({'format': 1, 'skills': [entry(), entry()]})

    def test_install_does_not_execute_skill_and_reloads_catalog(self):
        source = self.prepare(self.archive())
        tasks = self.root / 'tasks'
        catalog = TaskCatalog(tasks)
        destination = install_prepared(source, tasks, catalog.reload)
        self.assertEqual(catalog.tasks()[0].id, '测试技能')
        self.assertTrue((destination / 'task.py').is_file())

    def test_checksum_failure_never_extracts(self):
        content = self.archive()
        with self.assertRaisesRegex(ValueError, '校验失败'):
            self.prepare(content, {**entry(content), 'sha256': '0'*64})
        self.assertFalse(list(self.root.rglob('task.py')))

    def test_existing_directory_and_executable_are_never_overwritten(self):
        source = self.prepare(self.archive())
        tasks = self.root / 'tasks'; tasks.mkdir()
        for existing in (tasks / '测试技能', tasks / 'task-测试技能.exe'):
            existing.write_bytes(b'user data')
            with self.assertRaises(FileExistsError):
                install_prepared(source, tasks, lambda: None)
            self.assertEqual(existing.read_bytes(), b'user data')
            existing.unlink()
        self.assertTrue(source.exists())

    def test_reload_failure_rolls_back_new_install(self):
        source = self.prepare(self.archive())
        with self.assertRaisesRegex(RuntimeError, 'reload failed'):
            install_prepared(source, self.root / 'tasks', Mock(side_effect=RuntimeError('reload failed')))
        self.assertTrue(source.exists())
        self.assertFalse((self.root / 'tasks/测试技能').exists())

    def test_archive_rejects_paths_aliases_and_links(self):
        link = zipfile.ZipInfo('测试技能/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ('../escape.py', 'other/file.py', '测试技能/NUL', '测试技能/file.',
                     '测试技能/.git/config', '测试技能/TASK.PY', link):
            with self.subTest(name=str(name)), self.assertRaises(ValueError):
                self.prepare(self.archive([(name, b'bad')]))
        self.assertFalse((self.root / 'escape.py').exists())

    def test_manifest_mismatch_and_invalid_python_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ID'):
            self.prepare(self.archive(manifest={'protocol_version': 1, 'id': '其他技能'}))
        with self.assertRaises(SyntaxError):
            self.prepare(self.archive(script='def invalid'))

    def test_cancellation_leaves_no_installed_skill(self):
        content = self.archive()
        with self.download(content), self.assertRaises(InterruptedError):
            prepare_skill(entry(content), self.root, Mock(side_effect=InterruptedError('cancel')))
        self.assertFalse((self.root / 'stage').exists())


if __name__ == '__main__':
    unittest.main()
