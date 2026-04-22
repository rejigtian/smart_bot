# Smart-Androidbot Agent 架构文档

> 这个程序整体就是一个 Agent。不是某段提示词，而是六个层级协同工作的完整系统。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser (用户)                        │
│     填写测试套件 / 快速任务 / 查看结果 / 步骤回放 / 下载报告    │
└────────────────────────┬────────────────────────────────────┘
                         │ REST + SSE
┌────────────────────────▼────────────────────────────────────┐
│               FastAPI Server (cloud)                         │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              TestCaseAgent (Agent 本体)              │   │
│  │                                                     │   │
│  │  Planner → [SubGoal 分解]                           │   │
│  │  感知层 → 决策层 → 行动层 → 记忆层 → 验证层 → 回放层  │   │
│  │                                                     │   │
│  │  复杂任务:                                           │   │
│  │    Planner → SubAgent #1 → SubAgent #2 → ... → 汇总  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  SQLite: Device / TestSuite / TestCase /                    │
│          TestRun / TestResult / TestStepLog                 │
└────────────────────────┬────────────────────────────────────┘
                         │ WebSocket JSON-RPC
┌────────────────────────▼────────────────────────────────────┐
│              Portal App (Android 设备)                       │
│         执行实际操作：tap / swipe / input / screenshot        │
└─────────────────────────────────────────────────────────────┘
```

Agent 运行在服务器上，设备可以在任意网络。Portal App 通过反向 WebSocket 连接服务器，Agent 的工具调用经由 JSON-RPC 下发到设备执行。

---

## 二、六个层级在代码中的位置

### 层级 1：感知层 (Perception)
**Agent 看到什么**

| 组件 | 文件 | 作用 |
|------|------|------|
| `screenshot()` | `agent/ws_device.py` | 抓取设备当前截图（PNG bytes） |
| `get_ui_state()` | `agent/ws_device.py` | 获取 a11y 树，格式化为结构化文本 |
| `_resize_screenshot()` | `core/test_agent.py` | 截图缩小到 50%，返回 `(bytes, w, h)` |
| `_annotate_screenshot()` | `core/test_agent.py` | 叠加坐标网格 + SoM 蓝点 |
| `_prune_node()` | `agent/ws_device.py` | 过滤 disabled/invisible 节点和空容器 |
| `_format_node()` | `agent/ws_device.py` | 递归把 a11y 树转为带 index 的文本列表 |
| `format_ui_state()` | `agent/ws_device.py` | 组装完整的 `[Device State]` + `[UI Elements]` 文本 |

**截图处理流水线（每步）：**

```
原始设备截图 (1080×2400px)
    ↓ _resize_screenshot()
半尺寸截图 (540×1200px)  ← AI 在此图上给出像素坐标
    ↓ _annotate_screenshot()
标注截图：
  ① 坐标网格（每20%一条线，边缘标注真实像素值如 "108"/"216"）
     → AI 读取边缘数字即可确定坐标，无需估算
  ② SoM 蓝点（在每个 a11y 元素中心画编号蓝圆点）
     → AI 通过视觉确认点位再调用 tap_element(index)
    ↓ base64 发送给 LLM
```

**坐标转换（关键）：**

```
AI 输出: tap(x=270, y=600)   ← 在半尺寸截图中的像素坐标
服务器转换: x×2=540, y×2=1200 ← 设备真实坐标
```

半尺寸策略的优势：AI 的"自然直觉"就是像素坐标，不需要任何归一化数学。`_img_to_abs(img_x, img_y)` 就是简单的 `×2`。

**VLM Fallback（纯视觉元素检测）：**

当 a11y 树返回空列表时（Canvas/游戏/WebView 等场景），感知层自动触发 `detect_elements_vlm()`（`agent/perception.py`），通过 VLM 从截图中检测可交互元素。返回的虚拟元素列表与 a11y 解析器格式一致，可继续进行 SoM 标注和 `tap_element(index)` 调用。

**每步 LLM 收到的感知信息：**

```
[Previous State]              ← 上一步的 UI 树（对比变化）
[Device State]
  App: com.example (首页)
  Keyboard: hidden

[UI Elements]  ...
1. FrameLayout: "root"
  2. [tap] TextView: "com.example:id/tab_home" "首页"
  3. [tap] TextView: "com.example:id/tab_mine" "我的"
  ...

