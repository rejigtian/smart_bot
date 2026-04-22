# Smart-Androidbot Roadmap

> 统一维护所有待做事项。按优先级排列，完成后打勾。

最后更新: 2026-04-17

---

## 已完成

| 功能 | 层级 | 完成时间 |
|------|------|----------|
| `scroll` / `wait` / `list_packages` 工具 | 行动层 | 2026-04 |
| SoM 蓝点标注 + 坐标网格 | 感知层 | 2026-04 |
| 半尺寸截图 + x2 坐标转换 | 感知层 | 2026-04 |
| 动态变化验证 (agent_reason) | 验证层 | 2026-04 |
| 步骤回放 (StepLog + UI 时间线 + HTML 报告) | 回放层 | 2026-04 |
| 星标参考案例注入 | 记忆层 | 2026-04 |
| `remember()` 工具 — Agent 做笔记 | 记忆层 | 2026-04 |
| Few-shot 增强 — 参考案例带思考过程 | 决策层 | 2026-04 |
| 历史压缩 — LLM 摘要替代硬截断 | 记忆层 | 2026-04 |
| Step 级重试 — tap_element 元素过期自动刷新 | 行动层 | 2026-04 |
| 主动恢复 — stuck 4 级递进式自动恢复 | 决策层 | 2026-04 |
| 运行对比页面 | 前端 | 2026-04 |
| CLI 运行器 (`python cli.py run`) | 工程化 | 2026-04 |
| Token 用量追踪 (O1) — 每步 prompt/completion/total + 聚合 | 可��测性 | 2026-04 |
| 耗时分析 (O2) — perception_ms / llm_ms / action_ms 每步计时 | 可观测性 | 2026-04 |
| ��证反馈结构化 (A2) — Verifier 返回 gap 描述，注入 Agent 反馈 | 验证层 | 2026-04 |
| Planner 分层 (A1) — 复杂任务先规划再执行，plan 作为 pinned 消息 | 决策层 | 2026-04 |
| Subagent 隔离 (A1-P2) — Hermes 式 SubGoal 分解 + 独立 context 执行 | 决策层 | 2026-04 |
| Case 级失败重试 (A4) — fail/error 自动重试，home 重置后重跑 | 运行层 | 2026-04 |
| Webhook 通知 (E1) — 飞书/钉钉/Slack/Custom，run 完成自动推送 | 工程化 | 2026-04 |
| Pass Rate 趋势图 (O3) — 后端 trends API + 前端 SVG 折线图 | 可观测性 | 2026-04 |
| 纯视觉 UI 元素检测 (A3) — a11y 空时 VLM 检测可交互元素 | 感知层 | 2026-04 |
| 参数化用例 (E2) — TestCase.parameters + `{{key}}` 模板展开 | 工程化 | 2026-04 |
| SYSTEM_PROMPT 防误触规则 | 决策层 | 2026-04 |
| 负面经验学习 — LessonLearned 自动提取 + 注入 | 记忆层 | 2026-04 |
| 星标参考过滤 — 剔除误操作+恢复步骤对 | 记忆层 | 2026-04 |

---

## P0: 可观测性（知道 Agent 在做什么、花了多少）

### O1. Token 用量追踪
> 每个 case 花了多少 token、多少钱，完全不透明。跑 100 个 case 可能就花了 $50。

**改动：**
- `StepLog` 增加 `prompt_tokens` / `completion_tokens` / `total_tokens` 字段
- `test_agent.py` — 从 `response.usage` 中提取 token 数，写入 StepLog
- `CaseResult` 增加 `total_tokens` / `total_cost` 聚合
- `TestStepLog` DB 模型加对应列
- `TestResult` 增加 `total_tokens` 列
- RunDetail 前端 — 每步显示 token 数，Run 总计显示总 token 和估算费用
- HTML 报告 — 摘要栏增加总 token 数

**估计工作量：** 中等（后端 + 前端 + DB 迁移）

- [x] 后端: StepLog / CaseResult / DB 增加 token 字段
- [x] 后端: test_agent.py 从 LLM response 提取 usage
- [x] 后端: report.py 增加 token 摘要（summary card + 每行 token 列）
- [x] 前端: RunDetail 展示 token
- [x] 前端: Runs 列表增加 token 列
- [x] 后端: RunOut 增加 total_tokens 聚合

