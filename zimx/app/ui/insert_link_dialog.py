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
        self.search.setPlaceholderText("Type to filter pages (e.g., 'PageC')…")
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._activate_current)
        form.addRow("Link to:", self.search)
        
        self.link_name = QLineEdit()
        self.link_name.setPlaceholderText("Display name (defaults to link target)")
        self.link_name.textChanged.connect(self._on_link_name_changed)
        self.link_name.returnPressed.connect(self._activate_current)
        self.link_name.installEventFilter(self)
        form.addRow("Link Name:", self.link_name)
        
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
        self.search.setFocus()
        self._refresh()

    def selected_colon_path(self) -> str | None:
        """Return the selected page in colon notation (e.g., 'PageA:PageB:PageC')."""
        return self.search.text().strip() or None
    
    def selected_link_name(self) -> str | None:
        """Return the display name for the link."""
        name = self.link_name.text().strip()
        # Default to the colon path if no custom name provided
        return name if name else self.selected_colon_path()
    
    def _accept_from_list(self):
        """Accept dialog when item in list is double-clicked."""
        item = self.list_widget.currentItem()
        if item:
            self.search.setText(item.data(Qt.UserRole))
            self.accept()
    
    def _on_search_changed(self):
        """Called when user types in the search field."""
        self._refresh()
        # Update link name if not manually edited
        if not self._link_name_manually_edited:
            self.link_name.setText(self.search.text())
    
    def _on_link_name_changed(self):
        """Track that user has manually edited the link name."""
        self._link_name_manually_edited = True
    
    def _on_selection_changed(self, current, previous):
        """Called when user navigates through the list with arrow keys."""
        if current:
            colon_path = current.data(Qt.UserRole)
            # Update the search field with the selected item
            self.search.blockSignals(True)
            self.search.setText(colon_path)
            self.search.blockSignals(False)
            # Update link name if not manually edited
            if not self._link_name_manually_edited:
                self.link_name.blockSignals(True)
                self.link_name.setText(colon_path)
                self.link_name.blockSignals(False)

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Select all text in link name field when it receives focus."""
        if obj is self.link_name and event.type() == event.Type.FocusIn:
            # Use a single-shot timer to select all after the focus event completes
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.link_name.selectAll)
            # Reset the manually edited flag when focus is received
            self._link_name_manually_edited = False
        return super().eventFilter(obj, event)
    
    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            # Pass arrow keys to list while keeping search focused
            previous_focus = self.focusWidget()
            QApplication.sendEvent(self.list_widget, event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._activate_current():
                return
        super().keyPressEvent(event)

    def _activate_current(self) -> bool:
        """Accept dialog if an item is selected."""
        if self.list_widget.currentItem():
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        """Refresh the list of pages based on search term."""
        term = self.search.text().strip()
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
            # Try to select exact match if present
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == term:
                    self.list_widget.setCurrentRow(i)
                    break
            else:
                self.list_widget.setCurrentRow(0)
