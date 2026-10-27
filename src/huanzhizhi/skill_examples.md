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

## 自动与提示共用动作

两种模式共用识别结果、动作坐标和下面的调用，仅 `hint` 不同。窗口校验、客户区坐标转换和桌宠动作动画由现有接口处理；提示不发送鼠标输入，不需要业务选择 `line/click` 显示类型或维护动画计时。

```python
# info、x、y 来自当前已核对的截图，两种模式传同一个目标。
api.post_click(info, x, y, hint=(mode == 'hint'), label='选择此棋子')
# 右键、双击仍用 action='right_click' / 'double_click'。
# 拖动也沿用同一组起终点及动作时长：
api.post_drag(info, x, y, end_x, end_y, duration=.5,
              hint=(mode == 'hint'), label='拖动到目标')
```

业务只根据实际画面判断动作是否仍有效、何时推进。例如象棋的两点动作：未选中时执行或提示起点，确认选中后执行或提示终点，确认真实落子后才推进棋局。提示返回不表示已操作；用户可选其他合法动作，需按实际画面重新判断。自动模式发送一次后等待结果，不能因下一帧没变化而重发。

提示使用原有过期保护，业务每次重新观察确认当前动作后再调用同一接口，宿主会续期而不重启动画。无法确认、暂停或动作失效时沿用 `session.emit_hint([], 'clear', '建议已失效')`；不要另开线程盲目续期。旧的多点标记和 `line` 辅助线接口仍可用，但新动作提示无需调用它们。退出沿用现有清理逻辑，先 clear 再发送唯一的 finished。

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

探索时直接调用上述截图与输入接口，保存前后画面及标注；固化为可运行技能后，使用宿主固定入口验证，在本次草稿目录执行：

```text
<项目python> -I -X utf8 -m huanzhizhi.skill_trial --source "已有技能目录或本草稿working/技能目录" --until-log "业务结果已确认的完整日志"
```

先确认来源适用于本次用户目标、窗口及实际状态。验证业务时，`--until-log` 必须对应技能在核对真实业务结果之后通过 `session.emit_log()` 发出的完整消息，不能用启动、初始化、开始分析等消息证明业务成功。没有这样的消息时只在 working 副本的实际成功分支补充正常运行日志，不改成无依据的成功声明。

若已有业务证据仍有效、本次只补验停止回执或资源释放，可用现有的“资源已就绪、进入可停止等待”日志触发停止，沿用同一入口及 `--until-log`，不再重放已通过的业务动作。确认收到无错误且 `stopped_by_request=true` 的 finished、最终 idle，并核对被修改的资源释放；这只能证明收尾，不能代替业务或尚未覆盖模式的验证。用 `--result stop_result.json` 等明确名称保留补验结果，不覆盖此前业务证据。

入口复制已安装技能的可分发文件到本草稿 working，不复制原运行数据；已有副本直接传 working 路径。每个模式默认最长运行 30 秒，`--timeout` 可指定不超过 180 秒的本次运行时限，参数用 `--options` 传 JSON。保持命令工具 `tty=false`。

声明了 `mode` 参数且包含 `hint`、`auto` 的技能，传 `--options '{"mode":"auto"}'` 时入口固定先验提示、再验自动；提示失败不会启动自动。提示阶段拦截宿主鼠标、键盘、文本和滚轮输入，尝试输入即记为失败，捕获异常后继续显示提示也不能通过。按宿主实时显示记录确认同一建议连续可见至少 2 秒，再停止并等待无错误的停止回执、idle 和宿主清除记录；技能自身日志不能代替显示证据。只补验提示时传 `--options '{"mode":"hint"}'`，无需 `--until-log`。自动与普通技能沿用原 TaskProcessManager、本轮输入模式及宿主通道，匹配业务日志后停止并核对收尾。

`first_action.json` 在启动前建立，并随事件实时更新；`status=running` 表示尚未验完，`passed/failed` 才是最终结果。`runs` 保留各模式的耗时、错误与停止回执，`host_hints_path` 指向本轮宿主即时追加的真实显示/消失记录；无需等模型返回或另行交接。详细技能事件放在副本自身 `.runtime/runs/trial_*/events.jsonl`。自动模式匹配消息只是所选业务确认分支的证据，仍须结合实际画面与该分支的核对依据判断结果。超时、正常退出或仅收到 finished 不等于成功。未确认结果时先查看已有错误与画面，不重发输入。

验证取得真实结果后，沿用同一份实现和样本。后续只补验受改动影响的部分和未覆盖要求，影响不明时扩大验证；必要运行完成后结束，不编写断言套件、模拟用例或审计报告。提示通过本轮宿主通道实际展示，使用现有画面与事件核对，不把桌宠窗口枚举、显示计时等验收代码写进技能业务循环。已有实现完全满足目标时直接说明可复用，不重复创建同功能技能。

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
