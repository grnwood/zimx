from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QFont, QTextCharFormat, QKeyEvent
from PySide6.QtWidgets import QApplication, QCalendarWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from zimx.server.adapters.files import PAGE_SUFFIX


PATH_ROLE = Qt.UserRole + 1


class CalendarPanel(QWidget):
    """Calendar tab with a journal-focused navigation tree."""

    dateActivated = Signal(int, int, int)  # year, month, day
    pageActivated = Signal(str)  # relative path to a page

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.clicked.connect(self._on_date_clicked)
        self.calendar.currentPageChanged.connect(self._on_month_changed)
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.setFocusPolicy(Qt.StrongFocus)

        self.journal_tree = QTreeWidget()
        self.journal_tree.setHeaderHidden(True)
        self.journal_tree.setColumnCount(1)
        self.journal_tree.itemClicked.connect(self._on_tree_activated)
        self.journal_tree.itemActivated.connect(self._on_tree_activated)
        self.journal_tree.setFocusPolicy(Qt.StrongFocus)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.calendar)
        layout.addWidget(self.journal_tree, 1)
        self.setLayout(layout)

        self.vault_root: Optional[str] = None
        self.setFocusPolicy(Qt.StrongFocus)

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Set vault root for calendar and tree data."""
        self.vault_root = vault_root
        self.refresh()

    def refresh(self) -> None:
        """Refresh the journal tree and calendar highlights."""
        self._populate_tree()
        self._update_calendar_dates()

    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Move the calendar to a specific date and expand the tree."""
        target = QDate(year, month, day)
        self.calendar.setSelectedDate(target)
        self._update_calendar_dates(year, month)
        self._expand_to_date(target)

    def _on_month_changed(self, year: int, month: int) -> None:
        self._update_calendar_dates(year, month)

    def _on_date_clicked(self, date: QDate) -> None:
        """Emit selected date and sync the tree."""
        self._expand_to_date(date)
        self.dateActivated.emit(date.year(), date.month(), date.day())

    def _populate_tree(self) -> None:
        """Build a tree rooted at Journal with year/month/day nodes."""
        had_tree = self.journal_tree.topLevelItemCount() > 0
        expanded_paths = self._capture_expanded_paths()
        selected_path = self._capture_selected_path()

        self.journal_tree.clear()
        root_item = QTreeWidgetItem(["Journal"])
        root_item.setData(0, Qt.UserRole, None)
        root_item.setData(0, PATH_ROLE, "Journal")
        root_item.setExpanded("Journal" in expanded_paths or not had_tree)
        self.journal_tree.addTopLevelItem(root_item)

        if not self.vault_root:
            return

        journal_path = Path(self.vault_root) / "Journal"
        if not journal_path.exists():
            return

        self._add_children(root_item, journal_path)

        if expanded_paths:
            self._restore_expanded_paths(root_item, expanded_paths)
        if selected_path:
            self._restore_selection(selected_path)

    def _update_calendar_dates(self, year: Optional[int] = None, month: Optional[int] = None) -> None:
        """Bold dates with saved journal entries for the visible month."""
        if not self.vault_root:
            return

        current = self.calendar.selectedDate()
        year = year or current.year()
        month = month or current.month()

        journal_path = Path(self.vault_root) / "Journal" / str(year) / f"{month:02d}"
        days_in_month = QDate(year, month, 1).daysInMonth()

        default_format = QTextCharFormat()
        bold_format = QTextCharFormat()
        bold_font = QFont()
        bold_font.setBold(True)
        bold_font.setWeight(QFont.Black)
        bold_format.setFont(bold_font)

        for day in range(1, days_in_month + 1):
            self.calendar.setDateTextFormat(QDate(year, month, day), default_format)

        if not journal_path.exists():
            return

        for day_dir in journal_path.iterdir():
            if not day_dir.is_dir() or not day_dir.name.isdigit():
                continue
            day_num = int(day_dir.name)
            day_file = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"
            if day_file.exists():
                self.calendar.setDateTextFormat(QDate(year, month, day_num), bold_format)

    def _on_tree_activated(self, item: QTreeWidgetItem, column: int | None = None) -> None:  # noqa: ARG002
        """Sync calendar to the activated tree item and open pages."""
        date_value = item.data(0, Qt.UserRole)
        path_value = item.data(0, PATH_ROLE)

        if isinstance(date_value, QDate):
            self.calendar.setSelectedDate(date_value)
            self._update_calendar_dates(date_value.year(), date_value.month())
            # Only trigger journal-date open for day-level nodes (directories), not child pages
            path_obj = Path(self.vault_root) / str(path_value).lstrip("/") if path_value and self.vault_root else None
            if not path_obj or path_obj.is_dir():
                self.dateActivated.emit(date_value.year(), date_value.month(), date_value.day())

        if path_value and self.vault_root:
            page_path = Path(self.vault_root) / str(path_value).lstrip("/")
            # For folder nodes, prefer the matching .txt inside that folder if it exists
            if page_path.is_dir():
                candidate = page_path / f"{page_path.name}{PAGE_SUFFIX}"
                if candidate.exists():
                    page_path = candidate
            if page_path.is_file():
                rel_path = "/" + page_path.relative_to(self.vault_root).as_posix()
                self.pageActivated.emit(rel_path)

    def _expand_to_date(self, date: QDate) -> None:
        """Expand and select the tree path for the given date."""
        target_year = f"{date.year()}"
        target_month = f"{date.month():02d}"
        target_day = f"{date.day():02d}"

        root = self.journal_tree.topLevelItem(0)
        if not root:
            return

        year_item = self._find_child_by_text(root, target_year)
        if not year_item:
            return
        self.journal_tree.expandItem(year_item)

        month_item = self._find_child_by_text(year_item, target_month)
        if not month_item:
            return
        self.journal_tree.expandItem(month_item)

        day_item = self._find_child_by_text(month_item, target_day)
        if not day_item:
            return

        self.journal_tree.setCurrentItem(day_item)
        self.journal_tree.scrollToItem(day_item)

    def _find_child_by_text(self, parent: QTreeWidgetItem, text: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child and child.text(0) == text:
                return child
        return None

    def keyPressEvent(self, event):  # type: ignore[override]
        """Allow arrow keys and vi-style nav to move within the journal tree."""
        key_map = {
            Qt.Key_H: Qt.Key_Left,
            Qt.Key_L: Qt.Key_Right,
            Qt.Key_J: Qt.Key_Down,
            Qt.Key_K: Qt.Key_Up,
        }
        target_key = key_map.get(event.key(), event.key())
        if target_key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self.journal_tree.setFocus(Qt.OtherFocusReason)
            forwarded = QKeyEvent(event.type(), target_key, event.modifiers())
            QApplication.sendEvent(self.journal_tree, forwarded)
            event.accept()
            return
        super().keyPressEvent(event)

    def _capture_expanded_paths(self) -> set[str]:
        """Remember which nodes are expanded so refreshes don't collapse them."""
        paths: set[str] = set()

        def _walk(item: QTreeWidgetItem) -> None:
            path = item.data(0, PATH_ROLE)
            if path and item.isExpanded():
                paths.add(path)
            for i in range(item.childCount()):
                child = item.child(i)
                if child:
                    _walk(child)

        root = self.journal_tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            if child:
                _walk(child)
        return paths

    def _capture_selected_path(self) -> Optional[str]:
        current = self.journal_tree.currentItem()
        if not current:
            return None
        return current.data(0, PATH_ROLE)

    def _restore_expanded_paths(self, root: QTreeWidgetItem, expanded_paths: set[str]) -> None:
        def _walk(item: QTreeWidgetItem) -> None:
            path = item.data(0, PATH_ROLE)
            if path in expanded_paths:
                item.setExpanded(True)
            for i in range(item.childCount()):
                child = item.child(i)
                if child:
                    _walk(child)

        _walk(root)

    def _restore_selection(self, path: str) -> None:
        item = self._find_item_by_path(path)
        if item:
            self.journal_tree.setCurrentItem(item)
            self.journal_tree.scrollToItem(item)

    def _find_item_by_path(self, path: str) -> Optional[QTreeWidgetItem]:
        def _walk(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            if item.data(0, PATH_ROLE) == path:
                return item
            for i in range(item.childCount()):
                child = item.child(i)
                if not child:
                    continue
                found = _walk(child)
                if found:
                    return found
            return None

        root = self.journal_tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            if child:
                found = _walk(child)
                if found:
                    return found
        return None

    def _add_children(self, parent_item: QTreeWidgetItem, path: Path, inherited_date: Optional[QDate] = None) -> None:
        """Recursively add directories and files under the Journal root."""
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for entry in entries:
            if entry.is_dir():
                child_date = inherited_date
                parts = entry.parts[-3:]
                if len(parts) == 3 and all(part.isdigit() for part in parts):
                    try:
                        year, month, day = map(int, parts)
                        child_date = QDate(year, month, day)
                    except ValueError:
                        pass

                item = QTreeWidgetItem([entry.name])
                item.setData(0, Qt.UserRole, child_date)
                item.setData(0, PATH_ROLE, entry.relative_to(self.vault_root).as_posix() if self.vault_root else entry.name)
                parent_item.addChild(item)
                self._add_children(item, entry, child_date)
            # Mirror left nav: only directories, no individual .txt nodes
