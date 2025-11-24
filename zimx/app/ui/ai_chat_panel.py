
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
import html
import re
from typing import Dict, List, Optional, Tuple

import httpx
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QUrl
from PySide6.QtGui import QPalette, QTextCursor, QKeyEvent, QDesktopServices, QCursor
from markdown import markdown

# Use zimx_config for global config storage
from zimx.app import config as zimx_config

# Shared config (aligns with slipstream/ask-server/ask-client.py defaults)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_CONFIG_FILE = PROJECT_ROOT / "slipstream" / "server_configs.json"
DEFAULT_API_URL = os.getenv("PUBLISHED_API", "http://localhost:3000")
DEFAULT_API_SECRET = os.getenv("API_SECRET_TOKEN", "my-secret-token")

def fetch_and_cache_models(server_config: dict) -> List[str]:
    """Fetch models from the server and cache them in the config file under 'server_models'."""
    server = server_config or {}
    fallback_model = server.get("default_model") or "gpt-3.5-turbo"
    base_url = server.get("base_url", "")
    if not base_url or not server.get("name"):
        return [fallback_model]
    models_path = server.get("models_path") or ("/mods" if server.get("auth_mode") == "proxy" else "/v1/models")
    url = compose_url(base_url, models_path)
    headers = build_auth_headers(server)
    verify = bool(server.get("verify_ssl", True))
    try:
        print("[AIChat][models request]", {"url": url, "headers": headers})
        with httpx.Client(timeout=10.0, verify=verify) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            print(f"[AIChat][models response] {resp.status_code} {resp.text[:500]}")
            payload = resp.json()
    except Exception as exc:
        print(f"Error fetching models from {url}: {exc}")
        payload = None
    model_ids: List[str] = []
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], list):
            data_list = payload["data"]
            if data_list and isinstance(data_list[0], dict):
                model_ids = [item.get("id") for item in data_list if item.get("id")]
            else:
                model_ids = [str(item) for item in data_list if item]
        elif "models" in payload and isinstance(payload["models"], list):
            model_ids = [str(item) for item in payload["models"] if item]
    elif isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            model_ids = [item.get("id") for item in payload if item.get("id")]
        else:
            model_ids = [str(item) for item in payload if item]
    cleaned = sorted({m for m in model_ids if m})
    if not cleaned:
        cleaned = [fallback_model]
    # Cache in config
    payload = zimx_config._read_global_config()
    server_models = payload.get("server_models", {})
    server_models[server["name"]] = cleaned
    zimx_config._update_global_config({"server_models": server_models})
    return cleaned



import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QUrl
from PySide6.QtGui import QPalette, QTextCursor, QKeyEvent, QDesktopServices
from markdown import markdown

# Use zimx_config for global config storage
from zimx.app import config as zimx_config

# Shared config (aligns with slipstream/ask-server/ask-client.py defaults)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_CONFIG_FILE = PROJECT_ROOT / "slipstream" / "server_configs.json"
DEFAULT_API_URL = os.getenv("PUBLISHED_API", "http://localhost:3000")
DEFAULT_API_SECRET = os.getenv("API_SECRET_TOKEN", "my-secret-token")


def normalize_base_url(url: str) -> str:
    if not url:
        return ""
    return url.rstrip("/")


def compose_url(base_url: str, path: str) -> str:
    base = normalize_base_url(base_url)
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(value)


def build_default_server_configs():
    servers = [
        {
            "name": "Proxy Server",
            "base_url": normalize_base_url(DEFAULT_API_URL),
            "auth_mode": "proxy",
            "api_secret": DEFAULT_API_SECRET,
            "api_key": "",
            "models_path": "/mods",
            "chat_path": "/v1/chat/completions",
            "verify_ssl": False,
            "default_model": "gpt-3.5-turbo",
            "auto_summarize": True,
            "timeout": "",
        }
    ]
    return servers


class ServerManager:
    SERVERS_KEY = "servers"
    ACTIVE_SERVER_KEY = "active_server"

    def __init__(self, config_path: Optional[Path] = None):
        self._servers_cache: Optional[List[dict]] = None

    # No longer needed: _load_file and _save_file

    def _normalize_server(self, entry: dict) -> dict:
        entry = entry or {}
        auth_mode = entry.get("auth_mode") or ("proxy" if entry.get("api_secret") else "openai")
        base_url = normalize_base_url(entry.get("base_url", ""))
        models_path = entry.get("models_path") or ("/mods" if auth_mode == "proxy" else "/v1/models")
        chat_path = entry.get("chat_path") or "/v1/chat/completions"
        name = entry.get("name") or (base_url or "Server")
        default_model = entry.get("default_model") or "gpt-3.5-turbo"
        return {
            "name": name,
            "base_url": base_url,
            "auth_mode": auth_mode,
            "api_secret": entry.get("api_secret", ""),
            "api_key": entry.get("api_key", ""),
            "custom_header_name": entry.get("custom_header_name", ""),
            "custom_header_value": entry.get("custom_header_value", ""),
            "models_path": models_path,
            "chat_path": chat_path,
            "verify_ssl": bool(entry.get("verify_ssl", True)),
            "default_model": default_model,
            "auto_summarize": to_bool(entry.get("auto_summarize"), default=True),
            "timeout": entry.get("timeout", ""),
        }

    def load_servers(self) -> List[dict]:
        if self._servers_cache is not None:
            return list(self._servers_cache)
        payload = zimx_config._read_global_config()
        servers = payload.get(self.SERVERS_KEY, [])
        if not servers:
            servers = build_default_server_configs()
        normalized = [self._normalize_server(entry) for entry in servers if entry]
        self._servers_cache = normalized
        if not payload.get(self.SERVERS_KEY):
            zimx_config._update_global_config({self.SERVERS_KEY: normalized})
        return list(normalized)

    def list_server_names(self) -> List[str]:
        return [server["name"] for server in self.load_servers()]

    def get_server(self, name: Optional[str]) -> Optional[dict]:
        if not name:
            return None
        for server in self.load_servers():
            if server["name"] == name:
                return server
        return None

    def set_active_server(self, name: str) -> None:
        if name and self.get_server(name):
            zimx_config._update_global_config({self.ACTIVE_SERVER_KEY: name})
            self._servers_cache = None

    def get_active_server_name(self) -> Optional[str]:
        payload = zimx_config._read_global_config()
        active = payload.get(self.ACTIVE_SERVER_KEY)
        names = self.list_server_names()
        if active in names:
            return active
        if names:
            self.set_active_server(names[0])
            return names[0]
        return None

    def add_or_update_server(self, server: dict) -> dict:
        servers = self.load_servers()
        target_name = server.get("name")
        original_name = server.get("original_name")
        updated = False
        for idx, existing in enumerate(servers):
            if existing["name"] == target_name or (original_name and existing["name"] == original_name):
                servers[idx] = self._normalize_server(server)
                updated = True
                break
        if not updated:
            if any(existing["name"] == target_name for existing in servers):
                raise ValueError(f"A server named '{target_name}' already exists.")
            servers.append(self._normalize_server(server))
        payload = zimx_config._read_global_config()
        if original_name and payload.get(self.ACTIVE_SERVER_KEY) == original_name:
            zimx_config._update_global_config({self.ACTIVE_SERVER_KEY: target_name})
        zimx_config._update_global_config({self.SERVERS_KEY: servers})
        self._servers_cache = None
        return self.get_server(target_name) or self._normalize_server(server)

    def delete_server(self, name: str) -> List[dict]:
        servers = [srv for srv in self.load_servers() if srv["name"] != name]
        if not servers:
            servers = build_default_server_configs()
        zimx_config._update_global_config({self.SERVERS_KEY: servers})
        active_name = self.get_active_server_name()
        if active_name == name and servers:
            zimx_config._update_global_config({self.ACTIVE_SERVER_KEY: servers[0]["name"]})
        self._servers_cache = None
        return self.load_servers()