[Screenshot: 540×1200px — give pixel coordinates in this image for tap() and swipe()]
[当前标注截图]
```

---

### 层级 2：决策层 (Decision)
**Agent 怎么思考**

| 组件 | 文件 | 作用 |
|------|------|------|
| `SYSTEM_PROMPT` | `agent/prompt.py` | 角色定义 + 行为规则 + 坐标说明 + 恢复规则 + Memory 规则（`remember()` 工具） |
| `TOOLS` | `agent/tools.py` | OpenAI function-call 工具 schema |
| LLM 调用 | `core/test_agent.py` | `litellm.acompletion`，支持任意 provider |
| Planner | `agent/planner.py` | 为复杂任务生成执行计划，注入为 pinned message |
| Subagent 路由 | `core/test_agent.py` | 复杂任务（expected > 40字 或 path 含 ≥2 个 ">"）通过 `generate_subgoals()` 分解为 SubGoal，每个由独立 sub-agent 执行 |

**SYSTEM_PROMPT 的结构：**

```
角色定义：你是 Android UI 测试自动化 Agent

输入说明：每步收到 [Device State] / [UI Elements] / 标注截图

坐标系统：
  - tap() / swipe() 使用截图中的像素坐标
  - 截图边缘印有像素标签（如 "108"/"216"）
  - 服务器自动 ×2 转换为设备坐标
  - 宽高在每步消息中给出，作为坐标合法范围

导航规则（优先级）：
  1. 优先用 tap_element(index)，通过 SoM 蓝点视觉确认后再调用
  2. 只有无 a11y 节点的 Canvas 区域才用 tap(x, y)
  3. 需要找屏幕外元素时用 scroll()，不能用 swipe() 来滚动
  4. 看到加载/动画时调用 wait()，不要乱点

防误触规则（Avoid common mistakes）：
  - 不要点击 EditText/输入框，除非任务要求输入文本
  - 点击前先读元素 text/resourceId 确认意图
  - 游戏 UI 中寻找特定视觉线索，不要随机点击
  - 区分外观相似的按钮，先读文字再操作

恢复规则：
  - 跳出目标 app → 立刻 start_app() 恢复，不要直接 fail
  - start_app() 失败两次 → call list_packages() 确认包名

完成规则：
  - 不能基于假设调用 mark_done(pass)，必须截图确认
  - 卡住 3 次同一步骤 → mark_done(fail)
```

**工具顺序影响决策：** `TOOLS` 列表里 `tap_element` 排在 `tap` 前面且 description 写了 "PREFER this"，LLM 倾向于选择靠前且有明确倾向描述的工具。

---

### 层级 3：行动层 (Action)
**Agent 能做什么**

| 工具 | 文件 | Portal RPC | 说明 |
|------|------|-----------|------|
| `tap_element(index)` | `ws_device.py` | `tap` | **首选**：按 index 精确点击元素中心，坐标由服务器从 a11y 树计算 |
| `tap(x, y)` | `ws_device.py` | `tap` | 备用：截图像素坐标，服务器 ×2 转为设备坐标 |
| `scroll(direction, distance)` | `ws_device.py` | `swipe` | 滚动露出屏幕外内容（small/medium/large） |
| `swipe(x1,y1,x2,y2)` | `ws_device.py` | `swipe` | 手势滑动（非滚动场景） |
| `input_text(text, clear)` | `ws_device.py` | `keyboard/input` | 文本输入（base64 编码） |
| `press_key(key)` | `ws_device.py` | `keyboard/key` | 硬件按键（back/home/enter...） |
| `global_action(action)` | `ws_device.py` | `keyboard/key` | 系统级操作（back/home/recent/notifications） |
| `start_app(package)` | `ws_device.py` | `app` | 启动应用 |
| `list_packages()` | `ws_device.py` | `listPackages` | 列出所有已安装包名（start_app 失败时用） |
| `wait(seconds)` | `test_agent.py` | —（asyncio.sleep） | 等待加载/动画完成，防止在过渡中误触 |
| `mark_done(status, reason)` | `test_agent.py` | —（内部） | 声明测试完成（pass/fail/skip）→ 触发验证层 |
| `remember(key, value)` | `test_agent.py` | —（内部） | 存储笔记，跨上下文截断存活，每步以 [Agent Notes] 注入 |

**坐标转换细节（tap vs tap_element）：**

```
tap_element(index=7)
  → 服务器从 a11y 树取出 element[7] 的边界框
  → 计算中心点 (cx, cy) [设备坐标，直接用]
  → 发送 Portal RPC: tap(cx, cy)

