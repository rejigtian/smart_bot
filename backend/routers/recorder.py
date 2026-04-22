"""
Recorder API — interactive remote-control + step recording.

Flow:
  1. GET  /api/recorder/snapshot   → current screenshot + a11y elements
  2. POST /api/recorder/action     → execute one action, return new snapshot
  3. POST /api/recorder/save       → convert step list into TestSuite + TestCase

The saved TestCase uses `path` as a human-readable step description so the
LLM agent has clear context when replaying:

    [录制步骤]
    1. 启动应用: com.android.settings
    2. 点击 "Wi-Fi"
    3. 点击 "Wi-Fi 开关"
    请重新执行以上步骤并验证期望结果。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from agent.ws_device import WebSocketDevice
from db.database import AsyncSessionLocal
from db.models import TestCase, TestSuite
from ws.portal_ws import connected_devices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recorder", tags=["recorder"])


# ── Response / Request models ─────────────────────────────────────────────────

class Element(BaseModel):
    index: int
    text: str
    className: str
    resourceId: str
    cx: int
    cy: int


class SnapshotResponse(BaseModel):
    screenshot_b64: str
    ui_text: str
    elements: List[Element]


class ActionRequest(BaseModel):
    device_id: str
    action: str          # tap_element / input_text / scroll / global_action / start_app / press_key
    args: Dict[str, Any] = {}


class ActionResponse(BaseModel):
    result: str
    screenshot_b64: str
    ui_text: str
    elements: List[Element]
    description: str     # human-readable step description


class RecordedStep(BaseModel):
    action: str
    args: Dict[str, Any]
    description: str


class SaveRequest(BaseModel):
    device_id: str
    suite_name: str
    expected: str
    steps: List[RecordedStep]


class SaveResponse(BaseModel):
    suite_id: str
    case_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_device(device_id: str) -> WebSocketDevice:
    conn = connected_devices.get(device_id)
    if conn is None or not conn.is_connected:
        raise HTTPException(status_code=400, detail=f"Device '{device_id}' is not connected")
    return WebSocketDevice(conn)


def _step_description(action: str, args: dict, elements: list) -> str:
    """Generate a human-readable description of one step."""
    q = "\u201c"  # "
    Q = "\u201d"  # "
    if action == "tap_element":
        idx = args.get("index")
        el = next((e for e in elements if e.get("index") == idx), None)
        label = el["text"] if el and el.get("text") else f"element[{idx}]"
        return f"点击 {q}{label}{Q}"
    if action == "tap":
        return f"点击坐标 ({args.get('x')}, {args.get('y')})"
    if action == "input_text":
        text = args.get("text", "")
        clear = "（清空后）" if args.get("clear") else ""
        return f"输入文本{clear}: {q}{text}{Q}"
    if action == "scroll":
        direction_map = {"down": "向下", "up": "向上", "left": "向左", "right": "向右"}
        d = direction_map.get(args.get("direction", ""), args.get("direction", ""))
        dist = args.get("distance", "medium")
        return f"滚动 {d}（{dist}）"
    if action == "global_action":
        action_map = {"back": "返回", "home": "回到主页", "recent": "打开最近应用", "notifications": "展开通知"}
        return action_map.get(args.get("action", ""), f"系统操作: {args.get('action')}")
    if action == "press_key":
        return f"按键: {args.get('key')}"
    if action == "start_app":
        return f"启动应用: {args.get('package')}"
    if action == "swipe":
        return f"滑动: ({args.get('x1')},{args.get('y1')}) -> ({args.get('x2')},{args.get('y2')})"
    return f"{action}({json.dumps(args, ensure_ascii=False)})"


async def _snapshot(device: WebSocketDevice) -> SnapshotResponse:
    img_bytes = await device.screenshot()
    ui_text, elements_raw = await device.get_ui_state()
    import base64
    screenshot_b64 = base64.b64encode(img_bytes).decode()
    elements = [
        Element(
            index=e["index"],
            text=e.get("text", ""),
            className=e.get("className", ""),
            resourceId=e.get("resourceId", ""),
            cx=e["cx"],
            cy=e["cy"],
        )
        for e in elements_raw
    ]
    return SnapshotResponse(
        screenshot_b64=screenshot_b64,
        ui_text=ui_text,
        elements=elements,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(device_id: str):
    """Return the current screenshot and a11y element list for a device."""
    device = _get_device(device_id)
    return await _snapshot(device)


@router.post("/action", response_model=ActionResponse)
async def execute_action(req: ActionRequest):
    """Execute one action on the device and return the new screen state."""
    device = _get_device(req.device_id)

    # Pre-fetch elements for description generation (before action changes the screen)
    try:
        _, pre_elements = await device.get_ui_state()
    except Exception:
        pre_elements = []

    # Execute the action
    action = req.action
    args = req.args
    try:
        if action == "tap_element":
            result = await device.tap_element(args["index"])
        elif action == "tap":
            result = await device.tap(args["x"], args["y"])
        elif action == "input_text":
            result = await device.input_text(args["text"], args.get("clear", False))
        elif action == "scroll":
            result = await device.scroll(args["direction"], args.get("distance", "medium"))
        elif action == "swipe":
            result = await device.swipe(
                args["x1"], args["y1"], args["x2"], args["y2"],
                args.get("duration_ms", 500),
            )
        elif action == "global_action":
            result = await device.global_action(args["action"])
        elif action == "press_key":
            result = await device.press_key(args["key"])
        elif action == "start_app":
            result = await device.start_app(args["package"], args.get("activity", ""))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Post-action delay so screen settles before screenshot
    _POST_DELAYS = {"start_app": 2.5, "global_action": 1.2}
    _FAST = {"input_text", "press_key", "scroll", "swipe"}
    delay = _POST_DELAYS.get(action, 0.3 if action in _FAST else 1.0)
    await asyncio.sleep(delay)

    description = _step_description(action, args, pre_elements)
    snap = await _snapshot(device)

    return ActionResponse(
        result=result,
        screenshot_b64=snap.screenshot_b64,
        ui_text=snap.ui_text,
        elements=snap.elements,
        description=description,
    )


@router.post("/save", response_model=SaveResponse, status_code=201)
async def save_recording(req: SaveRequest):
    """Convert a recorded step list into a TestSuite + TestCase."""
    if not req.steps:
        raise HTTPException(status_code=400, detail="No steps recorded")
    if not req.suite_name.strip():
        raise HTTPException(status_code=400, detail="suite_name is required")
    if not req.expected.strip():
        raise HTTPException(status_code=400, detail="expected is required")

    # Build path from step descriptions
    steps_text = "\n".join(f"{i + 1}. {s.description}" for i, s in enumerate(req.steps))
    path = (
        f"[录制步骤]\n"
        f"{steps_text}\n"
        f"请重新执行以上步骤并验证期望结果。"
    )

    async with AsyncSessionLocal() as db:
        suite = TestSuite(
            name=req.suite_name.strip(),
            source_format="recorded",
        )
        db.add(suite)
        await db.flush()

        case = TestCase(
            suite_id=suite.id,
            path=path,
            expected=req.expected.strip(),
            order=0,
        )
        db.add(case)
        await db.commit()
        await db.refresh(suite)
        await db.refresh(case)

    logger.info("Saved recorded test '%s' (%d steps)", req.suite_name, len(req.steps))
    return SaveResponse(suite_id=suite.id, case_id=case.id)


@router.get("/raw-state")
async def raw_state(device_id: str):
    """DEBUG: return raw Portal state RPC response (unprocessed a11y tree)."""
    conn = connected_devices.get(device_id)
    if conn is None or not conn.is_connected:
        raise HTTPException(status_code=400, detail=f"Device '{device_id}' not connected")
    from ws.portal_ws import send_rpc
    raw = await send_rpc(conn, "state", {"filter": True}, timeout=30.0)
    return raw
