from __future__ import annotations

import json
import platform
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence

GLOBAL_CONFIG = Path.home() / ".zimx_config.json"

_ACTIVE_CONN: Optional[sqlite3.Connection] = None
_ACTIVE_ROOT: Optional[Path] = None


def init_settings() -> None:
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)


def _read_global_config() -> dict:
    """Return the parsed global config, or an empty dict on error/missing."""
    if not GLOBAL_CONFIG.exists():
        return {}
    try:
        return json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_last_vault() -> Optional[str]:
    payload = _read_global_config()
    last = payload.get("last_vault")
    return last if isinstance(last, str) else None


def save_last_vault(path: str) -> None:
    _update_global_config({"last_vault": path})


def load_known_vaults() -> list[dict[str, str]]:
    """Load previously used vaults with display names."""
    payload = _read_global_config()
    vaults = payload.get("vaults", [])
    result: list[dict[str, str]] = []
    if isinstance(vaults, list):
        for entry in vaults:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not path:
                continue
            name = entry.get("name") or Path(path).name
            result.append({"name": str(name), "path": str(path)})
    return result


def remember_vault(path: str, name: Optional[str] = None) -> None:
    """Add or move a vault to the top of the known vault list."""
    normalized_path = str(Path(path))
    display_name = name or Path(normalized_path).name
    vaults = [v for v in load_known_vaults() if v.get("path") != normalized_path]
    vaults.insert(0, {"name": display_name, "path": normalized_path})
    _update_global_config({"vaults": vaults})


def delete_known_vault(path: str) -> None:
    """Remove a vault from the known list and clear default if it matched."""
    normalized_path = str(Path(path))
    payload = _read_global_config()
    vaults = payload.get("vaults", [])
    filtered: list[dict[str, str]] = []
    if isinstance(vaults, list):
        for entry in vaults:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("path")) == normalized_path:
                continue
            path_val = entry.get("path")
            name_val = entry.get("name") or (Path(path_val).name if path_val else None)
            if path_val:
                filtered.append({"name": str(name_val), "path": str(path_val)})
    updates: dict = {"vaults": filtered}
    if payload.get("default_vault") == normalized_path:
        updates["default_vault"] = None
    _update_global_config(updates)


def load_default_vault() -> Optional[str]:
    payload = _read_global_config()
    default_path = payload.get("default_vault")
    return default_path if isinstance(default_path, str) else None


def save_default_vault(path: Optional[str]) -> None:
    _update_global_config({"default_vault": path})


def load_vi_block_cursor_enabled() -> bool:
    """Load app-level preference for vi-mode block cursor. Defaults to True on Windows, False elsewhere."""
    if not GLOBAL_CONFIG.exists():
        return platform.system() == "Windows"
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return platform.system() == "Windows"
    if "vi_block_cursor" in payload:
        return bool(payload["vi_block_cursor"])
    return platform.system() == "Windows"


def save_vi_block_cursor_enabled(enabled: bool) -> None:
    """Save app-level preference for vi-mode block cursor."""
    _update_global_config({"vi_block_cursor": enabled})


def load_ai_chat_font_size(default: int = 13) -> int:
    """Load preferred font size for AI chat panel."""
    if not GLOBAL_CONFIG.exists():
        return default
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        return int(payload.get("ai_chat_font_size", default))
    except Exception:
        return default


def save_ai_chat_font_size(size: int) -> None:
    """Persist preferred font size for AI chat panel."""
    _update_global_config({"ai_chat_font_size": int(size)})


def load_enable_ai_chats() -> bool:
    """Load preference for enabling AI Chats tab. Defaults to False."""
    if not GLOBAL_CONFIG.exists():
        return False
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("enable_ai_chats", False))


def save_enable_ai_chats(enabled: bool) -> None:
    """Save preference for enabling AI Chats tab."""
    _update_global_config({"enable_ai_chats": bool(enabled)})


def load_default_ai_server() -> Optional[str]:
    """Load preferred default AI server for new chats."""
    payload = _read_global_config()
    server = payload.get("default_ai_server")
    return str(server) if server else None


def save_default_ai_server(name: Optional[str]) -> None:
    """Persist preferred default AI server for new chats."""
    _update_global_config({"default_ai_server": name})


def load_default_ai_model() -> Optional[str]:
    """Load preferred default AI model for new chats."""
    payload = _read_global_config()
    model = payload.get("default_ai_model")
    return str(model) if model else None


