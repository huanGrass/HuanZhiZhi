import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from huanzhizhi.component_install import _download, _publish, _unpack, _unpack_7z, install, install_plan
from huanzhizhi.shared_components import katago_paths, supported_components


class ComponentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dependencies = Path(self.temporary.name) / 'dependencies'
        self.workdir = self.dependencies / '.install-test'
        self.workdir.mkdir(parents=True)
        self.events = []

    def report(self, *event):
        self.events.append(event)

    def archive(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def asset(self, content, **extra):
        return dict(id='test', name='test', url='https://example.test/component.zip',
                    sha256=hashlib.sha256(content).hexdigest(), size=len(content),
                    destination='engine/v1', archive_prefix='archive/', runtime_only=True, **extra)

    def curl_response(self, content, returncode=0):
        real_popen = subprocess.Popen
        def launch(command, **kwargs):
            if '--output' not in command:
                return real_popen(command, **kwargs)
            Path(command[command.index('--output') + 1]).write_bytes(content)
            process = mock.Mock()
            process.poll.return_value = returncode
            process.returncode = returncode
            return process
        return mock.patch('huanzhizhi.component_install.subprocess.Popen', side_effect=launch)

    def test_plan_installs_only_failed_requirements_and_deduplicates_engine(self):
        catalog = supported_components()
        self.assertEqual(install_plan(catalog, {'katago.gpu', 'katago.analysis'}), [])
        plan = install_plan(catalog, {'katago.engine', 'katago.msvc', 'katago.model'})
        self.assertEqual([asset['id'] for asset in plan], ['engine', 'model'])

    def test_pikafish_engine_and_model_share_one_verified_download(self):
        catalog = supported_components()
        engine = install_plan(catalog, {'pikafish.engine'})
        self.assertEqual(engine, install_plan(catalog, {'pikafish.model'}))
        self.assertEqual(engine, install_plan(catalog, {'pikafish.engine', 'pikafish.model'}))
        self.assertEqual(len(engine), 1)
        self.assertEqual(install_plan(catalog, {'pikafish.analysis'}), [])

    def test_native_archive_installs_only_catalog_files_and_keeps_licenses(self):
        # Windows tar accepts both ZIP and 7z; a small ZIP exercises the real
        # streaming extraction path without storing a binary fixture in Git.
        content = self.archive({'engine.exe': b'engine', 'model.nnue': b'model',
                                'Copying.txt': b'license', '../escape': b'unselected',
                                'linux-engine': b'other platform'})
        asset = self.asset(content)
        asset.pop('archive_prefix')
        asset.update(archive_format='7z', archive_files=['engine.exe', 'model.nnue', 'Copying.txt'])
        with self.curl_response(content):
            install([asset], self.dependencies, self.workdir, self.report)
        self.assertEqual(sorted(p.name for p in (self.dependencies / 'engine/v1').iterdir()),
                         ['Copying.txt', 'engine.exe', 'model.nnue'])
        self.assertEqual((self.dependencies / 'engine/v1/model.nnue').read_bytes(), b'model')

    def test_native_archive_rejects_bad_selected_paths_and_missing_members(self):
        archive = self.workdir / 'archive'
        archive.write_bytes(self.archive({'engine.exe': b'engine'}))
        for member, error in (('../escape', ValueError), ('missing', RuntimeError)):
            with self.subTest(member=member), self.assertRaises(error):
                _unpack_7z(dict(destination='engine', archive_files=[member]), archive, self.workdir / 'stage')

    def test_verified_runtime_install_preserves_unrelated_files_and_license(self):
        destination = self.dependencies / 'engine/v1'
        destination.mkdir(parents=True)
        (destination / 'custom.txt').write_text('keep')
        content = self.archive({'archive/bin/runtime.dll': b'runtime', 'archive/LICENSE': b'license',
                                'archive/lib/dev.lib': b'development-only'})
        with self.curl_response(content):
            install([self.asset(content)], self.dependencies, self.workdir, self.report)
        self.assertEqual((destination / 'bin/runtime.dll').read_bytes(), b'runtime')
        self.assertEqual((destination / 'licenses/test/LICENSE').read_bytes(), b'license')
        self.assertEqual((destination / 'custom.txt').read_text(), 'keep')
        self.assertFalse((destination / 'lib').exists())
        self.assertEqual(self.events[-1][0], 'complete')

    def test_wrong_hash_or_incomplete_download_never_overwrites_existing_component(self):
        destination = self.dependencies / 'engine/v1/bin'
        destination.mkdir(parents=True)
        (destination / 'runtime.dll').write_bytes(b'existing')
        content = self.archive({'archive/bin/runtime.dll': b'new'})
        for wrong_content in (content[:-1], b'x' * len(content)):
            with self.subTest(size=len(wrong_content)), self.curl_response(wrong_content), self.assertRaises(ValueError):
                _download('https://example.test/component', self.workdir / 'download', lambda _: None,
                          size=len(content), sha256=hashlib.sha256(content).hexdigest())
            self.assertEqual((destination / 'runtime.dll').read_bytes(), b'existing')

    def test_traversal_absolute_paths_and_zip_symlinks_are_rejected(self):
        stage = self.workdir / 'stage'
        stage.mkdir()
        archive = self.workdir / 'archive.zip'
        for name in ('archive/../../escape', 'C:/escape', '/escape', 'archive/a:stream'):
            archive.write_bytes(self.archive({name: b'unsafe'}))
            with self.subTest(name=name), self.assertRaises(ValueError):
                _unpack(self.asset(archive.read_bytes()), archive, stage)
        with zipfile.ZipFile(archive, 'w') as package:
            entry = zipfile.ZipInfo('archive/link')
            entry.external_attr = 0o120777 << 16
            package.writestr(entry, '../outside')
        with self.assertRaises(ValueError):
            _unpack(self.asset(archive.read_bytes()), archive, stage)

    def test_existing_example_config_is_preserved_when_repairing(self):
        stage = self.workdir / 'stage'
        (stage / 'engine').mkdir(parents=True)
        (self.dependencies / 'engine').mkdir()
        (stage / 'engine/example.cfg').write_text('default')
        (self.dependencies / 'engine/example.cfg').write_text('custom')
        (stage / 'engine/runtime.dll').write_bytes(b'verified')
        _publish(stage, self.dependencies)
        self.assertEqual((self.dependencies / 'engine/example.cfg').read_text(), 'custom')
        self.assertEqual((self.dependencies / 'engine/runtime.dll').read_bytes(), b'verified')

    def test_local_cuda_runtime_is_preferred_without_changing_environment(self):
        component = supported_components()[0]
        local = self.dependencies / component['cuda']
        local.mkdir(parents=True)
        (local / 'cudart64_12.dll').touch()
        before = dict(os.environ)
        _, _, directories, environment = katago_paths(component, self.dependencies.parent)
        self.assertEqual(directories[-1], local)
        self.assertIn(str(local), environment['PATH'])
        self.assertEqual(before, dict(os.environ))

    def test_download_error_and_low_disk_space_do_not_publish(self):
        with self.curl_response(b'', returncode=22), self.assertRaises(RuntimeError):
            _download('https://example.test/component', self.workdir / 'download', lambda _: None)
        with mock.patch('huanzhizhi.component_install.shutil.disk_usage', return_value=mock.Mock(free=0)), \
                mock.patch('huanzhizhi.component_install._download') as download, self.assertRaises(OSError):
            install([self.asset(b'')], self.dependencies, self.workdir, self.report)
        download.assert_not_called()
        self.assertFalse((self.dependencies / 'engine').exists())
