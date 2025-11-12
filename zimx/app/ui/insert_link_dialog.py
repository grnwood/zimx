"""Dialog for inserting links to other pages in colon notation."""
from __future__ import annotations

from PySide6.QtCore import Qt
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
from .path_utils import path_to_colon


class InsertLinkDialog(QDialog):
    """Dialog for searching and inserting page links in colon notation (PageA:PageB:PageC)."""
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Link")
        self.setModal(True)
        self.resize(450, 350)
        layout = QVBoxLayout()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to filter pages (e.g., 'PageC')…")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_current)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.accept)
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
        item = self.list_widget.currentItem()
        if not item:
            return None
        # The colon path is stored in UserRole
        return item.data(Qt.UserRole)

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
            self.list_widget.setCurrentRow(0)
