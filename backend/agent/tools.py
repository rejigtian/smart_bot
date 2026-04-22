"""
Action layer — OpenAI function-call tool schemas.

TOOLS is consumed verbatim by litellm.completion(tools=TOOLS).
The description of each tool directly influences the LLM's tool selection —
keep them precise and opinionated (e.g. "PREFER this over tap()").
"""
from __future__ import annotations

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "tap_element",
            "description": (
                "Tap a UI element by its index from the [UI Elements] list. "
                "PREFER this over tap() whenever the element is visible in the UI list — "
                "it is precise and never misses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Element index from the [UI Elements] list",
                    },
                },
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tap",
            "description": (
                "Tap a point on the screen using pixel coordinates in the screenshot image. "
                "Use only when tap_element() cannot identify the target element. "
                "Read x, y directly from the grid labels printed on the screenshot edges. "
                "The server converts image pixels to device pixels automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "integer",
                        "description": "Horizontal pixel coordinate in the screenshot image",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Vertical pixel coordinate in the screenshot image",
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": (
                "Scroll the screen to reveal off-screen content. "
                "Use this when the target element is NOT in the [UI Elements] list — "
                "scroll to expose it, then tap_element. "
                "Do NOT use swipe() to scroll."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "down — reveal content below; up — go back up.",
                    },
                    "distance": {
                        "type": "string",
                        "enum": ["small", "medium", "large"],
                        "default": "medium",
                        "description": "How far to scroll. Default: medium.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swipe",
            "description": (
                "Swipe from one point to another using pixel coordinates in the screenshot image. "
                "Use scroll() to scroll — do NOT use swipe for scrolling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Start X pixel in screenshot"},
                    "y1": {"type": "integer", "description": "Start Y pixel in screenshot"},
                    "x2": {"type": "integer", "description": "End X pixel in screenshot"},
                    "y2": {"type": "integer", "description": "End Y pixel in screenshot"},
                    "duration_ms": {"type": "integer", "default": 500},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "input_text",
            "description": "Type text into the focused field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "clear": {
                        "type": "boolean",
                        "default": False,
                        "description": "Clear existing text first",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a hardware key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "enum": ["back", "home", "recent", "enter", "del", "tab"],
                    },
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "global_action",
            "description": "Trigger a global Android action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["back", "home", "recent", "notifications"],
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_app",
            "description": "Launch an Android app by package name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string"},
                    "activity": {"type": "string", "default": ""},
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_packages",
            "description": (
                "List all installed app package names on the device. "
                "Call this when start_app() fails with 'Could not create intent' "
                "or when you don't know the correct package name for an app. "
                "Search the result for the app name, then retry start_app() with the correct package."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": (
                "Wait N seconds without touching the screen. "
                "Use this when you see a loading screen, splash/intro screen, progress bar, "
                "or any animation in progress. "
                "Do NOT tap the screen in these cases — tapping during a transition will "
                "land on the wrong element once the screen settles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "Seconds to wait. Use 2 for splash screens, up to 5 for slow loads.",
                    },
                },
                "required": ["seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Store a key observation or fact that you will need in later steps. "
                "These notes survive context truncation and are shown to you every step. "
                "Use this to remember: package names, screen titles, whether login succeeded, "
                "element positions, or any discovery you don't want to forget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short label, e.g. 'target_package', 'login_status'",
                    },
                    "value": {
                        "type": "string",
                        "description": "The observation to remember",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_done",
            "description": (
                "Mark the test case as finished. "
                "If the action just triggered an animation or toast that takes time to appear, "
                "set wait_before_verify to the number of seconds to wait before verification "
                "(e.g. 0.5 for a 500ms animation, 2 for a slow network response)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pass", "fail", "skip"],
                        "description": (
                            "pass — the expected result is visible/confirmed; "
                            "fail — the test ran but result is wrong; "
                            "skip — cannot run (device state, dependency)."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short explanation (1-2 sentences).",
                    },
                    "wait_before_verify": {
                        "type": "number",
                        "description": (
                            "Seconds to wait before taking the verification screenshot. "
                            "Use when the last action triggers an animation, toast, or delayed result. "
                            "Default 0. Example: 0.5 for a flying animation, 2 for a network response."
                        ),
                        "default": 0,
                    },
                },
                "required": ["status", "reason"],
            },
        },
    },
]
