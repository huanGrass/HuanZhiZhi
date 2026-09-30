# Build with: .venv/Scripts/python.exe -m PyInstaller --noconfirm 幻之之.spec
from pathlib import Path
import json
import platform
import os
import sys
from importlib.metadata import version

# Do not collect unrelated DLLs from terminal tools (e.g. Poppler's icuuc.dll,
# which lacks the Windows ICU entry points required by Qt). Package hooks add
# their own library directories; Windows system DLLs remain system-provided.
windows = Path(os.environ["SystemRoot"])
os.environ["PATH"] = os.pathsep.join(map(str, (
    Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows,
)))

root = Path(SPECPATH)
src = root / "src"
task_ids = ("brick_hint",)

host = Analysis(
    [str(src / "huanzhizhi" / "__main__.py")],
    pathex=[str(src)],
    datas=[(str(src / "huanzhizhi" / "assets"), "huanzhizhi/assets")],
)
analyses = [host]
host_pyz = PYZ(host.pure)
executables = [EXE(
    host_pyz, host.scripts, [], exclude_binaries=True,
    name="幻之之", console=False, upx=False, contents_directory="tasks",
    icon=str(src / "huanzhizhi" / "assets" / "app_icon.png"),
)]

for task_id in task_ids:
    task_dir = root / "tasks" / task_id
    paths = [str(task_dir), str(src)]
    datas = []
    if (task_dir / "assets").is_dir():
        datas.append((str(task_dir / "assets"), f"resources/{task_id}/assets"))
    analysis = Analysis([str(task_dir / "task.py")], pathex=paths, datas=datas)
    # Each task has its own controller/recognition modules; keep its Python
    # archive independent while sharing binary dependencies through COLLECT.
    expected_controller = task_dir / "controller.py"
    controller = next(source for name, source, _ in analysis.pure if name == "controller")
    assert Path(controller).resolve() == expected_controller.resolve(), controller
    analyses.append(analysis)
    executables.append(EXE(
        PYZ(analysis.pure), analysis.scripts, [("u", None, "OPTION")],
        exclude_binaries=True, name=f"task-{task_id}",
        console=True, hide_console="hide-early", upx=False, contents_directory=".",
    ))

# Widgets UI does not use virtual keyboard or PDF/QML plugins. Do not ship
# their unused runtimes (Qt Virtual Keyboard is GPL-only for open-source use).
unused_qt = {
    "qtvirtualkeyboardplugin.dll", "qt6virtualkeyboard.dll", "qpdf.dll", "qt6pdf.dll",
    "qt6qml.dll", "qt6qmlmeta.dll", "qt6qmlmodels.dll", "qt6qmlworkerscript.dll", "qt6quick.dll",
}
for analysis in analyses:
    analysis.binaries = [entry for entry in analysis.binaries
                         if Path(entry[0]).name.lower() not in unused_qt]

# Reject different binary/resource files targeting the same shared path.
# base_library.zip is generated separately for each Analysis; its module set
# is checked below before retaining one copy.
import hashlib
import zipfile
seen = {}
for analysis in analyses:
    for destination, source, kind in analysis.binaries + analysis.datas:
        key = destination.replace("\\", "/").lower()
        if kind == "SYMLINK":
            fingerprint = source
        elif key == "base_library.zip":
            with zipfile.ZipFile(source) as archive:
                fingerprint = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
        else:
            fingerprint = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        if key in seen and seen[key] != fingerprint:
            raise ValueError(f"Conflicting shared dependency: {destination}")
        seen[key] = fingerprint

bundle = COLLECT(
    executables[0],
    [(f"tasks/{Path(exe.name).name}", exe.name, "EXECUTABLE") for exe in executables[1:]],
    *(exe.dependencies for exe in executables[1:]),
    *(analysis.binaries for analysis in analyses),
    *(analysis.datas for analysis in analyses),
    name="幻之之", upx=False,
)
manifest = {
    "python": platform.python_version(),
    "architecture": platform.machine(),
    "dependencies": {name: version(name) for name in (
        "PyInstaller", "PySide6", "numpy", "opencv-python", "Pillow", "windows-capture",
    )},
    "tasks": list(task_ids),
}
(Path(bundle.name) / "build-info.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
)


# Keep third-party notices alongside the binary distribution.
import shutil
shutil.copytree(root / "licenses", Path(bundle.name) / "licenses", dirs_exist_ok=True)
shutil.copy2(root / "LICENSE", Path(bundle.name) / "LICENSE")
