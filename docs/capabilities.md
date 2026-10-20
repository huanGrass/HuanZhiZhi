# 通用能力介绍与调用方法

这里记录实际可调用的方法，不包含数独求解、游戏通关等业务任务。视觉实现放在 `src/huanzhizhi/vision/`，能力目录在 `src/huanzhizhi/capabilities.py`，包含方法名、用途和真实参数签名；现有游戏任务也引用公共视觉实现。本文源码路径均相对于项目根目录。

## 现行技能开发

“创造新技能”由 `src/huanzhizhi/skill_creation.py` 使用任务提示和[技能开发知识索引](../src/huanzhizhi/skill_creation.md)，先复用相关已有技能与[常用调用示例](../src/huanzhizhi/skill_examples.md)，只补缺失部分；接口信息不足或实际调用失败时再深入相关源码。集中验证改动涉及的调用链，交付 `output/<中文技能名称>/task.py`。之之检查描述及命名后保存到 `test_tasks/<中文技能名称>/task.py`，由用户手动“加入正式”后才移动到 `tasks/`，继续使用现有加载运行层。

“修改技能”从任务详情单独进入，在草稿的 `working/<技能目录>/` 修改已有技能副本，保留原 ID、名称和目录名；完成后交付 `output/<技能目录>/task.py`，由之之检查原文件变化、备份并替换。两个入口及保存行为详见 [README](../README.md)。

Python 任务可以直接调用 `from huanzhizhi import vision`，具体参数以 `src/huanzhizhi/vision/__init__.py` 导出的方法及实现为准；窗口、截图和输入接口见 `src/huanzhizhi/platform/target_window.py`。完整机器可读能力目录可用以下命令查看：

```powershell
.\python\python.exe -I -X utf8 -m huanzhizhi.capabilities
```

## 按需求选择

| 需要做什么 | 优先调用 | 适用边界 |
| --- | --- | --- |
| 固定图标、按钮找位置 | `FindTemplateList` | 使用已有图片或截图裁剪；相似图标要缩小范围并核对 |
| 颜色不同但形状相似 | `FindTemplateWithColorCheckList` | 同时比较形状和颜色 |
| 图案缩放或旋转 | `FindFeature` | 适合纹理丰富的图案，需足够有效特征点；纯色小按钮不适合 |
| 按颜色找按钮、标记 | `FindColorComponents` | RGB/HSV 规则加面积、宽高和形状条件 |
| 去背景再找物体 | `BuildForegroundMask` → `FindMaskComponents` | 背景可描述且前景可分离 |
| 识别文字或找文字位置 | `ReadText` / `FindText` | Windows 已安装语言包；结果没有置信度，需核对 |
| 比较画面是否相似 | `ImageHash` → `HashDistance` | 快速相似度，不作为操作成功的唯一证据 |
| 比较多个小图样本 | `MatchPatchSignatureList` | 图片先裁剪至 512×512 内，不包含游戏规则 |
| 找规则排列 | `FindRegularGridCells` | 输入候选点或矩形；推测补全的格子不等于实际检测到的物体 |
| 识别不准时看原因 | `DiagnoseMatch` / `DiagnoseCandidates` | 查看颜色、匹配分数及候选范围 |

## 旧探索协议与流程兼容

以下输入表、AI 调用和方法组合格式对应保留的 `src/huanzhizhi/skill_exploration.py` 旧探索实现。现行创建入口使用上文的普通 Python 开发流程。已有 `flow.json` 技能通过自身 `task.py` 调用 `src/huanzhizhi/visual_flow.py` 的 `task_main`，继续由原加载运行层执行；`src/huanzhizhi/flow_program.py` 和 `src/huanzhizhi/skill_methods.py` 保留识别程序与自定义计算方法的兼容支持。下述协议供维护这些旧流程和产物时参考。

### 输入和宿主能力

