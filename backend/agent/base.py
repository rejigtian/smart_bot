"""
Shared primitives for the agent layer.

  DeviceDriver   — Protocol defining the interface every device backend must implement.
                   Lets TestCaseAgent depend on the interface, not a concrete class.

  build_model_kwargs — Eliminate duplicated provider/api_base logic between the main
                       agent loop and LLMVerifier.
"""
from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple, runtime_checkable


@runtime_checkable
class DeviceDriver(Protocol):
    """Minimum interface a device backend must satisfy."""

    async def tap(self, x: int, y: int) -> str: ...
    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500
    ) -> str: ...
    async def input_text(self, text: str, clear: bool = False) -> str: ...
    async def press_key(self, key: str) -> str: ...
    async def screenshot(self) -> bytes: ...
    async def get_ui_state(self) -> Tuple[str, List[Dict[str, Any]]]: ...
    async def tap_element(self, index: int) -> str: ...
    async def start_app(self, package: str, activity: str = "") -> str: ...
    async def global_action(self, action: str) -> str: ...


def build_model_kwargs(
    provider: str, model: str, api_base: str
) -> tuple[str, dict[str, Any]]:
    """Return (model_str, extra_kwargs) ready to pass to litellm.completion.

    Rules:
    - ZhipuAI / ZhiPu  → openai/<model> + hard-coded api_base
    - Custom api_base   → openai/<model> + caller's api_base (proxy speaks OpenAI protocol)
    - Everything else   → provider/<model> with no extra_kwargs
    """
    extra_kwargs: dict[str, Any] = {}

    if provider.lower() in ("zhipuai", "zhipu"):
        model_str = f"openai/{model}"
        extra_kwargs["api_base"] = "https://open.bigmodel.cn/api/paas/v4"
    elif api_base:
        model_str = f"openai/{model}"
        extra_kwargs["api_base"] = api_base
    else:
        model_str = f"{provider}/{model}" if provider else model

    return model_str, extra_kwargs
