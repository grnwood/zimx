from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt, Signal, QTimer, QObject
from PySide6.QtGui import (
    QAction,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
    QTextCursor,
    QKeyEvent,
    QPainter,
    QColor,
    QFont,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTreeView,
    QDialog,
    QWidget,
    QVBoxLayout,
    QFrame,
    QLabel,
    QHBoxLayout,
)

from zimx.app import config, indexer
from zimx.server.adapters.files import PAGE_SUFFIX

from .markdown_editor import MarkdownEditor
from .task_panel import TaskPanel
from .jump_dialog import JumpToPageDialog
from .preferences_dialog import PreferencesDialog


PATH_ROLE = int(Qt.ItemDataRole.UserRole)
TYPE_ROLE = PATH_ROLE + 1
OPEN_ROLE = TYPE_ROLE + 1


class InlineNameEdit(QLineEdit):
    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.returnPressed.connect(self._emit_submit)

    def _emit_submit(self) -> None:
        self.submitted.emit(self.text())

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.cancelled.emit()
            self.deleteLater()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self.cancelled.emit()
        self.deleteLater()


class VaultTreeView(QTreeView):
    enterActivated = Signal()

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            self.collapseAll()
            event.accept()
            return
        if event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Down, Qt.Key_Up):
            direction = 1 if event.key() == Qt.Key_Down else -1
            self._walk_tree(direction)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            super().keyPressEvent(event)
            self.enterActivated.emit()
            return
        super().keyPressEvent(event)

    def _walk_tree(self, direction: int) -> None:
        indexes = self._flatten()
        if not indexes:
            return
        current = self.currentIndex()
        try:
            idx = indexes.index(current)
        except ValueError:
            idx = -1 if direction > 0 else 0
        new_idx = idx + direction
        new_idx = max(0, min(len(indexes) - 1, new_idx))
        if new_idx == idx:
            return
        prev = current if current.isValid() else None
        target = indexes[new_idx]
        if prev and prev.data(TYPE_ROLE):
            self.collapse(prev)
        if target.data(TYPE_ROLE):
            self.expand(target)
        self.setCurrentIndex(target)
        self.scrollTo(target)

    def _flatten(self) -> list[QModelIndex]:
        model = self.model()
        if model is None:
            return []
        order: list[QModelIndex] = []

        def recurse(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                order.append(idx)
                recurse(idx)

        recurse(QModelIndex())
        return order


class MainWindow(QMainWindow):
    def __init__(self, api_base: str) -> None:
        super().__init__()
        self.setWindowTitle("ZimX Desktop")
        self.api_base = api_base.rstrip("/")
        self.http = httpx.Client(base_url=self.api_base, timeout=10.0)
        self.vault_root: Optional[str] = None
        self.vault_root_name: Optional[str] = None
        self.current_path: Optional[str] = None

        self.tree_view = VaultTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["Vault"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setHeaderHidden(False)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._open_context_menu)
        self.tree_view.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.tree_view.enterActivated.connect(self._focus_editor_from_tree)
        self.dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)

        self.editor = MarkdownEditor()
        self.editor.imageSaved.connect(self._on_image_saved)
        self.editor.textChanged.connect(lambda: self.autosave_timer.start())
        self.editor.focusLost.connect(lambda: self._save_current_file(auto=True))
        self.editor.cursorMoved.connect(self._on_cursor_changed)
        self.editor.linkActivated.connect(self._open_camel_link)
        self.font_size = 14
        self.editor.set_font_point_size(self.font_size)
        # Load vi-mode block cursor preference
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())

        self.task_panel = TaskPanel()
        self.task_panel.refresh()
        self.task_panel.taskActivated.connect(self._open_task_from_panel)
        self._inline_editor: Optional[InlineNameEdit] = None
        self._pending_selection: Optional[str] = None
        self._cursor_cache: dict[str, int] = {}
        self._suspend_autosave = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30_000)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True))

        # Vi-mode state
        self._vi_mode_active = False
        # Optional vi debug logging (set True to enable noisy logs)
        self._vi_debug = False

        # Debounced cursor-position persistence
        self._cursor_save_timer = QTimer(self)
        self._cursor_save_timer.setSingleShot(True)
        self._cursor_save_timer.setInterval(250)
        self._cursor_save_timer.timeout.connect(self._flush_cursor_save)
        self._pending_cursor_save_path = None  # type: Optional[str]
        self._pending_cursor_save_pos = None   # type: Optional[int]

        editor_split = QSplitter()
        editor_split.addWidget(self.editor)
        editor_split.addWidget(self.task_panel)
        editor_split.setStretchFactor(0, 4)
        editor_split.setStretchFactor(1, 2)

        splitter = QSplitter()
        splitter.addWidget(self.tree_view)
        splitter.addWidget(editor_split)
        splitter.setStretchFactor(1, 5)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Container (no vi-mode banner)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(container)

        # No overlay/indicator widgets; vi-mode is represented by editor cursor style

        self._build_toolbar()
        self._register_shortcuts()
        self._vi_filter_targets: list[QObject] = []
        self._install_vi_mode_filters()
        self.statusBar().showMessage("Select a vault to get started")
        self._default_status_stylesheet = self.statusBar().styleSheet()
        last_vault = config.load_last_vault()
        if last_vault:
            self._set_vault(last_vault)

    # --- UI wiring -----------------------------------------------------
    def _build_toolbar(self) -> None:
        self.toolbar = self.addToolBar("Main")
        self.toolbar.setMovable(False)

        open_vault_action = QAction("Open Vault", self)
        open_vault_action.triggered.connect(self._select_vault)
        self.toolbar.addAction(open_vault_action)

        create_vault_action = QAction("New Vault", self)
        create_vault_action.triggered.connect(self._create_vault)
        self.toolbar.addAction(create_vault_action)

        new_journal_action = QAction("New Today", self)
        new_journal_action.triggered.connect(self._create_journal_today)
        self.toolbar.addAction(new_journal_action)

        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_action.triggered.connect(self._save_current_file)
        self.toolbar.addAction(save_action)

        # Right-aligned spacer before preferences icon
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)

        # Preferences/settings cog icon
        prefs_action = QAction("Preferences", self)
        prefs_action.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        prefs_action.triggered.connect(self._open_preferences)
        self.toolbar.addAction(prefs_action)

        # Store default style to restore later
        self._default_toolbar_stylesheet = self.toolbar.styleSheet()

    def _register_shortcuts(self) -> None:
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._save_current_file)
        zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        zoom_in.activated.connect(lambda: self._adjust_font_size(1))
        zoom_out.activated.connect(lambda: self._adjust_font_size(-1))
        jump_shortcut = QShortcut(QKeySequence("Ctrl+J"), self)
        jump_shortcut.activated.connect(self._jump_to_page)
        task_cycle = QShortcut(QKeySequence(Qt.Key_F12), self)
        task_cycle.activated.connect(self.editor.toggle_task_state)
        nav_up = QShortcut(QKeySequence("Alt+Up"), self)
        nav_down = QShortcut(QKeySequence("Alt+Down"), self)
        nav_pg_up = QShortcut(QKeySequence("Alt+PgUp"), self)
        nav_pg_down = QShortcut(QKeySequence("Alt+PgDown"), self)
        nav_up.activated.connect(lambda: self._navigate_tree(-1, leaves_only=False))
        nav_down.activated.connect(lambda: self._navigate_tree(1, leaves_only=False))
        nav_pg_up.activated.connect(lambda: self._navigate_tree(-1, leaves_only=True))
        nav_pg_down.activated.connect(lambda: self._navigate_tree(1, leaves_only=True))

    # --- Vault actions -------------------------------------------------
    def _select_vault(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Vault")
        if not directory:
            return
        self._set_vault(directory)

    def _create_vault(self) -> None:
        target_path, _ = QFileDialog.getSaveFileName(self, "Create Vault", str(Path.home() / "NewVault"))
        if not target_path:
            return
        target = Path(target_path)
        try:
            if target.exists():
                if not target.is_dir():
                    self._alert("A file with that name already exists.")
                    return
                reply = QMessageBox.question(
                    self,
                    "Use Existing Folder",
                    f"{target} already exists. Use it as the vault?",
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            else:
                target.mkdir(parents=True)
                self._seed_vault(target)
        except OSError as exc:
            self._alert(f"Failed to create vault: {exc}")
            return
        self._set_vault(str(target))

    def _seed_vault(self, root: Path) -> None:
        root_page = root / f"{root.name}{PAGE_SUFFIX}"
        if not root_page.exists():
            root_page.write_text(
                f"# {root.name}\n\nWelcome to your vault. Use the tree to add new pages or jump into Inbox to capture ideas.\n",
                encoding="utf-8",
            )
        starter_pages = [
            ("Inbox", "# Inbox\n\nCapture quick notes here.\n"),
            ("Journal", "# Journal\n\nUse the New Today action to create a dated entry.\n"),
            ("README", "# README\n\nDescribe how you plan to use this space.\n"),
        ]
        for name, body in starter_pages:
            page_dir = root / name
            page_dir.mkdir(parents=True, exist_ok=True)
            page_file = page_dir / f"{name}{PAGE_SUFFIX}"
            if not page_file.exists():
                page_file.write_text(body, encoding="utf-8")

    def _set_vault(self, directory: str) -> None:
        self.task_panel.clear()
        config.set_active_vault(None)
        try:
            resp = self.http.post("/api/vault/select", json={"path": directory})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to set vault: {exc}")
            return
        self.vault_root = resp.json().get("root")
        self.vault_root_name = Path(self.vault_root).name if self.vault_root else None
        if self.vault_root:
            config.set_active_vault(self.vault_root)
            config.save_last_vault(self.vault_root)
            self.font_size = config.load_font_size(self.font_size)
            self.editor.set_font_point_size(self.font_size)
        self.editor.set_context(self.vault_root, None)
        self.statusBar().showMessage(f"Vault: {self.vault_root}")
        self._populate_vault_tree()
        self._reindex_vault()

    def _populate_vault_tree(self) -> None:
        self._cancel_inline_editor()
        if not self.vault_root:
            return
        try:
            resp = self.http.get("/api/vault/tree")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to load vault tree: {exc}")
            return
        data = resp.json().get("tree", [])
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        for node in data:
            self._add_tree_node(self.tree_model.invisibleRootItem(), node)
        self.tree_view.expandAll()
        if self._pending_selection:
            self._select_tree_path(self._pending_selection)
            self._pending_selection = None
        self.task_panel.refresh()

    def _add_tree_node(self, parent: QStandardItem, node: dict) -> None:
        item = QStandardItem(node["name"])
        folder_path = node.get("path")
        open_path = node.get("open_path")
        has_children = bool(node.get("children"))
        item.setData(folder_path, PATH_ROLE)
        item.setData(has_children, TYPE_ROLE)
        item.setData(open_path, OPEN_ROLE)
        icon = self.dir_icon if has_children or folder_path == "/" else self.file_icon
        item.setIcon(icon)
        item.setEditable(False)
        parent.appendRow(item)
        for child in node.get("children", []):
            self._add_tree_node(item, child)

    def _on_selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self._debug(
            f"Tree selection changed: current={self._describe_index(current)}, previous={self._describe_index(previous)}"
        )
        if previous.isValid():
            prev_target = previous.data(OPEN_ROLE) or previous.data(PATH_ROLE)
            if prev_target and prev_target == self.current_path:
                self._save_current_file(auto=True)
        if not current.isValid():
            self._debug("Tree selection cleared (no valid index).")
            return
        open_target = current.data(OPEN_ROLE) or current.data(PATH_ROLE)
        self._debug(f"Tree selection target resolved to: {open_target!r}")
        if not open_target:
            self._debug("Tree selection skipped: no open target.")
            return
        if open_target == self.current_path:
            self._debug("Tree selection skipped: already editing this path.")
            return
        try:
            self._open_file(open_target)
        except Exception as exc:
            self._debug(f"Tree selection crash while opening {open_target!r}: {exc!r}")
            raise

    def _open_file(self, path: str, retry: bool = False) -> None:
        if not path or path == self.current_path:
            return
        self.autosave_timer.stop()
        try:
            resp = self.http.post("/api/file/read", json={"path": path})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._alert(f"Failed to open {path}: {exc}")
            return
        except httpx.HTTPError as exc:
            self._alert(f"Failed to open {path}: {exc}")
            return
        content = resp.json().get("content", "")
        self.editor.set_context(self.vault_root, path)
        self.current_path = path
        self._suspend_autosave = True
        self.editor.set_markdown(content)
        self._suspend_autosave = False
        indexer.index_page(path, content)
        self.task_panel.refresh()
        stored_pos = config.load_cursor_position(path) if config.has_active_vault() else None
        if stored_pos is not None:
            cursor = self.editor.textCursor()
            cursor.setPosition(min(stored_pos, len(content)))
            self.editor.setTextCursor(cursor)
            self._cursor_cache[path] = stored_pos
        else:
            self.editor.moveCursor(QTextCursor.Start)
            self._cursor_cache[path] = self.editor.textCursor().position()
        # Always show editing status; vi-mode banner is separate
        self.statusBar().showMessage(f"Editing {path}")

    def _save_current_file(self, auto: bool = False) -> None:
        if self._suspend_autosave:
            self._debug("Autosave suppressed (suspend flag set).")
            return
        if not self.current_path:
            if not auto:
                self._alert("No file selected to save.")
            return
        editor_path = self.editor.current_relative_path()
        if editor_path and self.current_path and editor_path != self.current_path:
            self._debug(
                f"Autosave skipped due to path mismatch editor={editor_path} window={self.current_path}"
            )
            return
        payload = {"path": self.current_path, "content": self.editor.to_markdown()}
        try:
            resp = self.http.post("/api/file/write", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            if not auto:
                self._alert(f"Failed to save {self.current_path}: {exc}")
            return
        if config.has_active_vault():
            position = self._cursor_cache.get(self.current_path, self.editor.textCursor().position())
            config.save_cursor_position(self.current_path, position)
            indexer.index_page(self.current_path, payload["content"])
            self.task_panel.refresh()
        self.autosave_timer.stop()
        message = "Auto-saved" if auto else "Saved"
        self.statusBar().showMessage(f"{message} {self.current_path}", 2000 if auto else 4000)

    def _create_journal_today(self) -> None:
        if not self.vault_root:
            self._alert("Select a vault before creating journal entries.")
            return
        try:
            resp = self.http.post("/api/journal/today", json={})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create journal entry: {exc}")
            return
        path = resp.json().get("path")
        if path:
            self._open_file(path)

    def _on_image_saved(self, filename: str) -> None:
        self.statusBar().showMessage(f"Image pasted as {filename}", 5000)

    def _on_cursor_changed(self, position: int) -> None:
        # Update in-memory cache immediately for UX (e.g., focus restore)
        if self.current_path:
            self._cursor_cache[self.current_path] = position

        # Debounce disk writes to avoid UI stalls during rapid navigation
        if self.current_path and config.has_active_vault():
            self._pending_cursor_save_path = self.current_path
            self._pending_cursor_save_pos = position
            # restart the timer
            self._cursor_save_timer.start()

    def _flush_cursor_save(self) -> None:
        path = self._pending_cursor_save_path
        pos = self._pending_cursor_save_pos
        self._pending_cursor_save_path = None
        self._pending_cursor_save_pos = None
        if not path or pos is None:
            return
        if not config.has_active_vault():
            return
        # Only write if still on same file
        if self.current_path != path:
            return
        try:
            config.save_cursor_position(path, pos)
        except Exception as exc:
            # Non-fatal; log quietly
            self._debug(f"Cursor save failed for {path}: {exc!r}")

    def _jump_to_page(self) -> None:
        if not config.has_active_vault():
            return
        dlg = JumpToPageDialog(self)
        if dlg.exec() == QDialog.Accepted:
            target = dlg.selected_path()
            if target:
                self._select_tree_path(target)
                self._open_file(target)

    def _open_preferences(self) -> None:
        """Open the preferences dialog."""
        dlg = PreferencesDialog(self)
        if dlg.exec() == QDialog.Accepted:
            # Reload vi-mode cursor setting and apply to editor
            self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
            # Re-apply vi-mode state to refresh cursor
            if self._vi_mode_active:
                self.editor.set_vi_mode(True)

    def _open_task_from_panel(self, path: str, line: int) -> None:
        self._select_tree_path(path)
        self._open_file(path)
        # Focus first, then go to line so the selection isn't cleared
        self.editor.setFocus()
        self._goto_line(line, select_line=True)

    def _open_camel_link(self, name: str) -> None:
        if not self.current_path:
            self._alert("Open a page before following CamelCase links.")
            return
        rel_current = Path(self.current_path.lstrip("/"))
        parent_folder = rel_current.parent
        target_rel = parent_folder / name if parent_folder.parts else Path(name)
        rel_string = target_rel.as_posix()
        folder_path = f"/{rel_string}" if rel_string else "/"
        if not self._ensure_page_folder(folder_path, allow_existing=True):
            return
        target_file = self._folder_to_file_path(folder_path)
        if not target_file:
            return
        self._pending_selection = target_file
        self._populate_vault_tree()
        self._open_file(target_file)

    def _adjust_font_size(self, delta: int) -> None:
        new_size = max(10, min(36, self.font_size + delta))
        if new_size == self.font_size:
            return
        self.font_size = new_size
        self.editor.set_font_point_size(self.font_size)
        if config.has_active_vault():
            config.save_font_size(self.font_size)

    def _focus_editor_from_tree(self) -> None:
        self._focus_editor()

    def _focus_editor(self) -> None:
        self.editor.setFocus()
        if self.current_path:
            pos = self._cursor_cache.get(self.current_path) or config.load_cursor_position(self.current_path)
            if pos is not None:
                cursor = self.editor.textCursor()
                cursor.setPosition(min(pos, len(self.editor.toPlainText())))
                self.editor.setTextCursor(cursor)

    def _goto_line(self, line: int, select_line: bool = False) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        if line > 1:
            cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, line - 1)
        if select_line:
            cursor.select(QTextCursor.LineUnderCursor)
        self.editor.setTextCursor(cursor)

    def _ensure_page_folder(self, folder_path: str, allow_existing: bool = False) -> bool:
        payload = {"path": folder_path, "is_dir": True}
        try:
            resp = self.http.post("/api/path/create", json=payload)
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if allow_existing and exc.response is not None and exc.response.status_code == 409:
                return True
            self._alert(f"Failed to create page {folder_path}: {exc}")
            return False
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create page {folder_path}: {exc}")
            return False

    def _folder_to_file_path(self, folder_path: str) -> Optional[str]:
        if not self.vault_root_name:
            return None
        cleaned = (folder_path or "/").strip()
        if cleaned in ("", "/"):
            return f"/{self.vault_root_name}{PAGE_SUFFIX}"
        rel = Path(cleaned.lstrip("/"))
        name = rel.name or self.vault_root_name
        rel_file = rel / f"{name}{PAGE_SUFFIX}"
        return f"/{rel_file.as_posix()}"

    # --- Tree context menu -------------------------------------------
    def _open_context_menu(self, pos: QPoint) -> None:
        if not self.vault_root:
            return
        index = self.tree_view.indexAt(pos)
        global_pos = self.tree_view.viewport().mapToGlobal(pos)
        menu = QMenu(self)
        if index.isValid():
            path = index.data(PATH_ROLE) or "/"
            menu.addAction(
                "New Page",
                lambda checked=False, p=path, idx=index: self._start_inline_creation(p, global_pos, idx),
            )
            open_path = index.data(OPEN_ROLE)
            if path != "/":
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(
                    lambda checked=False, p=path, op=open_path: self._delete_path(p, op, global_pos)
                )
        else:
            menu.addAction("New Page", lambda checked=False: self._start_inline_creation("/", global_pos, None))
        if menu.actions():
            menu.exec(global_pos)

    def _start_inline_creation(
        self,
        parent_path: str,
        global_pos: QPoint,
        anchor_index: Optional[QModelIndex] = None,
    ) -> None:
        self._cancel_inline_editor()
        editor = InlineNameEdit(self.tree_view.viewport())
        editor.setPlaceholderText("Page name")
        editor.submitted.connect(lambda name: self._handle_inline_submit(parent_path, name))
        editor.cancelled.connect(self._inline_editor_cancelled)
        self._inline_editor = editor
        if anchor_index and anchor_index.isValid():
            rect = self.tree_view.visualRect(anchor_index)
            viewport_pos = rect.bottomLeft()
            viewport_pos.setY(viewport_pos.y() + 4)
        else:
            viewport_pos = self.tree_view.viewport().mapFromGlobal(global_pos)
        editor.move(viewport_pos)
        width = max(200, self.tree_view.viewport().width() - 40)
        editor.resize(width, editor.sizeHint().height())
        editor.show()
        editor.setFocus()

    def _handle_inline_submit(self, parent_path: str, name: str) -> None:
        name = name.strip()
        if not name:
            self._cancel_inline_editor()
            return
        if "/" in name:
            self.statusBar().showMessage("Names cannot contain '/'", 4000)
            return
        target_path = self._join_paths(parent_path, name)
        try:
            resp = self.http.post("/api/path/create", json={"path": target_path, "is_dir": True})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                self.statusBar().showMessage("Name already exists", 4000)
            else:
                self._alert(f"Failed to create entry: {exc}")
            return
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create entry: {exc}")
            return
        self._cancel_inline_editor()
        file_path = self._folder_to_file_path(target_path)
        if file_path:
            self._pending_selection = file_path
        self._populate_vault_tree()

    def _inline_editor_cancelled(self) -> None:
        self._inline_editor = None

    def _cancel_inline_editor(self) -> None:
        if self._inline_editor:
            editor = self._inline_editor
            self._inline_editor = None
            try:
                editor.cancelled.disconnect(self._inline_editor_cancelled)
            except Exception:
                pass
            editor.deleteLater()

    def _delete_path(self, folder_path: str, open_path: Optional[str], global_pos: QPoint) -> None:
        if folder_path == "/":
            self.statusBar().showMessage("Cannot delete the root page.", 4000)
            return
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("Delete")
        confirm.setText(f"Delete {folder_path}? This cannot be undone.")
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm.setDefaultButton(QMessageBox.No)
        confirm.adjustSize()
        confirm.move(global_pos - QPoint(confirm.width() // 2, confirm.height() // 2))
        result = confirm.exec()
        if result != QMessageBox.Yes:
            return
        try:
            resp = self.http.post("/api/path/delete", json={"path": folder_path})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to delete {folder_path}: {exc}")
            return
        if open_path:
            config.delete_page_index(open_path)
        if self.current_path and open_path and self.current_path == open_path:
            self.current_path = None
            self.editor.set_markdown("")
        self._populate_vault_tree()
        self.task_panel.refresh()

    def _parent_path(self, index: QModelIndex) -> str:
        parent = index.parent()
        if parent.isValid():
            return parent.data(PATH_ROLE) or "/"
        return "/"

    def _join_paths(self, parent_path: str, name: str) -> str:
        parent = (parent_path or "/").rstrip("/")
        if parent in ("", "/"):
            return f"/{name}"
        return f"{parent}/{name}"

    def _select_tree_path(self, target_path: str) -> None:
        match = self._find_item(self.tree_model.invisibleRootItem(), target_path)
        if match:
            index = match.index()
            self.tree_view.setCurrentIndex(index)
            self.tree_view.scrollTo(index)

    def _find_item(self, parent: QStandardItem, target: str) -> Optional[QStandardItem]:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            child_path = child.data(PATH_ROLE)
            child_open = child.data(OPEN_ROLE)
            if target in (child_path, child_open):
                return child
            found = self._find_item(child, target)
            if found:
                return found
        return None

    def _gather_indexes(self, leaves_only: bool) -> list[QModelIndex]:
        model = self.tree_model
        flat: list[QModelIndex] = []

        def recurse(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                is_dir = bool(idx.data(TYPE_ROLE))
                if not leaves_only or not is_dir:
                    flat.append(idx)
                recurse(idx)

        recurse(QModelIndex())
        return [idx for idx in flat if idx.isValid()]

    def _navigate_tree(self, delta: int, leaves_only: bool) -> None:
        indexes = self._gather_indexes(leaves_only)
        if not indexes:
            return
        current = self.tree_view.currentIndex()
        try:
            idx = indexes.index(current)
        except ValueError:
            idx = -1 if delta > 0 else 0
        new_idx = max(0, min(len(indexes) - 1, idx + delta))
        if new_idx == idx:
            return
        target = indexes[new_idx]
        self.tree_view.setCurrentIndex(target)
        self.tree_view.scrollTo(target)

    def _reindex_vault(self) -> None:
        if not self.vault_root or not config.has_active_vault():
            return
        root = Path(self.vault_root)
        for path in root.rglob(f"*{PAGE_SUFFIX}"):
            rel = f"/{path.relative_to(root).as_posix()}"
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            indexer.index_page(rel, content)
        self.task_panel.refresh()

    # --- Utilities -----------------------------------------------------
    def _alert(self, message: str) -> None:
        QMessageBox.critical(self, "ZimX", message)

    def _toggle_vi_mode(self) -> None:
        """Toggle vi-mode navigation on/off with visual status bar indicator."""
        self._vi_mode_active = not self._vi_mode_active
        self._apply_vi_mode_statusbar_style()

    def _install_vi_mode_filters(self) -> None:
        for target in getattr(self, "_vi_filter_targets", []):
            target.removeEventFilter(self)
        self._vi_filter_targets = []
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._vi_filter_targets.append(app)

    def eventFilter(self, obj, event):  # type: ignore[override]
        # (Removed overlay repositioning logic; using status bar indicator)
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_CapsLock:
                # Toggle vi-mode and consume the event to prevent CapsLock from activating
                self._toggle_vi_mode()
                return True  # Block the event completely
            if self._vi_mode_active:
                target = self._vi_mode_target_widget()
                if target:
                    mapping = self._translate_vi_key_event(event)
                    if mapping:
                        if self._vi_debug:
                            key_name = chr(event.key()) if Qt.Key_A <= event.key() <= Qt.Key_Z else f"Key_{event.key()}"
                            self._debug(f"Vi-mode: {key_name} -> {mapping[0]} with mods {mapping[1]}")
                        self._dispatch_vi_navigation(target, mapping)
                        return True
                    # Block unmapped letter keys ONLY if they don't have Control modifier
                    # (Control+key are commands that should be allowed through)
                    if Qt.Key_A <= event.key() <= Qt.Key_Z:
                        has_ctrl = bool(event.modifiers() & Qt.ControlModifier)
                        if not has_ctrl:
                            if self._vi_debug:
                                self._debug(f"Vi-mode: blocking unmapped key {chr(event.key())}")
                            return True
        elif event.type() == QEvent.KeyRelease:
            # Also consume CapsLock release to fully prevent OS from toggling caps
            if event.key() == Qt.Key_CapsLock:
                return True
        return super().eventFilter(obj, event)

    # Removed overlay helpers

    def _vi_mode_target_widget(self) -> QWidget | None:
        widget = self.focusWidget()
        if widget is None:
            return None
        if widget is self.editor or (self.editor and self.editor.isAncestorOf(widget)):
            return widget
        if widget is self.tree_view or self.tree_view.isAncestorOf(widget):
            return widget
        if widget is self.task_panel or self.task_panel.isAncestorOf(widget):
            return widget
        return None

    def _translate_vi_key_event(self, event: QKeyEvent) -> tuple[int, Qt.KeyboardModifiers] | None:
        # Check for disallowed modifiers (but allow keys with only Shift or no modifiers)
        key = event.key()
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        
        # Check if there are modifiers other than Shift that we don't handle
        disallowed = event.modifiers() & ~(Qt.ShiftModifier | Qt.KeypadModifier)
        if disallowed:
            return None

        target_key = None
        target_modifiers = Qt.KeyboardModifiers(Qt.NoModifier)

        if key == Qt.Key_J:
            target_key = Qt.Key_Down if not shift else Qt.Key_PageDown
        elif key == Qt.Key_K:
            target_key = Qt.Key_Up if not shift else Qt.Key_PageUp
        elif key == Qt.Key_N and shift:
            target_key = Qt.Key_Down
            target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_U and not shift:
            target_key = Qt.Key_Z
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_A:
            target_key = Qt.Key_Home
            if shift:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_H:
            if shift:
                target_key = Qt.Key_Home
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            else:
                target_key = Qt.Key_Left
        elif key == Qt.Key_L and not shift:
            target_key = Qt.Key_Right
        elif key == Qt.Key_Semicolon:
            target_key = Qt.Key_End
            if shift:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_X and not shift:
            target_key = Qt.Key_X
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_P and not shift:
            target_key = Qt.Key_V
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_D and not shift:
            # Delete current line: use custom handler with Alt+Delete as marker
            target_key = Qt.Key_Delete
            target_modifiers = Qt.KeyboardModifiers(Qt.AltModifier)

        if target_key is None:
            return None

        return target_key, target_modifiers

    def _dispatch_vi_navigation(self, target: QWidget, mapping: tuple[int, Qt.KeyboardModifiers]) -> None:
        key, modifiers = mapping

        # Special handling for delete line (d key maps to a special marker)
        if key == Qt.Key_Delete and modifiers == Qt.KeyboardModifiers(Qt.AltModifier):
            # This is our custom "delete line" command
            if hasattr(target, 'textCursor'):
                cursor = target.textCursor()
                # Move to start of the current line
                cursor.movePosition(QTextCursor.StartOfLine)
                # Select to the start of the next line (includes newline)
                cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor)
                # If we're at the last line, make sure we still delete it
                if not cursor.hasSelection():
                    cursor.movePosition(QTextCursor.StartOfLine)
                    cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                target.setTextCursor(cursor)
                return

        # Prefer direct cursor operations for the editor to reduce repaint churn
        if target is self.editor or (hasattr(target, 'textCursor') and hasattr(target, 'setTextCursor')):
            try:
                cur = target.textCursor()
                keep = QTextCursor.KeepAnchor if (modifiers & Qt.ShiftModifier) else QTextCursor.MoveAnchor
                if key == Qt.Key_Down:
                    cur.movePosition(QTextCursor.Down, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Up:
                    cur.movePosition(QTextCursor.Up, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Left and not (modifiers & Qt.ShiftModifier):
                    cur.movePosition(QTextCursor.Left, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Right and not (modifiers & Qt.ShiftModifier):
                    cur.movePosition(QTextCursor.Right, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Home:
                    cur.movePosition(QTextCursor.StartOfLine, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_End:
                    cur.movePosition(QTextCursor.EndOfLine, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Z and (modifiers & Qt.ControlModifier):
                    try:
                        target.undo()
                        return
                    except Exception:
                        pass
                if key == Qt.Key_X and (modifiers & Qt.ControlModifier):
                    try:
                        target.cut()
                        return
                    except Exception:
                        pass
                if key == Qt.Key_V and (modifiers & Qt.ControlModifier):
                    try:
                        target.paste()
                        return
                    except Exception:
                        pass
            except Exception:
                # Fallback to synthetic below
                pass

        # Fallback: synthesize key events for non-editor targets or unhandled keys
        press_event = QKeyEvent(QEvent.KeyPress, key, modifiers)
        release_event = QKeyEvent(QEvent.KeyRelease, key, modifiers)
        QApplication.sendEvent(target, press_event)
        QApplication.sendEvent(target, release_event)

    def _apply_vi_mode_statusbar_style(self) -> None:
        # Switch editor cursor styling for vi-mode; no banners/overlays
        self.editor.set_vi_mode(self._vi_mode_active)

    # (Removed move/resize overlays; not used)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_current_file(auto=True)
        self.http.close()
        config.set_active_vault(None)
        return super().closeEvent(event)

    def _describe_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return "<invalid>"
        return (
            f"path={index.data(PATH_ROLE)}, open={index.data(OPEN_ROLE)}, is_dir={bool(index.data(TYPE_ROLE))}"
        )

    def _debug(self, message: str) -> None:
        print(f"[ZimX] {message}")