tap(x=270, y=600)       ← AI 给的截图像素坐标
  → _img_to_abs(270, 600) = (540, 1200)  [×2]
  → 发送 Portal RPC: tap(540, 1200)
```

**Step 级重试：** `tap_element` 执行失败时（元素过期等），自动刷新 UI 树后重试一次，无需 agent 额外决策。

---

### 层级 4：记忆层 (Memory)
**Agent 记住什么**

| 记忆类型 | 实现 | 文件 |
|----------|------|------|
| 短期记忆（对话历史） | `messages` 列表 | `agent/memory.py` |
| 历史压缩 | `compress()` 每 4 步 LLM 摘要旧消息（`_summarize()` in `test_agent.py`），替代硬截断 | `agent/memory.py` |
| 工作记忆（操作历史） | `action_records` 列表 | `agent/memory.py` |
| 上一步 UI 状态 | `prev_ui_text` | `agent/memory.py` |
| 图片去重 | 旧消息中剔除 image_url | `agent/memory.py:drop_old_images()` |
| 参考示例（跨次） | starred result 的 action_history | `core/test_runner.py` |
| Agent 笔记 | `notes: Dict[str, str]` — 由 `remember()` 工具写入，每步以 [Agent Notes] 注入，永不截断 | `agent/memory.py` |
| 负面经验（跨次） | `LessonLearned` 表存储提取的误操作教训，运行前加载并作为 pinned message 注入 | `core/lesson_extractor.py` / `db/models.py` |
| Pinned 消息 | `pinned_count` — system + goal + reference + plan + lessons 全部 pinned，不参与截断 | `agent/memory.py` |
| 恢复级别 | `recovery_level` — 4 级递进（warn → auto back → auto restart → force fail），渐衰而非硬重置 | `agent/memory.py` |

**每步注入上下文的顺序（`build_step_text`）：**

```
[Agent Notes]            ← remember() 笔记（永不截断）
[History Summary]        ← 压缩后的旧步骤摘要（如有）
[Screenshot: WxH px]     ← 截图尺寸，AI 以此作为坐标合法范围
[Previous State]         ← prev_ui_text（上一步 a11y 树，对比变化用）
[Action History]         ← action_records 最近 5 条
[Current State]          ← 当前 a11y 树
Step N: What action should you take next?
⚠ WARNING (if stuck)     ← 卡住时注入恢复提示
```

**参考示例机制：** 每次运行前，从数据库加载同一 case 最新的 **starred（标记为参考）** 结果的 action_history，作为 soft reference 注入 goal 中。加载时自动检测并过滤"误操作→恢复"步骤对（如 tap 错误元素 → press_key back），只注入干净有效的步骤：

```
[Reference: a previous successful run took 5 steps]
  Step 1: 💭 "需要打开设置" → tap_element(3) → Tapped '设置'
  Step 2: 💭 "找到关于手机" → scroll(down) → 滚动成功
  ...
Use this as a soft reference — adapt to actual current UI. Do not copy indices blindly.
```

**负面经验机制（LessonLearned）：** 每次 case 执行结束后，`lesson_extractor.py` 分析步骤记录，提取误操作教训存入 `LessonLearned` 表。下次执行同一 case 前加载最近的 lesson，作为 pinned message `[Lessons from past runs — AVOID these mistakes]` 注入 Agent context。

提取方式：
- **Pattern-based 检测（零成本）：** `_detect_wasted_steps()` 识别 "tap → 键盘弹出 → back" 和 "tap 错误 → close" 模式
- **LLM 分析（可选）：** `analyze_with_llm()` 对步骤 trace 进行更深层分析

---

### 层级 5：验证层 (Verification)
**Agent 确认做对了吗**

| 组件 | 文件 | 触发时机 |
|------|------|---------|
| `LLMVerifier.verify()` | `agent/verifier.py` | agent 调用 `mark_done(pass)` 时 |
| Previous State 对比 | `agent/memory.py` | 每步自动注入，LLM 自行判断 |

**`verify()` 流程：**

```
agent 调用 mark_done(status="pass", reason="经验值从17增加到187")
    ↓
