from __future__ import annotations

import sys
import traceback
import json
from collections.abc import Callable
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.logging import RunLogComponent, RunLogError
from huanzhizhi.pet import PetComponent
from huanzhizhi.pet.context_menu import PetContextMenu
from huanzhizhi.pet.point_overlay import PointOverlayWindow
from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.pet.window import PetWindow
from huanzhizhi.pet.chat_window import ChatWindow
from huanzhizhi.platform.display_recovery import DisplayRecovery
from huanzhizhi.settings import DesktopPetSettingsComponent, SettingsComponent, SettingsError
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.tray import TrayComponent


class _QtMessageBridge(QtCore.QObject):
    message_received = QtCore.Signal(str)

    _LEVELS = {
        QtCore.QtMsgType.QtWarningMsg: "WARNING",
        QtCore.QtMsgType.QtCriticalMsg: "CRITICAL",
        QtCore.QtMsgType.QtFatalMsg: "FATAL",
    }

    @classmethod
    def format_message(
        cls,
        message_type: QtCore.QtMsgType,
        context: QtCore.QMessageLogContext,
        message: str,
    ) -> str:
        level = cls._LEVELS.get(message_type, "UNKNOWN")
        location = ""
        if context.file:
            location = f" ({context.file}:{context.line})"
        return f"Qt {level}{location}: {message}"

    @QtCore.Slot(QtCore.QtMsgType, QtCore.QMessageLogContext, str)
    def handle(
        self,
        message_type: QtCore.QtMsgType,
        context: QtCore.QMessageLogContext,
        message: str,
    ) -> None:
        if message_type in self._LEVELS:
            self.message_received.emit(self.format_message(message_type, context, message))