| 名称 | 用途与参数 | 状态 |
| --- | --- | --- |
| `sequence` | `{"steps":{"result":"求解调用id","path":["steps"]}}`；一次求解整批动作，批内只做局部核对 | 已接入 AI；目标必须由当前数据推导并位于本次识别区域内 |
| `click` | 左键单击 `target` 中心；参数 `{}` | 已接入 AI，沿用原点击与桌宠指示 |
| `right_click` | 右键单击；参数 `{}` | 已接入 AI |
| `double_click` | 左键双击；参数 `{}` | 已接入 AI |
| `drag` | 按住左键拖动；`{"end":[x,y],"duration":0.5}` | 已接入 AI，停止时释放按键 |
| `scroll` | `{"delta":-120}`；正数向上，120 一格，最多 10 格 | 已接入 AI |
| `key` | `{"key":"ENTER"}`；单键，支持方向键、导航键、A～Z、0～9、F1～F12 | 已接入 AI；不支持组合键 |
| `text` | `{"text":"输入内容"}`；最多 1000 字，无控制字符 | 已接入 AI；不自动提交 |
| 窗口查找、截图、裁剪 | `Win32DesktopApi`、WGC | 宿主自动使用；AI 只操作用户选定窗口 |
| 等待、画面稳定、操作结果核对 | `VisualFlow.wait/observe_settled/verify` | 宿主执行；AI 可返回 `wait` |
| 激活窗口、调整大小 | `Win32DesktopApi.focus/set_client_size` | Python API 已有，旧探索协议未开放自动调用 |

后台消息输入需要目标程序支持，按键和文字需要目标输入焦点正确。Qt 测试窗口通过不代表所有游戏都会响应；操作后仍须核对结果，失败不能当作成功。OCR 使用本机 Windows 语言包，不自动下载依赖、语言包或模型。

### 旧探索 AI 如何调用

当一次求解可以确定多个动作时，使用 `sequence` 保存这批计算结果。每项包含 `action`（click/right_click/key）、`target`（LTRB）、`parameters`、`when`、`after` 和布尔值 `skip`；两项条件都是完整的独立 `calls/checks` 程序。整个队列在首次输入前校验；每次输入前检查窗口和局部 `when`，输入后只等局部 `after` 成立。`skip=true` 允许跳过被前序动作连带改变的目标。批内不会重新求解、搜索整盘或固定等待0.4秒；请使用小ROI的颜色、字形等轻量条件。批后由节点的 `after` 核对整体结果。数独可一次计算全部空格，扫雷只计算当前局面能确定的一批，下一批再识别新线索。失败或停止保留已完成输入，不自动重新执行整批。

优先在一次 `status="plan"` 中同时提交准备和本地流程：`arguments` 可为 `{"items":[...]}`，无需准备时为 `{}`。条目为 `{"kind":"inspect","id":"唯一id","method":"方法名","arguments":{...}}` 或 `{"kind":"define","method":"custom_名称","arguments":{...}}`，最多32项。按顺序运行，同批后续项可以引用前项结果；准备通过后直接编译、执行 `program`。出错即停止，保留已完成结果并反馈错误，尚不执行动作。确实需要先检查未知识别结果时才单独使用 `status="batch"`，其 `arguments` 格式相同。

`status="plan"` 的 `program` 为 `{"nodes":[...],"done":完成判定或null}`，最多16个节点。每个节点包含 `action/message/expected/parameters/program/after`；`program` 是实时识别及目标计算，`after` 是操作前后核对的 `calls/checks` 对象。宿主自动循环截图、匹配、求解、输入和核对；未来状态无需先出现在当前截图中。只有核对通过的节点能保存，未知状态或规则错误返回 AI 后暂停该候选流程，保留它供修正，不在下一轮重跑它来覆盖最新提议的错误；核对超时也返回 AI，超时保留待核对动作且不重复输入。完成条件成立后保存技能，重放无需 AI。批量准备和流程提交的顶层 `action="none"`；有待核对动作时，可在同一份 plan 的 verification 提交独立核对；宿主先比较操作前后，再允许新规则执行。

无待核对动作时，`act` 可在 `program.verification` 预先声明本次操作后的条件，宿主转换为单节点 `plan`，执行相同的操作前检查、结果核对和保存逻辑。有待核对动作时，`verification` 仍表示上一步的结果依据。已有调用既可写 ID，也可完整写回完全相同的定义；同名但内容不同的定义仍会拒绝。

