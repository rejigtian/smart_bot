# Test Knowledge Base — 使用指南

> 配置驱动的测试知识库系统。Agent 在执行测试任务前会从 KB 检索相关 feature 上下文（入口路径、关键元素、已知坑点），让规划更准确、避免重蹈覆辙。

---

## 这是什么

Test KB 是一个 **per-feature** 的 markdown 集合，每个 md 文件描述被测 App 的一个功能（首页、登录、某个具体页面等），包含：

- 业务摘要：这个 feature 是干什么的
- 入口路径：从 App 启动到这个 feature 的导航步骤
- 关键元素：strings.xml 里的中文文本、layout 资源 id
- 典型测试步骤：人工编写的测试步骤参考
- 已知坑点：从历史 LessonLearned 自动汇总
- 相关源码：自动扫描得到的 Activity/Fragment/layout 列表

Agent 收到任务时会从 KB 找最相关的 feature md，把内容注入到 Planner，让规划阶段就拿到准确的元素 id 和已知陷阱。

---

## 快速开始（5 分钟）

```bash
cd smart-bot-open-source

# 1. 复制配置模板
cp test_knowledge/config.example.yml test_knowledge/config.yml

# 2. 编辑 config.yml，至少改这几个字段：
#    - project.app_package    被测 App 的包名
#    - source.root            Android 源码根目录
#    - modules                你的项目模块划分
nano test_knowledge/config.yml

# 3. 全量构建（自动扫描源码生成 feature md）
python test_knowledge/scripts/build_kb.py --all
```

构建完后会得到 `test_knowledge/features/<module>/<feature>.md` 一组文件，每个文件的 AUTO 区段已填好，HUMAN 区段留空给你后续补充。

---

## 配置文件（config.yml）

```yaml
project:
  name: my-app
  display_name: My App
  app_package: com.example.myapp        # Agent 用这个包名启动 App

source:
  root: ~/workspace/my-android-app      # 绝对路径或 ~/ 开头
  layout_dir: src/main/res/layout
  strings_file: src/main/res/values/strings.xml
  source_dir: src/main/java
  manifest: src/main/AndroidManifest.xml

# 可选：如果项目已有手写的业务知识库（md 形式），可以引入
# business_kb:
#   root: ~/workspace/my-android-app/docs/business
#   summary_heading: "业务概述"      # 摘要从这个 heading 下抽取

modules:
  home:
    display_name: 首页
    source_path: module/home            # 相对 source.root
    default_keywords: [home, main]      # 用于扫描源码相关文件

  settings:
    display_name: 设置
    source_path: module/settings
    default_keywords: [settings, config]

# 中英文别名 — 帮模糊匹配
runtime_aliases:
  首页: [home, main, 主页]
  登录: [login, signin]
```

完整字段说明见 `config.example.yml`。

---

## 命令参考

```bash
# 全量构建（首次必须）
python test_knowledge/scripts/build_kb.py --all

# 指定配置文件（多项目切换）
python test_knowledge/scripts/build_kb.py --all --config /path/to/another.yml

# 单模块增量更新
python test_knowledge/scripts/build_kb.py --module <module-name>

# 单 feature 更新
python test_knowledge/scripts/build_kb.py --feature <feature-slug>

# 仅刷新已知坑点（从最近的 LessonLearned 汇总，不重扫源码）
python test_knowledge/scripts/build_kb.py --lessons-only
```

---

## feature md 的结构

每个 feature md 由若干区段组成，**自动区段** 由脚本生成、可以重复覆盖；**人工区段** 由你写，脚本永远不动它。

```markdown
# <Feature 名>

<!-- AUTO: meta -->
> 业务来源: <如果有 business_kb 链接>
> 源码模块: module/xxx
> 最后更新: 2026-04-30 | 状态: auto + human-edited
<!-- END AUTO -->

## 业务简介
<!-- AUTO: business-summary -->
（从 business_kb 抽取的 1-2 段简介；没配 business_kb 时为空）
<!-- END AUTO -->

## 入口路径
<!-- HUMAN -->
（人工填：从 App 启动到这个 feature 的导航步骤）
首页 → 设置 → 账号 → 登录
<!-- END HUMAN -->

## 关键元素
<!-- AUTO: elements -->
| 元素 | ID / Name | 文本 | 来源 |
|------|-----------|------|------|
| 登录按钮 | btn_login | "登录" | strings.xml |
| ... | ... | ... | ... |
<!-- END AUTO -->

## 典型测试步骤
<!-- HUMAN -->
（人工填：写给 Agent 看的、推荐的测试路径）
<!-- END HUMAN -->

## 已知坑点
<!-- AUTO: lessons -->
（从 LessonLearned 表自动汇总的失败教训）
<!-- END AUTO -->

## 相关源码
<!-- AUTO: source-links -->
- Activity: ...
- Fragment: ...
- Layout: ...
<!-- END AUTO -->
```

---

## AUTO vs HUMAN 区段

构建脚本读取现有 md 时：

- `<!-- AUTO: xxx -->` 到 `<!-- END AUTO -->` 之间 → 丢弃、用最新扫描结果重新生成
- `<!-- HUMAN -->` 到 `<!-- END HUMAN -->` 之间 → 保留原样

工作流：

1. 跑一次 `--all` 让脚本生成所有 AUTO 区段
2. 手动补充 HUMAN 区段（入口路径 + 典型测试步骤）
3. 以后源码变了就再跑 `--all`，AUTO 区段刷新，HUMAN 区段不丢

---

## Agent 怎么用 KB

```
任务: "打开 App 设置页面，确认账号信息正确"
       ↓
Agent 启动前:
    search_feature(任务描述)
    → 模糊匹配到 settings/account.md
    → 读取该 md 的 「入口路径 + 关键元素 + 已知坑点」
    → 作为上下文注入到 Planner
       ↓
Planner 生成的 plan 自带准确的元素 id 和弹窗预警
       ↓
Agent 执行更快、更准
```

模糊匹配靠 `runtime_aliases` 字段：你写中文任务时，会查到对应的英文 feature slug。

---

## 维护节奏建议

| 时机 | 跑什么 |
|------|--------|
| 业务 md 变更 | `--module <name>` 同步业务摘要 |
| Android 大版本更新（资源/类名变化） | `--all` 全量重建 |
| 跑了一批新测试 | `--lessons-only` 把最新坑点汇总进 KB |

---

## 索引文件

`INDEX.md` 是所有 feature 的总索引（脚本运行后自动生成、自动更新）。Agent 查 KB 时先读它做粗筛，再读具体 feature md。**不要手动编辑 INDEX.md**。
