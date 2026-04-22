"""
Planner layer — decomposes complex test tasks into sub-goals before execution.

Two modes:
  1. Lightweight (Phase 1): generate_plan() → text plan injected as pinned message.
  2. Structured (Phase 2): generate_subgoals() → list[SubGoal] for subagent dispatch.

Inspired by AutoGLM's Planner/Grounder separation and Hermes Agent's subagent
pattern. Each SubGoal can be executed by an isolated ExecutorSubAgent with its
own AgentMemory, keeping parent context at O(subgoals) not O(steps).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional

import litellm

from agent.base import build_model_kwargs

logger = logging.getLogger(__name__)

# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class SubGoal:
    """One decomposed sub-task from the Planner."""
    index: int                # 1-based
    description: str          # "Open Settings app and navigate to About Phone"
    success_criteria: str     # "About Phone page is visible with version info"
    expected_steps: int = 5   # hint for max_steps allocation


# ── Prompts ──────────────────────────────────────────────────────────────────

PLANNER_PROMPT = """\
You are an Android test planning assistant. Given a test case description, \
break it down into a numbered list of concrete navigation steps.

Rules:
- Each step should be ONE action (tap a button, scroll down, type text, etc.)
- Use the app/feature names from the test case description
- Be specific about WHAT to tap/type (button text, menu item name)
- Do NOT include element indices or coordinates — those change between runs
- Keep steps concise (one line each)
- Include the final verification step ("Verify that X is visible")
- Maximum 10 steps. If the task is simple (1-3 steps), output fewer.

Example:
Test: 打开设置 > 关于手机 > 查看系统版本
Plan:
1. Open the Settings app
2. Scroll down to find "About phone" / "关于手机"
3. Tap "About phone"
4. Verify that system version information is displayed

Output ONLY the numbered plan, no other text.
"""

SUBGOAL_PROMPT = """\
You are an Android test planning assistant. Break down a test case into \
2-5 sub-goals. Each sub-goal is a self-contained navigation milestone that \
can be verified independently.

Rules:
- Each sub-goal should describe a milestone, NOT a single tap/scroll.
  Good: "Navigate to the Settings > About Phone page"
  Bad: "Tap the Settings icon" (too granular)
- Include a success_criteria for each sub-goal — what should be visible on \
screen when this sub-goal is complete.
- The last sub-goal should verify the final expected result.
- 2-5 sub-goals total. If the task only needs 1 sub-goal, output just 1.

Output JSON array ONLY — no markdown, no explanation:
[
  {"description": "...", "success_criteria": "...", "expected_steps": 5},
  ...
]

expected_steps is your estimate of how many agent steps this sub-goal needs (3-10).
"""


def _is_complex(path: str, expected: str) -> bool:
    """Heuristic: does this task benefit from subgoal decomposition?"""
    if len(expected) > 40:
        return True
    if path.count(">") >= 2:
        return True
    if len(path) > 80:
        return True
    return False


# ── Phase 1: text plan ──────────────────────────────────────────────────────

async def generate_plan(
    path: str,
    expected: str,
    provider: str,
    model: str,
    api_key: str = "",
    api_base: str = "",
) -> Optional[str]:
    """Generate a text execution plan (Phase 1 — injected as pinned message).

    Returns the plan as a formatted string, or None if the task is too simple.
    """
    if len(path) < 30 and ">" not in path and "\n" not in path:
        logger.debug("Skipping planner for simple task: %s", path[:50])
        return None

    task_description = f"Test case: {path}\nExpected result: {expected}"

    model_str, extra = build_model_kwargs(provider, model, api_base)
    kwargs: dict[str, Any] = {
        "model": model_str,
        "messages": [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": task_description},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
        **extra,
    }
    if api_key:
        kwargs["api_key"] = api_key

    try:
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=30.0,
        )
        plan_text = (response.choices[0].message.content or "").strip()

        if not plan_text or len(plan_text) < 10:
            return None

        lines = [l for l in plan_text.splitlines() if l.strip() and l.strip()[0].isdigit()]
        if len(lines) < 2:
            return None

        logger.info("Planner generated %d-step plan for: %s", len(lines), path[:60])
        return plan_text

    except Exception as exc:
        logger.warning("Planner failed (%s) — proceeding without plan", exc)
        return None


# ── Phase 2: structured sub-goals ───────────────────────────────────────────

async def generate_subgoals(
    path: str,
    expected: str,
    provider: str,
    model: str,
    api_key: str = "",
    api_base: str = "",
) -> Optional[List[SubGoal]]:
    """Decompose a complex task into SubGoal objects for subagent execution.

    Returns None if the task is simple or planning fails — caller should
    fall back to single-agent execution.
    """
    if not _is_complex(path, expected):
        return None

    task_description = f"Test case: {path}\nExpected result: {expected}"

    model_str, extra = build_model_kwargs(provider, model, api_base)
    kwargs: dict[str, Any] = {
        "model": model_str,
        "messages": [
            {"role": "system", "content": SUBGOAL_PROMPT},
            {"role": "user", "content": task_description},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
        **extra,
    }
    if api_key:
        kwargs["api_key"] = api_key

    try:
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=30.0,
        )
        content = (response.choices[0].message.content or "").strip()

        # Extract JSON array from response (may be wrapped in markdown)
        content_clean = re.sub(r"^```[a-z]*\n?", "", content).rstrip("` \n")
        json_match = re.search(r"\[.*\]", content_clean, re.DOTALL)
        if not json_match:
            logger.warning("Planner returned non-JSON: %s", content[:200])
            return None

        items = json.loads(json_match.group())
        if not isinstance(items, list) or len(items) < 1:
            return None

        subgoals = []
        for i, item in enumerate(items[:5]):  # cap at 5 sub-goals
            subgoals.append(SubGoal(
                index=i + 1,
                description=item.get("description", ""),
                success_criteria=item.get("success_criteria", ""),
                expected_steps=min(max(item.get("expected_steps", 5), 3), 10),
            ))

        if len(subgoals) < 1:
            return None

        logger.info(
            "Planner decomposed into %d sub-goals for: %s",
            len(subgoals), path[:60],
        )
        return subgoals

    except Exception as exc:
        logger.warning("Subgoal planner failed (%s) — falling back", exc)
        return None
