"""Desktop-pet settings panel."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet import PetComponent
from huanzhizhi.pet.context_menu import display_scale_label

if TYPE_CHECKING:
    from huanzhizhi.tasks.component import TaskHostComponent


class _SettingsWindow(QtWidgets.QWidget):
    closed = QtCore.Signal()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.closed.emit()
        super().closeEvent(event)


def desktop_pet_settings_style() -> str:
    return """
QWidget#desktopPetSettingsWindow { background: #ffffff; color: #242424; }
QWidget#desktopPetSettingsSidebar { background: #f8f8f8; border-right: 1px solid #ededed; }
QLabel#desktopPetSettingsBrand { font-size: 15px; font-weight: 600; padding: 18px 18px 12px 18px; }
QLabel#desktopPetSettingsSection { color: #777777; font-size: 12px; padding: 8px 18px 4px 18px; }
QListWidget#desktopPetSettingsNavigation { border: none; background: transparent; outline: none; padding: 0 8px; }
QListWidget#desktopPetSettingsNavigation::item { border-radius: 9px; padding: 10px 12px; margin: 2px 0; }
QListWidget#desktopPetSettingsNavigation::item:selected { background: #e9e9e9; color: #202020; font-weight: 600; }
QWidget#desktopPetSettingsContent { background: #ffffff; }
QLabel#desktopPetSettingsTitle { font-size: 28px; font-weight: 600; }
QLabel#desktopPetSettingsGroupTitle { font-size: 16px; font-weight: 600; margin-top: 12px; }
QFrame#desktopPetSettingsCard { background: #ffffff; border: 1px solid #e9e9e9; border-radius: 16px; }
QListWidget#desktopPetTaskCards { background: #ffffff; border: none; outline: none; }
QListWidget#desktopPetTaskCards::item { background: transparent; border: none; }
QFrame#desktopPetTaskCard { background: #ffffff; border: 1px solid #e9e9e9; border-radius: 14px; }
QFrame#desktopPetTaskCard:hover { border-color: #b7cdf0; }
QLabel#desktopPetSettingsRowTitle { font-size: 14px; font-weight: 600; }
QLabel#desktopPetSettingsRowDetail { color: #757575; font-size: 12px; }
QFrame#desktopPetSettingsDivider { background: #eeeeee; max-height: 1px; border: none; }
QComboBox#desktopPetSettingsCombo { border: 1px solid #e2e2e2; border-radius: 10px; padding: 6px 34px 6px 12px; min-width: 126px; background: #ffffff; }
QComboBox#desktopPetSettingsCombo:hover { background: #f8f8f8; border-color: #d3d3d3; }
QComboBox#desktopPetSettingsCombo:focus { border-color: #8ab4f8; }
QComboBox#desktopPetSettingsCombo::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
"""


class _ToggleSwitch(QtWidgets.QCheckBox):
    """A compact switch with an explicit full-control click target."""

    def __init__(self, accessible_name: str) -> None:
        super().__init__()
        self.setObjectName("desktopPetSettingsSwitch")
        self.setAccessibleName(accessible_name)
        self.setFixedSize(52, 30)
        self._thumb_position = 0.0
        self._thumb_animation = QtCore.QPropertyAnimation(self, b"thumbPosition", self)
        self._thumb_animation.setDuration(140)
        self._thumb_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_thumb)

    @QtCore.Property(float)
    def thumbPosition(self) -> float:
        return self._thumb_position

    @thumbPosition.setter
    def thumbPosition(self, position: float) -> None:
        self._thumb_position = min(1.0, max(0.0, float(position)))
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @QtCore.Slot(bool)
    def _animate_thumb(self, checked: bool) -> None:
        self._thumb_animation.stop()
        self._thumb_animation.setStartValue(self._thumb_position)
        self._thumb_animation.setEndValue(1.0 if checked else 0.0)
        self._thumb_animation.start()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(1, 3, -1, -3)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#3b82f6") if self.isChecked() else QtGui.QColor("#d5d5d5"))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        diameter = track.height() - 4
        minimum_x = track.left() + 2
        maximum_x = track.right() - diameter - 2
        x = round(minimum_x + (maximum_x - minimum_x) * self._thumb_position)
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawEllipse(x, track.top() + 2, diameter, diameter)
        painter.end()


class _SettingsComboBox(QtWidgets.QComboBox):
    """Keeps the native combobox behavior with a restrained chevron."""

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        center_x = self.width() - 18
        center_y = self.height() // 2
        painter.setPen(QtGui.QPen(QtGui.QColor("#737373"), 1.5))
        painter.drawLine(center_x - 5, center_y - 2, center_x, center_y + 3)
        painter.drawLine(center_x, center_y + 3, center_x + 5, center_y - 2)
        painter.end()


class DesktopPetSettingsComponent(QtCore.QObject):
    """Displays pet preferences and the current host run log."""

    error = QtCore.Signal(str)

    def __init__(
        self,
        pet: PetComponent,
        log_path: Path,
        parent: QtCore.QObject | None = None,
        *,
        tasks: TaskHostComponent | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(pet, PetComponent):
            raise TypeError("pet 必须是 PetComponent")
        if not isinstance(log_path, Path):
            raise TypeError("log_path 必须是 Path")
        self._pet = pet
        self._tasks = tasks
        self._log_path = log_path
        self._closed = False
        self._window = _SettingsWindow()
        self._window.setObjectName("desktopPetSettingsWindow")
        self._window.setWindowTitle("幻之之设置")
        self._window.resize(760, 540)
        self._window.setMinimumSize(640, 440)
        self._window.setStyleSheet(desktop_pet_settings_style())
        self._build_ui()
        if self._tasks is not None:
            self._tasks.tasks_changed.connect(self._sync_tasks)
        self._pet.scale_changed.connect(self._sync_scale)
        self._pet.mouse_point_visible_changed.connect(self._sync_mouse_point_visible)

    @property
    def window(self) -> QtWidgets.QWidget:
        return self._window

    def show(self) -> None:
        self._ensure_open()
        self._sync_from_pet()
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

    def close(self) -> None:
        if not self._closed:
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
        self._navigation.addItem("常规")
        self._navigation.addItem("运行日志")
        self._navigation.addItem("任务列表")
        self._navigation.setCurrentRow(0)
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(section)
        sidebar_layout.addWidget(self._navigation)
        sidebar_layout.addStretch(1)
        root.addWidget(sidebar)

        content = QtWidgets.QWidget()
        content.setObjectName("desktopPetSettingsContent")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(42, 32, 42, 36)
        self._pages = QtWidgets.QStackedWidget()
        self._pages.addWidget(self._build_general_page())
        self._pages.addWidget(self._build_log_page())
        self._pages.addWidget(self._build_tasks_page())
        content_layout.addWidget(self._pages)
        root.addWidget(content, 1)
        self._navigation.currentRowChanged.connect(self._show_page)

    def _build_general_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
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
        return page

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
        layout.setSpacing(12)
        title = QtWidgets.QLabel("任务列表")
        title.setObjectName("desktopPetSettingsTitle")
        detail = QtWidgets.QLabel("选择点击桌宠时显示的任务。修改后自动保存，不影响正在运行的任务。")
        detail.setObjectName("desktopPetSettingsRowDetail")
        detail.setWordWrap(True)
        self._task_cards = QtWidgets.QListWidget()
        self._task_cards.setObjectName("desktopPetTaskCards")
        self._task_cards.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self._task_cards.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self._task_cards.setMovement(QtWidgets.QListView.Movement.Static)
        self._task_cards.setWrapping(True)
        self._task_cards.setGridSize(QtCore.QSize(232, 266))
        self._task_cards.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self._task_cards.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._task_cards.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._task_descriptors = None
        self._task_switches: dict[str, _ToggleSwitch] = {}
        self._task_log_switches: dict[str, _ToggleSwitch] = {}
        self._task_titles: dict[str, QtWidgets.QLabel] = {}
        self._task_details: dict[str, QtWidgets.QLabel] = {}
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(self._task_cards, 1)
        self._sync_tasks()
        return page

    @QtCore.Slot()
    def _sync_tasks(self) -> None:
        if self._closed:
            return
        descriptors = self._tasks.catalog.tasks() if self._tasks is not None else ()
        if descriptors == self._task_descriptors:
            for task_id, switch in self._task_switches.items():
                visible = self._tasks.is_task_visible(task_id)
                if switch.isChecked() != visible:
                    switch.blockSignals(True)
                    switch.setChecked(visible)
                    switch.thumbPosition = float(visible)
                    switch.blockSignals(False)
                preferences = self._tasks.get_task_preferences(task_id)
                self._task_titles[task_id].setText(preferences["name"])
                self._task_details[task_id].setText(preferences["description"] or "暂无任务说明。")
                self._task_details[task_id].setToolTip(preferences["description"])
                switch.setAccessibleName(f"显示{preferences['name']}")
                log_switch = self._task_log_switches[task_id]
                log_switch.setAccessibleName(f"{preferences['name']}运行时显示日志")
                if log_switch.isChecked() != preferences["show_logs"]:
                    log_switch.blockSignals(True)
                    log_switch.setChecked(preferences["show_logs"])
                    log_switch.thumbPosition = float(preferences["show_logs"])
                    log_switch.blockSignals(False)
            return
        self._task_descriptors = descriptors
        self._task_cards.clear()
        self._task_switches = {}
        self._task_log_switches = {}
        self._task_titles = {}
        self._task_details = {}
        for descriptor in descriptors:
            preferences = self._tasks.get_task_preferences(descriptor.id)
            card = QtWidgets.QFrame()
            card.setObjectName("desktopPetTaskCard")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(8)
            title = QtWidgets.QLabel(preferences["name"])
            title.setObjectName("desktopPetSettingsRowTitle")
            title.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            title.setWordWrap(True)
            detail = QtWidgets.QLabel(preferences["description"] or "暂无任务说明。")
            detail.setObjectName("desktopPetSettingsRowDetail")
            detail.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            detail.setWordWrap(True)
            detail.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            detail.setToolTip(preferences["description"])
            heading = QtWidgets.QHBoxLayout()
            heading.addWidget(title, 1)
            edit = QtWidgets.QToolButton()
            edit.setText("编辑")
            edit.setAutoRaise(True)
            edit.setAccessibleName(f"编辑{preferences['name']}")
            edit.clicked.connect(
                lambda _checked=False, task_id=descriptor.id: self._edit_task(task_id)
            )
            heading.addWidget(edit, 0, QtCore.Qt.AlignmentFlag.AlignTop)
            card_layout.addLayout(heading)
            card_layout.addWidget(detail, 1)
            switch = self._switch(f"显示{preferences['name']}")
            visible = self._tasks.is_task_visible(descriptor.id)
            switch.blockSignals(True)
            switch.setChecked(visible)
            switch.thumbPosition = float(visible)
            switch.blockSignals(False)
            switch.toggled.connect(
                lambda checked, task_id=descriptor.id: self._set_task_visible(task_id, checked)
            )
            controls = QtWidgets.QHBoxLayout()
            label = QtWidgets.QLabel("显示任务")
            label.setObjectName("desktopPetSettingsRowDetail")
            controls.addWidget(label)
            controls.addStretch(1)
            controls.addWidget(switch)
            card_layout.addLayout(controls)
            log_switch = self._switch(f"{preferences['name']}运行时显示日志")
            log_switch.blockSignals(True)
            log_switch.setChecked(preferences["show_logs"])
            log_switch.thumbPosition = float(preferences["show_logs"])
            log_switch.blockSignals(False)
            log_switch.setToolTip("关闭后，运行气泡只显示“运行中”；运行日志仍会保存。")
            log_switch.toggled.connect(
                lambda checked, task_id=descriptor.id: self._set_task_logs_visible(task_id, checked)
            )
            log_controls = QtWidgets.QHBoxLayout()
            log_label = QtWidgets.QLabel("运行时显示日志")
            log_label.setObjectName("desktopPetSettingsRowDetail")
            log_controls.addWidget(log_label)
            log_controls.addStretch(1)
            log_controls.addWidget(log_switch)
            card_layout.addLayout(log_controls)
            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(220, 254))
            self._task_cards.addItem(item)
            self._task_cards.setItemWidget(item, card)
            self._task_switches[descriptor.id] = switch
            self._task_log_switches[descriptor.id] = log_switch
            self._task_titles[descriptor.id] = title
            self._task_details[descriptor.id] = detail
        if not descriptors:
            self._task_cards.addItem("暂无可用任务。")

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
        dialog.exec()
        dialog.deleteLater()

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
        self._scale_combo = _SettingsComboBox()
        self._scale_combo.setObjectName("desktopPetSettingsCombo")
        for scale in self._scale_options():
            self._scale_combo.addItem(display_scale_label(scale), scale)
        self._mouse_checkbox = self._switch("点击时显示指向动作")
        layout.addLayout(self._row("桌宠大小", "调整桌宠在屏幕上的显示大小。", self._scale_combo))
        layout.addWidget(self._divider())
        layout.addLayout(self._row("点击指向", "启用后，鼠标点击会显示桌宠的指向动作。", self._mouse_checkbox))
        self._scale_combo.currentIndexChanged.connect(self._set_scale)
        self._mouse_checkbox.toggled.connect(self._pet.set_mouse_point_visible)
        return card

    @staticmethod
    def _switch(accessible_name: str) -> _ToggleSwitch:
        return _ToggleSwitch(accessible_name)

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
