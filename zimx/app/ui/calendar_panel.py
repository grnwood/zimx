from __future__ import annotations

from pathlib import Path
import re
from datetime import date as Date
from typing import Optional

from PySide6.QtCore import Qt, Signal, QDate, QEvent
from PySide6.QtGui import QFont, QTextCharFormat, QKeyEvent, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QTableView,
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMenu,
    QLabel,
    QMessageBox,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
)
from shiboken6 import Shiboken

from zimx.server.adapters.files import PAGE_SUFFIX
from zimx.app import config
from .path_utils import path_to_colon


PATH_ROLE = Qt.UserRole + 1
LINE_ROLE = Qt.UserRole + 2


class CalendarPanel(QWidget):
    """Calendar tab with a journal-focused navigation tree."""

    dateActivated = Signal(int, int, int)  # year, month, day
    pageActivated = Signal(str)  # relative path to a page
    taskActivated = Signal(str, int)  # path, line number
    openInWindowRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setStyleSheet(
            """
            QCalendarWidget QWidget {
                alternate-background-color: palette(base);
            }
            QCalendarWidget QToolButton {
                padding: 4px 6px;
                font-weight: bold;
            }
            QCalendarWidget QTableView {
                selection-background-color: palette(highlight);
                selection-color: palette(highlighted-text);
                gridline-color: #888;
            }
            QCalendarWidget QTableView::item {
                border: 1px solid #888;
                padding: 4px;
            }
            """
        )
        self.calendar.clicked.connect(self._on_date_clicked)
        self.calendar.currentPageChanged.connect(self._on_month_changed)
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.setFocusPolicy(Qt.StrongFocus)
        self.calendar_view: QTableView | None = None
        self._drag_selecting = False
        self._last_drag_date: Optional[QDate] = None
        self._suppress_next_click = False
        self.multi_selected_dates: set[QDate] = {self.calendar.selectedDate()}
        self._attach_calendar_view()
        self.day_insights = QWidget()
        self.day_insights.setMinimumWidth(180)
        self.day_insights_layout = QVBoxLayout(self.day_insights)
        self.day_insights_layout.setContentsMargins(8, 8, 8, 8)
        self.day_insights_layout.setSpacing(6)
        self.insight_title = QLabel("No date selected")
        self.insight_title.setStyleSheet("font-weight: bold;")
        self.insight_counts = QLabel("")
        self.insight_tags = QLabel("")
        for lbl in (self.insight_title, self.insight_counts, self.insight_tags):
            lbl.setWordWrap(True)
        self.day_insights_layout.addWidget(self.insight_title)
        self.day_insights_layout.addWidget(self.insight_counts)
        self.day_insights_layout.addWidget(self.insight_tags)
        self.subpage_list = QListWidget()
        self.subpage_list.itemActivated.connect(self._open_insight_link)
        self.subpage_list.itemClicked.connect(self._open_insight_link)
        self.day_insights_layout.addWidget(QLabel("Pages"))
        self.day_insights_layout.addWidget(self.subpage_list, 1)
        self.tasks_due_list = QTreeWidget()
        self.tasks_due_list.setColumnCount(4)
        self.tasks_due_list.setHeaderLabels(["!", "Task", "Due", "Path"])
        self.tasks_due_list.setRootIsDecorated(False)
        self.tasks_due_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tasks_due_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tasks_due_list.setAlternatingRowColors(True)
        self.tasks_due_list.itemActivated.connect(self._open_task_item)
        self.tasks_due_list.itemDoubleClicked.connect(self._open_task_item)
        self.tasks_due_list.setSortingEnabled(True)
        self.tasks_due_list.sortByColumn(2, Qt.AscendingOrder)
        self.tasks_due_list.setColumnWidth(0, 24)
        self.tasks_due_list.setColumnWidth(2, 90)
        self.tasks_due_list.setColumnWidth(3, 140)
        self.day_insights_layout.addWidget(QLabel("Due Tasks"))
        self.day_insights_layout.addWidget(self.tasks_due_list, 1)
        self.day_insights_layout.addStretch(1)

        self.journal_tree = QTreeWidget()
        self.journal_tree.setHeaderHidden(True)
        self.journal_tree.setColumnCount(1)
        self.journal_tree.itemClicked.connect(self._on_tree_activated)
        self.journal_tree.itemActivated.connect(self._on_tree_activated)
        self.journal_tree.setFocusPolicy(Qt.StrongFocus)
        self.journal_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.journal_tree.customContextMenuRequested.connect(self._open_context_menu)

        # Vertical splitter for calendar + journal viewer
        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.addWidget(self.calendar)
        self.right_splitter.addWidget(self.journal_tree)
        self.right_splitter.setStretchFactor(0, 0)
        self.right_splitter.setStretchFactor(1, 1)

        # Horizontal splitter between insights and main area
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.addWidget(self.day_insights)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)

        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.main_splitter)
        self.setLayout(root_layout)

        self.vault_root: Optional[str] = None
        self.setFocusPolicy(Qt.StrongFocus)

    def showEvent(self, event):  # type: ignore[override]
        """Ensure we hook the calendar view after widget is shown."""
        super().showEvent(event)
        self._attach_calendar_view()
        self._apply_multi_selection_formats()

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Set vault root for calendar and tree data."""
        self.vault_root = vault_root
        self.refresh()

    def refresh(self) -> None:
        """Refresh the journal tree and calendar highlights."""
        self._populate_tree()
        self._update_calendar_dates()
        self._update_insights_from_calendar()

    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Move the calendar to a specific date and expand the tree."""
        target = QDate(year, month, day)
        self.calendar.setSelectedDate(target)
        self.multi_selected_dates = {target}
        self._update_calendar_dates(year, month)
        self._expand_to_date(target)
        self._update_day_listing(target)
        self._apply_multi_selection_formats()
        self._update_insights_for_selection()

    def set_current_page(self, rel_path: Optional[str]) -> None:
        """Sync calendar and tree based on an opened journal page."""
        if not rel_path or "Journal" not in rel_path:
            return
        parts = Path(rel_path.lstrip("/")).parts
        # Expect /Journal/YYYY/MM/DD[/Sub]/file.txt
        try:
            idx = parts.index("Journal")
        except ValueError:
            return
        if len(parts) < idx + 4:
            return
        year, month, day = parts[idx + 1 : idx + 4]
        try:
            y, m, d = int(year), int(month), int(day)
        except ValueError:
            return
        self.set_calendar_date(y, m, d)
        # If subpage, try selecting it
        if len(parts) > idx + 4:
            # Handle both folder-based and flat subpages
            sub_name = Path(parts[-1]).stem
            if len(parts) > idx + 5:
                sub_name = parts[idx + 4]
            self._select_subpage_item(y, m, d, sub_name, rel_path)
        # Update insights list selection
        self._update_insights_for_selection(rel_path)

    def _on_month_changed(self, year: int, month: int) -> None:
        self._update_calendar_dates(year, month)
        self._update_day_listing(self.calendar.selectedDate())
        self._apply_multi_selection_formats()
        self._update_insights_for_selection()

    def _on_date_clicked(self, date: QDate) -> None:
        """Emit selected date and sync the tree."""
        if self._suppress_next_click:
            self._suppress_next_click = False
            return
        self.multi_selected_dates = {date}
        self._apply_multi_selection_formats()
        self._expand_to_date(date)
        self._update_day_listing(date)
        self._update_insights_for_selection()
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
        self._update_day_listing(self.calendar.selectedDate())
        self._update_insights_from_calendar()

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
            self._apply_multi_selection_formats()
            return

        for day_dir in journal_path.iterdir():
            if not day_dir.is_dir() or not day_dir.name.isdigit():
                continue
            day_num = int(day_dir.name)
            day_file = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"
            if day_file.exists():
                self.calendar.setDateTextFormat(QDate(year, month, day_num), bold_format)
        self._apply_multi_selection_formats()

    def _apply_multi_selection_formats(self) -> None:
        """Highlight all currently multi-selected dates."""
        if not self.multi_selected_dates:
            return
        highlight_color = self.palette().highlight().color()
        highlight_color.setAlpha(210)
        text_color = self.palette().highlightedText().color()
        for date in self.multi_selected_dates:
            if date.isValid():
                highlight_format = QTextCharFormat()
                highlight_format.setBackground(highlight_color)
                highlight_format.setForeground(text_color)
                self.calendar.setDateTextFormat(date, highlight_format)
        if self.calendar_view and self.calendar_view.viewport():
            self.calendar_view.viewport().update()
        self.calendar.update()

    def _attach_calendar_view(self) -> None:
        """Find and attach to the internal calendar view for mouse tracking."""
        if self.calendar_view and Shiboken.isValid(self.calendar_view) and self.calendar_view.viewport():
            self.calendar_view.viewport().removeEventFilter(self)

        view = (
            self.calendar.findChild(QTableView, "qt_calendar_calendarview")
            or next(iter(self.calendar.findChildren(QTableView)), None)
        )
        self.calendar_view = view
        if self.calendar_view and Shiboken.isValid(self.calendar_view) and self.calendar_view.viewport():
            self.calendar_view.setSelectionMode(QAbstractItemView.NoSelection)
            self.calendar_view.viewport().installEventFilter(self)
            self.calendar_view.viewport().setMouseTracking(True)

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
        self._update_day_listing(date)
        self._update_insights_for_selection()

    def _find_child_by_text(self, parent: QTreeWidgetItem, text: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child and child.text(0) == text:
                return child
        return None

    def _select_subpage_item(self, year: int, month: int, day: int, sub_name: str, rel_path: Optional[str] = None) -> None:
        """Select a subpage row in the day listing if present."""
        for i in range(self.journal_tree.topLevelItemCount()):
            top = self.journal_tree.topLevelItem(i)
            if not top:
                continue
            if top.data(0, Qt.UserRole) and isinstance(top.data(0, Qt.UserRole), QDate):
                if top.data(0, Qt.UserRole) == QDate(year, month, day):
                    for j in range(top.childCount()):
                        child = top.child(j)
                        if not child:
                            continue
                        child_path = child.data(0, PATH_ROLE) or ""
                        label_match = child.text(0).endswith(sub_name)
                        path_match = rel_path and str(rel_path).endswith(child_path)
                        if label_match or path_match:
                            self.journal_tree.setCurrentItem(child)
                            self.journal_tree.scrollToItem(child)
                            return

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

    def eventFilter(self, obj, event):  # type: ignore[override]
        if (
            self.calendar_view
            and Shiboken.isValid(self.calendar_view)
            and self.calendar_view.viewport()
            and obj is self.calendar_view.viewport()
        ):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                date = self._date_from_pos(event.pos())
                if date.isValid():
                    if event.modifiers() & Qt.ControlModifier:
                        if date in self.multi_selected_dates:
                            self.multi_selected_dates.remove(date)
                        else:
                            self.multi_selected_dates.add(date)
                    else:
                        self.multi_selected_dates = {date}
                    self.calendar.setSelectedDate(date)
                    self._last_drag_date = date
                    self._drag_selecting = True
                    self._suppress_next_click = True
                    self._apply_multi_selection_formats()
                    self._update_day_listing(date)
                    self._update_insights_for_selection()
                    self.dateActivated.emit(date.year(), date.month(), date.day())
                    return True
            if event.type() == QEvent.MouseMove and self._drag_selecting and (event.buttons() & Qt.LeftButton):
                date = self._date_from_pos(event.pos())
                if date.isValid() and date != self._last_drag_date:
                    self.multi_selected_dates.add(date)
                    self._last_drag_date = date
                    self.calendar.setSelectedDate(date)
                    self._apply_multi_selection_formats()
                    self._update_insights_for_selection()
                    return True
            if event.type() == QEvent.MouseButtonRelease and self._drag_selecting and event.button() == Qt.LeftButton:
                self._drag_selecting = False
                self._last_drag_date = None
                return True
        return super().eventFilter(obj, event)

    def _date_from_pos(self, pos) -> QDate:
        if not self.calendar_view or not Shiboken.isValid(self.calendar_view):
            return QDate()
        idx = self.calendar_view.indexAt(pos)
        if not idx.isValid():
            return QDate()
        model = idx.model()
        if model:
            val = idx.data(Qt.UserRole)
            if isinstance(val, QDate) and val.isValid():
                return val
            day_val = idx.data(Qt.DisplayRole)
            if isinstance(day_val, int):
                return self._resolve_day_from_index(idx.row(), idx.column(), day_val)
        return QDate()

    def _resolve_day_from_index(self, row: int, col: int, day: int) -> QDate:
        """Best-effort mapping from table index to a real date."""
        year = self.calendar.yearShown()
        month = self.calendar.monthShown()
        # Heuristic: top rows with large day numbers belong to previous month,
        # bottom rows with small day numbers belong to next month.
        if row == 0 and day > 7:
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        elif row >= 4 and day <= 14:
            month += 1
            if month == 13:
                month = 1
                year += 1
        date = QDate(year, month, day)
        if date.isValid():
            return date
        return QDate()

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

    def _update_day_listing(self, date: QDate) -> None:
        """Render the selected day's page and its subpages as children of the day item."""
        if not self.vault_root:
            return
        day_item = self._find_item_by_path(f"Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}")
        if not day_item:
            return
        day_item.takeChildren()
        base_dir = Path(self.vault_root) / "Journal" / f"{date.year():04d}" / f"{date.month():02d}" / f"{date.day():02d}"
        if not base_dir.exists():
            return
        subpages = self._list_day_subpages(base_dir)
        # Main day page first
        main_path = f"/Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}/{date.day():02d}{PAGE_SUFFIX}"
        main = QTreeWidgetItem([f"{date.year():04d}-{date.month():02d}-{date.day():02d} (day)"])
        main.setData(0, Qt.UserRole, date)
        main.setData(0, PATH_ROLE, main_path)
        day_item.addChild(main)
        for label, rel_path in subpages:
            child = QTreeWidgetItem([label])
            child.setData(0, Qt.UserRole, date)
            child.setData(0, PATH_ROLE, rel_path)
            day_item.addChild(child)
        day_item.setExpanded(True)

    def _list_day_subpages(self, base_dir: Path) -> list[tuple[str, str]]:
        """Return (label, rel_path) for subpages under a journal day (recursive)."""

        entries: list[tuple[str, str]] = []

        def add_from_dir(directory: Path, prefix: str = "") -> None:
            try:
                children = sorted(directory.iterdir())
            except OSError:
                return
            for entry in children:
                if entry.is_dir():
                    add_from_dir(entry, f"{prefix}{entry.name}/")
                elif entry.is_file() and entry.suffix.lower() == PAGE_SUFFIX:
                    # Skip the root day's own file; everything else is a subpage
                    if directory == base_dir and entry.stem == base_dir.name:
                        continue
                    label = f"{prefix}{entry.stem}".rstrip("/")
                    rel = "/" + entry.relative_to(self.vault_root).as_posix()
                    entries.append((label, rel))

        add_from_dir(base_dir)
        return entries

    def _update_insights_from_calendar(self) -> None:
        self._update_insights_for_selection()

    def _update_insights_for_selection(self, current_path: Optional[str] = None) -> None:
        """Update insights based on the current multi-selection."""
        dates_for_tasks: list[QDate] = []
        if self.multi_selected_dates:
            dates = sorted(self.multi_selected_dates, key=lambda d: d.toJulianDay())
            if len(dates) == 1:
                self._update_insights(dates[0], current_path)
            else:
                self._update_insights_multi(dates, current_path)
            dates_for_tasks = dates
        else:
            date = self.calendar.selectedDate()
            self._update_insights(date, current_path)
            dates_for_tasks = [date]
        self._update_due_tasks(dates_for_tasks)

    def _update_insights_multi(self, dates: list[QDate], current_path: Optional[str] = None) -> None:
        if not self.vault_root:
            self.insight_title.setText("No date selected")
            self.insight_counts.setText("")
            self.insight_tags.setText("")
            self.subpage_list.clear()
            return
        tasks = 0
        tags: list[str] = []
        total_files: list[Path] = []
        day_entries = 0
        self.subpage_list.clear()
        for date in dates:
            base_dir = Path(self.vault_root) / "Journal" / f"{date.year():04d}" / f"{date.month():02d}" / f"{date.day():02d}"
            date_label = date.toString("yyyy-MM-dd")
            if not base_dir.exists():
                continue
            day_page = base_dir / f"{base_dir.name}{PAGE_SUFFIX}"
            if day_page.exists():
                total_files.append(day_page)
                day_entries += 1
                self._add_insight_item(f"{date_label} (day)", "/" + day_page.relative_to(self.vault_root).as_posix())
            subpages = self._list_day_subpages(base_dir)
            for label, rel in subpages:
                target = Path(self.vault_root) / rel.lstrip("/")
                if target.exists():
                    total_files.append(target)
                self._add_insight_item(f"{date_label} • {label}", rel)
        for file in total_files:
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue
            tasks += sum(1 for line in text.splitlines() if line.strip().startswith(("-", "(")) and "@" not in line[:2])
            tags.extend(re.findall(r"@(\w+)", text))
        unique_tags = sorted(set(tags))
        entries_count = len(total_files)
        subpages_count = max(0, entries_count - day_entries)
        self.insight_title.setText(f"Selected {len(dates)} days")
        self.insight_counts.setText(f"Entries: {entries_count}  •  Subpages: {subpages_count}  •  Tasks: {tasks}")
        self.insight_tags.setText("Tags: " + (", ".join(unique_tags[:8]) if unique_tags else "—"))
        if current_path:
            for idx in range(self.subpage_list.count()):
                it = self.subpage_list.item(idx)
                if it and current_path.endswith(str(it.data(PATH_ROLE))):
                    self.subpage_list.setCurrentItem(it)
                    break

    def _add_insight_item(self, label: str, rel_path: str) -> None:
        item = QListWidgetItem(label)
        item.setData(PATH_ROLE, rel_path)
        self.subpage_list.addItem(item)

    def _clear_due_tasks(self, message: Optional[str] = None) -> None:
        self.tasks_due_list.clear()
        if message:
            row = QTreeWidgetItem(["", message, "", ""])
            row.setFlags(Qt.NoItemFlags)
            self.tasks_due_list.addTopLevelItem(row)

    def _priority_brush(self, level: int) -> Optional[dict]:
        """Return background/foreground for priority level."""
        if level <= 0:
            return None
        colors = [
            {"bg": QColor("#FFF9C4"), "fg": QColor("#444444")},
            {"bg": QColor("#F57900"), "fg": QColor("#3A1D00")},
            {"bg": QColor("#CC0000"), "fg": QColor("#FFFFFF")},
        ]
        idx = min(level - 1, len(colors) - 1)
        return colors[idx]

    def _due_colors(self, due_str: str) -> Optional[tuple]:
        """Return (fg, bg) for due column with red/orange/yellow emphasis."""
        due_str = (due_str or "").strip()
        if not due_str:
            return None
        try:
            due_dt = Date.fromisoformat(due_str)
        except ValueError:
            return None
        today_dt = Date.today()
        if due_dt < today_dt:
            return QColor("#FFFFFF"), QColor("#CC0000")
        if due_dt == today_dt:
            return QColor("#3A1D00"), QColor("#F57900")
        return None

    @staticmethod
    def _parse_date(value: str) -> Optional[Date]:
        try:
            return Date.fromisoformat(value.strip())
        except Exception:
            return None

    def _update_due_tasks(self, dates: list[QDate]) -> None:
        """List tasks due on any of the selected dates."""
        if not dates or not config.has_active_vault():
            self._clear_due_tasks("No due tasks for selection")
            return
        valid_dates = [d for d in dates if d and d.isValid()]
        if not valid_dates:
            self._clear_due_tasks("No due tasks for selection")
            return
        start_dt = min(valid_dates, key=lambda d: d.toJulianDay())
        end_dt = max(valid_dates, key=lambda d: d.toJulianDay())
        range_start = Date(start_dt.year(), start_dt.month(), start_dt.day())
        range_end = Date(end_dt.year(), end_dt.month(), end_dt.day())
        try:
            tasks = config.fetch_tasks(include_done=False, include_ancestors=False)
        except Exception:
            tasks = []
        matches: list[dict] = []
        for task in tasks:
            path = task.get("path") or ""
            if not path:
                continue
            due_str = (task.get("due") or "").strip()
            start_str = (task.get("starts") or "").strip()
            due_dt = self._parse_date(due_str)
            start_dt_val = self._parse_date(start_str)
            is_overdue = bool(due_dt and due_dt < range_start)
            is_due_in_range = bool(due_dt and range_start <= due_dt <= range_end)
            starts_in_range = bool(start_dt_val and range_start <= start_dt_val <= range_end)
            if is_overdue or is_due_in_range or starts_in_range:
                matches.append(task)
        self.tasks_due_list.clear()
        if not matches:
            self._clear_due_tasks("No due tasks for selection")
            return
        for task in sorted(matches, key=lambda t: (t.get("due") or "", t.get("path") or "", t.get("line") or 0)):
            path = str(task.get("path") or "")
            if not path.startswith("/"):
                path = "/" + path.lstrip("/")
            line = task.get("line") or 1
            priority_txt = "!" * max(0, int(task.get("priority") or 0))
            row = QTreeWidgetItem([priority_txt, task.get("text") or "(task)", task.get("due") or "", path_to_colon(path)])
            row.setData(0, Qt.UserRole, task)
            row.setData(0, PATH_ROLE, path)
            row.setData(0, LINE_ROLE, line)
            tooltip_parts = []
            if due_str := (task.get("due") or "").strip():
                tooltip_parts.append(f"Due: {due_str}")
            if start_str:
                tooltip_parts.append(f"Start: {start_str}")
            if tooltip_parts:
                row.setToolTip(1, " • ".join(tooltip_parts))
            pri_brush = self._priority_brush(int(task.get("priority") or 0))
            if pri_brush:
                if pri_brush.get("bg"):
                    row.setBackground(0, pri_brush["bg"])
                if pri_brush.get("fg"):
                    row.setForeground(0, pri_brush["fg"])
            due_colors = self._due_colors(task.get("due") or "")
            if due_colors:
                fg, bg = due_colors
                row.setForeground(2, fg)
                row.setBackground(2, bg)
            self.tasks_due_list.addTopLevelItem(row)

    def _update_insights(self, date: QDate, current_path: Optional[str] = None) -> None:
        if not self.vault_root or not date.isValid():
            self.insight_title.setText("No date selected")
            self.insight_counts.setText("")
            self.insight_tags.setText("")
            self.subpage_list.clear()
            self.tasks_due_list.clear()
            return
        base_dir = Path(self.vault_root) / "Journal" / f"{date.year():04d}" / f"{date.month():02d}" / f"{date.day():02d}"
        if not base_dir.exists():
            self.insight_title.setText(date.toString("yyyy-MM-dd"))
            self.insight_counts.setText("No journal entry.")
            self.insight_tags.setText("")
            self.subpage_list.clear()
            self.tasks_due_list.clear()
            return
        day_page = base_dir / f"{base_dir.name}{PAGE_SUFFIX}"
        subpages = self._list_day_subpages(base_dir)
        files = [day_page] if day_page.exists() else []
        for _, rel_path in subpages:
            target = Path(self.vault_root) / rel_path.lstrip("/")
            if target.exists():
                files.append(target)
        tasks = 0
        tags = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue
            tasks += sum(1 for line in text.splitlines() if line.strip().startswith(("-", "(")) and "@" not in line[:2])
            tags.extend(re.findall(r"@(\w+)", text))
        unique_tags = sorted(set(tags))
        subpages_count = max(0, len(files) - 1)
        self.insight_title.setText(f"{date.toString('yyyy-MM-dd')}")
        self.insight_counts.setText(f"Entries: {len(files)}  •  Subpages: {subpages_count}  •  Tasks: {tasks}")
        self.insight_tags.setText("Tags: " + (", ".join(unique_tags[:8]) if unique_tags else "—"))
        # Populate pages list
        self.subpage_list.clear()
        # Main
        main_path = f"/Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}/{date.day():02d}{PAGE_SUFFIX}"
        main_item = QListWidgetItem(f"{date.year():04d}-{date.month():02d}-{date.day():02d} (day)")
        main_item.setData(PATH_ROLE, main_path)
        self.subpage_list.addItem(main_item)
        # Subpages (including nested folders)
        for label, rel in subpages:
            item = QListWidgetItem(label)
            item.setData(PATH_ROLE, rel)
            self.subpage_list.addItem(item)
        # Highlight current page if provided
        if current_path:
            for idx in range(self.subpage_list.count()):
                it = self.subpage_list.item(idx)
                if it and current_path.endswith(str(it.data(PATH_ROLE))):
                    self.subpage_list.setCurrentItem(it)
                    break

    def _open_insight_link(self, item: QListWidgetItem) -> None:
        if not item:
            return
        path = item.data(PATH_ROLE)
        if path:
            self.pageActivated.emit(str(path))

    def _open_task_item(self, item) -> None:
        """Open a due task's page at its line."""
        if not item:
            return
        path = item.data(0, PATH_ROLE) if hasattr(item, "data") else None
        line = item.data(0, LINE_ROLE) if hasattr(item, "data") else None
        if not path:
            return
        try:
            line_num = int(line or 1)
        except (TypeError, ValueError):
            line_num = 1
        norm = str(path)
        if not norm.startswith("/"):
            norm = "/" + norm.lstrip("/")
        self.taskActivated.emit(norm, max(1, line_num))

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

    def _resolve_page_relpath(self, rel_path: str) -> Optional[str]:
        """Return a file relpath for deletion if it exists."""
        if not self.vault_root or not rel_path:
            return None
        path_obj = Path(self.vault_root) / rel_path.lstrip("/")
        if path_obj.is_file():
            return rel_path
        if path_obj.is_dir():
            candidate = path_obj / f"{path_obj.name}{PAGE_SUFFIX}"
            if candidate.exists() and candidate.is_file():
                return "/" + candidate.relative_to(self.vault_root).as_posix()
        return None

    def _delete_page(self, rel_path: str) -> None:
        """Delete a journal page after confirmation."""
        if not self.vault_root or not rel_path:
            return
        abs_path = Path(self.vault_root) / rel_path.lstrip("/")
        if not abs_path.exists() or not abs_path.is_file():
            return
        confirm = QMessageBox.question(
            self,
            "Delete Page",
            f"Delete page:\n{path_to_colon(rel_path)}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            abs_path.unlink()
        except Exception:
            QMessageBox.warning(self, "Delete Page", "Failed to delete the page.")
            return
        # Clean up empty parent folders up to Journal
        try:
            parent = abs_path.parent
            journal_root = Path(self.vault_root) / "Journal"
            while parent != journal_root and parent.is_dir():
                if any(parent.iterdir()):
                    break
                parent.rmdir()
                parent = parent.parent
        except Exception:
            pass
        self.refresh()

    def _open_context_menu(self, pos) -> None:
        item = self.journal_tree.itemAt(pos)
        menu = QMenu(self)
        if item:
            path_value = item.data(0, PATH_ROLE)
            if path_value:
                rel_path = str(path_value)
                if not rel_path.startswith("/"):
                    rel_path = "/" + rel_path
                file_rel = self._resolve_page_relpath(rel_path)
                open_win = menu.addAction("Open in Editor Window")
                open_win.triggered.connect(lambda: self.openInWindowRequested.emit(rel_path))
                if file_rel:
                    delete_action = menu.addAction("Delete Page")
                    delete_action.triggered.connect(lambda: self._delete_page(file_rel))
                menu.addSeparator()
        refresh = menu.addAction("Refresh")
        refresh.triggered.connect(self.refresh)
        global_pos = self.journal_tree.viewport().mapToGlobal(pos)
        menu.exec(global_pos)

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

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
