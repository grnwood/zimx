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

import copy
import os
import shutil
import traceback
from pathlib import Path
from typing import Iterable, List, Literal, Optional
from datetime import datetime, timedelta
from functools import wraps
import secrets

import httpx
from fastapi import FastAPI, HTTPException, File as FastAPISingleFile, Form, Query, Request, UploadFile, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, ConfigDict
from jose import JWTError, jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from . import indexer
from . import file_ops
from . import search_index
from .adapters import files
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
_TASKS_CACHE: dict[tuple[str, tuple[str, ...], Optional[str]], list[dict]] = {}
_TASK_CACHE_VERSION: int = -1

_TREE_CACHE: dict[tuple[str, str, bool], dict[str, object]] = {}


def _normalize_tree_path(path: str) -> str:
    cleaned = (path or "/").strip().replace("\\", "/")
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    if cleaned != "/":
        cleaned = cleaned.rstrip("/") or "/"
    return cleaned or "/"


def _get_cached_tree(root: Path, path: str, recursive: bool, version: int) -> list[dict] | None:
    key = (str(root), path, recursive)
    cached = _TREE_CACHE.get(key)
    if not cached:
        return None
    if cached.get("version") != version:
        _TREE_CACHE.pop(key, None)
        return None
    try:
        return copy.deepcopy(cached["tree"])
    except Exception:
        return None


def _set_cached_tree(root: Path, path: str, recursive: bool, version: int, tree: list[dict]) -> None:
    _TREE_CACHE[(str(root), path, recursive)] = {"version": version, "tree": copy.deepcopy(tree)}


def _clear_tree_cache() -> None:
    _TREE_CACHE.clear()


# ===== JWT Authentication Configuration =====
JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(32)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() in ("true", "1", "yes")

password_hasher = PasswordHasher()
security = HTTPBearer(auto_error=False)


class AuthModels:
    class SetupRequest(BaseModel):
        username: str = Field(..., min_length=3, max_length=50)
        password: str = Field(..., min_length=8)

    class LoginRequest(BaseModel):
        username: str
        password: str

    class TokenResponse(BaseModel):
        access_token: str
        refresh_token: str
        token_type: str = "bearer"

    class UserInfo(BaseModel):
        username: str
        is_admin: bool = True


def _create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        password_hasher.verify(hashed_password, plain_password)
        return True
    except VerifyMismatchError:
        return False


def _hash_password(password: str) -> str:
    return password_hasher.hash(password)


def _get_auth_config():
    """Get auth configuration from vault's kv store"""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        return None
    if not vault_root:
        return None
    db_path = vault_root / ".zimx" / "settings.db"
    if not db_path.exists():
        return None
    
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT value FROM kv WHERE key = 'auth_config'")
        row = cursor.fetchone()
        if row:
            import json
            return json.loads(row[0])
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _set_auth_config(username: str, password_hash: str):
    """Store auth configuration in vault's kv store"""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=500, detail="No vault selected")
    if not vault_root:
        raise HTTPException(status_code=500, detail="No vault selected")
    db_path = vault_root / ".zimx" / "settings.db"
    import sqlite3
    import json
    
    config = {
        "username": username,
        "password_hash": password_hash,
        "configured_at": datetime.utcnow().isoformat()
    }
    
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
            ("auth_config", json.dumps(config))
        )
        conn.commit()
    finally:
        conn.close()


async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[AuthModels.UserInfo]:
    """Dependency to get current authenticated user"""
    if not AUTH_ENABLED:
        return AuthModels.UserInfo(username="admin", is_admin=True)
    
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return AuthModels.UserInfo(username=username, is_admin=True)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _filter_out_journal(tree: list[dict]) -> list[dict]:
    """Remove Journal folder/page from the top-level navigation tree."""
    filtered: list[dict] = []
    for node in tree:
        if node.get("name") == "Journal" or node.get("path") == "/Journal":
            continue
        if node.get("path") == "/":
            children = []
            for child in node.get("children") or []:
                if child.get("name") == "Journal" or child.get("path") == "/Journal":
                    continue
                children.append(child)
            node = {**node, "children": children}
        filtered.append(node)
    return filtered


