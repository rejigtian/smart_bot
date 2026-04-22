"""
SubAgent orchestrator — runs complex tasks as a series of isolated sub-agents.

Architecture (Hermes-inspired):
  Parent: TestCaseAgent.run() detects complex task → calls run_with_subagents()
  ├── Planner: one LLM call → list[SubGoal]
  ├── SubAgent #1: fresh TestCaseAgent + fresh AgentMemory for SubGoal #1
  │     → SubResult(status, summary, key_actions, step_logs, tokens)
  ├── SubAgent #2: fresh memory, receives previous SubResult.summary as context
  │     → SubResult(...)
  └── Parent: aggregates SubResults → final CaseResult

Key property: each sub-agent has an independent context window (AgentMemory).
The parent only accumulates O(n_subgoals) of compressed SubResult summaries,
not O(n_total_steps) of raw messages. This solves _MAX_CTX pressure for long tasks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.planner import SubGoal

logger = logging.getLogger(__name__)


@dataclass
class SubResult:
    """Compressed output from one sub-agent execution."""
    subgoal_index: int
    subgoal_desc: str
    status: str              # pass / fail / error
    summary: str             # 2-3 sentence summary of what happened
    key_actions: list = field(default_factory=list)  # top 5 actions [{fn_name, args, result}]
    step_logs: list = field(default_factory=list)     # full StepLog list for DB persistence
    steps: int = 0
    total_tokens: int = 0
    screenshot_b64: str = ""


async def run_with_subagents(
    agent,  # TestCaseAgent instance (avoid circular import)
    case,   # TestCaseData
    subgoals: list[SubGoal],
) -> "CaseResult":
    """Execute a task by dispatching sub-goals to isolated sub-agents.

    Each sub-goal gets a fresh TestCaseAgent with its own memory.
    The parent aggregates results into a single CaseResult.
    """
    from core.test_agent import CaseResult, StepLog, TestCaseAgent
    from core.test_parser import TestCaseData

    await agent._log(f"  🧩 Subagent mode: {len(subgoals)} sub-goals")

    all_step_logs: list[StepLog] = []
    all_action_history: list = []
    sub_results: list[SubResult] = []
    total_steps = 0
    total_tokens = 0
    last_screenshot_b64 = ""
    log_lines: list[str] = []
    prev_summary = ""  # carried forward to next subagent as context
    consecutive_failures = 0

    for sg in subgoals:
        await agent._log(
            f"\n  ── SubGoal {sg.index}/{len(subgoals)}: {sg.description} ──"
        )
        log_lines.append(f"[SubGoal {sg.index}] {sg.description}")

        # Build sub-case: the subgoal description becomes the "path",
        # success_criteria becomes the "expected"
        sub_path = sg.description
        if prev_summary:
            sub_path = f"[Previous context: {prev_summary}]\n\nCurrent task: {sg.description}"

        sub_case = TestCaseData(
            path=sub_path,
            expected=sg.success_criteria or case.expected,
        )

        # Create a fresh agent with independent memory
        sub_agent = TestCaseAgent(
            device=agent.device,
            provider=agent.provider,
            model=agent.model,
            api_key=agent.api_key,
            api_base=agent.api_base,
            max_steps=sg.expected_steps + 2,  # small buffer over estimated steps
            step_delay=agent.step_delay,
            log_callback=agent.log_callback,
            verifier_provider=agent._verifier.provider,
            verifier_model=agent._verifier.model,
            verifier_api_key=agent._verifier.api_key,
            verifier_api_base=agent._verifier.api_base,
        )

        # Execute sub-goal in isolated context
        sub_result_raw = await sub_agent.run(sub_case)

        # Build compressed SubResult
        key_actions = sub_result_raw.action_history[-5:] if sub_result_raw.action_history else []

        # Generate summary from action history
        if sub_result_raw.action_history:
            action_lines = [
                f"Step {a['step']}: {a['fn_name']}({a.get('args', {})}) → {a['result'][:60]}"
                for a in sub_result_raw.action_history[-5:]
            ]
            summary = (
                f"SubGoal {sg.index} ({sub_result_raw.status}): {sg.description}. "
                f"Took {sub_result_raw.steps} steps. "
                f"Key actions: {'; '.join(a['fn_name'] for a in key_actions)}. "
                f"{sub_result_raw.reason}"
            )
        else:
            summary = (
                f"SubGoal {sg.index} ({sub_result_raw.status}): {sg.description}. "
                f"{sub_result_raw.reason}"
            )

        # Tag step logs with subgoal info
        for sl in sub_result_raw.step_logs:
            sl.subgoal_index = sg.index
            sl.subgoal_desc = sg.description

        sr = SubResult(
            subgoal_index=sg.index,
            subgoal_desc=sg.description,
            status=sub_result_raw.status,
            summary=summary[:500],
            key_actions=key_actions,
            step_logs=sub_result_raw.step_logs,
            steps=sub_result_raw.steps,
            total_tokens=sub_result_raw.total_tokens,
            screenshot_b64=sub_result_raw.screenshot_b64,
        )
        sub_results.append(sr)

        # Accumulate
        all_step_logs.extend(sub_result_raw.step_logs)
        all_action_history.extend(sub_result_raw.action_history)
        total_steps += sub_result_raw.steps
        total_tokens += sub_result_raw.total_tokens
        if sub_result_raw.screenshot_b64:
            last_screenshot_b64 = sub_result_raw.screenshot_b64

        await agent._log(
            f"  {'✅' if sr.status == 'pass' else '❌'} SubGoal {sg.index}: "
            f"{sr.status} ({sr.steps} steps, {sr.total_tokens} tokens)"
        )
        log_lines.append(f"  → {sr.status}: {sr.summary[:200]}")

        # Carry summary forward for next subagent's context
        prev_summary = sr.summary

        # Failure handling
        if sr.status in ("fail", "error"):
            consecutive_failures += 1
            if consecutive_failures >= 2:
                await agent._log("  🛑 2 consecutive subgoal failures — aborting")
                log_lines.append("[ABORT] 2 consecutive subgoal failures")
                break
        else:
            consecutive_failures = 0

    # ── Aggregate final result ────────────────────────────────────────────
    all_passed = all(sr.status == "pass" for sr in sub_results)
    any_error = any(sr.status == "error" for sr in sub_results)

    if all_passed:
        final_status = "pass"
        final_reason = f"All {len(sub_results)} sub-goals passed"
    elif any_error:
        final_status = "error"
        failed_sgs = [sr for sr in sub_results if sr.status != "pass"]
        final_reason = f"SubGoal {failed_sgs[0].subgoal_index} errored: {failed_sgs[0].summary[:200]}"
    else:
        final_status = "fail"
        failed_sgs = [sr for sr in sub_results if sr.status != "pass"]
        final_reason = f"SubGoal {failed_sgs[0].subgoal_index} failed: {failed_sgs[0].summary[:200]}"

    return CaseResult(
        status=final_status,
        reason=final_reason,
        steps=total_steps,
        screenshot_b64=last_screenshot_b64,
        log="\n".join(log_lines),
        action_history=all_action_history,
        step_logs=all_step_logs,
        total_tokens=total_tokens,
    )
