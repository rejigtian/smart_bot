# Android APK 优化计划

> 维护设备端（`smart-androidbot/android`）的功能完善与稳定性增强任务。按优先级执行。

最后更新: 2026-04-21

---

## 已完成（本轮）

| 任务 | 改动 | 完成 |
|------|------|------|
| WebSocket 重连循环 bug | `connectBlocking()` → `connect()` + `CompletableDeferred.awaitClose()` | 2026-04-20 |
| UUID id 解析 | `json.optInt("id",-1)` → `json.opt("id"): Any?` | 2026-04-20 |
| 截图静默 hang | `withTimeoutOrNull(12s)` + JPEG 75 | 2026-04-20 |
| 亮屏保持 | `PARTIAL_WAKE_LOCK` → `SCREEN_BRIGHT_WAKE_LOCK \| ACQUIRE_CAUSES_WAKEUP` | 2026-04-20 |
| 列出所有 app | 加 `QUERY_ALL_PACKAGES` 权限 | 2026-04-21 |
| `app/stop` 生效 | 加 `KILL_BACKGROUND_PROCESSES` 权限 | 2026-04-21 |
| 状态栏遮挡 | `ScrollView fitsSystemWindows="true"` | 2026-04-21 |
| `install` RPC（外部用） | URL 下载 + FileProvider + 系统安装器 | 2026-04-21 |

---

## 待做计划（P0 → P3）

### ── P0：稳定性 ────────────────────────────

#### Task A1 · 电池优化白名单 + 引导 UI

- **目标**：Doze 模式下服务不被杀，长任务 30min+ 稳定运行
- **改动**：
  - `AndroidManifest.xml` 加 `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` 权限
  - `MainActivity.kt` 加一张 "Battery Optimization" 状态卡片，检测 `PowerManager.isIgnoringBatteryOptimizations(packageName)`，未加白名单就引导到系统设置
  - 新增 drawable 红点时用浅色提示（非强制项）
- **验证**：
  - 手动：在手机 "省电策略" 里确认 app 已加白名单
  - 长任务：连接后放置 30 分钟不操作，检查 WS 仍在线、KeepAlive 仍在轮询
- **依赖**：无

#### Task A2 · long_press 手势

- **目标**：补齐基础手势能力
- **改动**：
  - `GestureController.kt` 加 `suspend fun longPress(x: Int, y: Int, durationMs: Long = 800)`，用 `GestureDescription` 单点持续
  - `ApiHandler.kt` 加 `performLongPressAbs(x, y, durationMs)`
  - `ActionDispatcher.kt` 加 `"long_press"` case，参数 `{x, y, duration?}`
  - **后端** `ws_device.py` 加 `long_press(x, y, duration_ms)` 方法，`supported` 集合加 `"long_press"`
- **验证**：
  - Web UI 新任务跑一次"长按桌面图标出菜单"的 case
  - logcat 看 gesture dispatch 成功
- **依赖**：无

---

### ── P1：决策质量与速度 ────────────────────

#### Task B1 · device_info RPC

- **目标**：agent 能拿到屏幕尺寸/密度/Android 版本/机型，决策更泛化
- **改动**：
  - `ApiHandler.kt` 加 `getDeviceInfo()` 返回 `{model, manufacturer, androidVersion, sdkInt, screenWidth, screenHeight, densityDpi, locale}`
  - `ActionDispatcher.kt` 加 `"device_info"` case
  - **后端** `ws_device.py` 加 `async def get_device_info() -> dict`
  - agent `perception.py` 把 device_info 注入到 system prompt 首次即可（不需要每步）
- **验证**：
  - 单独调用 RPC 返回结构正确
  - 首次 plan 时 prompt 里能看到 "Screen: 1080x2400, Android 14" 之类信息
- **依赖**：无

#### Task B2 · IME commitText 批量输入

- **目标**：`input_text` 从逐字符发送改为 commitText 一次性提交，速度 5-10x
- **改动**：
  - `AgentKeyboardIME.kt` 的 `inputText` 改用 `currentInputConnection.commitText(text, 1)` 直接整段提交，不要 for-each key event
  - 保留对 `clear=true` 的处理（`deleteSurroundingText` 清空当前字段）
  - 失败 fallback 到原来的 ACTION_SET_TEXT 路径
- **验证**：
  - 长文本（100+ 字符）输入耗时 <500ms（之前 3-5s）
  - 中文、emoji 能正确输入
  - 日志对比前后耗时
- **依赖**：无

---

### ── P2：体验增强 ──────────────────────────

