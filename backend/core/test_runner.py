"""
Orchestrates a full test run: iterates over all TestCase rows for a TestRun,
executes each via TestCaseAgent, persists results, and streams live logs.

Log streaming uses RunState (history buffer + asyncio.Condition) so that:
- Any number of SSE consumers can subscribe.
- Reconnecting consumers replay the full history from the beginning.
- Cancellation propagates cleanly to the background task.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator, List, Optional

from sqlalchemy import select, update

from agent.ws_device import WebSocketDevice
from core.test_agent import CaseResult, TestCaseAgent
from core.test_parser import TestCaseData
from db.database import AsyncSessionLocal
from db.models import TestCase, TestResult, TestRun, TestStepLog
from ws.portal_ws import connected_devices

logger = logging.getLogger(__name__)


# ── RunState ─────────────────────────────────────────────────────────────────

class RunState:
    """
    Holds live state for one in-progress run.
    Multiple SSE consumers can call stream() concurrently; each gets the full
    history starting from log[0], then follows new lines live.
    """

    def __init__(self) -> None:
        self.logs: List[str] = []
        self.done: bool = False
        self.task: Optional[asyncio.Task] = None
        self.cond: asyncio.Condition = asyncio.Condition()

    async def emit(self, msg: str) -> None:
        async with self.cond:
            self.logs.append(msg)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.cond.notify_all()

    async def stream(self) -> AsyncGenerator[str, None]:
        """Replay all buffered logs then follow live until done."""
        idx = 0
        while True:
            async with self.cond:
                # Wait until there is something new to send or the run is done
                await self.cond.wait_for(lambda: idx < len(self.logs) or self.done)
                snapshot_len = len(self.logs)
                is_done = self.done
            # Send everything up to snapshot
            while idx < snapshot_len:
                yield f"data: {self.logs[idx]}\n\n"
                idx += 1
            if is_done and idx >= snapshot_len:
                yield "data: [done]\n\n"
                return


# Registry of active run states
active_runs: dict[str, RunState] = {}


# ── Public API ────────────────────────────────────────────────────────────────

async def start_run(run_id: str, max_steps: int = 20, step_delay: float = 1.0, max_retries: int = 0) -> None:
    """Register a RunState and launch execute_run as a background task."""
    state = RunState()
    active_runs[run_id] = state
    task = asyncio.create_task(
        execute_run(run_id, state, max_steps=max_steps, step_delay=step_delay, max_retries=max_retries)
    )
    state.task = task


async def cancel_run(run_id: str) -> bool:
    """Cancel an active run. Returns True if the run was found and cancelled."""
    state = active_runs.get(run_id)
    if state is None or state.task is None:
        return False
    state.task.cancel()
    return True


async def run_log_stream(run_id: str) -> AsyncGenerator[str, None]:
    """SSE generator for an active run (history replay + live follow)."""
    state = active_runs.get(run_id)
    if state is None:
        yield "data: Run not found or not active\n\n"
        return
    async for chunk in state.stream():
        yield chunk


# ── Background task ───────────────────────────────────────────────────────────

async def execute_run(
    run_id: str,
    state: RunState,
    max_steps: int = 20,
    step_delay: float = 1.0,
    max_retries: int = 0,
) -> None:
    """Main run loop. Called as an asyncio task via start_run()."""

    async def emit(msg: str) -> None:
        logger.info("[run:%s] %s", run_id, msg)
        await state.emit(msg)

    try:
        async with AsyncSessionLocal() as session:
            run_row = await session.get(TestRun, run_id)
            if not run_row:
                await emit(f"ERROR: run {run_id} not found")
                return

            device_id = run_row.device_id
            provider = run_row.provider
            model = run_row.model

            result = await session.execute(
                select(TestCase)
                .where(TestCase.suite_id == run_row.suite_id)
                .order_by(TestCase.order)
            )
            cases = result.scalars().all()

            res_result = await session.execute(
                select(TestResult).where(TestResult.run_id == run_id)
            )
            result_rows = {r.case_id: r for r in res_result.scalars().all()}

        # Check device is connected
        conn = connected_devices.get(device_id)
        if conn is None or not conn.is_connected:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(TestRun).where(TestRun.id == run_id)
                    .values(status="error", finished_at=datetime.utcnow())
                )
                await session.commit()
            await emit(f"ERROR: Device {device_id} is not connected")
            return

        device = WebSocketDevice(conn)
        api_key = _load_api_key(provider)
        api_base = _load_api_base(provider)
        v_provider, v_model, v_key, v_base = _load_verifier_settings()

        # Mark run as running
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TestRun).where(TestRun.id == run_id).values(status="running")
            )
            await session.commit()

        await emit(f"Run started: {len(cases)} test cases, device={device_id}, model={provider}/{model}")

        passed = failed = errored = skipped = 0

        def _expand_params(path: str, expected: str, params_json: str) -> list[TestCaseData]:
            """Expand a parameterized case into multiple TestCaseData instances."""
            if not params_json:
                return [TestCaseData(path=path, expected=expected)]
            try:
                param_sets = json.loads(params_json)
                if not isinstance(param_sets, list) or len(param_sets) == 0:
                    return [TestCaseData(path=path, expected=expected)]
            except Exception:
                return [TestCaseData(path=path, expected=expected)]
            expanded = []
            import re
            for ps in param_sets:
                if not isinstance(ps, dict):
                    continue
                p = path
                e = expected
                for k, v in ps.items():
                    p = p.replace(f"{{{{{k}}}}}", str(v))
                    e = e.replace(f"{{{{{k}}}}}", str(v))
                expanded.append(TestCaseData(path=p, expected=e))
            return expanded or [TestCaseData(path=path, expected=expected)]

        # Expand parameterized cases into flat list
        expanded_cases = []
        for case_row in cases:
            variants = _expand_params(case_row.path, case_row.expected, case_row.parameters or "")
            for v in variants:
                expanded_cases.append((case_row, v))

        for idx, (case_row, case_data) in enumerate(expanded_cases):
            result_row = result_rows.get(case_row.id)

            await emit(f"\n[{idx+1}/{len(expanded_cases)}] {case_data.path} | {case_data.expected}")

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(TestResult).where(TestResult.id == result_row.id)
                        .values(status="running", started_at=datetime.utcnow())
                    )
                    await session.commit()

            async def log_cb(msg: str) -> None:
                await emit(f"  {msg}")

            # Load the most recent starred result for this case as a reference.
            # Enrich with StepLog thoughts for better few-shot context.
            reference_examples: list = []
            async with AsyncSessionLocal() as session:
                ref_res = await session.execute(
                    select(TestResult)
                    .where(TestResult.case_id == case_row.id, TestResult.is_starred == True)
                    .order_by(TestResult.finished_at.desc())
                    .limit(1)
                )
                ref_row = ref_res.scalar_one_or_none()
                if ref_row and ref_row.action_history_json:
                    try:
                        reference_examples = json.loads(ref_row.action_history_json)
                        # Enrich with thought from StepLog if available
                        step_res = await session.execute(
                            select(TestStepLog)
                            .where(TestStepLog.result_id == ref_row.id)
                            .order_by(TestStepLog.step)
                        )
                        step_thoughts = {
                            sl.step: sl.thought
                            for sl in step_res.scalars().all()
                            if sl.thought
                        }
                        for rec in reference_examples:
                            thought = step_thoughts.get(rec.get("step", 0), "")
                            if thought:
                                rec["thought"] = thought[:200]
                        # Filter out wasted steps: tap→back/close pairs
                        filtered = []
                        skip_next = False
                        for j, rec in enumerate(reference_examples):
                            if skip_next:
                                skip_next = False
                                continue
                            fn = rec.get("fn_name", "")
                            # Check if next step is a recovery (back/close)
                            if j + 1 < len(reference_examples):
                                nxt_fn = reference_examples[j + 1].get("fn_name", "")
                                nxt_thought = step_thoughts.get(reference_examples[j + 1].get("step", 0), "")
                                recovery_fns = {"press_key", "global_action"}
                                wrong_kw = ["wrong", "not what", "accidentally", "误", "不是", "关闭", "keyboard"]
                                if nxt_fn in recovery_fns and any(kw in nxt_thought.lower() for kw in wrong_kw):
                                    skip_next = True  # skip both current (mistake) and next (recovery)
                                    continue
                            filtered.append(rec)
                        if len(filtered) < len(reference_examples):
                            await emit(f"  📌 Loaded {len(filtered)}-step reference (filtered {len(reference_examples) - len(filtered)} wasted steps)")
                        else:
                            await emit(f"  📌 Loaded {len(filtered)}-step reference from starred run")
                        reference_examples = filtered
                    except Exception:
                        pass

            # Load lessons learned from past mistakes
            # Extract a keyword from the task path for fuzzy matching across quick runs
            _task_kw = case_row.path.split(">")[0].strip()[:30] if ">" in case_row.path else case_row.path[:30]
            lessons: list[str] = []
            try:
                from core.lesson_extractor import load_lessons_for_case
                lessons = await load_lessons_for_case(
                    case_id=case_row.id,
                    suite_id=case_row.suite_id,
                    task_keyword=_task_kw,
                )
                if lessons:
                    await emit(f"  📖 Loaded {len(lessons)} lessons from past runs")
            except Exception:
                pass

            agent = TestCaseAgent(
                device=device,
                provider=provider,
                model=model,
                api_key=api_key,
                api_base=api_base,
                max_steps=max_steps,
                step_delay=step_delay,
                log_callback=log_cb,
                verifier_provider=v_provider,
                verifier_model=v_model,
                verifier_api_key=v_key,
                verifier_api_base=v_base,
                reference_examples=reference_examples or None,
                lessons_learned=lessons or None,
            )

            case_result: Optional[CaseResult] = None
            for attempt in range(1 + max_retries):
                try:
                    case_result = await agent.run(case_data)
                except asyncio.CancelledError:
                    if result_row:
                        async with AsyncSessionLocal() as session:
                            await session.execute(
                                update(TestResult).where(TestResult.id == result_row.id)
                                .values(status="error", reason="Run cancelled", finished_at=datetime.utcnow())
                            )
                            await session.commit()
                    raise
                except Exception as e:
                    case_result = CaseResult(status="error", reason=str(e), steps=0)

                # Retry on fail/error if retries remain
                if case_result.status in ("fail", "error") and attempt < max_retries:
                    await emit(f"  ↩ Retry {attempt + 1}/{max_retries} — resetting to home screen…")
                    try:
                        await device.global_action("home")
                        await asyncio.sleep(2.0)
                    except Exception:
                        pass
                    # Recreate agent with fresh memory for the retry
                    agent = TestCaseAgent(
                        device=device, provider=provider, model=model,
                        api_key=api_key, api_base=api_base,
                        max_steps=max_steps, step_delay=step_delay,
                        log_callback=log_cb,
                        verifier_provider=v_provider, verifier_model=v_model,
                        verifier_api_key=v_key, verifier_api_base=v_base,
                        reference_examples=reference_examples or None,
                    )
                    continue
                break  # pass or no retries left

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(TestResult).where(TestResult.id == result_row.id)
                        .values(
                            status=case_result.status,
                            reason=case_result.reason,
                            steps=case_result.steps,
                            screenshot_b64=case_result.screenshot_b64,
                            log=case_result.log,
                            finished_at=datetime.utcnow(),
                            action_history_json=json.dumps(case_result.action_history),
                            total_tokens=case_result.total_tokens,
                        )
                    )
                    # Persist per-step replay data
                    for sl in case_result.step_logs:
                        session.add(TestStepLog(
                            result_id=result_row.id,
                            step=sl.step,
                            thought=sl.thought,
                            action=sl.action,
                            action_result=sl.action_result,
                            screenshot_b64=sl.screenshot_b64,
                            prompt_tokens=sl.prompt_tokens,
                            completion_tokens=sl.completion_tokens,
                            total_tokens=sl.total_tokens,
                            perception_ms=sl.perception_ms,
                            llm_ms=sl.llm_ms,
                            action_ms=sl.action_ms,
                            subgoal_index=sl.subgoal_index,
                            subgoal_desc=sl.subgoal_desc or "",
                        ))
                    await session.commit()

            # Extract lessons from mistakes (async, best-effort)
            if result_row and case_result.steps >= 3:
                try:
                    from core.lesson_extractor import extract_and_store_lessons
                    n_lessons = await extract_and_store_lessons(
                        result_id=result_row.id,
                        run_id=run_id,
                        case_id=case_row.id,
                        suite_id=case_row.suite_id,
                        task_keyword=_task_kw,
                        provider=provider,
                        model=model,
                        api_key=api_key,
                        api_base=api_base,
                    )
                    if n_lessons:
                        await emit(f"  📖 Extracted {n_lessons} lesson(s) for future runs")
                except Exception as le_err:
                    logger.debug("Lesson extraction skipped: %s", le_err)

            status_icon = {"pass": "✅", "fail": "❌", "error": "💥", "skip": "⏭"}.get(
                case_result.status, "?"
            )
            await emit(f"  {status_icon} {case_result.status}: {case_result.reason}")

            if case_result.status == "pass":
                passed += 1
            elif case_result.status == "fail":
                failed += 1
            elif case_result.status == "skip":
                skipped += 1
            else:
                errored += 1

        # Finalize
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TestRun).where(TestRun.id == run_id)
                .values(status="done", finished_at=datetime.utcnow())
            )
            await session.commit()

        await emit(
            f"\nRun complete: {passed} passed, {failed} failed, "
            f"{errored} error(s), {skipped} skipped"
        )

        # Send webhook notification
        try:
            from core.webhook import send_run_notification
            suite_name = ""
            async with AsyncSessionLocal() as session:
                from db.models import TestSuite
                run_row2 = await session.get(TestRun, run_id)
                if run_row2:
                    suite_obj = await session.get(TestSuite, run_row2.suite_id)
                    suite_name = suite_obj.name if suite_obj else ""
            await send_run_notification(
                run_id=run_id, suite_name=suite_name,
                passed=passed, failed=failed, errored=errored,
                total=len(cases), provider=provider, model=model,
            )
        except Exception as wh_err:
            logger.warning("Webhook notification failed: %s", wh_err)

    except asyncio.CancelledError:
        await emit("⛔ Run cancelled by user")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TestRun).where(TestRun.id == run_id)
                .values(status="cancelled", finished_at=datetime.utcnow())
            )
            await session.commit()

    finally:
        await state.finish()
        active_runs.pop(run_id, None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_api_key(provider: str) -> str:
    """Read API key from settings.json for the given provider."""
    import json
    from pathlib import Path

    settings_path = Path(__file__).parent.parent / "data" / "settings.json"
    if not settings_path.exists():
        return ""
    try:
        data = json.loads(settings_path.read_text())
        key_map = {
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
            "google": "gemini_api_key",
            "gemini": "gemini_api_key",
            "zhipuai": "zhipu_api_key",
            "zhipu": "zhipu_api_key",
            "groq": "groq_api_key",
            "ollama": "",  # no key needed
        }
        field = key_map.get(provider.lower(), f"{provider.lower()}_api_key")
        return data.get(field, "")
    except Exception:
        return ""


def _load_api_base(provider: str) -> str:
    """Read provider-specific base URL from settings.json."""
    import json
    from pathlib import Path

    settings_path = Path(__file__).parent.parent / "data" / "settings.json"
    if not settings_path.exists():
        return ""
    try:
        data = json.loads(settings_path.read_text())
        base_map = {
            "anthropic": data.get("anthropic_base_url", ""),
            "ollama": data.get("ollama_base_url", "http://localhost:11434"),
        }
        return base_map.get(provider.lower(), "")
    except Exception:
        return ""


def _load_verifier_settings() -> tuple:
    """Return (verifier_provider, verifier_model, verifier_api_key, verifier_api_base).

    Empty strings mean "use the same model as the agent".
    """
    import json
    from pathlib import Path

    settings_path = Path(__file__).parent.parent / "data" / "settings.json"
    if not settings_path.exists():
        return "", "", "", ""
    try:
        data = json.loads(settings_path.read_text())
        v_provider = data.get("verifier_provider", "")
        v_model = data.get("verifier_model", "")
        v_key = _load_api_key(v_provider) if v_provider else ""
        v_base = _load_api_base(v_provider) if v_provider else ""
        return v_provider, v_model, v_key, v_base
    except Exception:
        return "", "", "", ""
