"""
CLI runner for Smart-Androidbot — trigger test runs from the command line.

Usage:
    python cli.py run --suite <id> --device <id> [--provider openai --model gpt-4o --max-steps 20]
    python cli.py run --suite <id> --device <id> --json   # machine-readable output for CI

Exit codes:
    0 = all tests passed
    1 = one or more tests failed/errored
    2 = run could not start (bad arguments, device offline, etc.)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from sqlalchemy import select, update

from core.test_agent import CaseResult, TestCaseAgent
from core.test_parser import TestCaseData
from db.database import AsyncSessionLocal, init_db
from db.models import TestCase, TestResult, TestRun, TestStepLog, TestSuite
from ws.portal_ws import connected_devices


def _load_api_key(provider: str) -> str:
    from pathlib import Path
    settings_path = Path(__file__).parent / "data" / "settings.json"
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
        }
        field = key_map.get(provider.lower(), f"{provider.lower()}_api_key")
        return data.get(field, "")
    except Exception:
        return ""


def _load_api_base(provider: str) -> str:
    from pathlib import Path
    settings_path = Path(__file__).parent / "data" / "settings.json"
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
    from pathlib import Path
    settings_path = Path(__file__).parent / "data" / "settings.json"
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


async def run_command(args: argparse.Namespace) -> int:
    """Execute a test suite and return exit code (0=pass, 1=fail, 2=error)."""
    await init_db()

    suite_id = args.suite
    device_id = args.device
    provider = args.provider
    model = args.model
    max_steps = args.max_steps
    output_json = args.json

    # Validate suite
    async with AsyncSessionLocal() as session:
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            print(f"ERROR: Suite '{suite_id}' not found", file=sys.stderr)
            return 2

        cases_res = await session.execute(
            select(TestCase).where(TestCase.suite_id == suite_id).order_by(TestCase.order)
        )
        cases = cases_res.scalars().all()
        if not cases:
            print(f"ERROR: Suite '{suite_id}' has no test cases", file=sys.stderr)
            return 2

    # Validate device
    from agent.ws_device import WebSocketDevice
    conn = connected_devices.get(device_id)
    if conn is None or not conn.is_connected:
        print(f"ERROR: Device '{device_id}' is not connected", file=sys.stderr)
        return 2

    device = WebSocketDevice(conn)
    api_key = _load_api_key(provider)
    api_base = _load_api_base(provider)
    v_provider, v_model, v_key, v_base = _load_verifier_settings()

    if not output_json:
        print(f"Suite: {suite.name} ({len(cases)} cases)")
        print(f"Device: {device_id}")
        print(f"Model: {provider}/{model}")
        print(f"Max steps: {max_steps}")
        print("=" * 60)

    # Create run record
    async with AsyncSessionLocal() as session:
        run = TestRun(
            suite_id=suite_id,
            device_id=device_id,
            provider=provider,
            model=model,
            status="running",
        )
        session.add(run)
        await session.flush()
        for case in cases:
            session.add(TestResult(run_id=run.id, case_id=case.id, status="pending"))
        await session.commit()
        run_id = run.id

    results_summary = []
    passed = failed = errored = 0

    for idx, case_row in enumerate(cases):
        case_data = TestCaseData(path=case_row.path, expected=case_row.expected)

        if not output_json:
            print(f"\n[{idx+1}/{len(cases)}] {case_data.path}")
            print(f"  Expected: {case_data.expected}")

        async def log_cb(msg: str) -> None:
            if not output_json:
                print(f"  {msg}")

        agent = TestCaseAgent(
            device=device,
            provider=provider,
            model=model,
            api_key=api_key,
            api_base=api_base,
            max_steps=max_steps,
            step_delay=1.0,
            log_callback=log_cb,
            verifier_provider=v_provider,
            verifier_model=v_model,
            verifier_api_key=v_key,
            verifier_api_base=v_base,
        )

        try:
            case_result: CaseResult = await agent.run(case_data)
        except Exception as e:
            case_result = CaseResult(status="error", reason=str(e), steps=0)

        # Persist result
        async with AsyncSessionLocal() as session:
            res_q = await session.execute(
                select(TestResult).where(
                    TestResult.run_id == run_id,
                    TestResult.case_id == case_row.id,
                )
            )
            result_row = res_q.scalar_one_or_none()
            if result_row:
                result_row.status = case_result.status
                result_row.reason = case_result.reason
                result_row.steps = case_result.steps
                result_row.screenshot_b64 = case_result.screenshot_b64
                result_row.log = case_result.log
                result_row.finished_at = datetime.utcnow()
                result_row.action_history_json = json.dumps(case_result.action_history)
                for sl in case_result.step_logs:
                    session.add(TestStepLog(
                        result_id=result_row.id,
                        step=sl.step,
                        thought=sl.thought,
                        action=sl.action,
                        action_result=sl.action_result,
                        screenshot_b64=sl.screenshot_b64,
                    ))
                await session.commit()

        icon = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skip": "SKIP"}.get(case_result.status, "?")
        if not output_json:
            print(f"  -> {icon}: {case_result.reason}")

        results_summary.append({
            "path": case_data.path,
            "expected": case_data.expected,
            "status": case_result.status,
            "reason": case_result.reason,
            "steps": case_result.steps,
        })

        if case_result.status == "pass":
            passed += 1
        elif case_result.status == "fail":
            failed += 1
        else:
            errored += 1

    # Finalize run
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(TestRun).where(TestRun.id == run_id)
            .values(status="done", finished_at=datetime.utcnow())
        )
        await session.commit()

    if output_json:
        print(json.dumps({
            "run_id": run_id,
            "suite": suite.name,
            "total": len(cases),
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "success": failed == 0 and errored == 0,
            "results": results_summary,
        }, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"Results: {passed} passed, {failed} failed, {errored} errored / {len(cases)} total")
        if failed == 0 and errored == 0:
            print("ALL TESTS PASSED")
        else:
            print("SOME TESTS FAILED")

    return 0 if (failed == 0 and errored == 0) else 1


def main():
    parser = argparse.ArgumentParser(
        prog="smart-androidbot",
        description="CLI for Smart-Androidbot test runner",
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run a test suite")
    run_parser.add_argument("--suite", required=True, help="Suite ID")
    run_parser.add_argument("--device", required=True, help="Device ID")
    run_parser.add_argument("--provider", default="openai", help="LLM provider (default: openai)")
    run_parser.add_argument("--model", default="gpt-4o", help="Model name (default: gpt-4o)")
    run_parser.add_argument("--max-steps", type=int, default=20, help="Max steps per case (default: 20)")
    run_parser.add_argument("--json", action="store_true", help="Output JSON (for CI/CD)")

    args = parser.parse_args()

    if args.command == "run":
        exit_code = asyncio.run(run_command(args))
        sys.exit(exit_code)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
