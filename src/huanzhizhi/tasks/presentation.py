"""Task-list and run-status bubbles anchored around the desktop pet."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6 import QtCore, QtGui

from huanzhizhi.pet.bubble_layout import BubbleLayoutManager
from huanzhizhi.pet.task_bubble import TaskBubbleWindow
from huanzhizhi.pet.views import StatusStrip


TASK_BUBBLE_HIDE_MS = 20_000
TASK_RESULT_HIDE_MS = 3_000
TASK_BUBBLE_POP_MS = 160
TASK_BUBBLE_POP_STAGGER_MS = 28
MAX_VISIBLE_TASKS = 10


@dataclass(frozen=True, slots=True)
class TaskListItem:
    id: str
    name: str
    description: str = ""
    presentation: str = "persistent"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("任务 ID 不能为空")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("任务名称不能为空")
        if not isinstance(self.description, str):
            raise TypeError("任务说明必须是字符串")
        if self.presentation not in {"persistent", "launch_only"}:
            raise ValueError("任务展示模式无效")


class TaskPresentationCoordinator(QtCore.QObject):
    start_task_requested = QtCore.Signal(str)
    stop_task_requested = QtCore.Signal()
    bubbles_changed = QtCore.Signal()
    status_changed = QtCore.Signal(str, str, str, bool)
    parameters_requested = QtCore.Signal(str)

    def __init__(
        self,
        *,
        pet_rect_provider: Callable[[], QtCore.QRect],
        display_scale_provider: Callable[[], float],
        speech_rect_provider: Callable[[], QtCore.QRect | None],
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        providers = {
            "pet_rect_provider": pet_rect_provider,
            "display_scale_provider": display_scale_provider,
            "speech_rect_provider": speech_rect_provider,
        }
        for name, provider in providers.items():
            if not callable(provider):
                raise TypeError(f"{name} 必须可调用")
        self._pet_rect_provider = pet_rect_provider
        self._display_scale_provider = display_scale_provider
        self._speech_rect_provider = speech_rect_provider

        self._tasks: tuple[TaskListItem, ...] = ()
        self._task_preferences: dict[str, dict] = {}
        self._bubbles: list[TaskBubbleWindow] = []
        self._mode = ""
        self._selected_id = ""
        self._running = False
        self._status_title = "未开始"
        self._task_name = ""
        self._detail = "等待启动"
        self._state_revision = 0
        self._closed = False
        self._display_scale_override: float | None = None
        self._result_pending = False
        self._pet_visible = True
        self._strip = StatusStrip()
        self._strip.details_requested.connect(self.handle_pet_clicked)
        self._strip.stop_requested.connect(self.stop_task_requested)

        self._auto_hide_timer = QtCore.QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._hide_transient_bubbles)
        self._result_hide_timer = QtCore.QTimer(self)
        self._result_hide_timer.setSingleShot(True)
        self._result_hide_timer.timeout.connect(self._hide_result)

    @property
    def has_bubbles(self) -> bool:
        return bool(self._bubbles)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def selected_id(self) -> str:
        return self._selected_id

    @property
    def status(self) -> tuple[str, str, str]:
        return self._status_title, self._task_name, self._detail

    @property
    def can_close_on_outside_click(self) -> bool:
        return bool(self._bubbles) and self._mode in {"list", "check"}

    def is_bubble_hit(self, global_point: QtCore.QPoint) -> bool:
        self._ensure_open()
        if not isinstance(global_point, QtCore.QPoint):
            raise TypeError("全局点击位置必须是 QPoint")
        return (self._strip.isVisible() and self._strip.geometry().contains(global_point)) or any(
            bubble.isVisible() and bubble.geometry().contains(global_point)
            for bubble in self._bubbles
        )

    def set_tasks(self, items: Iterable[TaskListItem]) -> None:
        self._ensure_open()
        if isinstance(items, (str, bytes)):
            raise TypeError("任务列表必须是 TaskListItem 的可迭代对象")
        try:
            values = tuple(items)
        except TypeError as error:
            raise TypeError("任务列表必须可迭代") from error
        if any(not isinstance(item, TaskListItem) for item in values):
            raise TypeError("任务列表只能包含 TaskListItem")
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("任务列表包含重复 ID")
        self._tasks = values
        if self._bubbles and not self._running:
            if self._mode == "list" or (
                self._mode == "check" and self._selected_id not in ids
            ):
                self._show_task_list()
            elif self._mode == "status":
                self._set_status_content(self._bubbles[0])
                self.reposition()
            elif self._mode == "check":
                self._show_check_bubble(self._selected_id)

    def set_task_preferences(self, preferences: dict[str, dict]) -> None:
        self._ensure_open()
        self._task_preferences = {task_id: dict(value) for task_id, value in preferences.items()}
        self._sync_strip()
        if self._mode == "status" and self._bubbles:
            self._set_status_content(self._bubbles[0])
            self.reposition()

    def set_running(
        self,
        running: bool,
        selected_id: str = "",
        status_title: str = "未开始",
        task_name: str = "",
        detail: str = "等待启动",
    ) -> None:
        self._ensure_open()
        if not isinstance(running, bool):
            raise TypeError("运行状态必须是布尔值")
        for name, value in (
            ("selected_id", selected_id),
            ("status_title", status_title),
            ("task_name", task_name),
            ("detail", detail),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} 必须是字符串")
        if not status_title.strip():
            raise ValueError("状态标题不能为空")
        if not detail.strip():
            raise ValueError("状态详情不能为空")

        started = running and not self._running
        finished = not running and self._running
        self._running = running
        if started:
            self._result_hide_timer.stop()
            self._result_pending = False
            self._clear_bubbles()
        elif finished:
            self._result_pending = True
            self._result_hide_timer.start(TASK_RESULT_HIDE_MS)
        self._selected_id = selected_id
        self._status_title = status_title
        self._task_name = task_name
        self._detail = detail
        self._state_revision += 1
        self.status_changed.emit(
            self._status_title,
            self._task_name,
            self._detail,
            self._running,
        )
        self._sync_strip()
        self.reposition()
        if self._mode == "status" and self._bubbles:
            self._set_status_content(self._bubbles[0])
            self.reposition()
            self._auto_hide_timer.stop()

    def handle_pet_clicked(self) -> None:
        self._ensure_open()
        if self._running or self._result_pending:
            if self._mode == "status" and self._bubbles:
                self._clear_bubbles()
                if not self._running:
                    self._result_hide_timer.stop()
                    self._result_pending = False
                    self._sync_strip()
            else:
                self._show_status_bubble()
        else:
            self._show_task_list()

    def set_pet_visible(self, visible: bool) -> None:
        self._pet_visible = visible
        for bubble in self._bubbles:
            bubble.setVisible(visible)
        self._sync_strip()
        if visible:
            self.reposition()

    def _sync_strip(self) -> None:
        active = self._running or self._result_pending
        if active:
            quiet = self._running and self._status_title != "停止中" and not self._task_preferences.get(self._selected_id, {}).get("show_logs", True)
            self._strip.set_status(self._resolved_task_name(), "运行中" if quiet else self._status_title,
                                   self._running and self._status_title != "停止中")
        self._strip.setVisible(active and self._pet_visible)

    def reposition(self) -> None:
        self._ensure_open()
        pet_rect = self._provided_pet_rect()
        screen = QtGui.QGuiApplication.screenAt(pet_rect.center()) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = min(max(pet_rect.center().x() - self._strip.width() // 2, area.left()), area.right() - self._strip.width() + 1)
            y = min(max(pet_rect.bottom() + 6, area.top()), area.bottom() - self._strip.height() + 1)
            self._strip.move(x, y)
        if not self._bubbles:
            return
        pet_rect = self._provided_pet_rect()
        display_scale = self._provided_display_scale()
        speech_rect = self._provided_speech_rect()
        avoided = (speech_rect,) if speech_rect is not None else ()
        for bubble in self._bubbles:
            bubble.set_display_scale(display_scale)

        if self._mode == "list" and self._tasks:
            BubbleLayoutManager.position_task_side_columns(
                self._bubbles,
                pet_rect,
                display_scale,
            )
        else:
            BubbleLayoutManager.position_task_stack(
                self._bubbles,
                pet_rect,
                display_scale,
                avoid_rects=avoided,
            )

    def close_bubbles(self) -> None:
        self._ensure_open()
        self._clear_bubbles()

    def handle_outside_click(self, global_point: QtCore.QPoint) -> bool:
        """Close transient bubbles unless the point hits one of them.

        The host must exclude desktop-pet hits before calling this method,
        because alpha-mask pet hit testing belongs to the pet window.
        """
        self._ensure_open()
        if not isinstance(global_point, QtCore.QPoint):
            raise TypeError("全局点击位置必须是 QPoint")
        if not self.can_close_on_outside_click:
            return False
        if any(
            bubble.isVisible() and bubble.geometry().contains(global_point)
            for bubble in self._bubbles
        ):
            return False
        self._clear_bubbles()
        return True

    def set_display_scale(self, scale: float) -> None:
        self._ensure_open()
        scale = self._validate_display_scale(scale)
        self._display_scale_override = scale
        for bubble in self._bubbles:
            bubble.set_display_scale(scale)
        self.reposition()

    def close(self) -> None:
        if self._closed:
            return
        self._clear_bubbles()
        self._result_hide_timer.stop()
        self._strip.close()
        self._closed = True

    def _show_task_list(self) -> None:
        self._clear_bubbles(reset_mode=False)
        self._mode = "list"
        self._selected_id = ""
        if not self._tasks:
            bubble = self._new_bubble()
            bubble.set_content(
                "没有可显示的任务",
                "可在设置中开启任务显示。",
                [],
            )
            self._bubbles.append(bubble)
        else:
            for index, item in enumerate(self._tasks[:MAX_VISIBLE_TASKS]):
                bubble = self._new_bubble()
                bubble.set_content(
                    item.name,
                    click_key=f"select:{item.id}",
                    layout_mode="compact",
                    accent_index=index,
                )
                self._bubbles.append(bubble)
        self.reposition()
        self._show_bubbles(stagger_ms=TASK_BUBBLE_POP_STAGGER_MS)
        self._auto_hide_timer.start(TASK_BUBBLE_HIDE_MS)
        self.bubbles_changed.emit()

    def _show_check_bubble(self, task_id: str) -> None:
        item = self._task_by_id(task_id)
        self._selected_id = item.id
        self._clear_bubbles(reset_mode=False)
        self._mode = "check"
        bubble = self._new_bubble()
        bubble.set_content(
            item.name,
            item.description or "这个任务没有提供说明。",
            [
                {"key": "start_task", "label": "开始执行", "kind": "primary"},
                {"key": "parameters", "label": "参数设置"},
            ],
        )
        self._bubbles.append(bubble)
        self.reposition()
        self._show_bubbles()
        self._auto_hide_timer.stop()
        self.bubbles_changed.emit()

    def _show_status_bubble(self) -> None:
        self._clear_bubbles(reset_mode=False)
        self._mode = "status"
        bubble = self._new_bubble()
        self._set_status_content(bubble)
        self._bubbles.append(bubble)
        self.reposition()
        self._show_bubbles()
        self._auto_hide_timer.stop()
        self.bubbles_changed.emit()

    def _set_status_content(self, bubble: TaskBubbleWindow) -> None:
        task_name = self._resolved_task_name()
        buttons: list[dict]
        if self._running:
            stopping = self._status_title == "停止中"
            buttons = [{"key": "stop_task", "label": "停止中…" if stopping else "停止任务",
                        "kind": "danger", "enabled": not stopping}]
        else:
            buttons = [{"key": "close_details", "label": "收起结果"}]
        show_logs = self._task_preferences.get(self._selected_id, {}).get("show_logs", True)
        quiet = self._running and self._status_title != "停止中" and not show_logs
        bubble.set_content(
            "运行中" if quiet else self._status_title,
            "" if quiet else f"{task_name}\n{self._detail}",
            buttons,
        )

    def _new_bubble(self) -> TaskBubbleWindow:
        bubble = TaskBubbleWindow(self._provided_display_scale())
        bubble.clicked.connect(self._handle_bubble_clicked)
        return bubble

    def _handle_bubble_clicked(self, key: str) -> None:
        if key.startswith("select:"):
            self._show_check_bubble(key.split(":", 1)[1])
        elif key == "parameters":
            self.parameters_requested.emit(self._selected_id)
        elif key == "close_details":
            self.handle_pet_clicked()
        elif key == "start_task":
            if not self._selected_id:
                raise RuntimeError("没有选中的任务，无法请求启动")
            revision = self._state_revision
            selected_id = self._selected_id
            selected = self._task_by_id(selected_id)
            selected_name = selected.name
            self.start_task_requested.emit(selected_id)
            if selected.presentation == "launch_only":
                self._clear_bubbles()
                return
            if self._state_revision == revision:
                self._running = True
                self._status_title = "运行中"
                self._task_name = selected_name
                self._detail = "任务运行中"
                self._state_revision += 1
            self._clear_bubbles()
            self._sync_strip()
            self.reposition()
        elif key == "stop_task":
            revision = self._state_revision
            self.stop_task_requested.emit()
            if self._state_revision == revision:
                self._running = False
                self._status_title = "未开始"
                self._detail = "等待启动"
                self._state_revision += 1
            self._show_status_bubble()
        else:
            raise ValueError(f"不支持的任务气泡动作：{key}")

    def _show_bubbles(self, stagger_ms: int = 0) -> None:
        if stagger_ms < 0:
            raise ValueError("气泡错峰时间不能小于零")
        for index, bubble in enumerate(self._bubbles):
            if self._pet_visible:
                bubble.show_pop_in(delay_ms=stagger_ms * index)

    def _hide_transient_bubbles(self) -> None:
        if self._mode == "list" and self._bubbles and not self._running:
            self._clear_bubbles()

    def _hide_result(self) -> None:
        if self._running or not self._result_pending:
            return
        self._result_pending = False
        if self._mode == "status":
            self._clear_bubbles()
        self._sync_strip()

    def _clear_bubbles(self, *, reset_mode: bool = True) -> None:
        had_bubbles = bool(self._bubbles)
        self._auto_hide_timer.stop()
        for bubble in self._bubbles:
            bubble.hide()
            bubble.deleteLater()
        self._bubbles = []
        if reset_mode:
            self._mode = ""
        if had_bubbles:
            self.bubbles_changed.emit()

    def _task_by_id(self, task_id: str) -> TaskListItem:
        for item in self._tasks:
            if item.id == task_id:
                return item
        raise ValueError(f"任务列表中不存在任务：{task_id}")

    def _resolved_task_name(self) -> str:
        preferences = self._task_preferences.get(self._selected_id)
        if preferences is not None:
            return preferences["name"]
        if self._task_name.strip():
            return self._task_name
        if self._selected_id:
            for item in self._tasks:
                if item.id == self._selected_id:
                    return item.name
            return self._selected_id
        return "任务"

    def _provided_pet_rect(self) -> QtCore.QRect:
        rect = self._pet_rect_provider()
        if not isinstance(rect, QtCore.QRect) or not rect.isValid():
            raise ValueError("pet_rect_provider 必须返回有效的 QRect")
        return QtCore.QRect(rect)

    def _provided_display_scale(self) -> float:
        if self._display_scale_override is not None:
            return self._display_scale_override
        return self._validate_display_scale(self._display_scale_provider())

    @staticmethod
    def _validate_display_scale(scale: float) -> float:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("display_scale_provider 必须返回数字")
        scale = float(scale)
        if scale <= 0:
            raise ValueError("桌宠显示倍率必须大于零")
        return scale

    def _provided_speech_rect(self) -> QtCore.QRect | None:
        rect = self._speech_rect_provider()
        if rect is None:
            return None
        if not isinstance(rect, QtCore.QRect) or not rect.isValid():
            raise ValueError("speech_rect_provider 必须返回有效的 QRect 或 None")
        return QtCore.QRect(rect)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("任务气泡协调器已经关闭")


__all__ = ("TaskListItem", "TaskPresentationCoordinator")