def _should_use_local_file_ops(request: Request) -> bool:
    if not _LOCAL_FILE_OPS_ENABLED:
        return False
    client = request.client
    if not client:
        return False
    return client.host in _LOCAL_HOSTS


def _clear_task_cache() -> None:
    global _TASK_CACHE_VERSION
    _TASKS_CACHE.clear()
    _TASK_CACHE_VERSION = -1


def _normalize_tags(raw_tags: Optional[List[str]]) -> tuple[str, ...]:
    if not raw_tags:
        return ()
    seen: list[str] = []
    for raw in raw_tags:
        for chunk in raw.split(","):
            tag = chunk.strip()
            if tag and tag not in seen:
                seen.append(tag)
    return tuple(seen)


def _normalize_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized in ("todo", "done"):
        return normalized
    if normalized == "all" or normalized == "":
        return None
    raise HTTPException(status_code=400, detail="Status must be one of: todo, done, all")


def _fetch_tasks(query: str, tags: tuple[str, ...], status: Optional[str]) -> list[dict]:
    global _TASK_CACHE_VERSION
    current_version = config.get_task_index_version()
    if _TASK_CACHE_VERSION != current_version:
        _clear_task_cache()
        _TASK_CACHE_VERSION = current_version
    cache_key = (query, tags, status)
    if cache_key in _TASKS_CACHE:
        return _TASKS_CACHE[cache_key]
    include_done = status != "todo"
    tasks_from_db = config.fetch_tasks(
        query=query,
        tags=tags,
        include_done=include_done,
        include_ancestors=False,
    )
    if status == "done":
        tasks_from_db = [task for task in tasks_from_db if (task.get("status") or "").lower() == "done"]
    elif status == "todo":
        tasks_from_db = [task for task in tasks_from_db if (task.get("status") or "").lower() != "done"]
    _TASKS_CACHE[cache_key] = tasks_from_db
    return tasks_from_db


def _serialize_task(task: dict) -> dict:
    status = (task.get("status") or "todo").lower()
    done = status == "done"
    return {
        "id": task.get("id"),
        "path": task.get("path"),
        "line": task.get("line"),
        "text": task.get("text") or "",
        "status": status,
        "done": done,
        "priority": task.get("priority") or 0,
        "due": task.get("due"),
        "starts": task.get("starts"),
        "parent": task.get("parent"),
        "level": task.get("level") or 0,
        "tags": task.get("tags") or [],
        "actionable": task.get("actionable", not done),
    }


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


class ReorderPayload(BaseModel):
    parent_path: str
    page_order: List[str]


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
    allow_origins=[
        "http://127.0.0.1",
        "http://localhost",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "null"
    ],
    allow_origin_regex=r"^https?://(127\\.0\\.0\\.1|localhost)(:\\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ===== Authentication Endpoints =====

