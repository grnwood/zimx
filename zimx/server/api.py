from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import indexer
from .adapters import files, tasks
from .adapters.files import FileAccessError
from .state import vault_state


class FilePathPayload(BaseModel):
    path: str = Field(..., description="Vault-relative path beginning with /")


class FileWritePayload(FilePathPayload):
    content: str


class JournalPayload(BaseModel):
    template: Optional[str] = None


class VaultSelectPayload(BaseModel):
    path: str


class CreatePathPayload(BaseModel):
    path: str
    is_dir: bool = False
    content: Optional[str] = ""


class DeletePathPayload(BaseModel):
    path: str


class ChatPayload(BaseModel):
    messages: List[dict]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = 0.2


app = FastAPI(title="ZimX Local API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1", "http://localhost", "null"],
    allow_origin_regex=r"^https?://127\.0\.0\.1(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/vault/select")
def select_vault(payload: VaultSelectPayload) -> dict:
    try:
        root = vault_state.set_root(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"root": str(root)}


@app.get("/api/vault/tree")
def vault_tree() -> dict:
    root = vault_state.get_root()
    return {"root": str(root), "tree": files.list_dir(root)}


@app.post("/api/file/read")
def file_read(payload: FilePathPayload) -> dict:
    root = vault_state.get_root()
    try:
        content = files.read_file(root, payload.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": content}


@app.post("/api/file/write")
def file_write(payload: FileWritePayload) -> dict:
    root = vault_state.get_root()
    try:
        files.write_file(root, payload.path, payload.content)
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/journal/today")
def journal_today(payload: JournalPayload) -> dict:
    root = vault_state.get_root()
    target = files.ensure_journal_today(root)
    if payload.template and target.stat().st_size == 0:
        target.write_text(payload.template, encoding="utf-8")
    rel = f"/{target.relative_to(root).as_posix()}"
    return {"path": rel}


@app.get("/api/tasks")
def api_tasks(query: Optional[str] = None) -> dict:
    root = vault_state.get_root()
    md_files = []
    for path in root.rglob("*.txt"):
        rel = f"/{path.relative_to(root).as_posix()}"
        md_files.append((rel, path.read_text(encoding="utf-8")))
    extracted = tasks.aggregate_tasks(md_files)
    if query:
        query_lower = query.lower()
        extracted = [t for t in extracted if query_lower in t.text.lower()]
    return {"items": [t.__dict__ for t in extracted]}


@app.get("/api/search")
def api_search(q: Optional[str] = None, limit: int = 5) -> dict:
    hits = indexer.stub_search(q or "", limit)
    return {"hits": hits}


@app.post("/api/ai/chat")
async def api_chat(payload: ChatPayload) -> dict:
    base_url = os.getenv("LMSTUDIO_BASE_URL")
    if not base_url:
        return {"choices": [{"message": {"role": "assistant", "content": "LM Studio base URL not configured."}}]}
    url = base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                url,
                json={
                    "model": "lmstudio",
                    "messages": payload.messages,
                    "max_tokens": payload.max_tokens,
                    "temperature": payload.temperature,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return resp.json()


@app.post("/api/path/create")
def create_path(payload: CreatePathPayload) -> dict:
    root = vault_state.get_root()
    try:
        if payload.is_dir:
            files.create_directory(root, payload.path)
        else:
            files.create_markdown_file(root, payload.path, payload.content or "")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/path/delete")
def delete_path(payload: DeletePathPayload) -> dict:
    root = vault_state.get_root()
    try:
        files.delete_path(root, payload.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


def get_app() -> FastAPI:
    return app
