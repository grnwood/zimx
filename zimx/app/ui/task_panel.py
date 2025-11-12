from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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


class TaskPanel(QWidget):
    taskActivated = Signal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
        layout.addWidget(self.search)
        layout.addWidget(splitter, 1)
        self.setLayout(layout)

    def eventFilter(self, obj, event):
        if obj is self.tag_list.viewport() and event.type() == QEvent.MouseButtonPress:
            if self.tag_list.itemAt(event.pos()) is None:
                self.active_tags.clear()
                self._refresh_tags()
                self._refresh_tasks()
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
        cleaned = path.strip("/")
        if not cleaned:
            return "/"
        parts = cleaned.split("/")
        if parts:
            last = parts[-1]
            if last.endswith(PAGE_SUFFIX):
                parts[-1] = last[: -len(PAGE_SUFFIX)]
        return "/".join(parts)