尚未掌握后续状态的单次探索使用不带 `verification` 的 `act`，操作后交给 AI 核对；局部流程会连续匹配，必须排除已操作状态。`plan` 顶层重复的动作字段不会产生额外输入，实际只执行经校验的节点动作。只读 `inspect/define/batch` 只采集证据并保留待核对操作，不能据此保存成功记录或继续输入。

首次探索 `act` 若在输入前因画面、目标或输入数据变化被拒绝，会把原因反馈给 AI 重新观察，不创建待核对动作。已经开始输入后的失败仍停止，不自动重试。输入前的像素和实时识别检查保持不变。

裁剪优先调用 `CropRect`，使用 `[left,top,right,bottom]`，再通过 `result` 交给找图或自定义方法。最近最多四张有效识别结果图片会附在 AI 请求中，文件顺序见 `context.inspection_images`；这让局部图可供检查，实际输入仍必须重新识别。旧的内联 `crop` 宽高格式继续兼容。

探索采用 JSON 协议，优先组合通用方法，需要规则或算法时可定义技能自己的 Python 方法。宿主在请求 AI 前使用 Windows 默认 OCR 语言读取当前画面，将文字与真实像素坐标放入 `context.screen_text`；OCR 不可用时返回原因，仍可使用其他视觉方法。观察结果不能替代实时识别规则。调用方法时返回 `status="inspect"`，`method` 填方法名，`arguments` 直接填写 JSON 对象，`action="none"`。其余已有状态字段照常填写。宿主运行后把结果放入 `context.inspections`，AI 再决定下一步。最近最多4张有效的局部裁剪也会作为图片附上，四分区观察不会丢失半幅内容。待核对状态由宿主管理，模型不填写 previous；不能只因工具调用结束就宣称操作成功。本地动作核对成功后，也会写入 `context.history`，避免下一轮重复提议已完成的步骤。

`context.diagnostics` 提供本轮本地识别和自定义方法失败的具体原因，最多保留最近8条。即使流程对外表现为“条件不匹配”，AI 也能看到内部参数或计算错误；这些诊断不会使失败条件通过，也不会代替操作验证。

示例方法及参数（填写在 `operation` 中，`arguments` 直接使用对象）：

```json
{"method":"FindColorComponents","arguments":{"color":{"red_min":210,"green_min":65,"green_max":175,"blue_max":90},"roi":[0,0,800,600],"min_area":120,"min_width":30,"max_count":10}}
```

```json
{"method":"ReadText","arguments":{"roi":[100,200,500,300],"language":"zh-Hans-CN"}}
```

图片参数默认使用当前截图。参考模板可以写 `{"crop":[x,y,width,height]}`，或者 `{"asset":"inspect-1.png"}`；素材仅限本次探索目录。前一次原始结果可以用 `{"result":"inspect-1"}`，部分结果用 `{"result":"inspect-1","path":[0,"rect"]}`。例如把颜色掩膜交给连通区域检测：

```json
{"method":"FindMaskComponents","arguments":{"mask":{"result":"inspect-1"},"min_area":100}}
```

- 视觉方法的 `roi` / `rect` 和结果 `rect` 是 `[left,top,right,bottom]`，右下边界不包含；ROI 全部为 0～1 时按比例解释。
- 模型不再填写未用于执行的顶层 `target/evidence` 标注。`crop` 引用是 `[x,y,width,height]`；可执行的 `program.target` 和批内 `steps.target` 则是 `[left,top,right,bottom]`。全部基于窗口客户区截图的物理像素。
- `FindText` 忽略中文字符间的 OCR 空格，保留英文单词边界，返回所有匹配行的列表，原文与矩形不变。用 `SelectOne` 确认唯一候选后再引用其 `rect`。
- RGB 通道范围 0～255；HSV 的 H 为 0～179，S/V 为 0～255。颜色字典使用 `red_min/red_max` 等，HSV 使用 `hue_min/hue_max/sat_min/sat_max/val_min/val_max`。
- 图片或掩膜结果保存成 PNG；原始结果在本次探索内可继续引用。色块特征的二进制部分不发送给 AI，但可通过 `result` 继续比较。
- 错误会明确返回；没有匹配、多个候选、OCR 缺少语言包不能当成成功。结果过大时要求缩小 ROI 或 `max_count`。
- `max_count` 允许 1～1000 的整数，可一次覆盖较大的网格；达到上限的结果可能被截断，不能视为完整。图像运算的 `size/block_size/radius` 仍限制为 1～100 的整数。
- 每次识别的方法、参数及结果写入 `inspect-*.json` 和探索日志。创建技能时保存需要执行的方法、依赖关系、动作参数引用与核对条件；重放重新截图、重新调用方法，使用本次结果操作。记录中的旧文字和坐标不作为运行结果复用。
- 画面变化后清除旧识别结果，调用定义保留在 `context.known_calls`，可在 `program.calls` 引用原编号重新执行。已冻结的参考模板继续使用原素材，不从变化后的旧位置重新截取。
- 尚未执行的提议若未通过结构、识别条件或动态参数校验，会通过 `context.rejected_decision` 返回具体错误供 AI 修正，并计入40次探索限制。数字按键参数也必须是字符串。实际输入之后仍须独立核对结果，不能把提议被拒绝当成已执行。

