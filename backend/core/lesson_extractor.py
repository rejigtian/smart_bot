"""
Lesson Extractor — analyzes completed runs to extract negative experiences.

Detects "wasted step" patterns:
  1. tap → keyboard opened → back/press_key(back)  → "Don't tap input fields"
  2. tap → wrong dialog/page → close/back           → "X button is not the target"
  3. tap(x,y) → ERROR                               → "Coordinates were wrong"

Extracted lessons are stored in DB and injected into future runs of the same
case or app, so the agent avoids repeating the same mistakes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import litellm

from agent.base import build_model_kwargs
from db.database import AsyncSessionLocal
from db.models import LessonLearned, TestResult, TestStepLog
from sqlalchemy import func, select

logger = logging.getLogger(__name__)

# ── Pattern-based detection (no LLM needed) ─────────────────────────────────

def _detect_wasted_steps(steps: list[dict]) -> list[dict]:
    """Detect wasted step patterns from action history.

    Returns list of {step, action, mistake, lesson}.
    """
    lessons = []

    for i in range(len(steps) - 1):
        curr = steps[i]
        nxt = steps[i + 1]

        curr_action = curr.get("action", "")
        curr_result = curr.get("action_result", "")
        nxt_action = nxt.get("action", "")
        nxt_result = nxt.get("action_result", "")
        nxt_thought = nxt.get("thought", "")

        # Combine action + result text for broader keyword matching
        nxt_all = f"{nxt_action} {nxt_result} {nxt_thought}".lower()

        # Pattern 1: tap → keyboard opened → need to close
        if ("keyboard" in curr_result.lower() or "keyboard" in nxt_thought.lower()) and \
           ("press_key" in nxt_action or "back" in nxt_action):
            lessons.append({
                "step": curr.get("step", i),
                "action": curr_action,
                "mistake": "Tapped input field, triggered keyboard",
                "lesson": f"Do NOT tap near the bottom chat/input area. "
                          f"The action '{curr_action[:80]}' accidentally opened the keyboard.",
            })

        # Pattern 2: tap → wrong element → immediately close/back/dismiss
        elif ("tap" in curr_action):
            # Check if next step is a recovery action
            recovery_signals = ["close", "dismiss", "back", "press_key", "关闭"]
            wrong_indicators = [
                "not what i need", "wrong", "accidentally", "不是", "误",
                "this is not", "unintended", "unrelated", "didn't want",
            ]
            is_recovery = any(kw in nxt_all for kw in recovery_signals)
            is_mistake = any(ind in nxt_thought.lower() for ind in wrong_indicators)
            if is_recovery and is_mistake:
                lessons.append({
                    "step": curr.get("step", i),
                    "action": curr_action,
                    "mistake": f"Tapped wrong element, had to recover",
                    "lesson": f"Avoid: '{curr_action[:80]}' — it opens an unrelated dialog/page. "
                              f"Context: {nxt_thought[:100]}",
                })

        # Pattern 3: tap(x,y) with ERROR result
        if "tap(" in curr_action and "ERROR" in curr_result:
            lessons.append({
                "step": curr.get("step", i),
                "action": curr_action,
                "mistake": "Coordinate tap failed",
                "lesson": f"The coordinate tap '{curr_action[:80]}' failed. Use tap_element(index) instead.",
            })

        # Pattern 4: dismissing unexpected popup/dialog (game events, rewards, ads)
        curr_thought = curr.get("thought", "").lower()
        dismiss_signals = ["dismiss", "close this", "关闭", "我知道了", "确定", "取消",
                           "outside the dialog", "return to"]
        popup_signals = ["dialog", "popup", "弹窗", "奖励", "reward", "event",
                         "奇遇", "公告", "notice", "ad "]
        if any(d in curr_thought for d in dismiss_signals) and \
           any(p in curr_thought for p in popup_signals):
            lessons.append({
                "step": curr.get("step", i),
                "action": curr_action,
                "mistake": "Spent step dismissing popup/dialog",
                "lesson": f"Random popup/dialog appeared at step {curr.get('step', i)}. "
                          f"Quickly dismiss with tap_element on '关闭'/'我知道了'/'确定' button, "
                          f"or tap outside the dialog. Don't waste multiple steps on it.",
            })

    # Pattern 5: max steps exhausted — overall lesson about efficiency
    if len(steps) >= 18:  # near max_steps limit
        last = steps[-1]
        if "mark_done" not in last.get("action", ""):
            # Agent didn't finish — ran out of steps
            # Count steps spent on popups/dialogs
            popup_steps = sum(
                1 for s in steps
                if any(p in s.get("thought", "").lower()
                       for p in ["dismiss", "dialog", "popup", "弹窗", "奖励", "我知道了"])
            )
            if popup_steps >= 2:
                lessons.append({
                    "step": len(steps),
                    "action": "max_steps_reached",
                    "mistake": f"Ran out of steps ({len(steps)}), {popup_steps} steps spent on popups",
                    "lesson": f"This task may trigger {popup_steps} popups/dialogs. "
                              f"Dismiss them with ONE action each (tap '我知道了' or '确定'). "
                              f"Don't try to analyze popup content — just close immediately.",
                })

    return lessons


# ── LLM-based analysis (deeper understanding) ───────────────────────────────

_ANALYSIS_PROMPT = """\
Analyze this test execution trace and identify MISTAKES — steps where the agent \
tapped the wrong element, opened an unintended dialog, or wasted time recovering.

