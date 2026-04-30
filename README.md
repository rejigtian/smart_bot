# Smart-Androidbot

> AI 驱动的 Android 自动化测试平台 — 用自然语言编写测试用例，或者导入 xmind / md 等文件生成用例。Agent 在真实设备上执行，结果可视化追踪。也可以当作自动化操作手机的工具来用，具备自学习与增强回放功能。

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [架构概览](#架构概览)
- [深入文档](#深入文档)
- [鸣谢](#鸣谢)
- [License](#license)

---

## 项目简介

Smart-Androidbot 是一个面向 Android 应用 QA 团队（或希望自动化操作 Android 的开发者）的 AI 平台。

工作流程：

1. 写测试用例 — 自然语言、Excel / YAML / xmind / md 都行
2. 任务分发到 `TestCaseAgent` 在服务器运行
3. Agent 通过 WebSocket 远程控制真实 Android 设备（不需要 ADB / 同网段）
4. 每一步：截图 → 分析 UI 树 → 决策 → 执行 → 验证
5. 全程步骤回放，可导出自包含 HTML 报告
6. 失败用例自动总结教训，下次同任务自动避免

不需要 XPath，不需要 Appium，不需要录制脚本。

---

## 核心特性

- **自然语言测试用例**：中文 / 英文、YAML / Excel / xmind / md 多格式导入
- **双重感知**：截图（视觉） + a11y 树（语义）双路融合决策
- **多 LLM 支持**：OpenAI / Anthropic / Gemini / 智谱 GLM / Groq / Ollama
- **远程设备 + 任意网络**：Portal App 反向 WebSocket 连接，设备在 4G/5G 也能管
- **完整测试管理 UI**：套件 / 用例 / 历史 Run / 步骤回放 / Run 对比 / Pass Rate 趋势
- **自包含 HTML 报告**：单文件含截图、思考、动作、验证结果，可离线分享
- **Planner + Subagent**：复杂任务自动分解，每个子目标独立 context
- **Page-aware 决策**：注入 Activity 类名 + 历史轨迹，识别"页面错了"
- **双证据校验**：Verifier 同看动作瞬间帧（捕获 toast）+ 沉淀帧
- **从错误中学习**：自动提取 LessonLearned，下次同任务避免重蹈覆辙
- **智能恢复**：检测 stuck 后 4 级递进式自动恢复
- **可观测**：Token 消耗、感知/LLM/动作三阶段耗时、Pass Rate 趋势
- **CI/CD**：CLI 集成、Webhook 通知（飞书/钉钉/Slack）

完整对比和路线图：[竞品对比](docs/comparison.md) · [Roadmap](docs/roadmap.md)

---

## 快速开始

### 前置条件

- Python 3.9+
- Node.js 18+
- Android 设备（真机或模拟器）

### 启动后端 + 前端

```bash
git clone https://github.com/rejigtian/smart_bot.git
cd smart_bot

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

或者一键启动：

```bash
./start.sh
```

访问 http://localhost:5173，在设置页填入 LLM API Key。

### 构建并安装 Portal App

```bash
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

首次启动 App：
1. 设置页填 **服务器 WS 地址**（例如 `ws://192.168.1.10:8000/v1/providers/join`）和 **设备 Token**（在 Web UI 设备页生成）
2. 系统设置 → 无障碍服务 → 启用 **AgentAccessibilityService**
3. 回到 App 点"启动连接"，前台服务通知出现即上线

### 写一个测试用例

在 Web UI 的 **测试套件** 创建套件，添加用例：

```
路径（操作目标）: 打开设置，找到"关于手机"，截图当前版本号
期望结果: 页面显示系统版本信息，未出现错误弹窗
```

选设备 + 模型，点 Run。

### CLI（CI/CD 集成）

```bash
cd backend
python cli.py run --suite <id> --device <id> --json
```

退出码：0 全过；1 有失败。

---

## 架构概览

```
Browser (管理 UI)
  │ REST API + SSE
FastAPI Server
  ├── Planner (复杂任务分解)
  │     └── SubAgent #1..N (独立 context 执行)
  ├── TestCaseAgent (6 层架构 + VLM fallback)
  │     感知 → 决策 → 行动 → 记忆 → 验证 → 回放
  └── SQLite + Webhook + CLI
        Device / Suite / Case / Run / Result / StepLog
  │
  │ WebSocket JSON-RPC
Android 设备 (Portal App)
  tap / swipe / input / screenshot / get_ui_state
```

详细设计见 [`docs/agent-architecture.md`](docs/agent-architecture.md)。

---

## 深入文档

| 文档 | 说明 |
|------|------|
| [Agent 架构](docs/agent-architecture.md) | 6 层 Agent + Planner / Subagent 详细设计 |
| [Android Portal](docs/android-optimization.md) | Portal App 性能与连接稳定性 |
| [Test KB 使用](test_knowledge/PLAN.md) | 测试知识库构建、AUTO/HUMAN 区段、检索机制 |
| [Roadmap](docs/roadmap.md) | 已完成功能清单 + 待办优先级 |
| [竞品对比](docs/comparison.md) | DroidRun / Midscene / AutoGLM 技术路线对比 |
| [故障排查](docs/troubleshooting.md) | 常见问题（连接、截图、识别）排查清单 |

---

## 鸣谢

本项目从这些优秀的开源项目中获得了启发：

- **[droidrun / droidrun-portal](https://github.com/droidrun/droidrun-portal)** — Portal App 的反向 WebSocket、连接稳定性策略（库级 ping/pong、重连预算、终态错误识别）直接借鉴自 droidrun-portal 的实现
- **[Midscene.js](https://github.com/web-infra-dev/midscene)** — Set-of-Marks 视觉标注思路启发了我们对 a11y 元素的可视化方案（最终选择品红十字而非数字气泡，避免和游戏内容混淆）
- **[AutoGLM](https://github.com/THUDM/AutoGLM)** — Planner / Grounder 分层思想影响了我们的双感知融合架构

---

## 贡献

欢迎 PR 和 Issue。常见贡献方向：

- 新增 LLM provider 适配（在 `agent/base.py` 添加分支）
- Portal App 新动作（`agent/tools.py` 工具定义 + `ws_device.py` 实现）
- 测试用例格式解析器（`core/test_parser.py`）
- 文档完善 / 国际化

---

## License

MIT