def save_default_ai_model(model: Optional[str]) -> None:
    """Persist preferred default AI model for new chats."""
    _update_global_config({"default_ai_model": model})


def load_toc_collapsed() -> bool:
    """Return whether the table-of-contents panel should start collapsed."""
    if not GLOBAL_CONFIG.exists():
        return False
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("toc_collapsed", False))


def save_toc_collapsed(collapsed: bool) -> None:
    """Persist the collapsed state of the table-of-contents panel."""
    _update_global_config({"toc_collapsed": bool(collapsed)})


def get_page_hash(path: str) -> Optional[str]:
    """Return last stored content hash for a page path, or None."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", (f"hash:{path}",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def set_page_hash(path: str, digest: str) -> None:
    """Persist content hash for a page path in kv."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", (f"hash:{path}", digest))
    conn.commit()


def load_bookmarks() -> list[str]:
    """Load bookmarked page paths. Returns list of paths."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.execute("SELECT path FROM bookmarks ORDER BY position")
        return [row[0] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return []


def save_bookmarks(paths: list[str]) -> None:
    """Save bookmarked page paths with their order."""
    conn = _get_conn()
    if not conn:
        return
    with conn:
        conn.execute("DELETE FROM bookmarks")
        conn.executemany(
            "INSERT INTO bookmarks(path, position) VALUES(?, ?)",
            ((path, idx) for idx, path in enumerate(paths))
        )


def load_show_journal() -> bool:
    """Load show_journal setting. Defaults to False (hidden)."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("show_journal",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_show_journal(show: bool) -> None:
    """Save show_journal setting."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("show_journal", "true" if show else "false"))
    conn.commit()


def load_popup_editor_geometry() -> Optional[str]:
    """Return the saved geometry for popup editors, if any."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("popup_editor_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_popup_editor_geometry(geometry: str) -> None:
    """Persist geometry for popup editors (base64 of QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("popup_editor_geometry", geometry))
    conn.commit()


def load_popup_font_size(default: int = 14) -> int:
    """Load preferred font size for popup editors."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("popup_font_size",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return max(8, int(row[0]))
    except ValueError:
        return default


