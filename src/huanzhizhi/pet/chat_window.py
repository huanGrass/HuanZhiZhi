"""Window-first skill creation in a resizable panel attached to the pet."""
from __future__ import annotations

import json

import ctypes
import ctypes.wintypes
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from huanzhizhi.widgets import SCROLLBAR_STYLE, TOOL_BUTTON_STYLE

from huanzhizhi.platform.window_policy import WindowPolicy
from huanzhizhi.platform.target_window import DesktopWindowsError, Win32DesktopApi, WindowInfo
from huanzhizhi.skill_creation import CreationWorker
from huanzhizhi.settings.component import SettingsError
from huanzhizhi.pet.exploration_conversation import ExplorationConversation


def _readable_icon(image: QtGui.QImage) -> QtGui.QIcon:
    visible = [image.pixelColor(x, y) for y in range(image.height())
               for x in range(image.width()) if image.pixelColor(x, y).alpha() > 32]
    if visible and all(min(color.red(), color.green(), color.blue()) > 220 for color in visible):
        # White native icons need contrast on both the plain and selected rows.
        backed = QtGui.QImage(image.size(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        backed.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(backed)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor('#645b70'))
        bounds = QtCore.QRectF(backed.rect())
        painter.drawRoundedRect(bounds, image.width()*.22, image.height()*.22)
        painter.drawImage(bounds.adjusted(image.width()*.12, image.height()*.12,
                                         -image.width()*.12, -image.height()*.12), image)
        painter.end()
        image = backed
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))


def _window_icon(hwnd: int) -> QtGui.QIcon:
    """Copy the window's borrowed native icon; bound calls to unresponsive apps."""
    fallback = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
    if sys.platform != 'win32':
        return fallback
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    send = user32.SendMessageTimeoutW
    send.argtypes = (ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.c_size_t,
                     ctypes.c_ssize_t, ctypes.wintypes.UINT, ctypes.wintypes.UINT,
                     ctypes.POINTER(ctypes.c_size_t))
    send.restype = ctypes.c_ssize_t
    handle = ctypes.c_size_t()
    # WM_GETICON / ICON_SMALL2, SMTO_ABORTIFHUNG. Do not send input or activate.
    send(hwnd, 0x007F, 2, 0, 0x0002, 50, ctypes.byref(handle))
    if not handle.value:
        get_icon = user32.GetClassLongPtrW if sys.maxsize > 2**32 else user32.GetClassLongW
        get_icon.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        get_icon.restype = ctypes.c_size_t
        handle.value = get_icon(hwnd, -34) or get_icon(hwnd, -14)
    if handle.value:
        image = QtGui.QImage.fromHICON(handle.value)
        if not image.isNull():
            return _readable_icon(image)
    return fallback


