"""
WebSocketDevice — bridges litellm tool calls to Portal app via JSON-RPC.
All method names are the actual ActionDispatcher strings from Portal 0.6.5.

A11y tree parsing/formatting lives in agent.perception — this module is
purely the transport/action layer.
"""
import base64
import logging
import struct
from typing import Any, Dict, List, Optional, Tuple

from agent.perception import format_ui_state
from ws.portal_ws import DeviceConnection, send_rpc

logger = logging.getLogger(__name__)

_KEY_CODES = {
    "back": 4, "home": 3, "recent": 187,
    "enter": 66, "del": 67, "tab": 61, "space": 62,
}


class WebSocketDevice:
    def __init__(self, connection: DeviceConnection):
        self.connection = connection
        # Cache from last get_ui_state() call — used by tap_element() and guards
        self._elements: List[Dict[str, Any]] = []
        self.screen_width: int = 0
        self.screen_height: int = 0
        self.keyboard_visible: bool = False
        # Dimensions of the last screenshot image (may differ from screen_width/screen_height
        # if Portal downscales the screenshot before sending)
        self._screenshot_width: int = 0
        self._screenshot_height: int = 0

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
                   timeout: float = 30.0) -> Any:
        if not self.connection.is_connected:
            raise RuntimeError(f"Device {self.connection.device_id} is not connected")
        return await send_rpc(self.connection, method, params, timeout=timeout)

    def _img_to_abs(self, img_x: int, img_y: int) -> tuple[int, int]:
        """Convert half-size screenshot pixel coordinates to device pixel coordinates.

        The screenshot is always resized to 50% of the device's native resolution,
        so the conversion is simply ×2.  Using image pixel coordinates directly
        avoids the normalized-vs-pixel confusion that caused off-target taps.
        """
        return img_x * 2, img_y * 2

    async def tap(self, x: int, y: int) -> str:
        ax, ay = self._img_to_abs(x, y)
        await self._rpc("tap", {"x": ax, "y": ay})
        return f"Tapped img=({x},{y}) → abs=({ax},{ay})"

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> str:
        ax1, ay1 = self._img_to_abs(x1, y1)
        ax2, ay2 = self._img_to_abs(x2, y2)
        await self._rpc("swipe", {"startX": ax1, "startY": ay1,
                                   "endX": ax2, "endY": ay2, "duration": duration_ms})
        return f"Swiped img=({x1},{y1})→({x2},{y2}) abs=({ax1},{ay1})→({ax2},{ay2})"

    async def scroll(self, direction: str, distance: str = "medium") -> str:
        """Scroll the screen by performing a swipe gesture.

        Uses cached screen dimensions from the last get_ui_state() call;
        falls back to sensible defaults (1080×2400) if not yet populated.
        """
        w = self.screen_width or 1080
        h = self.screen_height or 2400
        cx = w // 2

        dist_map = {"small": 0.2, "medium": 0.4, "large": 0.65}
        ratio = dist_map.get(distance, 0.4)
        delta = int(h * ratio)

        if direction == "down":
            # Finger moves up → content scrolls down (reveal content below)
            y1, y2 = int(h * 0.65), int(h * 0.65) - delta
        elif direction == "up":
            # Finger moves down → content scrolls up (go back up)
            y1, y2 = int(h * 0.35), int(h * 0.35) + delta
        elif direction == "left":
            x1_s = int(w * 0.75)
            x2_s = int(w * 0.25)
            await self._rpc("swipe", {"startX": x1_s, "startY": h // 2,
                                       "endX": x2_s, "endY": h // 2, "duration": 400})
            return f"Scrolled left ({distance})"
        elif direction == "right":
            x1_s = int(w * 0.25)
            x2_s = int(w * 0.75)
            await self._rpc("swipe", {"startX": x1_s, "startY": h // 2,
                                       "endX": x2_s, "endY": h // 2, "duration": 400})
            return f"Scrolled right ({distance})"
        else:
            raise ValueError(f"Unknown scroll direction: {direction!r}")

        await self._rpc("swipe", {"startX": cx, "startY": y1,
                                   "endX": cx, "endY": y2, "duration": 400})
        return f"Scrolled {direction} ({distance})"

    async def input_text(self, text: str, clear: bool = False) -> str:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        await self._rpc("keyboard/input", {"base64_text": encoded, "clear": clear})
        return f"Typed: {text}"

    async def press_key(self, key: str) -> str:
        code = _KEY_CODES.get(key.lower())
        if code is None:
            raise ValueError(f"Unknown key: {key!r}. Valid: {list(_KEY_CODES)}")
        await self._rpc("keyboard/key", {"key_code": code})
        return f"Pressed key: {key}"

    @staticmethod
    def _png_dimensions(data: bytes) -> tuple[int, int]:
        """Extract (width, height) from a PNG bytestring using the IHDR chunk."""
        # PNG signature = 8 bytes, then IHDR chunk: 4-len + 4-type + 4-width + 4-height
        if len(data) >= 24 and data[:4] == b"\x89PNG":
            w, h = struct.unpack(">II", data[16:24])
            return w, h
        return 0, 0

    async def screenshot(self) -> bytes:
        result = await self._rpc("screenshot", {"hideOverlay": True}, timeout=15.0)
        if isinstance(result, str):
            b64 = result
        elif isinstance(result, dict):
            b64 = result.get("data") or result.get("image") or result.get("screenshot", "")
        else:
            b64 = str(result)
        img = base64.b64decode(b64)
        w, h = self._png_dimensions(img)
        if w and h:
            self._screenshot_width = w
            self._screenshot_height = h
        return img

    async def get_ui_state(self) -> Tuple[str, List[Dict[str, Any]]]:
        """Fetch a11y tree and return (formatted_text, elements).

        Formatted text is ready to inject into LLM messages.
        Elements are cached so tap_element() can resolve indices.
        """
        raw = await self._rpc("state", {"filter": True}, timeout=30.0) or {}
        formatted, elements, w, h, is_editable = format_ui_state(raw)
        self._elements = elements
        self.keyboard_visible = is_editable
        if w:
            self.screen_width = w
        if h:
            self.screen_height = h
        return formatted, elements

    async def tap_element(self, index: int) -> str:
        """Tap the UI element at the given index (from the last get_ui_state() result)."""
        el = next((e for e in self._elements if e["index"] == index), None)
        if el is None:
            available = [e["index"] for e in self._elements[:20]]
            raise ValueError(
                f"No element with index {index}. Available: {available}"
            )
        cx, cy = el["cx"], el["cy"]
        if cx == 0 and cy == 0:
            raise ValueError(
                f"Element {index} ('{el['text']}') has no valid bounds"
            )
        await self._rpc("tap", {"x": cx, "y": cy})
        return f"Tapped element {index} ('{el['text']}') at ({cx}, {cy})"

    async def start_app(self, package: str, activity: str = "") -> str:
        await self._rpc("app", {"package": package, "activity": activity,
                                 "stopBeforeLaunch": False})
        return f"Started {package}"

    async def stop_app(self, package: str) -> str:
        await self._rpc("app/stop", {"package": package})
        return f"Stopped {package}"

    async def list_packages(self) -> List[str]:
        result = await self._rpc("packages", {})
        apps: list = result if isinstance(result, list) else result.get("packages", [])
        return [a.get("packageName", "") for a in apps]

    async def global_action(self, action: str) -> str:
        """action: back / home / recent / notifications

        back/home/recent are forwarded as keyboard/key (same as press_key, which works).
        notifications uses the accessibility globalAction API (code 4).
        """
        key_map = {"back": 4, "home": 3, "recent": 187}
        lower = action.lower()
        if lower in key_map:
            await self._rpc("keyboard/key", {"key_code": key_map[lower]})
        elif lower == "notifications":
            # GLOBAL_ACTION_NOTIFICATIONS = 4
            await self._rpc("globalAction", {"action": 4})
        else:
            raise ValueError(f"Unknown global action: {action!r}")
        return f"Global action: {action}"
