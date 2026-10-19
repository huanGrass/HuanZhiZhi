"""External verification script, executed with the release's own Python."""
import json
import re
import shutil
import sys
import time
from pathlib import Path

from PySide6 import QtWidgets
from PIL import Image
import huanzhizhi
from huanzhizhi.application import HuanZhiZhiApplication
from huanzhizhi.games import go, minesweeper
from huanzhizhi.python_runtime import run_code
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import FinishedEvent, StatusEvent


def run_safely(task):
    if task.id != "package_probe":
        return {"id": task.id, "result": "description only; no approved safe run scenario"}
    manager = TaskProcessManager()
    events, errors = [], []
    manager.event_received.connect(events.append)
    manager.error.connect(errors.append)
    try:
        manager.start(task.id, task.script_path, {})
        assert Path(manager._process.program()).resolve() == bundle / "python/python.exe"
        deadline = time.monotonic() + 30
        stop_sent = False
        while manager.state != "idle" and time.monotonic() < deadline:
            app.processEvents()
            if not stop_sent and any(isinstance(e, StatusEvent) for e in events):
                manager.stop()
                stop_sent = True
            time.sleep(0.01)
        assert manager.state == "idle", f"{task.id}: timeout"
        assert not errors, (errors, manager.stderr_text)
        finished = [e for e in events if isinstance(e, FinishedEvent)]
        assert len(finished) == 1, (events, manager.stderr_text)
        assert stop_sent and finished[0].stopped_by_request and not finished[0].error, finished[0]
        if task.id == "package_probe":
            assert any(isinstance(e, StatusEvent) and e.detail == task.name for e in events), events
        return {"id": task.id, "name": task.name,
                "result": "started and stopped"}
    finally:
        manager.close()
        app.processEvents()


app = QtWidgets.QApplication([])
app.setApplicationName("HuanZhiZhiPackageSmoke")
report_dir = Path(sys.argv[1]).resolve()
bundle = Path(sys.executable).resolve().parent.parent
assert Path(huanzhizhi.__file__).resolve().is_relative_to(bundle / "src")
host = HuanZhiZhiApplication(app, settings_path=report_dir / "settings.ini")
try:
    manifest = json.loads((bundle / "build-info.json").read_text(encoding="utf-8"))
    expected = set(manifest["tasks"])
    tasks = host.tasks.catalog.tasks()
    assert {task.id for task in tasks} == expected
    assert all(task.script_path.suffix == ".py" for task in tasks)
    results = [run_safely(task) for task in tasks]
    script_result = run_code(
        "import sys, cv2, numpy, PIL, windows_capture; from PySide6 import QtCore; "
        "from pathlib import Path; print(sys.executable); print('共享依赖可用'); "
        "Path('result.txt').write_text('成功', encoding='utf-8')",
        runs_dir=report_dir / "python_runs",
    )
    assert script_result.returncode == 0, script_result
    assert str(bundle / "python/python.exe") in script_result.stdout, script_result
    assert (script_result.workdir / "result.txt").read_text(encoding="utf-8") == "成功"
    results.append({"script": "shared Python and dependencies passed"})
    for module in (go, minesweeper):
        assert Path(module.__file__).resolve().is_relative_to(bundle / "src/huanzhizhi/games")
    board = [1, -1, -1, -1]
    near = minesweeper.neighbors(1, 4)
    _, mines, _ = minesweeper.deduce(board, set(), near, 1)
    safe, _, _ = minesweeper.deduce(board, mines, near, 1)
    assert mines == {1} and safe == {2, 3}
    for size in (9, 13, 19):
        initial = [[0] * size for _ in range(size)]
        after = go.move_result(initial, 0, 0, 1)
        assert go.single_new_stone(initial, after, 1) == (0, 0)
        assert go.parse_coordinate(go.coordinate(0, 0, size), size) == (0, 0)
    results.append({"shared_games": "bundled Minesweeper logic and Go board APIs passed"})
    host.pet.show()
    app.processEvents()
    host.pet.clicked.emit()
    app.processEvents()
    for update_arg in sys.argv[2:]:
        # Updates are exercised only on a disposable copy under the report directory.
        assert bundle.is_relative_to(report_dir)
        update = Path(update_arg)
        update_info = json.loads((update / "update-info.json").read_text(encoding="utf-8"))
        assert all(update_info[key] == manifest[key] for key in ("format", "runtime_sha256", "host_sha256"))
        task_id = update_info["task"]
        assert re.fullmatch(r"[^\W_][\w-]*", task_id)
        target = (bundle / "tasks" / task_id).resolve()
        assert target.is_relative_to((bundle / "tasks").resolve())
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(update / "tasks" / task_id, target)
        host.pet.clicked.emit()
        app.processEvents()
        expected.add(task_id)
        assert {task.id for task in host.tasks.catalog.tasks()} == expected
        assert host.tasks.catalog.loaded_signature == host.tasks.catalog.signature()
        results.append(run_safely(host.tasks.catalog.get(task_id)))
    images = [p for p in (bundle / "src/huanzhizhi/assets").rglob("*")
              if p.suffix in (".png", ".webp")]
    assert not list((bundle / "tasks").rglob("tests"))
    for path in images:
        with Image.open(path) as image:
            image.load()
    for relative in ("幻之之.exe", "LICENSE", "licenses/README.txt", "licenses/ASSETS.md", "python/LICENSE.txt",
                     "src/huanzhizhi/skill_creation.md", "src/huanzhizhi/skill_examples.md",
                     "src/huanzhizhi/skill_reference.md", "src/huanzhizhi/assets/themes/metadata.json",
                     "src/huanzhizhi/game_examples.md",
                     "src/huanzhizhi/assets/dock/LICENSE.txt", "docs/capabilities.md"):
        assert (bundle / relative).is_file(), relative
    assert not (bundle / "test_tasks").exists()
    assert not (bundle / "python/Lib/test").exists()
    assert not (bundle / "licenses/PyInstaller").exists()
    assert not (bundle / "licenses/pywin32").exists()
    assert not (bundle / "run.ps1").exists()
    assert not (bundle / "启动.cmd").exists()
    assert {p.name for p in (bundle / "dependencies").iterdir()} == {"README.md"}
    assert not list((bundle / "python").rglob("*VirtualKeyboard*"))
    assert not list((bundle / "python").rglob("Qt6Pdf.dll"))
    results.append({"resources": "images, AI knowledge and license notices present", "images": len(images)})
    results.append({"skill_scope": "explicit manifest selection only; no skill test directories"})
    image = host.pet.window.grab()
    assert not image.isNull()
    assert image.save(str(report_dir / "host.png"))
    assert host.run_log.path.parent == bundle / "logs"
    results.append({"host": "portable initialization passed", "tasks": len(expected), "python": sys.executable})
    print(json.dumps(results, ensure_ascii=False))
finally:
    host._close_components()
    app.processEvents()
