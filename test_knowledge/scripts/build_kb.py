"""
Test KB 构建器 — 从源码 + 业务 KB + 历史 LessonLearned 生成/更新测试知识库。

Usage:
    python test_knowledge/scripts/build_kb.py --all                    # 全量
    python test_knowledge/scripts/build_kb.py --module voice-room      # 单模块
    python test_knowledge/scripts/build_kb.py --feature xiuxian        # 单 feature
    python test_knowledge/scripts/build_kb.py --lessons-only           # 只更新坑点

幂等：保留 <!-- HUMAN --> ... <!-- END HUMAN --> 区段中的人工编辑。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
KB_ROOT = SCRIPT_DIR.parent                                    # test_knowledge/
PROJECT_ROOT = KB_ROOT.parent                                  # smart-androidbot/
WORKSPACE_ROOT = PROJECT_ROOT.parent                           # ~/myproject/

# External paths (configurable via env vars)
WESPY_ROOT = Path(os.environ.get(
    "WESPY_ROOT",
    Path.home() / "AndroidxProjects/repo-wespy/wespy-android"
))
LORE_ROOT = WESPY_ROOT / "huiwan-lore-for-ai"
DB_PATH = PROJECT_ROOT / "backend/data/db.sqlite3"

# Module → feature directory mapping
MODULE_MAPPING = {
    "voice-room": {
        "source": WESPY_ROOT / "module/voiceroom",
        "lore": LORE_ROOT / "knowledge/business/features/voice-room",
        "kb_dir": KB_ROOT / "features/voice-room",
    },
    "im": {
        "source": WESPY_ROOT / "module/basechat",
        "lore": LORE_ROOT / "knowledge/business/features/im",
        "kb_dir": KB_ROOT / "features/im",
    },
    "social": {
        "source": WESPY_ROOT / "module",
        "lore": LORE_ROOT / "knowledge/business/features/social",
        "kb_dir": KB_ROOT / "features/social",
    },
    "profile": {
        "source": WESPY_ROOT / "module/app",
        "lore": LORE_ROOT / "knowledge/business/features/profile",
        "kb_dir": KB_ROOT / "features/profile",
    },
}


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class FeatureData:
    slug: str                   # e.g. "xiuxian"
    module: str                 # e.g. "voice-room"
    business_md: Optional[Path] # path to the business KB md
    business_summary: str = ""
    elements: list = None       # list of {name, id, text, source, notes}
    source_files: list = None   # list of paths
    sub_systems: list = None    # list of {name, desc, priority}
    lessons: list = None        # list of lesson strings

    def __post_init__(self):
        if self.elements is None: self.elements = []
        if self.source_files is None: self.source_files = []
        if self.sub_systems is None: self.sub_systems = []
        if self.lessons is None: self.lessons = []


# ── Section markers (for idempotent updates) ──────────────────────────────

AUTO_START = "<!-- AUTO: {name} -->"
AUTO_END = "<!-- END AUTO -->"
HUMAN_START = "<!-- HUMAN -->"
HUMAN_END = "<!-- END HUMAN -->"


def _extract_human_sections(content: str) -> dict[int, str]:
    """Extract all HUMAN sections keyed by their position index in the file."""
    pattern = re.compile(
        re.escape(HUMAN_START) + r"(.*?)" + re.escape(HUMAN_END),
        re.DOTALL
    )
    sections = {}
    for i, match in enumerate(pattern.finditer(content)):
        sections[i] = match.group(1)
    return sections


def _replace_auto_section(content: str, name: str, new_body: str) -> str:
    """Replace the body between <!-- AUTO: name --> ... <!-- END AUTO --> markers."""
    start = AUTO_START.format(name=name)
    pattern = re.compile(
        re.escape(start) + r"(.*?)" + re.escape(AUTO_END),
        re.DOTALL
    )
    if not pattern.search(content):
        # Section doesn't exist yet — do nothing (template should already have it)
        return content
    return pattern.sub(f"{start}\n{new_body.strip()}\n{AUTO_END}", content)


# ── Source scanners ───────────────────────────────────────────────────────

def scan_strings_xml(module_path: Path, keywords: list[str]) -> list[tuple[str, str]]:
    """Extract strings from res/values/strings.xml matching any keyword.

    Returns list of (name, value) tuples.
    """
    strings_file = module_path / "src/main/res/values/strings.xml"
    if not strings_file.exists():
        return []
    try:
        content = strings_file.read_text(encoding="utf-8")
    except Exception:
        return []
    results = []
    pattern = re.compile(r'<string name="([^"]+)">([^<]+)</string>')
    for m in pattern.finditer(content):
        name, value = m.group(1), m.group(2)
        if any(kw in name.lower() or kw in value for kw in keywords):
            results.append((name, value))
    return results[:30]  # cap to avoid bloat


def scan_layouts(module_path: Path, keywords: list[str]) -> list[tuple[str, Path]]:
    """Find layout XML files whose name contains any keyword."""
    layout_dir = module_path / "src/main/res/layout"
    if not layout_dir.exists():
        return []
    results = []
    for xml in layout_dir.glob("*.xml"):
        name_lower = xml.stem.lower()
        if any(kw in name_lower for kw in keywords):
            results.append((xml.stem, xml))
    return results[:20]


def scan_source_files(module_path: Path, keywords: list[str]) -> list[Path]:
    """Find Kotlin/Java source files whose path contains any keyword."""
    src_dir = module_path / "src/main/java"
    if not src_dir.exists():
        return []
    results = []
    for path in src_dir.rglob("*.kt"):
        path_str = str(path).lower()
        if any(kw in path_str for kw in keywords):
            results.append(path)
    for path in src_dir.rglob("*.java"):
        path_str = str(path).lower()
        if any(kw in path_str for kw in keywords):
            results.append(path)
    return results[:30]


# ── Business KB parser ────────────────────────────────────────────────────

def parse_business_summary(md_path: Path) -> str:
    """Extract the first paragraph under '## 业务概述' from a business KB md."""
    if not md_path or not md_path.exists():
        return ""
    try:
        content = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r"##\s*业务概述\s*\n+([\s\S]*?)(?=\n##|\n---|\Z)", content)
    if m:
        summary = m.group(1).strip()
        # Strip trailing separators and trim to first 2 paragraphs
        summary = re.sub(r"\n---\s*$", "", summary).strip()
        paragraphs = [p.strip() for p in summary.split("\n\n") if p.strip()][:2]
        return "\n\n".join(paragraphs)
    return ""


# ── LessonLearned loader ──────────────────────────────────────────────────

def load_lessons_for_slug(slug: str) -> list[str]:
    """Load lessons from DB where task_keyword contains slug keywords."""
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        # Match on task_keyword or screen_context
        cur.execute(
            "SELECT DISTINCT lesson FROM lessons_learned "
            "WHERE task_keyword LIKE ? OR screen_context LIKE ? "
            "ORDER BY created_at DESC LIMIT 8",
            (f"%{slug}%", f"%{slug}%")
        )
        return [row[0] for row in cur.fetchall() if row[0]]
    except Exception:
        return []
    finally:
        try: conn.close()
        except: pass


# ── Template renderer ─────────────────────────────────────────────────────

TEMPLATE = """# {name} — 测试知识

