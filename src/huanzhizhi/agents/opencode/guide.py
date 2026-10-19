"""Only OpenCode offers installation and a free-model first-use guide."""
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from huanzhizhi.agents.runtime import AgentTaskError, SettingsError
from huanzhizhi.widgets import ComboBox
from . import adapter
from .install import install, managed_executable


class _SetupWorker(QtCore.QThread):
    progress = QtCore.Signal(str)

    def __init__(self, action, options, parent):
        super().__init__(parent)
        self.action, self.options = action, dict(options)
        self.result = None
        self.error = ''

    def check_stop(self):
        if self.isInterruptionRequested():
            raise InterruptedError('操作已取消，可随时重试。')

    def run(self):
        try:
            if self.action == 'install':
                try:
                    self.options['executable'] = adapter.check_configuration(self.options, self.check_stop)
                except SettingsError:
                    self.options['executable'] = install(self.progress.emit, self.check_stop)
            if self.action in ('test', 'enable'):
                self.result = {'reply': adapter.test_connection(self.options, self.check_stop)}
            else:
                path = adapter.check_configuration(self.options, self.check_stop)
                self.progress.emit('程序可用，正在读取当前免费模型。')
                self.result = {'executable': path, 'models': adapter.free_models(path, self.check_stop)}
        except Exception as error:
            self.error = error.user_message if isinstance(error, AgentTaskError) else str(error)


