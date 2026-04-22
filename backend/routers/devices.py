"""Device management — list connected devices, generate tokens, delete."""
import secrets
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import Device
from ws.portal_ws import connected_devices

router = APIRouter(prefix="/api/devices", tags=["devices"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class DeviceOut(BaseModel):
    id: str
    name: str
    token: str
    status: str
    last_seen: str

    class Config:
        from_attributes = True


class TokenCreateRequest(BaseModel):
    name: str = "New Device"


@router.get("", response_model=List[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.last_seen.desc()))
    devices = result.scalars().all()
    # Sync status from in-memory connected_devices
    out = []
    for d in devices:
        status = "online" if d.id in connected_devices and connected_devices[d.id].is_connected else "offline"
        out.append(DeviceOut(
            id=d.id,
            name=d.name,
            token=d.token,
            status=status,
            last_seen=d.last_seen.isoformat() if d.last_seen else "",
        ))
    return out


@router.post("", response_model=DeviceOut, status_code=201)
async def create_device(req: TokenCreateRequest, db: AsyncSession = Depends(get_db)):
    token = secrets.token_urlsafe(32)
    device = Device(name=req.name, token=token, status="offline")
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return DeviceOut(
        id=device.id,
        name=device.name,
        token=device.token,
        status="offline",
        last_seen=device.last_seen.isoformat() if device.last_seen else "",
    )


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: str, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
    await db.commit()
