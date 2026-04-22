"""
WebSocket endpoint — Portal Android app connects here (reverse connection).
Portal → Server: Bearer token auth, then sends JSON-RPC responses.
Server → Portal: JSON-RPC requests to control the device.
"""
import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from db.database import AsyncSessionLocal
from db.models import Device

logger = logging.getLogger(__name__)

connected_devices: Dict[str, "DeviceConnection"] = {}


@dataclass
class DeviceConnection:
    ws: WebSocket
    device_id: str
    device_name: str
    token: str
    pending: Dict[str, asyncio.Future] = field(default_factory=dict)
    is_connected: bool = True


async def portal_websocket_endpoint(websocket: WebSocket):
    headers = dict(websocket.headers)
    authorization = headers.get("authorization", "")
    device_name = headers.get("x-device-name", "Unknown")

    if not authorization.startswith("Bearer "):
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    token = authorization.removeprefix("Bearer ").strip()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Device).where(Device.token == token))
        device_row = result.scalar_one_or_none()

    if device_row is None:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    db_device_id = device_row.id
    await websocket.accept()

    conn = DeviceConnection(ws=websocket, device_id=db_device_id,
                            device_name=device_name, token=token)
    connected_devices[db_device_id] = conn

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Device).where(Device.token == token)
            .values(status="online", last_seen=datetime.utcnow(),
                    name=device_name or device_row.name)
        )
        await session.commit()

    logger.info("Device connected: %s (%s)", db_device_id, device_name)

    try:
        while True:
            # Use a short receive timeout so we can send periodic pings.
            # If the client goes silent for >30s without responding to pings,
            # we treat the connection as dead.
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                _handle_message(conn, raw)
            except asyncio.TimeoutError:
                # No message for 30s — send a WebSocket ping to check liveness.
                try:
                    await asyncio.wait_for(websocket.send_text('{"id":"__ping__","method":"ping","params":{}}'), timeout=5.0)
                except Exception:
                    logger.warning("Ping failed for %s — treating as disconnected", db_device_id)
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Portal WS error for %s: %s", db_device_id, e)
    finally:
        conn.is_connected = False
        connected_devices.pop(db_device_id, None)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Device).where(Device.token == token)
                .values(status="offline", last_seen=datetime.utcnow())
            )
            await session.commit()
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Device disconnected"))
        conn.pending.clear()
        logger.info("Device disconnected: %s", db_device_id)


def _handle_message(conn: DeviceConnection, raw: str):
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    msg_id = str(msg.get("id", ""))
    if not msg_id:
        return

    future = conn.pending.pop(msg_id, None)
    if future is None or future.done():
        return

    if "result" in msg:
        future.set_result(msg["result"])
    elif "error" in msg:
        err = msg["error"]
        if isinstance(err, dict):
            err = err.get("message", str(err))
        future.set_exception(RuntimeError(str(err)))
    else:
        future.set_result(None)


async def send_rpc(conn: DeviceConnection, method: str,
                   params: Optional[Dict[str, Any]] = None,
                   timeout: float = 30.0) -> Any:
    call_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    conn.pending[call_id] = future

    payload = json.dumps({"id": call_id, "method": method, "params": params or {}})
    try:
        # Wrap send_text in a timeout: a silent TCP half-open state can cause
        # send_text to hang for several minutes waiting for kernel retransmission.
        await asyncio.wait_for(conn.ws.send_text(payload), timeout=10.0)
    except asyncio.TimeoutError:
        conn.pending.pop(call_id, None)
        conn.is_connected = False
        raise RuntimeError(f"RPC '{method}' send timeout — connection appears dead")
    except Exception as e:
        conn.pending.pop(call_id, None)
        raise RuntimeError(f"Failed to send RPC '{method}': {e}") from e

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        conn.pending.pop(call_id, None)
        raise TimeoutError(f"RPC '{method}' timed out after {timeout}s")
