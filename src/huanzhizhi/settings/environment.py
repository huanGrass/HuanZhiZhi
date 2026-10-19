"""List supported shared components and inspect their required environments."""
from html import escape
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.python_runtime import python_executable
from huanzhizhi.shared_components import supported_components
from huanzhizhi.component_install import install_plan


class EnvironmentPage(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self._process = None
        self._buffer = bytearray()
        self._failure = ""
        self._items = {}
        self._components = supported_components()
        self._received = set()
        self._mode = 'check'
        self._check_valid = False
        self._staging = None
        self._install_complete = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("外部组件")
        title.setObjectName("desktopPetSettingsTitle")
        layout.addWidget(title)
        detail = QtWidgets.QLabel("幻之之支持的共享外部组件及其依赖环境。列入清单不代表已安装；检测会实际运行引擎，KataGo 使用 GPU，皮卡鱼使用 CPU。")
        detail.setObjectName("desktopPetSettingsRowDetail")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.detect_button = QtWidgets.QPushButton("开始检测")
        self.detect_button.clicked.connect(self.start)
        self.copy_button = QtWidgets.QPushButton("复制报告")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_report)
        actions.addWidget(self.detect_button)
        self.install_button = QtWidgets.QPushButton("一键安装")
        self.install_button.setEnabled(False)
        self.install_button.setToolTip("检测后安装缺失的组件")
        self.install_button.clicked.connect(self.install_missing)
        actions.addWidget(self.install_button)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)
        self.install_hint = QtWidgets.QLabel()
        self.install_hint.setObjectName("desktopPetSettingsRowDetail")
        self.install_hint.setWordWrap(True)
        self.install_hint.hide()
        layout.addWidget(self.install_hint)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.results = QtWidgets.QTreeWidget()
        self.results.setObjectName("desktopPetEnvironmentResults")
        self.results.setAccessibleName("外部组件检测结果")
        self.results.setHeaderLabels(["共享组件 / 依赖", "状态", "要求与检测结果"])
        self.results.setRootIsDecorated(True)
        self.results.setWordWrap(True)
        self.results.setAlternatingRowColors(True)
        self.results.setColumnWidth(0, 190)
        self.results.setColumnWidth(1, 75)
        self.results.header().setStretchLastSection(True)
        self.results.header().sectionResized.connect(self.results.doItemsLayout)
        self.results.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
        layout.addWidget(self.results, 1)
        self.source = QtWidgets.QLabel("选择组件或依赖可查看官方来源。")
        self.source.setWordWrap(True)
        self.source.setOpenExternalLinks(True)
        self.results.currentItemChanged.connect(self._show_source)
        layout.addWidget(self.source)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("desktopPetSettingsRowDetail")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self._populate()

    def _show_source(self, item, _previous=None):
        url = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        self.source.setText(f'<a href="{escape(url, quote=True)}">{escape(item.text(0))} · 官方来源与说明</a>'
                            if url else "选择组件或依赖可查看官方来源。")

    @staticmethod
    def _set_status(item, status):
        color = QtGui.QColor({
            '可用': '#17803d', '满足': '#17803d',
            '不可用': '#c43b3b', '不满足': '#c43b3b',
            '检测中': '#2675c9', '未完成': '#b97712',
        }.get(status, '#8a9099'))
        light = QtGui.QPixmap(24, 24)
        light.setDevicePixelRatio(2)
        light.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(light)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QtCore.QRectF(2, 2, 8, 8))
        painter.end()
        icon = QtGui.QIcon(light)
        icon.addPixmap(light, QtGui.QIcon.Mode.Selected)
        item.setIcon(1, icon)
        item.setForeground(1, color)
        item.setText(1, status)
        item.setToolTip(1, status)

    def _populate(self):
        self.results.clear()
        self._items.clear()
        self.copy_button.setEnabled(False)
        self.detect_button.setText("开始检测")
        for component in self._components:
            parent = QtWidgets.QTreeWidgetItem([component['name'], '未检测', component['purpose']])
            self._set_status(parent, '未检测')
            parent.setData(0, QtCore.Qt.ItemDataRole.UserRole, component['source'])
            self.results.addTopLevelItem(parent)
            for requirement in component['requirements']:
                item = QtWidgets.QTreeWidgetItem([requirement['name'], '未检测', requirement['detail']])
                self._set_status(item, '未检测')
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, requirement['source'])
                item.setToolTip(2, requirement['detail'])
                parent.addChild(item)
                self._items[component['id'] + '.' + requirement['id']] = item
            parent.setExpanded(True)
        self.detect_button.setEnabled(bool(self._items))
        self.status.setText("开始检测以确认本机可用性；缺失组件可点击一键安装。" if self._items else "暂无支持的共享组件。")

    def start(self):
        if self._process is not None:
            return
        if not self._items:
            return
        self._populate()
        self._mode = 'check'
        self._check_valid = False
        self.install_button.setEnabled(False)
        self.install_hint.hide()
        self.progress.hide()
        self._buffer.clear()
        self._received.clear()
        self._failure = ""
        self.copy_button.setEnabled(False)
        try:
            command = [str(python_executable()), "-I", "-B", "-X", "utf8", "-u", "-m",
                       "huanzhizhi.shared_components"]
        except OSError as exc:
            self.status.setText(f"无法开始检测：{exc}")
            return
        self._start_process(command)

    def _start_process(self, command):
        process = QtCore.QProcess(self)
        self._process = process
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.remove("PYTHONHOME")
        environment.remove("PYTHONPATH")
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(Path(command[0]).parent.parent))
        if self._mode == 'check':
            process.started.connect(self._mark_next_check)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._process_error)
        self.detect_button.setEnabled(False)
        if self._mode == 'check':
            self.status.setText("正在检查共享组件及其依赖环境…")
            self._timer.start(75000)
        process.start(command[0], command[1:])
        process.closeWriteChannel()

    def _update_install_action(self):
        failed = {name for name, item in self._items.items() if item.text(1) == '不满足'}
        assets = install_plan(self._components, failed) if self._check_valid else []
        self.install_button.setText('一键安装')
        self.install_button.setEnabled(bool(assets))
        self.install_button.setToolTip('下载并安装检测未通过的组件' if assets else '没有可自动安装的缺失项')
        notes = []
        if assets:
            size = sum(asset['size'] for asset in assets) / 1024 ** 2
            notes.append(f'可安装 {len(assets)} 项，需下载约 {size:.0f} MB。')
        if any(name.endswith('.gpu') for name in failed):
            notes.append('GPU 或驱动需要另行处理，可选中该项打开官方入口。')
        if failed and not assets and self._check_valid:
            notes.append('其他问题请查看检测报告。')
        self.install_hint.setText(''.join(notes))
        self.install_hint.setVisible(bool(notes))

    def install_missing(self):
        if self._process is not None:
            if self._mode == 'install':
                self.stop()
            return
        failed = {name for name, item in self._items.items() if item.text(1) == '不满足'}
        if not self._check_valid or not install_plan(self._components, failed):
            return
        try:
            interpreter = python_executable()
            dependencies = interpreter.parent.parent / 'dependencies'
            dependencies.mkdir(parents=True, exist_ok=True)
            self._staging = tempfile.TemporaryDirectory(prefix='.install-', dir=dependencies)
        except OSError as exc:
            self.status.setText(f'无法开始安装：{exc}')
            return
        self._mode = 'install'
        self._failure = ''
        self._buffer.clear()
        self._install_complete = False
        self.copy_button.setEnabled(False)
        self.install_button.setText('取消安装')
        self.install_button.setToolTip('停止下载与安装')
        self.install_hint.hide()
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText('正在准备安装…')
        self._start_process([str(interpreter), '-I', '-B', '-X', 'utf8', '-u', '-m',
                             'huanzhizhi.component_install', '--workdir', self._staging.name,
                             '--failed', *sorted(failed)])

    def _install_event(self, row):
        if (not isinstance(row, dict) or set(row) != {'event', 'message', 'received', 'total'}
                or row['event'] not in ('progress', 'complete', 'error')
                or not isinstance(row['message'], str)
                or type(row['received']) is not int or type(row['total']) is not int
                or not 0 <= row['received'] <= row['total']):
            raise ValueError('安装进度格式错误')
        if row['event'] == 'error':
            self._failure = row['message']
        else:
            self.status.setText(row['message'])
            if row['total']:
                self.progress.setValue(min(99, row['received'] * 100 // row['total']))
            self._install_complete = self._install_complete or row['event'] == 'complete'

    def _mark_next_check(self):
        # The probe checks components and requirements sequentially in catalog order.
        for name, item in self._items.items():
            if name not in self._received:
                self._set_status(item, '检测中')
                if item.parent().text(1) == '未检测':
                    self._set_status(item.parent(), '检测中')
                break

    def _read_output(self):
        if self._process is None:
            return
        self._buffer.extend(bytes(self._process.readAllStandardOutput()))
        while b"\n" in self._buffer:
            line, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            try:
                row = json.loads(line)
                if self._mode == 'install':
                    self._install_event(row)
                    continue
                if (not isinstance(row, dict) or set(row) != {"name", "satisfied", "summary", "detail"}
                        or not isinstance(row["name"], str) or type(row["satisfied"]) is not bool
                        or not isinstance(row["summary"], str)
                        or not isinstance(row["detail"], str)):
                    raise ValueError("无效检测结果")
                if row["name"] not in self._items or row["name"] in self._received:
                    raise ValueError("未知或重复的依赖检查结果")
                self._received.add(row["name"])
            except (ValueError, UnicodeError):
                self._failure = "安装进度格式错误" if self._mode == 'install' else "检测结果格式错误"
                continue
            item = self._items[row['name']]
            item.setData(2, QtCore.Qt.ItemDataRole.UserRole, row['detail'])
            self._set_status(item, '满足' if row['satisfied'] else '不满足')
            item.setText(2, row['summary'])
            item.setToolTip(2, row['summary'])
            parent = item.parent()
            states = [parent.child(i).text(1) for i in range(parent.childCount())]
            self._set_status(parent, '不可用' if '不满足' in states else '可用' if all(s == '满足' for s in states) else '检测中')
            self._mark_next_check()

    def _timeout(self):
        if self._process is not None:
            self._failure = "检测超时（75 秒），已保留已完成的结果"
            self._kill_process_tree()

    def _kill_process_tree(self):
        process = self._process
        if process is None:
            return
        if process.processId():
            try:
                subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                                '/PID', str(process.processId()), '/T', '/F'],
                               capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        process.kill()

    def _process_error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._finished(-1)

    def _finished(self, code, _status=None):
        process = self._process
        if process is None:
            return
        self._timer.stop()
        self._read_output()
        diagnostic = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if code and not self._failure:
            self._failure = diagnostic[-2000:] or process.errorString()
        if self._mode == 'install':
            self._finish_install(process)
            return
        if self._buffer or len(self._received) != len(self._items):
            self._failure = self._failure or "未收到完整的检测结果"
        failures = sum(item.text(1) == "不满足" for item in self._items.values())
        if self._failure:
            for item in self._items.values():
                if item.text(1) == '检测中':
                    self._set_status(item, '未完成')
            for index in range(self.results.topLevelItemCount()):
                self._set_status(self.results.topLevelItem(index), '未完成')
        timestamp = QtCore.QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        summary = f"检测未完成：{self._failure}" if self._failure else f"检测完成，{failures} 项检查不满足。" if failures else "共享组件及其依赖均可用。"
        self.status.setText(f"{timestamp} · {summary}")
        self.detect_button.setText("重新检测")
        self.detect_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self._process = None
        process.deleteLater()
        self._check_valid = not self._failure
        self._update_install_action()

    def _finish_install(self, process):
        self._process = None
        process.deleteLater()
        if self._staging is not None:
            try:
                self._staging.cleanup()
            except OSError as exc:
                self._failure = self._failure or f'暂存文件清理失败：{exc}'
            self._staging = None
        success = self._install_complete and not self._failure and not self._buffer
        self.detect_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self._update_install_action()
        if success:
            self.progress.setValue(100)
            self.start()
        else:
            self._failure = self._failure or '未收到完整的安装结果'
            self.status.setText('安装已取消，可重新检测或重试。' if self._failure == '安装已取消' else
                                '安装未完成，请复制报告查看原因后重试。')

    def copy_report(self):
        lines = ["共享组件环境检查", self.status.text()]
        if self._mode == 'install' and self._failure:
            lines.append(self._failure)
        for index in range(self.results.topLevelItemCount()):
            item = self.results.topLevelItem(index)
            lines.append("\t".join(item.text(column) for column in range(3)))
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                lines.append('\t' + '\t'.join(child.text(column) for column in range(3)))
                diagnostic = child.data(2, QtCore.Qt.ItemDataRole.UserRole)
                if diagnostic:
                    lines.append('  ' + diagnostic)
                lines.append('  ' + child.data(0, QtCore.Qt.ItemDataRole.UserRole))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def stop(self):
        self._timer.stop()
        if self._process is not None:
            self._failure = "安装已取消" if self._mode == 'install' else "检测已停止，结果可能不完整"
            self._kill_process_tree()
            self._process.waitForFinished(1000)
