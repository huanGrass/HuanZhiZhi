from __future__ import annotations

import sys
import traceback
import json
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.logging import RunLogComponent, RunLogError
from huanzhizhi.pet import PetComponent
from huanzhizhi.pet.context_menu import PetContextMenu
from huanzhizhi.pet.point_overlay import PointOverlayWindow
from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.pet.window import PetWindow
from huanzhizhi.settings import DesktopPetSettingsComponent, SettingsComponent
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

        asset_root = Path(__file__).parent / "assets"
        pet_image = asset_root / "pet_static_tablet.png"
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
        frozen = getattr(sys, "frozen", False)
        project_root = project_root or (Path(sys.executable).resolve().parent if frozen
                                        else Path(__file__).resolve().parents[2])

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
        display_scale = self.settings.get_display_scale()
        pet_window = PetWindow(pet_image, display_scale)
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
        )
        self.tasks = TaskHostComponent(self.pet, project_root / "tasks", settings=self.settings)
        self.pet_settings = DesktopPetSettingsComponent(self.pet, self.run_log.path, tasks=self.tasks)
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
        self.pet.position_changed.connect(self._save_pet_position)
        self.pet.scale_changed.connect(self._save_pet_scale)
        self.pet.mouse_point_visible_changed.connect(self._save_mouse_point_visible)
        self.pet.fatal_error.connect(self.shutdown)
        self.pet.runtime_error.connect(self._show_runtime_error)
        self.pet.show_settings_requested.connect(self.pet_settings.show)
        self.pet.quit_requested.connect(self.shutdown)
        self.pet.restart_requested.connect(self.restart)
        self.tasks.error.connect(self._show_runtime_error)
        self.tasks.log_message.connect(self._append_log)
        self.pet_settings.error.connect(self._show_runtime_error)
        self._app.aboutToQuit.connect(self._close_components)
        self._append_log(f"日志文件：{self.run_log.path}")

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

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
        QtCore.qInstallMessageHandler(self._previous_qt_message_handler)
        sys.excepthook = self._previous_excepthook
        self.tray.close()
        self.pet_settings.close()
        self.tasks.close()
        self.pet.close()
        self.settings.close()
        self.run_log.close()

    @QtCore.Slot(QtCore.QPoint)
    def _save_pet_position(self, position: QtCore.QPoint) -> None:
        self.settings.set_pet_position(position)
        self.settings.sync()
        self._append_log(f"桌宠位置已更新：({position.x()}, {position.y()})")

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


    @QtCore.Slot(str)
    def _show_runtime_error(self, message: str) -> None:
        try:
            self._append_log(message)
        except RunLogError as log_error:
            message = f"{message}\n\n同时无法写入运行日志：{log_error}"
        try:
            self.pet.show_notification("运行异常，请查看运行日志")
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
