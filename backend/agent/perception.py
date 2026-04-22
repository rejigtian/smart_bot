"""
Perception layer — converts raw Portal state into LLM-ready text.

  _prune_node     — remove disabled/invisible/empty-container nodes from the a11y tree
  _format_node    — recursively render a single node into an indexed text line
  format_ui_state — entry point: Portal state dict → (formatted_text, elements, w, h)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)



def _prune_node(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Remove disabled/invisible nodes and pure container nodes with no useful content."""
    if not node.get("isEnabled", True) or not node.get("isVisibleToUser", True):
        return None

    pruned_children = []
    for child in node.get("children", []):
        pruned = _prune_node(child)
        if pruned is not None:
            pruned_children.append(pruned)

    # Button-like class names are always potentially tappable even when
    # the developer omitted contentDescription and the a11y tree reports
    # clickable=false (common for toolbar icons, FABs, custom views).
    class_name = node.get("className", "")
    is_button_like = any(c in class_name for c in ("Button", "ImageView", "FloatingAction"))

    has_content = bool(
        node.get("text")
        or node.get("contentDescription")
        or node.get("isClickable")
        or node.get("isFocusable")
        or node.get("isCheckable")
        or node.get("isScrollable")
        or is_button_like
    )

    if not has_content and not pruned_children:
        return None

    result = dict(node)
    result["children"] = pruned_children
    return result


def _format_node(
    node: Dict[str, Any],
    index_counter: List[int],
    elements: List[Dict[str, Any]],
    level: int = 0,
) -> str:
    """Recursively format an a11y tree node into one indented text line.

    Index assignment rule: only nodes with visible text/description OR
    interactive attributes (clickable/focusable/checkable/scrollable) get an
    index.  Pure layout containers are shown without an index for structural
    context only — the LLM cannot meaningfully target them.
    """
    # ── Bounds (stored for tap_element; not shown to LLM) ────────────────────
    bounds_raw = node.get("boundsInScreen", {})
    if isinstance(bounds_raw, dict):
        l = bounds_raw.get("left", 0)
        t = bounds_raw.get("top", 0)
        r = bounds_raw.get("right", 0)
        b = bounds_raw.get("bottom", 0)
        cx = (l + r) // 2
        cy = (t + b) // 2
    else:
        cx, cy = 0, 0

    # ── Text / class / resource ───────────────────────────────────────────────
    text = node.get("text") or node.get("contentDescription") or ""
    class_name = node.get("className", "")
    short_class = class_name.split(".")[-1] if class_name else ""
    resource_id = node.get("resourceId", "")
    # Strip "com.example.app:id/" prefix — keep only the id name
    if ":" in resource_id:
        resource_id = resource_id.split(":", 1)[1].lstrip("/")

    # ── Interactive flags ─────────────────────────────────────────────────────
    is_interactive = bool(
        node.get("isClickable") or node.get("isFocusable")
        or node.get("isCheckable") or node.get("isScrollable")
    )
    is_button_like = any(c in class_name for c in ("Button", "ImageView", "FloatingAction"))

    # ── Index only for nodes the LLM can act on ───────────────────────────────
    if text or is_interactive or is_button_like:
        idx: Optional[int] = index_counter[0]
        index_counter[0] += 1
        elements.append({
            "index": idx,
            "text": text or resource_id or short_class,
            "className": short_class,
            "resourceId": resource_id,
            "cx": cx,
            "cy": cy,
        })
    else:
        idx = None  # container-only node — no index assigned

    # ── Build display line ────────────────────────────────────────────────────
    indent = "  " * level
    parts: List[str] = []
    parts.append(f"{idx}." if idx is not None else " ")
    if short_class:
        parts.append(f"{short_class}:")
    if resource_id and text != resource_id:
        parts.append(f"<{resource_id}>")
    if text:
        parts.append(f'"{text}"')


    # Attribute tags — tell the LLM what this element CAN do
    attrs: List[str] = []
    if node.get("isClickable") or is_button_like:
        attrs.append("tap")
    if node.get("isCheckable"):
        attrs.append("✓" if node.get("isChecked") else "○")
    if node.get("isSelected"):
        attrs.append("sel")
    if node.get("isScrollable"):
        attrs.append("scroll")
    if attrs:
        parts.append("[" + ",".join(attrs) + "]")

    line = indent + " ".join(parts)
    children_lines = [
        _format_node(child, index_counter, elements, level + 1)
        for child in node.get("children", [])
    ]
    return "\n".join([line] + children_lines) if children_lines else line


