"""
Parse test cases from XMind (.xmind) or Markdown (.md) files.

Output: list of TestCaseData(path, expected)
  path     — full breadcrumb, e.g. "活动主页面 > 入口 > 开关-开"
  expected — the leaf node text (what we verify on screen)
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


@dataclass
class TestCaseData:
    path: str       # "Module > Scenario > Condition"
    expected: str   # leaf node — the assertion text


# ---------------------------------------------------------------------------
# XMind parser
# ---------------------------------------------------------------------------

def parse_xmind(source: Union[str, Path, bytes]) -> list[TestCaseData]:
    """Extract leaf-node paths from an XMind file (new JSON format)."""
    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    else:
        data = source

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        content = json.loads(zf.read("content.json"))

    cases: list[TestCaseData] = []
    for sheet in content:
        root = sheet.get("rootTopic", {})
        _walk_xmind(root, ancestors=[], cases=cases)
    return cases


def _walk_xmind(node: dict, ancestors: list[str], cases: list[TestCaseData]):
    title = node.get("title", "").strip()
    if not title:
        return

    children = node.get("children", {}).get("attached", [])
    path = ancestors + [title]

    if not children:
        # Leaf node → one test case
        cases.append(TestCaseData(
            path=" > ".join(path[:-1]) if len(path) > 1 else path[0],
            expected=title,
        ))
    else:
        for child in children:
            _walk_xmind(child, path, cases)


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def parse_markdown(source: Union[str, Path, bytes]) -> list[TestCaseData]:
    """
    Parse a Markdown file into test cases.

    Headings (#, ##, ###, ...) define the hierarchy.
    List items (-, *) under the current heading are leaf test cases.

    Example:
        ## Module
        ### Scenario
        - test condition A
        - test condition B
          - expected result   ← sub-list becomes the expected text
    """
    if isinstance(source, (str, Path)):
        if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source):
            text = Path(source).read_text(encoding="utf-8")
        else:
            text = source
    else:
        text = source.decode("utf-8")

    cases: list[TestCaseData] = []
    # heading_stack[i] = title at heading level (i+1)
    heading_stack: list[str] = []
    # item stack for nested list items
    item_stack: list[tuple[int, str]] = []  # (indent, text)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue

        # Heading
        if line.startswith("#"):
            stripped = line.lstrip("#")
            level = len(line) - len(stripped)
            title = stripped.strip()
            heading_stack = heading_stack[: level - 1] + [title]
            item_stack.clear()
            continue

        # List item
        lstripped = line.lstrip()
        indent = len(line) - len(lstripped)
        if lstripped.startswith(("- ", "* ", "+ ")):
            item_text = lstripped[2:].strip()

            # Pop item_stack to current indent level
            while item_stack and item_stack[-1][0] >= indent:
                item_stack.pop()

            item_stack.append((indent, item_text))

            # If the next line is deeper we keep going; emit now as a candidate.
            # We emit every leaf (handled by the absence of deeper items).
            # Emit eagerly — deeper items will emit themselves too.
            context = heading_stack + [s for _, s in item_stack[:-1]]
            path = " > ".join(context)
            cases.append(TestCaseData(path=path, expected=item_text))

    # De-duplicate: if a parent item also has children, the parent entry is
    # redundant because the children are more specific.  Keep only leaves.
    # We detect this by checking if any other case's path starts with this case.
    leaves: list[TestCaseData] = []
    all_paths = {f"{c.path} > {c.expected}" for c in cases}
    for c in cases:
        full = f"{c.path} > {c.expected}"
        is_parent = any(p.startswith(full + " >") for p in all_paths)
        if not is_parent:
            leaves.append(c)

    return leaves


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def parse_file(filename: str, content: bytes) -> list[TestCaseData]:
    """Dispatch to the right parser based on file extension."""
    ext = Path(filename).suffix.lower()
    if ext == ".xmind":
        return parse_xmind(content)
    elif ext in (".md", ".markdown"):
        return parse_markdown(content)
    else:
        raise ValueError(f"Unsupported format: {ext}. Use .xmind or .md")