@app.post("/auth/setup", response_model=AuthModels.TokenResponse)
def auth_setup(payload: AuthModels.SetupRequest) -> dict:
    """First-time password setup. Only works when no password is configured."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=400, detail="No vault selected. Select a vault first.")
    if not vault_root:
        raise HTTPException(status_code=400, detail="No vault selected. Select a vault first.")
    
    auth_config = _get_auth_config()
    if auth_config:
        raise HTTPException(status_code=400, detail="Authentication already configured")
    
    # Hash password and store
    password_hash = _hash_password(payload.password)
    _set_auth_config(payload.username, password_hash)
    
    # Generate tokens
    access_token = _create_token(
        {"sub": payload.username},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = _create_token(
        {"sub": payload.username, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/auth/login", response_model=AuthModels.TokenResponse)
def auth_login(payload: AuthModels.LoginRequest) -> dict:
    """Login with username and password."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=400, detail="No vault selected")
    if not vault_root:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured. Use /auth/setup first.")
    
    # Verify credentials
    if payload.username != auth_config["username"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not _verify_password(payload.password, auth_config["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Generate tokens
    access_token = _create_token(
        {"sub": payload.username},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = _create_token(
        {"sub": payload.username, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/auth/refresh", response_model=AuthModels.TokenResponse)
def auth_refresh(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Refresh access token using refresh token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Generate new tokens
        access_token = _create_token(
            {"sub": username},
            timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        refresh_token = _create_token(
            {"sub": username, "type": "refresh"},
            timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        )
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@app.post("/auth/logout")
def auth_logout(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    """Logout (client should discard tokens)."""
    return {"ok": True, "message": "Logged out successfully"}


@app.get("/auth/me", response_model=AuthModels.UserInfo)
def auth_me(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    """Get current user info."""
    return user.model_dump()


@app.get("/auth/status")
def auth_status() -> dict:
    """Check if authentication is configured and enabled."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        return {"configured": False, "enabled": AUTH_ENABLED, "vault_selected": False}
    if not vault_root:
        return {"configured": False, "enabled": AUTH_ENABLED, "vault_selected": False}
    
    auth_config = _get_auth_config()
    return {
        "configured": auth_config is not None,
        "enabled": AUTH_ENABLED,
        "vault_selected": True
    }


@app.post("/api/vault/select")
def select_vault(payload: VaultSelectPayload) -> dict:
    try:
        root = vault_state.set_root(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _clear_tree_cache()
    try:
        config.set_active_vault(str(root))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to initialize vault: {exc}") from exc
    _clear_task_cache()
    return {"root": str(root)}


@app.get("/api/vault/tree")
def vault_tree(path: str = "/", recursive: bool = True) -> dict:
    root = vault_state.get_root()
    version = config.get_tree_version()
    normalized_path = _normalize_tree_path(path)
    tree = _get_cached_tree(root, normalized_path, recursive, version)
    cache_hit = tree is not None
    if not cache_hit:
        tree = files.list_dir(root, subpath=normalized_path, recursive=recursive)
        if normalized_path in ("/", ""):
            tree = _filter_out_journal(tree)
        order_map = config.fetch_display_order_map()
        if normalized_path == "/":
            print(f"{_ANSI_BLUE}[API] Root order_map sample: {list(order_map.items())[:5]}{_ANSI_RESET}")
        _sort_tree_nodes(tree, order_map)
        if normalized_path == "/" and tree:
            print(f"{_ANSI_BLUE}[API] Root tree order after sort: {[n.get('name') for n in tree[:5]]}{_ANSI_RESET}")
        _set_cached_tree(root, normalized_path, recursive, version, tree)
    print(
        f"{_ANSI_BLUE}[API] GET /api/vault/tree path={normalized_path} recursive={recursive} "
        f"version={version} cached={cache_hit}{_ANSI_RESET}"
    )
    return {"root": str(root), "tree": tree, "version": version}


@app.get("/api/vault/stats")
def vault_stats() -> dict:
    """Get vault statistics including folder count for lazy loading decisions."""
    root = vault_state.get_root()
    folder_count = config.count_folders()
    print(f"{_ANSI_BLUE}[API] GET /api/vault/stats folder_count={folder_count}{_ANSI_RESET}")
    return {"folder_count": folder_count}


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
def file_write(
    payload: FileWritePayload,
    if_match: Optional[str] = Header(None),
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    root = vault_state.get_root()
    
    # Check If-Match header for conflict detection
    if if_match is not None:
        db_path = config._vault_db_path()
        if db_path:
            import sqlite3
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                row = conn.execute(
                    "SELECT rev, title FROM pages WHERE path = ?",
                    (payload.path,)
                ).fetchone()
                
                if row:
                    current_rev = row[0] or 0
                    try:
                        expected_rev = int(if_match)
                    except ValueError:
                        conn.close()
                        raise HTTPException(status_code=400, detail="Invalid If-Match header format")
                    
                    if current_rev != expected_rev:
                        # Conflict: return current state
                        try:
                            current_content = files.read_file(root, payload.path)
                        except FileAccessError:
                            current_content = ""
                        
                        conn.close()
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "error": "Conflict",
                                "current_rev": current_rev,
                                "current_content": current_content,
                                "current_title": row[1]
                            }
                        )
            finally:
                conn.close()
    
    try:
        files.write_file(root, payload.path, payload.content)
        # Update search index
        db_path = config._vault_db_path()
        if db_path:
            import sqlite3
            import time
            conn = sqlite3.connect(db_path, check_same_thread=False)
            search_index.upsert_page(conn, payload.path, int(time.time()), payload.content)
            conn.close()
            
            # Get new revision
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                row = conn.execute("SELECT rev FROM pages WHERE path = ?", (payload.path,)).fetchone()
                new_rev = row[0] if row else 0
                return {"ok": True, "rev": new_rev}
            finally:
                conn.close()
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
def api_tasks(
    query: Optional[str] = None,
    tags: Optional[List[str]] = Query(None),
    status: Optional[str] = None,
) -> dict:
    _get_vault_root()
    normalized_query = (query or "").strip()
    normalized_tags = _normalize_tags(tags)
    normalized_status = _normalize_status(status)
    task_rows = _fetch_tasks(normalized_query, normalized_tags, normalized_status)
    return {"items": [_serialize_task(task) for task in task_rows]}


@app.get("/api/search")
def api_search(
    q: Optional[str] = None,
    subtree: Optional[str] = None,
    limit: int = 50
) -> dict:
    """Full-text search across all pages using FTS5."""
    subtree_str = f" subtree={subtree}" if subtree else ""
    print(f"{_ANSI_BLUE}[API] GET /api/search q={q}{subtree_str} limit={limit}{_ANSI_RESET}")
    
    if not q or not q.strip():
        return {"results": []}
    
    db_path = config._vault_db_path()
    if not db_path:
        return {"results": []}
    
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, check_same_thread=False)
        results = search_index.search_pages(conn, q, subtree, limit)
        conn.close()
        return {"results": results}
    except Exception as e:
        print(f"[API] Search error: {e}")
        return {"results": []}


# ===== Web Sync API Endpoints =====

@app.get("/sync/changes")
def sync_changes(
    since_rev: int = 0,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get all pages changed since a given sync revision.
    
    Returns pages with rev > since_rev, including deleted pages.
    """
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        current_sync_rev = config.get_sync_revision()
        
        # Get changed pages (including deleted ones)
        rows = conn.execute(
            """
            SELECT page_id, path, title, updated, rev, deleted, pinned, parent_path
            FROM pages
            WHERE rev > ?
            ORDER BY rev ASC
            """,
            (since_rev,)
        ).fetchall()
        
        changes = []
        for row in rows:
            changes.append({
                "page_id": row[0],
                "path": row[1],
                "title": row[2],
                "updated": row[3],
                "rev": row[4],
                "deleted": bool(row[5]),
                "pinned": bool(row[6]),
                "parent_path": row[7]
            })
        
        return {
            "sync_revision": current_sync_rev,
            "changes": changes,
            "has_more": False
        }
    finally:
        conn.close()


@app.get("/recent")
def get_recent_pages(
    limit: int = 20,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get recently modified pages."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            """
            SELECT page_id, path, title, updated, rev
            FROM pages
            WHERE deleted = 0
            ORDER BY updated DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        
        pages = []
        for row in rows:
            pages.append({
                "page_id": row[0],
                "path": row[1],
                "title": row[2],
                "updated": row[3],
                "rev": row[4]
            })
        
        return {"pages": pages}
    finally:
        conn.close()


@app.get("/tags")
def get_all_tags(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    """Get all tags with page counts."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            """
            SELECT tag, COUNT(DISTINCT page) as count
            FROM page_tags
            WHERE page IN (SELECT path FROM pages WHERE deleted = 0)
            GROUP BY tag
            ORDER BY tag
            """
        ).fetchall()
        
        tags = [{"tag": row[0], "count": row[1]} for row in rows]
        return {"tags": tags}
    finally:
        conn.close()


@app.get("/pages/{page_id}/links")
def get_page_links(
    page_id: str,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get outgoing links from a page."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        # Get page path from page_id
        row = conn.execute("SELECT path FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Page not found")
        
        from_path = row[0]
        
        # Get outgoing links
        rows = conn.execute(
            "SELECT to_path FROM links WHERE from_path = ?",
            (from_path,)
        ).fetchall()
        
        links = [row[0] for row in rows]
        return {"links": links}
    finally:
        conn.close()


@app.get("/pages/{page_id}/backlinks")
def get_page_backlinks(
    page_id: str,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get incoming links (backlinks) to a page."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        # Get page path from page_id
        row = conn.execute("SELECT path FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Page not found")
        
        to_path = row[0]
        
        # Get backlinks
        rows = conn.execute(
            "SELECT from_path FROM links WHERE to_path = ?",
            (to_path,)
        ).fetchall()
        
        backlinks = [row[0] for row in rows]
        return {"backlinks": backlinks}
    finally:
        conn.close()


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
    version = config.get_tree_version()
    try:
        if payload.is_dir:
            files.create_directory(root, payload.path)
            page_path = config.folder_to_page_path(payload.path)
        else:
            files.create_markdown_file(root, payload.path, payload.content or "")
            page_path = payload.path
        if page_path:
            config.ensure_page_entry(page_path)
            # Update search index for new page
            db_path = config._vault_db_path()
            if db_path:
                import sqlite3
                import time
                conn = sqlite3.connect(db_path, check_same_thread=False)
                content = payload.content or ""
                search_index.upsert_page(conn, page_path, int(time.time()), content)
                conn.close()
        version = config.bump_tree_version()
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "version": version}


@app.post("/api/path/delete")
def delete_path(payload: DeletePathPayload) -> dict:
    root = vault_state.get_root()
    try:
        result = file_ops.delete_folder(root, payload.path)
        # Remove from search index
        db_path = config._vault_db_path()
        if db_path:
            import sqlite3
            conn = sqlite3.connect(db_path, check_same_thread=False)
            search_index.delete_page(conn, payload.path)
            conn.close()
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


@app.post("/api/tree/reorder")
def tree_reorder(payload: ReorderPayload) -> dict:
    """Reorder pages within a parent folder without moving files."""
    _get_vault_root()
    print(f"{_ANSI_BLUE}[API] POST /api/tree/reorder parent={payload.parent_path} count={len(payload.page_order)}{_ANSI_RESET}")
    try:
        config.reorder_pages(payload.parent_path, payload.page_order)
        version = config.bump_tree_version()
        _clear_tree_cache()
        print(f"{_ANSI_BLUE}[API] Reordered {len(payload.page_order)} items, new version={version}{_ANSI_RESET}")
    except Exception as exc:
        print(f"{_ANSI_BLUE}[API] Reorder failed: {exc}{_ANSI_RESET}")
        raise HTTPException(status_code=500, detail=f"Failed to reorder: {exc}") from exc
    return {"ok": True, "version": version}


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


if __name__ == "__main__":
    import uvicorn
    
    print(f"\n{_ANSI_BLUE}=== ZimX API Server ==={_ANSI_RESET}")
    print(f"{_ANSI_BLUE}Starting server on http://127.0.0.1:8000{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}API docs: http://127.0.0.1:8000/docs{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}Auth enabled: {AUTH_ENABLED}{_ANSI_RESET}\n")
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info"
    )
