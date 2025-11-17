from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEvent, Qt, Signal, QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QCheckBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QLabel,
)

from zimx.app import config
from zimx.server.adapters.files import PAGE_SUFFIX
from .path_utils import path_to_colon


class TaskPanel(QWidget):
    taskActivated = Signal(str, int)
    dateActivated = Signal(int, int, int)  # year, month, day

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        
        # Calendar widget for journal date selection
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.clicked.connect(self._on_date_clicked)
        self.calendar.setSelectedDate(QDate.currentDate())
        
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks…")
        self.search.textChanged.connect(self._refresh_tasks)

        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.tag_list.setFocusPolicy(Qt.NoFocus)
        self.tag_list.itemClicked.connect(self._toggle_tag_selection)
        self.tag_list.viewport().installEventFilter(self)
        self.active_tags: set[str] = set()

        self.show_completed = QCheckBox("Show completed")
        self.show_completed.setChecked(False)
        self.show_completed.toggled.connect(self._refresh_tasks)

        self.task_tree = QTreeWidget()
        self.task_tree.setColumnCount(4)
        self.task_tree.setHeaderLabels(["!", "Task", "Due", "Path"])
        self.task_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_tree.setRootIsDecorated(False)
        self.task_tree.itemActivated.connect(self._emit_task_activation)
        self.task_tree.itemDoubleClicked.connect(self._emit_task_activation)
        self.task_tree.setSortingEnabled(True)
        self.sort_column = 0
        self.sort_order = Qt.AscendingOrder
        header = self.task_tree.header()
        header.sectionClicked.connect(self._handle_header_click)
        header.setSortIndicator(self.sort_column, self.sort_order)

        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("Tags"))
        sidebar.addWidget(self.tag_list)
        sidebar.addWidget(self.show_completed)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)

        splitter = QSplitter()
        splitter.addWidget(sidebar_widget)
        splitter.addWidget(self.task_tree)
        splitter.setSizes([180, 360])

        layout = QVBoxLayout()
        layout.addWidget(self.calendar)
        layout.addWidget(self.search)
        layout.addWidget(splitter, 1)
        self.setLayout(layout)
        
        self.vault_root = None

    def eventFilter(self, obj, event):
        if obj is self.tag_list.viewport() and event.type() == QEvent.MouseButtonPress:
            if self.tag_list.itemAt(event.pos()) is None:
                self.active_tags.clear()
                self._refresh_tasks()
                self._refresh_tags()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.active_tags.clear()
            self._refresh_tags()
            self._refresh_tasks()
            event.accept()
            return
        super().keyPressEvent(event)

    def _toggle_tag_selection(self, item: QListWidgetItem) -> None:
        tag = item.data(Qt.UserRole)
        if not tag:
            return
        if tag in self.active_tags:
            self.active_tags.remove(tag)
        else:
            self.active_tags.add(tag)
        self._refresh_tags()
        self._refresh_tasks()

    def refresh(self) -> None:
        if not config.has_active_vault():
            self.clear()
            return
        self._refresh_tags()
        self._refresh_tasks()

    def clear(self) -> None:
        self.active_tags.clear()
        self.tag_list.clear()
        self.task_tree.clear()

    def _refresh_tags(self) -> None:
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        tags = config.fetch_task_tags()
        for tag, count in tags:
            item = QListWidgetItem(f"{tag} ({count})")
            item.setData(Qt.UserRole, tag)
            active = tag in self.active_tags
            brush_bg = self.palette().highlight() if active else self.palette().base()
            brush_fg = self.palette().highlightedText() if active else self.palette().text()
            item.setBackground(brush_bg)
            item.setForeground(brush_fg)
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)

    def _refresh_tasks(self) -> None:
        query = self.search.text().strip()
        include_done = self.show_completed.isChecked()
        tasks = config.fetch_tasks(query, sorted(self.active_tags), include_done=include_done)
        self.task_tree.clear()
        for task in tasks:
            priority = "!" * task.get("priority", 0)
            due = task.get("due", "") or ""
            text = task["text"]
            display_path = self._present_path(task["path"])
            item = QTreeWidgetItem([priority, text, due, display_path])
            item.setData(0, Qt.UserRole, task)
            # Set tooltip on the task text column (column 1) to show full text
            item.setToolTip(1, text)
            self.task_tree.addTopLevelItem(item)
        self.task_tree.sortItems(self.sort_column, self.sort_order)

    def _handle_header_click(self, column: int) -> None:
        if column == self.sort_column:
            self.sort_order = Qt.DescendingOrder if self.sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            self.sort_column = column
            self.sort_order = Qt.AscendingOrder
        self.task_tree.header().setSortIndicator(self.sort_column, self.sort_order)
        self.task_tree.sortItems(self.sort_column, self.sort_order)

    def set_active_tags(self, tags: Iterable[str]) -> None:
        self.active_tags = set(tags)
        self._refresh_tags()
        self._refresh_tasks()

    def _emit_task_activation(self, item: QTreeWidgetItem) -> None:
        task = item.data(0, Qt.UserRole)
        if not task:
            return
        self.taskActivated.emit(task["path"], task.get("line") or 1)

    def _present_path(self, path: str) -> str:
        return path_to_colon(path)
    
    def _on_date_clicked(self, date: QDate) -> None:
        """Handle calendar date click - emit signal to create/open journal entry."""
        year = date.year()
        month = date.month()
        day = date.day()
        self.dateActivated.emit(year, month, day)
    
    def set_vault_root(self, vault_root: str) -> None:
        """Set vault root for calendar date formatting."""
        self.vault_root = vault_root
        self._update_calendar_dates()
    
    def _update_calendar_dates(self) -> None:
        """Scan journal folder and bold dates that have saved entries."""
        if not self.vault_root:
            return
        
        from pathlib import Path
        from PySide6.QtGui import QTextCharFormat, QFont
        
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
        bold_font.setWeight(QFont.Black)  # Maximum weight for prominence
        bold_format.setFont(bold_font)
        # Add a distinct color to make it more visible on Windows
        bold_format.setForeground(QColor(0, 100, 200))  # Dark blue color
        
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