### O2. 耗时分析
> 一个 case 跑了 3 分钟，不知道时间花在哪 — LLM 调用？截图？step_delay？

**改动：**
- `StepLog` 增加 `llm_time_ms` / `perception_time_ms` / `action_time_ms` 字段
- `test_agent.py` — 在感知/LLM/动作阶段前后计时
- RunDetail 步骤回放 — 每步显示耗时分布条

- [x] 后端: StepLog / DB 增加耗时字段
- [x] 后端: test_agent.py 各阶段计时
- [x] 前端: 步骤回放增加耗时展示

### O3. Pass Rate 趋势图
> 跑了 10 次同一个 Suite，pass rate 从 60% 提升到 85%，但没有图表看趋势。

**改动：**
- 后端 API: `GET /api/suites/{id}/trends` — 返回最近 N 次 Run 的 pass/fail/total
- 前端: SuiteDetail 页面增加折线图（用 lightweight chart 库如 recharts）

- [ ] 后端: trends API
- [ ] 前端: 趋势折线图

### O4. 视频录屏回放
> 截图序列不如视频直观，尤其是页面切换动画。

**改动：**
- Portal App: 使用 `MediaProjection` API 录屏
- WebSocket 协议: 增加 `start_recording` / `stop_recording` 消息
- `ws_device.py`: 增加录屏控制方法
- `test_runner.py`: 在 agent.run() 前后调用 start/stop
- 后端: 视频存储 + API endpoint
- 前端/报告: 视频播放器 + AI 操作时间线 overlay

**前置依赖：** Portal App (Android APK) 需要先改

- [ ] Android: MediaProjection 录屏实现
- [ ] 协议: start_recording / stop_recording
- [ ] 后端: ws_device 录屏控制
- [ ] 后端: 视频存储 + API
- [ ] 前端: 视频播放器

---

## P1: Agent 准确率

### A5. Subagent 隔离上下文（Planner 升级版）
> A1 简版已落地（plan 作为 pinned 消息），但 plan 仍和执行步骤共用同一个 context，长任务仍会因 token 膨胀丢失全局方向。Hermes 的 subagent 模式把每个子目标放在独立会话里执行，主 Agent 只看到压缩后的总结——这是 A1 的下一步演化。

**核心设计：**

```
Parent Agent (TestCaseAgent)
    │
    ├── Planner (一次调用，便宜模型)
    │     入: case.path + case.expected + 当前 UI 概览
    │     出: [SubGoal(desc, success_criteria, expected_steps), ...]
    │
    ├── Executor SubAgent #1    ← 独立 AgentMemory，从空 messages 起步
    │     目标: SubGoal #1
    │     回传: SubResult(status, final_ui_summary, key_actions[≤5])
    │
    ├── Executor SubAgent #2    ← 新的独立 context
    │     只看到: 上一个 subgoal 的 final_ui_summary + 当前 subgoal
    │
    └── Parent 汇总 → mark_done → Verifier
```

**关键：每个 SubAgent 用独立 `AgentMemory`。** 跑完只回传压缩后的 `SubResult`，parent 的 context 始终是 O(n_subgoals) 而非 O(n_steps)。

**触发条件：** `expected > 40 字` 或 `path 含 ≥3 个 '>'` 或 planner 输出 ≥3 个 subgoal；否则走原单层 loop（向后兼容）。

**改动：**

- 新增 `agent/planner.py`
  - `class Planner: async def plan(goal, expected, initial_ui) -> list[SubGoal]`
  - 独立 LLM 调用，支持便宜模型（配置项 `planner_model`）
  - 结构化输出: `[{desc, success_criteria, expected_steps}, ...]`
- 新增 `agent/subagent.py`
  - `class ExecutorSubAgent` — 复用 TestCaseAgent 的单步循环，构造函数改为接收 `SubGoal` 而非整个 case
  - 独立 `AgentMemory` 实例，沿用 SYSTEM_PROMPT
  - 完成时生成 `SubResult(status, final_ui_summary, key_actions)`，summary 复用 `memory.py` 的压缩逻辑
- 重构 `core/test_agent.py:TestCaseAgent.run()`
  - 分流: 满足触发条件 → 分层调度（planner → 循环 subagents → 汇总）；否则 → 原 loop
  - 把原 loop 的核心（感知/决策/行动/记忆更新）抽成可复用方法 `_run_step()`，供 ExecutorSubAgent 共用
