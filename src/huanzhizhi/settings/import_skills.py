"""Batch local ZIP import with validation off the UI thread."""
from pathlib import Path
import tempfile
import zipfile

from PySide6 import QtCore, QtWidgets
from huanzhizhi.tasks.storage import prepare_local_skill
from huanzhizhi.cloud_skills import install_prepared


class _ImportWorker(QtCore.QThread):
    prepared = QtCore.Signal(str, object, str)

    def __init__(self, filenames, workdir, parent):
        super().__init__(parent)
        self.filenames, self.workdir = filenames, workdir

    def run(self):
        for index, filename in enumerate(self.filenames):
            if self.isInterruptionRequested():
                break
            try:
                stage = self.workdir / str(index)
                stage.mkdir()
                source = prepare_local_skill(Path(filename), stage)
                self.prepared.emit(filename, source, '')
            except zipfile.BadZipFile:
                self.prepared.emit(filename, None, 'ZIP 文件损坏或格式不正确')
            except SyntaxError as error:
                self.prepared.emit(filename, None, f'Python 语法校验失败：{Path(error.filename).name} 第 {error.lineno} 行')
            except Exception as error:
                self.prepared.emit(filename, None, str(error))


class ImportSkillsDialog(QtWidgets.QDialog):
    progress_message = QtCore.Signal(str)
    completed = QtCore.Signal()

    def __init__(self, tasks, filenames, parent):
        super().__init__(parent)
        self.setWindowTitle('导入技能')
        self.resize(520, 320)
        self.tasks = tasks
        self.count = 0
        self.cancelled = False
        self.done_importing = False
        tasks.tasks_root.parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='.skill-import-', dir=tasks.tasks_root.parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel('正在校验技能包…')
        layout.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, len(filenames))
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.results = QtWidgets.QPlainTextEdit()
        self.results.setReadOnly(True)
        layout.addWidget(self.results)
        self.button = QtWidgets.QPushButton('取消')
        self.button.clicked.connect(self.reject)
        layout.addWidget(self.button, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        self.worker = _ImportWorker(filenames, Path(self.temporary.name), self)
        self.worker.prepared.connect(self._prepared)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _prepared(self, filename, source, error):
        if self.cancelled or self.done_importing:
            return
        if not error:
            try:
                if self.tasks.process.state != 'idle' or self.tasks.external_work_active:
                    raise RuntimeError('有技能正在运行或开发，请结束后重试')
                install_prepared(source, self.tasks.tasks_root, self.tasks.reload_tasks)
                self.count += 1
            except Exception as exc:
                error = str(exc)
        self.results.appendPlainText(f'{Path(filename).name}：' + (f'未导入 — {error}' if error else '已加入正式技能'))
        self.progress.setValue(self.progress.value() + 1)
        self.progress_message.emit(f'正在导入技能 {self.progress.value()}/{self.progress.maximum()}，已成功 {self.count} 个。')

    def _finished(self):
        if self.done_importing:
            return
        self.worker.wait()
        self.temporary.cleanup()
        self.done_importing = True
        self.status.setText(f'{"已取消后续导入" if self.cancelled else "导入完成"}，成功 {self.count} 个，失败 {self.progress.value() - self.count} 个。')
        self.button.setText('关闭')
        self.button.setEnabled(True)
        self.completed.emit()

    def stop(self):
        if not self.done_importing:
            self.cancelled = True
            self.worker.requestInterruption()
            self.worker.wait()
            self._finished()

    def reject(self):
        if self.done_importing:
            super().reject()
        else:
            self.cancelled = True
            self.worker.requestInterruption()
            self.status.setText('正在取消，已导入的技能会保留…')
            self.button.setEnabled(False)

    def closeEvent(self, event):
        if self.done_importing:
            event.accept()
        else:
            self.reject()
            event.ignore()
