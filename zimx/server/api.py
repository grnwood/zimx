from __future__ import annotations
 # --- Fix for FastAPI + PyInstaller + python-multipart ---
try:
    import importlib

    multipart = importlib.import_module("multipart")
    # FastAPI checks for multipart.__version__ to verify python-multipart
    if not getattr(multipart, "__version__", None):
        try:
            # Try to get the real version from the installed dist
            import pkg_resources
            multipart.__version__ = pkg_resources.get_distribution("python-multipart").version
        except Exception:
            # Fallback: any non-empty string will satisfy FastAPI's check
            multipart.__version__ = "0.0.0"
except ImportError:
    # If multipart truly isn't installed, FastAPI will still raise a clear error later
    pass
 # --- end fix ---

import os
import shutil
import traceback
from pathlib import Path
from typing import Iterable, List, Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, File as FastAPISingleFile, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict

from . import indexer
from . import file_ops
from .adapters import files, tasks
from .adapters.files import FileAccessError
from .state import vault_state
from .vector import vector_manager
from zimx.rag.index import RetrievedChunk
from zimx.app import config
from datetime import date as Date

_ANSI_BLUE = "\033[94m"
_ANSI_RESET = "\033[0m"

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


class FileDeletePayload(BaseModel):
    path: str
    version: Optional[int] = None


class RenameMovePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_path: str = Field(..., alias="from")
    to_path: str = Field(..., alias="to")
    version: Optional[int] = None


class UpdateLinksPayload(BaseModel):
    path_map: dict[str, str]


class ModifiedRangePayload(BaseModel):
    start_date: str
    end_date: str


class AttachmentDeletePayload(BaseModel):
    paths: List[str] = Field(..., description="Vault-relative attachment paths to delete")


class VectorAddPayload(BaseModel):
    page_ref: str
    text: str
    kind: Literal["page", "attachment"] = "page"
    attachment_name: Optional[str] = None


class VectorRemovePayload(BaseModel):
    page_ref: str
    kind: Literal["page", "attachment"] = "page"
    attachment_name: Optional[str] = None


class VectorQueryPayload(BaseModel):
    query_text: str
    kind: Literal["page", "attachment"] = "page"
    page_refs: Optional[List[str]] = None
    attachment_names: Optional[List[str]] = None
    limit: int = 4


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
def vault_tree(path: str = "/", recursive: bool = True) -> dict:
    root = vault_state.get_root()
    tree = files.list_dir(root, subpath=path, recursive=recursive)
    order_map = config.fetch_display_order_map()
    _sort_tree_nodes(tree, order_map)
    version = config.get_tree_version()
    print(f"{_ANSI_BLUE}[API] GET /api/vault/tree path={path} recursive={recursive} version={version}{_ANSI_RESET}")
    return {"root": str(root), "tree": tree, "version": version}


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


@app.post("/api/files/modified")
def files_modified(payload: ModifiedRangePayload) -> dict:
    try:
        start = Date.fromisoformat(payload.start_date)
        end = Date.fromisoformat(payload.end_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}") from exc
    print(f"{_ANSI_BLUE}[API] POST /api/files/modified {payload.start_date} -> {payload.end_date}{_ANSI_RESET}")
    root = vault_state.get_root()
    try:
        items = files.list_files_modified_between(root, start, end)
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}


@app.post("/api/journal/today")
def journal_today(payload: JournalPayload) -> dict:
    root = vault_state.get_root()
    # Pass template through so the initial content becomes the user's day template
    target, created = files.ensure_journal_today(root, template=payload.template)
    rel = f"/{target.relative_to(root).as_posix()}"
    return {"path": rel, "created": created}


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
    page_path: Optional[str] = None
    try:
        if payload.is_dir:
            files.create_directory(root, payload.path)
            page_path = config.folder_to_page_path(payload.path)
        else:
            files.create_markdown_file(root, payload.path, payload.content or "")
            page_path = payload.path
        if page_path:
            config.ensure_page_entry(page_path)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/path/delete")