def save_popup_font_size(size: int) -> None:
    """Persist preferred font size for popup editors."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("popup_font_size", str(int(size))))
    conn.commit()


def load_vault_force_read_only() -> bool:
    """Return True if this vault should be opened in read-only mode by default."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("force_read_only",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_vault_force_read_only(force: bool) -> None:
    """Persist the per-vault read-only preference."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("force_read_only", "true" if force else "false"),
    )
    conn.commit()


def load_show_future_tasks() -> bool:
    """Load preference for showing tasks that start in the future."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("show_future_tasks",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_show_future_tasks(show: bool) -> None:
    """Persist preference for showing future tasks."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("show_future_tasks", "true" if show else "false"),
    )
    conn.commit()


def load_last_file() -> Optional[str]:
    """Load the last opened file path. Returns None if no file was previously opened."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("last_file",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_last_file(path: str) -> None:
    """Save the last opened file path."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("last_file", path))
    conn.commit()


def load_recent_history() -> list[str]:
    """Load recent page history."""
    conn = _get_conn()
    if not conn:
        return []
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("recent_history",))
    row = cur.fetchone()
    if not row:
        return []
    try:
        data = json.loads(row[0])
        if isinstance(data, list):
            return [str(p) for p in data if isinstance(p, str)]
    except Exception:
        pass
    return []


def save_recent_history(history: list[str]) -> None:
    """Persist recent page history (limited to last 50 entries)."""
    conn = _get_conn()
    if not conn:
        return
    try:
        payload = json.dumps(history[:50])
    except Exception:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("recent_history", payload))
    conn.commit()


def load_recent_history_positions() -> dict[str, int]:
    """Load saved cursor positions for recent history."""
    conn = _get_conn()
    if not conn:
        return {}
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("recent_history_positions",))
    row = cur.fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, int)}
    except Exception:
        pass
    return {}


def save_recent_history_positions(positions: dict[str, int]) -> None:
    """Persist cursor positions for recent history."""
    conn = _get_conn()
    if not conn:
        return
    try:
        payload = json.dumps(positions)
    except Exception:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("recent_history_positions", payload))
    conn.commit()


def load_window_geometry() -> Optional[str]:
    """Load the saved window geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("window_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_window_geometry(geometry: str) -> None:
    """Save the window geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("window_geometry", geometry))
    conn.commit()


def load_splitter_state() -> Optional[str]:
    """Load the saved main splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("splitter_state",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_splitter_state(state: str) -> None:
    """Save the main splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("splitter_state", state))
    conn.commit()


def load_editor_splitter_state() -> Optional[str]:
    """Load the saved editor splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("editor_splitter_state",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_editor_splitter_state(state: str) -> None:
    """Save the editor splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("editor_splitter_state", state))
    conn.commit()


def load_dialog_geometry(dialog_name: str) -> Optional[str]:
    """Load the saved dialog geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", (f"{dialog_name}_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_dialog_geometry(dialog_name: str, geometry: str) -> None:
    """Save the dialog geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", (f"{dialog_name}_geometry", geometry))
    conn.commit()


def _update_global_config(updates: dict) -> None:
    """Merge updates into global config file."""
    existing = {}
    if GLOBAL_CONFIG.exists():
        try:
            existing = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(updates)
    GLOBAL_CONFIG.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def load_font_size(default: int = 14) -> int:
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("font_size",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return max(8, int(row[0]))
    except ValueError:
        return default


def save_font_size(size: int) -> None:
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("font_size", str(size)))
    conn.commit()


def save_cursor_position(path: str, position: int) -> None:
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO cursor_positions(path, position) VALUES(?, ?)", (path, position))
    conn.commit()


def load_cursor_position(path: str) -> Optional[int]:
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT position FROM cursor_positions WHERE path = ?", (path,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def update_page_index(
    path: str,
    title: str,
    tags: Iterable[str],
    links: Iterable[str],
    tasks: Sequence[dict],
) -> None:
    conn = _get_conn()
    if not conn:
        return
    now = time.time()
    with conn:
        conn.execute("REPLACE INTO pages(path, title, updated) VALUES(?, ?, ?)", (path, title, now))
        conn.execute("DELETE FROM page_tags WHERE page = ?", (path,))
        conn.executemany(
            "INSERT INTO page_tags(page, tag) VALUES(?, ?)",
            ((path, tag) for tag in tags),
        )
        conn.execute("DELETE FROM links WHERE from_path = ?", (path,))
        conn.executemany(
            "INSERT INTO links(from_path, to_path) VALUES(?, ?)",
            ((path, link) for link in links),
        )
        conn.execute("DELETE FROM tasks WHERE path = ?", (path,))
        conn.execute("DELETE FROM task_tags WHERE task_id LIKE ?", (f"{path}:%",))
        conn.executemany(
            """
            INSERT INTO tasks(task_id, path, line, text, status, priority, due, starts, parent_id, level, actionable)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    task["id"],
                    path,
                    task.get("line"),
                    task.get("text"),
                    task.get("status"),
                    task.get("priority"),
                    task.get("due"),
                    task.get("start"),
                    task.get("parent"),
                    task.get("level"),
                    1
                    if task.get("actionable", task.get("status") != "done")
                    else 0,
                )
                for task in tasks
            ),
        )
        all_tags = []
        for task in tasks:
            for tag in task.get("tags", []):
                all_tags.append((task["id"], tag))
        if all_tags:
            conn.executemany("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", all_tags)


def delete_page_index(path: str) -> None:
    """Delete a single page from the index."""
    conn = _get_conn()
    if not conn:
        return
    like = f"{path}:%"
    with conn:
        conn.execute("DELETE FROM pages WHERE path = ?", (path,))
        conn.execute("DELETE FROM page_tags WHERE page = ?", (path,))
        conn.execute("DELETE FROM links WHERE from_path = ? OR to_path = ?", (path, path))
        conn.execute("DELETE FROM tasks WHERE path = ?", (path,))
        conn.execute("DELETE FROM task_tags WHERE task_id LIKE ?", (like,))


