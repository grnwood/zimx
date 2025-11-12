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
from zimx.server.adapters.files import PAGE_SUFFIX


class JumpToPageDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jump to page")
        self.setModal(True)
        self.resize(420, 320)
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
        self.search.setFocus()
        self._refresh()

    def selected_path(self) -> str | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
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
        if self.list_widget.currentItem():
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        term = self.search.text().strip()
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
        cleaned = path.strip("/")
        if not cleaned:
            return "/"
        parts = cleaned.split("/")
        if parts:
            last = parts[-1]
            if last.endswith(PAGE_SUFFIX):
                parts[-1] = last[: -len(PAGE_SUFFIX)]
        return "/".join(parts)
