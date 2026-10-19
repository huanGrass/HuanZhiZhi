"""Exercise the real Windows launcher, including native window visibility."""
import json
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.build_launcher import build_launcher


PROBE_MAIN = '''
import ctypes
import json
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from huanzhizhi.application import HuanZhiZhiApplication

root = Path.cwd()
app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
host = HuanZhiZhiApplication(app, project_root=root, settings_path=root / 'settings.ini')
host.start()
native = ctypes.WinDLL('user32')
native.IsWindowVisible.argtypes = [ctypes.c_void_p]
observations = []

def open_settings():
    host.pet.show_settings_requested.emit()
    window = host.pet_settings.window
    observations.append({'qt': window.isVisible(),
                         'native': bool(native.IsWindowVisible(int(window.winId())))})
    if len(observations) == 1:
        window.close()
        QtCore.QTimer.singleShot(100, open_settings)
    else:
        host._close_components()
        (root / 'result.json').write_text(json.dumps(observations), encoding='utf-8')
        app.quit()

QtCore.QTimer.singleShot(100, open_settings)
QtCore.QTimer.singleShot(5000, app.quit)
app.exec()
'''


@unittest.skipUnless(sys.platform == 'win32', 'Windows launcher integration')
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher_directory = tempfile.TemporaryDirectory(prefix='launcher build ')
        cls.addClassCleanup(cls.launcher_directory.cleanup)
        cls.launcher = Path(cls.launcher_directory.name) / '幻之之.exe'
        build_launcher(Path(__file__).resolve().parents[1], cls.launcher)

    def test_exe_shows_settings_from_unicode_path_and_other_working_directory(self):
        self.check_settings_launcher()

    def check_settings_launcher(self, *, source=None):
        source = Path(source) if source is not None else Path(__file__).resolve().parents[1]
        runtime = source / 'python'
        with tempfile.TemporaryDirectory(prefix='之之 launcher ') as temporary:
            root = Path(temporary)
            python = root / 'python'
            python.mkdir()
            # Copy only the interpreter; reuse installed libraries through _pth.
            for name in ('pythonw.exe', 'python311.dll', 'python3.dll',
                         'vcruntime140.dll', 'vcruntime140_1.dll'):
                shutil.copy2(runtime / name, python / name)
            (python / 'python311._pth').write_text('\n'.join(map(str, (
                root / 'src', runtime / 'Lib', runtime / 'DLLs',
                runtime / 'Lib/site-packages', source / 'src',
            ))) + '\nimport site\n', encoding='utf-8')
            package = root / 'src/huanzhizhi'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text(
                f'__path__.append({str(source / "src/huanzhizhi")!r})\n', encoding='utf-8')
            (package / '__main__.py').write_text(PROBE_MAIN, encoding='utf-8')
            environment = dict(os.environ, QT_QPA_PLATFORM='windows')
            shutil.copy2(self.launcher, root / '幻之之.exe')
            command = [str(root / '幻之之.exe')]
            subprocess.run(command, cwd=source, env=environment,
                           creationflags=subprocess.CREATE_NO_WINDOW,
                           check=True, timeout=10, capture_output=True)
            result = root / 'result.json'
            deadline = time.monotonic() + 8
            while not result.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(result.is_file(), 'Launcher did not produce the window probe result')
            observations = json.loads(result.read_text(encoding='utf-8'))
            # Let pythonw finish unloading its DLLs before removing the fixture.
            time.sleep(.3)
            # Qt alone reports visible even when STARTUPINFO suppressed the HWND.
            self.assertEqual(observations, [{'qt': True, 'native': True}] * 2)

    def test_exe_missing_runtime_shows_actionable_error(self):
        native = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        native.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        native.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        native.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        native.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        native.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        def text(window):
            buffer = ctypes.create_unicode_buffer(4096)
            native.GetWindowTextW(window, buffer, len(buffer))
            return buffer.value

        process = subprocess.Popen([str(self.launcher)])
        dialogs, messages = [], []

        @callback_type
        def find_dialog(window, _):
            pid = wintypes.DWORD()
            native.GetWindowThreadProcessId(window, ctypes.byref(pid))
            if pid.value == process.pid and text(window) == '幻之之启动失败':
                dialogs.append(window)
            return True

        @callback_type
        def read_message(window, _):
            messages.append(text(window))
            return True

        try:
            deadline = time.monotonic() + 8
            while not dialogs and process.poll() is None and time.monotonic() < deadline:
                native.EnumWindows(find_dialog, 0)
                time.sleep(.05)
            self.assertTrue(dialogs, 'Missing runtime did not show a launcher error')
            native.EnumChildWindows(dialogs[0], read_message, 0)
            self.assertIn('请完整解压发行包', '\n'.join(messages))
            native.PostMessageW(dialogs[0], 0x0010, 0, 0)  # WM_CLOSE, owned test dialog only
            self.assertEqual(process.wait(timeout=5), 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
