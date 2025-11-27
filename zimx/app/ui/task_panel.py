from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Iterable, Optional

from PySide6.QtCore import QEvent, Qt, Signal, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QStyle,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
    QLabel,
)

from zimx.app import config
from .path_utils import path_to_colon


def _active_tag_token(text: str, cursor: int) -> Optional[str]:
    """Return the @tag token currently under the cursor, if any."""
    prefix = text[: max(cursor, 0)]
    match = re.search(r"@[\w_]*$", prefix)
    return match.group(0) if match else None


def _should_suspend_nav_for_tag(text: str, cursor: int, available_tags: set[str]) -> bool:
    """Return True if nav keys should be suspended because a tag is being typed that isn't yet valid."""
    token = _active_tag_token(text, cursor)
    if not token:
        return False
    tag = token.lstrip("@")
    return tag not in available_tags


class TaskPanel(QWidget):
    taskActivated = Signal(str, int)
    focusGained = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks…")
        self.search.textChanged.connect(self._refresh_tasks)
        self.search.installEventFilter(self)

        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.tag_list.setFocusPolicy(Qt.NoFocus)
        self.tag_list.itemClicked.connect(self._toggle_tag_selection)
        self.tag_list.viewport().installEventFilter(self)
        self.active_tags: set[str] = set()
        self._available_tags: set[str] = set()

        style = self.style()
        icon_size = QSize(18, 18)

        self.show_completed = QCheckBox()
        self.show_completed.setChecked(False)
        self.show_completed.toggled.connect(self._refresh_tasks)
        self.show_completed.setToolTip("Toggle to include tasks marked as done.")
        self.show_completed.setIcon(style.standardIcon(QStyle.SP_DialogApplyButton))
        self.show_completed.setIconSize(icon_size)
        self.show_completed.setAccessibleName("Completed?")

        self.show_future = QCheckBox()
        self.show_future.setChecked(False)
        self.show_future.toggled.connect(self._on_show_future_toggled)
        self.show_future.setToolTip(
            "Toggle to include tasks that start in the future.\n"
            "Ex: ( ) task not started yet >YYYY-mm-dd"
        )
        self.show_future.setIcon(style.standardIcon(QStyle.SP_MediaSeekForward))
        self.show_future.setIconSize(icon_size)
        self.show_future.setAccessibleName("Future?")

        self.show_actionable = QCheckBox()
        self.show_actionable.setChecked(False)
        self.show_actionable.toggled.connect(self._refresh_tasks)
        self.show_actionable.setToolTip(
            "Show tasks you can act on now:\n"
            "- Not done\n"
            "- No open subtasks\n"
            "Parents become actionable\n"
            "once children are done;\n"
            "due/priority inherit unless\n"
            "overridden."
        )
        self.show_actionable.setIcon(style.standardIcon(QStyle.SP_MediaPlay))
        self.show_actionable.setIconSize(icon_size)
        self.show_actionable.setAccessibleName("Actionable?")

        self.task_tree = QTreeWidget()
        self.task_tree.setColumnCount(4)
        self.task_tree.setHeaderLabels(["!", "Task", "Due", "Path"])
        self.task_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_tree.setRootIsDecorated(True)
        self.task_tree.itemActivated.connect(self._emit_task_activation)
        self.task_tree.itemDoubleClicked.connect(self._emit_task_activation)
        self.task_tree.setSortingEnabled(True)
        self.sort_column = 0
        self.sort_order = Qt.AscendingOrder
        header = self.task_tree.header()
        header.sectionClicked.connect(self._handle_header_click)
        header.setSortIndicator(self.sort_column, self.sort_order)
        self.task_tree.setColumnWidth(0, 40)
        self.task_tree.setFocusPolicy(Qt.StrongFocus)
        self.task_tree.installEventFilter(self)
        self.task_tree.setFocusPolicy(Qt.StrongFocus)

        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("Tags"))
        sidebar.addWidget(self.tag_list)
        toggles_row = QHBoxLayout()
        toggles_row.addWidget(self.show_completed)
        toggles_row.addWidget(self.show_future)
        toggles_row.addWidget(self.show_actionable)
        toggles_row.addStretch(1)
        toggles_widget = QWidget()
        toggles_widget.setLayout(toggles_row)
        sidebar.addWidget(toggles_widget)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)

        splitter = QSplitter()
        splitter.addWidget(sidebar_widget)
        splitter.addWidget(self.task_tree)
        splitter.setSizes([180, 360])

        layout = QVBoxLayout()
        layout.addWidget(self.search)
        layout.addWidget(splitter, 1)
        self.setLayout(layout)
        
        self.vault_root = None
        self._setup_focus_defaults()

    def _setup_focus_defaults(self) -> None:
        """Ensure sensible default focus inside the Tasks tab."""
        self.search.setFocusPolicy(Qt.StrongFocus)
        self.setFocusPolicy(Qt.StrongFocus)
        self.search.setFocus()
        self.task_tree.setFocusPolicy(Qt.StrongFocus)
        self.search.installEventFilter(self)
        self.task_tree.installEventFilter(self)

    def focusInEvent(self, event):  # type: ignore[override]
        super().focusInEvent(event)
        self.focus_search()
        try:
            self.focusGained.emit()
        except Exception:
            pass

    def focus_search(self) -> None:
        """Public helper to focus the task search field."""
        try:
            self.search.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.tag_list.viewport() and event.type() == QEvent.MouseButtonPress:
            if self.tag_list.itemAt(event.pos()) is None:
                self.active_tags.clear()
                self._refresh_tasks()
                self._refresh_tags()
                return True
        if obj in (self.search, self.task_tree) and event.type() == QEvent.KeyPress:
            if obj is self.search and _should_suspend_nav_for_tag(
                self.search.text(), self.search.cursorPosition(), self._available_tags
            ):
                return super().eventFilter(obj, event)
            if self._handle_task_nav_key(event):
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if self._handle_task_nav_key(event):
            return
        if event.key() == Qt.Key_Escape:
            self.active_tags.clear()
            self.search.clear()
            self._refresh_tags()
            self._refresh_tasks()
            event.accept()
            return
        super().keyPressEvent(event)

    def _handle_task_nav_key(self, event) -> bool:
        """Handle up/down navigation (including vi j/k) within the task list."""
        key = event.key()
        if key in (Qt.Key_J, Qt.Key_Down):
            self._cycle_task_selection(1)
            event.accept()
            return True
        if key in (Qt.Key_K, Qt.Key_Up):
            self._cycle_task_selection(-1)
            event.accept()
            return True
        return False

    def _cycle_task_selection(self, direction: int) -> None:
        """Move selection up/down with wrap-around in the task list."""
        items = self._visible_items()
        if not items:
            return
        current_item = self.task_tree.currentItem()
        if current_item not in items:
            target_index = 0 if direction > 0 else len(items) - 1
        else:
            current_index = items.index(current_item)
            target_index = (current_index + direction) % len(items)
        target_item = items[target_index]
        if target_item:
            self.task_tree.setCurrentItem(target_item)
            self.task_tree.scrollToItem(target_item)
            self.task_tree.setFocus(Qt.OtherFocusReason)

    def _visible_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        iterator = QTreeWidgetItemIterator(self.task_tree, QTreeWidgetItemIterator.All)
        while iterator.value():
            item = iterator.value()
            if not item.isHidden():
                items.append(item)
            iterator += 1
        return items

    def _parse_search_tags(self, text: str) -> tuple[str, Optional[list[str]], list[str]]:
        """Extract @tags from search text; return (query_without_tags, found_tags_or_None, missing_tags)."""
        tags = re.findall(r"@([A-Za-z0-9_]+)", text)
        if not tags:
            return text, None, []
        found = [t for t in tags if t in self._available_tags]
        missing = [t for t in tags if t not in self._available_tags]
        # Remove tags from query
        query = re.sub(r"@([A-Za-z0-9_]+)", "", text).strip()
        return query, found, missing

    def _apply_search_tag_feedback(self, found: Optional[list[str]], missing: list[str]) -> None:
        """Color the search field based on tag validity."""
        if found is None:
            # No tags in search: reset color
            self.search.setStyleSheet("")
            return
        if missing:
            self.search.setStyleSheet("color: #c62828;")  # red for missing tags
        else:
            # At least one tag present and all valid
            self.search.setStyleSheet("color: #00b33c;")  # brighter green for valid tags

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
        self._available_tags = {tag for tag, _ in tags}
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
        query, found_tags, missing_tags = self._parse_search_tags(query)
        self._apply_search_tag_feedback(found_tags, missing_tags)
        # If tags were supplied in the search, override active_tags and keep tag list in sync
        if found_tags is not None:
            if set(found_tags) != self.active_tags:
                self.active_tags = set(found_tags)
                self._refresh_tags()
        include_done = self.show_completed.isChecked()
        effective_tags = self.active_tags
        searching = bool(query) or bool(effective_tags)
        actionable_toggle = self.show_actionable.isChecked()
        tasks = config.fetch_tasks(
            query,
            sorted(effective_tags),
            include_done=include_done,
            include_ancestors=True,
            # Force actionable-only when toggled, otherwise keep the default active view
            # unless the user is explicitly filtering/searching.
            actionable_only=actionable_toggle or (not include_done and not searching),
        )
        self.task_tree.clear()
        if not tasks:
            return
        task_map = {task["id"]: task for task in tasks}
        visible_ids: set[str] = set()

        def _mark_visible(task_id: str) -> None:
            if task_id in visible_ids:
                return
            visible_ids.add(task_id)
            parent_id = task_map.get(task_id, {}).get("parent")
            if parent_id and parent_id in task_map:
                _mark_visible(parent_id)

        for task in tasks:
            if self.show_future.isChecked() or not self._is_future_task(task):
                _mark_visible(task["id"])

        items_by_id: dict[str, QTreeWidgetItem] = {}
        for task in sorted(tasks, key=self._task_sort_key):
            if task["id"] not in visible_ids:
                continue
            priority_level = min(task.get("priority", 0) or 0, 3)
            priority = "!" * priority_level
            due = task.get("due", "") or ""
            text = task["text"]
            display_path = self._present_path(task["path"])
            item = QTreeWidgetItem([priority, text, due, display_path])
            item.setData(0, Qt.UserRole, task)
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
            elif not task.get("actionable", True):
                muted = QColor("#666666")
                for col in range(item.columnCount()):
                    item.setForeground(col, muted)
            parent_id = task.get("parent")
            if parent_id and parent_id in items_by_id:
                items_by_id[parent_id].addChild(item)
            else:
                self.task_tree.addTopLevelItem(item)
            items_by_id[task["id"]] = item
        self.task_tree.expandAll()
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

    def _task_sort_key(self, task: dict) -> tuple:
        """Sort tasks to ensure parents are created before children."""
        return (task.get("path") or "", task.get("line") or 0, task.get("level") or 0)

    def _emit_task_activation(self, item: QTreeWidgetItem) -> None:
        task = item.data(0, Qt.UserRole)
        if not task:
            return
        self.taskActivated.emit(task["path"], task.get("line") or 1)

    def _present_path(self, path: str) -> str:
        return path_to_colon(path)
    
    def set_vault_root(self, vault_root: str) -> None:
        """Set vault root for task filtering preferences."""
        self.vault_root = vault_root
        self._apply_show_future_preference()

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