def delete_path(payload: DeletePathPayload) -> dict:
    root = vault_state.get_root()
    try:
        result = file_ops.delete_folder(root, payload.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.options("/api/file/operation")
def file_operation_options(path: str, op: Literal["rename", "move", "delete"], dest: Optional[str] = None) -> dict:
    root = vault_state.get_root()
    ok, reason = file_ops.preflight(root, op, path, dest)
    return {"canOperate": ok, "reason": reason}


@app.post("/api/file/rename")
def file_rename(payload: RenameMovePayload) -> dict:
    root = vault_state.get_root()
    ok, reason = file_ops.preflight(root, "rename", payload.from_path, payload.to_path)
    if not ok:
        raise HTTPException(status_code=400, detail=reason or "Preflight failed")
    try:
        result = file_ops.rename_folder(root, payload.from_path, payload.to_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/file/move")
def file_move(payload: RenameMovePayload) -> dict:
    print(f"{_ANSI_BLUE}[API] POST /api/file/move from={payload.from_path} to={payload.to_path}{_ANSI_RESET}")
    root = vault_state.get_root()
    ok, reason = file_ops.preflight(root, "move", payload.from_path, payload.to_path)
    if not ok:
        print(f"{_ANSI_BLUE}[API] /api/file/move preflight failed: {reason}{_ANSI_RESET}")
        raise HTTPException(status_code=400, detail=reason or "Preflight failed")
    try:
        result = file_ops.move_folder(root, payload.from_path, payload.to_path)
    except FileNotFoundError as exc:
        print(f"{_ANSI_BLUE}[API] /api/file/move not found: {exc}{_ANSI_RESET}")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileAccessError as exc:
        print(f"{_ANSI_BLUE}[API] /api/file/move error: {exc}{_ANSI_RESET}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.delete("/api/file")
def file_delete(payload: FileDeletePayload) -> dict:
    root = vault_state.get_root()
    ok, reason = file_ops.preflight(root, "delete", payload.path)
    if not ok:
        raise HTTPException(status_code=400, detail=reason or "Preflight failed")
    try:
        result = file_ops.delete_folder(root, payload.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/vault/update-links")
def vault_update_links(payload: UpdateLinksPayload) -> dict:
    root = vault_state.get_root()
    touched = file_ops.update_links_on_disk(root, payload.path_map)
    return {"ok": True, "touched": touched}


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


@app.post("/vector/add")
def vector_add(payload: VectorAddPayload) -> dict:
    root = _get_vault_root()
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Text must not be empty")
    try:
        vector_manager.index_text(root, payload.page_ref, payload.text, payload.kind, payload.attachment_name)
        _log_vector(f"Added vector entry for {payload.page_ref} ({payload.kind})")
    except Exception as exc:
        _handle_vector_exception("indexing vector data", exc)
    return {"ok": True}


@app.post("/vector/remove")
def vector_remove(payload: VectorRemovePayload) -> dict:
    root = _get_vault_root()
    try:
        vector_manager.delete_text(root, payload.page_ref, payload.kind, payload.attachment_name)
        _log_vector(f"Removed vector entry for {payload.page_ref} ({payload.kind})")
    except Exception as exc:
        _handle_vector_exception("removing vector data", exc)
    return {"ok": True}


def _chunk_to_dict(chunk: RetrievedChunk) -> dict:
    return {
        "page_ref": chunk.page_ref,
        "content": chunk.content,
        "score": chunk.score,
        "attachment_name": chunk.attachment_name,
    }


@app.post("/vector/query")
def vector_query(payload: VectorQueryPayload) -> dict:
    root = _get_vault_root()
    try:
        if payload.kind == "attachment":
            if not payload.attachment_names:
                raise HTTPException(status_code=400, detail="Attachment names required for attachment query")
            chunks = vector_manager.query_attachments(root, payload.query_text, payload.attachment_names, limit=payload.limit)
        else:
            chunks = vector_manager.query(root, payload.query_text, page_refs=payload.page_refs, limit=payload.limit)
        _log_vector(
            f"Queried {payload.kind} context limit={payload.limit} "
            f"pages={payload.page_refs or 'any'} "
            f"attachments={payload.attachment_names or 'any'}"
        )
    except HTTPException:
        raise
    except Exception as exc:
        _handle_vector_exception("querying vector data", exc)
    return {"chunks": [_chunk_to_dict(chunk) for chunk in chunks]}


def _sort_tree_nodes(nodes: list[dict], order_map: dict[str, int]) -> None:
    """Sort tree nodes in-place using display order, defaulting to alpha."""
    for node in nodes:
        children = node.get("children") or []
        _sort_tree_nodes(children, order_map)
        node["children"] = children

    def _key(node: dict) -> tuple:
        open_path = node.get("open_path")
        order_val = order_map.get(open_path) if open_path else None
        return (order_val if order_val is not None else float("inf"), (node.get("name") or "").lower())

    nodes.sort(key=_key)


def _log_attachment(message: str) -> None:
    print(f"[Attachments] {message}")


def _log_vector(message: str) -> None:
    print(f"[Vector] {message}")


def _handle_vector_exception(context: str, exc: Exception) -> None:
    _log_vector(f"{context} failed: {exc}")
    traceback.print_exc()
    raise HTTPException(status_code=500, detail=str(exc)) from exc


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
