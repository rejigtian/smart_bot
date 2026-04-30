"""
TestCaseAgent — orchestration layer.

Wires the five agent layers together for a single test-case run:
  Perception  → agent.perception   (a11y tree → LLM text)
  Decision    → agent.prompt       (SYSTEM_PROMPT)
               agent.tools         (TOOLS)
               litellm             (LLM call)
  Action      → agent.base         (DeviceDriver protocol)
  Memory      → agent.memory       (AgentMemory)
  Verification→ agent.verifier     (LLMVerifier)

This file contains only the agent loop — no formatting, no prompt text,
no LLM provider logic, no memory management.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import litellm

from agent.base import DeviceDriver, build_model_kwargs
from agent.memory import AgentMemory
from agent.perception import detect_elements_vlm
from agent.planner import generate_plan, generate_subgoals
from agent.prompt import SYSTEM_PROMPT
from agent.tools import TOOLS
from agent.verifier import LLMVerifier
from core.test_parser import TestCaseData

logger = logging.getLogger(__name__)

def _resize_screenshot(img_bytes: bytes) -> tuple[bytes, int, int]:
    """Downscale a screenshot to 50% of its original size.

    Returns (resized_bytes, new_width, new_height).
    Halving preserves the exact device aspect ratio.  The AI outputs pixel
    coordinates in this half-size image; the server multiplies by 2 to get
    device pixel coordinates — no normalization math involved.

    Falls back to (original_bytes, 0, 0) if Pillow is unavailable.
    """
    try:
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        new_w, new_h = w // 2, h // 2
        img = img.resize((new_w, new_h), Image.LANCZOS)
        out = io.BytesIO()
        # JPEG at quality 65 is ~5-10x smaller than PNG with minimal visual loss
        img.convert("RGB").save(out, format="JPEG", quality=65, optimize=True)
        resized = out.getvalue()
        logger.debug("Screenshot resized %dx%d → %dx%d (%d→%d bytes, JPEG)",
                     w, h, new_w, new_h, len(img_bytes), len(resized))
        return resized, new_w, new_h
    except Exception as exc:
        logger.warning("Screenshot resize failed (%s) — sending original", exc)
        return img_bytes, 0, 0


def _annotate_screenshot(
    img_bytes: bytes,
    elements: list,
    screen_width: int,
    screen_height: int,
) -> bytes:
    """Annotate the screenshot with two layers for coordinate accuracy:

    1. Coordinate grid (always) — faint lines every 200 normalized units with
       axis labels, so the LLM can read off tap(x,y) values directly from the
       image instead of estimating by eye.  Crucial for canvas-rendered screens
       where the a11y tree is empty.

    2. Set-of-Marks dots (when a11y elements exist) — a blue numbered dot at
       each element's center so the LLM maps visual position → tap_element(index)
       instead of guessing coordinates.

    Returns the annotated PNG bytes, or the original bytes if Pillow is absent.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        iw, ih = img.size

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # ── Try platform fonts (small) ────────────────────────────────────────
        small_font = None
        dot_font = None
        for fp in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSDisplay.ttf",
        ]:
            try:
                small_font = ImageFont.truetype(fp, size=max(10, iw // 80))
                dot_font   = ImageFont.truetype(fp, size=max(10, iw // 58))
                break
            except Exception:
                pass
        if small_font is None:
            try:
                small_font = ImageFont.load_default(size=max(10, iw // 80))
                dot_font   = ImageFont.load_default(size=max(10, iw // 58))
            except TypeError:
                small_font = dot_font = ImageFont.load_default()

        # ── Layer 1: coordinate grid (lines at 20%/40%/60%/80%, labeled in px) ──
        # Labels show the actual pixel coordinate in this half-size image so the
        # AI can read off coordinates directly and pass them to tap(x, y).
        for frac in (0.2, 0.4, 0.6, 0.8):
            px = int(frac * iw)
            label = str(px)
            draw.line([(px, 0), (px, ih)], fill=(200, 200, 200, 60), width=1)
            bb = draw.textbbox((0, 0), label, font=small_font)
            tw = bb[2] - bb[0]
            draw.text((px - tw // 2, 3), label, fill=(180, 180, 180, 180), font=small_font)
            draw.text((px - tw // 2, ih - (bb[3] - bb[1]) - 3), label,
                      fill=(180, 180, 180, 180), font=small_font)
        for frac in (0.2, 0.4, 0.6, 0.8):
            py = int(frac * ih)
            label = str(py)
            draw.line([(0, py), (iw, py)], fill=(200, 200, 200, 60), width=1)
            bb = draw.textbbox((0, 0), label, font=small_font)
            th = bb[3] - bb[1]
            draw.text((3, py - th // 2), label, fill=(180, 180, 180, 180), font=small_font)
            draw.text((iw - (bb[2] - bb[0]) - 3, py - th // 2), label,
                      fill=(180, 180, 180, 180), font=small_font)

        # ── Layer 2: SoM crosshair markers ────────────────────────────────────
        # Bright magenta CROSSHAIRS (+) — distinctive overlay style, NEVER
        # confused with in-game elements (games don't use bright magenta
        # crosshairs as collectibles). Number labeled next to the crosshair,
        # not inside a filled shape.
        if elements and screen_width and screen_height:
            sx = iw / screen_width
            sy = ih / screen_height
            n = len(elements)
            # Crosshair arm length — smaller when crowded
            arm = max(4, min(8, iw // 90)) if n <= 20 else max(3, iw // 120)
            stroke = (255, 0, 180, 230)       # bright magenta, high opacity
            stroke_outline = (255, 255, 255, 220)  # white halo for contrast

            for el in elements:
                cx, cy = el.get("cx", 0), el.get("cy", 0)
                if not cx and not cy:
                    continue
                ix = int(cx * sx)
                iy = int(cy * sy)
                label = str(el["index"])

                # White halo for contrast on dark/bright backgrounds
                draw.line([(ix - arm - 1, iy), (ix + arm + 1, iy)], fill=stroke_outline, width=3)
                draw.line([(ix, iy - arm - 1), (ix, iy + arm + 1)], fill=stroke_outline, width=3)
                # Magenta crosshair
                draw.line([(ix - arm, iy), (ix + arm, iy)], fill=stroke, width=1)
                draw.line([(ix, iy - arm), (ix, iy + arm)], fill=stroke, width=1)

                # Label OUTSIDE the crosshair — white text with black outline
                # so it's readable on any background
                bb = draw.textbbox((0, 0), label, font=small_font)
                lw, lh = bb[2] - bb[0], bb[3] - bb[1]
                lx, ly = ix + arm + 2, iy - lh - 1
                # Black outline
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    draw.text((lx + dx, ly + dy), label, fill=(0, 0, 0, 200), font=small_font)
                # White text
                draw.text((lx, ly), label, fill=(255, 255, 255, 255), font=small_font)

        annotated = Image.alpha_composite(img, overlay).convert("RGB")
        out = io.BytesIO()
        annotated.save(out, format="JPEG", quality=70, optimize=True)
        annotated_bytes = out.getvalue()
        logger.debug(
            "Screenshot annotated: grid + %d SoM dots on %dx%d image",
            len(elements), iw, ih,
        )
        return annotated_bytes
    except Exception as exc:
        logger.warning("Screenshot annotation failed (%s) — sending original", exc)
        return img_bytes


# ── A11y tree quality check ──────────────────────────────────────────────────

# Tree is "sufficient" for text-only mode when element count is in the sweet spot:
# - Too few (< 3): empty/weak tree (Canvas/H5/game), need visual
# - Sweet spot (3-20): clean standard UI, text is enough
# - Too many (> 20): complex page with many overlays/lists, visual confirmation helps avoid ambiguity
_MIN_TAPPABLE_ELEMENTS = 3
_MAX_ELEMENTS_FOR_TEXT_ONLY = 20
_STEPS_BETWEEN_SCREENSHOTS = 3  # force a screenshot every N text-only steps for safety

def _is_tree_sufficient(elements: list) -> bool:
    """Check if the a11y tree is in the sweet spot for text-only decisions.

    Returns False (= need screenshot) when:
      - Too few elements (empty tree, Canvas/game/H5)
      - Too many elements (complex page with dialogs/lists stacked — visual disambiguates)
    """
    n = len(elements)
    if n < _MIN_TAPPABLE_ELEMENTS:
        return False
    if n > _MAX_ELEMENTS_FOR_TEXT_ONLY:
        return False
    return True


# Actions that need extra time for the screen to settle.
# tap_element/tap include a 0.35s transient capture inside the dispatch loop,
# so post-action delay only covers the remaining settle time (~0.9s).
_POST_ACTION_DELAYS: dict = {
    "start_app": 2.5,
    "global_action": 1.5,
    "tap_element": 0.9,   # total settle ≈ 0.35 (transient) + 0.9 = 1.25s
    "tap": 0.9,
}
# Actions that are nearly instantaneous (no screen change expected)
_FAST_ACTIONS = frozenset({"input_text", "press_key", "scroll", "swipe", "wait"})


@dataclass
class StepLog:
    step: int
    thought: str
    action: str          # e.g. "tap_element({'index': 7})"
    action_result: str   # e.g. "Tapped element 7 at (550,200)"
    screenshot_b64: str  # annotated screenshot shown to the LLM this step
    # Token usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Timing (milliseconds)
    perception_ms: int = 0
    llm_ms: int = 0
    action_ms: int = 0
    # Subgoal context (set by subagent orchestrator, None for single-agent runs)
    subgoal_index: Optional[int] = None
    subgoal_desc: str = ""


@dataclass
class CaseResult:
    status: str           # pass / fail / error / skip
    reason: str
    steps: int
    screenshot_b64: str = ""
    log: str = ""
    action_history: list = field(default_factory=list)  # structured records for DB persistence
    step_logs: list = field(default_factory=list)        # list[StepLog] for replay
    total_tokens: int = 0


class TestCaseAgent:
    def __init__(
        self,
        device: DeviceDriver,
        provider: str,
        model: str,
        api_key: str = "",
        api_base: str = "",
        max_steps: int = 20,
        step_delay: float = 1.0,
        log_callback=None,  # async callable(str)
        verifier_provider: str = "",
        verifier_model: str = "",
        verifier_api_key: str = "",
        verifier_api_base: str = "",
        reference_examples: Optional[list] = None,  # starred action records from past runs
        lessons_learned: Optional[list] = None,  # negative experiences from past runs
    ):
        self.device = device
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_steps = max_steps
        self.step_delay = step_delay
        self.log_callback = log_callback
        # Verifier uses its own model if configured, otherwise falls back to the agent model.
        v_provider = verifier_provider or provider
        v_model = verifier_model or model
        v_key = verifier_api_key or api_key
        v_base = verifier_api_base or api_base
        self._verifier = LLMVerifier(v_provider, v_model, v_key, v_base)
        self._reference_examples: list = reference_examples or []
        self._lessons_learned: list = lessons_learned or []

    async def _log(self, msg: str) -> None:
        logger.info(msg)
        if self.log_callback:
            await self.log_callback(msg)

    async def _summarize(self, text: str) -> str:
        """Compress a block of old conversation messages into a brief summary.

        Uses the same LLM provider but with a lightweight prompt.
        """
        model_str, extra = build_model_kwargs(self.provider, self.model, self.api_base)
        kwargs: dict[str, Any] = {
            "model": model_str,
            "messages": [
                {"role": "system", "content": (
                    "Summarize the following Android test agent interaction into a brief "
                    "paragraph (3-5 sentences). Focus on: which screens were visited, "
                    "which actions succeeded or failed, and what the agent discovered. "
                    "Be concise and factual."
                )},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
            **extra,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=30.0)
        return (response.choices[0].message.content or "").strip()

    def _build_llm_kwargs(self, messages: list) -> dict[str, Any]:
        model_str, extra = build_model_kwargs(self.provider, self.model, self.api_base)
        kwargs: dict[str, Any] = {
            "model": model_str,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            **extra,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return kwargs

    async def _dispatch(self, fn_name: str, args: dict) -> str:
        """Execute a tool call on the device. Does not handle mark_done."""
        if fn_name == "tap_element":
            return await self.device.tap_element(args["index"])
        if fn_name == "tap":
            return await self.device.tap(args["x"], args["y"])
        if fn_name == "swipe":
            return await self.device.swipe(
                args["x1"], args["y1"], args["x2"], args["y2"],
                args.get("duration_ms", 500),
            )
        if fn_name == "input_text":
            if not self.device.keyboard_visible:
                return (
                    "ERROR: Keyboard not visible — the text input is not focused. "
                    "Call tap_element() on the text field first, then call input_text()."
                )
            return await self.device.input_text(args["text"], args.get("clear", False))
        if fn_name == "press_key":
            return await self.device.press_key(args["key"])
        if fn_name == "global_action":
            return await self.device.global_action(args["action"])
        if fn_name == "scroll":
            return await self.device.scroll(args["direction"], args.get("distance", "medium"))
        if fn_name == "start_app":
            return await self.device.start_app(args["package"], args.get("activity", ""))
        if fn_name == "list_packages":
            packages = await self.device.list_packages()
            if not packages:
                return "No packages found (device may not support this query)"
            return "Installed packages:\n" + "\n".join(sorted(packages))
        if fn_name == "wait":
            secs = min(float(args.get("seconds", 2)), 10)
            await asyncio.sleep(secs)
            return f"Waited {secs:.1f}s"
        return f"Unknown tool: {fn_name}"

    async def run(self, case: TestCaseData) -> CaseResult:
        goal = (
            f"Test context: {case.path}\n"
            f"Expected result to verify: {case.expected}"
        )
        ref_message = ""
        if self._reference_examples:
            ref_lines = [
                f"[Reference: a previous successful run took {len(self._reference_examples)} steps]"
            ]
            for r in self._reference_examples[:8]:  # cap at 8 steps
                thought = r.get("thought", "")
                thought_part = f' 💭 "{thought}"' if thought else ""
                ref_lines.append(
                    f"  Step {r['step']}:{thought_part} → {r['fn_name']}({r['args']}) → {r['result']}"
                )
            if len(self._reference_examples) > 8:
                ref_lines.append(f"  … ({len(self._reference_examples) - 8} more steps omitted)")
            ref_lines.append(
                "Use this as a soft guide — look for similar elements by text/id, "
                "but adapt to the actual current UI. Do NOT copy indices blindly."
            )
            ref_message = "\n".join(ref_lines)

        # Build lessons message from past negative experiences
        lessons_message = ""
        if self._lessons_learned:
            lessons_lines = [
                "[Lessons from past runs — AVOID these mistakes]",
            ]
            for i, lesson in enumerate(self._lessons_learned[:5], 1):
                lessons_lines.append(f"  {i}. {lesson}")
            lessons_message = "\n".join(lessons_lines)

        # ── Test KB lookup — inject feature-specific knowledge ──────────
        kb_message = ""
        try:
            from agent.test_kb import search_feature
            kb_content = search_feature(f"{case.path} {case.expected}")
            if kb_content:
                kb_message = (
                    "[Test Knowledge — feature-specific reference]\n\n"
                    "This is domain knowledge about the feature you're testing. "
                    "Use it to understand entry paths, key elements, expected UI, "
                    "and known pitfalls. Adapt to the actual UI state.\n\n"
                    + kb_content
                )
        except Exception as e:
            pass  # KB not available — proceed without

        await self._log(f"▶ Starting: {case.path} | expected: {case.expected}")
        if kb_message:
            await self._log(f"  📚 Test KB loaded ({len(kb_message)} chars)")

        # ── Subagent routing (Phase 2) ───────────────────────────────
        # Complex tasks are decomposed into SubGoals, each executed by an
        # isolated sub-agent with its own AgentMemory (Hermes-style).
        try:
            subgoals = await generate_subgoals(
                case.path, case.expected,
                self.provider, self.model,
                self.api_key, self.api_base,
            )
            if subgoals and len(subgoals) >= 2:
                from agent.subagent import run_with_subagents
                return await run_with_subagents(self, case, subgoals)
        except Exception as e:
            await self._log(f"  Subagent routing skipped: {e}")

        # ── Planner (Phase 1 fallback for moderately complex tasks) ──
        plan_message = ""
        try:
            plan_text = await generate_plan(
                case.path, case.expected,
                self.provider, self.model,
                self.api_key, self.api_base,
            )
            if plan_text:
                await self._log(f"  📋 Plan generated:\n{plan_text}")
                plan_message = f"[Execution Plan]\n{plan_text}\n\nFollow this plan step by step. Adapt to the actual UI — the plan describes WHAT to do, not exact element indices."
        except Exception as e:
            await self._log(f"  Planner skipped: {e}")

        init_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": goal},
        ]
        if kb_message:
            init_messages.append({"role": "user", "content": kb_message})
        if ref_message:
            init_messages.append({"role": "user", "content": ref_message})
        if lessons_message:
            init_messages.append({"role": "user", "content": lessons_message})
        if plan_message:
            init_messages.append({"role": "user", "content": plan_message})
        memory = AgentMemory(messages=init_messages, pinned_count=len(init_messages))
        steps = 0
        verify_fail_count = 0  # circuit breaker — auto-fail after N verify rejections
        _MAX_VERIFY_FAILS = 3
        last_screenshot_b64 = ""
        llm_img_b64 = ""
        log_lines: list[str] = [goal]
        step_logs_list: list[StepLog] = []
        _step_screenshot_b64 = ""   # screenshot sent to LLM this step
        _step_thought = ""          # LLM reasoning text this step
        _step_tokens = (0, 0, 0)    # (prompt, completion, total) per step
        _step_perception_ms = 0
        _step_llm_ms = 0
        _step_action_ms = 0
        _case_total_tokens = 0
        _steps_without_screenshot = 0  # force screenshot every N text-only steps
        _force_screenshot_next = False # set by request_screenshot tool
        _screenshot_requests_used = 0  # rate limit: max 3 per case
        _MAX_SCREENSHOT_REQUESTS = 3
        _last_action_screenshot_b64 = ""  # transient screenshot after most recent tap (has toast/animation)

        try:
            while steps < self.max_steps:
                await self._log(f"── Step {steps + 1}/{self.max_steps} ──────────────────────")
                log_lines.append(f"[Step {steps + 1}]")

                # ── Perception ────────────────────────────────────────────────
                _t0 = time.monotonic()
                img_bytes = None
                last_err = None
                for attempt in range(3):  # 3 tries: initial + 2 retries
                    try:
                        img_bytes = await self.device.screenshot()
                        break
                    except Exception as e:
                        last_err = e
                        if attempt < 2:
                            await self._log(f"Screenshot failed ({e}) — retry {attempt + 1}/2 after 2s")
                            await asyncio.sleep(2.0)
                        else:
                            await self._log(f"Screenshot failed after 3 attempts: {e}")
                if img_bytes is None:
                    return CaseResult(
                        status="error", reason=f"Screenshot failed: {last_err}",
                        steps=steps, log="\n".join(log_lines),
                        step_logs=step_logs_list,
                    )
                last_screenshot_b64 = base64.b64encode(img_bytes).decode()
                resized_bytes, img_w, img_h = _resize_screenshot(img_bytes)

                ui_text = ""
                _ui_elements: list = []
                try:
                    ui_text, _ui_elements = await self.device.get_ui_state()
                    if "ProgressBar" in ui_text:
                        await self._log("  ⏳ Loading detected — waiting 2s…")
                        await asyncio.sleep(2.0)
                        try:
                            ui_text, _ui_elements = await self.device.get_ui_state()
                        except Exception:
                            pass
                except Exception as e:
                    await self._log(f"UI state fetch failed (screenshot only): {e}")

                # VLM fallback: detect elements visually when a11y tree is empty
                if not _ui_elements:
                    try:
                        resized_b64 = base64.b64encode(resized_bytes).decode()
                        vlm_text, vlm_elements = await detect_elements_vlm(
                            resized_b64, img_w, img_h,
                            self.provider, self.model,
                            self.api_key, self.api_base,
                        )
                        if vlm_elements:
                            _ui_elements = vlm_elements
                            # Prepend device state if we have it, append VLM elements
                            if ui_text:
                                ui_text = ui_text.split("[UI Elements]")[0] + vlm_text
                            else:
                                ui_text = vlm_text
                            await self._log(f"  👁 VLM detected {len(vlm_elements)} elements (no a11y tree)")
                    except Exception as vlm_e:
                        await self._log(f"  VLM fallback failed: {vlm_e}")

                # Annotate screenshot only when we'll send it to the LLM.
                # For text-only steps, save the raw resized screenshot for replay only.
                tree_ok_pre = _is_tree_sufficient(_ui_elements)
                will_send_image = (
                    not tree_ok_pre
                    or steps == 0
                    or _steps_without_screenshot >= _STEPS_BETWEEN_SCREENSHOTS
                    or _force_screenshot_next
                )
                if will_send_image:
                    annotated = _annotate_screenshot(
                        resized_bytes, _ui_elements,
                        self.device.screen_width, self.device.screen_height,
                    )
                    llm_img_b64 = base64.b64encode(annotated).decode()
                else:
                    llm_img_b64 = base64.b64encode(resized_bytes).decode()
                _step_screenshot_b64 = llm_img_b64  # save for StepLog (always)
                _step_perception_ms = int((time.monotonic() - _t0) * 1000)

                # Log first meaningful line of UI state (app/screen info)
                # and record current Activity for page-stuck detection.
                current_activity = ""
                if ui_text:
                    ui_summary = ""
                    for ln in ui_text.splitlines():
                        ln_stripped = ln.strip()
                        if not ln_stripped or ln_stripped == "[Device State]":
                            continue
                        if not ui_summary:
                            ui_summary = ln_stripped
                        if ln_stripped.startswith("Page:"):
                            # e.g. "Page: CreateRoomActivity (full: com.example.xxx)"
                            import re as _re
                            m = _re.match(r"Page:\s*(\S+)", ln_stripped)
                            if m:
                                current_activity = m.group(1)
                    if ui_summary:
                        await self._log(f"  📱 {ui_summary}")

                # ── Memory ────────────────────────────────────────────────────
                memory.drop_old_images()
                memory.record_activity(current_activity)

                # Inject "Recent pages" trail into [Device State] — helps agent
                # understand navigation history without needing real Activity stack.
                if len(memory._activity_history) >= 2 and ui_text:
                    recent = memory._activity_history[-5:]
                    # Deduplicate consecutive duplicates
                    trail = []
                    for a in recent:
                        short = a.rsplit(".", 1)[-1]
                        if not trail or trail[-1] != short:
                            trail.append(short)
                    if len(trail) >= 2:
                        trail_line = f"  Recent pages: {' → '.join(trail)}"
                        # Insert right after "Page:" line if present, else after "App:"
                        lines = ui_text.split("\n")
                        inserted = False
                        for i, ln in enumerate(lines):
                            if ln.strip().startswith("Page:"):
                                lines.insert(i + 1, trail_line)
                                inserted = True
                                break
                        if not inserted:
                            for i, ln in enumerate(lines):
                                if ln.strip().startswith("App:"):
                                    lines.insert(i + 1, trail_line)
                                    inserted = True
                                    break
                        if inserted:
                            ui_text = "\n".join(lines)

                # Decide whether to include screenshot or go text-only.
                # Rich a11y tree → text-only (save ~60% tokens, 2-3x faster).
                # Weak/empty tree, first step, or periodic check → include image.
                tree_ok = _is_tree_sufficient(_ui_elements)
                _was_agent_requested = _force_screenshot_next
                need_screenshot = (
                    not tree_ok                                          # weak tree (game/H5/Canvas)
                    or steps == 0                                        # first step always needs visual
                    or _steps_without_screenshot >= _STEPS_BETWEEN_SCREENSHOTS  # periodic visual check
                    or _force_screenshot_next                            # agent requested via request_screenshot tool
                )
                # Consume the forced-screenshot flag after use
                if _force_screenshot_next and need_screenshot:
                    _force_screenshot_next = False

                if need_screenshot:
                    step_text = memory.build_step_text(steps, ui_text, img_w, img_h)
                    memory.messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": step_text},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/jpeg;base64,{llm_img_b64}"
                            }},
                        ],
                    })
                    _steps_without_screenshot = 0
                    if _was_agent_requested:
                        await self._log("  📷 Screenshot included (agent requested)")
                    elif steps > 0 and tree_ok:
                        await self._log("  📷 Periodic screenshot check")
                    elif not tree_ok:
                        n = len(_ui_elements)
                        if n < _MIN_TAPPABLE_ELEMENTS:
                            await self._log(f"  📷 Screenshot included (weak a11y tree, {n} elements)")
                        else:
                            await self._log(f"  📷 Screenshot included (complex page, {n} elements)")
                else:
                    # Text-only: no image, much faster and cheaper
                    step_text = memory.build_step_text(steps, ui_text, 0, 0)  # no img dimensions
                    memory.messages.append({
                        "role": "user",
                        "content": step_text,
                    })
                    _steps_without_screenshot += 1
                    await self._log(f"  📝 Text-only step ({len(_ui_elements)} elements, skip screenshot)")

                await memory.compress(self._summarize)

                # ── Auto-recovery (when stuck at level 2+) ───────────────────
                if memory.recovery_level >= 4:
                    await self._log("  🛑 Auto-recovery: forcing fail after prolonged stuck")
                    return CaseResult(
                        status="fail",
                        reason="Agent stuck in loop — auto-terminated",
                        steps=steps + 1,
                        screenshot_b64=last_screenshot_b64,
                        log="\n".join(log_lines),
                        action_history=list(memory.action_records),
                        step_logs=step_logs_list,
                    )
                if memory.recovery_level == 3:
                    await self._log("  🔄 Auto-recovery (L3): restarting app")
                    log_lines.append(f"[{steps}] AUTO_RECOVERY: start_app")
                    # Try to extract package name from notes or action history
                    pkg = memory.notes.get("target_app", "")
                    if pkg:
                        try:
                            await self.device.start_app(pkg)
                            await asyncio.sleep(2.5)
                        except Exception:
                            pass
                elif memory.recovery_level == 2:
                    await self._log("  🔄 Auto-recovery (L2): going back")
                    log_lines.append(f"[{steps}] AUTO_RECOVERY: global_action(back)")
                    try:
                        await self.device.global_action("back")
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass

                # ── Decision (LLM) ────────────────────────────────────────────
                _t1 = time.monotonic()
                response = None
                for _attempt in range(2):  # 1 retry on timeout
                    try:
                        kwargs = self._build_llm_kwargs(memory.messages)
                        response = await asyncio.wait_for(
                            litellm.acompletion(**kwargs),
                            timeout=120.0,
                        )
                        break
                    except asyncio.TimeoutError:
                        if _attempt == 0:
                            await self._log("LLM call timed out — retrying…")
                            await asyncio.sleep(3)
                        else:
                            await self._log("LLM call timed out after 2 attempts")
                            return CaseResult(
                                status="error", reason="LLM call timed out",
                                steps=steps, screenshot_b64=last_screenshot_b64,
                                log="\n".join(log_lines),
                                step_logs=step_logs_list,
                            )
                    except Exception as e:
                        await self._log(f"LLM error: {e}")
                        return CaseResult(
                            status="error", reason=f"LLM error: {e}",
                            steps=steps, screenshot_b64=last_screenshot_b64,
                            log="\n".join(log_lines),
                            step_logs=step_logs_list,
                        )

                _step_llm_ms = int((time.monotonic() - _t1) * 1000)

                # Extract token usage
                usage = getattr(response, "usage", None)
                if usage:
                    _step_tokens = (
                        getattr(usage, "prompt_tokens", 0) or 0,
                        getattr(usage, "completion_tokens", 0) or 0,
                        getattr(usage, "total_tokens", 0) or 0,
                    )
                    _case_total_tokens += _step_tokens[2]
                else:
                    _step_tokens = (0, 0, 0)

                msg = response.choices[0].message
                memory.messages.append(msg.model_dump(exclude_none=True))

                # Log LLM reasoning content whenever present (helps debugging)
                msg_content = (getattr(msg, "content", "") or "").strip()
                _step_thought = msg_content  # save for StepLog
                if msg_content:
                    await self._log(f"  💭 {msg_content[:400]}")
                    log_lines.append(f"[{steps}] LLM: {msg_content[:400]}")

                tool_calls = getattr(msg, "tool_calls", None) or []
                if not tool_calls:
                    if not msg_content:
                        await self._log("No tool call, no content — nudging…")
                    else:
                        await self._log("No tool call — nudging…")
                    log_lines.append(f"[{steps}] LLM said (no action): {msg_content[:200]}")
                    # Nudge: force the LLM to call a tool on the next turn
                    memory.messages.append({
                        "role": "user",
                        "content": (
                            "You must call a tool to proceed — do NOT just describe the screen. "
                            "Review your goal and take the next concrete action using one of the "
                            "available tools. If the task is fully complete, call mark_done()."
                        ),
                    })
                    steps += 1
                    continue

                # ── Action + Verification ─────────────────────────────────────
                _t2 = time.monotonic()
                done_result: Optional[CaseResult] = None
                tool_results = []
                dispatched_names: list[str] = []

                for tc in tool_calls:
                    fn_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    await self._log(f"  → {fn_name}({args})")
                    log_lines.append(f"[{steps}] {fn_name}({args})")

                    if fn_name == "remember":
                        result_text = memory.remember(args.get("key", ""), args.get("value", ""))
                        await self._log(f"    📝 {result_text}")
                        tool_results.append({
                            "tool_call_id": tc.id,
                            "role": "tool",
                            "name": fn_name,
                            "content": result_text,
                        })
                        continue

                    if fn_name == "request_screenshot":
                        if _screenshot_requests_used >= _MAX_SCREENSHOT_REQUESTS:
                            result_text = (
                                f"Rejected: already used {_screenshot_requests_used}/"
                                f"{_MAX_SCREENSHOT_REQUESTS} screenshot requests this case. "
                                "Trust the UI Elements list."
                            )
                            await self._log(f"    🚫 {result_text}")
                        elif need_screenshot:
                            # Current step already sent a screenshot — no need to flag next one
                            result_text = (
                                "No effect: current step already included a screenshot. "
                                "You already have visual context."
                            )
                            await self._log(f"    ⚠ {result_text}")
                        else:
                            _force_screenshot_next = True
                            _screenshot_requests_used += 1
                            result_text = (
                                f"OK: screenshot will be included next step "
                                f"({_screenshot_requests_used}/{_MAX_SCREENSHOT_REQUESTS} used)."
                            )
                            await self._log(f"    📸 {result_text}")
                        tool_results.append({
                            "tool_call_id": tc.id,
                            "role": "tool",
                            "name": fn_name,
                            "content": result_text,
                        })
                        continue

                    if fn_name == "mark_done":
                        status = args["status"]
                        reason = args.get("reason", "")
                        wait_secs = min(float(args.get("wait_before_verify", 0)), 5)
                        if wait_secs > 0:
                            await self._log(f"  ⏳ Waiting {wait_secs:.1f}s for animation/toast…")
                            await asyncio.sleep(wait_secs)
                            # Re-capture screenshot after waiting (toast should be visible now)
                            try:
                                fresh_bytes = await self.device.screenshot()
                                resized_fresh, _, _ = _resize_screenshot(fresh_bytes)
                                _step_screenshot_b64 = base64.b64encode(resized_fresh).decode()
                                last_screenshot_b64 = base64.b64encode(fresh_bytes).decode()
                            except Exception:
                                pass
                        if status == "pass":
                            await self._log("  Verifying pass claim…")
                            confirmed, v_reason, v_b64, v_gap = await self._verifier.verify(
                                self.device, case.expected, self._log,
                                action_history=memory.action_history,
                                agent_reason=reason,
                                # Pass last post-action transient (closest to toast)
                                # instead of current step's (post-settle, toast gone)
                                pre_screenshot_b64=_last_action_screenshot_b64 or _step_screenshot_b64,
                            )
                            if v_b64:
                                last_screenshot_b64 = v_b64
                                # Use verifier's combined screenshot (pre+fresh) as
                                # this step's evidence in the replay — captures the
                                # post-action state which is critical for mark_done
                                # debugging.
                                _step_screenshot_b64 = v_b64
                            if confirmed:
                                await self._log(f"  ✓ Verified: {v_reason}")
                                log_lines.append(f"[{steps}] VERIFIED_PASS: {v_reason}")
                                done_result = CaseResult(
                                    status="pass",
                                    reason=f"{reason} [verified: {v_reason}]",
                                    steps=steps + 1,
                                    screenshot_b64=last_screenshot_b64,
                                    log="\n".join(log_lines),
                                    action_history=list(memory.action_records),
                                )
                                result_text = "Marked as pass (verified)"
                            else:
                                verify_fail_count += 1
                                await self._log(f"  ✗ Pass rejected — {v_reason}")
                                if v_gap:
                                    await self._log(f"  📋 Gap: {v_gap}")
                                log_lines.append(f"[{steps}] VERIFY_FAIL ({verify_fail_count}/{_MAX_VERIFY_FAILS}): {v_reason}")

                                # Circuit breaker: too many failed mark_done(pass) attempts
                                # means the agent is hallucinating or task is impossible
                                if verify_fail_count >= _MAX_VERIFY_FAILS:
                                    await self._log(
                                        f"  🛑 Circuit broken: {verify_fail_count} consecutive verify failures — "
                                        "agent appears to be hallucinating. Auto-failing."
                                    )
                                    return CaseResult(
                                        status="fail",
                                        reason=(
                                            f"Verification rejected {verify_fail_count} times in a row. "
                                            f"Last reason: {v_reason}"
                                        ),
                                        steps=steps + 1,
                                        screenshot_b64=last_screenshot_b64,
                                        log="\n".join(log_lines),
                                        action_history=list(memory.action_records),
                                        step_logs=step_logs_list,
                                    )

                                gap_hint = f" Gap: {v_gap}" if v_gap else ""
                                # Strong correction message — explicitly tell the agent
                                # not to repeat the same false claim, and consult fresh
                                # observation from THIS step's screenshot only.
                                result_text = (
                                    f"VERIFICATION REJECTED ({verify_fail_count}/{_MAX_VERIFY_FAILS}): {v_reason}.{gap_hint}\n\n"
                                    "IMPORTANT:\n"
                                    "- Do NOT repeat the same mark_done claim. Your previous reason "
                                    "did not match what's actually on screen.\n"
                                    "- Re-read the CURRENT screenshot and [UI Elements] carefully — "
                                    "ignore numbers from your past messages or memory.\n"
                                    "- Possible reasons: (a) the action had no effect (UI may be "
                                    "blocked, e.g. game debuff), (b) you misread the screen, "
                                    "(c) you need a different action.\n"
                                    "- If the expected result is genuinely not achievable, call "
                                    "mark_done(status='fail') honestly instead of repeating false claims."
                                )
                        else:
                            done_result = CaseResult(
                                status=status,
                                reason=reason,
                                steps=steps + 1,
                                screenshot_b64=last_screenshot_b64,
                                log="\n".join(log_lines),
                                action_history=list(memory.action_records),
                            )
                            result_text = f"Marked as {status}"
                    else:
                        try:
                            result_text = await self._dispatch(fn_name, args)
                        except (ValueError, KeyError) as e:
                            # Transient error (e.g. stale element index after screen
                            # transition) — re-fetch UI tree and retry once.
                            if fn_name == "tap_element":
                                await self._log(f"    ⟳ {fn_name} failed ({e}), refreshing UI and retrying…")
                                await asyncio.sleep(1.0)
                                try:
                                    ui_text, _ui_elements = await self.device.get_ui_state()
                                    result_text = await self._dispatch(fn_name, args)
                                except Exception as e2:
                                    result_text = f"ERROR (after retry): {e2}"
                                    await self._log(f"  ✗ Retry also failed: {e2}")
                                    log_lines.append(f"[{steps}] TOOL_ERROR_RETRY: {e2}")
                            else:
                                result_text = f"ERROR: {e}"
                        except Exception as e:
                            result_text = f"ERROR: {e}"
                            await self._log(f"  ✗ Tool error: {e}")
                            log_lines.append(f"[{steps}] TOOL_ERROR: {e}")

                        if not result_text.startswith("ERROR"):
                            await self._log(f"    ↳ {result_text}")
                        memory.record_action(steps, fn_name, args, result_text)
                        dispatched_names.append(fn_name)

                        # Capture one transient screenshot shortly after tap to
                        # preserve short-lived UI (toasts, floating +N, flash).
                        # Single fixed timing is more reliable than burst — the
                        # "largest sample" heuristic could pick mid-transition
                        # frames that confuse the next step's perception.
                        if fn_name in ("tap_element", "tap") and not result_text.startswith("ERROR"):
                            try:
                                await asyncio.sleep(0.35)  # toast window sweet spot
                                transient_bytes = await self.device.screenshot()
                                resized_t, _, _ = _resize_screenshot(transient_bytes)
                                if _ui_elements:
                                    annotated_t = _annotate_screenshot(
                                        resized_t, _ui_elements,
                                        self.device.screen_width, self.device.screen_height,
                                    )
                                    _step_screenshot_b64 = base64.b64encode(annotated_t).decode()
                                else:
                                    _step_screenshot_b64 = base64.b64encode(resized_t).decode()
                                last_screenshot_b64 = base64.b64encode(transient_bytes).decode()
                                _last_action_screenshot_b64 = base64.b64encode(resized_t).decode()
                            except Exception:
                                pass

                    tool_results.append({
                        "tool_call_id": tc.id,
                        "role": "tool",
                        "name": fn_name,
                        "content": result_text,
                    })

                memory.messages.extend(tool_results)

                # ── Record step for replay ─────────────────────────────────────
                _step_action_ms = int((time.monotonic() - _t2) * 1000)
                # Record StepLog for ANY step with tool calls — including
                # mark_done (even rejected), remember, request_screenshot.
                # Previously we only recorded steps with device actions, which
                # left mark_done attempts invisible in the replay.
                if tool_calls:
                    action_parts = [
                        f"{tc.function.name}({tc.function.arguments})"
                        for tc in tool_calls
                    ]
                    result_parts = [
                        tr["content"] for tr in tool_results
                    ]
                    step_logs_list.append(StepLog(
                        step=steps + 1,
                        thought=_step_thought[:800],
                        action=" | ".join(action_parts),
                        action_result=" | ".join(result_parts),
                        screenshot_b64=_step_screenshot_b64,
                        prompt_tokens=_step_tokens[0],
                        completion_tokens=_step_tokens[1],
                        total_tokens=_step_tokens[2],
                        perception_ms=_step_perception_ms,
                        llm_ms=_step_llm_ms,
                        action_ms=_step_action_ms,
                    ))

                if done_result is not None:
                    done_result.step_logs = step_logs_list
                    done_result.total_tokens = _case_total_tokens
                    await self._log(f"✓ Done: {done_result.status} — {done_result.reason} (tokens: {_case_total_tokens})")
                    return done_result

                memory.prev_ui_text = ui_text
                steps += 1
                if self.step_delay > 0:
                    # Slow actions (app launch, back) need more time to settle;
                    # fast actions (type, scroll) need almost none.
                    slow = max(
                        (_POST_ACTION_DELAYS.get(n, 0.0) for n in dispatched_names),
                        default=0.0,
                    )
                    if slow:
                        await asyncio.sleep(slow)
                    elif dispatched_names and all(n in _FAST_ACTIONS for n in dispatched_names):
                        await asyncio.sleep(0.3)
                    else:
                        await asyncio.sleep(self.step_delay)

        except Exception as e:
            await self._log(f"Unexpected error: {e}")
            return CaseResult(
                status="error", reason=str(e), steps=steps,
                screenshot_b64=last_screenshot_b64,
                log="\n".join(log_lines),
                action_history=list(memory.action_records),
                step_logs=step_logs_list,
            )

        await self._log(f"Max steps ({self.max_steps}) reached without completion")
        return CaseResult(
            status="fail",
            reason=f"Exceeded {self.max_steps} steps without completing the test",
            steps=steps,
            screenshot_b64=last_screenshot_b64,
            log="\n".join(log_lines),
            action_history=list(memory.action_records),
            step_logs=step_logs_list,
        )
