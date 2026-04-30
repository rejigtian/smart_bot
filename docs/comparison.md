# 竞品对比

> 同类工具：DroidRun、Midscene.js、AutoGLM。下面客观评估各自的技术路线和适用场景。

---

## 工具简介

**DroidRun**（德国，MIT 开源，约 8.2k stars）
Python 框架，支持多 LLM，通过 ADB + Portal App 控制设备。主打 Android/iOS 自动化工作流，同时提供云端并行执行服务（Mobilerun）。

**Midscene.js**（字节跳动，MIT 开源，约 12.6k stars）
TypeScript 框架，纯视觉方案（Set of Marks），无需 DOM/a11y 树，支持 Web + Android + iOS + HarmonyOS，通过 ADB 控制 Android。有可视化步骤回放报告。

**AutoGLM**（智谱 AI / 清华，商业产品）
基于 GLM 模型，通过 Android AccessibilityService 获取 UI 树，分离 planner/grounder 提升点击精度。专注中文生态，已应用于 z.ai 商业产品。

---

## 详细对比矩阵

| 维度 | Smart-Androidbot | DroidRun | Midscene.js | AutoGLM |
|------|:----------------:|:--------:|:-----------:|:-------:|
| **主要定位** | Android 测试平台 | 自动化工作流 | 跨平台 UI 自动化 | 手机/Web 自主 Agent |
| **UI 感知方式** | 截图 + a11y 树 | 截图（VLM） | 纯截图（Set of Marks） | 截图 + AccessibilityService |
| **Android 控制** | WebSocket（Portal App） | ADB + Portal App | ADB | AccessibilityService |
| **设备连接** | 反向 WS，任意网络 | ADB，需同网段 | ADB，需同网段 | AccessibilityService，本机 |
| **测试用例格式** | YAML / Excel / xmind / md | Python 脚本 | YAML + JS/TS SDK | 自然语言目标 |
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

## 技术路线差异详解

### 感知层

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

### 控制层：WebSocket vs. ADB

DroidRun 和 Midscene 都依赖 ADB，要求设备与 PC 在同一网络（或 USB 直连）。Smart-Androidbot 的 Portal App 主动建立 WebSocket 连接，设备可以在任意位置（4G/5G/公司 WiFi），服务器部署在云端即可管理全球设备 — 这在多端 QA 场景（如设备农场）中是显著优势。

### 测试管理层：这是真正的空白

竞品对比显示一个共同缺口：**三者都没有测试用例管理 + 结果 Dashboard**。DroidRun 和 Midscene 专注于执行框架，测试组织完全依赖用户自己的 Python/YAML + CI 脚本。AutoGLM 是对话式 Agent，不是测试框架。

Smart-Androidbot 在这一层有完整实现：套件创建 / 用例增删改 / 历史 Run 列表 / 单 Run 详情 / 步骤回放 / 星标参考案例 / HTML 报告导出。这是当前最直接的差异化。

---

## 优势与差距

### 我们的优势

1. **完整的测试管理闭环** — 从用例编写到结果分析，一个 Web 界面全覆盖，竞品没有这一层
2. **设备网络无关** — WebSocket 反向连接，设备可在任意网络，适合云端 QA 农场
3. **双感知融合 + VLM Fallback** — a11y 树提供语义兜底，截图提供视觉确认；当 a11y 树为空时 VLM 自动检测可交互元素，Canvas/游戏场景也能覆盖
4. **Planner + Subagent 分层** — 复杂多步任务自动分解为子目标，每个子目标独立 context 执行
5. **坐标精度设计** — 半尺寸 + 网格标注解决 AI 坐标估算不准的根本问题
6. **步骤回放内嵌** — 回放直接集成在 Web UI 和 HTML 报告中，不需要外部工具
7. **LLM 无关** — 支持 6 个 provider 且架构解耦，换模型不改代码
8. **智能恢复 + 失败重试** — 检测到卡住后 4 级递进式自动恢复
9. **可观测性** — Token 消耗追踪、三阶段耗时分析、Pass Rate 趋势图
10. **Webhook + CLI** — 飞书/钉钉/Slack 通知；CI/CD 管道直接调用
11. **从错误中学习** — 自动提取历史误操作教训（LessonLearned），下次执行同任务时注入 Agent 避免重蹈覆辙

### 我们的差距

1. **纯视觉成熟度** — VLM fallback 已实现，但在 Canvas/游戏等复杂场景下的鲁棒性仍不如 Midscene 的 Set of Marks 分层方案
2. **跨平台支持** — 目前仅支持 Android。Midscene 支持 Web + iOS + HarmonyOS + 桌面端
3. **测试用例规模** — 没有 DroidRun 内置的 40+ 主流 App 工作流模板库
4. **并行执行** — 当前单设备串行；DroidRun 云端支持多设备并行
5. **社区与生态** — 独立项目，暂无外部用户生态
