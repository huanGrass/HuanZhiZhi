"""Strict persistence for settings shared by HuanZhiZhi components."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from PySide6 import QtCore

from huanzhizhi.agents.runtime import SettingsError
from huanzhizhi.agents.opencode import adapter as ai_opencode
from huanzhizhi.agents.codex.adapter import (
    DEFAULT_CODEX_OPTIONS, CODEX_REASONING_EFFORTS, codex_executable,
    check_codex_configuration, validate_options,
)


DEFAULT_DISPLAY_SCALE = 0.32
MIN_DISPLAY_SCALE = 0.19
MAX_DISPLAY_SCALE = 1.25

_PET_X_KEY = "desktopPetIdleX"
_PET_Y_KEY = "desktopPetIdleY"
_DISPLAY_SCALE_KEY = "desktopPetScale"
_MOUSE_POINT_VISIBLE_KEY = "desktopPetMouseClickVisible"
_RUNTIME_TRANSPARENT_KEY = "desktopPetRuntimeTransparent"
_INPUT_MODE_KEY = "inputMode"
_PET_THEME_KEY = "desktopPetTheme"
_SOUND_ENABLED_KEY = "desktopPetSoundEnabled"
_HIDDEN_TASK_IDS_KEY = "desktopPetHiddenTaskIds"
_TASK_PREFERENCES_KEY = "desktopPetTaskPreferences"
_INTEGER_PATTERN = re.compile(r"[+-]?\d+")


class SettingsComponent:
    """Read and write HuanZhiZhi settings in one explicitly selected INI file."""

    def __init__(self, ini_path: Path) -> None:
        if not isinstance(ini_path, Path):
            raise TypeError("ini_path must be a pathlib.Path")
        self._path = ini_path
        self.ai_work_active = lambda: False
        self._settings: QtCore.QSettings | None = QtCore.QSettings(
            str(ini_path), QtCore.QSettings.Format.IniFormat
        )
        if not self._settings.contains('aiEnabled'):
            # Only an explicitly saved, valid legacy configuration opts existing users in.
            enabled = False
            if self._settings.contains('codexOptions'):
                try:
                    self.get_codex_options()
                    enabled = True
                except SettingsError:
                    pass
            self._settings.setValue('aiEnabled', enabled)
        # Retired tool selections must not silently start Codex with AI enabled.
        if self._settings.value('agentTool', 'codex') not in ('codex', 'opencode'):
            self._settings.setValue('aiEnabled', False)
            self._settings.remove('agentTool')
        if self._settings.value('agentTool') == 'codex':
            self._settings.remove('agentTool')
        for key in ('deepseekOptions', 'codebuddyOptions', 'traeOptions', 'qwenOptions', 'kimiOptions', 'cursorOptions'):
            self._settings.remove(key)

    @property
    def path(self) -> Path:
        return self._path

    def get_ai_enabled(self) -> bool:
        return self._read_boolean('aiEnabled', default=False)

    def get_opencode_model_cache(self):
        try:
            cache = json.loads(self._open_settings().value('opencodeModelCache', '{}'))
            if (cache['executable'] != self.get_opencode_options()['executable'] or
                    not 0 <= time.time() - cache['updated'] < 86400):
                return []
            models = cache['models']
            if not isinstance(models, list):
                return []
            for model in models:
                if (set(model) != {'id', 'name', 'image'} or model['image'] is not True or
                        not isinstance(model['name'], str) or not model['id']):
                    return []
                ai_opencode.validate_options({'executable': '', 'model': model['id']})
            return models
        except (ValueError, KeyError, TypeError, SettingsError):
            return []

    def set_opencode_model_cache(self, models):
        # Persist only the public display fields, never raw provider metadata.
        self._open_settings().setValue('opencodeModelCache', json.dumps({
            'executable': self.get_opencode_options()['executable'],
            'updated': time.time(),
            'models': [{'id': model['id'], 'name': model['name'], 'image': True}
                       for model in models if model['image'] is True],
        }))

    def require_ai_enabled(self) -> None:
        if not self.get_ai_enabled():
            raise SettingsError('AI 已禁用，不能创建、修改或修复技能。请到“设置 → AI 设置”完成配置并启用 AI；已有技能仍可运行。')

    def set_ai_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError('AI 开关必须是布尔值')
        if self.ai_work_active():
            raise SettingsError('AI 正在工作，请先在对话中停止，结束后可切换。')
        if enabled:
            self.check_agent_configuration()
        self._set_boolean('aiEnabled', enabled)

    def check_agent_configuration(self):
        if self.get_agent_tool() == 'opencode':
            options = self.get_opencode_options()
            if not self.is_opencode_verified():
                raise SettingsError('请先在 OpenCode 引导中选择免费模型并完成连接测试。')
            return ai_opencode.executable(options)
        return check_codex_configuration(self.get_codex_options())

    def get_agent_tool(self):
        tool = self._open_settings().value('agentTool', 'codex')
        if tool not in ('codex', 'opencode'):
            raise SettingsError('未知 AI 工具。')
        return tool

    def set_agent_tool(self, tool):
        if tool not in ('codex', 'opencode'):
            raise SettingsError('未知 AI 工具。')
        if self.ai_work_active():
            raise SettingsError('AI 正在工作，请先停止后切换工具。')
        if tool != self.get_agent_tool():
            self._open_settings().setValue('agentTool', tool)
            self._set_boolean('aiEnabled', False)

    def get_opencode_options(self):
        try:
            options = json.loads(self._open_settings().value('opencodeOptions', json.dumps(ai_opencode.DEFAULT_OPTIONS)))
        except (ValueError, TypeError) as error:
            raise SettingsError('OpenCode 配置无效，请重新配置。') from error
        ai_opencode.validate_options(options)
        return options

    def set_opencode_options(self, options):
        if self.ai_work_active():
            raise SettingsError('AI 正在工作，请先停止后修改配置。')
        ai_opencode.validate_options(options)
        if options != self.get_opencode_options():
            self.clear_opencode_verification()
        self._open_settings().setValue('opencodeOptions', json.dumps(options))

    def clear_opencode_verification(self):
        if self.ai_work_active():
            raise SettingsError('AI 正在工作，请先停止后测试连接。')
        self._open_settings().remove('opencodeVerifiedOptions')
        if self.get_agent_tool() == 'opencode':
            self._set_boolean('aiEnabled', False)

    def mark_opencode_verified(self, options):
        if self.ai_work_active() or options != self.get_opencode_options():
            raise SettingsError('配置已变化，请重新进行连接测试。')
        self._open_settings().setValue('opencodeVerifiedOptions', json.dumps(options, sort_keys=True))

    def is_opencode_verified(self):
        return self._open_settings().value('opencodeVerifiedOptions') == json.dumps(self.get_opencode_options(), sort_keys=True)

    def get_codex_options(self) -> dict[str, str]:
        raw = self._open_settings().value("codexOptions", json.dumps(DEFAULT_CODEX_OPTIONS))
        try:
            options = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise SettingsError("Codex 设置必须是 JSON 对象") from error
        self.validate_codex_options(options)
        return options

    def set_codex_options(self, options: dict[str, str]) -> None:
        self.validate_codex_options(options)
        self._open_settings().setValue("codexOptions", json.dumps(options, ensure_ascii=False))

    validate_codex_options = staticmethod(validate_options)

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

    def get_runtime_transparent(self) -> bool:
        return self._read_boolean(_RUNTIME_TRANSPARENT_KEY, default=True)

    def set_runtime_transparent(self, transparent: bool) -> None:
        self._set_boolean(_RUNTIME_TRANSPARENT_KEY, transparent)

    def get_input_mode(self) -> str:
        mode = self._open_settings().value(_INPUT_MODE_KEY, "background")
        if not isinstance(mode, str) or mode not in ("background", "foreground"):
            raise SettingsError("操作模式必须是 background 或 foreground")
        return mode

    def set_input_mode(self, mode: str) -> None:
        if not isinstance(mode, str) or mode not in ("background", "foreground"):
            raise SettingsError("操作模式必须是 background 或 foreground")
        self._open_settings().setValue(_INPUT_MODE_KEY, mode)

    def get_pet_theme(self) -> str:
        settings = self._open_settings()
        theme = settings.value(_PET_THEME_KEY, "ufo")
        if theme == "original":
            settings.setValue(_PET_THEME_KEY, "ufo")
            self.sync()
            return "ufo"
        if not isinstance(theme, str) or theme not in ("ufo", "broom"):
            raise SettingsError("桌宠主题必须是 ufo 或 broom")
        return theme

    def set_pet_theme(self, theme: str) -> None:
        if not isinstance(theme, str) or theme not in ("ufo", "broom"):
            raise SettingsError("桌宠主题必须是 ufo 或 broom")
        self._open_settings().setValue(_PET_THEME_KEY, theme)

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
        for key, value in values.items():
            value['name'] = key.removeprefix('test/')
        return values

    def migrate_task_ids(self) -> None:
        """Move preferences for the five renamed built-in skills, keeping newer values."""
        renames = {
            'brick_hint': '砖了个砖提示',
            'delta_live': '三角洲实时定位',
            'delta_zero_dam': '三角洲录制测试',
            'sudoku_finish': '数独留一格',
            'yang_le_ge_yang_data_clear': '羊了个羊数据通关',
        }
        settings = self._open_settings()
        preferences = self.get_task_preferences()
        hidden = self.get_hidden_task_ids()
        migrated_hidden = tuple(renames.get(task_id, task_id) for task_id in hidden)
        if migrated_hidden != hidden:
            self.set_hidden_task_ids(migrated_hidden)
        changed = False
        for old, new in renames.items():
            if old in preferences:
                preferences.setdefault(new, preferences.pop(old))
                changed = True
            old_key, new_key = f'tasks/{old}/parameters', f'tasks/{new}/parameters'
            if settings.contains(old_key):
                if not settings.contains(new_key):
                    self.set_task_parameters(new, self.get_task_parameters(old))
                settings.remove(old_key)
        if changed:
            self.set_task_preferences(preferences)
        self.sync()

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
            _TASK_PREFERENCES_KEY, json.dumps({key: {k: v for k, v in value.items() if k != 'name'}
                                             for key, value in values.items()}, ensure_ascii=False)
        )

    def rename_task_settings(self, renames: dict[str, str]) -> None:
        """Move preferences and parameters together; restore them on a write failure."""
        settings = self._open_settings()
        keys = [key for key in settings.allKeys()
                if key.startswith('tasks/') or key in (_TASK_PREFERENCES_KEY, _HIDDEN_TASK_IDS_KEY)]
        before = {key: settings.value(key) for key in keys}
        preferences = self.get_task_preferences()
        hidden = self.get_hidden_task_ids()
        parameters = {old: self.get_task_parameters(old) for old in renames}
        try:
            moved = {renames.get(key, key): value for key, value in preferences.items()
                     if key not in renames.values() or key in renames}
            # Existing destination settings are retained unless the source supplies them.
            for key in renames.values():
                if key not in moved and key in preferences:
                    moved[key] = preferences[key]
            self.set_task_preferences(moved)
            self.set_hidden_task_ids(tuple(renames.get(key, key) for key in hidden))
            for old in renames:
                settings.remove(f'tasks/{old}/parameters')
            for old, new in renames.items():
                self.set_task_parameters(new, parameters[old])
            self.sync()
        except Exception:
            for key in settings.allKeys():
                if key.startswith('tasks/') or key in (_TASK_PREFERENCES_KEY, _HIDDEN_TASK_IDS_KEY):
                    settings.remove(key)
            for key, value in before.items():
                settings.setValue(key, value)
            settings.sync()
            raise

    @staticmethod
    def validate_task_preferences(values: dict[str, dict]) -> None:
        if not isinstance(values, dict):
            raise SettingsError("task preferences must be an object")
        for task_id, preferences in values.items():
            if not isinstance(task_id, str) or not task_id.strip():
                raise SettingsError("task preference ID must be non-empty text")
            if not isinstance(preferences, dict) or set(preferences) not in (
                {"description", "show_logs"},
                {"description", "show_logs", "group"},
                {"name", "description", "show_logs"},
                {"name", "description", "show_logs", "group"},
            ):
                raise SettingsError("task preferences require name, description and show_logs")
            if 'name' in preferences and (not isinstance(preferences["name"], str) or not preferences["name"].strip()):
                raise SettingsError("任务名称不能为空")
            if not isinstance(preferences["description"], str):
                raise SettingsError("任务介绍必须是文字")
            if not isinstance(preferences["show_logs"], bool):
                raise SettingsError("任务日志显示设置必须是布尔值")
            if preferences.get("group", "formal") not in ("formal", "test"):
                raise SettingsError("任务分组必须是正式或测试")

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
