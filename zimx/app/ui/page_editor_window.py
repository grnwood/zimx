from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
import httpx

from PySide6.QtCore import QTimer, Qt, QByteArray
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QMessageBox, QToolBar, QLabel

from .markdown_editor import MarkdownEditor
from .insert_link_dialog import InsertLinkDialog
from .page_load_logger import PageLoadLogger, PAGE_LOGGING_ENABLED
from zimx.app import config


class PageEditorWindow(QMainWindow):
    """Lightweight single-page editor window (no navigation panes)."""

    def __init__(
        self,
        api_base: str,
        vault_root: str,
        page_path: str,
        read_only: bool,
        open_in_main_callback: Callable[[str], None],
        local_auth_token: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.api_base = api_base.rstrip("/")
        self.vault_root = vault_root
        self._source_path = page_path  # lock the target path for all saves
        self.page_path = page_path
        self._read_only = read_only
        self._open_in_main = open_in_main_callback
        headers = {"X-Local-UI-Token": local_auth_token} if local_auth_token else None
        self.http = httpx.Client(base_url=self.api_base, timeout=10.0, headers=headers)
        self._badge_base_style = "border: 1px solid #666; padding: 2px 6px; border-radius: 3px;"
        self._font_size = config.load_popup_font_size(14)

        self.editor = MarkdownEditor()
        # Disable context menus in the popup editor to keep right-click actions only in the main window
        self.editor.setContextMenuPolicy(Qt.NoContextMenu)
        self.editor.set_context(self.vault_root, self._source_path)
        self.editor.set_font_point_size(self._font_size)
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
        self.editor.set_vi_mode_enabled(config.load_vi_mode_enabled())
        self.editor.set_read_only_mode(self._read_only)
        self.editor.linkActivated.connect(self._forward_link_to_main)
        self.editor.focusLost.connect(lambda: self._save_current_file(auto=True))
        self.setCentralWidget(self.editor)

        self._last_saved_content: Optional[str] = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True))
        self.editor.textChanged.connect(lambda: self._autosave_timer.start())
        self.editor.document().modificationChanged.connect(lambda _: self._update_dirty_indicator())

        self._build_toolbar()
        self._load_content()
        self._update_title()
        self._size_and_center(parent)
        self._restore_geometry()
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setInterval(400)
        self._geometry_timer.setSingleShot(True)

        # Status badges
        self._dirty_status_label = QLabel("")
        self._dirty_status_label.setObjectName("popupDirtyStatusLabel")
        self._dirty_status_label.setStyleSheet(self._badge_base_style + " background-color: transparent; margin-right: 6px;")
        self._dirty_status_label.setToolTip("Unsaved changes")
        self.statusBar().addPermanentWidget(self._dirty_status_label, 0)
        self._update_dirty_indicator()

        # Font shortcuts (popup-local)
        zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        zoom_in.activated.connect(lambda: self._adjust_font_size(1))
        zoom_out.activated.connect(lambda: self._adjust_font_size(-1))
        plus_shortcut = QShortcut(QKeySequence("+"), self)
        minus_shortcut = QShortcut(QKeySequence("-"), self)
        plus_shortcut.activated.connect(lambda: self._adjust_font_size(1))
        minus_shortcut.activated.connect(lambda: self._adjust_font_size(-1))

    def set_read_only(self, read_only: bool) -> None:
        """Toggle read-only state and refresh window badges/title."""
        self._read_only = bool(read_only)
        try:
            self.editor.set_read_only_mode(self._read_only)
        except Exception:
            try:
                self.editor.setReadOnly(self._read_only)
            except Exception:
                pass
        self._update_title()
        self._update_dirty_indicator()

    def _size_and_center(self, parent=None) -> None:
        """Size the popup similar to the parent editor and center it."""
        try:
            if parent and hasattr(parent, "size"):
                self.resize(parent.size())
        except Exception:
            pass
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            win_size = self.size()
            x = geo.x() + (geo.width() - win_size.width()) // 2
            y = geo.y() + (geo.height() - win_size.height()) // 2
            self.move(x, y)

    def _restore_geometry(self) -> None:
        """Restore saved geometry if available."""
        try:
            geom = config.load_popup_editor_geometry()
            if geom:
                self.restoreGeometry(QByteArray.fromBase64(geom.encode("ascii")))
        except Exception:
            pass

    def _save_geometry(self) -> None:
        """Persist current geometry to the vault config."""
        try:
            geom = self.saveGeometry().toBase64().data().decode("ascii")
            config.save_popup_editor_geometry(geom)
        except Exception:
            pass

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Page")
        toolbar.setMovable(False)
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        save_action.triggered.connect(lambda: self._save_current_file(auto=False))
        toolbar.addAction(save_action)
        toolbar.addSeparator()
        font_up = QAction("A+", self)
        font_up.setToolTip("Increase font size")
        font_up.triggered.connect(lambda: self._adjust_font_size(1))
        font_down = QAction("A-", self)
        font_down.setToolTip("Decrease font size")
        font_down.triggered.connect(lambda: self._adjust_font_size(-1))
        toolbar.addAction(font_down)
        toolbar.addAction(font_up)

        self.addToolBar(Qt.TopToolBarArea, toolbar)

    def _load_content(self) -> None:
        tracer = PageLoadLogger(self._source_path) if PAGE_LOGGING_ENABLED else None
        if tracer:
            tracer.mark("api read start")
        try:
            resp = self.http.post("/api/file/read", json={"path": self._source_path})
            resp.raise_for_status()
            content = resp.json().get("content", "")
            if tracer:
                try:
                    content_len = len(content.encode("utf-8"))
                except Exception:
                    content_len = len(content or "")
                tracer.mark(f"api read complete bytes={content_len}")
            try:
                self.editor.set_page_load_logger(tracer)
            except Exception:
                pass
            self.editor.set_markdown(content)
            if tracer:
                tracer.mark("editor content applied")
            self.editor.document().setModified(False)
            self._last_saved_content = content
            self.statusBar().showMessage("Ready")
            if tracer:
                tracer.end("ready for edit (popup)")
        except httpx.HTTPError as exc:
            if tracer:
                tracer.mark(f"api read failed ({exc})")
            QMessageBox.critical(self, "Error", f"Failed to load page: {exc}")

    def _update_title(self) -> None:
        label = Path(self._source_path).name or self._source_path
        suffix = "ZimX Editor"
        if self._read_only:
            self.setWindowTitle(f"{label} | Read-Only | {suffix}")
        else:
            self.setWindowTitle(f"{label} | {suffix}")
        self._update_dirty_indicator()

    def _is_dirty(self) -> bool:
        current = self.editor.to_markdown()
        return current != (self._last_saved_content or "")

    def _ensure_writable(self, auto: bool) -> bool:
        if not self._read_only:
            return True
        if auto:
            return False
        QMessageBox.warning(
            self,
            "Read-Only",
            "This page is open in read-only mode.\nTo save changes, enable write access in the main window.",
        )
        return False

    def _save_current_file(self, auto: bool = False) -> None:
        if not self._is_dirty():
            return
        if not self._ensure_writable(auto):
            return
        payload = {"path": self._source_path, "content": self.editor.to_markdown()}
        print(f"[ZimX Popup] Writing page {self._source_path} -> /api/file/write payload bytes={len(payload['content'].encode('utf-8'))}")
        try:
            resp = self.http.post("/api/file/write", json=payload)
            resp.raise_for_status()
            print(f"[ZimX Popup] Write OK {self._source_path} status={resp.status_code}")
        except httpx.HTTPError as exc:
            try:
                body = exc.response.text if exc.response else str(exc)
                status = exc.response.status_code if exc.response else "n/a"
                print(f"[ZimX Popup] Write FAILED {self._source_path} status={status} body={body}")
            except Exception:
                print(f"[ZimX Popup] Write FAILED {self._source_path}: {exc}")
            if not auto:
                QMessageBox.critical(self, "Save Failed", f"Failed to save: {exc}")
            return
        self._last_saved_content = payload["content"]
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        self.statusBar().showMessage("Saved", 2000)
        # Notify parent/main window to refresh if editing the same page
        try:
            if hasattr(self._open_in_main, "__call__"):
                self._open_in_main(self._source_path, force=True, refresh_only=True)
        except Exception:
            pass
        self._update_dirty_indicator()

    def _insert_link(self) -> None:
        if not self.vault_root:
            QMessageBox.information(self, "ZimX", "Select a vault before inserting links.")
            return
        # Disabled in popup editor (links are handled in main window)
        QMessageBox.information(self, "Link Insert Disabled", "Insert Link is available only in the main editor.")

    def _forward_link_to_main(self, link: str) -> None:
        if link:
            self._open_in_main(link)

    def _update_dirty_indicator(self) -> None:
        if not hasattr(self, "_dirty_status_label"):
            return
        if self._read_only:
            self._dirty_status_label.setText("O/")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #9e9e9e; color: #f5f5f5; margin-right: 6px; text-decoration: line-through;"
            )
            self._dirty_status_label.setToolTip("Read-only: changes cannot be saved in this window")
            return
        dirty = self._is_dirty()
        if dirty:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #e57373; color: #000; margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("Unsaved changes")
        else:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #81c784; color: #000; margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("All changes saved")

    def _adjust_font_size(self, delta: int) -> None:
        new_size = max(6, min(24, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        self.editor.set_font_point_size(self._font_size)
        try:
            config.save_popup_font_size(self._font_size)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Autosave on close if dirty and writable
        self._save_current_file(auto=False)
        self._save_geometry()
        try:
            self.http.close()
        except Exception:
            pass
        super().closeEvent(event)