For each mistake, output:
- step: which step number
- mistake: what went wrong (one sentence)
- lesson: a concrete rule to avoid this in the future (one sentence, imperative)

Output JSON array ONLY:
[{"step": 8, "mistake": "Tapped chat input field", "lesson": "Avoid tapping the bottom input bar in party rooms"}, ...]

If there are no mistakes, output: []
"""


async def analyze_with_llm(
    steps: list[dict],
    provider: str,
    model: str,
    api_key: str = "",
    api_base: str = "",
) -> list[dict]:
    """Use LLM to analyze step trace for deeper mistake patterns."""
    # Build compact trace
    trace_lines = []
    for s in steps:
        trace_lines.append(
            f"Step {s.get('step', '?')}: "
            f"Thought: {s.get('thought', '')[:150]} | "
            f"Action: {s.get('action', '')[:100]} | "
            f"Result: {s.get('action_result', '')[:100]}"
        )
    trace_text = "\n".join(trace_lines)

    model_str, extra = build_model_kwargs(provider, model, api_base)
    kwargs = {
        "model": model_str,
        "messages": [
            {"role": "system", "content": _ANALYSIS_PROMPT},
            {"role": "user", "content": trace_text},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
        **extra,
    }
    if api_key:
        kwargs["api_key"] = api_key

    try:
        response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=30.0)
        content = (response.choices[0].message.content or "").strip()

        json_match = re.search(r"\[.*\]", content, re.DOTALL)
        if json_match:
            items = json.loads(json_match.group())
            return items if isinstance(items, list) else []
        return []
    except Exception as exc:
        logger.warning("LLM lesson analysis failed: %s", exc)
        return []


# ── Main extraction + storage ────────────────────────────────────────────────

async def extract_and_store_lessons(
    result_id: str,
    run_id: str,
    case_id: str,
    suite_id: str = "",
    app_package: str = "",
    task_keyword: str = "",
    provider: str = "",
    model: str = "",
    api_key: str = "",
    api_base: str = "",
) -> int:
    """Extract lessons from a completed test result and store in DB.

    Returns the number of lessons stored.
    """
    async with AsyncSessionLocal() as session:
        # Load step logs
        res = await session.execute(
            select(TestStepLog)
            .where(TestStepLog.result_id == result_id)
            .order_by(TestStepLog.step)
        )
        step_logs = res.scalars().all()

        if len(step_logs) < 3:
            return 0  # too short to have mistakes

        steps = [
            {
                "step": sl.step,
                "thought": sl.thought,
                "action": sl.action,
                "action_result": sl.action_result,
            }
            for sl in step_logs
        ]

    # Pattern-based detection (fast, no LLM cost)
    pattern_lessons = _detect_wasted_steps(steps)

    # LLM-based analysis (optional, deeper)
    llm_lessons = []
    if provider and model:
        llm_lessons = await analyze_with_llm(steps, provider, model, api_key, api_base)

    # Merge and deduplicate
    all_lessons = []
    seen = set()
    for src in [pattern_lessons, llm_lessons]:
        for item in src:
            lesson_text = item.get("lesson", "")
            if lesson_text and lesson_text not in seen:
                seen.add(lesson_text)
                all_lessons.append(item)

    if not all_lessons:
        return 0

    # Store in DB — deduplicate against existing lessons and cap total per case
    _MAX_LESSONS_PER_CASE = 8  # keep at most this many lessons per case_id

    async with AsyncSessionLocal() as session:
        # Load existing lessons for dedup
        existing_res = await session.execute(
            select(LessonLearned.lesson)
            .where(LessonLearned.case_id == case_id)
        )
        existing_texts = {row[0] for row in existing_res.all()}

        stored = 0
        for item in all_lessons[:5]:
            lesson_text = item.get("lesson", "")
            if lesson_text in existing_texts:
                continue  # already have this exact lesson
            session.add(LessonLearned(
                case_id=case_id,
                suite_id=suite_id,
                app_package=app_package,
                task_keyword=task_keyword,
                screen_context=item.get("mistake", ""),
                lesson=lesson_text,
                source_run_id=run_id,
                source_step=item.get("step", 0),
            ))
            existing_texts.add(lesson_text)
            stored += 1
        await session.commit()

        # Evict oldest lessons if over the cap
        count_res = await session.execute(
            select(func.count(LessonLearned.id))
            .where(LessonLearned.case_id == case_id)
        )
        total = count_res.scalar() or 0
        if total > _MAX_LESSONS_PER_CASE:
            # Delete oldest ones beyond the cap
            old_res = await session.execute(
                select(LessonLearned.id)
                .where(LessonLearned.case_id == case_id)
                .order_by(LessonLearned.created_at.asc())
                .limit(total - _MAX_LESSONS_PER_CASE)
            )
            old_ids = [row[0] for row in old_res.all()]
            if old_ids:
                from sqlalchemy import delete
                await session.execute(
                    delete(LessonLearned).where(LessonLearned.id.in_(old_ids))
                )
                await session.commit()
                logger.info("Evicted %d old lessons for case %s", len(old_ids), case_id[:8])

    logger.info("Stored %d new lessons from result %s (total: %d)",
                 stored, result_id[:8], min(total + stored, _MAX_LESSONS_PER_CASE))
    return stored


async def load_lessons_for_case(
    case_id: str,
    suite_id: str = "",
    task_keyword: str = "",
) -> list[str]:
    """Load relevant lessons for a case.

    Matches by (in priority order):
      1. Exact case_id match
      2. Same suite_id (lessons from sibling cases in same suite)
      3. task_keyword overlap (for quick runs with different case_ids but similar tasks)
    """
    from sqlalchemy import or_

    async with AsyncSessionLocal() as session:
        conditions = []
        if case_id:
            conditions.append(LessonLearned.case_id == case_id)
        if suite_id:
            conditions.append(LessonLearned.suite_id == suite_id)
        if task_keyword:
            # SQLite LIKE for fuzzy keyword matching
            conditions.append(LessonLearned.task_keyword.like(f"%{task_keyword}%"))

        if not conditions:
            return []

        res = await session.execute(
            select(LessonLearned)
            .where(or_(*conditions))
            .order_by(LessonLearned.created_at.desc())
            .limit(10)
        )
        lessons = res.scalars().all()

        # Deduplicate by lesson text
        seen = set()
        result = []
        for l in lessons:
            if l.lesson and l.lesson not in seen:
                seen.add(l.lesson)
                result.append(l.lesson)
        return result
