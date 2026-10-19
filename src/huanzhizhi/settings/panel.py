"""Desktop-pet settings panel."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets
from huanzhizhi.widgets import BUTTON_STYLE, ComboBox, ToggleSwitch

from huanzhizhi.pet import PetComponent
from huanzhizhi.pet.context_menu import display_scale_label
from .component import CODEX_REASONING_EFFORTS, DEFAULT_CODEX_OPTIONS, SettingsComponent
from .environment import EnvironmentPage
from huanzhizhi.agents.codex import adapter as ai_codex
from huanzhizhi.agents.opencode.guide import OpenCodeGuide
from huanzhizhi.agents.runtime import SettingsError

if TYPE_CHECKING:
    from huanzhizhi.tasks.component import TaskHostComponent


class _SettingsWindow(QtWidgets.QWidget):
    closed = QtCore.Signal()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.closed.emit()
        super().closeEvent(event)


def desktop_pet_settings_style() -> str:
    return BUTTON_STYLE + """
QWidget#desktopPetSettingsWindow { background: #ffffff; color: #242424; }
QWidget#desktopPetSettingsSidebar { background: #f8f8f8; border-right: 1px solid #ededed; }
QLabel#desktopPetSettingsBrand { font-size: 15px; font-weight: 600; padding: 18px 18px 12px 18px; }
QLabel#desktopPetSettingsSection { color: #777777; font-size: 12px; padding: 8px 18px 4px 18px; }
QListWidget#desktopPetSettingsNavigation { border: none; background: transparent; outline: none; padding: 0 8px; }
QListWidget#desktopPetSettingsNavigation::item { border-radius: 9px; padding: 10px 12px; margin: 2px 0; }
QListWidget#desktopPetSettingsNavigation::item:selected { background: #e9e9e9; color: #202020; font-weight: 600; }
QTreeWidget#desktopPetEnvironmentResults { selection-background-color: #e9e9e9; selection-color: #242424; outline: none; }
QTreeWidget#desktopPetEnvironmentResults::item:selected { background: #e9e9e9; color: #242424; border: none; }
QWidget#desktopPetSettingsContent { background: #ffffff; }
QLabel#desktopPetSettingsTitle { font-size: 28px; font-weight: 600; }
QLabel#desktopPetSettingsGroupTitle { font-size: 16px; font-weight: 600; margin-top: 12px; }
QFrame#desktopPetSettingsCard { background: #ffffff; border: 1px solid #e9e9e9; border-radius: 16px; }
QListWidget#desktopPetTaskCards { background: #ffffff; border: none; outline: none; }
QListWidget#desktopPetTaskCards::item { background: transparent; border: none; }
QFrame#desktopPetTaskCard { background: #ffffff; border: 1px solid #e9e9e9; border-radius: 4px; }
QFrame#desktopPetTaskCard:hover { border-color: #b7cdf0; }
QLabel#desktopPetTaskPageTitle { font-size: 20px; font-weight: 600; }
QLabel#desktopPetTaskTitle { font-size: 13px; font-weight: 600; }
QTabBar#desktopPetTaskGroups::tab { color: #757575; background: transparent; padding: 5px 14px; border-bottom: 2px solid transparent; font-size: 12px; }
QTabBar#desktopPetTaskGroups::tab:selected { color: #397df6; border-bottom-color: #397df6; }
QToolButton#desktopPetTaskInfo { color: #8993a3; border: none; background: transparent; font-size: 14px; }
QToolButton#desktopPetTaskEdit { color: #397df6; border: none; background: transparent; font-size: 12px; }
QToolButton#desktopPetTaskInfo:hover, QToolButton#desktopPetTaskEdit:hover { background: #eef4ff; }
QToolButton#desktopPetTaskInfo:focus, QToolButton#desktopPetTaskEdit:focus { border: 1px solid #8ab4f8; }
QLabel#desktopPetSettingsRowTitle { font-size: 14px; font-weight: 600; }
QLabel#desktopPetSettingsRowDetail { color: #757575; font-size: 12px; }
QFrame#desktopPetSettingsDivider { background: #eeeeee; max-height: 1px; border: none; }
"""


class DesktopPetSettingsComponent(QtCore.QObject):
    """Displays pet preferences and the current host run log."""

    ai_enabled_changed = QtCore.Signal(bool)
    ai_options_changed = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(
        self,
        pet: PetComponent,
        log_path: Path,
        parent: QtCore.QObject | None = None,
        *,
        tasks: TaskHostComponent | None = None,
        settings: SettingsComponent | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(pet, PetComponent):
            raise TypeError("pet 必须是 PetComponent")
        if not isinstance(log_path, Path):
            raise TypeError("log_path 必须是 Path")
        self._pet = pet
        self._tasks = tasks
        self._settings = settings
        self._log_path = log_path
        self._closed = False
        self._window = _SettingsWindow()
        self._window.setObjectName("desktopPetSettingsWindow")
        self._window.setWindowTitle("幻之之设置")
        self._window.resize(1040, 540)
        self._window.setMinimumSize(640, 440)
        self._window.setStyleSheet(desktop_pet_settings_style())
        self._build_ui()
        if self._tasks is not None:
            self._tasks.tasks_changed.connect(self._sync_tasks)
            self._tasks.process.state_changed.connect(self._sync_input_options)
        self._pet.scale_changed.connect(self._sync_scale)
        self._pet.mouse_point_visible_changed.connect(self._sync_mouse_point_visible)
        self._pet.theme_changed.connect(self._sync_input_options)
        self._sync_input_options()

    @property
    def window(self) -> QtWidgets.QWidget:
        return self._window

    def show(self) -> None:
        self._ensure_open()
        self._sync_from_pet()
        self._sync_codex_options()
        self._sync_ai_enabled()
        if self._tasks is not None:
            try:
                if self._tasks.catalog.signature() != self._tasks.catalog.loaded_signature:
                    self._tasks.reload_tasks()
                self._sync_tasks()
            except Exception as exc:
                self.error.emit(f"读取任务列表失败：{exc}")
        self._load_log()
        self._window.show()
        if self._window.isMinimized():
            self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def show_ai_settings(self) -> None:
        self._navigation.setCurrentRow(3)
        self.show()

    def close(self) -> None:
        if not self._closed:
            self._opencode_guide.stop()
            self._environment_page.stop()
            # Child destruction may follow collection of this controller's attributes.
            for watched in self._window.findChildren(QtCore.QObject):
                watched.removeEventFilter(self)
            self._window.close()
            self._closed = True

    def _build_ui(self) -> None:
        root = QtWidgets.QHBoxLayout(self._window)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("desktopPetSettingsSidebar")
        sidebar.setFixedWidth(184)
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 16)
        brand = QtWidgets.QLabel("幻之之设置")
        brand.setObjectName("desktopPetSettingsBrand")
        section = QtWidgets.QLabel("桌宠")
        section.setObjectName("desktopPetSettingsSection")
        self._navigation = QtWidgets.QListWidget()
        self._navigation.setObjectName("desktopPetSettingsNavigation")
        self._navigation.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._navigation.addItem("常规")
        self._navigation.addItem("运行日志")
        self._navigation.addItem("任务列表")
        self._navigation.addItem("AI 设置")
        self._navigation.addItem("外部组件")
        self._navigation.setCurrentRow(0)
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(section)
        sidebar_layout.addWidget(self._navigation, 1)
        root.addWidget(sidebar)

        content = QtWidgets.QWidget()
        content.setObjectName("desktopPetSettingsContent")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(42, 32, 42, 36)
        self._pages = QtWidgets.QStackedWidget()
        self._pages.addWidget(self._build_general_page())
        self._pages.addWidget(self._build_log_page())
        self._pages.addWidget(self._build_tasks_page())
        self._pages.addWidget(self._build_ai_page())
        self._environment_page = EnvironmentPage()
        self._pages.addWidget(self._environment_page)
        self._window.closed.connect(self._environment_page.stop)
        content_layout.addWidget(self._pages)
        root.addWidget(content, 1)
        self._navigation.currentRowChanged.connect(self._show_page)

    def _build_general_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("desktopPetSettingsContent")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._title = QtWidgets.QLabel("常规")
        self._title.setObjectName("desktopPetSettingsTitle")
        group_title = QtWidgets.QLabel("桌宠行为")
        group_title.setObjectName("desktopPetSettingsGroupTitle")
        layout.addWidget(self._title)
        layout.addWidget(group_title)
        layout.addWidget(self._build_general_card())
        layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll


    def _build_ai_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName('desktopPetSettingsContent')
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QtWidgets.QLabel('AI 设置')
        title.setObjectName('desktopPetSettingsTitle')
        layout.addWidget(title)
        card = QtWidgets.QFrame()
        card.setObjectName('desktopPetSettingsCard')
        rows = QtWidgets.QVBoxLayout(card)
        self._ai_switch = ToggleSwitch('启用 AI')
        rows.addLayout(self._row('启用 AI', '关闭后仍可运行已有技能。', self._ai_switch))
        permissions = QtWidgets.QLabel(
            'AI 会以你的用户权限执行命令、读写文件，也可能操作鼠标和键盘。'
            '草稿目录仅通过指令约定，不是强制文件沙箱。'
            'Codex 使用完整权限且不逐项请求批准；OpenCode 使用自动模式，仍遵守其显式拒绝规则。'
            '请在了解这些权限后启用 AI。')
        permissions.setObjectName('desktopPetSettingsRowDetail')
        permissions.setWordWrap(True)
        rows.addWidget(permissions)
        rows.addWidget(self._divider())
        self._agent_combo = ComboBox()
        self._agent_combo.setAccessibleName('AI 工具')
        self._agent_combo.addItem('Codex', 'codex')
        self._agent_combo.addItem('OpenCode（免费体验）', 'opencode')
        rows.addLayout(self._row('AI 工具', '切换工具后需重新启用。', self._agent_combo))
        rows.addWidget(self._divider())
        self._codex_fields = QtWidgets.QWidget()
        codex_rows = QtWidgets.QVBoxLayout(self._codex_fields)
        codex_rows.setContentsMargins(0, 0, 0, 0)
        self._model_combo = ComboBox()
        self._model_combo.setObjectName('desktopPetSettingsCombo')
        self._model_combo.setAccessibleName('Codex 模型')
        self._reasoning_combo = ComboBox()
        self._reasoning_combo.setObjectName('desktopPetSettingsCombo')
        self._reasoning_combo.setAccessibleName('推理强度')
        codex_rows.addLayout(self._row('模型', '从 Codex 本机模型目录选择。', self._model_combo))
        codex_rows.addWidget(self._divider())
        codex_rows.addLayout(self._row('推理强度', '修改后自动保存。', self._reasoning_combo))
        rows.addWidget(self._codex_fields)
        self._opencode_guide = OpenCodeGuide(self._settings)
        self._opencode_guide.layout().insertLayout(0, self._row(
            '模型', '可直接体验的免费模型。', self._opencode_guide.model_combo))
        rows.addWidget(self._opencode_guide)
        layout.addWidget(card)
        self._ai_status = QtWidgets.QLabel()
        self._ai_status.setObjectName('desktopPetSettingsRowDetail')
        self._ai_status.setWordWrap(True)
        layout.addWidget(self._ai_status)
        layout.addStretch(1)
        self._sync_codex_options()
        self._sync_ai_enabled()
        card.setEnabled(self._settings is not None)
        self._ai_switch.toggled.connect(self._set_ai_enabled)
        self._model_combo.activated.connect(self._set_codex_model)
        self._reasoning_combo.currentIndexChanged.connect(self._save_codex_options)
        self._agent_combo.currentIndexChanged.connect(self._set_agent_tool)
        self._opencode_guide.changed.connect(self._opencode_changed)
        self._opencode_guide.busy_changed.connect(self._sync_ai_enabled)
        self._window.closed.connect(self._opencode_guide.cancel)
        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def refresh_ai_availability(self, *_args) -> None:
        self._sync_ai_enabled()
        self._sync_input_options()

    def _sync_ai_enabled(self) -> None:
        enabled = self._settings.get_ai_enabled() if self._settings else False
        busy = self._settings.ai_work_active() if self._settings else False
        busy = busy or self._opencode_guide.worker is not None
        tool = self._settings.get_agent_tool() if self._settings else 'codex'
        self._agent_combo.blockSignals(True)
        self._agent_combo.setCurrentIndex(self._agent_combo.findData(tool))
        self._agent_combo.blockSignals(False)
        self._agent_combo.setEnabled(self._settings is not None and not busy)
        self._codex_fields.setVisible(tool == 'codex')
        self._opencode_guide.setVisible(tool == 'opencode')
        self._opencode_guide.update_controls()
        reason = 'AI 正在工作，请先在对话中停止，结束后可修改设置。'
        if self._opencode_guide.worker is not None:
            reason = '正在准备 AI，请稍候。'
        for control in (self._ai_switch, self._model_combo, self._reasoning_combo):
            control.setEnabled(self._settings is not None and not busy)
        self._ai_switch.setToolTip(reason if busy else '')
        self._ai_switch.blockSignals(True)
        self._ai_switch.setChecked(enabled)
        self._ai_switch.blockSignals(False)
        self._ai_switch._thumb_animation.stop()
        self._ai_switch.thumbPosition = 1.0 if enabled else 0.0
        self._ai_status.setText(reason if busy else
            'AI 已启用。' if enabled else
            'Codex 需自行安装并登录，费用由对应账户承担。')
        if tool == 'opencode' and not busy:
            self._ai_status.setText('AI 已启用。' if enabled else '免费额度与可用性由服务商决定。')

    def _set_agent_tool(self):
        if not self._settings:
            return
        try:
            self._settings.set_agent_tool(self._agent_combo.currentData())
            self._settings.sync()
            self._opencode_changed()
        except Exception as error:
            self._sync_ai_enabled()
            self._ai_status.setText(str(error))

    def _opencode_changed(self):
        self._sync_ai_enabled()
        self.ai_enabled_changed.emit(self._settings.get_ai_enabled())
        self.ai_options_changed.emit()

    def _set_ai_enabled(self, enabled: bool) -> None:
        if self._settings is None:
            return
        try:
            if enabled and self._settings.get_agent_tool() == 'opencode' and not self._settings.is_opencode_verified():
                self._opencode_guide.enable()
                self._sync_ai_enabled()
                return
            self._settings.set_ai_enabled(enabled)
            self._settings.sync()
        except Exception as exc:
            self._sync_ai_enabled()
            self._ai_status.setText(f'无法启用 AI：{exc}' if enabled else f'无法关闭 AI：{exc}')
            return
        self._sync_ai_enabled()
        self.ai_enabled_changed.emit(enabled)
        self.ai_options_changed.emit()

    def _sync_codex_options(self) -> None:
        self._model_efforts = ai_codex.model_catalog()
        options = self._settings.get_codex_options() if self._settings else DEFAULT_CODEX_OPTIONS
        model = options['model']
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for key in self._model_efforts:
            self._model_combo.addItem(key, key)
        if self._model_combo.findData(model) < 0:
            self._model_combo.addItem(model, model)
        self._model_combo.setCurrentIndex(self._model_combo.findData(model))
        self._model_combo.blockSignals(False)
        self._sync_reasoning_options(options['reasoning_effort'], preserve=True)

    def _sync_reasoning_options(self, effort: str, *, preserve: bool = False) -> None:
        efforts = self._model_efforts.get(self._model_combo.currentData(), CODEX_REASONING_EFFORTS)
        if preserve and effort and effort not in efforts:
            efforts = (*efforts, effort)
        self._reasoning_combo.blockSignals(True)
        self._reasoning_combo.clear()
        self._reasoning_combo.addItems(list(efforts))
        self._reasoning_combo.setCurrentText(effort if effort in efforts else ('high' if 'high' in efforts else efforts[0] if efforts else ''))
        self._reasoning_combo.blockSignals(False)

    def _set_codex_model(self) -> None:
        self._sync_reasoning_options(self._reasoning_combo.currentText())
        self._save_codex_options()

    def _save_codex_options(self) -> None:
        if self._settings is None:
            return
        try:
            if self._settings.ai_work_active():
                raise SettingsError('AI 正在工作，请先停止后修改设置。')
            self._settings.set_codex_options({'model': self._model_combo.currentData(),
                                             'reasoning_effort': self._reasoning_combo.currentText()})
            self._settings.sync()
            self._sync_ai_enabled()
            self.ai_options_changed.emit()
        except Exception as exc:
            self._sync_codex_options()
            self._ai_status.setText(f'保存 AI 设置失败：{exc}')

    def _build_log_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("运行日志")
        title.setObjectName("desktopPetSettingsTitle")
        detail = QtWidgets.QLabel(f"当前文件：{self._log_path.name}")
        detail.setObjectName("desktopPetSettingsRowDetail")
        self._log_view = QtWidgets.QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setPlaceholderText("当前还没有运行日志。")
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(self._log_view, 1)
        return page

    def _build_tasks_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QtWidgets.QLabel("任务列表")
        title.setObjectName("desktopPetTaskPageTitle")
        detail = QtWidgets.QLabel("选择点击桌宠时显示的任务。修改后自动保存，不影响正在运行的任务。")
        detail.setObjectName("desktopPetSettingsRowDetail")
        detail.setWordWrap(True)
        self._task_groups = QtWidgets.QTabBar()
        self._task_groups.setObjectName("desktopPetTaskGroups")
        self._task_groups.setAccessibleName("任务分组")
        self._task_groups.setExpanding(False)
        self._task_groups.addTab("正式")
        self._task_groups.setTabData(0, "formal")
        self._task_groups.addTab("测试")
        self._task_groups.setTabData(1, "test")
        self._task_cards = QtWidgets.QListWidget()
        self._task_cards.setObjectName("desktopPetTaskCards")
        self._task_cards.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self._task_cards.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self._task_cards.setMovement(QtWidgets.QListView.Movement.Static)
        self._task_cards.setWrapping(True)
        self._task_cards.setSpacing(0)
        self._task_cards.setGridSize(QtCore.QSize(360, 48))
        self._task_cards.viewport().installEventFilter(self)
        self._task_cards.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self._task_cards.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._task_cards.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._task_empty_label = QtWidgets.QLabel(self._task_cards.viewport())
        self._task_empty_label.setObjectName("desktopPetSettingsRowDetail")
        self._task_empty_label.move(8, 10)
        self._task_descriptors = None
        self._task_items: dict[str, QtWidgets.QListWidgetItem] = {}
        self._task_switches: dict[str, ToggleSwitch] = {}
        self._task_log_switches: dict[str, ToggleSwitch] = {}
        self._task_titles: dict[str, QtWidgets.QLabel] = {}
        self._task_details: dict[str, QtWidgets.QToolButton] = {}
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(self._task_groups)
        layout.addWidget(self._task_cards, 1)
        self._task_groups.currentChanged.connect(self._filter_task_group)
        self._sync_tasks()
        return page

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self._closed:
            return super().eventFilter(watched, event)
        if watched is self._task_cards.viewport():
            if event.type() == QtCore.QEvent.Type.Resize:
                QtCore.QTimer.singleShot(0, self._layout_task_cards)
        elif event.type() == QtCore.QEvent.Type.Resize and isinstance(watched, QtWidgets.QLabel):
            watched.setText(watched.fontMetrics().elidedText(
                watched.accessibleName(), QtCore.Qt.TextElideMode.ElideRight, watched.width()
            ))
        elif isinstance(watched, QtWidgets.QToolButton) and watched.objectName() == "desktopPetTaskInfo":
            if event.type() == QtCore.QEvent.Type.Enter:
                QtWidgets.QToolTip.showText(
                    watched.mapToGlobal(QtCore.QPoint(0, watched.height())), watched.toolTip(), watched
                )
            elif event.type() == QtCore.QEvent.Type.Leave:
                QtWidgets.QToolTip.hideText()
        return super().eventFilter(watched, event)

    def _layout_task_cards(self) -> None:
        if self._closed:
            return
        # IconMode reserves scrollbar space when deciding where to wrap, even
        # before the vertical scrollbar is visible.
        scrollbar_width = self._task_cards.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
        width = max(1, self._task_cards.maximumViewportSize().width() - scrollbar_width - 2)
        columns = max(1, width // 360)
        cell_width = width // columns
        self._task_cards.setGridSize(QtCore.QSize(cell_width, 48))
        for index in range(self._task_cards.count()):
            self._task_cards.item(index).setSizeHint(QtCore.QSize(max(1, cell_width - 8), 42))
        self._task_cards.doItemsLayout()

    @QtCore.Slot()
    def _filter_task_group(self) -> None:
        if self._closed:
            return
        selected_group = self._task_groups.tabData(self._task_groups.currentIndex())
        visible_count = 0
        for item in self._task_items.values():
            hidden = item.data(QtCore.Qt.ItemDataRole.UserRole) != selected_group
            if item.isHidden() != hidden:
                item.setHidden(hidden)
            if not hidden:
                visible_count += 1
        self._task_empty_label.setText("暂无正式任务。" if selected_group == "formal" else "暂无测试任务。")
        self._task_empty_label.adjustSize()
        self._task_empty_label.setVisible(visible_count == 0)
        self._task_cards.doItemsLayout()

    @QtCore.Slot()
    def _sync_tasks(self) -> None:
        if self._closed:
            return
        descriptors = self._tasks.catalog.tasks() if self._tasks is not None else ()
        preferences_by_id = {
            descriptor.key: self._tasks.get_task_preferences(descriptor.key) for descriptor in descriptors
        }
        for index, label in enumerate(("正式", "测试")):
            group = self._task_groups.tabData(index)
            count = sum(p.get("group", "formal") == group for p in preferences_by_id.values())
            self._task_groups.setTabText(index, f"{label}（{count}）")
        if descriptors == self._task_descriptors:
            for task_id, switch in self._task_switches.items():
                visible = self._tasks.is_task_visible(task_id)
                if switch.isChecked() != visible:
                    switch.blockSignals(True)
                    switch.setChecked(visible)
                    switch.thumbPosition = float(visible)
                    switch.blockSignals(False)
                preferences = preferences_by_id[task_id]
                self._task_items[task_id].setData(QtCore.Qt.ItemDataRole.UserRole, preferences.get("group", "formal"))
                title = self._task_titles[task_id]
                title.setAccessibleName(preferences["name"])
                title.setText(title.fontMetrics().elidedText(
                    preferences["name"], QtCore.Qt.TextElideMode.ElideRight, title.width()
                ))
                detail = self._task_details[task_id]
                description = escape(preferences["description"] or "暂无任务说明。").replace("\n", "<br>")
                detail.setToolTip(f"<qt>{description}</qt>")
                detail.setAccessibleName(f"{preferences['name']}的任务说明")
                detail.setAccessibleDescription(preferences["description"] or "暂无任务说明。")
                title.parentWidget().findChild(QtWidgets.QToolButton, "desktopPetTaskEdit").setAccessibleName(
                    f"编辑{preferences['name']}"
                )
                switch.setAccessibleName(f"显示{preferences['name']}")
                log_switch = self._task_log_switches[task_id]
                log_switch.setAccessibleName(f"{preferences['name']}运行时显示日志")
                if log_switch.isChecked() != preferences["show_logs"]:
                    log_switch.blockSignals(True)
                    log_switch.setChecked(preferences["show_logs"])
                    log_switch.thumbPosition = float(preferences["show_logs"])
                    log_switch.blockSignals(False)
            self._filter_task_group()
            return
        self._task_descriptors = descriptors
        self._task_cards.clear()
        self._task_items = {}
        self._task_switches = {}
        self._task_log_switches = {}
        self._task_titles = {}
        self._task_details = {}
        for descriptor in descriptors:
            preferences = preferences_by_id[descriptor.key]
            card = QtWidgets.QFrame()
            card.setObjectName("desktopPetTaskCard")
            card_layout = QtWidgets.QHBoxLayout(card)
            card_layout.setContentsMargins(10, 4, 10, 4)
            card_layout.setSpacing(6)
            title = QtWidgets.QLabel(preferences["name"])
            title.setObjectName("desktopPetTaskTitle")
            title.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            title.setAccessibleName(preferences["name"])
            title.setMinimumWidth(0)
            title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            title.installEventFilter(self)
            detail = QtWidgets.QToolButton()
            detail.setObjectName("desktopPetTaskInfo")
            detail.installEventFilter(self)
            detail.setIcon(self._window.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation))
            detail.setIconSize(QtCore.QSize(14, 14))
            detail.setFixedSize(18, 24)
            description = escape(preferences["description"] or "暂无任务说明。").replace("\n", "<br>")
            detail.setToolTip(f"<qt>{description}</qt>")
            detail.setAccessibleName(f"{preferences['name']}的任务说明")
            detail.setAccessibleDescription(preferences["description"] or "暂无任务说明。")
            detail.clicked.connect(
                lambda _checked=False, button=detail: QtWidgets.QToolTip.showText(
                    button.mapToGlobal(QtCore.QPoint(0, button.height())), button.toolTip(), button
                )
            )
            card_layout.addWidget(title, 1)
            card_layout.addWidget(detail)
            edit = QtWidgets.QToolButton()
            edit.setObjectName("desktopPetTaskEdit")
            edit.setText("编辑")
            edit.setAutoRaise(True)
            edit.setFixedSize(32, 24)
            edit.setAccessibleName(f"编辑{preferences['name']}")
            edit.clicked.connect(
                lambda _checked=False, task_id=descriptor.key: self._edit_task(task_id)
            )
            switch = self._switch(f"显示{preferences['name']}")
            switch.setFixedSize(30, 22)
            switch.setToolTip("在点击桌宠时显示此任务")
            visible = self._tasks.is_task_visible(descriptor.key)
            switch.blockSignals(True)
            switch.setChecked(visible)
            switch.thumbPosition = float(visible)
            switch.blockSignals(False)
            switch.toggled.connect(
                lambda checked, task_id=descriptor.key: self._set_task_visible(task_id, checked)
            )
            label = QtWidgets.QLabel("显示")
            label.setObjectName("desktopPetSettingsRowDetail")
            card_layout.addWidget(label)
            card_layout.addWidget(switch)
            log_switch = self._switch(f"{preferences['name']}运行时显示日志")
            log_switch.setFixedSize(30, 22)
            log_switch.blockSignals(True)
            log_switch.setChecked(preferences["show_logs"])
            log_switch.thumbPosition = float(preferences["show_logs"])
            log_switch.blockSignals(False)
            log_switch.setToolTip("关闭后，运行气泡只显示“运行中”；运行日志仍会保存。")
            log_switch.toggled.connect(
                lambda checked, task_id=descriptor.key: self._set_task_logs_visible(task_id, checked)
            )
            log_label = QtWidgets.QLabel("日志")
            log_label.setObjectName("desktopPetSettingsRowDetail")
            card_layout.addWidget(log_label)
            card_layout.addWidget(log_switch)
            card_layout.addSpacing(4)
            card_layout.addWidget(edit)
            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(352, 42))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, preferences.get("group", "formal"))
            self._task_cards.addItem(item)
            self._task_cards.setItemWidget(item, card)
            self._task_items[descriptor.key] = item
            self._task_switches[descriptor.key] = switch
            self._task_log_switches[descriptor.key] = log_switch
            self._task_titles[descriptor.key] = title
            self._task_details[descriptor.key] = detail
        self._filter_task_group()
        self._layout_task_cards()

    def _set_task_logs_visible(self, task_id: str, visible: bool) -> None:
        if self._tasks is None:
            return
        try:
            preferences = self._tasks.get_task_preferences(task_id)
            preferences["show_logs"] = visible
            self._tasks.set_task_preferences(task_id, **preferences)
        except Exception as exc:
            self._sync_tasks()
            self.error.emit(f"更新任务日志显示设置失败：{exc}")

    def _edit_task(self, task_id: str) -> None:
        if self._tasks is None:
            return
        preferences = self._tasks.get_task_preferences(task_id)
        dialog = QtWidgets.QDialog(self._window)
        dialog.setWindowTitle("编辑任务")
        dialog.resize(420, 320)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("任务名称"))
        name = QtWidgets.QLineEdit(preferences["name"])
        name.setObjectName("taskNameEdit")
        name.setAccessibleName("任务名称")
        layout.addWidget(name)
        group = QtWidgets.QLabel('当前分组：' + ('测试' if preferences['group'] == 'test' else '正式'))
        group.setObjectName('taskGroupLabel')
        layout.addWidget(group)
        move = QtWidgets.QPushButton('加入正式' if preferences['group'] == 'test' else '移回测试')
        move.setObjectName('taskGroupMove')
        move.setAccessibleName(move.text())
        layout.addWidget(move)
        layout.addWidget(QtWidgets.QLabel("任务介绍"))
        description = QtWidgets.QPlainTextEdit(preferences["description"])
        description.setObjectName("taskDescriptionEdit")
        description.setAccessibleName("任务介绍")
        layout.addWidget(description, 1)
        error_label = QtWidgets.QLabel()
        error_label.setObjectName("taskEditError")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("color: #b42318;")
        layout.addWidget(error_label)
        buttons = QtWidgets.QDialogButtonBox()
        save = buttons.addButton("保存", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        save.setProperty("primary", True)
        save.setDefault(True)
        layout.addWidget(buttons)

        def save_changes() -> None:
            try:
                current = self._tasks.get_task_preferences(task_id)
                self._tasks.set_task_preferences(
                    task_id, name=name.text(), description=description.toPlainText(),
                    show_logs=current["show_logs"],
                )
            except Exception as exc:
                error_label.setText(f"保存失败：{exc}")
                return
            dialog.accept()

        buttons.accepted.connect(save_changes)
        buttons.rejected.connect(dialog.reject)
        def move_task() -> None:
            if preferences['group'] == 'test':
                changed = self.promote_task(task_id)
            else:
                try:
                    self._tasks.demote_task(task_id)
                    self._task_groups.setCurrentIndex(1)
                    changed = True
                except Exception as exc:
                    error_label.setText(str(exc))
                    return
            if changed:
                dialog.accept()
        move.clicked.connect(move_task)
        dialog.exec()
        dialog.deleteLater()

    def promote_task(self, task_id: str) -> bool:
        """Only connected to explicit user actions in skill details/settings."""
        if self._tasks is None:
            return False
        try:
            task = self._tasks.catalog.get(task_id)
            formal = next((item for item in self._tasks.catalog.tasks()
                           if item.id == task.id and item.group == 'formal'), None)
            replace = formal is not None
            if replace:
                answer = QtWidgets.QMessageBox.question(
                    self._window, '替换正式技能',
                    f'将用测试版“{task.name}”替换正式版“{formal.name}”。\n旧正式版会先备份。是否继续？',
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No)
                if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                    return False
            self._tasks.promote_task(task_id, replace_existing=replace)
            self._task_groups.setCurrentIndex(0)
            return True
        except Exception as exc:
            self.error.emit(f'加入正式失败：{exc}')
            return False

    def _set_task_visible(self, task_id: str, visible: bool) -> None:
        if self._tasks is None:
            return
        try:
            self._tasks.set_task_visible(task_id, visible)
        except Exception as exc:
            self._sync_tasks()
            self.error.emit(f"更新任务显示设置失败：{exc}")

    def _build_general_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("desktopPetSettingsCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(0)
        self._scale_combo = ComboBox()
        self._scale_combo.setObjectName("desktopPetSettingsCombo")
        for scale in self._scale_options():
            self._scale_combo.addItem(display_scale_label(scale), scale)
        self._input_mode_combo = ComboBox()
        self._input_mode_combo.setObjectName("desktopPetInputMode")
        self._input_mode_combo.setAccessibleName("操作模式")
        self._input_mode_combo.addItem("后台操作", "background")
        self._input_mode_combo.addItem("前台操作", "foreground")
        self._theme_combo = ComboBox()
        self._theme_combo.setObjectName("desktopPetTheme")
        self._theme_combo.setAccessibleName("桌宠主题")
        for title, theme in (("星际飞碟", "ufo"), ("魔法扫帚", "broom")):
            self._theme_combo.addItem(title, theme)
        self._mouse_checkbox = self._switch("点击时显示指向动作")
        layout.addLayout(self._row("桌宠大小", "调整桌宠在屏幕上的显示大小。", self._scale_combo))
        layout.addWidget(self._divider())
        layout.addLayout(self._row("操作模式", "后台操作直接点击目标窗口；前台操作移动鼠标并同步桌宠动作。", self._input_mode_combo))
        layout.addWidget(self._divider())
        layout.addLayout(self._row("桌宠主题", "选择之之的外观与动作。", self._theme_combo))
        layout.addWidget(self._divider())
        layout.addLayout(self._row("点击指向", "启用后，鼠标点击会显示桌宠的指向动作。", self._mouse_checkbox))
        self._scale_combo.currentIndexChanged.connect(self._set_scale)
        self._input_mode_combo.currentIndexChanged.connect(self._set_input_mode)
        self._theme_combo.currentIndexChanged.connect(self._set_pet_theme)
        self._mouse_checkbox.toggled.connect(self._pet.set_mouse_point_visible)
        return card

    @staticmethod
    def _switch(accessible_name: str) -> ToggleSwitch:
        return ToggleSwitch(accessible_name)

    @staticmethod
    def _row(title: str, detail: str, control: QtWidgets.QWidget) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(8, 12, 8, 12)
        labels = QtWidgets.QVBoxLayout()
        labels.setSpacing(3)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("desktopPetSettingsRowTitle")
        detail_label = QtWidgets.QLabel(detail)
        detail_label.setObjectName("desktopPetSettingsRowDetail")
        detail_label.setWordWrap(True)
        labels.addWidget(title_label)
        labels.addWidget(detail_label)
        row.addLayout(labels, 1)
        row.addWidget(control)
        return row

    @staticmethod
    def _divider() -> QtWidgets.QFrame:
        divider = QtWidgets.QFrame()
        divider.setObjectName("desktopPetSettingsDivider")
        divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        return divider

    @staticmethod
    def _scale_options() -> tuple[float, ...]:
        return (0.192,) + tuple(round(0.20 + index * 0.04, 2) for index in range(10)) + (0.64, 0.80, 1.00, 1.25)

    @QtCore.Slot(int)
    def _set_scale(self, index: int) -> None:
        scale = self._scale_combo.itemData(index)
        if isinstance(scale, bool) or not isinstance(scale, float):
            self.error.emit("桌宠大小选项无效")
            return
        try:
            self._pet.set_display_scale(scale)
        except Exception as exc:
            self.error.emit(f"更新桌宠大小失败：{exc}")

    @QtCore.Slot()
    def _sync_from_pet(self) -> None:
        self._sync_scale(self._pet.display_scale, QtCore.QPoint())
        self._sync_mouse_point_visible(self._pet.mouse_point_visible)
        self._sync_input_options()

    def _sync_input_options(self, *_args) -> None:
        if self._closed:
            return
        busy = (
            self._tasks is not None and self._tasks.process.state != "idle"
            or self._settings is not None and self._settings.ai_work_active()
        )
        reason = "任务或 AI 正在工作，结束后可切换。" if busy else ""
        mode = self._settings.get_input_mode() if self._settings else "background"
        for control, value in ((self._input_mode_combo, mode), (self._theme_combo, self._pet.theme)):
            control.blockSignals(True)
            control.setCurrentIndex(control.findData(value))
            control.blockSignals(False)
            control.setEnabled(not busy)
            control.setToolTip(reason)
        if self._settings is None:
            self._input_mode_combo.setEnabled(False)

    @QtCore.Slot(int)
    def _set_input_mode(self, index: int) -> None:
        mode = self._input_mode_combo.itemData(index)
        self._sync_input_options()
        if not self._input_mode_combo.isEnabled():
            return
        try:
            self._settings.set_input_mode(mode)
            self._settings.sync()
        except Exception as exc:
            self.error.emit(f"更新操作模式失败：{exc}")
        self._sync_input_options()

    @QtCore.Slot(int)
    def _set_pet_theme(self, index: int) -> None:
        theme = self._theme_combo.itemData(index)
        self._sync_input_options()
        if not self._theme_combo.isEnabled():
            return
        try:
            self._pet.set_theme(theme)
        except Exception as exc:
            self.error.emit(f"更新桌宠主题失败：{exc}")
        self._sync_input_options()


    @QtCore.Slot(float, QtCore.QPoint)
    def _sync_scale(self, scale: float, _position: QtCore.QPoint) -> None:
        index = self._scale_combo.findData(scale)
        if index < 0:
            self._scale_combo.addItem(display_scale_label(scale), scale)
            index = self._scale_combo.count() - 1
        self._scale_combo.blockSignals(True)
        self._scale_combo.setCurrentIndex(index)
        self._scale_combo.blockSignals(False)

    @QtCore.Slot(bool)
    def _sync_mouse_point_visible(self, visible: bool) -> None:
        self._mouse_checkbox.blockSignals(True)
        self._mouse_checkbox.setChecked(visible)
        self._mouse_checkbox.blockSignals(False)

    @QtCore.Slot(int)
    def _show_page(self, index: int) -> None:
        if not 0 <= index < self._pages.count():
            return
        self._pages.setCurrentIndex(index)

    def append_log(self, message: str) -> None:
        """Append a successfully persisted host log message to the live view."""
        if not isinstance(message, str):
            raise TypeError("日志消息必须是字符串")
        cursor = self._log_view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(f"{message}\n")
        self._scroll_log_to_end()

    def _load_log(self) -> None:
        try:
            text = self._log_path.read_text(encoding="utf-8")
        except OSError as exc:
            self.error.emit(f"读取运行日志失败：{exc}")
            return
        self._log_view.setPlainText(text)
        self._scroll_log_to_end()

    def _scroll_log_to_end(self) -> None:
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("桌宠设置面板已经关闭")


__all__ = ("DesktopPetSettingsComponent", "desktop_pet_settings_style")
