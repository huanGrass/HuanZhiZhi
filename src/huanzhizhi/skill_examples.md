# 常用调用示例

只阅读当前任务需要的段落。以下片段使用现有 API，放入本次草稿后按实际目标填写参数；不需要给它们再加包装层。业务识别、策略及完成判定优先沿用相关已有技能。

## 选定窗口与截图

输入是用户选定窗口的实际标题特征、客户区宽高及句柄。`resolve` 会检查匹配且拒绝歧义，`refresh` 会检查窗口是否仍符合规则，`capture_client` 返回 RGB 的 PIL 图像。整窗截图的坐标原点是客户区左上角，点击使用同一坐标系。

```python
from huanzhizhi.platform.target_window import Win32DesktopApi, WindowRule

def capture_selected(title, width, height, hwnd):
    api = Win32DesktopApi()
    rule = WindowRule(title, width, height)
    info = api.resolve(rule, selected_hwnd=hwnd)
    image = api.capture_client(info)
    return api, rule, info, image
```

后续循环使用 `info = api.refresh(info, rule)` 与 `image = api.capture_client(info)`。从当次截图确认目标坐标 `(x, y)` 后，调用 `api.post_click(info, x, y, action='click')`；不要再发送点击动画事件。调用须位于 `TaskSession` 或创建流程提供的点击事件通道内。返回仅表示消息已投递，须用新截图核对实际结果。等待画面变化时使用截止时间和 `session.wait_for_stop(...)`，不要因画面暂未更新而重复点击。

## 通用异常确认

对可能由动画、加载或短暂遮挡引起的观察异常，使用 SDK 的 `confirm_observation`。检测函数每次重新采集状态，并对同一个参考状态做完整校验；不满足且可能自行恢复时抛出 `ObservationPending`，通过时返回实际结果。不要把所有 `Exception` 或 `ValueError` 统一转成可复检异常。

```python
from huanzhizhi.task_sdk import ObservationPending, confirm_observation

def observe_result():
    current_info = api.resolve(rule, selected_hwnd=info.hwnd)
    image = api.capture_client(current_info)
    state = recognize(image)  # 使用技能已有的只读识别函数
    if not is_expected_result(reference, state):
        raise ObservationPending('结果尚未完整显示')
    return current_info, state

records = []
current_info, state = confirm_observation(session, observe_result, records=records)
```

默认首次检测失败后每隔 200 毫秒复检一次，最多复检 3 次（合计 4 次检测）。不同错误共用同一预算，不因错误变化而重置次数。可按已知业务等待时限传入 `retries`、`interval`、`timeout`；次数耗尽或总时间到期时抛出最后一次原始观察异常。`timeout` 限制继续等待和启动下一次检测，不强制中断正在运行的函数；检测中的 I/O 必须自行设置超时并响应停止。

每次未通过及复检恢复都会写入会话日志；可选 `records` 列表记录检测序号、已耗秒数和错误文本（通过时为 `None`），由技能随其截图、参考状态和每次观测保存。调用时使用新的列表，避免整段任务无限累计。

复检函数不得包含点击、提交、发送、写文件等目标修改操作；这些操作执行一次后，只复检实际结果。参数错误、权限错误、引擎错误等未显式标记的异常直接传播。用户停止和 `InterruptedError` 立即结束确认，不自动重试。确认返回后仍按原流程在下一次操作前核对目标和坐标。公共机制不会自动接管未接入的技能。

## 共享引擎与模型

扫雷和围棋优先使用[共享游戏模块示例](game_examples.md)。围棋的 `go.start_engine()` / `go.analyze()` 已实现下述 KataGo 通信与进程清理，直接调用即可；无需在技能内再写一套。以下底层约定供其他引擎或接口排查参考。

先按用途筛选 [支持清单](shared_components.json)。当前 KataGo 的现有定位接口如下；返回引擎路径、模型路径、DLL 目录列表和只供子进程使用的环境变量，不启动引擎。

```python
from huanzhizhi.python_runtime import python_executable
from huanzhizhi.shared_components import katago_paths, supported_components

component = next(item for item in supported_components() if item['id'] == 'katago')
root = python_executable().parent.parent
engine, model, dll_directories, environment = katago_paths(component, root)
```

若采用 `check_katago(component, root)` 做环境检查，必须消费全部结果并检查每项 `satisfied`；仅拿到生成器不表示通过。启动技能时仍保留 SDK 的 `environment_checks`，创建阶段的验证不替代运行前检查。

持续对局采用以下生命周期，按清单中的官方分析协议接入，不把单次环境检测脚本原样放进每轮循环：

1. 进入技能时创建临时配置，使用上面的路径和环境启动一个 `katago analysis -config <配置> -model <模型>` 子进程；Windows 使用 `subprocess.CREATE_NO_WINDOW`。配置与日志放在临时目录或技能数据目录。
2. 每轮输入当前棋局 JSON，包含唯一 `id`、`moves`、规则、贴目、棋盘尺寸及搜索限制；从中途局面开始时提供 `initialStones` 并确认当前行棋方。通过 `stdin.write(json.dumps(request) + '\n')` 后 `flush()` 发送，保持 stdin 打开，不在每轮 `close()` 或调用等待进程退出的 `communicate()`。
3. 持续读取 stdout 的逐行 JSON；核对响应 `id`、错误字段、搜索完成标志与行棋方后，使用 `moveInfos`。持续消费 stderr 或定向到日志文件，避免管道写满；stdout 的读取应支持超时和 `session.stop_requested`，不要让主循环卡在无期限 `readline()`。
4. 正常结束、异常或用户停止都在 `finally` 中关闭进程和文件、回收临时目录；退出等待超时后终止，必要时 kill 并回收。不要保留孤立进程。