class OpenCodeGuide(QtWidgets.QWidget):
    changed = QtCore.Signal()
    busy_changed = QtCore.Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.worker = None
        self._models = []
        self._closed = False
        self._load_attempted = False
        self._failed = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        self.model_combo = ComboBox()
        self.model_combo.setAccessibleName('OpenCode 免费模型')
        self.model_combo.setToolTip('免费模型的可用性与额度由服务商决定。')
        actions = QtWidgets.QHBoxLayout()
        self.install_button = QtWidgets.QPushButton('安装 OpenCode')
        self.detect_button = QtWidgets.QPushButton('重试')
        for button in (self.install_button, self.detect_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.program_toggle = program_toggle = QtWidgets.QToolButton()
        program_toggle.setText('修复设置')
        program_toggle.setCheckable(True)
        program_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        program_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        layout.addWidget(program_toggle)
        program_details = QtWidgets.QWidget()
        program_layout = QtWidgets.QVBoxLayout(program_details)
        program_layout.setContentsMargins(0, 0, 0, 0)
        self.location = QtWidgets.QLabel()
        self.location.setWordWrap(True)
        program_layout.addWidget(self.location)
        self.browse_button = QtWidgets.QPushButton('选择已安装程序')
        program_layout.addWidget(self.browse_button)
        program_details.hide()
        program_toggle.toggled.connect(program_details.setVisible)
        program_toggle.toggled.connect(lambda opened: program_toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if opened else QtCore.Qt.ArrowType.RightArrow))
        layout.addWidget(program_details)
        self.cancel_button = QtWidgets.QPushButton('取消')
        layout.addWidget(self.cancel_button)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName('desktopPetSettingsRowDetail')
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.install_button.clicked.connect(lambda: self.start('install'))
        self.detect_button.clicked.connect(lambda: self.start('detect'))
        self.browse_button.clicked.connect(self.browse)
        self.model_combo.currentIndexChanged.connect(self.save_model)
        self.cancel_button.clicked.connect(self.cancel)
        self.refresh()

    def refresh(self):
        options = self.settings.get_opencode_options() if self.settings else adapter.DEFAULT_OPTIONS
        self.location.setText('程序：' + (options['executable'] or '自动检测；新安装目录：' + str(managed_executable().parent)))
        self.location.setToolTip(options['executable'] or str(managed_executable().parent))
        if not self._models and self.settings:
            self._models = self.settings.get_opencode_model_cache()
        self._fill_models(options['model'])
        if self._models and self.model_combo.currentData() != options['model'] and not self.settings.ai_work_active():
            options['model'] = self.model_combo.currentData()
            self.settings.set_opencode_options(options)
            self.settings.sync()
        self.update_controls()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._load_attempted and self.settings and not self.settings.ai_work_active():
            self._load_attempted = True
            if not self._models:
                self.start('detect')

    def _fill_models(self, selected):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in self._models:
            self.model_combo.addItem(model['name'], model['id'])
        if not self._models:
            self.model_combo.addItem('正在加载模型…', '')
        index = self.model_combo.findData(selected)
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_combo.blockSignals(False)

    def enable(self):
        if not self.model_combo.currentData():
            self.status.setText('请先选择可用模型。')
            return
        self.start('enable')

    def update_controls(self):
        working = self.settings.ai_work_active() if self.settings else False
        available = self.settings is not None and self.worker is None and not working
        for control in (self.install_button, self.detect_button, self.browse_button):
            control.setEnabled(available)
        options = self.settings.get_opencode_options() if self.settings else adapter.DEFAULT_OPTIONS
        try:
            path = adapter.executable(options)
        except SettingsError:
            self.install_button.setText('安装并开始使用')
            self.install_button.setVisible(True)
        else:
            self.install_button.setText('已安装')
            self.install_button.setVisible(False)
            self.install_button.setEnabled(False)
            self.location.setText('程序：' + path)
            self.location.setToolTip(path)
        self.model_combo.setEnabled(available and bool(self._models))
        self.detect_button.setVisible(self._failed)
        self.program_toggle.setVisible(self._failed)
        if not self._failed:
            self.program_toggle.setChecked(False)
        self.cancel_button.setVisible(self.worker is not None)

    def start(self, action):
        if self.worker is not None or self.settings is None or self.settings.ai_work_active():
            return
        options = self.settings.get_opencode_options()
        if action in ('test', 'enable'):
            self.settings.clear_opencode_verification()
            self.settings.sync()
            self.changed.emit()
        self._failed = False
        self.worker = _SetupWorker(action, options, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self.finished)
        self.status.setText('正在连接，请稍候…' if action in ('test', 'enable') else '正在加载可用模型…')
        self.update_controls()
        self.busy_changed.emit()
        self.worker.start()

    def finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        try:
            if self._closed:
                return
            if worker.error or worker.isInterruptionRequested():
                self._failed = True
                self.status.setText(worker.error or '操作已取消，可随时重试。')
                return
            if worker.action in ('test', 'enable'):
                self.settings.mark_opencode_verified(worker.options)
                self.settings.sync()
                if worker.action == 'enable' and self.settings.get_agent_tool() == 'opencode':
                    self.settings.set_ai_enabled(True)
                    self.settings.sync()
                self.changed.emit()
                self.status.setText('')
            else:
                options = self.settings.get_opencode_options()
                if worker.action == 'install':
                    options['executable'] = worker.result['executable']
                self._models = worker.result['models']
                self._fill_models(options['model'])
                options['model'] = self.model_combo.currentData() or ''
                self.settings.set_opencode_options(options)
                self.settings.set_opencode_model_cache(self._models)
                self.settings.sync()
                self.changed.emit()
                self.location.setText('程序：' + options['executable'])
                self._failed = not bool(self._models)
                self.status.setText('' if self._models else '暂时没有可用的免费模型，请稍后重试。')
        except Exception as error:
            self._failed = True
            self.status.setText(str(error))
        finally:
            worker.deleteLater()
            self.update_controls()
            self.busy_changed.emit()

    def save_model(self):
        if not self.settings or self.worker is not None:
            return
        try:
            options = self.settings.get_opencode_options()
            options['model'] = self.model_combo.currentData() or ''
            self.settings.set_opencode_options(options)
            self.settings.sync()
            self.status.setText('')
            self.changed.emit()
        except Exception as error:
            self.status.setText(str(error))
        self.update_controls()

    def browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择 OpenCode 2 程序', '', 'OpenCode 程序 (*.exe)')
        if not path:
            return
        try:
            options = self.settings.get_opencode_options()
            options['executable'] = str(Path(path).resolve())
            self.settings.set_opencode_options(options)
            self.settings.sync()
            self.changed.emit()
            self._models = []
            self.refresh()
            self.start('detect')
        except Exception as error:
            self.status.setText(str(error))

    def cancel(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status.setText('正在取消并停止子进程…')

    def stop(self):
        self._closed = True
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait()
            self.finished()