def format_ui_state(
    raw_state: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], int, int, bool]:
    """Parse Portal state RPC response into (formatted_text, elements, w, h).

    Returns:
        formatted_text  — multi-line string ready to inject into LLM messages
        elements        — flat list with index/text/bounds/cx/cy per element
        screen_width
        screen_height
    """
    elements: List[Dict[str, Any]] = []
    index_counter = [1]

    device_ctx = raw_state.get("device_context", {})
    screen_bounds = device_ctx.get("screen_bounds", {})
    w = screen_bounds.get("width", 0)
    h = screen_bounds.get("height", 0)

    phone = raw_state.get("phone_state", {})
    current_app = phone.get("currentApp", "")
    package = phone.get("packageName", "")
    is_editable = phone.get("isEditable", False)
    focused_el = phone.get("focusedElement")
    focused_text = ""
    if focused_el and isinstance(focused_el, dict):
        focused_text = focused_el.get("text", "")

    app_line = ""
    if current_app and package:
        app_line = f"  App: {current_app} ({package})"
    elif current_app or package:
        app_line = f"  App: {current_app or package}"

    phone_lines = ["[Device State]"]
    if app_line:
        phone_lines.append(app_line)
    phone_lines.append(f"  Keyboard: {'visible' if is_editable else 'hidden'}")
    if focused_text:
        phone_lines.append(f"  Focused: \"{focused_text}\"")

    a11y = raw_state.get("a11y_tree", {})
    ui_lines = [
        "[UI Elements] format: index. ClassName: <resourceId> \"text\" [attrs]",
        "  attrs: tap=clickable  ○/✓=checkbox  sel=selected  scroll=scrollable",
        "  (nodes without an index are layout containers — not directly actionable)",
    ]
    if a11y:
        pruned = _prune_node(a11y)
        if pruned:
            ui_lines.append(_format_node(pruned, index_counter, elements))
        else:
            ui_lines.append("  (no elements)")
    else:
        ui_lines.append("  (no elements)")

    formatted = "\n".join(phone_lines) + "\n\n" + "\n".join(ui_lines)
    return formatted, elements, w, h, is_editable


# ── VLM-based element detection (fallback for empty a11y tree) ──────────────

_VLM_DETECT_PROMPT = """\
Analyze this Android screenshot and identify ALL interactive UI elements \
(buttons, text fields, icons, tabs, links, toggles, checkboxes, menu items).

For each element, provide:
- index: sequential number starting from 1
- label: text shown on/near the element (or brief visual description if no text)
- cx: center X pixel coordinate in this image
- cy: center Y pixel coordinate in this image
- type: one of [button, text_field, icon, tab, link, toggle, checkbox, menu_item, other]

Output a JSON array ONLY — no markdown, no explanation:
[{"index": 1, "label": "Settings", "cx": 270, "cy": 100, "type": "icon"}, ...]

Be precise with coordinates — use the grid labels on the image edges. \
Include ALL visible interactive elements. \
Do NOT include static text labels or decorative images that cannot be tapped.
"""


async def detect_elements_vlm(
    img_b64: str,
    img_width: int,
    img_height: int,
    provider: str,
    model: str,
    api_key: str = "",
    api_base: str = "",
) -> Tuple[str, List[Dict]]:
    """Detect interactive elements using VLM when a11y tree is empty.

    Returns (ui_text, elements) in the same format as format_ui_state(),
    so the caller can use them for SoM annotation and tap_element().
    """
    import litellm
    from agent.base import build_model_kwargs

    model_str, extra = build_model_kwargs(provider, model, api_base)
    kwargs: Dict[str, Any] = {
        "model": model_str,
        "messages": [
            {"role": "system", "content": _VLM_DETECT_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                {"type": "text", "text": f"Image: {img_width}x{img_height}px. Detect all interactive elements."},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
        **extra,
    }
    if api_key:
        kwargs["api_key"] = api_key

    try:
        response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=30.0)
        content = (response.choices[0].message.content or "").strip()

        content_clean = re.sub(r"^```[a-z]*\n?", "", content).rstrip("` \n")
        json_match = re.search(r"\[.*\]", content_clean, re.DOTALL)
        if not json_match:
            logger.warning("VLM detection returned non-JSON: %s", content[:200])
            return "", []

        items = json.loads(json_match.group())
        if not isinstance(items, list):
            return "", []

        elements: List[Dict] = []
        ui_lines = [
            "[UI Elements] (detected by vision — no a11y tree available)",
        ]
        for item in items[:30]:
            idx = item.get("index", len(elements) + 1)
            label = item.get("label", "")
            cx = item.get("cx", 0)
            cy = item.get("cy", 0)
            el_type = item.get("type", "other")

            elements.append({"index": idx, "cx": cx, "cy": cy, "label": label, "type": el_type})
            ui_lines.append(f"  {idx}. [tap] {el_type}: \"{label}\"")

        ui_text = "\n".join(ui_lines)
        logger.info("VLM detected %d elements from screenshot", len(elements))
        return ui_text, elements

    except Exception as exc:
        logger.warning("VLM element detection failed: %s", exc)
        return "", []
