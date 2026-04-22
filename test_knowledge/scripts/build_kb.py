"""
Test KB 构建器 — 从源码 + 业务 KB + 历史 LessonLearned 生成/更新测试知识库。

配置驱动：读取 test_knowledge/config.yml（复制 config.example.yml 为起点）。

Usage:
    python test_knowledge/scripts/build_kb.py --all                    # 全量
    python test_knowledge/scripts/build_kb.py --module voice-room      # 单模块
    python test_knowledge/scripts/build_kb.py --feature xiuxian        # 单 feature
    python test_knowledge/scripts/build_kb.py --lessons-only           # 只更新坑点
    python test_knowledge/scripts/build_kb.py --config PATH            # 自定义 config

幂等：保留 <!-- HUMAN --> ... <!-- END HUMAN --> 区段中的人工编辑。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


# ── Paths ─────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
KB_ROOT = SCRIPT_DIR.parent                                    # test_knowledge/
PROJECT_ROOT = KB_ROOT.parent                                  # smart-androidbot/
DEFAULT_CONFIG = KB_ROOT / "config.yml"
DB_PATH = PROJECT_ROOT / "backend/data/db.sqlite3"

# Section markers for idempotent updates
AUTO_START = "<!-- AUTO: {name} -->"
AUTO_END = "<!-- END AUTO -->"
HUMAN_START = "<!-- HUMAN -->"
HUMAN_END = "<!-- END HUMAN -->"


# ── Config loader ─────────────────────────────────────────────────────────

@dataclass
class ModuleConfig:
    slug: str
    display_name: str
    source_path: Path           # absolute
    business_kb_subdir: Optional[Path]   # absolute or None
    default_keywords: List[str]
    kb_dir: Path                # test_knowledge/features/<slug>/


@dataclass
class KBConfig:
    project_name: str
    display_name: str
    app_package: str
    source_root: Path
    layout_dir: str
    strings_file: str
    source_dir: str
    business_kb_root: Optional[Path]
    summary_heading: str
    modules: Dict[str, ModuleConfig] = field(default_factory=dict)
    feature_keywords: Dict[str, List[str]] = field(default_factory=dict)
    runtime_aliases: Dict[str, List[str]] = field(default_factory=dict)


def _expand_path(p: str) -> Path:
    """Expand ~, env vars, and return absolute Path."""
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


def load_config(config_path: Path) -> KBConfig:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found: {config_path}\n"
            f"Copy test_knowledge/config.example.yml to config.yml and edit it."
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    project = raw.get("project", {})
    source = raw.get("source", {})
    business_kb = raw.get("business_kb")  # may be None

    source_root = _expand_path(source.get("root", "."))
    business_kb_root = None
    summary_heading = "业务概述"
    if business_kb:
        business_kb_root = _expand_path(business_kb.get("root", ""))
        summary_heading = business_kb.get("summary_heading", "业务概述")

    cfg = KBConfig(
        project_name=project.get("name", "unknown"),
        display_name=project.get("display_name", "Unknown"),
        app_package=project.get("app_package", ""),
        source_root=source_root,
        layout_dir=source.get("layout_dir", "src/main/res/layout"),
        strings_file=source.get("strings_file", "src/main/res/values/strings.xml"),
        source_dir=source.get("source_dir", "src/main/java"),
        business_kb_root=business_kb_root,
        summary_heading=summary_heading,
        feature_keywords=raw.get("feature_keywords") or {},
        runtime_aliases=raw.get("runtime_aliases") or {},
    )

    # Parse modules
    for slug, m in (raw.get("modules") or {}).items():
        source_path = source_root / m.get("source_path", "")
        biz_subdir = None
        if business_kb_root and m.get("business_kb_subdir"):
            biz_subdir = business_kb_root / m["business_kb_subdir"]
        cfg.modules[slug] = ModuleConfig(
            slug=slug,
            display_name=m.get("display_name", slug),
            source_path=source_path,
            business_kb_subdir=biz_subdir,
            default_keywords=m.get("default_keywords") or [],
            kb_dir=KB_ROOT / "features" / slug,
        )

    return cfg


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class FeatureData:
    slug: str
    module: str
    business_md: Optional[Path]
    business_summary: str = ""
    elements: list = field(default_factory=list)
    source_files: list = field(default_factory=list)
    lessons: list = field(default_factory=list)


# ── Section helpers ───────────────────────────────────────────────────────

def _replace_auto_section(content: str, name: str, new_body: str) -> str:
    start = AUTO_START.format(name=name)
    pattern = re.compile(
        re.escape(start) + r"(.*?)" + re.escape(AUTO_END),
        re.DOTALL
    )
    if not pattern.search(content):
        return content
    return pattern.sub(f"{start}\n{new_body.strip()}\n{AUTO_END}", content)


# ── Source scanners (use config paths) ────────────────────────────────────

def scan_strings_xml(module_path: Path, strings_rel: str, keywords: list[str]) -> list[tuple[str, str]]:
    strings_file = module_path / strings_rel
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
        if any(kw.lower() in name.lower() or kw in value for kw in keywords):
            results.append((name, value))
    return results[:30]


def scan_layouts(module_path: Path, layout_rel: str, keywords: list[str]) -> list[tuple[str, Path]]:
    layout_dir = module_path / layout_rel
    if not layout_dir.exists():
        return []
    results = []
    for xml in layout_dir.glob("*.xml"):
        name_lower = xml.stem.lower()
        if any(kw.lower() in name_lower for kw in keywords):
            results.append((xml.stem, xml))
    return results[:20]


def scan_source_files(module_path: Path, source_rel: str, keywords: list[str]) -> list[Path]:
    src_dir = module_path / source_rel
    if not src_dir.exists():
        return []
    results = []
    for ext in ("*.kt", "*.java"):
        for path in src_dir.rglob(ext):
            path_str = str(path).lower()
            if any(kw.lower() in path_str for kw in keywords):
                results.append(path)
    return results[:30]


# ── Business KB parser ────────────────────────────────────────────────────

def parse_business_summary(md_path: Path, heading: str) -> str:
    if not md_path or not md_path.exists():
        return ""
    try:
        content = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(rf"##\s*{re.escape(heading)}\s*\n+([\s\S]*?)(?=\n##|\n---|\Z)", content)
    if m:
        summary = m.group(1).strip()
        summary = re.sub(r"\n---\s*$", "", summary).strip()
        paragraphs = [p.strip() for p in summary.split("\n\n") if p.strip()][:2]
        return "\n\n".join(paragraphs)
    return ""


# ── LessonLearned loader ──────────────────────────────────────────────────

def load_lessons_for_slug(slug: str) -> list[str]:
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
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


# ── Template ──────────────────────────────────────────────────────────────

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
1. start_app({{"package": "{app_package}"}})
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
        lines.append(
            f"| {el.get('name','-')} | `{el.get('id','-')}` "
            f"| {el.get('text','-')} | {el.get('source','-')} |"
        )
    return "\n".join(lines)


def render_lessons_section(lessons: list[str]) -> str:
    if not lessons:
        return "（无历史教训 — 运行测试后脚本自动汇总）"
    return "\n".join(f"{i}. {l}" for i, l in enumerate(lessons, 1))


def render_source_links(feature: FeatureData, source_root: Path) -> str:
    if not feature.source_files:
        return "（未扫描到相关源码）"
    by_type: Dict[str, list] = {"Activity": [], "Fragment": [], "Dialog": [], "Other": []}
    for f in feature.source_files[:20]:
        name = f.stem
        if "Activity" in name: by_type["Activity"].append(f)
        elif "Fragment" in name: by_type["Fragment"].append(f)
        elif "Dialog" in name: by_type["Dialog"].append(f)
        else: by_type["Other"].append(f)
    lines = []
    for cat, files in by_type.items():
        if files:
            lines.append(f"\n**{cat}**:")
            for f in files[:8]:
                try:
                    rel = f.relative_to(source_root)
                except ValueError:
                    rel = f
                lines.append(f"- `{rel}`")
    return "\n".join(lines) if lines else "（未扫描到相关源码）"


def rendered_section(rendered: str, name: str) -> str:
    start = AUTO_START.format(name=name)
    pattern = re.compile(re.escape(start) + r"\n(.*?)\n" + re.escape(AUTO_END), re.DOTALL)
    m = pattern.search(rendered)
    return m.group(1) if m else ""


# ── Main build logic ──────────────────────────────────────────────────────

def build_feature(feature: FeatureData, cfg: KBConfig, kb_dir: Path) -> bool:
    kb_dir.mkdir(parents=True, exist_ok=True)
    md_path = kb_dir / f"{feature.slug}.md"

    today = datetime.now().strftime("%Y-%m-%d")
    business_link = "N/A"
    business_link_name = "(none)"
    if feature.business_md:
        business_link_name = feature.business_md.name
        try:
            business_link = os.path.relpath(feature.business_md, kb_dir)
        except Exception:
            business_link = str(feature.business_md)

    # Find module source path for display
    module_cfg = cfg.modules.get(feature.module)
    source_module_rel = ""
    if module_cfg:
        try:
            source_module_rel = str(module_cfg.source_path.relative_to(cfg.source_root))
        except ValueError:
            source_module_rel = str(module_cfg.source_path)

    rendered = TEMPLATE.format(
        name=feature.slug.replace("-", " ").title(),
        business_link_name=business_link_name,
        business_link=business_link,
        source_module=source_module_rel,
        date=today,
        status="auto-generated" if not md_path.exists() else "auto + human-edited",
        business_summary=feature.business_summary or "（待补充）",
        elements_table=render_elements_table(feature.elements),
        lessons_section=render_lessons_section(feature.lessons),
        source_links=render_source_links(feature, cfg.source_root),
        app_package=cfg.app_package,
    )

    if md_path.exists():
        existing = md_path.read_text(encoding="utf-8")
        updated = existing
        for section_name in ("meta", "business-summary", "elements", "lessons", "source-links"):
            new_body = rendered_section(rendered, section_name)
            if new_body:
                updated = _replace_auto_section(updated, section_name, new_body)
        md_path.write_text(updated, encoding="utf-8")
    else:
        md_path.write_text(rendered, encoding="utf-8")

    return True


def discover_features(cfg: KBConfig, module: str) -> list[FeatureData]:
    mod = cfg.modules.get(module)
    if not mod:
        print(f"Module {module!r} not in config", file=sys.stderr)
        return []

    features: list[FeatureData] = []
    # Enumerate from business KB (preferred) if available
    seen_slugs = set()
    if mod.business_kb_subdir and mod.business_kb_subdir.exists():
        for md in sorted(mod.business_kb_subdir.glob("*.md")):
            if md.stem in ("README",):
                continue
            slug = md.stem
            seen_slugs.add(slug)
            features.append(_build_feature_data(slug, module, cfg, mod, business_md=md))

    # Also enumerate from existing KB dir to keep features that lack business md
    if mod.kb_dir.exists():
        for md in mod.kb_dir.glob("*.md"):
            slug = md.stem
            if slug in seen_slugs:
                continue
            features.append(_build_feature_data(slug, module, cfg, mod, business_md=None))

    return features


def _build_feature_data(
    slug: str, module: str, cfg: KBConfig, mod: ModuleConfig, business_md: Optional[Path]
) -> FeatureData:
    f = FeatureData(slug=slug, module=module, business_md=business_md)

    if business_md:
        f.business_summary = parse_business_summary(business_md, cfg.summary_heading)

    # Keywords: feature-specific overrides + module defaults + slug itself
    kws = list(cfg.feature_keywords.get(slug, []))
    kws.extend(mod.default_keywords)
    kws.append(slug.replace("-", "_"))
    kws.append(slug.replace("-", ""))
    kws = list(dict.fromkeys(kws))  # dedupe, preserve order

    f.source_files = scan_source_files(mod.source_path, cfg.source_dir, kws)

    # Build element entries
    for name, value in scan_strings_xml(mod.source_path, cfg.strings_file, kws)[:10]:
        f.elements.append({
            "name": value[:20],
            "id": f"@string/{name}",
            "text": value[:40],
            "source": "strings.xml",
        })
    for name, path in scan_layouts(mod.source_path, cfg.layout_dir, kws)[:5]:
        f.elements.append({
            "name": f"Layout: {name}",
            "id": name,
            "text": "-",
            "source": "res/layout",
        })

    f.lessons = load_lessons_for_slug(slug)
    return f


# ── INDEX.md updater ──────────────────────────────────────────────────────

def update_index(cfg: KBConfig):
    index_path = KB_ROOT / "INDEX.md"
    lines = [
        f"# Test KB 索引 — {cfg.display_name}",
        "",
        f"> 自动生成 — 不要手动编辑。运行 `python test_knowledge/scripts/build_kb.py` 更新。",
        "",
        f"项目: `{cfg.project_name}` | App: `{cfg.app_package}`",
        f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
        "## 快速查找",
        "",
    ]

    total = 0
    for module, mod in cfg.modules.items():
        if not mod.kb_dir.exists():
            continue
        md_files = sorted(mod.kb_dir.glob("*.md"))
        if not md_files:
            continue
        lines.append(f"### {mod.display_name} ({module})")
        for f in md_files:
            slug = f.stem
            try:
                body = f.read_text(encoding="utf-8")
                m = re.search(
                    re.escape(AUTO_START.format(name="business-summary")) +
                    r"\s*\n+(.*?)\s*" + re.escape(AUTO_END),
                    body, re.DOTALL
                )
                preview = ""
                if m:
                    summary = m.group(1).strip()
                    for line in summary.splitlines():
                        clean = line.strip().lstrip("-").strip()
                        if clean and not clean.startswith("<!") and not clean.startswith("#") and clean != "---":
                            preview = clean.replace("*", "").replace("`", "")[:80]
                            break
            except Exception:
                preview = ""
            rel = os.path.relpath(f, KB_ROOT)
            suffix = f" — {preview}" if preview else ""
            lines.append(f"- [{slug}]({rel}){suffix}")
        lines.append("")
        total += len(md_files)

    lines.extend([
        "---",
        "",
        "## 状态总览",
        "",
        "| 模块 | feature 数 |",
        "|------|-----------|",
    ])
    for module, mod in cfg.modules.items():
        count = len(list(mod.kb_dir.glob("*.md"))) if mod.kb_dir.exists() else 0
        lines.append(f"| {module} ({mod.display_name}) | {count} |")

    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ Updated INDEX.md ({total} features)")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                       help=f"Config file path (default: {DEFAULT_CONFIG})")
    parser.add_argument("--all", action="store_true", help="Build all modules")
    parser.add_argument("--module", help="Build specific module")
    parser.add_argument("--feature", help="Build specific feature slug")
    parser.add_argument("--lessons-only", action="store_true", help="Only update lessons sections")
    args = parser.parse_args()

    if not (args.all or args.module or args.feature or args.lessons_only):
        parser.print_help()
        return

    cfg = load_config(args.config)
    print(f"Project: {cfg.display_name} ({cfg.project_name})")
    print(f"Source root: {cfg.source_root}")
    if cfg.business_kb_root:
        print(f"Business KB: {cfg.business_kb_root}")
    print()

    total_built = 0

    if args.all:
        for module in cfg.modules:
            for f in discover_features(cfg, module):
                if build_feature(f, cfg, cfg.modules[module].kb_dir):
                    total_built += 1
                    print(f"  ✓ {module}/{f.slug}")
    elif args.module:
        for f in discover_features(cfg, args.module):
            if build_feature(f, cfg, cfg.modules[args.module].kb_dir):
                total_built += 1
                print(f"  ✓ {args.module}/{f.slug}")
    elif args.feature:
        found = False
        for module in cfg.modules:
            for f in discover_features(cfg, module):
                if f.slug == args.feature:
                    if build_feature(f, cfg, cfg.modules[module].kb_dir):
                        total_built += 1
                        print(f"  ✓ {module}/{f.slug}")
                    found = True
                    break
            if found: break
        if not found:
            print(f"Feature {args.feature!r} not found")
            sys.exit(1)
    elif args.lessons_only:
        for module, mod in cfg.modules.items():
            if not mod.kb_dir.exists(): continue
            for md in mod.kb_dir.glob("*.md"):
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

    update_index(cfg)
    print(f"\nDone. Built/updated {total_built} feature files.")


if __name__ == "__main__":
    main()