重新截图（fresh screenshot）
    ↓
发给 LLM（verifier 模型，可独立配置）：
  "Expected result: {expected}
   Agent's final observation: {reason}   ← 作为直接目击证据
   是否在截图中确认？JSON: {confirmed, reason}"
    ↓
confirmed=true  → 接受 pass，返回 CaseResult(status="pass")
confirmed=false → 拒绝 pass，注入 gap 描述（如 "页面还在首页，需要导航到设置"），循环继续
```

**静态 vs 动态验证：** verifier 的 system prompt 明确区分两种场景：
- **静态断言**（按钮可见、文字出现）：截图中直接确认
- **动态变化**（经验值增加、数字变大）：agent 的 reason 描述了前后值变化，加上截图显示最终状态，两者合并即为充分证据

**为什么需要这层：** 没有验证层时，agent 经常在错误页面调用 pass。强制验证把误报率从 ~40% 降到接近 0。

---

### 层级 6：回放层 (Replay)
**每步留存证据**

| 组件 | 文件 | 作用 |
|------|------|------|
| `StepLog` dataclass | `core/test_agent.py` | 单步数据结构：step/thought/action/action_result/screenshot_b64 |
| `CaseResult.step_logs` | `core/test_agent.py` | agent loop 收集的所有步骤 |
| `TestStepLog` 表 | `db/models.py` | 步骤持久化到 SQLite |
| runner 写入 | `core/test_runner.py` | 每个 case 结束后批量写 TestStepLog |
| API endpoint | `routers/testruns.py` | `GET /runs/{run_id}/results/{result_id}/steps` |
| Web UI | `frontend/src/pages/RunDetail.tsx` | 点击 case → 逐步回放时间线 |
| HTML 报告 | `core/report.py` | "▶ N步" 按钮 → 全屏 Step Replay 模态框 |

**每步收集时机：**

```
while steps < max_steps:
    截图 → 标注 → llm_img_b64       ← 保存为 _step_screenshot_b64
    LLM 决策 → msg_content          ← 保存为 _step_thought
    执行所有 tool_calls
    ─────────────────────────────
    StepLog(
      step=N,
      thought=_step_thought,
      action="tap_element({'index': 7}) | ...",
      action_result="Tapped element 7 at (550,200) | ...",
      screenshot_b64=_step_screenshot_b64,  ← AI 当时看到的截图
      prompt_tokens=...,
      completion_tokens=...,
      total_tokens=...,
      perception_ms=...,                    ← 截图+a11y 耗时
      llm_ms=...,                           ← LLM 推理耗时
      action_ms=...,                        ← 工具执行耗时
      subgoal_index=...,                    ← SubAgent 运行时的子目标序号
      subgoal_desc=...,                     ← SubAgent 运行时的子目标描述
    ) → step_logs_list
```

**回放的意义：** 每帧截图是 AI 做出该步决策时看到的画面，thought 是决策理由，action 是执行内容，三者完全对齐，可以精确还原"AI 在想什么、看到什么、做了什么"。

---

## 三、一次完整的测试用例执行流程

```
输入: TestCaseData(path="打开微信 > 我的 > 设置 > 关于微信 > 查看版本号", expected="显示微信版本信息和版权声明")

判断复杂度: expected > 40字 或 path 含 ≥2 个 ">" → 走 Subagent 路径

Subagent 路径:
  Planner → [SubGoal(1: 打开微信, 2: 导航到设置, 3: 查看版本信息)]
  SubAgent #1: 独立 memory → 完成 → SubResult(status=pass, summary="...")
  SubAgent #2: 收到上一个 summary → 独立执行 → SubResult(...)
  汇总 → CaseResult

简单任务路径: 原 6 层循环不变 ↓
```

```
输入: TestCaseData(path="修炼 > 收取经验", expected="经验值增加")

前置:
  1. 从 DB 加载 starred 参考示例（过滤浪费步骤）
  2. 从 DB 加载 LessonLearned（负面经验）
  3. Planner 规划（复杂任务）

