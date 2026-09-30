"""Host-side assembly for task discovery, processes, presentation and input."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PySide6 import QtCore, QtWidgets

from huanzhizhi.pet.component import PetComponent
from huanzhizhi.pet.views import ParameterDialog
from huanzhizhi.parameters import validate_values
from huanzhizhi.platform import GlobalMouseWatcher, physical_to_qt_global
from huanzhizhi.settings.component import SettingsComponent
from huanzhizhi.tasks.catalog import TaskCatalog, TaskDescriptor
from huanzhizhi.tasks.presentation import TaskListItem, TaskPresentationCoordinator
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import (
    FinishedEvent,
    LogEvent,
    PointEvent,
    HintEvent,
    PreviewEvent,
    SpeakEvent,
    StatusEvent,
    TaskEvent,
)


@dataclass(frozen=True, slots=True)
class TaskPreview:
    image_path: Path
    image_bytes: bytes
    width: int
    height: int
    analysis_json: str
    rows: tuple[int, ...]
    columns: tuple[int, ...]


class TaskHostComponent(QtCore.QObject):
    """Coordinate external tasks without importing their implementation."""

    error = QtCore.Signal(str)
    log_message = QtCore.Signal(str)
    preview_received = QtCore.Signal(object)
    status_received = QtCore.Signal(str, str, str)
    tasks_changed = QtCore.Signal()

    def __init__(
        self,
        pet: PetComponent,
        tasks_root: Path,
        *,
        python_executable: Path | None = None,
        settings: SettingsComponent | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(pet, PetComponent):
            raise TypeError("pet 必须是 PetComponent")
        if not isinstance(tasks_root, Path):
            raise TypeError("tasks_root 必须是 Path")
        executable = python_executable or Path(sys.executable)
        self._pet = pet
        self._settings = settings
        self._hidden_task_ids = set(settings.get_hidden_task_ids()) if settings else set()
        self._task_preferences = settings.get_task_preferences() if settings else {}
        self._catalog = TaskCatalog(tasks_root, executable, 3_000)
        self._process = TaskProcessManager(self)
        self._presentation = TaskPresentationCoordinator(
            pet_rect_provider=self._pet.window.visible_geometry,
            display_scale_provider=lambda: self._pet.display_scale,
            speech_rect_provider=self._speech_rect,
            parent=self,
        )
        self._mouse = GlobalMouseWatcher(self)
        self._active_descriptor: TaskDescriptor | None = None
        self._task_completed_successfully = False
        self._context_menu_open = False
        self._pet_visible = False
        self._ignore_mouse_sequence = False
        self._closed = False

        self._presentation.start_task_requested.connect(self.start_task)
        self._presentation.parameters_requested.connect(self.show_parameters)
        self._presentation.stop_task_requested.connect(self.stop_task)
        self._presentation.bubbles_changed.connect(self._sync_mouse_watcher)
        self._process.event_received.connect(self._handle_task_event)
        self._process.state_changed.connect(self._handle_process_state)
        self._process.error.connect(self._handle_process_error)
        self._mouse.pressed.connect(self._handle_mouse_pressed)
        self._mouse.moved.connect(self._handle_mouse_moved)
        self._mouse.released.connect(self._handle_mouse_released)
        self._mouse.error.connect(self._handle_mouse_error)
        self._pet.clicked.connect(self._handle_pet_clicked)
        self._pet.position_changing.connect(self._presentation.reposition)
        self._pet.position_changed.connect(self._presentation.reposition)
        self._pet.scale_changed.connect(self._handle_scale_changed)
        self._pet.mouse_point_visible_changed.connect(self._sync_mouse_watcher)
        self._pet.visibility_changed.connect(self._handle_pet_visibility)
        self._pet.context_menu_open_changed.connect(self._handle_context_menu_open)

        self.reload_tasks()

    def task_parameters(self, task_id: str) -> dict:
        descriptor = self._catalog.get(task_id)
        values = self._settings.get_task_parameters(task_id) if self._settings is not None else {}
        return validate_values(descriptor.parameters, values)

    @QtCore.Slot(str)
    def show_parameters(self, task_id: str) -> None:
        if self._process.state != "idle":
            return
        dialog = None
        try:
            descriptor = self._catalog.get(task_id)
            dialog = ParameterDialog(descriptor, self.task_parameters(task_id))
            self._handle_context_menu_open(True)
            while dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                try:
                    values = validate_values(descriptor.parameters, dialog.values())
                    if self._settings is None:
                        raise RuntimeError("没有可用的任务设置存储")
                    self._settings.set_task_parameters(task_id, values)
                    self._settings.sync()
                    break
                except (ValueError, OSError, RuntimeError) as error:
                    dialog.error_label.setText(str(error))
        except Exception as error:
            self.error.emit(f"参数设置失败：{error}")
        finally:
            self._handle_context_menu_open(False)
            if dialog is not None:
                dialog.deleteLater()

    @property
    def catalog(self) -> TaskCatalog:
        return self._catalog

    @property
    def process(self) -> TaskProcessManager:
        return self._process

    @property
    def presentation(self) -> TaskPresentationCoordinator:
        return self._presentation

    @property
    def mouse_watcher(self) -> GlobalMouseWatcher:
        return self._mouse

    def reload_tasks(self) -> None:
        self._ensure_open()
        self._catalog.reload()
        self._update_task_list()
        self.tasks_changed.emit()

    def is_task_visible(self, task_id: str) -> bool:
        self._ensure_open()
        self._catalog.get(task_id)
        return task_id not in self._hidden_task_ids

    def set_task_visible(self, task_id: str, visible: bool) -> None:
        self._ensure_open()
        self._catalog.get(task_id)
        if not isinstance(visible, bool):
            raise TypeError("任务显示状态必须是布尔值")
        hidden = self._hidden_task_ids.copy()
        if visible:
            hidden.discard(task_id)
        else:
            hidden.add(task_id)
        if hidden == self._hidden_task_ids:
            return
        if self._settings is not None:
            self._settings.set_hidden_task_ids(tuple(hidden))
            self._settings.sync()
        self._hidden_task_ids = hidden
        self._update_task_list()
        self.tasks_changed.emit()

    def get_task_preferences(self, task_id: str) -> dict:
        self._ensure_open()
        descriptor = self._catalog.get(task_id)
        return dict(self._task_preferences.get(task_id, {
            "name": descriptor.name,
            "description": descriptor.description,
            "show_logs": True,
        }))

    def set_task_preferences(
        self, task_id: str, *, name: str, description: str, show_logs: bool
    ) -> None:
        self._ensure_open()
        self._catalog.get(task_id)
        preferences = {"name": name, "description": description, "show_logs": show_logs}
        SettingsComponent.validate_task_preferences({task_id: preferences})
        preferences["name"] = name.strip()
        if preferences == self.get_task_preferences(task_id):
            return
        values = {**self._task_preferences, task_id: preferences}
        if self._settings is not None:
            self._settings.set_task_preferences(values)
            self._settings.sync()
        self._task_preferences = values
        self._update_task_list()
        self.tasks_changed.emit()

    def _update_task_list(self) -> None:
        self._presentation.set_task_preferences(self._task_preferences)
        self._presentation.set_tasks(
            TaskListItem(
                descriptor.id,
                self.get_task_preferences(descriptor.id)["name"],
                self.get_task_preferences(descriptor.id)["description"],
                descriptor.presentation,
            )
            for descriptor in self._catalog.tasks()
            if descriptor.id not in self._hidden_task_ids
        )

    @QtCore.Slot(str)
    def start_task(self, task_id: str, options: dict | None = None) -> None:
        self._ensure_open()
        if self._process.state != "idle":
            raise RuntimeError("已有任务正在运行")
        descriptor = self._catalog.get(task_id)
        if options is None:
            options = self.task_parameters(task_id) or None
        elif isinstance(options, dict):
            options = {**self.task_parameters(task_id), **options}
        self._active_descriptor = descriptor
        self._task_completed_successfully = False
        self._presentation.set_running(
            True,
            descriptor.id,
            "启动中",
            descriptor.name,
            "任务启动中",
        )
        try:
            self._process.start(descriptor.id, descriptor.script_path, options)
        except Exception as exc:
            self._active_descriptor = None
            self._pet.set_task_state("idle")
            self._presentation.set_running(
                False,
                descriptor.id,
                "失败",
                descriptor.name,
                str(exc),
            )
            self.error.emit(f"任务启动失败：{exc}")
        else:
            self.log_message.emit(f"任务启动：{descriptor.name}")

    @QtCore.Slot()
    def stop_task(self) -> None:
        self._ensure_open()
        descriptor = self._active_descriptor
        if descriptor is None or self._process.state == "idle":
            raise RuntimeError("当前没有正在运行的任务")
        self._process.stop()
        self._pet.point_overlay.hide_hint()
        self._presentation.set_running(
            True,
            descriptor.id,
            "停止中",
            descriptor.name,
            "正在停止任务",
        )

    @QtCore.Slot()
    def step_task(self) -> None:
        self._ensure_open()
        self._process.step()

    def close(self) -> None:
        if self._closed:
            return
        self._mouse.close()
        self._pet.point_overlay.hide_hint()
        self._process.close()
        self._pet.set_task_state("idle")
        self._closed = True
        self._presentation.close()
        self._active_descriptor = None

    @QtCore.Slot()
    def _handle_pet_clicked(self) -> None:
        try:
            if self._catalog.signature() != self._catalog.loaded_signature:
                self.reload_tasks()
            self._presentation.handle_pet_clicked()
        except Exception as exc:
            self.error.emit(f"无法显示任务列表：{exc}")

    @QtCore.Slot(object)
    def _handle_task_event(self, event: TaskEvent) -> None:
        try:
            self._dispatch_task_event(event)
        except Exception as exc:
            descriptor = self._active_descriptor
            self._task_completed_successfully = False
            self._pet.set_task_state("idle")
            self._pet.point_overlay.hide_hint()
            task_id = descriptor.id if descriptor is not None else ""
            task_name = descriptor.name if descriptor is not None else "任务"
            message = f"任务请求处理失败：{exc}"
            self._presentation.set_running(False, task_id, "失败", task_name, message)
            self.error.emit(message)
            self._process.stop()

    def _dispatch_task_event(self, event: TaskEvent) -> None:
        descriptor = self._require_active_descriptor()
        if isinstance(event, StatusEvent):
            self.status_received.emit(event.status_title, event.title, event.detail)
            self._presentation.set_running(
                True,
                descriptor.id,
                event.status_title,
                event.title,
                event.detail,
            )
            self.log_message.emit(
                f"任务状态：{event.status_title} · {event.title} · {event.detail}"
            )
        elif isinstance(event, LogEvent):
            self.log_message.emit(event.message)
        elif isinstance(event, SpeakEvent):
            self._present_speech(event, descriptor)
        elif isinstance(event, PointEvent):
            point = physical_to_qt_global(event.x, event.y)
            self._pet.show_point_at_click(point.x(), point.y())
        elif isinstance(event, HintEvent):
            if event.kind == "clear" or not self._pet_visible:
                self._pet.point_overlay.hide_hint()
            else:
                points = tuple(physical_to_qt_global(x, y) for x, y in event.points)
                self._pet.point_overlay.show_hint(points, event.kind, event.label)
        elif isinstance(event, PreviewEvent):
            self.preview_received.emit(self._load_preview(event, descriptor))
        elif isinstance(event, FinishedEvent):
            # Celebrate only after process exit has also passed protocol/exit checks.
            self._task_completed_successfully = (
                not event.error
                and not event.stopped_by_request
                and self._process.state != "stopping"
            )
            if not self._task_completed_successfully:
                self._pet.set_task_state("idle")
            self._pet.point_overlay.hide_hint()
            self.status_received.emit(event.status_title, event.title, event.detail)
            self._presentation.set_running(
                False,
                descriptor.id,
                event.status_title,
                event.title,
                event.detail,
            )
            self.log_message.emit(
                f"任务状态：{event.status_title} · {event.title} · {event.detail}"
            )
            if event.error:
                self.error.emit(f"任务失败：{event.error}")
        else:
            raise TypeError(f"未知任务事件对象：{type(event).__name__}")

    @QtCore.Slot(str)
    def _handle_process_state(self, state: str) -> None:
        if state == "starting":
            self._task_completed_successfully = False
            self._pet.set_task_state("thinking")
        elif state == "running":
            self._pet.set_task_state("working")
        elif state == "stopping":
            self._task_completed_successfully = False
            self._pet.set_task_state("idle")
        elif state == "idle":
            self._pet.set_task_state("complete" if self._task_completed_successfully else "idle")
            self._task_completed_successfully = False
            self._pet.point_overlay.hide_hint()
            self._active_descriptor = None

    @QtCore.Slot(str)
    def _handle_process_error(self, message: str) -> None:
        self._task_completed_successfully = False
        self._pet.set_task_state("idle")
        descriptor = self._active_descriptor
        task_id = descriptor.id if descriptor is not None else ""
        task_name = descriptor.name if descriptor is not None else "任务"
        self._presentation.set_running(False, task_id, "失败", task_name, message)
        self.error.emit(message)

    @QtCore.Slot(bool)
    def _handle_pet_visibility(self, visible: bool) -> None:
        self._pet_visible = bool(visible)
        if not visible and self._presentation.can_close_on_outside_click:
            self._presentation.close_bubbles()
        self._presentation.set_pet_visible(bool(visible))
        self._sync_mouse_watcher()

    @QtCore.Slot(bool)
    def _handle_context_menu_open(self, open_: bool) -> None:
        self._context_menu_open = bool(open_)
        self._ignore_mouse_sequence = bool(open_)
        self._sync_mouse_watcher()

    @QtCore.Slot(float, QtCore.QPoint)
    def _handle_scale_changed(self, scale: float, _position: QtCore.QPoint) -> None:
        self._presentation.set_display_scale(scale)

    @QtCore.Slot()
    def _sync_mouse_watcher(self) -> None:
        if self._closed:
            return
        enabled = (
            self._pet_visible
            and not self._context_menu_open
            and (
                self._pet.mouse_point_visible
                or self._presentation.can_close_on_outside_click
            )
        )
        try:
            self._mouse.set_enabled(enabled)
        except Exception as exc:
            if self._pet.mouse_point_visible:
                self._pet.set_mouse_point_visible(False)
            self.error.emit(str(exc))

    @QtCore.Slot(int, int)
    def _handle_mouse_pressed(self, x: int, y: int) -> None:
        point = QtCore.QPoint(x, y)
        bubble_hit = self._presentation.is_bubble_hit(point)
        pet_hit = self._pet.is_pet_hit_at(point)
        if self._presentation.can_close_on_outside_click and not bubble_hit and not pet_hit:
            self._presentation.handle_outside_click(point)
        if not self._pet.mouse_point_visible or bubble_hit or pet_hit:
            self._ignore_mouse_sequence = True
            return
        self._ignore_mouse_sequence = False
        self._pet.begin_manual_point_at_click(x, y)

    @QtCore.Slot(int, int)
    def _handle_mouse_moved(self, x: int, y: int) -> None:
        if self._ignore_mouse_sequence or not self._pet.mouse_point_visible:
            return
        self._pet.move_manual_point_at_click(x, y)

    @QtCore.Slot(int, int)
    def _handle_mouse_released(self, x: int, y: int) -> None:
        if self._ignore_mouse_sequence or not self._pet.mouse_point_visible:
            self._ignore_mouse_sequence = False
            return
        self._pet.release_manual_point_at_click(x, y)

    @QtCore.Slot(str)
    def _handle_mouse_error(self, message: str) -> None:
        if self._pet.mouse_point_visible:
            self._pet.set_mouse_point_visible(False)
        self.error.emit(message)

    def _present_speech(self, event: SpeakEvent, descriptor: TaskDescriptor) -> None:
        self._pet.show_message(event.text)

    @staticmethod
    def _load_preview(event: PreviewEvent, descriptor: TaskDescriptor) -> TaskPreview:
        path = Path(event.image_path)
        if not path.is_absolute():
            path = descriptor.script_path.parent / path
        path = path.resolve()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"无法读取任务预览图 {path}：{exc}") from exc
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                actual_size = image.size
                image_format = image.format
        except Exception as exc:
            raise RuntimeError(f"任务预览图不是有效图像 {path}：{exc}") from exc
        if image_format != "PNG":
            raise RuntimeError(f"任务预览图必须是 PNG：{path}")
        if actual_size != (event.width, event.height):
            raise RuntimeError(
                f"任务预览图尺寸不一致：协议为 {event.width}x{event.height}，"
                f"文件为 {actual_size[0]}x{actual_size[1]}"
            )
        analysis_json = json.dumps(
            event.analysis,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
            sort_keys=True,
        )
        return TaskPreview(
            path,
            data,
            event.width,
            event.height,
            analysis_json,
            tuple(event.grid_overlay["rows"]),
            tuple(event.grid_overlay["columns"]),
        )

    @staticmethod
    def _normalized_action(action: str, *, allow_auto: bool) -> str:
        if not isinstance(action, str):
            raise TypeError("任务语音动作必须是字符串")
        normalized = action.strip().lower().replace("-", "_")
        if normalized in {"v", "talkv"}:
            normalized = "talk_v"
        allowed = {"talk", "talk_v"}
        if allow_auto:
            allowed.add("auto")
        if normalized not in allowed:
            raise ValueError(f"不支持的任务语音动作：{action}")
        return normalized

    def _speech_rect(self) -> QtCore.QRect | None:
        bubble = self._pet.speech_bubble
        return QtCore.QRect(bubble.geometry()) if bubble.isVisible() else None

    def _require_active_descriptor(self) -> TaskDescriptor:
        if self._active_descriptor is None:
            raise RuntimeError("收到任务事件时没有活动任务")
        return self._active_descriptor

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("任务宿主组件已经关闭")


__all__ = ("TaskHostComponent", "TaskPreview")
