"""MMC backend application. M4: sessions API + WebSocket state pushes."""

from fastapi import FastAPI

from mmc.api.routes import router
from mmc.api.routes_vision import router as vision_router

app = FastAPI(title="Master Mechanic Copilot")
app.include_router(router)
app.include_router(vision_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
