"""Select models from the installed Harness without making a model request."""
from PySide6 import QtCore, QtWidgets

from huanzhizhi.widgets import ComboBox
from . import adapter


class _CatalogWorker(QtCore.QThread):
    def __init__(self, parent):
        super().__init__(parent)
        self.models = []
        self.error = ''

    def run(self):
        def check_stop():
            if self.isInterruptionRequested():
                raise InterruptedError('读取已取消。')
        try:
            self.models = adapter.model_catalog(check_stop)
        except Exception as error:
            self.error = str(error)


class DeepSeekGuide(QtWidgets.QWidget):
    changed = QtCore.Signal()
    busy_changed = QtCore.Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.worker = None
        self._models = []
        self._closed = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        self.model_combo = ComboBox()
        self.model_combo.setAccessibleName('DeepSeek Harness 模型')
        self.reasoning_combo = ComboBox()
        self.reasoning_combo.setAccessibleName('DeepSeek Harness 推理强度')
        self.status = QtWidgets.QLabel('使用 Harness 中已登录的 DeepSeek 账号；读取目录不调用模型。')
        self.status.setObjectName('desktopPetSettingsRowDetail')
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.installation = QtWidgets.QLabel()
        self.installation.setWordWrap(True)
        self.installation.setOpenExternalLinks(True)
        layout.addWidget(self.installation)
        self._refresh_installation()
        self._fill()
        self.model_combo.activated.connect(self._model_changed)
        self.reasoning_combo.activated.connect(self._save)

    def _refresh_installation(self):
        try:
            adapter.launcher()
        except adapter.SettingsError:
            self.installation.setText('未检测到 DeepSeek Harness。'
                                      '<a href="https://www.deepseek.com/harness/">前往官网下载</a>')
        else:
            self.installation.setText('已检测到 DeepSeek Harness。登录和更新请在 Harness 中操作。')

    def _fill(self):
        try:
            options = self.settings.get_deepseek_options() if self.settings else adapter.DEFAULT_OPTIONS
        except adapter.SettingsError as error:
            self.status.setText(str(error))
            options = adapter.DEFAULT_OPTIONS
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in self._models:
            label = model['name'] + ('' if model['image'] else '（不支持图片）')
            self.model_combo.addItem(label, model['id'])
        if self.model_combo.findData(options['model']) < 0:
            self.model_combo.addItem(options['model'], options['model'])
        self.model_combo.setCurrentIndex(self.model_combo.findData(options['model']))
        self.model_combo.blockSignals(False)
        self._fill_efforts(options['reasoning_effort'], preserve=True)

    def _fill_efforts(self, selected, *, preserve=False):
        model = next((m for m in self._models if m['id'] == self.model_combo.currentData()), None)
        efforts = list(model['efforts']) if model else [selected]
        if preserve and selected not in efforts:
            efforts.append(selected)
        self.reasoning_combo.clear()
        self.reasoning_combo.addItems(efforts)
        self.reasoning_combo.setCurrentText(selected if selected in efforts else efforts[0])

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        if self.worker or self._closed or not self.settings or self.settings.ai_work_active():
            return
        self._refresh_installation()
        self.status.setText('正在读取 Harness 账号模型目录，不调用模型。')
        self.worker = _CatalogWorker(self)
        self.worker.finished.connect(self._finished)
        self.update_controls()
        self.busy_changed.emit()
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        if not self._closed:
            if worker.error:
                self.status.setText(worker.error)
            else:
                self._models = worker.models
                self._fill()
                self.status.setText('模型目录已读取。登录状态、额度和模型权限由实际请求确认。')
            self.update_controls()
            self.busy_changed.emit()
        worker.deleteLater()

    def _model_changed(self):
        self._fill_efforts(self.reasoning_combo.currentText())
        self._save()

    def _save(self):
        if not self.settings:
            return
        try:
            self.settings.set_deepseek_options(dict(model=self.model_combo.currentData(),
                                                   reasoning_effort=self.reasoning_combo.currentText()))
            self.settings.sync()
            self.changed.emit()
        except Exception as error:
            self.status.setText(str(error))
            self._fill()

    def update_controls(self):
        available = self.settings is not None and not self.settings.ai_work_active() and self.worker is None
        self.model_combo.setEnabled(available and bool(self._models))
        self.reasoning_combo.setEnabled(available and bool(self._models))

    def cancel(self):
        if self.worker:
            self.worker.requestInterruption()

    def stop(self):
        self._closed = True
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