Step 1:
  感知层 → screenshot() → resize 50% → annotate (grid + SoM dots)
  记忆层 → build_step_text: [Prev State][History][Current State][540×1200px]
  决策层 → LLM 决策: tap_element(index=5)  ← SoM 蓝点 5 号 = "修炼"入口
  行动层 → ws_device.tap_element(5) → Portal JSON-RPC → 设备执行
  记忆层 → action_records.append(...)
  回放层 → StepLog(step=1, thought="...", action="tap_element({'index':5})", ...)

Step 2~N:
  ... 同上循环 ...

Step N:
  决策层 → LLM: mark_done(pass, reason="经验值从17增加到187，修炼栏显示187/425")
  验证层 → LLMVerifier.verify(expected, agent_reason=reason)
    → 重新截图
    → verifier LLM: "动态变化 + 截图显示结果状态 → confirmed=true"
  回放层 → step_logs_list 赋值到 done_result.step_logs
  runner → UPDATE test_results + INSERT test_step_logs (×N行)
  返回: CaseResult(status="pass", steps=N, step_logs=[...])
```

---

## 四、数据模型

```
TestSuite ─── TestCase (1:N)
    │
TestRun ──── TestResult (1:N) ──── TestStepLog (1:N)
    │             │
    │         action_history_json   step / thought / action
    │         screenshot_b64        action_result / screenshot_b64
    │         is_starred            (AI 当步看到的截图)
    │
    device_id / provider / model