其他支持连续请求的引擎同样在技能运行期间复用进程；可复用模型只加载一次。首次初始化时间与单轮分析时间分别记录，避免用“整轮慢”猜测求解器耗时。不要求不支持连续请求的工具保持常驻。

## 集中验证

以下脚本放在草稿的 `verify.py`，填写真实技能 ID、草稿入口和当次参数。用项目解释器以 `-I -X utf8 -m huanzhizhi.script_runner <verify.py绝对路径>` 运行，命令工具使用 `tty=false`。示例只记录事件与耗时；根据用户目标增加实际画面断言，在目标事件确认后调用 `manager.stop()` 验证停止。超时只是终止条件，不能算成功。

```python
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from PySide6 import QtCore
from huanzhizhi.tasks.process import TaskProcessManager

app = QtCore.QCoreApplication([])
manager = TaskProcessManager()
records, errors, states = [], [], []
started = time.perf_counter()
timed_out = False

def record(event):
    records.append({'seconds': time.perf_counter() - started, **asdict(event)})
    # 在确认本次目标事件后调用 manager.stop()；不能把任意状态当作成功。

def state_changed(state):
    states.append(state)
    if state == 'idle':
        app.quit()

def timeout():
    global timed_out
    timed_out = True
    manager.stop()

timer = QtCore.QTimer()
timer.setSingleShot(True)
timer.timeout.connect(timeout)
manager.event_received.connect(record)
manager.error.connect(errors.append)
manager.state_changed.connect(state_changed)
try:
    manager.start('技能名称', Path(__file__).with_name('task_draft.py'), {},
                  input_mode=os.environ.get('HUANZHIZHI_INPUT_MODE', 'background'))
    timer.start(30000)  # 按任务设置验证时限；options 应使用当次真实参数。
    app.exec()
finally:
    timer.stop()
    manager.close()
print(json.dumps({'events': records, 'errors': errors, 'states': states,
                  'timed_out': timed_out}, ensure_ascii=False))
```

核对实际画面、业务结果、`FinishedEvent.error`、`stopped_by_request`、进程错误与最终 `idle` 状态。连续任务可在同一次短运行里覆盖两轮及停止；新改动影响哪一段就验证哪一段，必要的边界和错误处理仍需覆盖。验证通过后交付，不无依据重复扩展检查。

## 技能临时控制桌宠

桌面互动技能可使用 `TaskSession.acquire_pet()` 接管真实桌宠，返回当前主题、角色中心锚点 `pivot` 和各朝向/姿态的几何信息。必须由支持接管事件的桌宠宿主启动；仅创建 `TaskProcessManager` 而未接入 `TaskHostComponent`，不能处理接管请求。技能自建窗口使用包内 PySide6，游戏或其他互动规则全部保留在技能目录中。

```python
geometry = session.acquire_pet()
try:
    # x、y 是角色中心锚点的 Qt 全局逻辑坐标，不是 Win32 物理像素。
    session.update_pet(x, y, facing='left', pose='reach')
finally:
    session.release_pet()
```

`facing` 为 `left` / `right`；`pose` 为 `idle` / `move` / `reach`，复用宿主现有素材。`geometry['poses'][facing][pose]` 中的 `bounds=[x,y,width,height]` 和 `contact=[x,y]` 都是相对中心锚点的偏移。若要让接触点落在球拍中心 `(cx, cy)`，则提交 `(cx-contact[0], cy-contact[1])`。不要把立绘窗口左上角当成中心锚点。

宿主每次只保留最新待绘制状态，约每 16 ms 应用一次；技能建议以 30–60 Hz 更新，不等待每帧回包。该接口只改变角色显示，绝不移动鼠标或发送系统输入。坐标必须有限，中心锚点必须在屏幕内；技能负责按角色边界限制自己的活动区域。

飞碟与扫帚的 `idle` 姿态另提供 `head=[x,y]`、`head_radius` 和 `head_radius_x`：头部中心相对角色锚点的偏移，以及近似椭圆的纵向、横向半径。缺少横向半径时可回退到 `head_radius`。这是通用角色几何信息，是否碰撞、哪些部位参与互动仍由技能决定。

接管期间角色本身穿透鼠标输入，常规点击指向和自主动画暂停，前台输入及主题/大小切换被拒绝。归还恢复原有待机位置、朝向与交互。停止、进程崩溃、隐藏桌宠和显示器变化由宿主兜底释放；隐藏或显示器变化同时停止所属技能。正常代码仍应在 `finally` 归还。

GUI 技能在每次工作帧开始调用 `session.poll_pause()`，返回真时跳过模拟/工作但继续处理 GUI 事件及停止；该方法会确认宿主暂停/继续请求，不阻塞 Qt 线程。普通工作线程继续使用 `wait_if_paused()`，不要在同一循环混用两种暂停接口。
