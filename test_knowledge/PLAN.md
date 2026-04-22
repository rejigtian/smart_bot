# Test Knowledge Base — 维护计划

> 可循环运行的 KB 构建流程。像业务知识库一样持续更新。

---

## 运行方式

```bash
cd smart-androidbot

# 首次全量构建
python test_knowledge/scripts/build_kb.py --all

# 单模块增量更新
python test_knowledge/scripts/build_kb.py --module voice-room
python test_knowledge/scripts/build_kb.py --module im
python test_knowledge/scripts/build_kb.py --module social
python test_knowledge/scripts/build_kb.py --module profile

# 单 feature 更新
python test_knowledge/scripts/build_kb.py --feature xiuxian

# 仅更新已知坑点（从 LessonLearned 汇总，无需源码）
python test_knowledge/scripts/build_kb.py --lessons-only
```

---

## 优先级和覆盖范围

### P0: 语音房（voice-room）
- [x] xiuxian 修仙（手写模板，作为格式参考）
- [ ] room-entry 房间入口流程（所有房型共用）
- [ ] auction-room 拍拍房
- [ ] cp-room CP 房
- [ ] ktv-room KTV 房
- [ ] family-room 家族房
- [ ] audio-match 语音匹配房
- [ ] fixroom 固定房
- [ ] newbie-room 新手房
- [ ] red-packet 红包玩法
- [ ] pk-system PK 玩法
- [ ] love-home 爱家
- [ ] wedding 婚礼

### P1: 聊天（im）
- [ ] private-chat 私聊
- [ ] group-chat 群聊
- [ ] voice-message 语音消息
- [ ] gift-message 礼物消息

### P2: 社交（social）
- [ ] friends 好友
- [ ] moments 玩友圈（朋友圈）
- [ ] intimacy 亲密关系
- [ ] relation 关系链

### P3: 个人（profile）
- [ ] profile-main 个人主页
- [ ] profile-edit 编辑资料
- [ ] settings 设置
- [ ] avatar-square 头像广场

---

## 数据来源（混合）

每次 `build_kb.py` 运行时依次扫描：

```
1. huiwan-lore-for-ai/knowledge/business/features/<module>/  (业务 md)
   → 提取业务描述、安全提示、UI 组件清单
   → 保留为"业务引用"区段

2. wespy-android/module/<module>/src/                        (源码)
   → 扫描 Fragment/Activity
   → 扫描 res/layout/*.xml 提取 resourceId
   → 扫描 res/values/strings.xml 提取中文 label
   → 生成"关键元素"表

3. smart-androidbot/backend/data/db.sqlite3                  (历史数据)
   → 汇总同 module 的 LessonLearned
   → 提取星标参考的成功路径
   → 更新"已知坑点"+ "典型测试步骤"

4. HUMAN_EDIT 区段                                            (人工)
   → 保留手写的测试建议，脚本不覆盖
```

---

## 单 feature md 的结构（固定）

每个 feature md 有 7 个 section，脚本幂等更新其中**自动区段**，保留**人工编辑区段**：

```markdown
# <Feature 名>

<!-- AUTO: meta -->
> 来源: [business/xxx.md](链接)
> 源码: module/xxx/src/...
> 最后更新: <date> | 状态: <auto|human-verified>
<!-- END AUTO -->

## 业务简介
<!-- AUTO: business-summary -->
从 business/xxx.md 提取的 1-2 段业务描述
<!-- END AUTO -->

## 入口路径
<!-- HUMAN: 人工编辑，脚本不覆盖 -->
首页 → 派对 tab → ...
<!-- END HUMAN -->

## 关键元素
<!-- AUTO: elements -->
| 元素 | ID | 文本 | 来源 |
|------|-----|------|------|
| 派对 tab | tab_party | "派对" | strings.xml |
<!-- END AUTO -->

## 典型测试步骤
<!-- HUMAN -->
1. start_app(com.wepie.wespy)
2. ...
<!-- END HUMAN -->

## 已知坑点
<!-- AUTO: lessons -->
从 LessonLearned 汇总
<!-- END AUTO -->

## 相关源码
<!-- AUTO: source-links -->
- Fragment: ...
<!-- END AUTO -->
```

---

## 扫描器如何处理"人工 vs 自动"

脚本读取现有 md 时：

- `<!-- AUTO: xxx -->` 和 `<!-- END AUTO -->` 之间的内容 → 丢弃、重新生成
- `<!-- HUMAN -->` 和 `<!-- END HUMAN -->` 之间的内容 → 保留原样

这样你可以：
1. 先让脚本生成自动部分
2. 手动补充 HUMAN 部分
3. 以后再跑脚本，HUMAN 部分不丢

---

## Agent 如何使用 KB

运行时流程：

```
用户提交任务: "打开修仙收取结晶"
       ↓
Planner 启动前: 
    search_feature("修仙 结晶") 
    → 匹配 xiuxian.md
    → 读取该 md 的 "入口路径 + 关键元素 + 已知坑点"
    → 作为上下文注入到 Planner
       ↓
Planner 生成的 plan 带有准确的元素 ID 和弹窗预警
       ↓
Agent 执行更快、更准
```

---

## 更新频率建议

- **业务 md 变更后**: 跑一次 `--module <name>` 同步业务引用
- **Android 大版本更新**: 跑 `--all` 全量重建（重点元素 ID/strings 会变）
- **每跑 10-20 次测试**: 跑 `--lessons-only` 汇总最新坑点

---

## 索引维护

`INDEX.md` 是所有 feature 的快速索引，脚本运行后自动更新。AI 查询 KB 时会先读这个索引做粗筛。
