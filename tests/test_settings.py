from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6 import QtCore

from huanzhizhi.settings import SettingsComponent, SettingsError


class SettingsComponentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.ini_path = Path(self.temporary_directory.name) / "huanzhizhi.ini"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_values_have_documented_defaults(self) -> None:
        settings = SettingsComponent(self.ini_path)

        self.assertIsNone(settings.get_pet_position())
        self.assertEqual(settings.get_display_scale(), 0.32)
        self.assertFalse(settings.get_mouse_point_visible())
        self.assertTrue(settings.get_runtime_transparent())
        self.assertEqual(settings.get_input_mode(), "background")
        self.assertEqual(settings.get_pet_theme(), "ufo")
        self.assertTrue(settings.get_sound_enabled())
        self.assertEqual(settings.get_hidden_task_ids(), ())
        self.assertEqual(settings.get_task_preferences(), {})
        settings.close()

    def test_legacy_original_theme_is_migrated_and_persisted(self) -> None:
        raw = QtCore.QSettings(str(self.ini_path), QtCore.QSettings.Format.IniFormat)
        raw.setValue("desktopPetTheme", "original")
        raw.setValue("desktopPetScale", .56)
        raw.setValue("unrelated", "keep")
        raw.sync()
        settings = SettingsComponent(self.ini_path)
        self.assertEqual(settings.get_pet_theme(), "ufo")
        settings.close()
        raw.sync()
        self.assertEqual(raw.value("desktopPetTheme"), "ufo")
        self.assertEqual(raw.value("unrelated"), "keep")
        reopened = SettingsComponent(self.ini_path)
        self.assertEqual(reopened.get_pet_theme(), "ufo")
        self.assertEqual(reopened.get_display_scale(), .56)
        reopened.close()

    def test_values_persist_after_sync_and_reopen(self) -> None:
        settings = SettingsComponent(self.ini_path)
        settings.set_pet_position(QtCore.QPoint(-25, 480))
        settings.set_display_scale(0.56)
        settings.set_mouse_point_visible(True)
        settings.set_runtime_transparent(False)
        settings.set_input_mode("foreground")
        settings.set_pet_theme("broom")
        settings.set_sound_enabled(False)
        settings.sync()
        settings.close()

        reopened = SettingsComponent(self.ini_path)
        self.assertEqual(reopened.get_pet_position(), QtCore.QPoint(-25, 480))
        self.assertEqual(reopened.get_display_scale(), 0.56)
        self.assertTrue(reopened.get_mouse_point_visible())
        self.assertFalse(reopened.get_runtime_transparent())
        self.assertEqual(reopened.get_input_mode(), "foreground")
        self.assertEqual(reopened.get_pet_theme(), "broom")
        self.assertFalse(reopened.get_sound_enabled())
        reopened.close()

    def test_retired_agent_settings_are_removed_without_changing_codex_options(self) -> None:
        for tool in ('codex', 'deepseek'):
            with self.subTest(tool=tool):
                settings = SettingsComponent(self.ini_path)
                options = settings.get_codex_options()
                settings.set_codex_options(options)
                settings.close()
                raw = QtCore.QSettings(str(self.ini_path), QtCore.QSettings.Format.IniFormat)
                raw.setValue('agentTool', tool)
                raw.setValue('aiEnabled', True)
                raw.setValue('deepseekOptions', '{"model": "legacy"}')
                raw.setValue('unrelated', 'keep')
                raw.sync()

                settings = SettingsComponent(self.ini_path)
                self.assertEqual(settings.get_codex_options(), options)
                self.assertEqual(settings.get_ai_enabled(), tool == 'codex')
                settings.close()
                raw.sync()
                self.assertFalse(raw.contains('agentTool'))
                self.assertFalse(raw.contains('deepseekOptions'))
                self.assertEqual(raw.value('unrelated'), 'keep')

    def test_operation_mode_and_theme_reject_invalid_settings_without_overwriting(self) -> None:
        settings = SettingsComponent(self.ini_path)
        settings.set_input_mode("foreground")
        settings.set_pet_theme("ufo")
        try:
            for invalid in (None, True, 1, [], {}, "", "FOREGROUND", "unknown", "original"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(SettingsError):
                        settings.set_input_mode(invalid)
                    with self.assertRaises(SettingsError):
                        settings.set_pet_theme(invalid)
                    self.assertEqual(settings.get_input_mode(), "foreground")
                    self.assertEqual(settings.get_pet_theme(), "ufo")
        finally:
            settings.close()

        raw = QtCore.QSettings(str(self.ini_path), QtCore.QSettings.Format.IniFormat)
        raw.setValue("inputMode", "unknown")
        raw.setValue("desktopPetTheme", False)
        raw.sync()
        settings = SettingsComponent(self.ini_path)
        try:
            with self.assertRaisesRegex(SettingsError, "操作模式"):
                settings.get_input_mode()
            with self.assertRaisesRegex(SettingsError, "桌宠主题"):
                settings.get_pet_theme()
        finally:
            settings.close()

    def test_position_can_be_removed(self) -> None:
        settings = SettingsComponent(self.ini_path)
        settings.set_pet_position(QtCore.QPoint(10, 20))
        settings.set_pet_position(None)
        settings.sync()

        self.assertIsNone(settings.get_pet_position())
        settings.close()


    def test_hidden_tasks_persist_multiple_single_and_empty_selections(self) -> None:
        for task_ids in (("demo", "演示任务"), ("demo",), ()):
            with self.subTest(task_ids=task_ids):
                settings = SettingsComponent(self.ini_path)
                settings.set_hidden_task_ids(task_ids)
                settings.close()
                reopened = SettingsComponent(self.ini_path)
                self.assertEqual(set(reopened.get_hidden_task_ids()), set(task_ids))
                reopened.close()

    def test_invalid_hidden_task_settings_are_rejected(self) -> None:
        for value in ("bad-json", "{}", '[""]', '[12]'):
            with self.subTest(value=value):
                raw = QtCore.QSettings(str(self.ini_path), QtCore.QSettings.Format.IniFormat)
                raw.setValue("desktopPetHiddenTaskIds", value)
                raw.sync()
                settings = SettingsComponent(self.ini_path)
                with self.assertRaises(SettingsError):
                    settings.get_hidden_task_ids()
                settings.close()

    def test_task_preferences_persist_and_reject_invalid_values(self) -> None:
        values = {"demo": {"name": "我的任务", "description": "第一行\n第二行", "show_logs": False}}
        settings = SettingsComponent(self.ini_path)
        settings.set_task_preferences(values)
        settings.close()
        reopened = SettingsComponent(self.ini_path)
        self.assertEqual(reopened.get_task_preferences(), {key: {**value, "name": key} for key, value in values.items()})
        for invalid in (
            [], {"demo": {}},
            {"demo": {**values["demo"], "name": "  "}},
            {"demo": {**values["demo"], "description": 42}},
            {"demo": {**values["demo"], "show_logs": "false"}},
            {"demo": {**values["demo"], "group": "other"}},
            {"demo": {**values["demo"], "group": None}},
            {"demo": {**values["demo"], "group": []}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(SettingsError):
                reopened.set_task_preferences(invalid)
        self.assertEqual(reopened.get_task_preferences(), {key: {**value, "name": key} for key, value in values.items()})
        reopened.close()
        raw = QtCore.QSettings(str(self.ini_path), QtCore.QSettings.Format.IniFormat)
        raw.setValue("desktopPetTaskPreferences", '{"demo": {}}')
        raw.sync()
        settings = SettingsComponent(self.ini_path)
        with self.assertRaises(SettingsError):
            settings.get_task_preferences()
        settings.close()

    def test_task_groups_persist_alongside_legacy_preferences(self) -> None:
        legacy = {"name": "已有任务", "description": "说明", "show_logs": True}
        values = {
            "legacy": legacy,
            "formal": {**legacy, "group": "formal"},
            "test": {**legacy, "group": "test"},
        }
        settings = SettingsComponent(self.ini_path)
        settings.set_task_preferences(values)
        settings.close()
        reopened = SettingsComponent(self.ini_path)
        try:
            self.assertEqual(reopened.get_task_preferences(), {key: {**value, "name": key} for key, value in values.items()})
        finally:
            reopened.close()

    def test_chinese_task_ids_preserve_settings_and_do_not_replace_newer_values(self):
        settings = SettingsComponent(self.ini_path)
        old = {'name': '我的提示', 'description': '说明', 'show_logs': False, 'group': 'test'}
        newer = {**old, 'name': '新的名称', 'group': 'formal'}
        settings.set_task_preferences({'brick_hint': old, 'delta_live': old, '三角洲实时定位': newer})
        settings.set_hidden_task_ids(('brick_hint', 'sudoku_finish', 'unrelated'))
        settings.set_task_parameters('yang_le_ge_yang_data_clear', {'interval_ms': 600})
        settings.set_task_parameters('delta_live', {'value': 1})
        settings.set_task_parameters('三角洲实时定位', {'value': 2})
        settings.migrate_task_ids()
        settings.close()
        reopened = SettingsComponent(self.ini_path)
        try:
            reopened.migrate_task_ids()
            self.assertEqual(reopened.get_task_preferences(), {'砖了个砖提示': {**old, 'name': '砖了个砖提示'}, '三角洲实时定位': {**newer, 'name': '三角洲实时定位'}})
            self.assertEqual(set(reopened.get_hidden_task_ids()), {'砖了个砖提示', '数独留一格', 'unrelated'})
            self.assertEqual(reopened.get_task_parameters('羊了个羊数据通关'), {'interval_ms': 600})
            self.assertEqual(reopened.get_task_parameters('三角洲实时定位'), {'value': 2})
            self.assertEqual(reopened.get_task_parameters('delta_live'), {})
            self.assertEqual(reopened.get_task_parameters('yang_le_ge_yang_data_clear'), {})
        finally:
            reopened.close()

    def test_incomplete_position_is_an_error(self) -> None:
        self.ini_path.write_text("[General]\ndesktopPetIdleX=12\n", encoding="utf-8")
        settings = SettingsComponent(self.ini_path)

        with self.assertRaisesRegex(SettingsError, "position is incomplete"):
            settings.get_pet_position()
        settings.close()

    def test_invalid_persisted_types_are_errors(self) -> None:
        invalid_values = (
            ("desktopPetIdleX=left\ndesktopPetIdleY=20", "must be an integer"),
            ("desktopPetScale=large", "must be a number"),
            ("desktopPetMouseClickVisible=yes", "must be true or false"),
            ("desktopPetSoundEnabled=yes", "must be true or false"),
        )
        for content, message in invalid_values:
            with self.subTest(content=content):
                self.ini_path.write_text(f"[General]\n{content}\n", encoding="utf-8")
                settings = SettingsComponent(self.ini_path)
                with self.assertRaisesRegex(SettingsError, message):
                    settings.get_pet_position()
                    settings.get_display_scale()
                    settings.get_mouse_point_visible()
                    settings.get_sound_enabled()
                settings.close()

    def test_out_of_range_and_non_finite_scales_are_errors(self) -> None:
        for scale in (0.18, 1.26, float("nan"), float("inf")):
            with self.subTest(scale=scale):
                settings = SettingsComponent(self.ini_path)
                with self.assertRaises(SettingsError):
                    settings.set_display_scale(scale)
                settings.close()

    def test_out_of_range_persisted_scale_is_an_error(self) -> None:
        self.ini_path.write_text(
            "[General]\ndesktopPetScale=1.26\n", encoding="utf-8"
        )
        settings = SettingsComponent(self.ini_path)

        with self.assertRaisesRegex(SettingsError, "between 0.19 and 1.25"):
            settings.get_display_scale()
        settings.close()

    def test_closed_component_rejects_access(self) -> None:
        settings = SettingsComponent(self.ini_path)
        settings.close()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            settings.get_display_scale()


if __name__ == "__main__":
    unittest.main()
