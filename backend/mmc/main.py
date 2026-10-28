"""MMC backend application. M0: health endpoint only (scripts/dev.sh smoke test)."""

from fastapi import FastAPI

app = FastAPI(title="Master Mechanic Copilot")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
