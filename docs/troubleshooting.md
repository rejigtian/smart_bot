# 故障排查

| 现象 | 排查 |
|------|------|
| 设备一直显示"离线" | 1) 后端必须监听 `0.0.0.0`（不是 `127.0.0.1`），否则设备到不了；2) 在设备所在网络 ping 后端 IP 排除路由问题；3) 检查设备 Portal App 设置页里的 WS 地址和 Token 是否正确 |
| 连接成功但又频繁断开 | 看 logcat `ReverseConn` / `AgentWS` tag。`ECONNREFUSED` = 后端没起；`Unauthorized/401` = Token 错误；`Connection reset` = 网络中间件断流，检查公司 WiFi/VPN 是否限制长连接 |
| Portal App 进程无故崩溃 | 1) 升级到最新版本（旧版本存在 `onError` 自递归 StackOverflow bug，已修复）；2) 在 `adb logcat -t 200 *:E` 中检索 `AndroidRuntime` |
| 步骤报告里截图缺失 / 永远是 before 状态 | 已修复：现在每个有 tool_call 的步骤都会写 StepLog，verifier 返回的合并帧（A 瞬间 + B 沉淀）会作为该步证据 |
| Agent 把 SoM 标记误识为游戏内品 | 标记已切换为品红十字 + 显式 SYSTEM_PROMPT 提示。如果模型仍混淆，调整为更小/更不显眼的形状，或用 `request_screenshot` 强制下一步重新拍图 |
| Unity / Canvas 页面截图超时 | `ws_device.py` 已把超时从 15s 提到 25s；服务端 `portal_ws.py` 在有 pending RPC 时跳过 ping，避免误断流 |

---

## Portal App 连接稳定性策略

Portal App 内置 droidrun-portal 风格的连接稳定策略：

- **库级 ping/pong**（30s 超时）：自动检出僵尸连接
- **重连预算从首次失败计起**：连接成功后归零，避免被无限累计
- **终态错误识别**（401/403/400）：直接停止重试
- **AtomicBoolean 防止 onError/onClose 同时触发重复重连**：避免重连风暴

---

## 后端服务端策略

`backend/ws/portal_ws.py` 的 WebSocket 端点：

- **60s receive timeout**：允许慢 RPC（25s 截图）正常完成
- **跳过空闲 ping 当有 pending RPC**：设备正在做活就是活的，不需要再 ping
- **2 次 ping 失败才断开**：避免一次抖动就掉线