class HuanZhiZhiApplication:
    """创建顶层组件，并只负责连接它们的公开接口。"""

    def __init__(self, app: QtWidgets.QApplication, *, project_root: Path | None = None, settings_path: Path | None = None) -> None:
        self._app = app
        self._shutting_down = False
        self._components_closed = False
        self._restart_requested = False
        self._creation_button_retained = False
        self._pending_repair: tuple[str, str] | None = None
        self._pending_retry: tuple[str, dict | None] | None = None

        asset_root = Path(__file__).parent / "assets"
        app_icon = asset_root / "app_icon.png"
        settings_root = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.AppConfigLocation
        )
        if not settings_root:
            raise RuntimeError("无法确定幻之之的设置目录")
        migrate_initial_settings = settings_path is None
        settings_path = settings_path or Path(settings_root) / "settings.ini"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if migrate_initial_settings and not settings_path.exists():
            # Preserve settings from the previous application name without changing the original.
            legacy_path = Path(settings_root).parent / "HuanKeZhi" / "settings.ini"
            if legacy_path.is_file() and legacy_path != settings_path:
                settings_path.write_bytes(legacy_path.read_bytes())
        migrate_initial_settings = migrate_initial_settings and not settings_path.exists()
        project_root = project_root or Path(__file__).resolve().parents[2]

        self._app.setWindowIcon(QtGui.QIcon(str(app_icon)))
        self.run_log = RunLogComponent(project_root / "logs")
        self._previous_excepthook = sys.excepthook
        sys.excepthook = self._handle_uncaught_exception
        self.settings = SettingsComponent(settings_path)
        if migrate_initial_settings:
            previous = QtCore.QSettings("HuanKeZhi", "Desktop")
            if previous.contains("scale"):
                self.settings.set_display_scale(float(previous.value("scale")))
            if previous.contains("position"):
                self.settings.set_pet_position(previous.value("position"))
            for key in previous.allKeys():
                parts = key.split("/")
                if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "parameters":
                    self.settings.set_task_parameters(parts[1], json.loads(previous.value(key)))
            self.settings.sync()
        self.settings.migrate_task_ids()
        display_scale = self.settings.get_display_scale()
        pet_window = PetWindow()
        pet_speech = SpeechBubbleWindow(display_scale)
        pet_point = PointOverlayWindow(
            display_scale=display_scale,
        )
        self.pet = PetComponent(
            pet_window,
            pet_speech,
            pet_point,
            PetContextMenu,
            display_scale=display_scale,
            initial_position=self.settings.get_pet_position(),
            mouse_point_visible=self.settings.get_mouse_point_visible(),
            theme=self.settings.get_pet_theme(),
        )
        self.chat = ChatWindow(settings=self.settings)
        self.settings.ai_work_active = lambda: self.chat._exploration is not None
        self.tasks = TaskHostComponent(self.pet, project_root / "tasks", settings=self.settings,
                                       chat_button=self.chat.toggle_button)
        self.chat.tasks_root = project_root / 'tasks'
        self.chat.can_start = lambda: self.tasks.process.state == 'idle'
        self.chat.exploration_active.connect(lambda active: setattr(self.tasks, 'external_work_active', active))
        self.chat.skill_saved.connect(self._skill_saved)
        self.chat.log_message.connect(self._append_log)
        self.chat.ai_unavailable.connect(self._show_ai_unavailable)
        self.chat.click_point.connect(self.tasks.show_click_point)
        self.chat.hint_requested.connect(self.tasks.show_hint)
        self.chat.foreground_input_requested.connect(self._start_agent_input)
        self.chat.foreground_input_cancelled.connect(self.pet.cancel_foreground_input)
        self.chat.foreground_input_stop_requested.connect(self.pet.cancel_foreground_input)
        self.chat.tasks_requested.connect(self._show_tasks_after_exploration)
        self.tasks.presentation.edit_skill_requested.connect(self._edit_skill)
        self.tasks.presentation.set_runtime_transparency(self.settings.get_runtime_transparency())
        self.tasks.presentation.runtime_transparency_preference_changed.connect(self._save_runtime_transparency)
        self.pet_settings = DesktopPetSettingsComponent(self.pet, self.run_log.path, tasks=self.tasks,
                                                       settings=self.settings)
        self.tasks.presentation.promote_task_requested.connect(self.pet_settings.promote_task)
        self.pet_settings.ai_enabled_changed.connect(self._ai_enabled_changed)
        self.pet_settings.ai_options_changed.connect(self.chat.refresh_model_name)
        self.chat.exploration_active.connect(self.pet_settings.refresh_ai_availability)
        self.tray = TrayComponent(app_icon)
        self._qt_message_bridge = _QtMessageBridge()
        self._qt_message_bridge.message_received.connect(self._append_log)
        self._previous_qt_message_handler = QtCore.qInstallMessageHandler(
            self._qt_message_bridge.handle
        )

        self.tray.show_pet_requested.connect(self.pet.show)
        self.tray.hide_pet_requested.connect(self.pet.hide)
        self.tray.quit_requested.connect(self.shutdown)
        self.tray.show_settings_requested.connect(self.pet_settings.show)
        self.pet.visibility_changed.connect(self.tray.set_pet_visible)
        self.pet.visibility_changed.connect(self._sync_chat_visibility)
        self.pet.speech_bubble.visibility_changed.connect(self._sync_chat_visibility)
        self.tasks.presentation.bubbles_changed.connect(self._sync_chat_visibility)
        self.tasks.presentation.status_changed.connect(self._sync_chat_visibility)
        self.tasks.outside_clicked.connect(self._dismiss_retained_creation_button)
        self.chat.expanded_changed.connect(self._handle_chat_expanded)
        self.pet.clicked.connect(self._handle_pet_clicked)
        self.chat.drag_started.connect(self.pet.window.begin_drag)
        self.chat.drag_moved.connect(self.pet.window.drag_to)
        self.chat.drag_finished.connect(self.pet.window.end_drag)
        self.pet.position_changing.connect(self._reposition_chat)
        self.pet.position_changed.connect(self._reposition_chat)
        self.pet.scale_changed.connect(self._reposition_chat)
        self.pet.position_changed.connect(self._save_pet_position)
        self.pet.scale_changed.connect(self._save_pet_scale)
        self.pet.mouse_point_visible_changed.connect(self._save_mouse_point_visible)
        self.pet.theme_changed.connect(self._save_pet_theme)
        self.pet.fatal_error.connect(self.shutdown)
        self.pet.runtime_error.connect(self._show_runtime_error)
        self.pet.show_settings_requested.connect(self.pet_settings.show)
        self.pet.quit_requested.connect(self.shutdown)
        self.pet.restart_requested.connect(self.restart)
        self.tasks.error.connect(self._show_task_error)
        self.tasks.process.state_changed.connect(self._continue_skill_repair)
        self.tasks.log_message.connect(self._append_log)
        self.pet_settings.error.connect(self._show_runtime_error)
        self._app.aboutToQuit.connect(self._close_components)
        self._display_recovery = DisplayRecovery(
            self.pet.window, self.pet.restore_display, self._append_log,
        )
        self._append_log(f"日志文件：{self.run_log.path}")

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    def _start_agent_input(self, request, reply) -> None:
        worker = self.chat._exploration

        def finished(error):
            try:
                reply(error)
            except Exception as channel_error:
                try:
                    self._show_runtime_error(f'前台操作结果发送失败：{channel_error}')
                finally:
                    if self.chat._exploration is worker:
                        self.chat._stop_exploration()
                return
            if isinstance(error, InterruptedError) and self.chat._exploration is worker:
                self.chat._stop_exploration()

        if (self._shutting_down or worker is None or self.chat._exploration_stopping
                or self.tasks.process.state != 'idle'
                or self.settings.get_input_mode() not in ('foreground', 'background_smooth')):
            finished(InterruptedError('当前不能开始前台操作'))
            return
        try:
            if self.settings.get_input_mode() == 'background_smooth':
                self.pet.start_foreground_input(request, finished, background=True)
            else:
                self.pet.start_foreground_input(request, finished)
        except Exception as error:
            finished(error)

    def start(self) -> None:
        self.tray.start()
        self.pet.show()
        QtCore.QTimer.singleShot(
            0,
            lambda: self.pet.show_message("来了", 1000, action="talk_v"),
        )

    def restart(self) -> None:
        if self._shutting_down:
            return
        self._restart_requested = True
        self.shutdown()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._close_components()
        self._app.quit()

    def _close_components(self) -> None:
        if self._components_closed:
            return
        self._components_closed = True
        self._display_recovery.close()
        QtCore.qInstallMessageHandler(self._previous_qt_message_handler)
        sys.excepthook = self._previous_excepthook
        self.tray.close()
        self.pet_settings.close()
        self.tasks.close()
        self.chat.stop_exploration_and_wait()
        self.chat.hide_for_pet()
        self.pet.close()
        self.settings.close()
        self.run_log.close()

    def _sync_chat_visibility(self, *_args) -> None:
        if self._components_closed:
            return
        self.tasks.presentation.set_chat_open(self.chat.current_work is not None)
        if self.tasks.presentation.running:
            self._creation_button_retained = False
            self.chat.hide_for_pet()
            return
        if not self.settings.get_ai_enabled() and self.pet.speech_bubble.isVisible():
            self.chat.hide_for_pet()
            return
        pet_visible = self.pet.window.isVisible()
        has_bubbles = self.tasks.presentation.has_bubbles
        if not pet_visible or has_bubbles:
            self._creation_button_retained = False
        if pet_visible and (self.chat.expanded or has_bubbles or self._creation_button_retained
                            or self.chat.current_work == 'conversation'):
            self.chat.open_for_pet(self.pet.bubble_geometry())
            if has_bubbles:
                self.chat.conversation.set_visible(False)
        else:
            self.chat.hide_for_pet()

    def _handle_chat_expanded(self, expanded: bool) -> None:
        self._creation_button_retained = not expanded
        self._sync_chat_visibility()

    def _handle_pet_clicked(self) -> None:
        if not self.settings.get_ai_enabled():
            self.pet.speech_bubble.hide_bubble()
        if self.chat.current_work is not None:
            self.chat.toggle_current_work()
            self._sync_chat_visibility()
        else:
            self.tasks.handle_pet_clicked()

    def _skill_saved(self, directory: str) -> None:
        self._append_log('技能已保存：'+directory)
        self.tasks.reload_tasks()

    def _edit_skill(self, task_id: str, *, prompt: str = '') -> None:
        try:
            task = self.tasks.catalog.get(task_id)
            if task.script_path.suffix.lower() != '.py':
                raise ValueError('这个技能只有可执行文件，没有可修改的 Python 源码。')
            self.chat.edit_skill(task.script_path.parent, name=task.name, prompt=prompt)
            self._sync_chat_visibility()
        except SettingsError as error:
            self._show_ai_unavailable(str(error))
        except Exception as error:
            self._show_runtime_error(str(error))

    def _show_task_error(self, message: str) -> None:
        context = self.tasks.failure_context
        on_repair = None
        # Task errors cross the process protocol as text. Match only known
        # window-state failures; invalid arguments and missing dependencies
        # must retain the diagnostic/repair action.
        error = message.removeprefix('任务失败：').removeprefix('任务启动失败：').removeprefix('前台输入失败：')
        window_unavailable = error.startswith((
            'foreground verification failed: expected ',
            'SetForegroundWindow returned ',
            'selected target window does not exist or is not visible: ',
            'selected target window does not match rule: ',
            'no window matches title and client size: ',
            'multiple windows match the target rule: ',
            'refreshed target window no longer matches rule: ',
            'window is still minimized after restore: ',
            'target window is minimized: ',
            'window does not exist: ',
            'enumerated hwnd no longer exists: ',
            'window is not visible: ',
            'window has no title: ',
            'window information is stale; refresh is required: ',
            'SetWindowPos returned ',
            'client resize verification failed: ',
            '目标窗口已失去焦点，已停止前台操作',
            '目标位置被其他窗口遮挡，已取消点击',
            '未找到目标 WeiqiGo.com 窗口，',
            '找到多个 WeiqiGo.com 窗口，',
        )) or (error.startswith('window ') and ' client size is not positive: ' in error)
        capture_unavailable = error.startswith(('client capture failed for ', 'WGC ')) and (
            error.endswith(('WGC did not deliver the requested frame within 2 seconds',
                            'WGC capture failed: WGC session closed'))
            or ('WGC capture failed: WGC frame ' in error and ' cannot contain client crop ' in error)
        )
        if context is not None and (window_unavailable or capture_unavailable):
            task, options, _ = context
            self._show_runtime_error(
                message, on_repair=lambda: self._retry_skill(task.key, options),
                notification_text='请打开技能对应窗口后重试', action_text='重试')
            return
        if context is not None and self.settings.get_ai_enabled():
            task, options, detail = context
            if task.script_path.suffix.lower() == '.py':
                prompt = (
                    f'请检查并修复技能“{task.name}”（ID：{task.id}）此次运行异常。\n'
                    '先尝试复现，并依据日志、测试或实际调用链确认根因；证据不足时只调查，'
                    '不要猜测性修改业务逻辑。复用现有实现，完成必要验证并说明结果。\n'
                    f'当次运行参数：{json.dumps(options, ensure_ascii=False, default=str)}\n'
                    f'异常信息：{message}\n运行详情：{detail}\n'
                    f'运行日志：{self.run_log.path}\n'
                )
                on_repair = lambda: self._repair_skill(task.key, prompt)
        self._show_runtime_error(message, on_repair=on_repair)

    def _retry_skill(self, task_id: str, options: dict | None) -> None:
        if self._shutting_down:
            return
        if self.tasks.process.state != 'idle':
            self._pending_retry = (task_id, options)
            self.tasks.process.stop()
            return
        try:
            self.tasks.start_task(task_id, options)
        except Exception as error:
            self._show_runtime_error(str(error))

    def _repair_skill(self, task_id: str, prompt: str) -> None:
        try:
            self.settings.require_ai_enabled()
        except SettingsError as error:
            self._show_ai_unavailable(str(error))
            return
        if self.tasks.process.state != 'idle':
            self._pending_repair = (task_id, prompt)
            self.tasks.process.stop()
        else:
            self._edit_skill(task_id, prompt=prompt)

    def _ai_enabled_changed(self, enabled: bool) -> None:
        if not enabled:
            self._pending_repair = None
            self.chat.leave_ai_work()
            self._creation_button_retained = False
        self.pet.speech_bubble.hide_bubble()
        self._sync_chat_visibility()

    def _show_ai_unavailable(self, message: str) -> None:
        self.chat.leave_ai_work()
        self._creation_button_retained = False
        self.tasks.presentation.close_bubbles()
        self.pet.show_notification(message, on_repair=self.pet_settings.show_ai_settings,
                                   action_text='前往 AI 设置')
        self._sync_chat_visibility()

    def _continue_skill_repair(self, state: str) -> None:
        if state == 'starting':
            self._pending_retry = None
            self.pet.speech_bubble.hide_bubble()
        if state == 'idle' and self._pending_retry is not None and not self._shutting_down:
            task_id, options = self._pending_retry
            self._pending_retry = None
            self._retry_skill(task_id, options)
        if state == 'idle' and self._pending_repair is not None and not self._shutting_down:
            task_id, prompt = self._pending_repair
            self._pending_repair = None
            self.pet.speech_bubble.hide_bubble()
            self._edit_skill(task_id, prompt=prompt)

    def _show_tasks_after_exploration(self) -> None:
        self._sync_chat_visibility()
        self.tasks.handle_pet_clicked()

    def _dismiss_retained_creation_button(self) -> None:
        if self._creation_button_retained and not self.chat.expanded:
            self._creation_button_retained = False
            self._sync_chat_visibility()

    def _reposition_chat(self, *_args) -> None:
        self.chat.follow_pet(self.pet.bubble_geometry())

    @QtCore.Slot(QtCore.QPoint)
    def _save_pet_position(self, position: QtCore.QPoint) -> None:
        self.settings.set_pet_position(position)
        self.settings.sync()
        self._append_log(f"桌宠位置已更新：({position.x()}, {position.y()})")

    def _save_pet_theme(self, theme: str) -> None:
        self.settings.set_pet_theme(theme)
        self.settings.sync()
        self._reposition_chat()
        self._append_log(f'桌宠主题已更新：{theme}')

    @QtCore.Slot(float, QtCore.QPoint)
    def _save_pet_scale(self, scale: float, position: QtCore.QPoint) -> None:
        self.settings.set_display_scale(scale)
        self.settings.set_pet_position(position)
        self.settings.sync()
        self._append_log(f"桌宠大小已调整：{scale:.2f}x")

    @QtCore.Slot(bool)
    def _save_mouse_point_visible(self, visible: bool) -> None:
        self.settings.set_mouse_point_visible(visible)
        self.settings.sync()

    @QtCore.Slot(int)
    def _save_runtime_transparency(self, transparency: int) -> None:
        self.settings.set_runtime_transparency(transparency)
        self.settings.sync()


    @QtCore.Slot(str)
    def _show_runtime_error(self, message: str, *, on_repair: Callable[[], None] | None = None,
                            notification_text: str | None = None,
                            action_text: str = '让 AI 检查并修复') -> None:
        if notification_text is None:
            reason = message.strip().removeprefix('任务失败：').removeprefix('任务启动失败：').removeprefix('前台输入失败：').strip()
            notification_text = reason if len(reason.splitlines()) == 1 and len(reason) <= 120 else '出现未知情况'
        try:
            self._append_log(message)
        except RunLogError as log_error:
            message = f"{message}\n\n同时无法写入运行日志：{log_error}"
        try:
            self.pet.show_notification(notification_text, on_repair=on_repair, action_text=action_text)
        except Exception as notification_error:
            try:
                self._append_log(f"异常提示气泡显示失败：{notification_error}")
            except RunLogError:
                pass

    @QtCore.Slot(str)
    def _append_log(self, message: str) -> None:
        self.pet_settings.append_log(self.run_log.append(message))

    def _handle_uncaught_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: object,
    ) -> None:
        """Record uncaught Python exceptions into the run log (no console on GUI start)."""
        try:
            lines = "".join(
                traceback.format_exception(exc_type, exc_value, exc_traceback)
            ).rstrip("\n")
            self.run_log.append(f"未捕获异常：\n{lines}")
        except Exception:
            pass