def delete_folder_index(folder_path: str) -> None:
    """Delete all pages under a folder path (recursive) from the index.
    
    Args:
        folder_path: Folder path like /PageA/PageB (without .txt)
    """
    conn = _get_conn()
    if not conn:
        return
    
    # Clean up the folder path
    folder_prefix = folder_path.rstrip("/")
    if not folder_prefix.startswith("/"):
        folder_prefix = "/" + folder_prefix
    
    # Find all pages that start with this folder path
    # Pattern: /PageA/PageB/% will match /PageA/PageB/PageC.txt, /PageA/PageB/Sub/Sub.txt, etc.
    like_pattern = f"{folder_prefix}/%"
    
    with conn:
        # Delete all pages under this folder
        conn.execute("DELETE FROM pages WHERE path LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM page_tags WHERE page LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM links WHERE from_path LIKE ? OR to_path LIKE ?", 
                    (like_pattern, like_pattern))
        conn.execute("DELETE FROM tasks WHERE path LIKE ?", (like_pattern,))
        # For task_tags, the task_id format is "path:line", so we need to match "path:%"
        conn.execute("DELETE FROM task_tags WHERE task_id LIKE ?", (f"{folder_prefix}/%:%",))


def search_pages(term: str, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []
    like = f"%{term.lower()}%"
    cur = conn.execute(
        """
        SELECT path, title FROM pages
        WHERE lower(path) LIKE ? OR lower(title) LIKE ?
        ORDER BY updated DESC
        LIMIT ?
        """,
        (like, like, limit),
    )
    return [{"path": row[0], "title": row[1]} for row in cur.fetchall()]


def fetch_tag_summary() -> list[tuple[str, int]]:
    conn = _get_conn()
    if not conn:
        return []
    cur = conn.execute("SELECT tag, COUNT(DISTINCT page) FROM page_tags GROUP BY tag ORDER BY tag")
    return [(row[0], row[1]) for row in cur.fetchall()]


def fetch_task_tags() -> list[tuple[str, int]]:
    conn = _get_conn()
    if not conn:
        return []
    cur = conn.execute("SELECT tag, COUNT(DISTINCT task_id) FROM task_tags GROUP BY tag ORDER BY tag")
    return [(row[0], row[1]) for row in cur.fetchall()]


def fetch_tasks(
    query: str = "",
    tags: Sequence[str] = (),
    include_done: bool = False,
    include_ancestors: bool = False,
    actionable_only: bool = False,
) -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []

    select_cols = """
        SELECT
            t.task_id,
            t.path,
            t.line,
            t.text,
            t.status,
            t.priority,
            t.due,
            t.starts,
            t.parent_id,
            t.level,
            COALESCE(t.actionable, CASE WHEN t.status = 'done' THEN 0 ELSE 1 END) AS actionable
    """
    base = f"{select_cols} FROM tasks t LEFT JOIN task_tags tt ON tt.task_id = t.task_id"
    conditions = []
    params: list = []
    if query:
        conditions.append("lower(t.text) LIKE ?")
        params.append(f"%{query.lower()}%")
    if tags:
        placeholders = ",".join("?" for _ in tags)
        conditions.append(f"tt.tag IN ({placeholders})")
        params.extend(tags)
    if not include_done:
        conditions.append("t.status != 'done'")
    if actionable_only:
        conditions.append("COALESCE(t.actionable, CASE WHEN t.status = 'done' THEN 0 ELSE 1 END) = 1")
    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)
    sql = f"{base} {where} GROUP BY t.task_id ORDER BY t.path, COALESCE(t.line, 0), COALESCE(t.level, 0)"
    cur = conn.execute(sql, params)
    rows = cur.fetchall()

    def _row_to_task(row: tuple) -> dict:
        (
            task_id,
            path,
            line,
            text,
            status,
            priority,
            due,
            starts,
            parent_id,
            level,
            actionable,
        ) = row
        return {
            "id": task_id,
            "path": path,
            "line": line,
            "text": text,
            "status": status,
            "priority": priority or 0,
            "due": due,
            "starts": starts,
            "parent": parent_id,
            "level": level or 0,
            "actionable": bool(actionable),
            "tags": [],
        }

    tasks: dict[str, dict] = {}
    for row in rows:
        task = _row_to_task(row)
        tasks[task["id"]] = task

    if include_ancestors:
        missing_parents = {task["parent"] for task in tasks.values() if task.get("parent")}
        ancestor_sql_template = f"{select_cols} FROM tasks t WHERE t.task_id IN {{}}"
        while missing_parents:
            fetch_ids = [pid for pid in missing_parents if pid and pid not in tasks]
            if not fetch_ids:
                break
            placeholders = ",".join("?" for _ in fetch_ids)
            ancestor_sql = ancestor_sql_template.format(f"({placeholders})")
            ancestor_rows = conn.execute(ancestor_sql, fetch_ids).fetchall()
            for row in ancestor_rows:
                task = _row_to_task(row)
                tasks[task["id"]] = task
                if task.get("parent"):
                    missing_parents.add(task["parent"])
            missing_parents = {pid for pid in missing_parents if pid not in tasks}

    if tasks:
        all_ids = list(tasks.keys())
        placeholders = ",".join("?" for _ in all_ids)
        tag_rows = conn.execute(
            f"SELECT task_id, tag FROM task_tags WHERE task_id IN ({placeholders})", all_ids
        ).fetchall()
        for task_id, tag in tag_rows:
            if task_id in tasks:
                tasks[task_id]["tags"].append(tag)

    return sorted(
        tasks.values(),
        key=lambda t: (t.get("path") or "", t.get("line") or 0, t.get("level") or 0),
    )


