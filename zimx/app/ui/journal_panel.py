from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Set

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QTextCharFormat, QFont
from PySide6.QtWidgets import (
    QCalendarWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSplitter,
)

from zimx.server.adapters.files import PAGE_SUFFIX


class JournalPanel(QWidget):
    """Panel for journal navigation with calendar and tree view."""
    
    dateActivated = Signal(int, int, int)  # year, month, day
    journalPageActivated = Signal(str)  # path to journal page
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        
        # Calendar widget for date selection
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.clicked.connect(self._on_date_clicked)
        self.calendar.currentPageChanged.connect(self._on_month_changed)
        
        # Tree widget for journal navigation (filtered to Journal folder)
        self.journal_tree = QTreeWidget()
        self.journal_tree.setHeaderHidden(True)
        self.journal_tree.setColumnCount(1)
        self.journal_tree.itemDoubleClicked.connect(self._on_tree_item_activated)
        
        # Layout with splitter
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.calendar)
        splitter.addWidget(self.journal_tree)
        splitter.setStretchFactor(0, 0)  # Calendar fixed size
        splitter.setStretchFactor(1, 1)  # Tree takes remaining space
        
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.setLayout(layout)
        
        self.vault_root: Optional[str] = None
        self.virtual_pages: Set[str] = set()  # Track pages opened but not yet saved
        
        # Set calendar to today's date initially
        self.calendar.setSelectedDate(QDate.currentDate())
    
    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Set the vault root and refresh the journal tree."""
        self.vault_root = vault_root
        self.refresh()
    
    def refresh(self) -> None:
        """Refresh the journal tree view to show current journal structure."""
        self.journal_tree.clear()
        
        if not self.vault_root:
            return
        
        # Find Journal folder in vault
        vault_path = Path(self.vault_root)
        journal_path = vault_path / "Journal"
        
        if not journal_path.exists() or not journal_path.is_dir():
            return
        
        # Build tree from Journal folder
        self._populate_tree_from_path(self.journal_tree.invisibleRootItem(), journal_path, journal_path)
        
        # Update calendar to bold dates with existing entries
        self._update_calendar_dates()
    
    def add_virtual_page(self, path: str) -> None:
        """Mark a page as virtual (opened but not saved)."""
        self.virtual_pages.add(path)
        self.refresh()
    
    def mark_page_saved(self, path: str) -> None:
        """Mark a previously virtual page as saved."""
        if path in self.virtual_pages:
            self.virtual_pages.discard(path)
            self.refresh()
    
    def _update_calendar_dates(self) -> None:
        """Scan journal folder and bold dates that have saved entries."""
        if not self.vault_root:
            return
        
        vault_path = Path(self.vault_root)
        journal_path = vault_path / "Journal"
        
        if not journal_path.exists():
            return
        
        # Get current month/year from calendar
        current_date = self.calendar.selectedDate()
        year = current_date.year()
        month = current_date.month()
        
        # Bold format for dates with entries
        bold_format = QTextCharFormat()
        bold_font = QFont()
        bold_font.setBold(True)
        bold_format.setFont(bold_font)
        
        # Check each day in the current month
        year_path = journal_path / str(year)
        month_path = year_path / f"{month:02d}"
        
        if month_path.exists():
            for day_dir in month_path.iterdir():
                if day_dir.is_dir():
                    try:
                        day_num = int(day_dir.name)
                        day_file = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"
                        
                        # Check if the file exists (saved page)
                        if day_file.exists():
                            date = QDate(year, month, day_num)
                            self.calendar.setDateTextFormat(date, bold_format)
                    except (ValueError, OSError):
                        continue
    
    def _on_month_changed(self, year: int, month: int) -> None:
        """Handle calendar month/year change - update bold dates."""
        self._update_calendar_dates()
    
    def _populate_tree_from_path(self, parent_item: QTreeWidgetItem, base_path: Path, current_path: Path) -> None:
        """Recursively populate tree from filesystem path."""
        try:
            # Get all subdirectories, sorted
            subdirs = sorted([d for d in current_path.iterdir() if d.is_dir()])
            
            for subdir in subdirs:
                # Create tree item with folder name
                item = QTreeWidgetItem([subdir.name])
                
                # Store the relative path from vault root
                relative_path = "/" + subdir.relative_to(Path(self.vault_root)).as_posix()
                item.setData(0, Qt.UserRole, relative_path)
                
                # Check if this is a virtual (unsaved) page
                file_path = f"{relative_path}/{subdir.name}{PAGE_SUFFIX}"
                page_file = subdir / f"{subdir.name}{PAGE_SUFFIX}"
                
                if file_path in self.virtual_pages or not page_file.exists():
                    # Set italic font for virtual/non-existent pages
                    font = item.font(0)
                    font.setItalic(True)
                    item.setFont(0, font)
                
                parent_item.addChild(item)
                
                # Recurse into subdirectories
                self._populate_tree_from_path(item, base_path, subdir)
                
        except (OSError, ValueError):
            pass
    
    def _on_date_clicked(self, date: QDate) -> None:
        """Handle calendar date click - emit signal to create/open journal entry."""
        year = date.year()
        month = date.month()
        day = date.day()
        
        # Refresh the tree to show/highlight the selected date's folder structure
        self.refresh()
        
        # Expand tree to show the selected date's path
        self._expand_to_date(year, month, day)
        
        self.dateActivated.emit(year, month, day)
    
    def _expand_to_date(self, year: int, month: int, day: int) -> None:
        """Expand the tree to show the path for a specific date."""
        if not self.vault_root:
            return
        
        # Build the path we're looking for: /Journal/YYYY/MM/DD
        month_str = f"{month:02d}"
        day_str = f"{day:02d}"
        target_path = f"/Journal/{year}/{month_str}/{day_str}"
        
        # Find and expand items in the tree
        self._find_and_expand_path(self.journal_tree.invisibleRootItem(), target_path)
    
    def _find_and_expand_path(self, parent_item: QTreeWidgetItem, target_path: str) -> Optional[QTreeWidgetItem]:
        """Recursively find and expand tree items to reach target path."""
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            if child is None:
                continue
            
            stored_path = child.data(0, Qt.UserRole)
            if not stored_path:
                continue
            
            # Check if this is the target path
            if stored_path == target_path:
                # Expand all parents up to this item
                current = child
                while current:
                    self.journal_tree.expandItem(current)
                    current = current.parent()
                # Select and scroll to this item
                self.journal_tree.setCurrentItem(child)
                self.journal_tree.scrollToItem(child)
                return child
            
            # Check if target path starts with this path (it's on the way)
            if target_path.startswith(stored_path + "/"):
                self.journal_tree.expandItem(child)
                result = self._find_and_expand_path(child, target_path)
                if result:
                    return result
        
        return None
    
    def _on_tree_item_activated(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle tree item double-click - open the journal page."""
        folder_path = item.data(0, Qt.UserRole)
        if not folder_path:
            return
        
        # Convert folder path to file path (folder/folder.txt)
        path_obj = Path(folder_path.lstrip("/"))
        file_path = f"/{path_obj}/{path_obj.name}{PAGE_SUFFIX}"
        
        self.journalPageActivated.emit(file_path)