<!-- AUTO: meta -->
> 业务来源: [{business_link_name}]({business_link})
> 源码模块: `{source_module}`
> 最后更新: {date} | 状态: {status}
<!-- END AUTO -->

---

## 业务简介

<!-- AUTO: business-summary -->
{business_summary}
<!-- END AUTO -->

---

## 入口路径

<!-- HUMAN -->
（需要手动补充：描述如何从 App 首页导航到此 feature。例如）

首页 → XX tab → YY → ZZ

1. ...
2. ...
<!-- END HUMAN -->

---

## 关键元素

<!-- AUTO: elements -->
{elements_table}
<!-- END AUTO -->

---

## 典型测试步骤

<!-- HUMAN -->
（需要手动补充测试步骤模板）

```
Test case: ...
Expected: ...

步骤:
1. start_app({{"package": "com.wepie.wespy"}})
2. ...
```
<!-- END HUMAN -->

---

## 已知坑点

<!-- AUTO: lessons -->
{lessons_section}
<!-- END AUTO -->

---

## 相关源码

<!-- AUTO: source-links -->
{source_links}
<!-- END AUTO -->
"""


def render_elements_table(elements: list) -> str:
    if not elements:
        return "（暂无，需要手动补充或等待源码扫描结果）"
    lines = ["| 元素 | ID / Name | 文本 | 来源 |", "|------|-----------|------|------|"]
    for el in elements[:20]:
        name = el.get("name", "-")
        id_val = el.get("id", "-")
        text = el.get("text", "-")
        source = el.get("source", "-")
        lines.append(f"| {name} | `{id_val}` | {text} | {source} |")
    return "\n".join(lines)


def render_lessons_section(lessons: list[str]) -> str:
    if not lessons:
        return "（无历史教训 — 运行测试后脚本自动汇总）"
    lines = []
    for i, lesson in enumerate(lessons, 1):
        lines.append(f"{i}. {lesson}")
    return "\n".join(lines)


def render_source_links(feature: FeatureData) -> str:
    if not feature.source_files:
        return "（未扫描到相关源码）"
    lines = []
    rel_to_wespy = lambda p: str(p.relative_to(WESPY_ROOT))
    by_type = {"Activity": [], "Fragment": [], "Dialog": [], "Other": []}
    for f in feature.source_files[:20]:
        name = f.stem
        if "Activity" in name: by_type["Activity"].append(f)
        elif "Fragment" in name: by_type["Fragment"].append(f)
        elif "Dialog" in name: by_type["Dialog"].append(f)
        else: by_type["Other"].append(f)
    for category, files in by_type.items():
        if files:
            lines.append(f"\n**{category}**:")
            for f in files[:8]:
                lines.append(f"- `{rel_to_wespy(f)}`")
    return "\n".join(lines) if lines else "（未扫描到相关源码）"


# ── Main build logic ──────────────────────────────────────────────────────

def build_feature(feature: FeatureData, kb_dir: Path, force: bool = False) -> bool:
    """Build or update a single feature md. Returns True if file was written."""
    kb_dir.mkdir(parents=True, exist_ok=True)
    md_path = kb_dir / f"{feature.slug}.md"

    # Build rendered sections
    today = datetime.now().strftime("%Y-%m-%d")
    business_link = ""
    business_link_name = feature.business_md.name if feature.business_md else "(none)"
    if feature.business_md:
        try:
            # Compute relative path from kb md to business md
            business_link = os.path.relpath(feature.business_md, kb_dir)
        except Exception:
            business_link = str(feature.business_md)

    source_module_rel = f"module/{feature.module.replace('-', '')}"

    rendered = TEMPLATE.format(
        name=feature.slug.replace("-", " ").title(),
        business_link_name=business_link_name,
        business_link=business_link or "N/A",
        source_module=source_module_rel,
        date=today,
        status="auto-generated" if not md_path.exists() else "auto + human-edited",
        business_summary=feature.business_summary or "（待补充）",
        elements_table=render_elements_table(feature.elements),
        lessons_section=render_lessons_section(feature.lessons),
        source_links=render_source_links(feature),
    )

    if md_path.exists() and not force:
        # Merge: keep HUMAN sections from existing file
        existing = md_path.read_text(encoding="utf-8")
        # Update only AUTO sections
        updated = existing
        for section_name, new_body in [
            ("meta", rendered_section(rendered, "meta")),
            ("business-summary", rendered_section(rendered, "business-summary")),
            ("elements", rendered_section(rendered, "elements")),
            ("lessons", rendered_section(rendered, "lessons")),
            ("source-links", rendered_section(rendered, "source-links")),
            ("sub-systems", rendered_section(rendered, "sub-systems")),
        ]:
            if new_body:
                updated = _replace_auto_section(updated, section_name, new_body)
        md_path.write_text(updated, encoding="utf-8")
    else:
        md_path.write_text(rendered, encoding="utf-8")

    return True


def rendered_section(rendered: str, name: str) -> str:
    """Extract a specific AUTO section body from the rendered template."""
    start = AUTO_START.format(name=name)
    pattern = re.compile(re.escape(start) + r"\n(.*?)\n" + re.escape(AUTO_END), re.DOTALL)
    m = pattern.search(rendered)
    return m.group(1) if m else ""


# ── Feature discovery ─────────────────────────────────────────────────────

def discover_features(module: str) -> list[FeatureData]:
    """Discover all features in a module by listing business KB md files."""
    cfg = MODULE_MAPPING.get(module)
    if not cfg:
        print(f"Unknown module: {module}", file=sys.stderr)
        return []

    lore_dir = cfg["lore"]
    source_dir = cfg["source"]

    features = []
    if lore_dir.exists():
        for md in sorted(lore_dir.glob("*.md")):
            if md.stem in ("README",):
                continue
            slug = md.stem
            f = FeatureData(
                slug=slug,
                module=module,
                business_md=md,
            )
            f.business_summary = parse_business_summary(md)

            # Scan source code
            keywords = [slug.replace("-", "_"), slug.replace("-", "")]
            # Special keywords
            if slug == "xiuxian": keywords += ["xiuxian", "sect", "fly"]
            elif slug == "ktv-room": keywords += ["ktv"]
            elif slug == "cp-room": keywords += ["cp"]
            elif slug == "auction-room": keywords += ["auction", "paipai"]
            elif slug == "family-room-mic": keywords += ["family"]
            elif slug == "audio-match": keywords += ["audiomatch", "match"]

            f.source_files = scan_source_files(source_dir, keywords)

            # Scan strings
            string_matches = scan_strings_xml(source_dir, keywords)
            # Scan layouts
            layout_matches = scan_layouts(source_dir, keywords)

            # Build element entries
            for name, value in string_matches[:10]:
                f.elements.append({
                    "name": value[:20],
                    "id": f"@string/{name}",
                    "text": value[:40],
                    "source": "strings.xml",
                })
            for name, path in layout_matches[:5]:
                f.elements.append({
                    "name": f"Layout: {name}",
                    "id": name,
                    "text": "-",
                    "source": "res/layout",
                })

            # Load lessons
            f.lessons = load_lessons_for_slug(slug)

            features.append(f)

    return features


# ── INDEX.md updater ──────────────────────────────────────────────────────

def update_index():
    """Regenerate the INDEX.md with all discovered features."""
    index_path = KB_ROOT / "INDEX.md"
    lines = [
        "# Test KB 索引",
        "",
        "> 自动生成 — 不要手动编辑。运行 `python test_knowledge/scripts/build_kb.py` 更新。",
        "",
        f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
        "## 快速查找",
        "",
    ]

    for module, cfg in MODULE_MAPPING.items():
        kb_dir = cfg["kb_dir"]
        if not kb_dir.exists():
            continue
        md_files = sorted(kb_dir.glob("*.md"))
        if not md_files:
            continue
        module_name_cn = {
            "voice-room": "语音房",
            "im": "聊天",
            "social": "社交",
            "profile": "个人",
        }.get(module, module)
        lines.append(f"### {module_name_cn} ({module})")
        for f in md_files:
            slug = f.stem
            # Extract first meaningful sentence of business summary for preview
            try:
                body = f.read_text(encoding="utf-8")
                # Match AUTO:business-summary block specifically, skip markers
                m = re.search(
                    re.escape(AUTO_START.format(name="business-summary")) +
                    r"\s*\n+(.*?)\s*" + re.escape(AUTO_END),
                    body, re.DOTALL
                )
                preview = ""
                if m:
                    summary = m.group(1).strip()
                    # Skip any leading markers/blanks, take first sentence up to 80 chars
                    for line in summary.splitlines():
                        clean = line.strip().lstrip("-").strip()
                        if clean and not clean.startswith("<!") and not clean.startswith("#") and not clean == "---":
                            preview = clean.replace("*", "").replace("`", "")[:80]
                            break
            except Exception:
                preview = ""
            rel = os.path.relpath(f, KB_ROOT)
            suffix = f" — {preview}" if preview else ""
            lines.append(f"- [{slug}]({rel}){suffix}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 状态总览",
        "",
        "| 模块 | feature 数 |",
        "|------|-----------|",
    ])
    for module, cfg in MODULE_MAPPING.items():
        kb_dir = cfg["kb_dir"]
        count = len(list(kb_dir.glob("*.md"))) if kb_dir.exists() else 0
        lines.append(f"| {module} | {count} |")

    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ Updated INDEX.md with {sum(len(list(c['kb_dir'].glob('*.md'))) for c in MODULE_MAPPING.values() if c['kb_dir'].exists())} features")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Build all modules")
    parser.add_argument("--module", help="Build specific module (voice-room/im/social/profile)")
    parser.add_argument("--feature", help="Build specific feature slug (e.g. xiuxian)")
    parser.add_argument("--lessons-only", action="store_true", help="Only update lessons sections")
    parser.add_argument("--force", action="store_true", help="Overwrite HUMAN sections (use with caution)")
    args = parser.parse_args()

    if not args.all and not args.module and not args.feature and not args.lessons_only:
        parser.print_help()
        return

    total_built = 0

    if args.all:
        for module in MODULE_MAPPING.keys():
            features = discover_features(module)
            cfg = MODULE_MAPPING[module]
            for f in features:
                if build_feature(f, cfg["kb_dir"], args.force):
                    total_built += 1
                    print(f"  ✓ {module}/{f.slug}")
    elif args.module:
        features = discover_features(args.module)
        cfg = MODULE_MAPPING.get(args.module)
        if not cfg:
            print(f"Unknown module: {args.module}")
            sys.exit(1)
        for f in features:
            if build_feature(f, cfg["kb_dir"], args.force):
                total_built += 1
                print(f"  ✓ {args.module}/{f.slug}")
    elif args.feature:
        # Find the feature across all modules
        found = False
        for module, cfg in MODULE_MAPPING.items():
            features = discover_features(module)
            for f in features:
                if f.slug == args.feature:
                    if build_feature(f, cfg["kb_dir"], args.force):
                        total_built += 1
                        print(f"  ✓ {module}/{f.slug}")
                    found = True
                    break
            if found: break
        if not found:
            print(f"Feature {args.feature} not found")
            sys.exit(1)
    elif args.lessons_only:
        # Only update lessons sections in existing files
        for module, cfg in MODULE_MAPPING.items():
            kb_dir = cfg["kb_dir"]
            if not kb_dir.exists(): continue
            for md in kb_dir.glob("*.md"):
                slug = md.stem
                lessons = load_lessons_for_slug(slug)
                if not lessons: continue
                content = md.read_text(encoding="utf-8")
                new_body = render_lessons_section(lessons)
                updated = _replace_auto_section(content, "lessons", new_body)
                if updated != content:
                    md.write_text(updated, encoding="utf-8")
                    total_built += 1
                    print(f"  ✓ {module}/{slug} lessons updated ({len(lessons)})")

    update_index()
    print(f"\nDone. Built/updated {total_built} feature files.")


if __name__ == "__main__":
    main()