- `agent/memory.py`
  - 公开"压缩 messages 为 summary"的方法（目前是私有逻辑），供 SubResult 复用
- StepLog / DB 扩展
  - `TestStepLog` 增加 `subgoal_id` / `subgoal_desc` 列（可为 null）
  - 回放层按 subgoal 分组
- 前端: `RunDetail` 时间线按 subgoal 分段折叠
- 失败处理
  - SubAgent fail → 可选重试同 subgoal 一次（接入 A4 的重试机制）
  - 连续 2 个 subgoal fail → case fail，避免无限消耗

**Subagent 隔离的收益：**
1. **Context 复杂度** 从 O(steps) 降为 O(subgoals + steps_in_current_subgoal)
2. **压缩问题缓解**: subgoal boundary 就是天然压缩点，不再依赖"N 步一次摘要"
3. **可复用性**: SubGoal / SubResult 是天然的 Skill 候选（承接阶段 1 Skills 机制）
4. **可并行性**: 未来无依赖 subgoal 可并行（承接 X2 多设备并行）

**风险 / 验证：**
- Planner 质量决定上限，差的 plan 比没有 plan 更糟 → 先用 5 个真实长 case 评估 Planner 单独产物
- Subgoal 间 UI 状态传递丢失 → `final_ui_summary` 作为下一 subgoal 的起点上下文
- A/B 验证: 选 5 个当前失败率高的长 case，分层 vs 单层，pass rate 提升 ≥20% 视为成功
- 总 token 未必增加（context 更短抵消多轮调用），但需实测；如果 token 膨胀 >30% 则调整触发阈值

**任务拆解：**
- [x] `agent/memory.py` — 暴露 compress-to-summary 公开方法（先行，无依赖）
- [ ] `core/test_agent.py` — 抽取 `_run_step()` 可复用方法（纯重构，行为不变）
- [x] `agent/planner.py` — Planner（Phase 1: 轻量 plan 注入已完成，Phase 2: SubGoal 结构化已完成）
- [x] `agent/subagent.py` — `ExecutorSubAgent` + `SubResult` dataclass
- [x] `core/test_agent.py` — 分流调度 + SubResult 汇总 + 触发条件
- [x] DB: `TestStepLog.subgoal_index` / `subgoal_desc`
- [x] 前端: RunDetail subgoal 分段展示
- [ ] 评估脚本: 5 个长 case 的 A/B 对比报告
- [ ] 配置: `planner_model` 设置项（Settings 页 + 后端 config）

**依赖：** 成功后直接承接阶段 1 Skills 机制——star 的 subgoal → 命名 skill。

### A2. 验证反馈结构化
> Verifier 拒绝 mark_done("pass") 后，Agent 只被告知"继续"，但不知道**哪里不对**。

**改动：**
- `verifier.py` — verify() 返回值增加 `gap_description`（"页面显示首页，不是设置页"）
- `test_agent.py` — 把 gap_description 注入到 tool result 中：
  `"Verification failed: 页面仍在首页。Expected: 设置页显示版本号。Gap: 需要导航到设置 > 关于手机"`

- [x] verifier.py — 返回结构化的差距描述
- [x] test_agent.py — 注入差距到反馈

### A3. 纯视觉 UI 元素检测
> Canvas 绘制的 UI（游戏、自绘控件）a11y 树为空，Agent 只能靠坐标网格盲猜。

**改动：**
- 当 `_ui_elements` 为空时，用 VLM 做 UI 元素检测（"列出截图中所有可交互元素的位置和描述"）
- 生成虚拟元素列表，用于 SoM 标注
- 可选: 用轻量模型（Qwen-VL / UI-TARS）本地推理，避免额外 API 费用

- [x] 感知层: Canvas UI 元素检测 fallback
- [x] SoM 标注: 支持虚拟元素列表

### A4. 失败重试策略（case 级）
> 当前一个 case fail 后直接标记 fail 进入下个 case。某些场景下重试一次就能过（如网络抖动、页面加载慢）。

**改动：**
- `test_runner.py` — case fail/error 时，可选重试（`max_retries=1`）
- 重试前先 `global_action("home")` 重置状态
- StartRunRequest / CLI 增加 `--retries` 参数

