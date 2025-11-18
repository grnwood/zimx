from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)

from zimx.app import config
from .path_utils import path_to_colon, normalize_link_target


class EditLinkDialog(QDialog):
    """Dialog to edit a link with separate display text and target.

    - Link to: colon-notation path selected via type-ahead list (like Jump/Insert link)
    - Link text: free text to display in the editor
    """

    def __init__(self, link_to: str = "", link_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Link")
        self.resize(520, 420)
        
        # Track whether user has manually edited the link name
        self._link_name_manually_edited = bool(link_text)  # True if editing existing link with text

        layout = QVBoxLayout(self)
        form = QFormLayout()

        normalized_link = normalize_link_target(link_to)
        self.search_edit = QLineEdit(normalized_link)
        self.search_edit.setPlaceholderText("Type to filter pages…")
        self.search_edit.textChanged.connect(self._on_search_changed)
        form.addRow("Link to:", self.search_edit)

        self.text_edit = QLineEdit(link_text or normalized_link)
        self.text_edit.setPlaceholderText("Display name (defaults to link target)")
        self.text_edit.textChanged.connect(self._on_link_name_changed)
        self.text_edit.installEventFilter(self)
        form.addRow("Link Name:", self.text_edit)

        layout.addLayout(form)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._accept_from_list)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Select all text in link name field when it receives focus."""
        if obj is self.text_edit and event.type() == event.Type.FocusIn:
            # Use a single-shot timer to select all after the focus event completes
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.text_edit.selectAll)
            # Reset the manually edited flag when focus is received
            self._link_name_manually_edited = False
        return super().eventFilter(obj, event)
    
    def _accept_from_list(self):
        item = self.list_widget.currentItem()
        if item:
            normalized = item.data(Qt.UserRole)
            self.search_edit.setText(normalized)
            self.accept()
    
    def _on_search_changed(self):
        """Called when user types in the search field."""
        self._refresh()
        # Update link name if not manually edited
        if not self._link_name_manually_edited:
            self.text_edit.setText(self.search_edit.text())
    
    def _on_link_name_changed(self):
        """Track that user has manually edited the link name."""
        self._link_name_manually_edited = True
    
    def _on_selection_changed(self, current, previous):
        """Called when user navigates through the list with arrow keys."""
        if current:
            colon_path = current.data(Qt.UserRole)
            # Update the search field with the selected item
            self.search_edit.blockSignals(True)
            self.search_edit.setText(colon_path)
            self.search_edit.blockSignals(False)
            # Update link name if not manually edited
            if not self._link_name_manually_edited:
                self.text_edit.blockSignals(True)
                self.text_edit.setText(colon_path)
                self.text_edit.blockSignals(False)

    def _refresh(self) -> None:
        orig_term = self.search_edit.text().strip()
        search_term = orig_term
        if "#" in search_term:
            search_term = search_term.split("#", 1)[0].strip()
        normalized_term = search_term.lstrip(":")
        pages = config.search_pages(normalized_term)
        self.list_widget.clear()
        for page in pages:
            colon = path_to_colon(page["path"]) or ""
            if not colon:
                continue
            normalized_colon = normalize_link_target(colon)
            item = QListWidgetItem(normalized_colon)
            item.setToolTip(normalized_colon)
            item.setData(Qt.UserRole, normalized_colon)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            match_value = normalize_link_target(orig_term) if orig_term else normalized_term
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == match_value:
                    self.list_widget.setCurrentRow(i)
                    break
            else:
                self.list_widget.setCurrentRow(0)

    def link_to(self) -> str:
        return normalize_link_target(self.search_edit.text().strip())

    def link_text(self) -> str:
        return self.text_edit.text().strip()
