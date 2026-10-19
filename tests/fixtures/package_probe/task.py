"""Copied into a temporary tasks/package_probe directory for frozen smoke tests.

No screen capture, input injection, or game access.
"""
from pathlib import Path
import sys

from huanzhizhi.task_sdk import TaskSession, describe, load_skill

VERSION = load_skill(__file__)['name']


def main():
    if describe(__file__):
        return
    session = TaskSession(__file__)
    import cv2
    import numpy as np
    from PIL import Image
    from PySide6 import QtCore

    root = (Path(sys._MEIPASS) / "resources" / "package_probe"
            if getattr(sys, "frozen", False) else Path(__file__).parent)
    marker = (root / "assets" / "version.txt").read_text(encoding="utf-8").strip()
    assert marker == VERSION, (marker, VERSION)
    with Image.open(root / "assets" / "pixel.png") as image:
        assert cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY).shape == (1, 1)
    assert QtCore.qVersion()
    session.emit_status("Ready", VERSION, marker)
    assert session.wait_for_stop(20), "No cooperative stop received"
    session.emit_finished("Stopped", VERSION, marker, stopped_by_request=True)


if __name__ == "__main__":
    main()
