import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from huanzhizhi.tasks.catalog import TaskCatalog
from huanzhizhi.tasks.storage import export_skill


class SkillExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'tasks' / '演示'
        self.source.mkdir(parents=True)
        (self.source / 'task.py').write_text('raise RuntimeError("must not execute")', encoding='utf-8')
        (self.source / 'skill.json').write_text(json.dumps({'protocol_version': 1, 'id': '演示', 'description': '旧介绍'}), encoding='utf-8')
        (self.source / 'assets').mkdir()
        (self.source / 'assets' / '样本.bin').write_bytes(b'asset')
        (self.source / '__pycache__').mkdir()
        (self.source / '__pycache__' / 'task.pyc').write_bytes(b'cache')
        (self.source / '.git').mkdir()
        (self.source / '.git' / 'config').write_text('private')
        self.task = TaskCatalog(self.source.parent).reload()[0]
        self.output = self.root / '演示.zip'

    def test_zip_contains_saved_metadata_and_resources_without_executing(self):
        original = (self.source / 'skill.json').read_bytes()
        export_skill(self.task, self.output, description='已保存的介绍')
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {'演示/task.py', '演示/skill.json', '演示/assets/样本.bin'})
            self.assertEqual(json.loads(archive.read('演示/skill.json'))['description'], '已保存的介绍')
            self.assertEqual(archive.read('演示/assets/样本.bin'), b'asset')
        self.assertEqual((self.source / 'skill.json').read_bytes(), original)

    def test_failure_preserves_existing_destination_and_removes_partial_zip(self):
        self.output.write_bytes(b'original')
        with mock.patch('zipfile.ZipFile.write', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                export_skill(self.task, self.output, description='')
        self.assertEqual(self.output.read_bytes(), b'original')
        self.assertEqual(list(self.root.glob('*.zip')), [self.output])

    def test_root_runtime_is_excluded_without_reading_it_or_losing_resources(self):
        runtime = self.source / '.runtime'
        (runtime / 'state').mkdir(parents=True)
        checkpoint = runtime / 'state/checkpoint.json'
        checkpoint.write_bytes(b'private state')
        (self.source / 'assets/.runtime').mkdir()
        (self.source / 'assets/.runtime/reference.png').write_bytes(b'resource')
        read_bytes = Path.read_bytes

        def read_resource(path):
            if path.is_relative_to(runtime):
                raise AssertionError('Export must not read runtime files')
            return read_bytes(path)

        with mock.patch.object(Path, 'read_bytes', read_resource):
            export_skill(self.task, self.output, description='')
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any(name.startswith('演示/.runtime/') for name in archive.namelist()))
            self.assertEqual(archive.read('演示/assets/.runtime/reference.png'), b'resource')
        self.assertEqual(checkpoint.read_bytes(), b'private state')

    def test_destination_inside_skill_rejected(self):
        with self.assertRaisesRegex(ValueError, '技能目录之外'):
            export_skill(self.task, self.source / 'export.zip', description='')
        self.assertFalse((self.source / 'export.zip').exists())

    def test_standalone_executable_exports_only_entry_and_manifest(self):
        entry = self.root / 'legacy' / 'task-demo.exe'
        entry.parent.mkdir()
        entry.write_bytes(b'EXE')
        entry.with_suffix('.skill.json').write_text(json.dumps({'protocol_version': 1, 'id': 'demo'}))
        runtime = entry.parent / '.runtime' / entry.stem
        runtime.mkdir(parents=True)
        (runtime / 'state.json').write_text('private')
        (entry.parent / 'unrelated.txt').write_text('private')
        task = TaskCatalog(entry.parent).reload()[0]
        export_skill(task, self.output, description='legacy')
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {'task-demo.exe', 'task-demo.skill.json'})

    def test_link_rejection_does_not_create_archive(self):
        with mock.patch('pathlib.Path.is_symlink', return_value=True):
            with self.assertRaisesRegex(ValueError, '链接'):
                export_skill(self.task, self.output, description='')
        self.assertFalse(self.output.exists())