class ChatWindow(QtWidgets.QWidget):
    expanded_changed = QtCore.Signal(bool)
    drag_started = QtCore.Signal(QtCore.QPoint)
    drag_moved = QtCore.Signal(QtCore.QPoint)
    drag_finished = QtCore.Signal()
    exploration_active = QtCore.Signal(bool)
    skill_saved = QtCore.Signal(str)
    log_message = QtCore.Signal(str)
    tasks_requested = QtCore.Signal()
    click_point = QtCore.Signal(int, int)
    foreground_input_requested = QtCore.Signal(object, object)
    foreground_input_cancelled = QtCore.Signal(str)
    foreground_input_stop_requested = QtCore.Signal()
    ai_unavailable = QtCore.Signal(str)

    def __init__(self, *, settings=None) -> None:
        super().__init__(None, QtCore.Qt.WindowType.Tool
                         | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle('创造新技能')
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 300)
        self.resize(400, 520)
        self._window_policy = WindowPolicy(self)
        self._pet_rect: QtCore.QRect | None = None
        self._drag = None
        self.expanded = False
        self._work_view = None
        self.tasks_root = None
        self._settings = settings
        self.can_start = lambda: True
        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setWindowFlags(QtCore.Qt.WindowType.Tool
                                         | QtCore.Qt.WindowType.FramelessWindowHint
                                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.toggle_button.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.toggle_button.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.toggle_button.setWindowTitle('之之创造新技能按钮')
        self.toggle_button.setText('创造新技能')
        self.toggle_button.setFixedSize(110, 30)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolTip('展开 / 收起技能制作面板')
        self.toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.toggle_button.setStyleSheet('''
            QToolButton { color: #7453a3; background: #fffefd; border: 1px solid #d9cbea;
                          border-radius: 14px; font: 13px "Microsoft YaHei UI"; }
            QToolButton:hover, QToolButton:checked { background: #eee5ff; border-color: #b79ad8; }
        ''')
        self.toggle_button.clicked.connect(self.toggle)
        self._button_policy = WindowPolicy(self.toggle_button)
        self.destroyed.connect(self.toggle_button.deleteLater)
        self.target_window: WindowInfo | None = None
        self._exploration: CreationWorker | None = None
        self._exploration_stopping = False
        self._pending_initial_message = None
        self._initial_message_timer = QtCore.QTimer(self)
        self._initial_message_timer.setSingleShot(True)
        self._initial_message_timer.setInterval(500)
        self._initial_message_timer.timeout.connect(self._show_initial_message)
        self._saved_skill = None
        self._creation_directory = None
        self._creation_thread_id = None
        self._editing_skill = None
        self._editing_name = ''
        self._choosing_window = True
        self.setStyleSheet(TOOL_BUTTON_STYLE + SCROLLBAR_STYLE + '''
            QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #332e40; }
            QFrame#creationCard { background: #fffefd; border: 1px solid #e7e2ed; border-radius: 18px; }
            QLabel { background: transparent; }
            QLabel#muted { color: #827b8e; font-size: 12px; }
            QPushButton { border: 1px solid #e7e2ed; border-radius: 10px; background: #fffefd; padding: 9px 14px; }
            QPushButton:hover { background: #faf7fe; border-color: #c8b4e1; }
            QPushButton#primary { background: #8158b4; color: white; border: none; font-weight: 600; }
            QPushButton#primary:hover { background: #7249a6; }
            QPushButton#primary:disabled { background: #eeeaf2; color: #a49bac; }
            QFrame#targetSummary { background: #f6f4f8; border: none; border-radius: 8px; }
            QScrollArea, QStackedWidget { border: none; background: transparent; }
            QListWidget { border: none; background: transparent; outline: none; }
            QListWidget::item { padding: 6px 10px; border-radius: 8px; border: 1px solid transparent; }
            QListWidget::item:hover { background: #f8f5fb; }
            QListWidget::item:selected { background: #f1e9fa; border-color: #d8c5ed; color: #62418a; }
            QPlainTextEdit { background: #f9f7fb; border: 1px solid #e9e3f0; border-radius: 9px; padding: 8px; }
            QPlainTextEdit:focus { border-color: #b395d3; }
        ''')
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.card = QtWidgets.QFrame()
        self.card.setObjectName('creationCard')
        outer.addWidget(self.card)
        card_layout = QtWidgets.QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 16, 18, 14)
        card_layout.setSpacing(12)
        self.header = QtWidgets.QWidget()
        self.header.setToolTip('拖动空白处，与之之一起移动')
        header_layout = QtWidgets.QVBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        top_row = QtWidgets.QHBoxLayout()
        self.step_label = QtWidgets.QLabel('新技能  /  1 · 选择创建方式')
        self.step_label.setObjectName('muted')
        self.step_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.fold_button = QtWidgets.QToolButton()
        self.fold_button.setText('−')
        self.fold_button.setToolTip('收起制作面板')
        self.fold_button.setAccessibleName('收起制作面板')
        self.fold_button.clicked.connect(self.collapse)
        self.return_button = QtWidgets.QToolButton()
        self.return_button.setText('返回任务')
        self.return_button.setToolTip('退出技能创建，保留已选窗口和目标')
        self.return_button.clicked.connect(self._return_to_tasks)
        top_row.addWidget(self.step_label)
        top_row.addStretch()
        top_row.addWidget(self.return_button)
        top_row.addWidget(self.fold_button)
        header_layout.addLayout(top_row)
        self.title_label = QtWidgets.QLabel('选择窗口，或直接创建技能')
        self.title_label.setStyleSheet('font-size: 19px; font-weight: 600;')
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.title_label)
        card_layout.addWidget(self.header)

        self.pages = QtWidgets.QStackedWidget()
        card_layout.addWidget(self.pages, 1)

        self.window_page = QtWidgets.QWidget()
        window_layout = QtWidgets.QVBoxLayout(self.window_page)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.setSpacing(8)
        window_intro = QtWidgets.QHBoxLayout()
        self.window_status = QtWidgets.QLabel('选择一个已打开的应用窗口')
        self.window_status.setObjectName('muted')
        self.window_status.setWordWrap(True)
        window_intro.addWidget(self.window_status, 1)
        self.refresh_button = QtWidgets.QToolButton()
        self.refresh_button.setText('刷新')
        self.refresh_button.setToolTip('刷新窗口列表')
        self.refresh_button.clicked.connect(self._refresh_windows)
        window_intro.addWidget(self.refresh_button)
        window_layout.addLayout(window_intro)
        self.window_list = QtWidgets.QListWidget()
        self.window_list.setMinimumHeight(80)
        self.window_list.setIconSize(QtCore.QSize(26, 26))
        self.window_list.setSpacing(3)
        self.window_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.window_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.window_list.setAccessibleName('可选目标窗口')
        self.window_list.currentItemChanged.connect(
            lambda current, previous: self.select_window_button.setEnabled(current is not None))
        self.window_list.itemDoubleClicked.connect(lambda item: self._select_window())
        window_layout.addWidget(self.window_list, 1)
        self.pages.addWidget(self.window_page)

        self.window_footer = QtWidgets.QWidget()
        window_actions = QtWidgets.QHBoxLayout(self.window_footer)
        window_actions.setContentsMargins(0, 0, 0, 0)
        self.select_window_button = QtWidgets.QPushButton('下一步 →')
        self.select_window_button.setObjectName('primary')
        self.select_window_button.setEnabled(False)
        self.select_window_button.clicked.connect(self._select_window)
        self.no_window_button = QtWidgets.QPushButton('无需窗口，直接创建')
        self.no_window_button.clicked.connect(self._create_without_window)
        window_actions.addWidget(self.no_window_button)
        window_actions.addStretch()
        window_actions.addWidget(self.select_window_button)
        card_layout.addWidget(self.window_footer)
        self._drag_surfaces = (self.header, self.card, self.window_page, self.pages, self.window_footer)
        for surface in self._drag_surfaces:
            surface.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            surface.installEventFilter(self)
        for button in self.findChildren(QtWidgets.QAbstractButton):
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.resize_hint = QtWidgets.QLabel(self)
        self.resize_hint.setFixedSize(12, 12)
        self.resize_hint.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        mark = QtGui.QPixmap(24, 24)
        mark.setDevicePixelRatio(2)
        mark.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(mark)
        painter.setPen(QtGui.QPen(QtGui.QColor('#c9bfd4'), 1))
        painter.drawLine(2, 9, 9, 2)
        painter.drawLine(6, 9, 9, 6)
        painter.end()
        self.resize_hint.setPixmap(mark)
        self.conversation = ExplorationConversation()
        self.conversation.submitted.connect(self._send_message)
        self.conversation.stop_requested.connect(self._stop_exploration)
        self.conversation.tasks_requested.connect(self._return_to_tasks)
        self.conversation.dismissed.connect(self._cancel_initial_message)
        self.conversation.dismissed.connect(lambda: self.toggle_button.setText('修改技能' if self._editing_skill else '探索对话'))
        self.destroyed.connect(self.conversation.deleteLater)
        self.refresh_model_name()


    @property
    def current_work(self) -> str | None:
        return self._work_view

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, 'resize_hint'):
            self.resize_hint.move(self.width()-24, self.height()-24)
            self.resize_hint.raise_()

    def _available_windows(self) -> tuple[WindowInfo, ...]:
        own_handles = {int(widget.effectiveWinId()) for widget in QtWidgets.QApplication.topLevelWidgets()}
        api = Win32DesktopApi(configure_dpi_awareness=False)
        return tuple(window for window in api.list_windows() if window.hwnd not in own_handles)

    def _refresh_windows(self) -> None:
        previous = self.window_list.currentItem()
        previous_hwnd = previous.data(QtCore.Qt.ItemDataRole.UserRole).hwnd if previous else None
        self.window_list.clear()
        self.select_window_button.setEnabled(False)
        try:
            windows = self._available_windows()
        except DesktopWindowsError as error:
            self.window_status.setText('暂时无法读取窗口列表，请刷新重试。')
            self.window_status.setToolTip(str(error))
            return
        self.window_status.setToolTip('')
        self.window_status.setText(f'{len(windows)} 个可选窗口' if windows else '没有找到可选窗口，可直接创建；需要操作应用时，请打开应用后刷新。')
        for window in windows:
            item = QtWidgets.QListWidgetItem(_window_icon(window.hwnd), window.title)
            item.setSizeHint(QtCore.QSize(0, 52))
            item.setToolTip(f'{window.title}\n{window.width} × {window.height}')
            item.setData(QtCore.Qt.ItemDataRole.UserRole, window)
            self.window_list.addItem(item)
            if window.hwnd == previous_hwnd:
                self.window_list.setCurrentItem(item)

    def _create_without_window(self) -> None:
        if self._exploration is not None:
            return
        if self.target_window is not None or self._editing_skill is not None or self._saved_skill:
            self._reset_conversation()
        self.target_window = None
        self._editing_skill = None
        self._show_chat()

    def _select_window(self) -> None:
        if self._exploration is not None:
            return
        item = self.window_list.currentItem()
        if item is None:
            return
        selected = item.data(QtCore.Qt.ItemDataRole.UserRole)
        # A listed window may have closed while the user was choosing it.
        self._refresh_windows()
        for index in range(self.window_list.count()):
            fresh = self.window_list.item(index).data(QtCore.Qt.ItemDataRole.UserRole)
            if fresh.hwnd == selected.hwnd:
                try:
                    fresh = Win32DesktopApi(configure_dpi_awareness=False).restore(fresh)
                except DesktopWindowsError as error:
                    self.window_status.setText('无法恢复所选窗口，请手动打开后重试。')
                    self.window_status.setToolTip(str(error))
                    return
                if self.target_window is None or fresh.hwnd != self.target_window.hwnd or self._saved_skill:
                    self._reset_conversation()
                self.target_window = fresh
                self._show_chat()
                return
        if not self.window_status.toolTip():
            self.window_status.setText('这个窗口已不可用，请重新选择。')

    def _reset_conversation(self) -> None:
        if self._exploration is not None:
            return
        self._cancel_initial_message()
        self._creation_directory = None
        self._creation_thread_id = None
        self._saved_skill = None
        self.conversation.clear()

    def _show_chat(self) -> None:
        self.refresh_model_name()
        self._work_view = 'conversation'
        self.conversation.closed_by_user = False
        self.conversation.set_busy(False)
        self.conversation.set_editing(self._editing_name if self._editing_skill else '')
        self.conversation.set_target('修改 · '+self._editing_name if self._editing_skill else
                                     self.target_window.title if self.target_window else '无需窗口')
        self.collapse()
        self.conversation.set_visible(self.toggle_button.isVisible())
        self.conversation.activateWindow()
        self.conversation.input.setFocus()

    def edit_skill(self, directory: Path, *, name: str = '', prompt: str = '') -> None:
        self._require_ai_enabled()
        if self._exploration is not None or not self.can_start():
            raise RuntimeError('请先停止正在进行的任务，再修改技能。')
        directory = Path(directory).resolve()
        formal_root = Path(self.tasks_root).resolve()
        if directory.parent not in (formal_root, formal_root.parent / 'test_tasks') or not (directory / 'task.py').is_file():
            raise ValueError('这个技能没有可修改的 Python 源码。')
        if self._editing_skill != directory or self._saved_skill:
            self._reset_conversation()
            self.target_window = None
            self._editing_skill = directory
        self._editing_name = name or directory.name
        self.toggle_button.setText('修改技能')
        self._show_chat()
        if prompt:
            self._send_message(prompt)

    def _send_message(self, text: str) -> None:
        if not text.strip() or self._work_view != 'conversation' or self._exploration is not None:
            return
        if not self.can_start():
            self.conversation.add_message('assistant', '已有任务正在运行，请先停止该任务。')
            return
        try:
            self._require_ai_enabled()
            agent_tool, model_name = self._configured_model_name()
            if self._creation_directory:
                record = self._creation_directory / 'agent.json'
                draft_tool = json.loads(record.read_text(encoding='utf-8')).get('tool') if record.exists() else 'codex'
                if draft_tool != agent_tool:
                    self._creation_directory = None
                    self._creation_thread_id = None
        except Exception as exc:
            self.log_message.emit(f'读取 AI 设置失败：{exc}')
            self.refresh_model_name()
            self.ai_unavailable.emit(str(exc))
            return
        self._cancel_initial_message()
        self.conversation.add_message('user', text)
        self._work_view = 'conversation'
        if self._editing_skill and self._saved_skill:
            self._creation_directory = None
            self._creation_thread_id = None
        worker = CreationWorker(self.target_window, text, self, tasks_root=self.tasks_root,
                                directory=self._creation_directory, thread_id=self._creation_thread_id,
                                source_skill=self._editing_skill,
                                settings_path=self._settings.path)
        self._exploration = worker
        self.conversation.set_model_name(model_name)
        self._saved_skill = None
        self.exploration_active.emit(True)
        self._exploration_stopping = False
        self._pending_initial_message = (
            worker, self.conversation.entries[-1], f'收到，{model_name}正在处理')
        self.conversation.input.clear()
        self.conversation.set_busy(True)
        self.return_button.setEnabled(False)
        worker.progress.connect(self._exploration_progress)
        worker.click_point.connect(self._exploration_click)
        worker.input_request.connect(self._exploration_input_request)
        worker.input_cancel.connect(self.foreground_input_cancelled)
        worker.observed.connect(self._show_observation)
        worker.failed.connect(self._exploration_failed)
        worker.configuration_failed.connect(self._configuration_failed)
        worker.saved.connect(self._skill_saved)
        worker.log_message.connect(self.log_message)
        worker.finished.connect(self._exploration_finished)
        self.conversation.follow_pet(self._pet_rect)
        self._initial_message_timer.start()
        worker.start()

    def _configured_model_name(self) -> tuple[str, str]:
        if self._settings.get_agent_tool() == 'opencode':
            return 'opencode', self._settings.get_opencode_options()['model'] or 'OpenCode 未配置'
        return 'codex', self._settings.get_codex_options()['model']

    def refresh_model_name(self) -> None:
        if self._exploration is not None:
            return  # Keep the launch snapshot until this worker finishes.
        try:
            if self._settings is None:
                name = 'AI 未配置'
            elif not self._settings.get_ai_enabled():
                name = 'AI 已禁用'
            else:
                _, name = self._configured_model_name()
        except Exception:
            name = '模型未就绪'
        self.conversation.set_model_name(name)

    def _cancel_initial_message(self) -> None:
        self._initial_message_timer.stop()
        self._pending_initial_message = None

    def _show_initial_message(self) -> None:
        pending = self._pending_initial_message
        self._cancel_initial_message()
        if pending is None:
            return
        worker, user_entry, text = pending
        if (self._exploration is worker and not self._exploration_stopping
                and self._work_view == 'conversation' and not self.conversation.closed_by_user
                and self.conversation.entries and self.conversation.entries[-1] is user_entry):
            self.conversation.add_message('assistant', text)

    def _exploration_click(self, x: int, y: int) -> None:
        if self._exploration is not None and not self._exploration_stopping:
            self.click_point.emit(x, y)

    def _exploration_input_request(self, event, reply) -> None:
        if self._exploration is self.sender() and not self._exploration_stopping:
            self.foreground_input_requested.emit(event, reply)
        else:
            reply(InterruptedError('本轮工作已停止'))

    def _exploration_progress(self, text: str) -> None:
        if self._exploration is not None and not self._exploration_stopping:
            self._cancel_initial_message()
            self.conversation.add_message('assistant', text)

    def _show_observation(self, text: str) -> None:
        if self._exploration is None or self._exploration_stopping:
            return
        self._cancel_initial_message()
        # A published skill has a concise completion message from _skill_saved.
        # The full development report is retained in the turn's answer.txt.
        if not self._saved_skill:
            self.conversation.add_message('assistant', text)

    def _skill_saved(self, directory: str) -> None:
        self._cancel_initial_message()
        self._saved_skill = directory
        if self._editing_skill is not None:
            self._editing_skill = Path(directory).resolve()
        self.skill_saved.emit(directory)
        if self._exploration is not None and not self._exploration_stopping:
            group = '正式' if Path(directory).resolve().parent == Path(self.tasks_root).resolve() else '测试'
            self.conversation.add_message(
                'assistant', f'“{self._editing_name if self._editing_skill else Path(directory).name}”已{"更新并保存" if self._editing_skill else "保存"}到任务列表的“{group}”分组。\n返回任务列表即可试运行。',
                summary=f'已保存到“{group}”分组，可返回任务列表试运行。')

    def _return_to_tasks(self):
        if self._exploration is not None:
            return
        self._work_view = None
        self.conversation.dismiss()
        self.collapse()
        self.toggle_button.setText('创造新技能')
        self.tasks_requested.emit()

    def _exploration_failed(self, error: str) -> None:
        if self._exploration is not None and not self._exploration_stopping:
            self._cancel_initial_message()
            self.conversation.add_message('assistant', error)

    def _configuration_failed(self, error: str) -> None:
        if self._exploration is not None and not self._exploration_stopping:
            self._cancel_initial_message()
            self.conversation.add_message('assistant', error)
            if not self.conversation.input.toPlainText().strip():
                self.conversation.input.setPlainText(self._exploration.goal)

    def _stop_exploration(self) -> None:
        self._cancel_initial_message()
        if self._exploration is not None and not self._exploration_stopping:
            self._exploration_stopping = True
            self.foreground_input_stop_requested.emit()
            self._exploration.requestInterruption()
            self.conversation.stop_button.setEnabled(False)
            self.conversation.add_message('assistant', '正在停止……')

    def _exploration_finished(self) -> None:
        self._cancel_initial_message()
        worker = self._exploration
        if worker is None:
            return
        if worker.stop_error:
            self.conversation.add_message('assistant', worker.stop_error)
        elif self._exploration_stopping:
            self.conversation.add_message('assistant',
                                          '已停止，保存的技能可在任务列表试运行。' if self._saved_skill
                                          else ('已停止修改，原技能未替换。可以补充要求后继续。' if self._editing_skill else '已停止创建。可以补充目标后继续。'))
        self._creation_directory = worker.directory
        self._creation_thread_id = worker.thread_id
        if (not worker.thread_id and worker.directory and any(worker.directory.glob('turn_*'))
                and not (worker.directory / 'creation_flow.json').exists()):
            # A failed launch cannot be resumed; keep its files but use a fresh draft next time.
            self._creation_directory = None
        self._exploration = None
        worker.deleteLater()
        self.return_button.setEnabled(True)
        self.conversation.set_busy(False)
        self.exploration_active.emit(False)
        self.refresh_model_name()

    def stop_exploration_and_wait(self) -> None:
        self._cancel_initial_message()
        if self._exploration is not None:
            self._exploration_stopping = True
            self.foreground_input_stop_requested.emit()
            self._exploration.requestInterruption()
            self._exploration.wait()

    def leave_ai_work(self) -> None:
        """Release the pet's click routing, retaining drafts, conversation and input."""
        if self._exploration is not None:
            self._stop_exploration()
        self._work_view = None
        self.conversation.dismiss()
        self.collapse()
        self.toggle_button.setText('创造新技能')
        self.hide_for_pet()

    def open_for_pet(self, pet_rect: QtCore.QRect) -> None:
        self.refresh_model_name()
        self.follow_pet(pet_rect)
        self.toggle_button.show()
        self._button_policy.set_input_passthrough(False, layered=True, no_activate=True)
        self.conversation.set_visible(not self.expanded and self.current_work == 'conversation')
        if self.expanded:
            self.show()
            self._window_policy.set_input_passthrough(False, layered=True, no_activate=False)

    def hide_for_pet(self) -> None:
        self._cancel_initial_message()
        self._finish_drag()
        self.hide()
        self.toggle_button.hide()
        self.conversation.set_visible(False)

    def toggle(self) -> None:
        if self.current_work == 'conversation' and self.conversation.closed_by_user:
            self.toggle_current_work()
            return
        if self.expanded:
            self.collapse()
        else:
            self.expand()

    def toggle_current_work(self) -> None:
        if self.current_work == 'conversation':
            if self.conversation.closed_by_user:
                self.conversation.restore()
                self.toggle_button.setText('修改技能' if self._editing_skill else '创造新技能')
            else:
                self.conversation.dismiss()
        else:
            self.toggle()

    def expand(self) -> None:
        try:
            self._require_ai_enabled()
        except SettingsError as error:
            self.toggle_button.setChecked(False)
            self.ai_unavailable.emit(str(error))
            return
        if self._exploration is not None:
            self._work_view = 'conversation'
            self.conversation.restore()
            return
        if self.expanded:
            return
        if self._editing_skill is not None:
            self._reset_conversation()
            self._editing_skill = None
            self.target_window = None
            self.conversation.set_editing('')
        self._work_view = 'creation'
        self.toggle_button.setText('创造新技能')
        self.expanded = True
        self.conversation.set_visible(False)
        self.toggle_button.setChecked(True)
        self.expanded_changed.emit(True)
        if self._pet_rect is not None:
            self.follow_pet(self._pet_rect)
        self.show()
        self._window_policy.set_input_passthrough(False, layered=True, no_activate=False)
        self.raise_()
        self.activateWindow()
        if self._choosing_window:
            self._refresh_windows()
            self.window_list.setFocus()

    def _require_ai_enabled(self) -> None:
        if self._settings is None:
            raise SettingsError('请到“设置 → AI 设置”完成配置并启用 AI。')
        self._settings.require_ai_enabled()

    def collapse(self) -> None:
        if not self.expanded:
            return
        self._finish_drag()
        self.expanded = False
        self.toggle_button.setChecked(False)
        self.hide()
        self.conversation.set_visible(self.toggle_button.isVisible() and self.current_work == 'conversation')
        self.expanded_changed.emit(False)

    def follow_pet(self, pet_rect: QtCore.QRect) -> None:
        self._pet_rect = QtCore.QRect(pet_rect)
        self.conversation.follow_pet(pet_rect)
        screen = QtGui.QGuiApplication.screenAt(pet_rect.center()) or self.screen()
        area = screen.availableGeometry()
        button_x = pet_rect.center().x() - self.toggle_button.width() // 2
        button_y = pet_rect.top() - self.toggle_button.height() - 8
        self.toggle_button.move(
            min(max(button_x, area.left()), area.right() - self.toggle_button.width() + 1),
            min(max(button_y, area.top()), area.bottom() - self.toggle_button.height() + 1))
        x = pet_rect.left() - self.width() - 12
        if x < area.left():
            x = pet_rect.right() + 12
        pos = QtCore.QPoint(x, pet_rect.center().y() - self.height() + 35)
        self.move(min(max(pos.x(), area.left()), max(area.left(), area.right() - self.width() + 1)),
                  min(max(pos.y(), area.top()), max(area.top(), area.bottom() - self.height() + 1)))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        # Zero-alpha pixels pass through on Windows layered windows. Alpha 1 keeps
        # the gaps visually transparent while retaining a solid mouse hit area.
        painter.fillRect(self.rect(), QtGui.QColor(255, 255, 255, 1))
    def _edges_at(self, pos: QtCore.QPoint) -> QtCore.Qt.Edge:
        edges = QtCore.Qt.Edge(0)
        if not self.expanded:
            return edges
        if pos.x() >= self.width()-24 and pos.y() >= self.height()-24:
            return QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.BottomEdge
        if pos.x() < 8:
            edges |= QtCore.Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - 8:
            edges |= QtCore.Qt.Edge.RightEdge
        if pos.y() < 8:
            edges |= QtCore.Qt.Edge.TopEdge
        elif pos.y() >= self.height() - 8:
            edges |= QtCore.Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        edges = self._edges_at(event.position().toPoint())
        if (event.button() == QtCore.Qt.MouseButton.LeftButton
                and (edges or self._is_drag_surface(event.globalPosition().toPoint()))):
            self._begin_drag(event, edges)
        else:
            super().mousePressEvent(event)

    def _begin_drag(self, event: QtGui.QMouseEvent, edges: QtCore.Qt.Edge) -> None:
        self._drag = (event.globalPosition().toPoint(), self.geometry(), edges)
        if not edges:
            self.drag_started.emit(event.globalPosition().toPoint())
        event.accept()

    def _is_drag_surface(self, global_position: QtCore.QPoint) -> bool:
        target = self.childAt(self.mapFromGlobal(global_position))
        return target is None or target in self._drag_surfaces

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag is not None:
            origin, rect, edges = self._drag
            delta = event.globalPosition().toPoint() - origin
            target = QtCore.QRect(rect)
            if not edges:
                self.drag_moved.emit(event.globalPosition().toPoint())
                event.accept()
                return
            if edges & QtCore.Qt.Edge.LeftEdge:
                target.setLeft(min(rect.left() + delta.x(), rect.right() - self.minimumWidth() + 1))
            if edges & QtCore.Qt.Edge.RightEdge:
                target.setRight(max(rect.right() + delta.x(), rect.left() + self.minimumWidth() - 1))
            if edges & QtCore.Qt.Edge.TopEdge:
                target.setTop(min(rect.top() + delta.y(), rect.bottom() - self.minimumHeight() + 1))
            if edges & QtCore.Qt.Edge.BottomEdge:
                target.setBottom(max(rect.bottom() + delta.y(), rect.top() + self.minimumHeight() - 1))
            self.setGeometry(target)
            event.accept()
            return
        edges = self._edges_at(event.position().toPoint())
        E, C = QtCore.Qt.Edge, QtCore.Qt.CursorShape
        cursor = C.OpenHandCursor
        if edges in (E.TopEdge | E.LeftEdge, E.BottomEdge | E.RightEdge):
            cursor = C.SizeFDiagCursor
        elif edges in (E.TopEdge | E.RightEdge, E.BottomEdge | E.LeftEdge):
            cursor = C.SizeBDiagCursor
        elif edges & (E.LeftEdge | E.RightEdge):
            cursor = C.SizeHorCursor
        elif edges:
            cursor = C.SizeVerCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag is not None:
            self._finish_drag()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _finish_drag(self) -> None:
        if self._drag is None:
            return
        edges = self._drag[2]
        self._drag = None
        if not edges:
            self.drag_finished.emit()
        if self._pet_rect is not None:
            self.follow_pet(self._pet_rect)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched in self._drag_surfaces:
            if (event.type() == QtCore.QEvent.Type.MouseButtonPress
                    and event.button() == QtCore.Qt.MouseButton.LeftButton
                    and self._is_drag_surface(event.globalPosition().toPoint())):
                self._begin_drag(event, self._edges_at(self.mapFromGlobal(event.globalPosition().toPoint())))
                return True
            if event.type() == QtCore.QEvent.Type.MouseMove and self._drag is not None:
                self.mouseMoveEvent(event)
                return True
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                self.mouseReleaseEvent(event)
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.collapse()
        event.ignore()
