"""Host-side assembly for task discovery, processes, presentation and input."""

from __future__ import annotations

import io
import json
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6 import QtCore, QtWidgets

from huanzhizhi.pet.component import PetComponent
from huanzhizhi.pet.views import ParameterDialog
from huanzhizhi.parameters import validate_values
from huanzhizhi.python_runtime import python_executable as bundled_python
from huanzhizhi.platform import GlobalMouseWatcher, physical_to_qt_global
from huanzhizhi.settings.component import SettingsComponent
from huanzhizhi.tasks.catalog import TaskCatalog, TaskDescriptor
from huanzhizhi.tasks.presentation import TaskListItem, TaskPresentationCoordinator
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.storage import skill_files, publish_skill, synchronize_skill_names, validate_skill_name
from huanzhizhi.tasks.protocol import (
    FinishedEvent,
    LogEvent,
    ParametersAppliedEvent,
    PauseStateEvent,
    PointEvent,
    PetAcquireEvent,
    PetUpdateEvent,
    PetReleaseEvent,
    InputRequestEvent,
    InputCancelEvent,
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
    outside_clicked = QtCore.Signal()

    def __init__(
        self,
        pet: PetComponent,
        tasks_root: Path,
        *,
        python_executable: Path | None = None,
        settings: SettingsComponent | None = None,
        chat_button: QtWidgets.QWidget | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(pet, PetComponent):
            raise TypeError("pet 必须是 PetComponent")
        if not isinstance(tasks_root, Path):
            raise TypeError("tasks_root 必须是 Path")
        executable = python_executable or bundled_python()
        self._pet = pet
        self._hint_record_path = None
        self._hint_event = None
        self._hint_visible_since = None
        self._hint_signature = None
        self._chat_button = chat_button
        self._settings = settings
        self._hidden_task_ids = set(settings.get_hidden_task_ids()) if settings else set()
        self._task_preferences = settings.get_task_preferences() if settings else {}
        self._tasks_root = tasks_root.resolve()
        self._test_tasks_root = self._tasks_root.parent / 'test_tasks'
        self._catalog = TaskCatalog(tasks_root, test_tasks_root=self._test_tasks_root)
        self._process = TaskProcessManager(self, python_executable=executable)
        self._presentation = TaskPresentationCoordinator(
            pet_rect_provider=self._pet.bubble_geometry,
            task_menu_rect_provider=self._pet.task_menu_geometry,
            runtime_rect_provider=self._pet.runtime_geometry,
            display_scale_provider=lambda: self._pet.display_scale,
            speech_rect_provider=self._speech_rect,
            parent=self,
        )
        self._mouse = GlobalMouseWatcher(self)
        self._active_descriptor: TaskDescriptor | None = None
        self._active_options: dict | None = None
        self._parameter_request_id = 0
        self._pending_parameters: tuple[int, dict] | None = None
        self.failure_context: tuple[TaskDescriptor, dict | None, str] | None = None
        self._task_completed_successfully = False
        self._context_menu_open = False
        self._pet_visible = False
        self._ignore_mouse_sequence = False
        self._closed = False
        self.external_work_active = False
        self._run_input_mode = 'background'
        self._input_epoch = 0
        self._input_requests: set[str] = set()
        self._input_interruption: str | None = None

        self._presentation.start_task_requested.connect(self.start_task)
        self._presentation.parameters_requested.connect(self.show_parameters)
        self._presentation.runtime_parameters_requested.connect(self.update_runtime_parameters)
        self._presentation.runtime_transparency_changed.connect(self._pet.set_runtime_transparency)
        self._presentation.stop_task_requested.connect(self.stop_task)
        self._presentation.pause_task_requested.connect(self.toggle_pause)
        self._presentation.bubbles_changed.connect(self._sync_mouse_watcher)
        self._process.event_received.connect(self._handle_task_event)
        self._process.state_changed.connect(self._handle_process_state)
        self._process.pause_state_changed.connect(self._handle_pause_state)
        self._process.error.connect(self._handle_process_error)
        self._mouse.pressed.connect(self._handle_mouse_pressed)
        self._mouse.moved.connect(self._handle_mouse_moved)
        self._mouse.released.connect(self._handle_mouse_released)
        self._mouse.error.connect(self._handle_mouse_error)
        self._pet.position_changing.connect(self._presentation.reposition)
        self._pet.position_changed.connect(self._presentation.reposition)
        self._pet.theme_changed.connect(self._presentation.reposition)
        self._pet.scale_changed.connect(self._handle_scale_changed)
        self._pet.mouse_point_visible_changed.connect(self._sync_mouse_watcher)
        self._pet.visibility_changed.connect(self._handle_pet_visibility)
        self._pet.context_menu_open_changed.connect(self._handle_context_menu_open)
        self._pet.foreground_changed.connect(self._sync_mouse_watcher)
        self._pet.control_changed.connect(self._sync_mouse_watcher)
        self._pet.control_revoked.connect(self._handle_control_revoked)
        self._pet.point_overlay.installEventFilter(self)
        if self._chat_button is not None:
            self._chat_button.installEventFilter(self)

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

    @property
    def tasks_root(self) -> Path:
        return self._tasks_root

    @property
    def test_tasks_root(self) -> Path:
        return self._test_tasks_root

    def reload_tasks(self) -> None:
        self._ensure_open()
        catalog = TaskCatalog(self._tasks_root, test_tasks_root=self._test_tasks_root)
        tasks = catalog.reload()
        if self._process.state != 'idle' or self.external_work_active:
            if {task.key for task in self._catalog.tasks()} - {task.key for task in tasks}:
                raise RuntimeError('请等待当前任务结束后再加载改名后的技能')
        renames = synchronize_skill_names(tasks, self._settings)
        catalog.reload()
        self._catalog = catalog
        if self._settings is not None:
            self._task_preferences = self._settings.get_task_preferences()
            self._hidden_task_ids = set(self._settings.get_hidden_task_ids())
        else:
            self._task_preferences = {renames.get(key, key): value for key, value in self._task_preferences.items()}
            self._hidden_task_ids = {renames.get(key, key) for key in self._hidden_task_ids}
        for key, value in self._task_preferences.items():
            value['name'] = key.removeprefix('test/')
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
        preferences = dict(self._task_preferences.get(task_id, {
            "name": descriptor.name,
            "description": descriptor.description,
            "show_logs": True,
        }))
        preferences['name'] = descriptor.id
        preferences["group"] = descriptor.group
        return preferences

    def set_task_preferences(
        self, task_id: str, *, name: str, description: str, show_logs: bool,
        group: str | None = None,
    ) -> None:
        self._ensure_open()
        descriptor = self._catalog.get(task_id)
        current = self.get_task_preferences(task_id)
        if group is not None and group != descriptor.group:
            raise ValueError('分组由技能目录决定，请使用“加入正式”或“移回测试”。')
        preferences = {
            "name": name, "description": description, "show_logs": show_logs,
        }
        SettingsComponent.validate_task_preferences({task_id: preferences})
        preferences["name"] = name.strip()
        if preferences['name'] != descriptor.id:
            validate_skill_name(preferences['name'])
            if self._process.state != 'idle' or self.external_work_active:
                raise RuntimeError('请先停止当前任务，再修改任务名称')
            source = descriptor.script_path if descriptor.script_path.suffix.lower() == '.exe' else descriptor.script_path.parent
            renamed = preferences['name']
            destination = source.with_name(f'task-{renamed}.exe' if source.is_file() else renamed)
            if (source.parent / renamed).exists() or (source.parent / f'task-{renamed}.exe').exists():
                raise FileExistsError(f'同组已有名为“{renamed}”的技能')
            skill_files(source)  # Reject linked directories before moving files.
            publish_skill(source, destination)
            try:
                self.reload_tasks()
            except Exception:
                publish_skill(destination, source)
                raise
            task_id = ('test/' if descriptor.group == 'test' else '') + renamed
        if {**preferences, 'group': descriptor.group} == current:
            return
        values = {**self._task_preferences, task_id: preferences}
        if self._settings is not None:
            self._settings.set_task_preferences(values)
            self._settings.sync()
        self._task_preferences = values
        self._update_task_list()
        self.tasks_changed.emit()

    def copy_task_settings(self, source_key: str, destination_key: str, *, overwrite=False) -> None:
        """Preserve a version's display preferences and parameters when it moves."""
        if not overwrite and destination_key in self._task_preferences:
            return
        preferences = self.get_task_preferences(source_key)
        preferences.pop('group')
        values = {**self._task_preferences, destination_key: preferences}
        hidden = self._hidden_task_ids.copy()
        hidden.discard(destination_key)
        if source_key in self._hidden_task_ids:
            hidden.add(destination_key)
        if self._settings is not None:
            old_parameters = self._settings.get_task_parameters(destination_key)
            try:
                self._settings.set_task_parameters(destination_key, self._settings.get_task_parameters(source_key))
                self._settings.set_task_preferences(values)
                self._settings.set_hidden_task_ids(tuple(hidden))
                self._settings.sync()
            except Exception:
                self._settings.set_task_parameters(destination_key, old_parameters)
                self._settings.set_task_preferences(self._task_preferences)
                self._settings.set_hidden_task_ids(tuple(self._hidden_task_ids))
                self._settings.sync()
                raise
        self._task_preferences = values
        self._hidden_task_ids = hidden

    def promote_task(self, task_key: str, *, replace_existing: bool = False) -> None:
        self._ensure_open()
        if self.external_work_active or self._process.state != 'idle':
            raise RuntimeError('请先停止正在运行或修改的技能，再加入正式。')
        task = self._catalog.get(task_key)
        if task.group != 'test':
            raise ValueError('只能将测试技能加入正式。')
        formal = next((item for item in self._catalog.tasks()
                       if item.id == task.id and item.group == 'formal'), None)
        if formal is not None and not replace_existing:
            raise ValueError(f'已有正式技能“{formal.name}”，请先确认替换。')
        source = task.script_path if task.script_path.suffix.lower() == '.exe' else task.script_path.parent
        if source.parent != self._test_tasks_root:
            raise ValueError('测试技能路径不在测试目录内。')
        skill_files(source)
        destination = self._tasks_root / source.name
        previous = None
        backup = None
        if formal is not None:
            previous = formal.script_path if formal.script_path.suffix.lower() == '.exe' else formal.script_path.parent
            if previous.parent != self._tasks_root:
                raise ValueError('正式技能路径不在正式目录内。')
            skill_files(previous)
            backup = (self._tasks_root.parent / 'artifacts/skill_backups'
                      / datetime.now().strftime('%Y%m%d_%H%M%S_%f') / previous.name)
        for candidate in (self._tasks_root / task.id, self._tasks_root / f'task-{task.id}.exe'):
            if candidate.exists() and candidate != previous:
                raise ValueError('正式目录出现了同名技能，请重新打开任务列表后确认替换。')
        self._tasks_root.mkdir(parents=True, exist_ok=True)
        publish_skill(source, destination, previous=previous, backup=backup)
        try:
            self.copy_task_settings(task.key, task.id, overwrite=True)
        except Exception:
            publish_skill(destination, source)
            if previous is not None:
                publish_skill(backup, previous)
            raise
        entry = destination if destination.suffix.lower() == '.exe' else destination / 'task.py'
        self._catalog.record_move(task.key, entry, 'formal')
        self._update_task_list()
        self.tasks_changed.emit()
        self.log_message.emit(f'技能已手动加入正式：{task.name}' + (f'；旧版备份：{backup}' if backup else ''))

    def demote_task(self, task_key: str) -> None:
        self._ensure_open()
        if self.external_work_active or self._process.state != 'idle':
            raise RuntimeError('请先停止正在运行或修改的技能，再移回测试。')
        task = self._catalog.get(task_key)
        if task.group != 'formal':
            raise ValueError('只能将正式技能移回测试。')
        if (any(item.id == task.id and item.group == 'test' for item in self._catalog.tasks())
                or (self._test_tasks_root / task.id).exists()
                or (self._test_tasks_root / f'task-{task.id}.exe').exists()):
            raise ValueError('已有同名测试版本，请先处理测试版本；正式技能未移动。')
        source = task.script_path if task.script_path.suffix.lower() == '.exe' else task.script_path.parent
        if source.parent != self._tasks_root:
            raise ValueError('正式技能路径不在正式目录内。')
        skill_files(source)
        destination = self._test_tasks_root / source.name
        self._test_tasks_root.mkdir(parents=True, exist_ok=True)
        publish_skill(source, destination)
        try:
            self.copy_task_settings(task.key, f'test/{task.id}', overwrite=True)
        except Exception:
            publish_skill(destination, source)
            raise
        entry = destination if destination.suffix.lower() == '.exe' else destination / 'task.py'
        self._catalog.record_move(task.key, entry, 'test')
        self._update_task_list()
        self.tasks_changed.emit()
        self.log_message.emit(f'技能已移回测试：{task.name}')

    def _update_task_list(self) -> None:
        self._presentation.set_task_preferences(self._task_preferences)
        self._presentation.set_tasks(
            TaskListItem(
                descriptor.key,
                self.get_task_preferences(descriptor.key)["name"],
                self.get_task_preferences(descriptor.key)["description"],
                descriptor.presentation,
                descriptor.group,
            )
            for descriptor in self._catalog.tasks()
            if descriptor.key not in self._hidden_task_ids
        )

    @QtCore.Slot(str)
    def start_task(self, task_id: str, options: dict | None = None) -> None:
        self._ensure_open()
        if self.external_work_active or self._process.state != "idle":
            raise RuntimeError("已有任务正在运行")
        descriptor = self._catalog.get(task_id)
        if options is None:
            options = self.task_parameters(task_id) or None
        elif isinstance(options, dict):
            options = {**self.task_parameters(task_id), **options}
        self._start_descriptor(descriptor, options)


    def _start_descriptor(self, descriptor: TaskDescriptor, options: dict | None) -> None:
        self._input_epoch += 1
        self._input_requests.clear()
        self._input_interruption = None
        self._run_input_mode = self._settings.get_input_mode() if self._settings is not None else 'background'
        self._active_descriptor = descriptor
        self._active_options = deepcopy(options)
        self._pending_parameters = None
        self._task_completed_successfully = False
        self._presentation.set_running(
            True,
            descriptor.key,
            "启动中",
            descriptor.name,
            "任务启动中",
        )
        try:
            runtime = tuple(item for item in descriptor.parameters if item.runtime)
            values = validate_values(runtime, {item.key: options[item.key] for item in runtime if options and item.key in options})
            self._presentation.set_runtime_parameters(runtime, values)
            if self._run_input_mode == 'background':
                self._process.start(descriptor.id, descriptor.script_path, options)
            else:
                self._process.start(descriptor.id, descriptor.script_path, options, input_mode=self._run_input_mode)
        except Exception as exc:
            self._pet.set_task_state("idle")
            self._presentation.set_running(
                False,
                descriptor.key,
                "失败",
                descriptor.name,
                str(exc),
            )
            self._report_task_error(f"任务启动失败：{exc}")
            self._active_descriptor = None
        else:
            self.log_message.emit(f"任务启动：{descriptor.name}")

    @QtCore.Slot(dict)
    def update_runtime_parameters(self, values: dict) -> None:
        if self._pending_parameters is not None:
            return
        try:
            descriptor = self._require_active_descriptor()
            if self._process.state != 'running' or not self._presentation.running:
                raise RuntimeError('任务当前不能调整运行参数')
            if not isinstance(values, dict) or not values:
                raise ValueError('请选择要调整的参数')
            parameters = tuple(item for item in descriptor.parameters if item.runtime and item.key in values)
            checked = validate_values(parameters, values)
            self._parameter_request_id += 1
            self._process.update_parameters(self._parameter_request_id, checked)
            self._pending_parameters = (self._parameter_request_id, checked)
            self._presentation.runtime_update_pending()
        except (ValueError, RuntimeError) as error:
            self._presentation.runtime_update_finished({})
            self.error.emit(f'运行参数调整失败：{error}')

    @QtCore.Slot()
    def toggle_pause(self) -> None:
        try:
            self._process.set_paused(self._process.pause_state == 'running')
        except RuntimeError as error:
            self.error.emit(str(error))

    @QtCore.Slot(str)
    def _handle_pause_state(self, state: str) -> None:
        self._presentation.set_pause_state(state)
        if state == 'paused':
            self._pet.hide_hint(return_home=True)
            self._pet.set_task_state('idle')

    @QtCore.Slot()
    def stop_task(self) -> None:
        self._ensure_open()
        descriptor = self._active_descriptor
        if descriptor is None or self._process.state == "idle":
            raise RuntimeError("当前没有正在运行的任务")
        self._process.stop()
        self._pet.cancel_foreground_input()
        self._pet.hide_hint(return_home=True)
        self._presentation.set_running(
            True,
            descriptor.key,
            "停止中",
            descriptor.name,
            "正在停止任务",
        )

    @QtCore.Slot(int, int)
    def show_click_point(self, x: int, y: int) -> None:
        point = physical_to_qt_global(x, y)
        mode = (self._settings.get_input_mode()
                if self._process.state == 'idle' and self._settings is not None else self._run_input_mode)
        if mode == 'background_smooth':
            self._pet.show_point_at_click(point.x(), point.y(), smooth=True)
        else:
            self._pet.show_point_at_click(point.x(), point.y())

    def show_hint(self, event: HintEvent, record_path: Path | None = None) -> None:
        if record_path != self._hint_record_path:
            self._pet.hide_hint()
            self._hint_record_path = record_path
            self._hint_visible_since = None
        self._hint_event = event
        if event.kind == "clear" or not self._pet_visible:
            self._pet.hide_hint()
        else:
            points = tuple(physical_to_qt_global(x, y) for x, y in event.points)
            self._pet.show_hint(points, event.kind, event.label, duration=event.duration)
        self._record_hint_state()

    def _record_hint_state(self) -> None:
        if self._hint_record_path is None or self._hint_event is None:
            return
        overlay = self._pet.point_overlay
        visible = overlay.isVisible() and bool(overlay.hint_points)
        now = time.perf_counter()
        points = [[p.x(), p.y()] for p in overlay.hint_points]
        signature = (self._hint_event.kind, points, self._hint_event.label, self._hint_event.duration)
        if not visible:
            self._hint_visible_since = None
        elif self._hint_visible_since is None or signature != self._hint_signature:
            self._hint_visible_since = now
        self._hint_signature = signature
        data = dict(at_counter=now, visible=visible, visible_since=self._hint_visible_since,
                    kind=self._hint_event.kind, points=points, label=self._hint_event.label,
                    duration=self._hint_event.duration)
        try:
            with self._hint_record_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(data, ensure_ascii=False)+'\n')
        except OSError as error:
            self._hint_record_path = None
            self.error.emit(f'无法写入宿主提示记录：{error}')

    def close(self) -> None:
        if self._closed:
            return
        self._pet.release_control()
        self._mouse.close()
        self._pet.cancel_foreground_input(reason='程序正在关闭')
        self._pet.hide_hint(return_home=True)
        self._process.close()
        self._pet.set_task_state("idle")
        self._closed = True
        # Detach before Qt tears down widgets and clears their Python attributes.
        self._pet.point_overlay.removeEventFilter(self)
        if self._chat_button is not None:
            self._chat_button.removeEventFilter(self)
        self._presentation.close()
        self._active_descriptor = None

    @QtCore.Slot()
    def handle_pet_clicked(self) -> None:
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
            self._pet.release_control()
            self._task_completed_successfully = False
            self._pet.set_task_state("idle")
            self._pet.hide_hint(return_home=True)
            task_id = descriptor.key if descriptor is not None else ""
            task_name = descriptor.name if descriptor is not None else "任务"
            message = f"任务请求处理失败：{exc}"
            self._presentation.set_running(False, task_id, "失败", task_name, message)
            self._report_task_error(message)
            self._process.stop()

    def _dispatch_task_event(self, event: TaskEvent) -> None:
        descriptor = self._require_active_descriptor()
        if isinstance(event, PetAcquireEvent):
            geometry, error = None, None
            try:
                if self._process.state != 'running':
                    raise RuntimeError('技能当前不能接管桌宠')
                geometry = self._pet.acquire_control(event.lease)
            except Exception as exc:
                error = str(exc)
            try:
                self._process.send_pet_result(event.lease, geometry, error)
            except Exception:
                self._pet.release_control(event.lease)
                raise
            return
        if isinstance(event, PetUpdateEvent):
            if self._process.state == 'running':
                self._pet.update_control(event.lease, event.x, event.y, event.facing, event.pose)
            return
        if isinstance(event, PetReleaseEvent):
            self._pet.release_control(event.lease)
            return
        if isinstance(event, PauseStateEvent):
            return  # Process manager validates the acknowledgement and emits its state.
        if isinstance(event, StatusEvent):
            if self._process.state == "running":
                self._pet.set_task_state("working")
            self.status_received.emit(event.status_title, event.title, event.detail)
            self._presentation.set_running(
                True,
                descriptor.key,
                event.status_title,
                event.title,
                event.detail,
            )
            self.log_message.emit(
                f"任务状态：{event.status_title} · {event.title} · {event.detail}"
            )
        elif isinstance(event, LogEvent):
            self.log_message.emit(event.message)
        elif isinstance(event, ParametersAppliedEvent):
            if self._pending_parameters != (event.request_id, event.values):
                raise ValueError('运行参数确认与待应用请求不匹配')
            self._active_options = {**(self._active_options or {}), **event.values}
            self._pending_parameters = None
            self._presentation.runtime_update_finished(event.values)
            try:
                if self._settings is None:
                    raise RuntimeError('没有可用的任务设置存储')
                values = validate_values(descriptor.parameters, {**self.task_parameters(descriptor.key), **event.values})
                self._settings.set_task_parameters(descriptor.key, values)
                self._settings.sync()
            except (ValueError, OSError, RuntimeError) as error:
                self.error.emit(f'运行参数已应用，但保存失败：{error}')
            self.log_message.emit(f'运行参数已生效：{event.values}')
        elif isinstance(event, SpeakEvent):
            self._present_speech(event, descriptor)
        elif isinstance(event, PointEvent):
            self.show_click_point(event.x, event.y)
        elif isinstance(event, InputRequestEvent):
            self._start_input_request(event)
        elif isinstance(event, InputCancelEvent):
            self._pet.cancel_foreground_input(event.request_id, '任务已取消该操作')
        elif isinstance(event, HintEvent):
            self.show_hint(event)
        elif isinstance(event, PreviewEvent):
            self.preview_received.emit(self._load_preview(event, descriptor))
        elif isinstance(event, FinishedEvent):
            self._pet.release_control()
            if self._input_interruption is not None and event.stopped_by_request and not event.error:
                event = replace(event, status_title='已停止', detail=self._input_interruption)
            # Celebrate only after process exit has also passed protocol/exit checks.
            self._task_completed_successfully = (
                not event.error
                and not event.stopped_by_request
                and self._process.state != "stopping"
            )
            if not self._task_completed_successfully:
                self._pet.set_task_state("idle")
            self._pet.hide_hint(return_home=True)
            self.status_received.emit(event.status_title, event.title, event.detail)
            self._presentation.set_running(
                False,
                descriptor.key,
                event.status_title,
                event.title,
                event.detail,
            )
            self.log_message.emit(
                f"任务状态：{event.status_title} · {event.title} · {event.detail}"
            )
            if event.error:
                self._report_task_error(f"任务失败：{event.error}", detail=event.detail)
        else:
            raise TypeError(f"未知任务事件对象：{type(event).__name__}")

    def _start_input_request(self, event: InputRequestEvent) -> None:
        if self._run_input_mode not in ('foreground', 'background_smooth') or self._process.state != 'running':
            self._process.send_input_result(event.request_id, '当前任务没有启用前台操作')
            return
        if event.request_id in self._input_requests:
            raise ValueError('重复的前台输入请求')
        self._input_requests.add(event.request_id)
        epoch = self._input_epoch

        def done(error):
            if epoch != self._input_epoch or self._process.state != 'running':
                return
            if isinstance(error, InterruptedError):
                # A stop message interrupts the SDK wait without turning this
                # protection into a RuntimeError in the child process.
                self._input_interruption = str(error)
                descriptor = self._require_active_descriptor()
                self._process.stop()
                self._presentation.set_running(
                    True, descriptor.key, '停止中', descriptor.name, str(error))
                self.log_message.emit(f'前台操作已中断：{error}')
                if str(error) in ('目标窗口已失去焦点，已停止前台操作', '目标位置被其他窗口遮挡，已取消点击'):
                    self._report_task_error(str(error))
                else:
                    self._pet.show_notification(str(error))
                return
            try:
                self._process.send_input_result(event.request_id, None if error is None else str(error))
            except Exception as channel_error:
                try:
                    self._report_task_error(f'前台操作结果发送失败：{channel_error}')
                finally:
                    self._process.stop()
                return

        try:
            if self._run_input_mode == 'background_smooth':
                self._pet.start_foreground_input(event, done, background=True)
            else:
                self._pet.start_foreground_input(event, done)
        except Exception as error:
            done(error)

    @QtCore.Slot(str)
    def _handle_process_state(self, state: str) -> None:
        if state in {'stopping', 'idle'}:
            self._pet.release_control()
        if state == "starting":
            self._task_completed_successfully = False
            self._pet.set_task_state("thinking")
        elif state == "running":
            # QProcess.started only confirms process creation; the skill
            # reports its own activity through status and action events.
            self._presentation.set_runtime_enabled(True)
        elif state == "stopping":
            self._pet.cancel_foreground_input()
            self._pet.hide_hint(return_home=True)
            self._task_completed_successfully = False
            self._pet.set_task_state("idle")
            self._presentation.set_runtime_enabled(False)
        elif state == "idle":
            self._pet.cancel_foreground_input(reason='任务已结束')
            self._input_epoch += 1
            self._pet.set_task_state("complete" if self._task_completed_successfully else "idle")
            self._task_completed_successfully = False
            self._pet.hide_hint(return_home=True)
            self._active_descriptor = None
            self._pending_parameters = None
            self._presentation.set_runtime_parameters((), {})

    @QtCore.Slot(str)
    def _handle_process_error(self, message: str) -> None:
        self._pet.release_control()
        self._pet.cancel_foreground_input(reason='任务执行失败')
        self._pet.hide_hint(return_home=True)
        self._task_completed_successfully = False
        self._pet.set_task_state("idle")
        descriptor = self._active_descriptor
        task_id = descriptor.key if descriptor is not None else ""
        task_name = descriptor.name if descriptor is not None else "任务"
        self._presentation.set_running(False, task_id, "失败", task_name, message)
        self._report_task_error(message)

    def _report_task_error(self, message: str, *, detail: str = "") -> None:
        # Let the error receiver snapshot the failed run before idle clears it.
        descriptor = self._active_descriptor
        if descriptor is not None:
            self.failure_context = (descriptor, self._active_options, detail)
        try:
            self.error.emit(message)
        finally:
            self.failure_context = None

    @QtCore.Slot(bool)
    def _handle_pet_visibility(self, visible: bool) -> None:
        self._pet_visible = bool(visible)
        if not visible and self._presentation.can_close_on_outside_click:
            self._presentation.close_bubbles()
        self._presentation.set_pet_visible(bool(visible))
        self._sync_mouse_watcher()

    @QtCore.Slot(str)
    def _handle_control_revoked(self, reason: str) -> None:
        if self._process.state in {'starting', 'running'}:
            self.log_message.emit(reason)
            self.stop_task()

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
            and not self._pet.foreground_active
            and not self._pet.control_active
            and not self._context_menu_open
            and (
                self._pet.mouse_point_visible
                or self._presentation.can_close_on_outside_click
                or (self._chat_button is not None and self._chat_button.isVisible())
            )
        )
        try:
            self._mouse.set_enabled(enabled)
        except Exception as exc:
            if self._pet.mouse_point_visible:
                self._pet.set_mouse_point_visible(False)
            self.error.emit(str(exc))

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self._pet.point_overlay and event.type() in (QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Hide):
            self._record_hint_state()
        if watched is self._chat_button and event.type() in (QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Hide):
            self._sync_mouse_watcher()
        return super().eventFilter(watched, event)

    @QtCore.Slot(int, int)
    def _handle_mouse_pressed(self, x: int, y: int) -> None:
        if self._pet.foreground_active or self._pet.control_active:
            self._ignore_mouse_sequence = True
            return
        point = QtCore.QPoint(x, y)
        bubble_hit = self._presentation.is_bubble_hit(point)
        speech = self._pet.speech_bubble
        bubble_hit = bubble_hit or (speech.isVisible()
                                   and not speech.testAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                                   and speech.geometry().contains(point))
        if self._chat_button is not None:
            bubble_hit = bubble_hit or (self._chat_button.isVisible()
                                       and self._chat_button.geometry().contains(point))
        pet_hit = self._pet.is_pet_hit_at(point)
        if not bubble_hit and not pet_hit:
            if self._presentation.can_close_on_outside_click:
                self._presentation.handle_outside_click(point)
            self.outside_clicked.emit()
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
