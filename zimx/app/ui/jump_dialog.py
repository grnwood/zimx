from __future__ import annotations

from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from zimx.app import config
from zimx.server.adapters.files import PAGE_SUFFIX
from .path_utils import path_to_colon


class JumpToPageDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jump to page")
        self.setModal(True)
        
        # Set up geometry save timer (debounced)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)
        
        # Make dialog same size as insert link dialog
        self.resize(640, 360)
        layout = QVBoxLayout()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Start typing to filter pages…")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_current)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)
        
        # Restore saved geometry after layout is set up
        self._restore_geometry()
        
        self.search.setFocus()
        self._refresh()

    def selected_path(self) -> str | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def keyPressEvent(self, event):  # type: ignore[override]
        # Handle arrow keys and vi-mode shortcuts (Shift+J/K)
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            previous_focus = self.focusWidget()
            QApplication.sendEvent(self.list_widget, event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            return
        # Handle Shift+J (down) and Shift+K (up) as arrow key equivalents
        elif event.key() == Qt.Key_J and (event.modifiers() & Qt.ShiftModifier):
            previous_focus = self.focusWidget()
            # Create a synthetic Down arrow key event
            down_event = event.__class__(event.type(), Qt.Key_Down, Qt.NoModifier)
            QApplication.sendEvent(self.list_widget, down_event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            return
        elif event.key() == Qt.Key_K and (event.modifiers() & Qt.ShiftModifier):
            previous_focus = self.focusWidget()
            # Create a synthetic Up arrow key event
            up_event = event.__class__(event.type(), Qt.Key_Up, Qt.NoModifier)
            QApplication.sendEvent(self.list_widget, up_event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._activate_current():
                return
        super().keyPressEvent(event)

    def _activate_current(self) -> bool:
        if self.list_widget.currentItem():
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        term = self.search.text().strip()
        if term.startswith(":"):
            term = term.lstrip(":")
        pages = config.search_pages(term)
        self.list_widget.clear()
        for page in pages:
            display = page["title"] or page["path"]
            pretty_path = self._friendly_path(page["path"])
            item = QListWidgetItem(f"{display} — {pretty_path}")
            item.setData(Qt.UserRole, page["path"])
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _friendly_path(self, path: str) -> str:
        return path_to_colon(path)
    
    def _restore_geometry(self) -> None:
        """Restore saved dialog geometry."""
        saved_geometry = config.load_dialog_geometry("jump_dialog")
        if saved_geometry:
            try:
                print(f"[Dialog] Restoring jump dialog geometry: {len(saved_geometry)} chars")
                geometry_bytes = QByteArray.fromBase64(saved_geometry.encode('ascii'))
                result = self.restoreGeometry(geometry_bytes)
                print(f"[Dialog] Jump dialog geometry restore result: {result}")
            except Exception as e:
                print(f"[Dialog] Failed to restore jump dialog geometry: {e}")
        else:
            print("[Dialog] No saved jump dialog geometry found")
    
    def _save_geometry(self) -> None:
        """Save current dialog geometry."""
        try:
            geometry_bytes = self.saveGeometry()
            geometry_b64 = geometry_bytes.toBase64().data().decode('ascii')
            config.save_dialog_geometry("jump_dialog", geometry_b64)
            print(f"[Dialog] Saved jump dialog geometry: {len(geometry_b64)} chars")
        except Exception as e:
            print(f"[Dialog] Failed to save jump dialog geometry: {e}")
    
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Handle dialog resize: save geometry with debounce."""
        super().resizeEvent(event)
        self.geometry_save_timer.start()
    
    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Save dialog geometry when closing."""
        self.geometry_save_timer.stop()  # Cancel any pending save
        self._save_geometry()  # Immediate save on close
        super().closeEvent(event)