#### Task C1 · 剪贴板 RPC

- **目标**：`clipboard/get`、`clipboard/set` 方法，agent 可用剪贴板粘贴长内容、读取 app 复制的内容
- **改动**：
  - `ApiHandler.kt` 加 `getClipboard()`, `setClipboard(text)` 通过 `ClipboardManager`
  - `ActionDispatcher.kt` 加 `"clipboard/get"`, `"clipboard/set"` case
  - **注意**：Android 10+ 读剪贴板需要 app 处于前台或是 IME。我们的 IME 在 `AgentKeyboardIME` 中，剪贴板读取应该优先走 IME 路径
  - **后端** `ws_device.py` 加对应方法
- **验证**：
  - 在 app 里复制一段文字，通过 RPC 拉回来对比一致
  - 设置剪贴板后手动粘贴验证
- **依赖**：无

#### Task C2 · 截图格式参数化

- **目标**：支持 `{format: "png"|"jpeg", quality: 1-100}` 参数，debug 时用 PNG 无损
- **改动**：
  - `AgentAccessibilityService.kt` 的 `takeScreenshotBase64` 加 `format`, `quality` 参数
  - `ApiHandler.screenshot()` 从 params 透传
  - `ActionDispatcher` 的 `"screenshot"` case 解析新参数
  - **后端**保持默认 JPEG 75，debug 模式下手动传 PNG
- **验证**：
  - 默认 JPEG 大小 ~200KB
  - `{format: "png"}` 返回无损，文件大 10x+
- **依赖**：无

---

### ── P3：长期增强 ──────────────────────────

#### Task D1 · 屏幕录制

- **目标**：任务执行期间录一段视频，作为测试报告附件
- **改动**：
  - 新增 `service/ScreenRecorderService.kt`，使用 `MediaProjection` API
  - 需要 `FOREGROUND_SERVICE_MEDIA_PROJECTION` 权限 + Android 14+ 的 `foregroundServiceType="mediaProjection"`
  - 首次启动录制需要用户弹窗授权（系统 Intent）
  - RPC: `recording/start {outputPath?}`, `recording/stop` 返回文件路径
  - 文件回传：后端提供上传端点，Android 结束时上传
- **验证**：
  - 跑完一个任务得到一个 mp4
  - Web UI 任务详情页能播放
- **依赖**：后端新增上传端点、前端播放器组件

#### Task D2 · 多屏 / 折叠屏支持

- **目标**：折叠屏外屏/扩展屏能正确截图和点击
- **改动**：
  - `takeScreenshot(displayId, ...)` 从硬编码 0 改为按 `context.display.displayId`
  - `getScreenBounds()` 改从当前 window 的 display 取
  - `GestureController` 的 gesture 自动派发到对应 display（API 30+ 支持）
- **验证**：需要折叠屏真机测试
- **依赖**：无（有条件才做）

#### Task D3 · 设备日志回传

- **目标**：后端能通过 `GET /api/devices/{id}/logs` 拉近期 logcat，方便排查线上问题
- **改动**：
  - `ApiHandler.kt` 加 `getRecentLogs(lines=200)` 调用 `logcat -d -t N` 读取（需要 `READ_LOGS` 权限，不过普通 app 只能读自己进程）
  - **或者**更实用：在 app 里自己收集关键事件（连接/断开/RPC 错误）到环形 buffer，暴露 RPC
  - **后端**路由转发
- **验证**：前端设备详情页能看到最近 200 行
- **依赖**：后端路由 + 前端页面

---

## 回归测试清单

每次发新 APK 前跑一遍：

- [ ] 重启手机 → 服务自动启动（BootReceiver）
- [ ] 连接 WS 稳定 5 分钟，无 reconnect 日志
- [ ] `list_packages` 返回 >10 个包
- [ ] `screenshot` 3 秒内返回，图可解
- [ ] `state` 返回 a11y_tree、phone_state、device_context 三段
- [ ] 任务流程：start_app → tap_element → input_text → screenshot，全链路通
- [ ] 手机息屏后 tap 操作能唤醒（WakeLock 生效）
- [ ] KeepAlive 关闭后再开启能重启 `ReverseConnectionService`
- [ ] 覆盖安装后无障碍/IME 状态保留（MIUI 行为）

---

## 建议迭代节奏

- P0 两个任务一起做一个 commit：**app-stability-p0**
- P1 两个任务各一个 commit：**app-device-info-p1** + **app-ime-batch-p1**
- P2 按需，单独做
- P3 屏幕录制是大工程，单独开分支
