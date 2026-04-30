# Smart-Androidbot

> AI 驱动的 Android 自动化测试平台 — 用自然语言编写测试用例，Agent 在真实设备上执行，结果可视化追踪。

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [架构概览](#架构概览)
- [竞品对比](#竞品对比)
- [优势与差距](#优势与差距)
- [路线图](#路线图)
- [故障排查](#故障排查)

---

## 项目简介

Smart-Androidbot 是一个面向 Android 应用 QA 团队的 AI 测试平台。你只需用自然语言描述要执行的操作和期望结果，系统会：

1. 将任务分发给运行在服务器上的 `TestCaseAgent`
2. Agent 通过 WebSocket 远程控制真实 Android 设备
3. 每一步都截图 → 分析 UI 树 → 决策 → 执行 → 验证
4. 全程步骤可回放，结果可导出为自包含 HTML 报告

不需要写 XPath，不需要 Appium，不需要录制脚本。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **自然语言测试用例** | 用中文或英文描述操作目标，支持 YAML / Excel / 手动录入 |
| **双重感知：截图 + a11y 树** | 截图提供视觉上下文，无障碍树提供元素语义，两路信息融合决策 |
| **多 LLM 支持** | OpenAI / Anthropic / Google Gemini / 智谱 GLM / Groq / Ollama 本地部署 |
| **步骤回放时间线** | 每步记录：AI 看到的截图 → 思考内容 → 执行动作 → 操作结果 |
| **测试套件管理** | Web 界面管理测试集、查看历史 Run、对比多次运行结果 |
| **SSE 实时日志** | 测试执行时日志实时推送到浏览器，无需刷新 |
| **自包含 HTML 报告** | 一个 HTML 文件包含所有截图、步骤回放和日志，可离线分享 |
| **参考案例标记** | 将高质量执行结果标为"参考案例"，用于后续 few-shot 注入 |
| **远程设备，任意网络** | Portal App 主动连接服务器，30s 心跳 + 失败重连，设备不需要与服务器同网段 |
| **Agent 笔记 (remember)** | Agent 可自主记录关键信息（包名、登录状态），跨步骤不丢失 |
| **Planner + Subagent** | 复杂任务自动分解为子目标，每个子目标独立 context 执行 |
| **Token 追踪** | 每步记录 prompt/completion token 数，Run 总计展示消耗 |
| **耗时分析** | 每步记录感知/LLM/动作三阶段耗时 |
| **智能恢复** | 检测到 stuck 后 4 级递进式自动恢复 |
| **纯视觉检测** | a11y 树为空时 VLM 自动检测可交互元素 |
| **Page-aware 决策** | [Device State] 注入当前 Activity 类名 + 历史轨迹，Agent 能识别"页面错了"而不是盲点 |
| **双证据校验** | Verifier 同时使用动作瞬间帧（捕获 toast）+ 沉淀帧（稳定状态），合并图作为报告证据 |
| **Agent 主动请图** | 文本步骤遇到不确定时可调 `request_screenshot` 触发下一步注入截图（每 case 限 3 次） |
| **Case 失败重试** | 失败后自动 home 重置并重跑，可配置重试次数 |
| **Webhook 通知** | Run 完成后自动推送结果到飞书/钉钉/Slack |
| **运行对比** | 选择两次 Run 对比每个 case 的状态变化 |
| **Pass Rate 趋势图** | SuiteDetail 页面展示历史 pass rate 折线图 |
| **参数化用例** | 支持 `{{keyword}}` 模板语法，一条用例多组数据 |
| **负面经验学习** | 自动从历史 Run 中提取误操作教训，下次同任务自动避免 |

---

## 快速开始

### 前置条件

- Python 3.9+
- Node.js 18+
- Android 设备已安装 Portal App（APK 在 `android/` 目录）

### 启动服务

```bash
# 克隆项目
git clone https://github.com/yourname/smart-androidbot.git
cd smart-androidbot

# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 前端（另一个终端）
cd frontend
npm install
npm run dev
```

或者使用一键启动脚本（同时启动后端和前端）：

```bash
./start.sh
```

访问 http://localhost:5173，在设置页填入 LLM API Key，然后让设备 Portal App 连接服务器即可。

### 构建并安装 Portal App

```bash
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

首次启动 App 后：
1. 在设置页填入 **服务器 WS 地址**（例如 `ws://192.168.1.10:8000/v1/providers/join`）和 **设备 Token**（在 Web UI 的设备页生成）。
2. 系统设置 → 无障碍服务 → 启用 **AgentAccessibilityService**。
3. 回到 App，点击"启动连接"，前台服务通知出现即代表已上线。

Portal App 内置 droidrun-portal 风格的连接稳定策略：
- **库级 ping/pong**（30s 超时）自动检出僵尸连接
- **重连预算从首次失败计起**，连接成功后归零，避免被无限累计
- **终态错误识别**（401/403/400）直接停止重试
- **AtomicBoolean 防止 onError/onClose 同时触发重复重连

### CLI 用法（CI/CD 集成）

```bash
cd backend
python cli.py run --suite <id> --device <id> --json
```

### 写一个测试用例

在 **测试套件** 页面创建套件，添加用例：

```
路径（操作目标）: 打开设置，找到"关于手机"，截图当前版本号
期望结果: 页面显示系统版本信息，未出现错误弹窗
```

选择设备和模型，点击运行。

---

## 架构概览

```
Browser (管理界面)
  │ REST API + SSE
FastAPI Server
  │
  ├── Planner (复杂任务分解)
  │     └── SubAgent #1..N (独立 context 执行)
  ├── TestCaseAgent (6层架构 + VLM fallback)
  │     感知 → 决策 → 行动 → 记忆 → 验证 → 回放
  │
  └── SQLite + Webhook + CLI
        Device / Suite / Case / Run / Result / StepLog
  │
  │ WebSocket JSON-RPC
Android 设备 (Portal App)
  tap / swipe / input / screenshot / get_ui_state
```

详细架构见 [`docs/agent-architecture.md`](docs/agent-architecture.md)。

---

## 竞品对比

> 同类工具：DroidRun、Midscene.js、AutoGLM。下面客观评估各自的技术路线和适用场景。

### 工具简介

**DroidRun**（德国，MIT 开源，约 8.2k stars）
Python 框架，支持多 LLM，通过 ADB + Portal App 控制设备。主打 Android/iOS 自动化工作流，同时提供云端并行执行服务（Mobilerun）。

**Midscene.js**（字节跳动，MIT 开源，约 12.6k stars）
TypeScript 框架，纯视觉方案（Set of Marks），无需 DOM/a11y 树，支持 Web + Android + iOS + HarmonyOS，通过 ADB 控制 Android。有可视化步骤回放报告。

**AutoGLM**（智谱 AI / 清华，商业产品）
基于 GLM 模型，通过 Android AccessibilityService 获取 UI 树，分离 planner/grounder 提升点击精度。专注中文生态，已应用于 z.ai 商业产品。

---

### 详细对比矩阵

| 维度 | Smart-Androidbot | DroidRun | Midscene.js | AutoGLM |
|------|:----------------:|:--------:|:-----------:|:-------:|
| **主要定位** | Android 测试平台 | 自动化工作流 | 跨平台 UI 自动化 | 手机/Web 自主 Agent |
| **UI 感知方式** | 截图 + a11y 树 | 截图（VLM） | 纯截图（Set of Marks） | 截图 + AccessibilityService |
| **Android 控制** | WebSocket（Portal App） | ADB + Portal App | ADB | AccessibilityService |
| **设备连接** | 反向 WS，任意网络 | ADB，需同网段 | ADB，需同网段 | AccessibilityService，本机 |
| **测试用例格式** | YAML / Excel / 手动 | Python 脚本 | YAML + JS/TS SDK | 自然语言目标 |
| **多 LLM 支持** | 6 个 provider | 5 个 provider | 4 个 VLM | 仅 GLM 系列 |
| **测试套件管理 UI** | **有（完整 Web UI）** | 无 | 无 | 无 |
| **步骤回放** | **有（内嵌 + HTML 报告）** | Arize Phoenix 外部集成 | 有（本地 HTML 文件） | 无 |
| **实时日志流** | **有（SSE）** | 无 | 无 | 无 |
| **历史 Run 对比** | **有** | 无 | 无 | 无 |
| **HTML 报告导出** | **有（自包含）** | 无 | 有（本地文件） | 无 |
| **坐标精度优化** | 半尺寸截图 + 网格标注 | 无特殊处理 | Set of Marks | Planner/Grounder 分离 |
| **非开发者友好** | 中（Web UI 降低门槛） | 低（纯代码） | 中（YAML） | 高（对话式） |
| **开源** | 是 | MIT | MIT | 部分（模型权重）|
| **可自托管** | 完整自托管 | 是 | 是 | 否（商业 SaaS）|

---

### 技术路线差异详解

#### 感知层：我们 vs. 竞品

```
Midscene.js:   截图 → Set of Marks 标注 → 纯 VLM 决策
               优点：适用任何 UI 表面（Canvas / 游戏 / 非标准控件）
               缺点：视觉模糊时无语义兜底；坐标偏移风险高

AutoGLM:       截图 + AccessibilityService 树 → Planner 输出语义 → Grounder 转坐标
               优点：精度高，中文 App 生态优化到位
               缺点：AccessibilityService 不适合自动化测试场景（需手动开启）

Smart-Androidbot: 截图 × 0.5 缩放 + 网格标注 + 品红十字 SoM → a11y 树文本 + Activity → LLM 决策
               优点：两路信息互补；坐标系用乘法而非归一化，直觉准确；网格消除估算误差；
                    十字标记不会被误认为游戏内品（晶体/光球等）；Activity 名让 Agent 识别"页面错了"
               缺点：非标准 UI（Canvas 游戏）a11y 树为空，仍需依赖纯视觉
```

#### 控制层：WebSocket vs. ADB

DroidRun 和 Midscene 都依赖 ADB，要求设备与 PC 在同一网络（或 USB 直连）。Smart-Androidbot 的 Portal App 主动建立 WebSocket 连接，设备可以在任意位置（4G/5G/公司 WiFi），服务器部署在云端即可管理全球设备 — 这在多端 QA 场景（如设备农场）中是显著优势。

#### 测试管理层：这是真正的空白

竞品对比显示一个共同缺口：**三者都没有测试用例管理 + 结果 Dashboard**。DroidRun 和 Midscene 专注于执行框架，测试组织完全依赖用户自己的 Python/YAML + CI 脚本。AutoGLM 是对话式 Agent，不是测试框架。

Smart-Androidbot 在这一层有完整实现：套件创建 / 用例增删改 / 历史 Run 列表 / 单 Run 详情 / 步骤回放 / 星标参考案例 / HTML 报告导出。这是当前最直接的差异化。

---

## 优势与差距

### 我们的优势

1. **完整的测试管理闭环** — 从用例编写到结果分析，一个 Web 界面全覆盖，竞品没有这一层。
2. **设备网络无关** — WebSocket 反向连接，设备可在任意网络，适合云端 QA 农场。
3. **双感知融合 + VLM Fallback** — a11y 树提供语义兜底，截图提供视觉确认；当 a11y 树为空时 VLM 自动检测可交互元素，Canvas/游戏场景也能覆盖。
4. **Planner + Subagent 分层** — 复杂多步任务自动分解为子目标，每个子目标独立 context 执行，长任务成功率显著提升。
5. **坐标精度设计** — 半尺寸 + 网格标注解决了 AI 坐标估算不准的根本问题，比 Set of Marks 更精确（有具体坐标可读）。
6. **步骤回放内嵌** — 回放直接集成在 Web UI 和 HTML 报告中，不需要外部工具。
7. **LLM 无关** — 支持 6 个 provider 且架构解耦，换模型不改代码。
8. **智能恢复 + 失败重试** — 检测到卡住后 4 级递进式自动恢复；Case 级别失败重试，自动 home 重置后重跑。
9. **可观测性** — Token 消耗追踪、感知/LLM/动作三阶段耗时分析、Pass Rate 趋势图，运行成本一目了然。
10. **Webhook + CLI** — Run 完成后自动推送飞书/钉钉/Slack；CLI 支持 CI/CD 管道直接调用。
11. **从错误中学习** — 自动提取历史误操作教训（LessonLearned），下次执行同任务时注入 Agent 避免重蹈覆辙。竞品均无此能力。

### 我们的差距

1. **纯视觉成熟度** — VLM fallback 已实现基本的纯视觉元素检测，但在 Canvas/游戏等复杂场景下的鲁棒性仍不如 Midscene 的 Set of Marks 分层方案成熟。
2. **跨平台支持** — 目前仅支持 Android。Midscene 支持 Web + iOS + HarmonyOS + 桌面端。
3. **测试用例规模** — 没有 DroidRun 内置的 40+ 主流 App 工作流模板库。
4. **并行执行** — 当前单设备串行。DroidRun 云端支持多设备并行，我们尚未实现。
5. **社区与生态** — 独立项目，暂无外部用户生态。Midscene 背靠字节跳动，DroidRun 有活跃 Discord + 投资背书。

---

## 路线图

基于上述竞品分析，优先级排序如下：

### P0：提升准确率（直接影响核心价值）

- [ ] **Few-shot 注入**：将星标参考案例注入决策层 prompt，减少 LLM 随机性
- [x] **纯视觉 VLM Fallback**：a11y 树为空时 VLM 自动检测可交互元素，覆盖 Canvas/游戏场景
- [x] **Case 失败重试**：失败后自动 home 重置并重跑，可配置重试次数
- [x] **智能恢复**：检测到 stuck 后 4 级递进式自动恢复

### P1：扩展规模（扩大适用场景）

- [ ] **并行多设备执行**：一个 Run 分配到多台设备并行，缩短大套件时间
- [x] **Planner/Subagent 分层**：复杂任务自动分解为子目标，每个子目标独立 context 执行
- [x] **参数化用例**：支持 `{{keyword}}` 模板语法，一条用例多组数据
- [ ] **iOS 支持**：Portal App 对应 iOS 版本（XCTest / WebDriverAgent）

### P2：体验提升（降低使用门槛）

- [ ] **视频回放**：Portal App 录屏 + 与 AI 操作时间线对齐，比截图序列更直观
- [x] **CLI 集成**：提供 CLI 工具 (`cli.py`)，支持 CI/CD 管道自动触发 Run
- [x] **Webhook 通知**：Run 完成后自动推送结果到飞书/钉钉/Slack
- [x] **运行对比**：选择两次 Run 对比每个 case 的状态变化
- [x] **Pass Rate 趋势图**：SuiteDetail 页面展示历史 pass rate 折线图
- [ ] **App 工作流模板**：内置常用 App（微信、支付宝、抖音）的标准测试用例集

### P3：智能化（提升 Agent 能力）

- [ ] **历史压缩记忆**：超过 N 步时压缩旧步骤摘要，避免 context 溢出
- [x] **Agent 笔记 (`remember`)**：Agent 可自主记录关键信息（包名、登录状态），跨步骤不丢失
- [x] **Token 追踪 + 耗时分析**：每步记录 prompt/completion token 数及感知/LLM/动作三阶段耗时
- [x] **负面经验学习（LessonLearned）**：自动提取误操作教训 + SYSTEM_PROMPT 防误触规则 + 星标参考过滤
- [x] **Page-aware 决策**：[Device State] 注入当前 Activity 类名 + 历史轨迹，区分长得像的不同页面
- [x] **双证据校验**：Verifier 同时使用动作瞬间帧（toast 可见）+ 沉淀帧（稳定状态），合并图作为报告证据
- [x] **Agent 主动请图 (`request_screenshot`)**：文本步骤遇到不确定时可主动触发下一步注入截图（限 3 次/case）
- [ ] **自动用例生成**：给定 APK 自动探索并生成测试用例（类 Monkey 但 AI 驱动）

---

## 技术栈

**后端**
- Python 3.9 / FastAPI / SQLAlchemy (async) / SQLite
- WebSocket JSON-RPC（设备通信）
- SSE（实时日志推送）

**前端**
- React 18 / TypeScript / TanStack Query / Tailwind CSS / Vite

**Android 端**
- Kotlin / WebSocket 客户端
- AccessibilityService（UI 树）+ UIAutomator（操作执行）

**AI**
- 多 provider：OpenAI / Anthropic / Gemini / 智谱 GLM / Groq / Ollama
- Function Calling / Tool Use（标准工具调用接口）
- 双感知融合：截图（base64）+ a11y 树文本

---

## 故障排查

| 现象 | 排查 |
|------|------|
| 设备一直显示"离线" | 1) 后端必须监听 `0.0.0.0`（不是 `127.0.0.1`），否则设备到不了；2) 在设备所在网络 ping 后端 IP 排除路由问题；3) 检查设备 Portal App 设置页里的 WS 地址和 Token 是否正确。 |
| 连接成功但又频繁断开 | 看 logcat `ReverseConn` / `AgentWS` tag。`ECONNREFUSED` = 后端没起；`Unauthorized/401` = Token 错误；`Connection reset` = 网络中间件断流，检查公司 WiFi/VPN 是否限制长连接。 |
| Portal App 进程无故崩溃 | 1) 升级到最新版本（旧版本存在 `onError` 自递归 StackOverflow bug，已修复）；2) 在 `adb logcat -t 200 *:E` 中检索 `AndroidRuntime`。 |
| 步骤报告里截图缺失 / 永远是 before 状态 | 已修复：现在每个有 tool_call 的步骤都会写 StepLog，verifier 返回的合并帧（A 瞬间 + B 沉淀）会作为该步证据。 |
| Agent 把蓝点/紫色十字误识为游戏内品 | 已切换为品红十字 + 显式 SYSTEM_PROMPT 提示。如果模型仍混淆，调整为更小/更不显眼的形状，或用 `request_screenshot` 强制下一步重新拍图。 |
| Unity / Canvas 页面截图超时 | `ws_device.py` 已把超时从 15s 提到 25s；服务端 `portal_ws.py` 在有 pending RPC 时跳过 ping，避免误断流。 |

---

## 贡献

欢迎 PR 和 Issue。主要贡献方向：

- 新 LLM provider 适配（在 `agent/base.py` 添加 provider 分支）
- Portal App 新动作（在 `agent/tools.py` 定义工具 + `ws_device.py` 实现）
- 测试用例格式解析器（在 `core/test_parser.py` 添加格式）

---

## License

MIT