class AIChatStore:
    """Persist chats and messages in a per-vault .zimx/ai-chats.db."""

    def __init__(self, db_path: Optional[Path] = None, vault_root: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path)
        elif vault_root:
            self.db_path = Path(vault_root) / ".zimx" / "ai-chats.db"
        else:
            self.db_path = Path.home() / ".zimx" / "ai-chats.db"
        self._ensure_db()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER,
                path TEXT,
                type TEXT DEFAULT 'chat',
                last_model TEXT,
                last_server TEXT,
                system_prompt TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_path_type
            ON sessions(path, type)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                session_id INTEGER,
                role TEXT,
                content TEXT
            )
            """
        )
        conn.commit()
        # Backfill missing columns for existing DBs
        try:
            cur.execute("PRAGMA table_info(sessions)")
            cols = [row[1] for row in cur.fetchall()]
            if "last_model" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN last_model TEXT")
            if "last_server" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN last_server TEXT")
            if "system_prompt" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT")
                conn.commit()
        except Exception:
            pass
        conn.close()
        self._ensure_root_folder()

    def _ensure_root_folder(self) -> int:
        root = self.get_session_by_path("/", "folder")
        if root:
            return root["id"]
        return self._create_session("Chats", None, "/", "folder")

    def get_session_by_id(self, session_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, parent_id, path, type, last_model, last_server, system_prompt FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def _create_session(self, name: str, parent_id: Optional[int], path: str, type_: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO sessions (name, parent_id, path, type, last_model, last_server, system_prompt) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, parent_id, path, type_, None, None, None),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id

    def get_session_by_path(self, path: str, type_: str) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, parent_id, path, type, last_model, last_server, system_prompt FROM sessions WHERE path = ? AND type = ?",
            (path, type_),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_sessions(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, name, parent_id, path, type, last_model, last_server, system_prompt FROM sessions ORDER BY id")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _normalize_folder_path(self, folder_path: Optional[str]) -> str:
        if not folder_path:
            return "/"
        cleaned = folder_path.strip()
        if not cleaned or cleaned == "/":
            return "/"
        return "/" + Path(cleaned.lstrip("/")).as_posix()

    def _ensure_folder_chain(self, folder_path: str) -> Tuple[int, str]:
        folder_path = self._normalize_folder_path(folder_path)
        parts = [part for part in Path(folder_path.lstrip("/")).parts]
        parent_id = self._ensure_root_folder()
        current_path = "/"
        for part in parts:
            current_path = f"{current_path.rstrip('/')}/{part}"
            existing = self.get_session_by_path(current_path, "folder")
            if existing:
                parent_id = existing["id"]
                continue
            parent_id = self._create_session(part, parent_id, current_path, "folder")
        return parent_id, folder_path

    def _unique_chat_path(self, folder_path: str, base_name: str) -> str:
        base_path = self._normalize_folder_path(folder_path).rstrip("/")
        candidate = f"{base_path}/{base_name}".replace("//", "/")
        idx = 2
        while self.get_session_by_path(candidate, "chat"):
            candidate = f"{base_path}/{base_name}-{idx}"
            idx += 1
        return candidate

    def create_named_chat(self, folder_path: str, name: str) -> Dict:
        parent_id, folder_path = self._ensure_folder_chain(folder_path)
        chat_path = self._unique_chat_path(folder_path, name or "Chat")
        chat_id = self._create_session(name or "Chat", parent_id, chat_path, "chat")
        return {"id": chat_id, "name": name or "Chat", "parent_id": parent_id, "path": chat_path, "type": "chat"}

    def get_or_create_chat_for_page(self, rel_path: Optional[str]) -> Optional[Dict]:
        if not rel_path:
            return self.get_session_by_path("/", "chat") or self._create_root_chat()
        path_obj = Path(rel_path.lstrip("/"))
        folder_path = self._normalize_folder_path("/" + path_obj.parent.as_posix())
        parent_id, folder_path = self._ensure_folder_chain(folder_path)
        chat_name = path_obj.stem or Path(folder_path).name or "Chat"
        existing_chat = self.get_session_by_path(folder_path, "chat")
        if existing_chat:
            return existing_chat
        chat_id = self._create_session(chat_name, parent_id, folder_path, "chat")
        return {"id": chat_id, "name": chat_name, "parent_id": parent_id, "path": folder_path, "type": "chat"}

    def _create_root_chat(self) -> Dict:
        root_id = self._ensure_root_folder()
        chat_id = self._create_session("Root Chat", root_id, "/", "chat")
        return {"id": chat_id, "name": "Root Chat", "parent_id": root_id, "path": "/", "type": "chat"}

    def get_messages(self, session_id: int) -> List[Tuple[str, str]]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT role, content FROM messages WHERE session_id = ?", (session_id,))
        rows = cur.fetchall()
        conn.close()
        return [(row[0], row[1]) for row in rows]

    def save_message(self, session_id: int, role: str, content: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        conn.commit()
        conn.close()

    def update_session_last_model(self, session_id: int, model: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET last_model = ? WHERE id = ?", (model, session_id))
        conn.commit()
        conn.close()

    def update_session_last_server(self, session_id: int, server_name: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET last_server = ? WHERE id = ?", (server_name, session_id))
        conn.commit()
        conn.close()

    def update_session_system_prompt(self, session_id: int, prompt: Optional[str]) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET system_prompt = ? WHERE id = ?", (prompt, session_id))
        conn.commit()
        conn.close()

    def clear_chat(self, session_id: int) -> None:
        """Delete all messages for a chat and reset last-used metadata."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()

    def delete_message(self, session_id: int, role: str, content: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE session_id = ? AND role = ? AND content = ?", (session_id, role, content))
        conn.commit()
        conn.close()

    def has_chat_for_path(self, rel_path: Optional[str]) -> bool:
        """Return True if a chat exists for the given page path."""
        if not rel_path:
            return bool(self.get_session_by_path("/", "chat"))
        path_obj = Path(rel_path.lstrip("/"))
        folder_path = self._normalize_folder_path("/" + path_obj.parent.as_posix())
        return bool(self.get_session_by_path(folder_path, "chat"))

    def has_chats_under(self, folder_path: Optional[str]) -> bool:
        """Return True if any chat exists at or under the given folder path."""
        if not folder_path:
            return False
        normalized = self._normalize_folder_path(folder_path)
        like_pattern = f"{normalized.rstrip('/')}/%"
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sessions WHERE type = 'chat' AND (path = ? OR path LIKE ?) LIMIT 1",
            (normalized, like_pattern),
        )
        row = cur.fetchone()
        conn.close()
        return bool(row)

    def has_chat_for_path(self, rel_path: Optional[str]) -> bool:
        """Check if a chat exists for the given page path."""
        return self.store.has_chat_for_path(rel_path)

    def delete_chats_under(self, folder_path: Optional[str]) -> None:
        """Delete chats and their messages at or under the given folder path."""
        if not folder_path:
            return
        normalized = self._normalize_folder_path(folder_path)
        like_pattern = f"{normalized.rstrip('/')}/%"
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM sessions WHERE type = 'chat' AND (path = ? OR path LIKE ?)",
            (normalized, like_pattern),
        )
        ids = [row[0] for row in cur.fetchall()]
        if ids:
            cur.execute(f"DELETE FROM messages WHERE session_id IN ({','.join('?' for _ in ids)})", ids)
            cur.execute(f"DELETE FROM sessions WHERE id IN ({','.join('?' for _ in ids)})", ids)
            conn.commit()
        conn.close()


def build_auth_headers(server_config: dict) -> dict:
    headers = {}
    auth_mode = (server_config or {}).get("auth_mode", "proxy")
    if auth_mode == "proxy":
        token = (server_config or {}).get("api_secret")
        if token:
            headers["x-api-secret"] = token
    elif auth_mode == "openai":
        api_key = (server_config or {}).get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        header_name = (server_config or {}).get("custom_header_name")
        header_value = (server_config or {}).get("custom_header_value")
        if header_name and header_value:
            headers[header_name] = header_value
    return headers


def get_available_models(server_config: Optional[dict]) -> List[str]:
    server = server_config or {}
    fallback_model = server.get("default_model") or "gpt-3.5-turbo"
    if not server.get("name"):
        return [fallback_model]
    payload = zimx_config._read_global_config()
    server_models = payload.get("server_models", {})
    models = server_models.get(server["name"], [])
    if not models:
        return [fallback_model]
    return sorted(set(models))


def build_api_request(server_config: dict, messages: List[dict], model: str, stream: bool = True):
    server = server_config or {}
    base_url = server.get("base_url", "")
    chat_path = server.get("chat_path") or "/v1/chat/completions"
    if not base_url:
        raise ValueError("Selected server does not have a base URL configured.")
    url = compose_url(base_url, chat_path)
    headers = {"Content-Type": "application/json"}
    headers.update(build_auth_headers(server))
    verify = bool(server.get("verify_ssl", True))

    timeout_raw = server.get("timeout", "")
    try:
        timeout = float(timeout_raw) if str(timeout_raw).strip() else 120
    except (ValueError, TypeError):
        timeout = 120

    payload = {"model": model, "messages": messages, "stream": bool(stream)}
    return url, headers, verify, timeout, payload


class ApiWorker(QtCore.QThread):
    chunk = QtCore.Signal(str)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, server_config: dict, messages: List[dict], model: str, stream: bool = True, parent=None):
        super().__init__(parent)
        self.server_config = server_config
        self.messages = messages
        self.model = model
        self.stream = stream

    def run(self) -> None:
        try:
            url, headers, verify, timeout, payload = build_api_request(
                self.server_config, self.messages, self.model, stream=self.stream
            )
            print("[AIChat][request]", {"url": url, "headers": headers, "payload": payload})
            if self.stream:
                with httpx.stream(
                    "POST", url, json=payload, headers=headers, timeout=timeout, verify=verify
                ) as resp:
                    resp.raise_for_status()
                    full = ""
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else str(line)
                        #print(f"[AIChat][stream raw] {decoded}")
                        if decoded.startswith("data: "):
                            json_data = decoded[len("data: ") :]
                            if json_data.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(json_data)
                                if "choices" in data and data["choices"]:
                                    delta = data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        chunk = delta["content"]
                                        full += chunk
                                        self.chunk.emit(chunk)
                            except Exception:
                                continue
                    if full:
                        print("[AIChat][stream complete]", full)
                        self.finished.emit(full)
                        return
                # Fallback to non-stream if no chunks were handled
            with httpx.Client(timeout=timeout, verify=verify) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                print(f"[AIChat][response status] {resp.status_code}")
                print("[AIChat][response body]", resp.text)
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                content = ""
                if isinstance(choice, dict):
                    message = choice.get("message") or {}
                    if isinstance(message, dict):
                        content = message.get("content", "")
                self.finished.emit(content or str(data))
        except Exception as exc:
            self.failed.emit(str(exc))


class ServerConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, server: Optional[dict] = None, existing_names: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Server Configuration")
        # If adding a new server, set default paths
        if server is None:
            self.server = {
                "models_path": "/v1/models",
                "chat_path": "/v1/chat/completions"
            }
        else:
            self.server = server
        self.original_name = self.server.get("name")
        self.result: Optional[dict] = None
        self.existing_names = existing_names or []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QFormLayout(self)
        self.name_edit = QtWidgets.QLineEdit(self.server.get("name", ""))
        self.base_edit = QtWidgets.QLineEdit(self.server.get("base_url", ""))
        self.auth_combo = QtWidgets.QComboBox()
        self.auth_combo.addItems(["proxy", "openai", "custom"])
        self.auth_combo.setCurrentText(self.server.get("auth_mode", "proxy"))
        self.api_secret_edit = QtWidgets.QLineEdit(self.server.get("api_secret", ""))
        self.api_key_edit = QtWidgets.QLineEdit(self.server.get("api_key", ""))
        self.custom_header_name_edit = QtWidgets.QLineEdit(self.server.get("custom_header_name", ""))
        self.custom_header_value_edit = QtWidgets.QLineEdit(self.server.get("custom_header_value", ""))
        self.models_path_edit = QtWidgets.QLineEdit(self.server.get("models_path", ""))
        self.chat_path_edit = QtWidgets.QLineEdit(self.server.get("chat_path", ""))
        self.timeout_edit = QtWidgets.QLineEdit(str(self.server.get("timeout") or ""))
        self.default_model_edit = QtWidgets.QLineEdit(self.server.get("default_model", "gpt-3.5-turbo"))
        self.verify_ssl_check = QtWidgets.QCheckBox("Verify SSL certificates")
        self.verify_ssl_check.setChecked(bool(self.server.get("verify_ssl", True)))
        self.duplicate_label = QtWidgets.QLabel("")
        self.duplicate_label.setStyleSheet("color: red; font-weight: bold;")
        self.duplicate_label.hide()
        layout.addRow("Name", self.name_edit)
        layout.addRow("Base URL", self.base_edit)
        layout.addRow("Auth Mode", self.auth_combo)
        layout.addRow("API Secret (proxy)", self.api_secret_edit)
        layout.addRow("API Key (OpenAI)", self.api_key_edit)
        layout.addRow("Custom Header Name", self.custom_header_name_edit)
        layout.addRow("Custom Header Value", self.custom_header_value_edit)
        layout.addRow("Models Path", self.models_path_edit)
        layout.addRow("Chat Path", self.chat_path_edit)
        layout.addRow("Timeout (seconds)", self.timeout_edit)
        layout.addRow("Default Model", self.default_model_edit)
        layout.addRow(self.verify_ssl_check)
        layout.addRow(self.duplicate_label)

        self.button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._handle_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addRow(self.button_box)
        self.ok_button = self.button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if not self.original_name:
            self.ok_button.setText("Add")
        self.name_edit.textChanged.connect(self._validate_name)
        self._validate_name()

    def _handle_accept(self):
        name = self.name_edit.text().strip()
        base = self.base_edit.text().strip()
        if not name or not base:
            QtWidgets.QMessageBox.warning(self, "Validation", "Name and Base URL are required.")
            return
        if self._is_duplicate(name):
            self.duplicate_label.setText(f"Server name {name} already exists, choose a new name")
            self.duplicate_label.show()
            if self.ok_button:
                self.ok_button.setEnabled(False)
            return
        auth_mode = self.auth_combo.currentText()
        models_path = self.models_path_edit.text().strip() or ("/mods" if auth_mode == "proxy" else "/v1/models")
        chat_path = self.chat_path_edit.text().strip() or "/v1/chat/completions"

        timeout_raw = self.timeout_edit.text().strip()
        timeout_value = ""
        if timeout_raw:
            try:
                timeout_value = float(timeout_raw)
            except ValueError:
                timeout_value = timeout_raw

        self.result = {
            "name": name,
            "base_url": base,
            "auth_mode": auth_mode,
            "api_secret": self.api_secret_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "custom_header_name": self.custom_header_name_edit.text().strip(),
            "custom_header_value": self.custom_header_value_edit.text().strip(),
            "models_path": models_path,
            "chat_path": chat_path,
            "default_model": self.default_model_edit.text().strip() or "gpt-3.5-turbo",
            "verify_ssl": self.verify_ssl_check.isChecked(),
            "timeout": timeout_value,
            "original_name": self.original_name,
        }
        super().accept()

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        base = self.base_edit.text().strip()
        if not name or not base:
            QtWidgets.QMessageBox.warning(self, "Validation", "Name and Base URL are required.")
            return
        if self._is_duplicate(name):
            self.duplicate_label.setText(f"Server name {name} already exists, choose a new name")
            self.duplicate_label.show()
            if self.ok_button:
                self.ok_button.setEnabled(False)
            return
        auth_mode = self.auth_combo.currentText()
        models_path = self.models_path_edit.text().strip() or ("/mods" if auth_mode == "proxy" else "/v1/models")
        chat_path = self.chat_path_edit.text().strip() or "/v1/chat/completions"

        timeout_raw = self.timeout_edit.text().strip()
        timeout_value = ""
        if timeout_raw:
            try:
                timeout_value = float(timeout_raw)
            except ValueError:
                timeout_value = timeout_raw

        self.result = {
            "name": name,
            "base_url": base,
            "auth_mode": auth_mode,
            "api_secret": self.api_secret_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "custom_header_name": self.custom_header_name_edit.text().strip(),
            "custom_header_value": self.custom_header_value_edit.text().strip(),
            "models_path": models_path,
            "chat_path": chat_path,
            "default_model": self.default_model_edit.text().strip() or "gpt-3.5-turbo",
            "verify_ssl": self.verify_ssl_check.isChecked(),
            "timeout": timeout_value,
            "original_name": self.original_name,
        }
        super().accept()

    def _is_duplicate(self, name: str) -> bool:
        name_clean = name.strip()
        if not name_clean:
            return False
        if name_clean == self.original_name:
            return False
        return name_clean in self.existing_names

    def _validate_name(self) -> None:
        name = self.name_edit.text().strip()
        if self._is_duplicate(name):
            self.duplicate_label.setText(f"Server name {name} already exists, choose a new name")
            self.duplicate_label.show()
            if self.ok_button:
                self.ok_button.setEnabled(False)
        else:
            self.duplicate_label.hide()
            if self.ok_button:
                self.ok_button.setEnabled(True)


