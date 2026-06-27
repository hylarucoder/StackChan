from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .apps.native_agent_api import configure_app as configure_native_agent_api
from .xiaozhi.app import create_router as create_xiaozhi_router

logger = logging.getLogger("uvicorn.error")

# Built React UI. Served at the root so the front end and its bundled media
# (e.g. the dance wav) are same-origin with the API. Override with STACKCHAN_UI_DIST.
_DEFAULT_UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"
UI_DIST = Path(os.getenv("STACKCHAN_UI_DIST", str(_DEFAULT_UI_DIST)))


def create_app() -> FastAPI:
    app = FastAPI(title="StackChan Server", version="0.1.0")
    configure_native_agent_api(app)
    app.include_router(create_xiaozhi_router())
    # Mount last so API/WebSocket routes keep priority; everything else (the SPA
    # and its assets, including /dance/assets/*) falls through to the built UI.
    if UI_DIST.is_dir():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:
        logger.warning("UI dist not found at %s; run `cd ui && npm run build`", UI_DIST)
    return app


app = create_app()


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("stackchan_server.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