- [x] test_runner.py — case 重试逻辑
- [x] API / CLI — retries 参数

---

## P2: 测试工程化

### E1. Webhook 通知
> QA 团队需要跑完自动推送结果到飞书/钉钉/Slack。

**改动：**
- Settings 增加 `webhook_url` + `webhook_type` (feishu/dingtalk/slack/custom)
- `test_runner.py` — run 完成后调用 webhook，推送摘要（suite 名、pass/fail 数、链接）
- 前端 Settings 页增加 Webhook 配置区

- [ ] 后端: webhook 推送
- [ ] 前端: Settings webhook 配置

### E2. 参数化用例
> 同一个流程（"搜索商品"）需要用不同数据跑多次。当前只能复制多个 case。

**改动：**
- TestCase 增加 `parameters` JSON 字段（如 `[{"keyword": "iPhone"}, {"keyword": "耳机"}]`）
- `test_runner.py` — 一个 case 按参数展开为多次执行
- case.path 中 `{{keyword}}` 被替换为实际值
- 前端 SuiteDetail — 用例编辑支持参数表格

- [ ] DB: TestCase.parameters 字段
- [ ] 后端: 参数展开逻辑
- [ ] 前端: 参数编辑 UI

### E3. CI/CD 深度集成
> CLI 已有，但还缺 GitHub Action 和自动化触发。

- [ ] GitHub Action yaml 模板
- [ ] 支持 `--threshold 80` 参数（pass rate < 80% 时 exit 1）
- [ ] JUnit XML 输出格式（兼容 Jenkins / GitHub / GitLab）

### E4. 用例自动生成（探索模式）
> 给定 APK → Agent 自动探索 App → 记录路径 → 生成用例草稿。

**改动：**
- 新增 `ExplorerAgent` — 无目标自由探索，最大化页面覆盖
- 每到一个新页面记录路径和元素
- 探索结束后生成 TestCase 草稿（path = 导航路径，expected = "页面正常显示"）
- 用户在 Web UI 审核、修改、确认

- [ ] agent/explorer.py — 探索器
- [ ] 后端: 探索任务 API
- [ ] 前端: 探索结果审核 UI

---

## P3: 平台扩展

### X1. iOS 支持
> Portal App iOS 版，复用 WebSocket JSON-RPC 协议。

- [ ] iOS Portal App (Swift + XCTest)
- [ ] ws_device.py 适配 iOS 差异

### X2. 多设备并行
> 100 个 case 串行太慢，需要分配到多台设备并行。

- [ ] test_runner.py — execute_parallel_run()
- [ ] API — StartRunRequest 支持 device_ids 列表
- [ ] 前端 — 设备多选

### X3. Web UI 测试
> 用 Puppeteer/Playwright 控制浏览器，复用 Agent 决策层。

- [ ] agent/web_device.py — 浏览器 DeviceDriver 实现
- [ ] 感知层适配 DOM screenshot

---

## 当前 Sprint: 平台扩展 + 工程化收尾

> 上一轮 (O1 + O2 + A1 + A2 + A3 + A4 + A5 核心) 已完成。

**下一轮目标：E3 + O4 + X2**

```
E3 (CI/CD 深度集成)  ← GitHub Action + JUnit XML
O4 (视频录屏)        ← 需要改 Android APK
X2 (多设备并行)      ← 扩大规模
```

---

## 竞品对标

| 能力 | 我们 | DroidRun | Midscene | AutoGLM |
|------|------|----------|----------|---------|
| 测试管理 UI | **有** | 无 | 无 | 无 |
| 跨网络设备 | **有** | 无 | 无 | 无 |
| 步骤回放 | **有** | 外部 | 有 | 无 |
| 记忆/笔记 | **有** | 无 | 无 | 无 |
| 历史压缩 | **有** | 无 | 无 | 有 |
| 自动恢复 | **有** | 无 | 无 | 有 |
| Token 追踪 | **有** | 无 | 无 | 无 |
| Planner 分层 | **有** | 有 | 无 | **有** |
| 纯视觉检测 | **有** | 有 | **有** | 有 |
| iOS | X1 待做 | **有** | **有** | 无 |
| 并行执行 | X2 待做 | **有** | 无 | 无 |
| 用例自动生成 | E4 待做 | 无 | 无 | 无 |
| 负面经验学习 | **有** | 无 | 无 | 无 |