### 旧探索保存可执行的方法组合

缺少现成计算方法时，先返回 `status="define"`，`method="custom_方法名"`（小写字母、数字、下划线），`action="none"`，`program=null`。`arguments` 直接包含以下对象：

```json
{
  "description": "从当前识别文字计算加法答案，输入为text对象，输出为答案字符串",
  "source": "def run(data):\n    return str(sum(int(part) for part in data['text'].split('+')))\n",
  "tests": [
    {"data":{"text":"8+3"},"expected":"11"},
    {"data":{"text":"29+14"},"expected":"43"}
  ]
}
```

每个方法必须定义 `run(data)`，可以写辅助函数、递归、循环、搜索和游戏规则。输入输出使用 JSON 数据；识别图片或掩膜可先裁剪至512×512以内，传给规则时转为像素行数组。代码可使用基础内置计算函数，以及受限的 `math`、`collections`、`itertools` 计算函数；不支持类、装饰器、类型注解、文件、网络和窗口操作。目标矩形必须转换为完整客户区坐标。

提供1～8个样例，使用 `data` 和 `expected` 检查结果，也可以使用 `data` 和 `error` 检查错误片段。样例应覆盖不同局面和无效输入。定义时实际执行全部测试，一次返回所有失败项及各自的输入、预期和实际结果（每项有长度限制）；失败的方法不会注册，可以修正后重试。成功后保存 `custom_*.py` 和包含源码摘要、样例的同名 JSON。已验证名称不可覆盖，修订使用新名称；源码与验证版本不一致时拒绝运行。

之后调用 `inspect` 或在 `program.calls` 中使用此方法，参数为 `{"data":{"result":"识别调用id"}}`，也可组合多个结果。定义测试使用的样例只用于测试，重放必须传入当次识别数据。方法只计算并返回结果，宿主继续负责动作、窗口匹配和前后核对；计算在可停止的独立进程中运行，每次限时10秒，诊断文件写入技能目录的 `.runtime/runs/method-runs/`。

旧探索实现保存的 `flow.json` 使用 `version: 2`。每个操作节点包含 `program`（识别和数据处理步骤）、`parameters`（动作参数）和 `after.program`（操作后的核对步骤）。最终完成条件也包含可执行的识别步骤。运行时按条件选择匹配节点，连续任务可回到已验证节点继续循环；不同操作同时匹配时停止，不猜选。

探索中复用步骤后，若原核对规则超时，保留本次操作及操作前截图，交回 AI 继续检查新结果，期间不重复输入。只有新结果也通过前后核对后才补充为已验证分支。已保存技能的无 AI 重放遇到核对超时仍会停止。

模型顶层只填写 message/detail/name/continuous 和 operation；operation 根据 status 填写所需字段，宿主补齐执行器需要的默认值并管理待核对状态。arguments、program、parameters、引用与检查值统一使用普通 JSON 对象或值，不再把 JSON 放进 JSON 字符串。Python 方法输出也使用同一格式，无须在源码里序列化调用参数。没有依赖 provider 的输出 schema；宿主解析输出并保留现有结构、参数、实时识别及操作前后校验，解析错误返回 AI 修正且不执行输入。下面的调用格式同时用于模型输出和保存文件：

