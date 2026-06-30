import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .claude_runner import run_claude


app = FastAPI(title="claude-watch webhook")


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    timeout: int | None = Field(default=None, ge=1, le=600)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask")
async def ask(req: AskRequest, authorization: str | None = Header(default=None)):
    token = os.environ.get("WEBHOOK_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="WEBHOOK_TOKEN not configured")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")

    rc, stdout, stderr = await run_claude(req.prompt, timeout=req.timeout)
    if rc != 0:
        raise HTTPException(status_code=502, detail=f"claude exited rc={rc}: {stderr[:500]}")
    return {"answer": stdout.strip()}