class AIChatPanel(QtWidgets.QWidget):
    """Lightweight AI chat panel embedded in the right rail."""

    chatNavigateRequested = QtCore.Signal(str)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            key_event = event  # type: ignore[assignment]
            if key_event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                modifiers = key_event.modifiers()
                if modifiers & QtCore.Qt.ControlModifier:
                    self._send_message()
                    return True
                if modifiers & QtCore.Qt.AltModifier:
                    self.input_edit.insertPlainText("\n")
                    return True
        return super().eventFilter(obj, event)

    def __init__(self, parent=None, font_size=13):
        super().__init__(parent)
        self.vault_root = None
        self.store = AIChatStore()
        self.server_manager = ServerManager()
        self.current_server = self.server_manager.get_server(self.server_manager.get_active_server_name())
        self.messages = []
        self._api_worker = None
        self._condense_worker = None
        self.current_session_id = None
        self.current_page_path = None
        self._building_tree = False
        self._current_chat_path = None
        self.system_prompts = []
        self.current_system_prompt = None
        self.font_size = font_size
        self.condense_prompt = self._load_condense_prompt()
        self._condense_buffer = ""
        self._summary_content = None
        self._build_ui()
        self._load_system_prompts()
        self._refresh_server_dropdown()
        self._refresh_model_dropdown(initial=True)
        self._load_chat_tree()
        self._select_default_chat()

    def _config_default_server(self) -> Optional[str]:
        try:
            return zimx_config.load_default_ai_server()
        except Exception:
            return None

    def _config_default_model(self) -> Optional[str]:
        try:
            return zimx_config.load_default_ai_model()
        except Exception:
            return None

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        toggle_row = QtWidgets.QHBoxLayout()
        self.toggle_chats_btn = QtWidgets.QPushButton("Show Chats")
        self.toggle_chats_btn.setCheckable(True)
        self.toggle_chats_btn.setChecked(False)
        self.toggle_chats_btn.toggled.connect(self._toggle_chat_list)
        toggle_row.addWidget(self.toggle_chats_btn)
        self.server_config_btn = QtWidgets.QPushButton("Server Config")
        self.server_config_btn.setCheckable(True)
        self.server_config_btn.setChecked(False)
        self.server_config_btn.toggled.connect(self._toggle_server_config)
        toggle_row.addWidget(self.server_config_btn)
        self.prompt_btn = QtWidgets.QPushButton("System Prompts")
        self.prompt_btn.clicked.connect(self._open_prompt_dialog)
        toggle_row.addWidget(self.prompt_btn)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        # Chat name display
        self.chat_name_label = QtWidgets.QLabel("Chat Name: —")
        self.chat_name_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.chat_name_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.chat_name_label.setOpenExternalLinks(False)
        self.chat_name_label.linkActivated.connect(lambda href: self.chatNavigateRequested.emit(href))
        layout.addWidget(self.chat_name_label)

        # Server/model controls (at top)
        self.server_config_widget = QtWidgets.QWidget()
        self.server_config_widget.setVisible(False)
        cfg_layout = QtWidgets.QVBoxLayout(self.server_config_widget)
        cfg_layout.setContentsMargins(0, 0, 0, 0)
        cfg_layout.setSpacing(4)

        server_row = QtWidgets.QHBoxLayout()
        server_row.addWidget(QtWidgets.QLabel("Server:"))
        self.server_combo = QtWidgets.QComboBox()
        self.server_combo.currentIndexChanged.connect(self._on_server_selected)
        server_row.addWidget(self.server_combo, 1)
        cfg_layout.addLayout(server_row)

        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("Model:"))
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.currentTextChanged.connect(self._on_model_selected)
        model_row.addWidget(self.model_combo, 1)
        refresh_models_btn = QtWidgets.QPushButton("Refresh Models")
        refresh_models_btn.clicked.connect(self._refresh_models_from_server)
        model_row.addWidget(refresh_models_btn)
        cfg_layout.addLayout(model_row)

        layout.addWidget(self.server_config_widget)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(self.splitter, 1)

        # Left: chat tree
        left_container = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_container)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(4)
        header_row = QtWidgets.QHBoxLayout()
        header_row.addWidget(QtWidgets.QLabel("Chat Folders"))
        self.new_chat_btn = QtWidgets.QPushButton("New Chat")
        self.new_chat_btn.clicked.connect(self._new_chat)
        header_row.addWidget(self.new_chat_btn)
        header_row.addStretch()
        left_layout.addLayout(header_row)
        self.chat_tree = QtWidgets.QTreeWidget()
        self.chat_tree.setHeaderHidden(True)
        self.chat_tree.itemSelectionChanged.connect(self._on_chat_selected)
        self.chat_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.chat_tree.customContextMenuRequested.connect(self._on_chat_context_menu)
        left_layout.addWidget(self.chat_tree, 1)
        self.chat_tree_container = left_container
        self.splitter.addWidget(left_container)

        # Right: chat UI
        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(6)

        chat_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_layout.addWidget(chat_split, 1)

        # Chat display
        self.chat_view = QtWidgets.QTextBrowser()
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setOpenLinks(False)
        self.chat_view.anchorClicked.connect(self._on_anchor_clicked)
        self.chat_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.chat_view.customContextMenuRequested.connect(self._on_history_context_menu)
        self.chat_view.setReadOnly(True)
        self.chat_view.setStyleSheet("QTextBrowser { padding: 6px; }")
        self._apply_font_size()
        chat_split.addWidget(self.chat_view)

        # Input area
        input_container = QtWidgets.QWidget()
        input_layout = QtWidgets.QVBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(4)
        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setPlaceholderText("Ask anything…")
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.condense_btn = QtWidgets.QToolButton()
        self.condense_btn.setText("⤢")
        self.condense_btn.setToolTip("Condense Chat")
        self.condense_btn.clicked.connect(self._condense_chat)
        self.condense_btn.setFixedWidth(28)
        self.send_btn = QtWidgets.QToolButton()
        self.send_btn.setText("➤")
        self.send_btn.setToolTip("Send message (Ctrl+Enter)")
        self.send_btn.clicked.connect(self._send_message)
        self.send_btn.setFixedWidth(28)
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("↺")
        self.reset_btn.setToolTip("Reset chat history")
        self.reset_btn.clicked.connect(self._reset_chat_history)
        self.reset_btn.setFixedWidth(28)
        btn_row.addWidget(self.condense_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addWidget(self.send_btn)
        input_layout.addLayout(btn_row)

        chat_split.addWidget(input_container)
        chat_split.setStretchFactor(0, 3)
        chat_split.setStretchFactor(1, 1)

        self.status_label = QtWidgets.QLabel()
        right_layout.addWidget(self.status_label)
        self.model_status_label = QtWidgets.QLabel()
        right_layout.addWidget(self.model_status_label)

        self.splitter.addWidget(right_container)
        self._toggle_chat_list(False)
        self._update_chat_name_label()
        self._apply_font_size()

    def _load_condense_prompt(self) -> str:
        """Load the condense prompt from file or fall back to a default."""
        # Prefer a vault-specific prompt if present, otherwise try bundled paths.
        candidates = []
        if self.vault_root:
            candidates.append(Path(self.vault_root) / ".zimx" / "condense_prompt.txt")
        candidates.append(PROJECT_ROOT / "zimx" / "app" / "condense_prompt.txt")
        candidates.append(Path(__file__).resolve().parent.parent / "condense_prompt.txt")
        for path in candidates:
            try:
                if path.exists():
                    return path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
        print("[AIChat][condense] Using fallback condense prompt; file not found.")
        return "You are a helpful assistant. Summarize the following chat history."

    def focusInEvent(self, event):  # type: ignore[override]
        # Update server dropdown to reflect latest config state
        self._refresh_server_dropdown()
        super().focusInEvent(event)
        try:
            target_folder = self._current_chat_path
            if not target_folder:
                return
            current_folder = None
            if self.current_page_path:
                path_obj = Path(self.current_page_path.lstrip("/"))
                current_folder = "/" + path_obj.parent.as_posix()
            if current_folder != target_folder:
                self.chatNavigateRequested.emit(target_folder)
        except Exception:
            return

    def _toggle_chat_list(self, checked: bool) -> None:
        self.chat_tree_container.setVisible(checked)
        self.toggle_chats_btn.setText("Hide Chats" if checked else "Show Chats")
        if checked:
            self.splitter.setSizes([240, 760])
        else:
            self.splitter.setSizes([0, 1])

    def _on_chat_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.chat_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, QtCore.Qt.UserRole) or {}
        if data.get("type") != "chat":
            return
        menu = QtWidgets.QMenu(self)
        go_action = menu.addAction("Go To Page")
        go_action.triggered.connect(lambda: self._go_to_page_for_chat(data))
        menu.exec(self.chat_tree.viewport().mapToGlobal(pos))

    def _toggle_server_config(self, checked: bool) -> None:
        self.server_config_widget.setVisible(checked)
        self.server_config_btn.setText("Hide Config" if checked else "Server Config")
        self.prompt_btn.setVisible(checked)
        # Do not auto-refresh models on config toggle

    def _render_messages(self) -> None:
        parts: List[str] = []
        base_color = self.palette().color(QPalette.Base).name()
        text_color = self.palette().color(QPalette.Text).name()
        accent = self.palette().color(QPalette.Highlight).name()
        parts.append(
            f"<style>body {{ background:{base_color}; color:{text_color}; }}"
            f".bubble {{ position:relative; border-radius:6px; padding:6px 8px 12px; margin-bottom:8px; }}"
            f".bubble:hover {{ background:rgba(0,0,0,0.05); }}"
            f".actions {{ text-align:right; font-size:12px; display:none; margin-top:6px; }}"
            f".bubble:hover .actions {{ display:block; }}"
            f".actions a {{ margin-left:12px; text-decoration:none; color:{accent}; }}"
            f".user {{ background:rgba(80,120,200,0.10); }}"
            f".assistant {{ background:rgba(60,200,140,0.10); }}"
            f".summary {{ border:1px solid #e88; }}"
            f".role {{ font-weight:bold; color:{accent}; }}</style>"
        )
        self._message_map = {}
        for idx, (role, content) in enumerate(self.messages):
            cls = "assistant" if role == "assistant" else "user"
            msg_id = f"msg-{idx}"
            rendered = markdown(content, extensions=["fenced_code", "tables"])
            if self._is_plain_markdown(rendered):
                safe = html.escape(content).replace("\n", "<br>")
                rendered = f"<p>{safe}</p>"
            actions = [
                f"<a href='action:copy:{msg_id}' title='Copy message'>📋</a>",
                f"<a href='action:goto:{msg_id}' title='Go to start'>⬆</a>",
                f"<a href='action:delete:{msg_id}' title='Delete message'>🗑</a>",
            ]
            parts.append(
                f"<div class='bubble {cls}' id='{msg_id}'><a name='{msg_id}' href='msg:{msg_id}'></a>"
                f"<span class='role'>{role.title()}:</span><br>{rendered}"
                f"<div class='actions'>{' | '.join(actions)}</div>"
                f"</div>"
            )
            self._message_map[msg_id] = (role, content)
        if self._condense_buffer or self._summary_content:
            summary_text = self._summary_content or self._condense_buffer
            actions = []
            if self._summary_content:
                actions = [
                    "<a href='action:summary:accept' title='Accept condensed chat'>Accept</a>",
                    "<a href='action:summary:reject' title='Reject condensed chat'>Reject</a>",
                ]
            summary_html = markdown(summary_text, extensions=["fenced_code", "tables"])
            if self._is_plain_markdown(summary_html):
                safe = html.escape(summary_text).replace("\n", "<br>")
                summary_html = f"<p>{safe}</p>"
            parts.append(
                f"<div class='bubble summary' id='summary'><a name='summary'></a>"
                f"<span class='role'>Summary:</span><br>{summary_html}"
                f"{'<div class=\"actions\">' + ' | '.join(actions) + '</div>' if actions else ''}"
                f"</div>"
            )
        self.chat_view.setHtml("".join(parts))
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.chat_view.setTextCursor(cursor)

    def _is_plain_markdown(self, rendered_html: str) -> bool:
        """Heuristic: detect if the markdown output is just a <p> block without rich tags."""
        if not rendered_html.startswith("<p>") or not rendered_html.endswith("</p>"):
            return False
        heavy_tags = ("<ul", "<ol", "<pre", "<code", "<h", "<table", "<blockquote", "<li", "<hr", "<img", "<a ")
        return not any(tag in rendered_html for tag in heavy_tags)

    def _on_history_context_menu(self, pos: QtCore.QPoint) -> None:
        anchor = self.chat_view.anchorAt(pos)
        msg_id = None
        if anchor:
            if anchor.startswith("msg:"):
                msg_id = anchor.split(":", 1)[1]
            elif anchor.startswith("action:") and ":" in anchor:
                msg_id = anchor.split(":")[-1]
        if not msg_id and self._message_map:
            msg_id = list(self._message_map.keys())[-1]
        self._show_history_context_menu_for(msg_id, pos)

    def _show_history_context_menu_for(self, msg_id: Optional[str], pos: QtCore.QPoint) -> None:
        if not msg_id or msg_id not in self._message_map:
            return
        _, content = self._message_map[msg_id]
        menu = QtWidgets.QMenu(self)
        copy_act = menu.addAction("Copy Message")
        copy_act.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(content))
        goto_act = menu.addAction("Go To Start")
        goto_act.triggered.connect(lambda: self.chat_view.scrollToAnchor(msg_id))
        del_act = menu.addAction("Delete Message")
        del_act.triggered.connect(lambda: self._delete_message(msg_id))
        menu.addSeparator()
        actions_menu = menu.addMenu("AI Actions")
        self._populate_ai_actions_menu(actions_menu, content)
        menu.exec(self.chat_view.mapToGlobal(pos))

    def _populate_ai_actions_menu(self, parent_menu: QtWidgets.QMenu, text: str) -> None:
        """Build AI actions submenu mirroring the editor."""

        def add_actions(menu, items):
            for title, prompt in items:
                act = menu.addAction(title)
                act.triggered.connect(lambda t=title, p=prompt: self.send_action_message(t, p, text))

        ai_menu_summarize = parent_menu.addMenu("Summarize")
        add_actions(
            ai_menu_summarize,
            [
                ("Short Summary", "Summarize this note in 3–5 sentences."),
                ("Bullet Summary", "Summarize into bullet points."),
                ("Key Insights", "Extract key insights / themes."),
                ("TL;DR", "Give a one-line TL;DR."),
                ("Meeting Minutes Style", "Convert to meeting minutes."),
                ("Executive Summary", "Summarize for an executive audience."),
            ],
        )

        ai_menu_rewrite = parent_menu.addMenu("Rewrite / Improve Writing")
        add_actions(
            ai_menu_rewrite,
            [
                ("Rewrite for Clarity", "Rewrite for clarity."),
                ("Rewrite Concisely", "Rewrite concisely."),
                ("Rewrite More Detailed", "Rewrite to be more detailed."),
                ("Rewrite More Casual", "Rewrite in a more casual tone."),
                ("Rewrite More Professional", "Rewrite in a more professional tone."),
                ("Rewrite as Email", "Rewrite as an email."),
                ("Rewrite as Action Plan", "Rewrite as an action plan."),
                ("Rewrite as Journal Entry", "Rewrite as a journal entry."),
                ("Rewrite as Tutorial / Guide", "Rewrite as a tutorial or guide."),
            ],
        )

        ai_menu_translate = parent_menu.addMenu("Translate")
        add_actions(
            ai_menu_translate,
            [
                ("Auto-Detect → English", "Translate to English (auto-detect source language)."),
                ("English → Spanish", "Translate to Spanish."),
                ("English → French", "Translate to French."),
                ("English → German", "Translate to German."),
                ("English → Italian", "Translate to Italian."),
                ("English → Chinese", "Translate to Chinese."),
                ("English → Japanese", "Translate to Japanese."),
                ("English → Korean", "Translate to Korean."),
            ],
        )

        ai_menu_extract = parent_menu.addMenu("Extract")
        add_actions(
            ai_menu_extract,
            [
                ("Tasks / To-Dos", "Extract tasks or to-dos."),
                ("Dates / Deadlines", "Extract dates or deadlines."),
                ("Names & People", "Extract names and people."),
                ("Action Items", "Extract action items."),
                ("Questions", "Extract questions."),
                ("Entities & Keywords", "Extract entities and keywords."),
                ("Topics / Tags", "Extract topics or tags."),
                ("Structured JSON Data", "Extract structured JSON data."),
                ("Links / URLs mentioned", "Extract links or URLs mentioned."),
            ],
        )

        ai_menu_analyze = parent_menu.addMenu("Analyze")
        add_actions(
            ai_menu_analyze,
            [
                ("Sentiment Analysis", "Analyze sentiment."),
                ("Tone Analysis", "Analyze tone."),
                ("Bias / Assumptions", "Identify biases or assumptions."),
                ("Logical Fallacies", "Find logical fallacies."),
                ("Risk Assessment", "Provide a risk assessment."),
                ("Pros & Cons", "Provide pros and cons."),
                ("Root-Cause Analysis", "Provide a root-cause analysis."),
                ("SWOT Analysis", "Provide a SWOT analysis."),
            ],
        )

        ai_menu_explain = parent_menu.addMenu("Explain")
        add_actions(
            ai_menu_explain,
            [
                ("Explain Like I’m 5", "Explain like I'm 5."),
                ("Explain for a Beginner", "Explain for a beginner."),
                ("Explain for an Expert", "Explain for an expert."),
                ("Break Down Step-By-Step", "Break down step by step."),
                ("Provide Examples", "Provide examples."),
                ("Define All Concepts", "Define all concepts."),
                ("Explain the Why Behind Each Step", "Explain the 'why' behind each step."),
            ],
        )

        ai_menu_brainstorm = parent_menu.addMenu("Brainstorm")
        add_actions(
            ai_menu_brainstorm,
            [
                ("Brainstorm Ideas", "Brainstorm ideas."),
                ("Brainstorm Questions to Ask", "Brainstorm questions to ask."),
                ("Alternative Approaches", "Suggest alternative approaches."),
                ("Solutions to the Problem", "Suggest solutions to the problem."),
                ("Potential Risks / Pitfalls", "List potential risks or pitfalls."),
                ("Related Topics I Should Explore", "List related topics to explore."),
            ],
        )

        ai_menu_transform = parent_menu.addMenu("Transform")
        add_actions(
            ai_menu_transform,
            [
                ("Convert to Markdown", "Convert to Markdown."),
                ("Convert to Bullet Points", "Convert to bullet points."),
                ("Convert to Outline", "Convert to an outline."),
                ("Convert to Table", "Convert to a table."),
                ("Convert to Checklist", "Convert to a checklist."),
                ("Convert to JSON", "Convert to JSON."),
                ("Convert to CSV", "Convert to CSV."),
                ("Convert to Code Comments", "Convert to code comments."),
                ("Convert to Script (Python / JS / Bash)", "Convert to a script (Python / JS / Bash)."),
                ("Convert to Slide Outline", "Convert to a slide outline."),
            ],
        )

        ai_menu_research = parent_menu.addMenu("Research Helper")
        add_actions(
            ai_menu_research,
            [
                ("Generate Questions I Should Ask", "Generate questions I should ask."),
                ("List Assumptions", "List assumptions."),
                ("Find Missing Info", "Find missing information."),
                ("Provide Historical Context", "Provide historical context."),
                ("Predict Outcomes", "Predict outcomes."),
                ("Summarize Top Debates Around This Topic", "Summarize top debates around this topic."),
                ("Give Related References / Sources (non-live)", "Give related references or sources (non-live)."),
            ],
        )

        ai_menu_creative = parent_menu.addMenu("Creative")
        add_actions(
            ai_menu_creative,
            [
                ("Rewrite as Story", "Rewrite as a story."),
                ("Rewrite as Poem", "Rewrite as a poem."),
                ("Rewrite as Dialogue", "Rewrite as a dialogue."),
                ("Rewrite as Song", "Rewrite as a song."),
                ("Rewrite as Fiction Scene", "Rewrite as a fiction scene."),
                ("Turn This Into: characters / plot / setting", "Turn this into characters, plot, and setting."),
            ],
        )

        ai_menu_chat = parent_menu.addMenu("Chat-About-This Note")
        add_actions(
            ai_menu_chat,
            [
                ("Ask the AI About This Note", "Ask the AI about this note."),
                ("Continue Thought from Here", "Continue the thought from here."),
                ("What Should I Do Next Based on This Note?", "What should I do next based on this note?"),
                ("How Can I Improve This?", "How can I improve this?"),
                ("Generate Next Section", "Generate the next section."),
            ],
        )

        ai_menu_memory = parent_menu.addMenu("Memory & Linking")
        add_actions(
            ai_menu_memory,
            [
                ("Suggest Tags", "Suggest tags."),
                ("Suggest Backlinks", "Suggest backlinks."),
                ("Build Concept Map", "Build a concept map."),
                ("Identify Repeating Themes Across Notes", "Identify repeating themes across notes."),
            ],
        )

        ai_menu_privacy = parent_menu.addMenu("Privacy / Redaction")
        add_actions(
            ai_menu_privacy,
            [
                ("Remove Personal Info", "Remove personal information."),
                ("Anonymize Names", "Anonymize names."),
                ("Anonymize Companies", "Anonymize companies."),
                ("Detect Sensitive Content", "Detect sensitive content."),
            ],
        )

        ai_menu_debug = parent_menu.addMenu("Debug Note Content")
        add_actions(
            ai_menu_debug,
            [
                ("Check for Contradictions", "Check for contradictions."),
                ("Check for Missing Steps", "Check for missing steps."),
                ("Check for Ambiguous Claims", "Check for ambiguous claims."),
                ("Check for Outdated Info", "Check for outdated information."),
                ("Validate Against External Knowledge (optional)", "Validate against external knowledge."),
            ],
        )

    def _select_default_chat(self) -> None:
        chat = self.store.get_session_by_path("/", "chat")
        if not chat:
            chat = self.store._create_root_chat()  # type: ignore[attr-defined]
            if chat:
                default_server = self._config_default_server()
                if default_server:
                    self.store.update_session_last_server(chat["id"], default_server)
                default_model = self._config_default_model()
                if default_model:
                    self.store.update_session_last_model(chat["id"], default_model)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            self._load_chat_messages(chat["id"])

    def _load_chat_tree(self, select_id: Optional[int] = None) -> None:
        self._building_tree = True
        self.chat_tree.clear()
        sessions = self.store.get_sessions()
        items: Dict[int, QtWidgets.QTreeWidgetItem] = {}
        for sess in sessions:
            item = QtWidgets.QTreeWidgetItem([sess["name"]])
            item.setData(0, QtCore.Qt.UserRole, sess)
            if sess["type"] == "chat":
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            items[sess["id"]] = item
        for sess in sessions:
            item = items[sess["id"]]
            parent_id = sess.get("parent_id")
            if parent_id and parent_id in items:
                items[parent_id].addChild(item)
            else:
                self.chat_tree.addTopLevelItem(item)
        self.chat_tree.expandAll()
        self._building_tree = False
        if select_id:
            self._select_chat_by_id(select_id)
        elif self.current_session_id:
            self._select_chat_by_id(self.current_session_id)

    def _select_chat_by_id(self, session_id: int) -> None:
        def walk(item: QtWidgets.QTreeWidgetItem) -> bool:
            data = item.data(0, QtCore.Qt.UserRole) or {}
            if data.get("id") == session_id:
                self.chat_tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    return True
            return False

        for i in range(self.chat_tree.topLevelItemCount()):
            if walk(self.chat_tree.topLevelItem(i)):
                return

    def _on_chat_selected(self) -> None:
        if self._building_tree:
            return
        item = self.chat_tree.currentItem()
        if not item:
            return
        data = item.data(0, QtCore.Qt.UserRole) or {}
        if data.get("type") != "chat":
            return
        self._load_chat_messages(data["id"])

    def _load_chat_messages(self, session_id: int) -> None:
        self.current_session_id = session_id
        self.messages = self.store.get_messages(session_id)
        self._render_messages()
        session = self.store.get_session_by_id(session_id)
        if session:
            self._apply_session_defaults(session)
            self.current_system_prompt = session.get("system_prompt")
        else:
            self._update_model_status()
        self._update_chat_name_label()

    def _apply_session_defaults(self, session: Dict) -> None:
        """Apply stored server/model defaults to UI for a chat session."""
        # Server
        last_server = session.get("last_server")
        if not last_server:
            last_server = self._config_default_server()
        if last_server:
            names = self.server_manager.list_server_names()
            if last_server in names:
                self.server_combo.blockSignals(True)
                self.server_combo.setCurrentText(last_server)
                self.server_combo.blockSignals(False)
                self.current_server = self.server_manager.get_server(last_server)
                if session.get("last_server") != last_server and self.current_session_id:
                    self.store.update_session_last_server(self.current_session_id, last_server)
        # Model
        server = self.current_server or self.server_manager.get_server(self.server_combo.currentText())
        # Only update model dropdown from cache, do not fetch
        models = get_available_models(server)
        if not models:
            models = [server.get("default_model") or "gpt-3.5-turbo"]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        desired_model = session.get("last_model")
        if not desired_model:
            cfg_model = self._config_default_model()
            if cfg_model in models:
                desired_model = cfg_model
        if desired_model in models:
            self.model_combo.setCurrentText(desired_model)
        elif server.get("default_model") in models:
            self.model_combo.setCurrentText(server.get("default_model"))
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        if self.current_session_id:
            chosen = self.model_combo.currentText()
            if session.get("last_model") != chosen:
                self.store.update_session_last_model(self.current_session_id, chosen)
        # System prompt
        self.current_system_prompt = session.get("system_prompt")
        self._update_model_status()

    def _refresh_server_dropdown(self, select_name: Optional[str] = None) -> None:
        names = self.server_manager.list_server_names()
        self.server_combo.blockSignals(True)
        self.server_combo.clear()
        self.server_combo.addItems(names)
        desired = select_name
        if not desired and self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session and session.get("last_server") in names:
                desired = session.get("last_server")
        if not desired:
            desired = self.server_manager.get_active_server_name()
        if not desired:
            cfg_default = self._config_default_server()
            if cfg_default in names:
                desired = cfg_default
        if desired and desired in names:
            self.server_combo.setCurrentText(desired)
        elif names:
            self.server_combo.setCurrentIndex(0)
        self.server_combo.blockSignals(False)
        current_name = self.server_combo.currentText()
        self.current_server = self.server_manager.get_server(current_name)
        if current_name:
            self.server_manager.set_active_server(current_name)
            if self.current_session_id:
                session = self.store.get_session_by_id(self.current_session_id) or {}
                if session.get("last_server") != current_name:
                    self.store.update_session_last_server(self.current_session_id, current_name)

    def _refresh_model_dropdown(self, initial: bool) -> None:
        server = self.current_server or {}
        models = get_available_models(server)
        if not models:
            models = [server.get("default_model") or "gpt-3.5-turbo"]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        target = None
        if self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session and session.get("last_model") in models:
                target = session.get("last_model")
        if not target:
            cfg_model = self._config_default_model()
            if cfg_model in models:
                target = cfg_model
        if not target:
            target = server.get("default_model") or models[0]
        if target in models:
            self.model_combo.setCurrentText(target)
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        if not initial:
            self.status_label.setText("Models refreshed.")
        self._update_model_status()

    def _refresh_models_from_server(self) -> None:
        if not self.current_server:
            QtWidgets.QMessageBox.warning(self, "No Server", "Please select a server to refresh models.")
            return
        models = fetch_and_cache_models(self.current_server)
        self._refresh_model_dropdown(initial=False)
        self.status_label.setText(f"Models refreshed from server. {len(models)} models cached.")

    def _on_server_selected(self) -> None:
        selected_name = self.server_combo.currentText()
        server = self.server_manager.get_server(selected_name)
        if not server:
            QtWidgets.QMessageBox.critical(self, "Server", "Selected server is unavailable.")
            return
        self.current_server = server
        self.server_manager.set_active_server(selected_name)
        if self.current_session_id:
            self.store.update_session_last_server(self.current_session_id, selected_name)
        self._refresh_model_dropdown(initial=False)
        self.status_label.setText(f"Switched to server: {selected_name}")
        self._update_model_status()

    def _on_model_selected(self) -> None:
        """Persist chosen model for the current chat."""
        if self.current_session_id:
            self.store.update_session_last_model(self.current_session_id, self.model_combo.currentText())
        self._update_model_status()

    def _manage_servers(self) -> None:
        dialog = ServerConfigDialog(self, self.current_server, existing_names=self.server_manager.list_server_names())
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.result:
            try:
                new_server = self.server_manager.add_or_update_server(dialog.result)
                self._refresh_server_dropdown(select_name=new_server["name"])
                self.current_server = new_server
                self._refresh_model_dropdown(initial=True)
                dialog.accept()  # Ensure dialog closes
            except ValueError as exc:
                QtWidgets.QMessageBox.critical(self, "Server Exists", str(exc))
                dialog.reject()
                return

    def _add_server(self) -> None:
        dialog = ServerConfigDialog(self, None, existing_names=self.server_manager.list_server_names())
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.result:
            try:
                new_server = self.server_manager.add_or_update_server(dialog.result)
                # Fetch and cache models for the new server
                fetch_and_cache_models(new_server)
                self._refresh_server_dropdown(select_name=new_server["name"])
                self.current_server = new_server
                self._refresh_model_dropdown(initial=True)
                self.status_label.setText(f"Added server: {new_server['name']} (models cached)")
                dialog.accept()  # Ensure dialog closes
            except ValueError as exc:
                QtWidgets.QMessageBox.critical(self, "Server Exists", str(exc))
                dialog.reject()
                return

    def _ensure_active_chat(self) -> bool:
        if self.current_session_id:
            return True
        chat = self.store.get_or_create_chat_for_page(self.current_page_path)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            return True
        return False

    def _send_message(self) -> None:
        content = self.input_edit.toPlainText().strip()
        self.input_edit.clear()
        self._start_send(content)

    def send_action_message(self, action: str, prompt: str, text: str) -> None:
        """Send a structured action message into the chat."""
        content = f"[{action}] {prompt}\n\n{text}"
        extra_system = f"AI Action: {action}\nInstruction: {prompt}"
        self._start_send(content, extra_system=extra_system)

    def _condense_chat(self) -> None:
        """Send the full chat history through the condense prompt."""
        if self._condense_worker:
            return
        if not self.current_server:
            QtWidgets.QMessageBox.critical(self, "Server", "Please configure a server before condensing.")
            return
        if not self._ensure_active_chat():
            QtWidgets.QMessageBox.critical(self, "Chat", "Could not find or create a chat.")
            return
        if not self.messages:
            QtWidgets.QMessageBox.information(self, "Condense", "No messages to condense yet.")
            return
        # Refresh prompt at use-time so edits take effect
        self.condense_prompt = self._load_condense_prompt()
        history_source = self.messages
        if self.current_session_id:
            try:
                history_source = self.store.get_messages(self.current_session_id)
            except Exception:
                history_source = self.messages
        history_text = "\n\n".join(f"{role.upper()}: {content}" for role, content in history_source)
        blocks = [{"role": "system", "content": self.condense_prompt}, {"role": "user", "content": history_text}]
        self._condense_buffer = ""
        self._summary_content = None
        self._render_messages()
        try:
            self._condense_worker = ApiWorker(self.current_server, blocks, self.model_combo.currentText(), stream=True)
            self._condense_worker.chunk.connect(self._handle_condense_chunk)
            self._condense_worker.finished.connect(self._handle_condense_finished)
            self._condense_worker.failed.connect(self._handle_condense_error)
            self._condense_worker.start()
            self.status_label.setText("Condensing chat…")
            self.condense_btn.setEnabled(False)
        except Exception as exc:
            self._condense_worker = None
            QtWidgets.QMessageBox.critical(self, "Condense", str(exc))

    def _start_send(self, content: str, extra_system: Optional[str] = None) -> None:
        content = (content or "").strip()
        if not content:
            return
        if not self.current_server:
            QtWidgets.QMessageBox.critical(self, "Server", "Please configure a server before sending.")
            return
        if not self._ensure_active_chat():
            QtWidgets.QMessageBox.critical(self, "Chat", "Could not find or create a chat.")
            return
        self.messages.append(("user", content))
        if self.current_session_id:
            self.store.save_message(self.current_session_id, "user", content)
        self.messages.append(("assistant", ""))
        assistant_index = len(self.messages) - 1
        self._render_messages()
        try:
            blocks = [{"role": role, "content": text} for role, text in self.messages[:-1]]
            merged_systems: List[str] = []
            if self.current_system_prompt:
                merged_systems.append(self.current_system_prompt)
            if extra_system:
                merged_systems.append(extra_system)
            if merged_systems:
                blocks.insert(0, {"role": "system", "content": "\n\n".join(merged_systems)})
            self._api_worker = ApiWorker(self.current_server, blocks, self.model_combo.currentText(), stream=True)
            self._api_worker.chunk.connect(lambda chunk, idx=assistant_index: self._handle_chunk(idx, chunk))
            self._api_worker.finished.connect(lambda full, idx=assistant_index: self._handle_finished(idx, full))
            self._api_worker.failed.connect(self._handle_error)
            self._api_worker.start()
            self.send_btn.setEnabled(False)
            self.status_label.setText("Waiting for response…")
        except Exception as exc:
            self.messages.pop()  # remove assistant placeholder
            QtWidgets.QMessageBox.critical(self, "Send failed", str(exc))
            self._render_messages()

    def _handle_chunk(self, idx: int, chunk: str) -> None:
        if 0 <= idx < len(self.messages):
            role, existing = self.messages[idx]
            if role == "assistant":
                self.messages[idx] = (role, existing + chunk)
                self._render_messages()

    def _handle_finished(self, idx: int, full: str) -> None:
        if 0 <= idx < len(self.messages):
            role, _ = self.messages[idx]
            self.messages[idx] = (role, full)
            if self.current_session_id:
                self.store.save_message(self.current_session_id, "assistant", full)
                self.store.update_session_last_model(self.current_session_id, self.model_combo.currentText())
                self.store.update_session_last_server(self.current_session_id, self.current_server.get("name", ""))
        self._render_messages()
        self.status_label.setText("Response received.")
        self._update_model_status()
        self.send_btn.setEnabled(True)
        self._api_worker = None

    def _handle_condense_chunk(self, chunk: str) -> None:
        self._condense_buffer += chunk
        self._render_messages()

    def _handle_condense_finished(self, full: str) -> None:
        self._summary_content = full or self._condense_buffer
        self._condense_buffer = ""
        self._condense_worker = None
        self.condense_btn.setEnabled(True)
        self._render_messages()
        self.status_label.setText("Condensed chat ready.")

    def _handle_condense_error(self, err: str) -> None:
        self._condense_buffer = ""
        self._summary_content = None
        self._condense_worker = None
        self.condense_btn.setEnabled(True)
        self._render_messages()
        self.status_label.setText(f"Condense failed: {err}")

    def _handle_error(self, err: str) -> None:
        if self.messages and self.messages[-1][0] == "assistant" and not self.messages[-1][1]:
            self.messages[-1] = ("assistant", f"[error] {err}")
        self._render_messages()
        self.status_label.setText(f"API error: {err}")
        self.send_btn.setEnabled(True)
        self._api_worker = None
        self._update_model_status()

    def _delete_message(self, msg_id: str) -> None:
        role, content = self._message_map.get(msg_id, ("", ""))
        if not content or self.current_session_id is None:
            return
        try:
            self.store.delete_message(self.current_session_id, role, content)
        except Exception:
            pass
        try:
            idx = int(msg_id.split("-")[-1])
            if 0 <= idx < len(self.messages):
                self.messages.pop(idx)
        except Exception:
            self.messages = [m for m in self.messages if not (m[0] == role and m[1] == content)]
        self._render_messages()

    def _current_folder_path(self) -> str:
        if self.current_page_path:
            path_obj = Path(self.current_page_path.lstrip("/"))
            return "/" + path_obj.parent.as_posix()
        session = self.store.get_session_by_id(self.current_session_id or -1) if self.current_session_id else None
        if session:
            return session.get("path", "/")
        return "/"

    def _go_to_page_for_chat(self, data: Dict) -> None:
        """Emit signal to focus the editor on the page associated with this chat."""
        path = data.get("path") or self._current_chat_path
        if path:
            self.chatNavigateRequested.emit(path)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("action:"):
            parts = href.split(":")
            if len(parts) >= 3:
                action, msg_id = parts[1], parts[2]
                if action == "summary":
                    if msg_id == "accept":
                        self._accept_summary()
                    elif msg_id == "reject":
                        self._reject_summary()
                    return
                if action == "copy":
                    content = self._message_map.get(msg_id, ("", ""))[1]
                    QtWidgets.QApplication.clipboard().setText(content)
                elif action == "goto":
                    self.chat_view.scrollToAnchor(msg_id)
                elif action == "delete":
                    self._delete_message(msg_id)
            return
        if href.startswith("msg:"):
            msg_id = href.split(":", 1)[1]
            self._show_history_context_menu_for(msg_id, self.mapFromGlobal(QCursor.pos()))
            return
        if href.startswith("http://") or href.startswith("https://"):
            QDesktopServices.openUrl(QUrl(href))

    def _accept_summary(self) -> None:
        """Replace history with condensed summary."""
        if not self.current_session_id or not self._summary_content:
            return
        summary_text = self._summary_content
        try:
            self.store.clear_chat(self.current_session_id)
            self.store.save_message(self.current_session_id, "assistant", summary_text)
        except Exception:
            pass
        self.messages = [("assistant", summary_text)]
        self._condense_buffer = ""
        self._summary_content = None
        self._render_messages()
        self.status_label.setText("Chat condensed.")

    def _reject_summary(self) -> None:
        self._condense_buffer = ""
        self._summary_content = None
        self._render_messages()

    def _update_model_status(self) -> None:
        current_model = self.model_combo.currentText()
        last_model = ""
        last_server = (self.current_server or {}).get("name", "")
        if self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session:
                last_model = session.get("last_model", "") or current_model
                last_server = session.get("last_server", "") or last_server
        self.model_status_label.setText(
            f"current model: {current_model} | last model used: {last_model} | last server: {last_server}"
        )
        # Track the page path associated with the current chat
        self._current_chat_path = None
        if self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session:
                self._current_chat_path = session.get("path")
        self._update_chat_name_label()

    def _update_chat_name_label(self) -> None:
        if not self.current_session_id:
            self.chat_name_label.setText("Chat context: —")
            return
        session = self.store.get_session_by_id(self.current_session_id)
        if not session:
            self.chat_name_label.setText("Chat context: —")
            return
        name = session.get("name") or "Chat"
        path = session.get("path") or ""
        display = path.replace("/", ":").lstrip(":") if path else name
        if path:
            self.chat_name_label.setText(f'Chat context: <a href="{path}">{display}</a>')
        else:
            self.chat_name_label.setText(f"Chat context: {display}")
        # Show prompt preview
        prompt_preview = (session.get("system_prompt") or "").strip()
        if prompt_preview:
            self.chat_name_label.setToolTip(prompt_preview[:500])
        else:
            self.chat_name_label.setToolTip("No system prompt set.")

    def set_font_size(self, size: int) -> None:
        """Apply font size to chat view and input."""
        self.font_size = max(6, min(24, size))
        self._apply_font_size()

    def get_font_size(self) -> int:
        return self.font_size

    def _apply_font_size(self) -> None:
        from PySide6.QtGui import QFont

        font = QFont()
        font.setPointSize(self.font_size)
        if hasattr(self, "chat_view"):
            self.chat_view.document().setDefaultFont(font)
        if hasattr(self, "input_edit"):
            self.input_edit.setFont(font)

    def _new_chat(self) -> None:
        folder_path = self._current_folder_path()
        name, ok = QtWidgets.QInputDialog.getText(self, "New Chat", "Chat context:", text="Chat")
        if not ok or not name.strip():
            return
        chat = self.store.create_named_chat(folder_path, name.strip())
        # Apply default server/model preferences to the new chat
        default_server = self._config_default_server()
        if default_server:
            self.store.update_session_last_server(chat["id"], default_server)
            if default_server in self.server_manager.list_server_names():
                self.server_combo.setCurrentText(default_server)
                self.current_server = self.server_manager.get_server(default_server)
        models = get_available_models(self.current_server or {})
        default_model = self._config_default_model()
        if default_model and default_model in models:
            self.store.update_session_last_model(chat["id"], default_model)
        elif models:
            self.store.update_session_last_model(chat["id"], models[0])
        self._load_chat_tree(select_id=chat["id"])
        self._load_chat_messages(chat["id"])
        self.status_label.setText(f"Created chat '{chat['name']}'")

    def _reset_chat_history(self) -> None:
        try:
            if not self.current_session_id:
                return
            confirm = QtWidgets.QMessageBox.question(
                self,
                "Clear Chat",
                "Are you sure you want to clear this chat history?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if confirm != QtWidgets.QMessageBox.Yes:
                return
            # Stop any active workers to avoid callbacks during reset
            try:
                if self._api_worker:
                    self._api_worker.finished.disconnect()
                    self._api_worker.failed.disconnect()
                    self._api_worker.chunk.disconnect()
            except Exception:
                pass
            try:
                if self._condense_worker:
                    self._condense_worker.finished.disconnect()
                    self._condense_worker.failed.disconnect()
                    self._condense_worker.chunk.disconnect()
            except Exception:
                pass
            self._api_worker = None
            self._condense_worker = None

            self.store.clear_chat(self.current_session_id)
            # Preserve last model/server for continuity
            if self.current_server:
                self.store.update_session_last_model(self.current_session_id, self.model_combo.currentText())
                self.store.update_session_last_server(self.current_session_id, self.current_server.get("name", ""))
            self._condense_buffer = ""
            self._summary_content = None
            self._load_chat_messages(self.current_session_id)
            self.status_label.setText("Chat history cleared.")
        except BaseException as exc:  # catch SystemExit/KeyboardInterrupt too to keep app alive
            print(f"[AIChat][reset] error clearing chat: {exc}")
            try:
                QtWidgets.QMessageBox.critical(self, "Reset Chat", f"Failed to clear chat:\n{exc}")
            except Exception:
                pass
            return

    def _load_system_prompts(self):
        self.system_prompts = []
        base = self.vault_root or str(Path.home())
        prompts_path = Path(base) / ".zimx" / "ai-system-prompts.json"
        if prompts_path.exists():
            try:
                with open(prompts_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            name = entry.get("name")
                            text = entry.get("text")
                            if name and text:
                                self.system_prompts.append({"name": name, "text": text})
            except Exception:
                pass
        if not self.system_prompts:
            self.system_prompts = [{"name": "Default", "text": "You are a helpful assistant."}]
        self.prompts_path = prompts_path

    def _save_system_prompts(self):
        if not hasattr(self, "prompts_path"):
            return
        try:
            self.prompts_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.prompts_path, "w", encoding="utf-8") as f:
                json.dump(self.system_prompts, f, indent=2)
        except Exception:
            pass

    def _open_prompt_dialog(self):
        dialog = SystemPromptDialog(self, prompts=list(self.system_prompts), current_prompt=self.current_system_prompt)
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.selected_prompt:
            self.current_system_prompt = dialog.selected_prompt
            if self.current_session_id:
                self.store.update_session_system_prompt(self.current_session_id, self.current_system_prompt)
            self.system_prompts = dialog.prompts
            self._save_system_prompts()

    def set_current_page(self, rel_path: Optional[str]) -> None:
        """Link chats to the currently open page without creating new chats."""
        self.current_page_path = rel_path
        if not rel_path:
            return
        folder_path = self.store._normalize_folder_path("/" + Path(rel_path.lstrip("/")).parent.as_posix())
        chat = self.store.get_session_by_path(folder_path, "chat")
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            self._load_chat_messages(chat["id"])
        else:
            self._update_model_status()
        self._update_chat_name_label()

    def open_chat_for_page(self, rel_path: Optional[str]) -> None:
        """Explicitly open (and create if needed) chat for the given page."""
        self.current_page_path = rel_path
        chat = self.store.get_or_create_chat_for_page(rel_path)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            self._load_chat_messages(chat["id"])
        self._update_model_status()
        self._update_chat_name_label()

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Switch backing store to the current vault's .zimx folder."""
        self.vault_root = vault_root
        self.store = AIChatStore(vault_root=vault_root)
        self.current_session_id = None
        self.messages = []
        self.chat_view.clear()
        self._load_system_prompts()
        self._load_chat_tree()
        self._select_default_chat()
        self._update_chat_name_label()
class SystemPromptDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, prompts: Optional[List[Dict[str, str]]] = None, current_prompt: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("System Prompts")
        self.prompts = prompts or []
        self.selected_prompt: Optional[str] = current_prompt
        self.resize(520, 520)
        self._dirty = False
        self._build_ui()
        self._select_prompt_by_text(current_prompt)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Prompt:"))
        self.prompt_combo = QtWidgets.QComboBox()
        self.prompt_combo.currentIndexChanged.connect(self._on_select)
        row.addWidget(self.prompt_combo, 1)
        del_btn = QtWidgets.QPushButton("Delete")
        del_btn.clicked.connect(self._delete_prompt)
        row.addWidget(del_btn)
        layout.addLayout(row)

        self.helper_label = QtWidgets.QLabel("Define how the AI should behave (role, tone, rules, formatting).")
        layout.addWidget(self.helper_label)

        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("Save Prompt")
        self.save_btn.clicked.connect(self._save_prompt)
        import_btn = QtWidgets.QPushButton("Import")
        import_btn.clicked.connect(self._import_prompts)
        export_btn = QtWidgets.QPushButton("Export")
        export_btn.clicked.connect(self._export_prompts)
        ok_btn = QtWidgets.QPushButton("Use Prompt")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(import_btn)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        self._reload_combo()

    def _reload_combo(self):
        self.prompt_combo.blockSignals(True)
        self.prompt_combo.clear()
        self.prompt_combo.addItem("Add New...")
        for prompt in self.prompts:
            self.prompt_combo.addItem(prompt.get("name", "Prompt"))
        self.prompt_combo.blockSignals(False)
        self._update_save_button_label()

    def _on_select(self, idx: int):
        if idx <= 0:
            self.text_edit.clear()
            self._update_save_button_label()
            return
        self.text_edit.setPlainText(self.prompts[idx - 1].get("text", ""))
        self.selected_prompt = self.prompts[idx - 1].get("text", "")
        self._dirty = False
        self._update_save_button_label()

    def _save_prompt(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            QtWidgets.QMessageBox.warning(self, "Prompt", "Prompt text cannot be empty.")
            return
        idx = self.prompt_combo.currentIndex()
        if idx <= 0:
            name, ok = QtWidgets.QInputDialog.getText(self, "Prompt name", "Name:")
            if not ok or not name.strip():
                return
            self.prompts.append({"name": name.strip(), "text": text})
            self._reload_combo()
            self.prompt_combo.setCurrentIndex(len(self.prompts))
        else:
            self.prompts[idx - 1]["text"] = text
            self._dirty = False
            self._update_save_button_label()

    def _delete_prompt(self):
        idx = self.prompt_combo.currentIndex()
        if idx <= 0 or idx - 1 >= len(self.prompts):
            return
        del self.prompts[idx - 1]
        self._reload_combo()
        self.text_edit.clear()
        self.selected_prompt = None
        self._update_save_button_label()

    def _import_prompts(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import prompts", "system_prompts.json", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            imported = []
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict) and "text" in entry:
                        imported.append({"name": entry.get("name", "Prompt"), "text": entry.get("text", "")})
            self.prompts.extend(imported)
            self._reload_combo()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import failed", str(exc))

    def _export_prompts(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export prompts", "system_prompts.json", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.prompts, f, indent=2)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))

    def _select_prompt_by_text(self, prompt_text: Optional[str]) -> None:
        if not prompt_text:
            self.prompt_combo.setCurrentIndex(0)
            return
        for idx, prompt in enumerate(self.prompts, start=1):
            if prompt.get("text") == prompt_text:
                self.prompt_combo.setCurrentIndex(idx)
                return
        self.prompt_combo.setCurrentIndex(0)

    def accept(self):
        idx = self.prompt_combo.currentIndex()
        if idx > 0 and idx - 1 < len(self.prompts):
            self.selected_prompt = self.prompts[idx - 1].get("text", "")
        else:
            self.selected_prompt = self.text_edit.toPlainText().strip() or None
        super().accept()

    def _update_save_button_label(self):
        idx = self.prompt_combo.currentIndex()
        if idx <= 0:
            label = "Save Prompt"
            enabled = bool(self.text_edit.toPlainText().strip())
        else:
            label = "Update Prompt"
            enabled = self._dirty
        if hasattr(self, "save_btn"):
            self.save_btn.setText(label)
            self.save_btn.setEnabled(enabled)

    def _on_text_changed(self):
        idx = self.prompt_combo.currentIndex()
        if idx > 0:
            self._dirty = True
        self._update_save_button_label()
