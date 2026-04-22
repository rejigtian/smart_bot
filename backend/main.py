"""FastAPI application entry point for smart-androidbot."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import litellm

from db.database import init_db
from routers import devices, recorder, settings, testsuites, testruns
from ws.portal_ws import portal_websocket_endpoint

# Drop provider-unsupported params (e.g. vector_store_ids leaking into Anthropic)
litellm.drop_params = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialised")
    yield


app = FastAPI(title="smart-androidbot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routers
app.include_router(devices.router)
app.include_router(testsuites.router)
app.include_router(testruns.router)
app.include_router(recorder.router)
app.include_router(settings.router)

# Portal reverse WebSocket
app.add_api_websocket_route("/v1/providers/join", portal_websocket_endpoint)

# Serve built frontend (production)
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
else:
    @app.get("/")
    async def root():
        return {"status": "ok", "frontend": "not built — run: cd frontend && npm run build"}
