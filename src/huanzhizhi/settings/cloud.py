"""Browse and install the small official cloud skill catalog."""
from pathlib import Path
import tempfile

from PySide6 import QtCore, QtWidgets
from huanzhizhi.cloud_skills import fetch_catalog, prepare_skill, install_prepared
from huanzhizhi.widgets import ComboBox


class _CloudWorker(QtCore.QThread):
    result = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(str)

    def __init__(self, item=None, workdir=None, parent=None):
        super().__init__(parent)
        self.item, self.workdir = item, workdir

    def report(self, received):
        if self.isInterruptionRequested():
            raise InterruptedError('已取消')
        if self.item:
            self.progress.emit(f'正在下载 {self.item["id"]}：{min(100, received * 100 // self.item["size"])}%')

    def run(self):
        try:
            result = (prepare_skill(self.item, self.workdir, self.report) if self.item
                      else fetch_catalog(self.report))
            self.report(self.item['size'] if self.item else 0)
            self.result.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class CloudSkillsPage(QtWidgets.QWidget):
    def __init__(self, tasks=None):
        super().__init__()
        self.tasks = tasks
        self.worker = None
        self._temporary = None
        self._cancelled = False
        self._loaded = False
        self._items = []
        self._buttons = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        detail = QtWidgets.QLabel('挑选喜欢的技能，安装后可在“技能列表 → 正式”中使用。技能以你的用户权限运行，请确认用途后安装。')
        detail.setObjectName('desktopPetSettingsRowDetail')
        detail.setWordWrap(True)
        layout.addWidget(detail)
        controls = QtWidgets.QHBoxLayout()
        self.category = ComboBox()
        self.category.setAccessibleName('技能分类')
        self.category.addItem('全部')
        controls.addWidget(self.category)
        controls.addStretch()
        self.refresh_button = QtWidgets.QPushButton('刷新')
        controls.addWidget(self.refresh_button)
        self.cancel_button = QtWidgets.QPushButton('取消')
        self.cancel_button.hide()
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll, 1)
        self.status = QtWidgets.QLabel('打开此页后加载云端技能。')
        self.status.setObjectName('desktopPetSettingsRowDetail')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_button.clicked.connect(self.refresh)
        self.cancel_button.clicked.connect(self.cancel)
        self.category.currentTextChanged.connect(self._render)
        if tasks is not None:
            tasks.tasks_changed.connect(self._sync_buttons)
            tasks.process.state_changed.connect(self._sync_buttons)
        self._render()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded and self.worker is None:
            self.refresh()
        else:
            self._sync_buttons()

    def _busy(self):
        return self.tasks is None or self.tasks.process.state != 'idle' or self.tasks.external_work_active

    def _installed(self, name):
        return self.tasks is not None and ((self.tasks.tasks_root / name).exists()
            or (self.tasks.tasks_root / f'task-{name}.exe').exists())

    def _sync_buttons(self, *_args):
        for name, button in self._buttons.items():
            installed = self._installed(name)
            button.setText('已安装' if installed else '安装')
            button.setEnabled(not installed and self.worker is None and not self._busy())
            button.setToolTip('保留本地版本，不自动覆盖。' if installed else
                              '请先停止正在运行或开发的技能。' if self._busy() else '')

    def _render(self, *_args):
        previous = self.scroll.takeWidget()
        if previous:
            previous.deleteLater()
        content = QtWidgets.QWidget()
        content.setObjectName("desktopPetSettingsContent")
        self._grid = QtWidgets.QGridLayout(content)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)
        self._grid.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self._cards = []
        self._buttons = {}
        selected = self.category.currentText()
        for item in self._items:
            if selected != '全部' and item['category'] != selected:
                continue
            card = QtWidgets.QFrame()
            card.setObjectName('desktopPetSettingsCard')
            card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
            text = QtWidgets.QVBoxLayout(card)
            text.setContentsMargins(16, 14, 16, 14)
            text.setSpacing(8)
            name = QtWidgets.QLabel(item['id'])
            name.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            name.setObjectName('desktopPetTaskTitle')
            name.setWordWrap(True)
            text.addWidget(name)
            metadata = QtWidgets.QLabel(f'{item["category"]} · {item["version"]} · {item["size"] / 1024:.0f} KB')
            metadata.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            metadata.setObjectName('desktopPetSettingsRowDetail')
            text.addWidget(metadata)
            description = QtWidgets.QLabel(item['description'])
            description.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            description.setWordWrap(True)
            text.addWidget(description)
            button = QtWidgets.QPushButton('安装')
            button.clicked.connect(lambda checked=False, entry=item: self.install(entry))
            text.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignRight)
            self._buttons[item['id']] = button
            self._cards.append(card)
        self.scroll.setWidget(content)
        self._layout_cards()
        self._sync_buttons()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QtCore.QEvent.Type.Resize:
            self._layout_cards()
        return super().eventFilter(watched, event)

    def _layout_cards(self):
        if not hasattr(self, '_grid'):
            return
        columns = max(1, (self.scroll.viewport().width() + 12) // 272)
        for index in range(self._grid.columnCount()):
            self._grid.setColumnStretch(index, 0)
        for card in self._cards:
            self._grid.removeWidget(card)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for index in range(columns):
            self._grid.setColumnStretch(index, 1)

    def refresh(self):
        if self.worker is not None:
            return
        self.status.setText('正在加载云端技能…')
        self._start(_CloudWorker(parent=self))

    def install(self, item):
        if self.worker is not None:
            return
        if self._busy():
            self.status.setText('请先停止正在运行或开发的技能，再安装。')
            return
        if self._installed(item['id']):
            self.status.setText('已有同名技能，未覆盖。')
            self._sync_buttons()
            return
        try:
            directory = self.tasks.tasks_root.parent / 'artifacts'
            directory.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix='cloud-skill-', dir=directory)
            self.status.setText(f'正在准备 {item["id"]}…')
            self._start(_CloudWorker(item, Path(self._temporary.name), self))
        except Exception as error:
            self._failed(str(error))
            self._finished()

    def _start(self, worker):
        self._cancelled = False
        self.worker = worker
        worker.result.connect(self._result)
        worker.failed.connect(self._failed)
        worker.progress.connect(self._progress)
        worker.finished.connect(self._finished)
        self.refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self._sync_buttons()
        worker.start()

    def _progress(self, message):
        if self.sender() is self.worker and not self._cancelled:
            self.status.setText(message)

    def _result(self, result):
        if self.sender() is not self.worker or self._cancelled:
            return
        if isinstance(result, list):
            self._items = result
            self._loaded = True
            selected = self.category.currentText()
            self.category.blockSignals(True)
            self.category.clear()
            self.category.addItems(['全部'] + sorted({item['category'] for item in result}))
            index = self.category.findText(selected)
            self.category.setCurrentIndex(max(0, index))
            self.category.blockSignals(False)
            self._render()
            self.status.setText(f'共 {len(result)} 个技能。' if result else '暂无云端技能。')
        else:
            try:
                if self._busy():
                    raise RuntimeError('有技能正在运行或开发，本次未安装；请结束后重试。')
                destination = install_prepared(result, self.tasks.tasks_root, self.tasks.reload_tasks)
                self.status.setText(f'已安装“{destination.name}”，可在技能列表的“正式”分组运行。')
            except Exception as error:
                self._failed(str(error))

    def _failed(self, message):
        if isinstance(self.sender(), _CloudWorker) and self.sender() is not self.worker:
            return
        self.status.setText('已取消。' if self._cancelled else f'未完成：{message}。可点击刷新或安装重试。')

    def _finished(self):
        if isinstance(self.sender(), _CloudWorker) and self.sender() is not self.worker:
            return
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self.refresh_button.setEnabled(True)
        self.cancel_button.hide()
        self._sync_buttons()

    def cancel(self):
        self._cancelled = True
        if self.worker is not None:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText('正在取消…')

    def stop(self):
        self.cancel()
        if self.worker is not None:
            # Downloads check interruption every 250 ms and terminate curl.
            self.worker.wait()
            self._finished()
