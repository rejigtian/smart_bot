"""
Memory layer — AgentMemory manages the rolling conversation state.

Responsibilities:
  - Store the LLM message list with controlled truncation to prevent token overflow.
  - Keep the last N action history lines for step-context injection.
  - Track the previous step's UI text for change-detection injection.
  - Strip old images from earlier messages to avoid "too many images" API errors.
  - Build the structured step-message text that gets injected each iteration.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_CTX = 10     # keep system message + this many most-recent messages
_MAX_HISTORY = 5  # action history lines injected per step
_STUCK_WINDOW = 3 # consecutive identical actions before "stuck" warning
_COMPRESS_THRESHOLD = 4  # compress every N steps (not every step)


@dataclass
class AgentMemory:
    """All mutable state that persists across steps of a single test-case run."""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    action_history: List[str] = field(default_factory=list)
    prev_ui_text: str = ""

    # Number of messages at the start that must never be truncated
    # (system prompt + goal + optional reference examples).
    pinned_count: int = 2

    # Normalized "fn_name:json_args" string per action — for stuck detection.
    # Not injected into messages; used only by is_stuck().
    _call_signatures: List[str] = field(default_factory=list)

    # Structured action records — persisted to DB after a successful run.
    # Each entry: {"step": int, "fn_name": str, "args": dict, "result": str}
    action_records: List[Dict[str, Any]] = field(default_factory=list)

    # Agent notes — key/value pairs written by the remember() tool.
    # Survives context truncation; injected into every step message.
    notes: Dict[str, str] = field(default_factory=dict)

    # History compression — summary of dropped messages.
    _summary: str = ""
    _steps_since_compress: int = 0

    # Recovery escalation — tracks how long the agent has been stuck.
    # Resets when is_stuck() returns False.
    recovery_level: int = 0  # 0=normal, 1=warned, 2=force back, 3=force restart, 4=force fail

    # ── Agent notes ─────────────────────────────────────────────────────────

    def remember(self, key: str, value: str) -> str:
        """Store a note that will be injected into every future step message."""
        self.notes[key] = value
        return f"Remembered: {key} = {value}"

    # ── Image management ──────────────────────────────────────────────────────

    def drop_old_images(self) -> None:
        """Remove image_url parts from all earlier user messages.

        Keeps only the most-recent screenshot in context; prevents API errors
        from too many images and reduces token consumption.
        """
        for msg in self.messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                msg["content"] = [
                    part for part in msg["content"]
                    if part.get("type") != "image_url"
                ]

    # ── Context window management ─────────────────────────────────────────────

    def truncate(self) -> None:
        """Keep pinned messages (system + goal + optional reference) plus the
        _MAX_CTX most-recent messages.

        messages[:pinned_count] = always preserved (system, goal, reference)
        messages[-_MAX_CTX:] = rolling recent context
        """
        total_keep = self.pinned_count + _MAX_CTX
        if len(self.messages) <= total_keep:
            return
        pinned = self.messages[:self.pinned_count]
        tail = self.messages[-_MAX_CTX:]
        # Avoid duplicates if tail overlaps with pinned
        tail = [m for m in tail if m not in pinned]
        self.messages = pinned + tail

    async def compress(
        self,
        summarizer: Optional[Callable[[str], Coroutine[Any, Any, str]]] = None,
    ) -> None:
        """Compress old messages into a summary instead of hard-truncating.

        Called instead of truncate(). Every _COMPRESS_THRESHOLD steps, if there
        are messages that would be dropped, they are summarized via an LLM call
        and the summary is injected as a pinned message right after the initial
        pinned block.

        If no summarizer is provided or if it's not time to compress yet,
        falls back to regular truncate().
        """
        self._steps_since_compress += 1
        total_keep = self.pinned_count + _MAX_CTX

        # Not enough messages to warrant compression — nothing to do
        if len(self.messages) <= total_keep:
            return

        # Only run the expensive LLM summarization every N steps
        if summarizer is None or self._steps_since_compress < _COMPRESS_THRESHOLD:
            self.truncate()
            return

        self._steps_since_compress = 0

        # Extract messages that would be dropped
        pinned = self.messages[:self.pinned_count]
        tail = self.messages[-_MAX_CTX:]
        middle = self.messages[self.pinned_count:-_MAX_CTX]

        if not middle:
            return

        # Build text from the about-to-be-dropped messages
        drop_lines: List[str] = []
        for msg in middle:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Multi-part message — extract only text parts
                content = " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
            if content:
                drop_lines.append(f"[{role}] {content[:300]}")

        if not drop_lines:
            self.truncate()
            return

        text_to_summarize = "\n".join(drop_lines)

        try:
            new_summary = await summarizer(text_to_summarize)
            # Merge with previous summary
            if self._summary:
                self._summary = f"{self._summary}\n{new_summary}"
            else:
                self._summary = new_summary
            # Cap summary length
            if len(self._summary) > 1200:
                self._summary = self._summary[-1200:]
            logger.debug("History compressed: %d messages → summary (%d chars)",
                         len(middle), len(self._summary))
        except Exception as exc:
            logger.warning("History compression failed (%s) — falling back to truncate", exc)
            self.truncate()
            return

        # Rebuild: pinned + summary message + tail
        summary_msg = {
            "role": "user",
            "content": f"[History Summary — earlier steps]\n{self._summary}",
        }
        tail = [m for m in tail if m not in pinned]
        self.messages = pinned + [summary_msg] + tail

    # ── Action history ────────────────────────────────────────────────────────

    def record_action(self, step: int, fn_name: str, args: dict, result: str) -> None:
        """Append one line to action history (called after every non-mark_done tool)."""
        self.action_history.append(
            f"Step {step + 1}: {fn_name}({args}) → {result[:80]}"
        )
        sig = f"{fn_name}:{json.dumps(args, sort_keys=True)}"
        self._call_signatures.append(sig)
        self.action_records.append({"step": step + 1, "fn_name": fn_name, "args": args, "result": result[:120]})

    def is_stuck(self, window: int = _STUCK_WINDOW) -> bool:
        """Return True if the last `window` actions are all identical.

        Also manages recovery_level escalation: increments when stuck,
        resets when not stuck.
        """
        if len(self._call_signatures) < window:
            self.recovery_level = 0
            return False
        tail = self._call_signatures[-window:]
        stuck = len(set(tail)) == 1
        if stuck:
            self.recovery_level = min(self.recovery_level + 1, 4)
        else:
            # Decay gradually rather than hard-resetting — prevents the agent
            # from escaping recovery by doing one different action then repeating.
            self.recovery_level = max(self.recovery_level - 1, 0)
        return stuck

    # ── Step message construction ─────────────────────────────────────────────

    def build_step_text(
        self, step: int, ui_text: str,
        img_width: int = 0, img_height: int = 0,
    ) -> str:
        """Assemble the text portion of the step message sent to the LLM.

        Structure:
            [Screenshot]       ← pixel dimensions of the image the AI sees
            [Previous State]   ← diff reference
            [Action History]   ← what the agent has done recently
            [Current State]    ← current a11y tree
            Step N: What action should you take next?
        """
        parts: List[str] = []
        if self.notes:
            note_lines = [f"  {k}: {v}" for k, v in self.notes.items()]
            parts.append("[Agent Notes]\n" + "\n".join(note_lines))
        if img_width and img_height:
            parts.append(
                f"[Screenshot: {img_width}×{img_height}px — "
                f"give pixel coordinates in this image for tap() and swipe()]"
            )
        if self.prev_ui_text:
            parts.append(f"[Previous State]\n{self.prev_ui_text}")
        if self.action_history:
            recent = self.action_history[-_MAX_HISTORY:]
            parts.append("[Action History]\n" + "\n".join(recent))
        if ui_text:
            parts.append(f"[Current State]\n{ui_text}")

        stuck_warn = ""
        if self.is_stuck():
            last_sig = self._call_signatures[-1] if self._call_signatures else ""
            fn = last_sig.split(":")[0]
            if self.recovery_level <= 1:
                stuck_warn = (
                    f"\n\n⚠ WARNING: You repeated {fn}() {_STUCK_WINDOW}+ times with no change. "
                    f"Try a DIFFERENT approach: scroll() to find the element, or "
                    f"global_action('back') to return to the previous screen."
                )
            elif self.recovery_level == 2:
                stuck_warn = (
                    f"\n\n🚨 STUCK (level 2): {fn}() repeated 6+ times. "
                    f"You MUST call global_action('back') NOW to go back, "
                    f"then try a completely different navigation path."
                )
            elif self.recovery_level == 3:
                stuck_warn = (
                    f"\n\n🚨 STUCK (level 3): Still stuck after going back. "
                    f"Call start_app() to restart the app from scratch, "
                    f"or call mark_done(status='fail') if the task is impossible."
                )
            else:
                stuck_warn = (
                    f"\n\n🛑 STUCK (level 4): You have been stuck for too long. "
                    f"Call mark_done(status='fail', reason='Unable to complete — stuck in loop') NOW."
                )

        suffix = f"\n\nStep {step + 1}: What action should you take next?{stuck_warn}"
        if parts:
            return "\n\n".join(parts) + suffix
        return f"Step {step + 1}: What do you see? What action should you take next?{stuck_warn}"