```json
{
  "calls": [
    {"id":"words","method":"ReadText","arguments":{"language":"en-US"}},
    {"id":"button","method":"SelectOne","arguments":{
      "items":{"result":"words","path":["lines"]},
      "field":"text","value":"Continue"
    }}
  ],
  "checks": [{"value":{"result":"button"},"op":"exists"}],
  "target": {"result":"button","path":["rect"]}
}
```

这个步骤每次读取当前画面，从当前 OCR 结果选择唯一的 Continue，点击这次识别出来的矩形中心。按钮移动后仍按新位置操作。`calls` 也可使用已经试过的编号字符串，例如 `"inspect-1"`；保存时会展开其真实方法、参数与依赖，不能只保存编号或旧结果。

`checks` 的条件全部满足才执行；支持 `exists/empty`，以及带 `expected` 的 `eq/ne/gt/ge/lt/le`。完成条件只需 `calls` 和 `checks`。检查必须来自当前画面，动作目标必须引用当前识别矩形。没有可重复执行的方法组合时不会退回固定坐标点击，也不会发布技能。

动作参数也支持结果引用。例如 `text` 的参数可以是 `{"text":{"result":"words","path":["text"]}}`，每次输入新读取的文字。`RectCenter` 可将识别矩形转成拖动终点，`CountItems` 可把识别数量用于条件比较。现有方法无法表达的推理应定义自有规则并验证；不得把当时算出的答案伪装成通用规则。

有待核对输入时，返回的 `program` 可以包含 `verification`，其内容是独立的 `calls/checks` 核对程序。它保存为上一动作的 `after.program`，与当前动作的识别程序分开。核对条件可用 `{"previous":"调用id","path":["字段"]}` 引用同一方法在操作前截图上的结果，例如当前剩余空格数 `lt` 操作前空格数，或当前棋盘 `ne` 操作前棋盘。重放会重新计算操作前后的数据；不能将 `previous` 用于动作目标、动作参数或当前方法的输入。

截图主要保留作诊断和找图素材。用于 `template/templates` 的参考图会固定保存；作为搜索输入的画面、裁剪区域和中间掩膜每次重新计算。模板已保存后，不再执行仅用于制作模板的无用步骤。

已保存的 `version: 1` 和 `version: 2` 流程仍按其原格式执行，不自动改写或删除。兼容运行时仍要求所选窗口和客户区尺寸匹配，识别算法自身的尺寸、语言、纹理等适用条件也仍然有效。

## 来源与取舍

