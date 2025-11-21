from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

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
        self.show_future = QCheckBox("Show future?")
        self.show_future.setChecked(False)
        self.show_future.toggled.connect(self._on_show_future_toggled)

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
        self.task_tree.setColumnWidth(0, 40)

        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("Tags"))
        sidebar.addWidget(self.tag_list)
        sidebar.addWidget(self.show_completed)
        sidebar.addWidget(self.show_future)
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
            self.search.clear()
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
            if self._is_future_task(task) and not self.show_future.isChecked():
                continue
            priority_level = min(task.get("priority", 0) or 0, 3)
            priority = "!" * priority_level
            due = task.get("due", "") or ""
            text = task["text"]
            display_path = self._present_path(task["path"])
            item = QTreeWidgetItem([priority, text, due, display_path])
            item.setData(0, Qt.UserRole, task)
            # Set tooltip on the task text column (column 1) to show full text
            item.setToolTip(1, text)
            due_fg_bg = self._due_colors(task)
            if due_fg_bg:
                fg, bg = due_fg_bg
                if bg:
                    item.setBackground(2, bg)
                if fg:
                    item.setForeground(2, fg)
            pri_brush = self._priority_brush(priority_level)
            if pri_brush:
                item.setBackground(0, pri_brush["bg"])
                item.setForeground(0, pri_brush["fg"])
            if task.get("status") == "done":
                font = item.font(1)
                font.setStrikeOut(True)
                for col in range(item.columnCount()):
                    item.setFont(col, font)
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

    def _due_colors(self, task: dict) -> Optional[tuple[QColor | None, QColor | None]]:
        """Return (fg, bg) for due column with red/orange/yellow emphasis."""
        due_str = (task.get("due") or "").strip()
        if not due_str:
            return None
        try:
            due_dt = date.fromisoformat(due_str)
        except ValueError:
            return None
        today_dt = date.today()
        if due_dt < today_dt:
            return QColor("#FFFFFF"), QColor("#CC0000")  # Overdue: white on solid red
        if due_dt == today_dt:
            return QColor("#3A1D00"), QColor("#F57900")  # Today: dark on orange
        if due_dt == today_dt + timedelta(days=1):
            return QColor("#444444"), QColor("#FDD835")  # Tomorrow: dark on yellow
        return None

    def _priority_brush(self, level: int) -> Optional[dict]:
        """Return background/foreground for priority level."""
        if level <= 0:
            return None
        # Three levels only, matching red/orange/yellow backgrounds
        colors = [
            {"bg": QColor("#FFF9C4"), "fg": QColor("#444444")},  # !
            {"bg": QColor("#F57900"), "fg": QColor("#3A1D00")},  # !!
            {"bg": QColor("#CC0000"), "fg": QColor("#FFFFFF")},  # !!!
        ]
        idx = min(level - 1, len(colors) - 1)
        return colors[idx]

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
        self._apply_show_future_preference()
        self._update_calendar_dates()

    def _on_show_future_toggled(self, checked: bool) -> None:
        if config.has_active_vault():
            config.save_show_future_tasks(checked)
        self._refresh_tasks()

    def _is_future_task(self, task: dict) -> bool:
        """Return True if task has a start date in the future."""
        start_str = (task.get("starts") or "").strip()
        if not start_str:
            return False
        try:
            start_dt = date.fromisoformat(start_str)
        except ValueError:
            return False
        return start_dt > date.today()

    def _apply_show_future_preference(self) -> None:
        """Sync the checkbox with saved preference and refresh the list."""
        if not config.has_active_vault():
            return
        saved = config.load_show_future_tasks()
        self.show_future.blockSignals(True)
        self.show_future.setChecked(saved)
        self.show_future.blockSignals(False)
        self._refresh_tasks()

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
