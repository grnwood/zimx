from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence

GLOBAL_CONFIG = Path.home() / ".zimx_config.json"

_ACTIVE_CONN: Optional[sqlite3.Connection] = None
_ACTIVE_ROOT: Optional[Path] = None


def init_settings() -> None:
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)


def load_last_vault() -> Optional[str]:
    if not GLOBAL_CONFIG.exists():
        return None
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload.get("last_vault")


def save_last_vault(path: str) -> None:
    GLOBAL_CONFIG.write_text(json.dumps({"last_vault": path}, indent=2), encoding="utf-8")


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
            INSERT INTO tasks(task_id, path, line, text, status, priority, due, starts)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
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


def fetch_tasks(query: str = "", tags: Sequence[str] = (), include_done: bool = False) -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []
    base = """
        SELECT t.task_id, t.path, t.line, t.text, t.status, t.priority, t.due, t.starts
        FROM tasks t
        LEFT JOIN task_tags tt ON tt.task_id = t.task_id
    """
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
    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)
    sql = base + where + " GROUP BY t.task_id ORDER BY COALESCE(t.priority, 0) DESC, COALESCE(t.due, '9999-12-31') ASC"
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    result = []
    for row in rows:
        task_id, path, line, text, status, priority, due, starts = row
        tag_rows = conn.execute("SELECT tag FROM task_tags WHERE task_id = ?", (task_id,)).fetchall()
        result.append(
            {
                "id": task_id,
                "path": path,
                "line": line,
                "text": text,
                "status": status,
                "priority": priority or 0,
                "due": due,
                "starts": starts,
                "tags": [t[0] for t in tag_rows],
            }
        )
    return result


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


def _get_conn() -> Optional[sqlite3.Connection]:
    return _ACTIVE_CONN


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
            starts TEXT
        );
        CREATE TABLE IF NOT EXISTS task_tags (
            task_id TEXT,
            tag TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task_tags_tag ON task_tags(tag);
        """
    )
    conn.commit()
