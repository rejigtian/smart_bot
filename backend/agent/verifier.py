"""
Verification layer — LLMVerifier confirms that a pass claim is actually correct.

Flow:
  1. Take a fresh screenshot via the device driver.
  2. Send screenshot + expected result to the LLM.
  3. LLM replies with JSON: {"confirmed": bool, "reason": str}.
  4. Return (confirmed, reason, fresh_screenshot_b64).

This prevents the agent from declaring "pass" while still on the wrong screen.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from typing import Any, Callable, Coroutine, Optional, Tuple

import litellm

from agent.base import DeviceDriver, build_model_kwargs

logger = logging.getLogger(__name__)

# Type for the optional log callback used by TestCaseAgent
_LogCallback = Optional[Callable[[str], Coroutine[Any, Any, None]]]


def _combine_screenshots(a_b64: str, b_b64: str) -> str:
    """Stack two screenshots vertically with labels for the final report.

    Returns base64 JPEG of the combined image, or empty string on failure.
    If one is missing or both are identical, returns the available one.
    """
    if not a_b64 and not b_b64:
        return ""
    if not a_b64:
        return b_b64
    if not b_b64 or a_b64 == b_b64:
        return a_b64
    try:
        from PIL import Image, ImageDraw, ImageFont
        img_a = Image.open(io.BytesIO(base64.b64decode(a_b64))).convert("RGB")
        img_b = Image.open(io.BytesIO(base64.b64decode(b_b64))).convert("RGB")
        # Normalize widths
        w = min(img_a.width, img_b.width, 720)
        scale_a = w / img_a.width
        scale_b = w / img_b.width
        h_a = int(img_a.height * scale_a)
        h_b = int(img_b.height * scale_b)
        if scale_a != 1.0:
            img_a = img_a.resize((w, h_a), Image.LANCZOS)
        if scale_b != 1.0:
            img_b = img_b.resize((w, h_b), Image.LANCZOS)

        # Label bar height
        bar = 28
        combined = Image.new("RGB", (w, h_a + h_b + bar * 2), (30, 30, 30))
        draw = ImageDraw.Draw(combined)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        except Exception:
            font = ImageFont.load_default()
        draw.text((10, 6), "A — at-action (toast may be visible)", fill=(255, 255, 255), font=font)
        combined.paste(img_a, (0, bar))
        draw.text((10, bar + h_a + 6), "B — settled (fresh screenshot)", fill=(255, 255, 255), font=font)
        combined.paste(img_b, (0, bar + h_a + bar))

        out = io.BytesIO()
        combined.save(out, format="JPEG", quality=75, optimize=True)
        return base64.b64encode(out.getvalue()).decode()
    except Exception as exc:
        logger.warning("Failed to combine screenshots: %s", exc)
        return a_b64 or b_b64


class LLMVerifier:
    """Verifies that the expected result is visible on screen before accepting a pass."""

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str = "",
        api_base: str = "",
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.api_base = api_base

    async def verify(
        self,
        device: DeviceDriver,
        expected: str,
        log: _LogCallback = None,
        action_history: Optional[list] = None,
        agent_reason: str = "",
        pre_screenshot_b64: str = "",
    ) -> Tuple[bool, str, str, str]:
        """Verify the expected result is visible on screen.

        Uses pre_screenshot_b64 (captured right after the action, while toast
        is still visible) as the primary evidence. Also takes a fresh screenshot
        for additional context — but the pre-screenshot catches transient UI
        like toasts that disappear within 2-3 seconds.

        Returns:
            (confirmed, reason, fresh_screenshot_b64, gap)
        """
        # Use pre-captured screenshot (from right after the action) if available
        pre_b64 = pre_screenshot_b64

        # Also take a fresh screenshot for additional context
        fresh_b64 = ""
        try:
            img_bytes = await device.screenshot()
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(img_bytes))
                w, h = img.size
                img = img.resize((w // 2, h // 2), Image.LANCZOS)
                out = io.BytesIO()
                img.convert("RGB").save(out, format="JPEG", quality=65)
                fresh_b64 = base64.b64encode(out.getvalue()).decode()
            except Exception:
                fresh_b64 = base64.b64encode(img_bytes).decode()
        except Exception as e:
            if not pre_b64:
                return False, f"Screenshot for verification failed: {e}", "", ""

        # Pick the best screenshot: prefer pre-screenshot (has toast),
        # fall back to fresh if pre is not available
        verify_b64 = pre_b64 or fresh_b64

        history_section = ""
        if action_history:
            recent = action_history[-10:]
            history_section = (
                "\n\nAgent's action history (what was actually done):\n"
                + "\n".join(recent)
                + "\n"
            )

        agent_reason_section = ""
        if agent_reason:
            agent_reason_section = (
                f"\n\nAgent's final observation (direct witness of what changed):\n"
                f"{agent_reason}\n"
                "This is what the agent literally observed on screen during execution — "
                "treat it as direct observational evidence, not speculation."
            )

        verify_messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict Android test verification assistant. "
                    "You must be conservative — only confirm pass when you have clear evidence. "
                    "For static outcomes (a button is visible, a page opened), the screenshot alone "
                    "is sufficient. For dynamic changes (a value increased, an item was added), "
                    "the agent's observation of what changed — combined with the screenshot "
                    "showing the resulting state — is sufficient evidence."
                ),
            },
            {
                "role": "user",
                "content": (
                    [
                        {"type": "text", "text": "SCREENSHOT A — captured right after the final action (transient UI like toasts may be visible here):"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{pre_b64}"}},
                    ] if pre_b64 and pre_b64 != fresh_b64 else []
                ) + [
                    {"type": "text", "text": "SCREENSHOT B — captured fresh now (settled state, transient UI gone):" if pre_b64 and pre_b64 != fresh_b64 else "Screenshot:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{fresh_b64 or pre_b64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "STEP 1 — Describe what you see across the screenshot(s):\n"
                            "If two screenshots were provided, compare them — A shows the moment "
                            "right after action (may include toasts/animations/transient notifications), "
                            "B shows the settled state. Both are valid evidence.\n"
                            "List the current screen/page name, all visible text labels, numbers, "
                            "progress bars, and any content shown. Be specific.\n\n"
                            f"STEP 2 — Compare to the expected result:\n"
                            f"Expected: {expected}"
                            f"{agent_reason_section}"
                            f"{history_section}\n"
                            "Does the evidence confirm the expected result?\n"
                            "Rules:\n"
                            "- For static outcomes: confirm TRUE only if the specific outcome "
                            "is literally visible in what you described in Step 1.\n"
                            "- For change-based outcomes (e.g. 'value increased', 'item added'): "
                            "confirm TRUE if the agent's observation describes the change with "
                            "specific before/after values AND the screenshot is consistent with "
                            "the 'after' state. You do NOT need to see both before and after "
                            "on screen simultaneously.\n"
                            "- If the page/content is DIFFERENT from what the expected result "
                            "describes, answer false.\n"
                            "- If the actions taken don't include the steps needed to produce "
                            "the expected result (e.g. expected requires posting but no text "
                            "was typed), answer false.\n\n"
                            "Reply with JSON only — no markdown:\n"
                            '{"confirmed": true/false, '
                            '"observation": "what you literally see on screen", '
                            '"reason": "why this matches or does not match the expected result", '
                            '"gap": "if not confirmed, describe specifically what is missing or wrong '
                            'and what the agent should do to reach the expected state (e.g. navigate to X page, '
                            'scroll down to find Y). Leave empty string if confirmed."}'
                        ),
                    },
                ],
            },
        ]

        try:
            model_str, extra_kwargs = build_model_kwargs(
                self.provider, self.model, self.api_base
            )
            kwargs: dict[str, Any] = {
                "model": model_str,
                "messages": verify_messages,
                "temperature": 0.1,
                **extra_kwargs,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key

            response = await asyncio.wait_for(
                litellm.acompletion(**kwargs),
                timeout=60.0,
            )
            content = (response.choices[0].message.content or "").strip()

            content_clean = re.sub(r"^```[a-z]*\n?", "", content).rstrip("` \n")
            json_match = re.search(r"\{.*\}", content_clean, re.DOTALL)
            # Combine both screenshots into one for the report — stack vertically
            # with a divider, so reviewers can see both the "at-action" frame
            # (with toast) and the "settled" frame.
            final_b64 = _combine_screenshots(pre_b64, fresh_b64) or verify_b64 or fresh_b64

            if json_match:
                data = json.loads(json_match.group())
                confirmed = bool(data.get("confirmed", False))
                reason = data.get("reason", content[:300])
                gap = data.get("gap", "") if not confirmed else ""
                return confirmed, reason, final_b64, gap

            # Heuristic fallback when JSON parsing fails
            lower = content.lower()
            if any(w in lower for w in ("true", "yes, ", "confirmed", "is visible", "can see")):
                return True, content[:300], final_b64, ""
            return False, content[:300], final_b64, content[:200]

        except Exception as e:
            if log:
                await log(f"Verification LLM error: {e}")
            else:
                logger.error("Verification LLM error: %s", e)
            return False, f"Verification call failed: {e}", fresh_b64, ""
