"""Dialog for inserting links to other pages in colon notation."""
from __future__ import annotations

from PySide6.QtCore import Qt
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
from .path_utils import path_to_colon


class InsertLinkDialog(QDialog):
    """Dialog for searching and inserting page links in colon notation (PageA:PageB:PageC)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Link")
        self.setModal(True)
        self.resize(520, 420)
        layout = QVBoxLayout()

        # Track whether user has manually edited the link name
        self._link_name_manually_edited = False

        # Form layout for Link to and Link Name fields
        form = QFormLayout()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type at least 3 characters to search…")
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._activate_current)
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

        layout.addLayout(form)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._accept_from_list)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)
        self.search.setFocus()
        # Start with an empty list; only search after >= 3 chars
        self.list_widget.clear()

    def selected_colon_path(self) -> str | None:
        """Return the selected page in colon notation (e.g., 'PageA:PageB:PageC')."""
        return self.search.text().strip() or None

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
                self.search.setText(colon_path)
            self.accept()

    def _on_search_changed(self):
        """Called when user types in the search field."""
        text = self.search.text().strip()
        if len(text) >= 3:
            self._refresh()
        else:
            self.list_widget.clear()

    def _on_link_name_changed(self):
        """Track that user has manually edited the link name."""
        self._link_name_manually_edited = True

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Event filter for link name field."""
        # Don't auto-select text to prevent interference with user input
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            # Only pass arrow keys to list if search field has focus
            # Don't interfere with arrow keys in link_name field
            previous_focus = self.focusWidget()
            if previous_focus is self.search or previous_focus is self.list_widget:
                QApplication.sendEvent(self.list_widget, event)
                if previous_focus is not self.list_widget:
                    previous_focus.setFocus()
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._activate_current():
                return
        super().keyPressEvent(event)

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
        """Refresh the list of pages based on search term (only called if >= 3 chars)."""
        term = self.search.text().strip()
        if len(term) < 3:
            self.list_widget.clear()
            return
        pages = config.search_pages(term)
        self.list_widget.clear()

        for page in pages:
            # Convert filesystem path to colon notation
            colon_path = path_to_colon(page["path"])
            if not colon_path:
                continue

            # Display format: show the colon path prominently
            display_text = colon_path

            # Also show title if it differs from the page name
            page_name = colon_path.split(":")[-1] if ":" in colon_path else colon_path
            title = page.get("title", "")
            if title and title != page_name:
                display_text = f"{colon_path} — {title}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, colon_path)
            item.setToolTip(colon_path)
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            # Select first item by default; user will press Enter/double-click to commit
            self.list_widget.setCurrentRow(0)