| 来源 | 采用内容 | 未照搬内容 |
| --- | --- | --- |
| [MaaFramework Pipeline](https://maafw.com/en/docs/3.1-PipelineProtocol/) | 按识别与动作分类、明确参数、区域和结果的能力组织方式 | 未接入 Maa；不提供任意 Shell 或绕过宿主输入的代码执行 |
| [OpenCV 特征匹配](https://docs.opencv.org/4.9.0/d1/de0/tutorial_py_feature_homography.html) | 特征对应、RANSAC 内点及有效几何范围核对 | 特征不足时不默默切换算法或假报成功 |
| [Windows OCR](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine) | 系统文字识别，返回行、词及位置 | 没有伪造置信度 |
| [Win32 滚轮消息](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-mousewheel) | 原生后台消息输入，滚轮使用屏幕坐标 | 不以投递成功替代界面结果验证 |

通用能力目录及旧探索协议没有引入神经网络分类/检测、UI Automation 控件树、组合快捷键或任意脚本动作。窗口、截图、坐标换算与桌宠点击沿用本项目现有实现。

## 视觉方法完整介绍表

下面的表由运行时目录生成；参数签名以机器可读目录和代码为准。

<!-- capability-table -->

| 方法名 | 用途 |
| --- | --- |
| CropRect | 裁剪区域，保存为图片，可交给其他识别方法 |
| sample_abs_rgb | 按像素坐标取色 |
| sample_rgb | 按0～1比例坐标取色 |
| AverageColor | 区域平均RGB颜色 |
| ColorSimilarity | 两种颜色相似度，0～255 |
| ColorsSimilar | 两种颜色是否达到相似度阈值 |
| IsColor | 比例坐标处是否满足RGB颜色规则 |
| BuildColorMask | 按RGB或HSV范围生成掩膜 |
| BuildSimilarColorMask | 按参考颜色及相似度生成掩膜 |
| CountRgb | 统计符合RGB规则的像素数 |
| CountHsv | 统计符合HSV规则的像素数 |
| CountSimilarColor | 统计与参考颜色相似的像素数 |
| FindColorRectList | 查找符合颜色规则的区域 |
| FindSimilarColorRectList | 查找与参考颜色相似的区域 |
| FindColorComponents | 按颜色找连通区域，并按面积、宽高、形状过滤 |
| BuildForegroundMask | 去除指定背景色，得到前景掩膜 |
| FindForegroundComponents | 去除背景后查找物体区域 |
| FindMaskComponents | 从掩膜查找区域并按面积、形状过滤 |
| GetBBox | 获取图像中的边界范围 |
| GetMaskBBox | 获取掩膜中非零部分的边界范围 |
| NmsRectList | 去除重复、重叠候选区域 |
| FindTemplate | 找图，返回最佳候选；固定尺寸，须检查分数和歧义 |
| FindTemplateList | 找图，返回多个候选；适合重复图标 |
| FindTemplateLuma | 按亮度找图，适合颜色变化较大的画面 |
| FindTemplateLumaList | 按亮度查找多个图案 |
| FindTemplateBinary | 二值化后找图，适合轮廓清楚的图案 |
| FindTemplateBinaryList | 二值化后查找多个图案 |
| FindTemplateByMask | 只匹配模板掩膜覆盖的有效部分 |
| FindTemplateWithColorCheck | 找图并核对颜色，区分形状相同颜色不同的目标 |
| FindTemplateWithColorCheckList | 找图加颜色核对，返回多个候选 |
| FindFeature | SIFT/ORB特征匹配，适合有纹理且缩放、旋转的图案；不适合纯色小按钮 |
| ImageHash | 计算图像哈希，用于快速比较画面 |
| HashDistance | 比较两个图像哈希，越小越相似 |
| BuildColorBlockSignature | 建立小图的色块特征，需先裁剪至512×512以内 |
| CompareColorBlockSignatures | 比较已建立的色块特征 |
| MatchColorBlockTemplateList | 将小图与参考样本分类匹配，需先裁剪 |
| MatchPatchSignatureList | 比较小图样本并返回排序、分数和差距，需先裁剪 |
| GroupPoints | 把邻近点分组 |
| EstimateGridStep | 从坐标估计重复排列的间距 |
| BuildGridAxis | 根据坐标和间距建立规则轴 |
| FindGridAxes | 从点或矩形估计横纵规则排列 |
| FindRegularGridCells | 从候选点或矩形构建规则网格；不包含游戏规则 |
| FindGridTileCells | 根据候选中心和前景估计网格单元 |
| HasGridTileAt | 检查指定规则单元内是否存在前景 |
| BuildGridTileSignature | 为规则单元构建视觉特征 |
| FindTextLikeBBox | 定位亮色或暗色文字形状区域，不识别文字内容 |
| ExtractLetters | 按参考文字颜色提取字形图，暗色表示接近文字颜色 |
| ExtractWhiteLetters | 提取白色字形图，暗色表示接近白色 |
| ReadText | Windows OCR读取文字及原图坐标；language如zh-Hans-CN/en-US，无置信度 |
| FindText | Windows OCR查找文字，返回所有匹配行；多个结果不能直接猜选 |
| DiagnoseCandidates | 查看候选区域的颜色、面积等诊断依据 |
| DiagnoseMatch | 查看找图、颜色规则的诊断依据 |
| DiagnoseState | 核对多个指定位置的颜色条件 |

| 数据处理方法 | 用途 |
| --- | --- |
| SelectOne | 从列表选择唯一项，可按字段和值筛选；多项时报错，无匹配返回空 |
| CountItems | 统计列表或文字长度，供条件判断使用 |
| RectCenter | 将当前识别矩形转换为中心坐标 |
