"""Strict persistence for settings shared by HuanZhiZhi components."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PySide6 import QtCore


DEFAULT_DISPLAY_SCALE = 0.32
MIN_DISPLAY_SCALE = 0.19
MAX_DISPLAY_SCALE = 1.25

_PET_X_KEY = "desktopPetIdleX"
_PET_Y_KEY = "desktopPetIdleY"
_DISPLAY_SCALE_KEY = "desktopPetScale"
_MOUSE_POINT_VISIBLE_KEY = "desktopPetMouseClickVisible"
_SOUND_ENABLED_KEY = "desktopPetSoundEnabled"
_HIDDEN_TASK_IDS_KEY = "desktopPetHiddenTaskIds"
_TASK_PREFERENCES_KEY = "desktopPetTaskPreferences"
_INTEGER_PATTERN = re.compile(r"[+-]?\d+")


class SettingsError(ValueError):
    """Raised when persisted settings are incomplete or invalid."""


class SettingsComponent:
    """Read and write HuanZhiZhi settings in one explicitly selected INI file."""

    def __init__(self, ini_path: Path) -> None:
        if not isinstance(ini_path, Path):
            raise TypeError("ini_path must be a pathlib.Path")
        self._path = ini_path
        self._settings: QtCore.QSettings | None = QtCore.QSettings(
            str(ini_path), QtCore.QSettings.Format.IniFormat
        )

    @property
    def path(self) -> Path:
        return self._path

    def get_pet_position(self) -> QtCore.QPoint | None:
        settings = self._open_settings()
        has_x = settings.contains(_PET_X_KEY)
        has_y = settings.contains(_PET_Y_KEY)
        if not has_x and not has_y:
            return None
        if has_x != has_y:
            raise SettingsError("desktop pet position is incomplete: both X and Y are required")
        return QtCore.QPoint(
            self._read_integer(_PET_X_KEY),
            self._read_integer(_PET_Y_KEY),
        )

    def set_pet_position(self, position: QtCore.QPoint | None) -> None:
        settings = self._open_settings()
        if position is None:
            settings.remove(_PET_X_KEY)
            settings.remove(_PET_Y_KEY)
            return
        if not isinstance(position, QtCore.QPoint):
            raise TypeError("position must be a QPoint or None")
        settings.setValue(_PET_X_KEY, position.x())
        settings.setValue(_PET_Y_KEY, position.y())

    def get_display_scale(self) -> float:
        settings = self._open_settings()
        if not settings.contains(_DISPLAY_SCALE_KEY):
            return DEFAULT_DISPLAY_SCALE
        raw = settings.value(_DISPLAY_SCALE_KEY)
        if isinstance(raw, bool):
            raise SettingsError("desktop pet display scale must be a number")
        try:
            scale = float(raw)
        except (TypeError, ValueError) as error:
            raise SettingsError("desktop pet display scale must be a number") from error
        return self._validate_display_scale(scale)

    def set_display_scale(self, scale: float) -> None:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("scale must be a number")
        self._open_settings().setValue(
            _DISPLAY_SCALE_KEY, self._validate_display_scale(float(scale))
        )

    def get_mouse_point_visible(self) -> bool:
        return self._read_boolean(_MOUSE_POINT_VISIBLE_KEY, default=False)

    def set_mouse_point_visible(self, visible: bool) -> None:
        self._set_boolean(_MOUSE_POINT_VISIBLE_KEY, visible)

    def get_hidden_task_ids(self) -> tuple[str, ...]:
        raw = self._open_settings().value(_HIDDEN_TASK_IDS_KEY, "[]")
        try:
            values = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise SettingsError("hidden task IDs must be a JSON list of non-empty text") from error
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise SettingsError("hidden task IDs must be a JSON list of non-empty text")
        return tuple(values)

    def get_sound_enabled(self) -> bool:
        return self._read_boolean(_SOUND_ENABLED_KEY, default=True)

    def set_sound_enabled(self, enabled: bool) -> None:
        self._set_boolean(_SOUND_ENABLED_KEY, enabled)

    def set_hidden_task_ids(self, task_ids: tuple[str, ...]) -> None:
        if not isinstance(task_ids, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in task_ids
        ):
            raise TypeError("hidden task IDs must be a tuple of non-empty text")
        self._open_settings().setValue(
            _HIDDEN_TASK_IDS_KEY, json.dumps(sorted(set(task_ids)), ensure_ascii=False)
        )

    def get_task_preferences(self) -> dict[str, dict]:
        raw = self._open_settings().value(_TASK_PREFERENCES_KEY, "{}")
        try:
            values = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise SettingsError("task preferences must be a JSON object") from error
        self.validate_task_preferences(values)
        return values

    def get_task_parameters(self, task_id: str) -> dict:
        raw = self._open_settings().value(f"tasks/{task_id}/parameters", "{}")
        try:
            values = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise SettingsError("任务参数必须是 JSON 对象") from error
        if not isinstance(values, dict):
            raise SettingsError("任务参数必须是 JSON 对象")
        return values

    def set_task_parameters(self, task_id: str, values: dict) -> None:
        if not isinstance(task_id, str) or not task_id or not isinstance(values, dict):
            raise SettingsError("任务 ID 和参数无效")
        self._open_settings().setValue(f"tasks/{task_id}/parameters", json.dumps(values, ensure_ascii=False, allow_nan=False))

    def set_task_preferences(self, values: dict[str, dict]) -> None:
        self.validate_task_preferences(values)
        self._open_settings().setValue(
            _TASK_PREFERENCES_KEY, json.dumps(values, ensure_ascii=False)
        )

    @staticmethod
    def validate_task_preferences(values: dict[str, dict]) -> None:
        if not isinstance(values, dict):
            raise SettingsError("task preferences must be an object")
        for task_id, preferences in values.items():
            if not isinstance(task_id, str) or not task_id.strip():
                raise SettingsError("task preference ID must be non-empty text")
            if not isinstance(preferences, dict) or set(preferences) != {
                "name", "description", "show_logs"
            }:
                raise SettingsError("task preferences require name, description and show_logs")
            if not isinstance(preferences["name"], str) or not preferences["name"].strip():
                raise SettingsError("任务名称不能为空")
            if not isinstance(preferences["description"], str):
                raise SettingsError("任务介绍必须是文字")
            if not isinstance(preferences["show_logs"], bool):
                raise SettingsError("任务日志显示设置必须是布尔值")

    def sync(self) -> None:
        settings = self._open_settings()
        settings.sync()
        if settings.status() != QtCore.QSettings.Status.NoError:
            raise OSError(
                f"failed to sync settings file {self._path}: {settings.status().name}"
            )

    def close(self) -> None:
        if self._settings is None:
            return
        self.sync()
        self._settings = None

    def _open_settings(self) -> QtCore.QSettings:
        if self._settings is None:
            raise RuntimeError("settings component is closed")
        if self._settings.status() != QtCore.QSettings.Status.NoError:
            raise OSError(
                f"cannot access settings file {self._path}: {self._settings.status().name}"
            )
        return self._settings

    def _read_integer(self, key: str) -> int:
        raw = self._open_settings().value(key)
        if isinstance(raw, bool):
            raise SettingsError(f"setting {key!r} must be an integer")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and _INTEGER_PATTERN.fullmatch(raw.strip()):
            return int(raw)
        raise SettingsError(f"setting {key!r} must be an integer")

    def _read_boolean(self, key: str, *, default: bool) -> bool:
        settings = self._open_settings()
        if not settings.contains(key):
            return default
        raw = settings.value(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise SettingsError(f"setting {key!r} must be true or false")

    def _set_boolean(self, key: str, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError("boolean setting value must be bool")
        self._open_settings().setValue(key, value)

    @staticmethod
    def _validate_display_scale(scale: float) -> float:
        if not math.isfinite(scale):
            raise SettingsError("desktop pet display scale must be finite")
        if not MIN_DISPLAY_SCALE <= scale <= MAX_DISPLAY_SCALE:
            raise SettingsError(
                "desktop pet display scale must be between "
                f"{MIN_DISPLAY_SCALE:.2f} and {MAX_DISPLAY_SCALE:.2f}"
            )
        return scale
