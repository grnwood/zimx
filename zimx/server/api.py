from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, File as FastAPISingleFile, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import indexer
from .adapters import files, tasks
from .adapters.files import FileAccessError
from .state import vault_state
from zimx.app import config

_LOCAL_FILE_OPS_ENABLED = os.getenv("ATTACHMENTS_LOCAL_FILE_OPS", "0") not in (
    "0",
    "false",
    "False",
    "",
    None,
)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _should_use_local_file_ops(request: Request) -> bool:
    if not _LOCAL_FILE_OPS_ENABLED:
        return False
    client = request.client
    if not client:
        return False
    return client.host in _LOCAL_HOSTS


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


class AttachmentDeletePayload(BaseModel):
    paths: List[str] = Field(..., description="Vault-relative attachment paths to delete")


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
    # Pass template through so the initial content becomes the user's day template
    target = files.ensure_journal_today(root, template=payload.template)
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


@app.post("/files/attach")
def attach_files(
    request: Request,
    page_path: str = Form(...),
    files: List[UploadFile] = FastAPISingleFile(...),
) -> dict:
    root = _get_vault_root()
    normalized_page = _vault_relative_path(page_path)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    stored_paths: list[str] = []
    use_local_ops = _should_use_local_file_ops(request)
    for upload in files:
        stored_paths.append(_store_attachment(root, normalized_page, upload, use_local_ops))
    _log_attachment(f"Attached {len(stored_paths)} file(s) for {normalized_page}")
    return {"ok": True, "page": normalized_page, "attachments": stored_paths}


@app.get("/files/")
def list_files(page_path: str) -> dict:
    _get_vault_root()
    normalized_page = _vault_relative_path(page_path)
    attachments = config.list_page_attachments(normalized_page)
    _log_attachment(f"Listed {len(attachments)} attachment(s) for {normalized_page}")
    return {"attachments": attachments}


@app.post("/files/delete")
def delete_files(request: Request, payload: AttachmentDeletePayload) -> dict:
    root = _get_vault_root()
    deleted: list[str] = []
    use_local_ops = _should_use_local_file_ops(request)
    seen: set[str] = set()
    for path in payload.paths:
        normalized = _vault_relative_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        _remove_attachment_copy(root, normalized, use_local_ops)
        if config.delete_attachment_entry(normalized):
            deleted.append(normalized)
    _log_attachment(f"Deleted {len(deleted)} attachment(s)")
    return {"ok": True, "deleted": deleted}


def _log_attachment(message: str) -> None:
    print(f"[Attachments] {message}")


def _vault_relative_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/").lstrip("/")
    return f"/{cleaned}" if cleaned else "/"


def _get_vault_root() -> Path:
    try:
        return vault_state.get_root()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _store_attachment(root: Path, page_path: str, upload: UploadFile, use_local_ops: bool) -> str:
    filename = Path(upload.filename).name
    if not filename:
        raise HTTPException(status_code=400, detail="Attachment filename is required")
    page_parts = Path(page_path.lstrip("/"))
    attachment_rel = page_parts.parent / filename
    attachment_normalized = f"/{attachment_rel.as_posix()}" if attachment_rel.as_posix() else f"/{filename}"
    dest_path = root / attachment_rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    log_msg = f"receive file {attachment_normalized} to vault {dest_path}"
    if use_local_ops and dest_path.exists():
        log_msg += " (server==client)"
    else:
        try:
            upload.file.seek(0)
            with dest_path.open("wb") as dest:
                shutil.copyfileobj(upload.file, dest)
        except OSError as exc:
            _log_attachment(f"Failed to persist {attachment_normalized}: {exc}")
            raise HTTPException(status_code=500, detail=f"Failed to persist attachment: {exc}") from exc
    _log_attachment(log_msg)
    config.upsert_attachment_entry(page_path, attachment_normalized, str(dest_path))
    return attachment_normalized


def _remove_attachment_copy(root: Path, attachment_path: str, use_local_ops: bool) -> None:
    target = root / attachment_path.lstrip("/")
    if not target.exists():
        _log_attachment(f"delete file {attachment_path} missing at {target}")
        return
    try:
        target.unlink()
        msg = f"delete file {attachment_path} from vault {target}"
        if use_local_ops:
            msg += " (server==client)"
        _log_attachment(msg)
    except OSError as exc:
        _log_attachment(f"Failed to delete file {attachment_path}: {exc}")


# Function to render the link
def render_link(label, target):
    # Display only the label as a hyperlink
    hyperlink = f'<a href="#" title="{target}">{label}</a>'
    return hyperlink


def get_app() -> FastAPI:
    return app