def fetch_link_relations(path: str) -> dict[str, list[str]]:
    """Return incoming and outgoing links for a page path."""
    conn = _get_conn()
    if not conn or not path:
        return {"incoming": [], "outgoing": []}
    outgoing = [
        row[0]
        for row in conn.execute("SELECT to_path FROM links WHERE from_path = ?", (path,)).fetchall()
    ]
    incoming = [
        row[0]
        for row in conn.execute("SELECT from_path FROM links WHERE to_path = ?", (path,)).fetchall()
    ]
    return {"incoming": incoming, "outgoing": outgoing}


def fetch_page_titles(paths: Iterable[str]) -> dict[str, str]:
    """Return a mapping of page path -> title for the provided paths."""
    conn = _get_conn()
    if not conn:
        return {}
    unique = [p for p in set(paths) if p]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    cur = conn.execute(
        f"SELECT path, title FROM pages WHERE path IN ({placeholders})",
        unique,
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def set_active_vault(root: Optional[str]) -> None:
    global _ACTIVE_CONN, _ACTIVE_ROOT
    if _ACTIVE_CONN:
        _ACTIVE_CONN.close()
        _ACTIVE_CONN = None
    if not root:
        _ACTIVE_ROOT = None
        return
    _ACTIVE_ROOT = Path(root)
    db_dir = _ACTIVE_ROOT / ".zimx"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "settings.db"
    _ACTIVE_CONN = sqlite3.connect(db_path)
    _ensure_schema(_ACTIVE_CONN)


def has_active_vault() -> bool:
    return _ACTIVE_CONN is not None


def is_vault_index_empty() -> bool:
    """Check if the vault index is empty (no pages indexed)."""
    conn = _get_conn()
    if not conn:
        return True
    try:
        cur = conn.execute("SELECT COUNT(*) FROM pages")
        count = cur.fetchone()[0]
        return count == 0
    except sqlite3.OperationalError:
        return True


def _get_conn() -> Optional[sqlite3.Connection]:
    return _ACTIVE_CONN


def _ensure_task_columns(conn: sqlite3.Connection) -> None:
    """Add newly introduced task columns for existing vault databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "parent_id" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT")
    if "level" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN level INTEGER")
    if "actionable" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN actionable INTEGER")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS cursor_positions (
            path TEXT PRIMARY KEY,
            position INTEGER
        );
        CREATE TABLE IF NOT EXISTS pages (
            path TEXT PRIMARY KEY,
            title TEXT,
            updated REAL
        );
        CREATE TABLE IF NOT EXISTS page_tags (
            page TEXT,
            tag TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_page_tags_tag ON page_tags(tag);
        CREATE TABLE IF NOT EXISTS links (
            from_path TEXT,
            to_path TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_path);
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            path TEXT,
            line INTEGER,
            text TEXT,
            status TEXT,
            priority INTEGER,
            due TEXT,
            starts TEXT,
            parent_id TEXT,
            level INTEGER,
            actionable INTEGER
        );
        CREATE TABLE IF NOT EXISTS task_tags (
            task_id TEXT,
            tag TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task_tags_tag ON task_tags(tag);
        CREATE TABLE IF NOT EXISTS bookmarks (
            path TEXT PRIMARY KEY,
            position INTEGER
        );
        """
    )
    _ensure_task_columns(conn)
    conn.commit()
