"""Persistent application settings."""

from .component import SettingsComponent, SettingsError
from .panel import DesktopPetSettingsComponent

__all__ = ["DesktopPetSettingsComponent", "SettingsComponent", "SettingsError"]
