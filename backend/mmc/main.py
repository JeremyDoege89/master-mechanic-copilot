"""MMC backend application. M4: sessions API + WebSocket state pushes."""

from fastapi import FastAPI

from mmc.api.routes import router

app = FastAPI(title="Master Mechanic Copilot")
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