```

**TestStepLog 字段：**
- `step` — 步骤序号（从 1 开始）
- `thought` — AI 的推理文本（mark_done 前最后一次 msg_content）
- `action` — 调用的工具及参数（多工具用 ` | ` 分隔）
- `action_result` — 工具执行返回（多工具用 ` | ` 分隔）
- `screenshot_b64` — AI 做决策时看到的标注截图（半尺寸）
- `prompt_tokens` / `completion_tokens` / `total_tokens` — 该步 LLM token 用量
- `perception_ms` / `llm_ms` / `action_ms` — 该步各阶段耗时（毫秒）
- `subgoal_index` / `subgoal_desc` — SubAgent 运行时的子目标序号和描述

**TestResult** 新增 `total_tokens` 字段（整个 case 的 token 聚合）。

**TestCase** 新增 `parameters` 字段（`{{key}}` 模板参数，运行时展开）。

---

## 五、后续优化路线

> 完整路线图见 [`docs/roadmap.md`](roadmap.md)。以下为已完成功能清单。

| 层级 | 改动 | 状态 |
|------|------|------|
| 行动层 | `scroll` / `wait` / `list_packages` 工具 | ✅ |
| 感知层 | SoM 蓝点标注 + 坐标网格 | ✅ |
| 感知层 | 半尺寸截图 + ×2 坐标转换 | ✅ |
| 验证层 | 动态变化验证 (agent_reason) | ✅ |
| 回放层 | 步骤回放 (StepLog + UI 时间线 + HTML 报告) | ✅ |
| 记忆层 | starred 参考示例跨次引导（含思考过程） | ✅ |
| 记忆层 | `remember()` 工具（agent 主动存笔记） | ✅ |
| 记忆层 | 历史压缩（LLM 摘要替代硬截断） | ✅ |
| 行动层 | Step 级重试（tap_element 元素过期自动刷新） | ✅ |
| 决策层 | 主动恢复（stuck 4 级递进式自动恢复） | ✅ |
| 前端 | 运行对比页面 | ✅ |
| 工程化 | CLI 运行器 (`python cli.py run`) | ✅ |
| 可观测性 | Token 追踪（每步 + 聚合 + HTML 报告 + Runs 列表） | ✅ |
| 可观测性 | 耗时分析（perception_ms / llm_ms / action_ms） | ✅ |
| 可观测性 | Pass Rate 趋势图 | ✅ |
| 验证层 | 验证反馈结构化（gap 描述注入 Agent） | ✅ |
| 决策层 | Planner 分层（复杂任务先规划） | ✅ |
| 决策层 | Subagent 隔离（Hermes 式独立 context） | ✅ |
| 运行层 | Case 级失败重试（home 重置后重跑） | ✅ |
| 工程化 | Webhook 通知（飞书/钉钉/Slack） | ✅ |
| 感知层 | 纯视觉 UI 元素检测（VLM fallback） | ✅ |
| 工程化 | 参数化用例（`{{key}}` 模板展开） | ✅ |
| 决策层 | SYSTEM_PROMPT 增加 "Avoid common mistakes" 防误触规则 | ✅ |
| 记忆层 | 负面经验学习（LessonLearned 自动提取 + 注入） | ✅ |
| 记忆层 | 星标参考过滤（剔除误操作+恢复步骤对） | ✅ |

---

## 六、关键设计决策记录

**Q: 坐标系统为什么用半尺寸截图 + ×2？**

早期方案：截图缩放到 768px 宽，要求 AI 给出 0-1000 归一化坐标，服务器计算 `x/1000 × screen_width`。实际发现 AI 无论如何都倾向于给出看到的像素值，导致坐标普遍偏低（AI 给 463，实际应该给 850+）。
解决方案：把截图缩到 50%，告诉 AI "这张图的像素坐标就是你要给的值，服务器会自动 ×2"。AI 的本能行为变成了正确行为。

**Q: SoM 蓝点的作用是什么？**

Set-of-Marks（SoM）源自 Microsoft Research 的研究。在截图每个 a11y 元素中心画编号蓝圆点，让 AI 可以"看图找数字"再调用 `tap_element(N)`，而不是对着空白截图猜坐标。对 a11y 树丰富的界面（标准 Android 控件）准确率接近 100%；对 Canvas 绘制的界面（游戏、自绘 UI），退回到坐标网格模式。

**Q: 验证层为什么需要 agent_reason？**

对于数值变化类测试（经验值增加、余额变化），截图只能显示最终状态，单张图无法证明"发生了变化"。Agent 在执行过程中亲眼看到了前后状态，其 `mark_done` 的 reason 就是直接目击证词。Verifier 收到 `agent_reason` 后将其视为直接观察证据（而非猜测），与截图结合判断——"observation: XP went from 17 to 187 + screenshot shows 187/425" 是充分证明。

**Q: 为什么不直接用 droidrun 的 DroidAgent？**

droidrun 的创新在传输层（ADB）。我们的创新在于把传输层替换为 Portal 反向 WebSocket，让设备可以跨网络连接。原始计划是注入 WebSocketDevice 到 DroidAgent，但实现时因为需要 test case 结构（path/expected/pass/fail）、强制验证、步骤回放等，最终自己实现了 agent loop。

**Q: 为什么要加 Planner / Executor 分层？**

早期判断 test case 的 `path` + `expected` 已经够明确，不需要规划。实践发现对于 10+ 步的复杂任务（跨 App、多层嵌套导航），flat loop 容易迷失在中间步骤。竞品 AutoGLM 的 Planner/Grounder 分离在长任务上准确率提升显著（27.3% → 55.2%）。已实现两层：Phase 1 文本 plan 注入 + Phase 2 Subagent 隔离（Hermes 式）。触发条件：expected > 40字 或 path 含 ≥2 个 ">"。简单任务仍走原单层 loop。

**Q: 视频录制为何还未做？**

当前 Portal App 通过 WebSocket 与后端通信，后端没有直接的 ADB 连接。视频录制需要 Android 侧使用 `MediaProjection` API 录制并上传，涉及 APK 层改动。当前步骤截图回放已覆盖"AI 在想什么/看到什么/做了什么"的核心透明度需求；视频的额外价值是平滑过渡动画，作为后续增强项。

**Q: `remember()` 工具为什么不记录到 action_history？**

因为它是 Agent 的内部笔记，不应影响 stuck 检测和步骤计数。`remember(key, value)` 写入 `notes` 字典后以 `[Agent Notes]` 形式每步注入，但不出现在 `action_records` 中。

**Q: 为什么 recovery_level 渐衰而非硬重置？**

防止 Agent 通过做一次不同操作就逃出恢复流程。渐衰意味着即使 Agent 做了一步不同的动作，recovery_level 只会缓慢下降，而非立刻归零，避免反复进入同一死循环。

**Q: 纯视觉检测什么时候触发？**

仅当 a11y 树返回空列表时。正常 Android 控件都有 a11y 节点，只有 Canvas/游戏/WebView 才会空。额外一次 LLM 调用的成本可接受，因为这类场景本身就无法通过 a11y 操作。

**Q: 负面经验怎么防止无限膨胀？**

每个 case 最多存 5 条 lesson，加载时取最近 10 条。pattern-based 检测无 LLM 成本，LLM 分析是可选的。
