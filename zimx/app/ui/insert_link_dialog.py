"""Dialog for inserting links to other pages in colon notation."""
from __future__ import annotations

from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from zimx.app import config
from .path_utils import path_to_colon, normalize_link_target


class InsertLinkDialog(QDialog):
    """Dialog for searching and inserting page links in colon notation (PageA:PageB:PageC)."""

    def __init__(self, parent=None, selected_text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Link")
        self.setModal(True)
        
        # Set up geometry save timer (debounced)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)
        
        # Make dialog wider than tall (~80 chars wide)
        self.resize(640, 360)
        layout = QVBoxLayout()

        # Track whether user has manually edited the link name
        self._link_name_manually_edited = False
        self._ignore_search_change = False

        # Form layout for Link to and Link Name fields
        form = QFormLayout()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to search pages…")
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._on_search_return)
        # Disable autocomplete to prevent Qt from suggesting completions
        self.search.setCompleter(None)
        form.addRow("Link to:", self.search)

        self.link_name = QLineEdit()
        self.link_name.setPlaceholderText("Display name (optional)")
        self.link_name.textChanged.connect(self._on_link_name_changed)
        self.link_name.returnPressed.connect(self._activate_current)
        self.link_name.installEventFilter(self)
        # Completely disable autocomplete
        self.link_name.setCompleter(None)
        # Also clear any auto-completion behavior
        try:
            from PySide6.QtWidgets import QCompleter
            empty_completer = QCompleter([])
            empty_completer.setCompletionMode(QCompleter.NoCompletion)
            self.link_name.setCompleter(empty_completer)
        except Exception:
            pass
        form.addRow("Link Name:", self.link_name)
        
        # Initialize with selected text if provided
        if selected_text:
            clean_text = selected_text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()
            normalized = normalize_link_target(clean_text)
            self.search.setText(normalized)
            self.link_name.setText(normalized)
            # Select all text in search field so typing replaces it
            self.search.selectAll()

        layout.addLayout(form)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._accept_from_list)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)
        
        # Restore saved geometry after layout is set up
        self._restore_geometry()
        
        self.search.setFocus()
        if selected_text:
            self._refresh()
        else:
            self.list_widget.clear()

    def selected_colon_path(self) -> str | None:
        """Return the selected page in colon notation (e.g., 'PageA:PageB:PageC')."""
        normalized = normalize_link_target(self.search.text().strip())
        return normalized or None

    def selected_link_name(self) -> str | None:
        """Return the display name for the link, or None if empty."""
        name = self.link_name.text().strip()
        return name or None

    def _accept_from_list(self):
        """Accept dialog when item in list is double-clicked."""
        item = self.list_widget.currentItem()
        if item:
            colon_path = item.data(Qt.UserRole)
            if colon_path:
                normalized = normalize_link_target(colon_path)
                self.search.setText(normalized)
            self.accept()

    def _on_search_changed(self):
        """Called when user types in the search field."""
        if self._ignore_search_change:
            return
        self._refresh()

    def _on_link_name_changed(self):
        """Track that user has manually edited the link name."""
        self._link_name_manually_edited = True

    def _on_selection_changed(self, current, previous):
        """Called when user navigates through the list with arrow keys or Shift+J/K."""
        if current:
            colon_path = current.data(Qt.UserRole)
            if colon_path:
                # Update the search field with the selected item
                self._ignore_search_change = True
                self.search.blockSignals(True)
                self.search.setText(colon_path)
                self.search.blockSignals(False)
                self._ignore_search_change = False
                # Update link name if not manually edited
                if not self._link_name_manually_edited:
                    self.link_name.blockSignals(True)
                    self.link_name.setText(colon_path)
                    self.link_name.blockSignals(False)

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Event filter for link name field."""
        # Don't auto-select text to prevent interference with user input
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # type: ignore[override]
        # Handle arrow keys and vi-mode shortcuts (Shift+J/K)
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            # Only pass arrow keys to list if search field has focus
            # Don't interfere with arrow keys in link_name field
            previous_focus = self.focusWidget()
            if previous_focus is self.search or previous_focus is self.list_widget:
                QApplication.sendEvent(self.list_widget, event)
                if previous_focus is not self.list_widget:
                    previous_focus.setFocus()
                return
        # Handle Shift+J (down) and Shift+K (up) as arrow key equivalents
        elif event.key() == Qt.Key_J and (event.modifiers() & Qt.ShiftModifier):
            previous_focus = self.focusWidget()
            # Create a synthetic Down arrow key event
            down_event = QKeyEvent(event.type(), Qt.Key_Down, Qt.NoModifier)
            QApplication.sendEvent(self.list_widget, down_event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            return
        elif event.key() == Qt.Key_K and (event.modifiers() & Qt.ShiftModifier):
            previous_focus = self.focusWidget()
            # Create a synthetic Up arrow key event
            up_event = QKeyEvent(event.type(), Qt.Key_Up, Qt.NoModifier)
            QApplication.sendEvent(self.list_widget, up_event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._activate_current():
                return
        super().keyPressEvent(event)

    def _on_search_return(self) -> None:
        """Handle Enter key in search field - create new page if needed."""
        # Check if current text matches an existing page
        current_text = self.search.text().strip()
        if not current_text:
            return
            
        # Check if exact match exists in list
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.UserRole) == current_text:
                # Exact match found, just accept
                self.list_widget.setCurrentItem(item)
                self.accept()
                return
        
        # No exact match - this will create a new page
        self.accept()

    def _activate_current(self) -> bool:
        """Accept dialog if an item is selected, or use what's typed in the search field."""
        item = self.list_widget.currentItem()
        if item:
            colon_path = item.data(Qt.UserRole)
            if colon_path:
                self.search.setText(colon_path)
            self.accept()
            return True
        elif self.search.text().strip():
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        """Refresh the list of pages based on search term."""
        term = self.search.text().strip()
        if not term:
            self.list_widget.clear()
            return

        search_term = term
        if "#" in search_term:
            search_term = search_term.split("#", 1)[0].strip()
        normalized_term = search_term.lstrip(":")
        if ":" in normalized_term:
            normalized_term = normalized_term.replace(":", "/")
        query = normalized_term or search_term
        pages = config.search_pages(query)
        self.list_widget.clear()

        for page in pages:
            # Convert filesystem path to colon notation
            colon_path = path_to_colon(page["path"])
            if not colon_path:
                continue
            normalized_colon = normalize_link_target(colon_path)
            display_text = normalized_colon

            # Also show title if it differs from the page name
            page_name = normalized_colon.split(":")[-1] if ":" in normalized_colon else normalized_colon
            title = page.get("title", "")
            if title and title != page_name:
                display_text = f"{normalized_colon} — {title}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, normalized_colon)
            item.setToolTip(normalized_colon)
            self.list_widget.addItem(item)

        # Do not auto-select results; user controls selection via keyboard/mouse
        if self.list_widget.count() == 0:
            self.list_widget.clearSelection()
    
    def _restore_geometry(self) -> None:
        """Restore saved dialog geometry."""
        saved_geometry = config.load_dialog_geometry("insert_link_dialog")
        if saved_geometry:
            try:
                print(f"[Dialog] Restoring insert link dialog geometry: {len(saved_geometry)} chars")
                geometry_bytes = QByteArray.fromBase64(saved_geometry.encode('ascii'))
                result = self.restoreGeometry(geometry_bytes)
                print(f"[Dialog] Insert link dialog geometry restore result: {result}")
            except Exception as e:
                print(f"[Dialog] Failed to restore insert link dialog geometry: {e}")
        else:
            print("[Dialog] No saved insert link dialog geometry found")
    
    def _save_geometry(self) -> None:
        """Save current dialog geometry."""
        try:
            geometry_bytes = self.saveGeometry()
            geometry_b64 = geometry_bytes.toBase64().data().decode('ascii')
            config.save_dialog_geometry("insert_link_dialog", geometry_b64)
            print(f"[Dialog] Saved insert link dialog geometry: {len(geometry_b64)} chars")
        except Exception as e:
            print(f"[Dialog] Failed to save insert link dialog geometry: {e}")
    
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Handle dialog resize: save geometry with debounce."""
        super().resizeEvent(event)
        self.geometry_save_timer.start()
    
    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Save dialog geometry when closing."""
        self.geometry_save_timer.stop()  # Cancel any pending save
        self._save_geometry()  # Immediate save on close
        super().closeEvent(event)
