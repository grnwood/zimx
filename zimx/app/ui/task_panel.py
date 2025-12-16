from __future__ import annotations

from datetime import date, timedelta
import html
import os
import re
from typing import Iterable, Optional

from PySide6.QtCore import QEvent, Qt, Signal, QSize, QTimer, QByteArray
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
    QToolButton,
)

from zimx.app import config
from zimx.server.adapters.files import PAGE_SUFFIX
from .path_utils import path_to_colon

TAG_PATTERN = re.compile(r"(?<![\w.+-])@([A-Za-z0-9_]+)")
TAG_PREFIX_PATTERN = re.compile(r"(?<![\w.+-])@[\w_]*$")


def _active_tag_token(text: str, cursor: int) -> Optional[str]:
    """Return the @tag token currently under the cursor, if any."""
    prefix = text[: max(cursor, 0)]
    match = TAG_PREFIX_PATTERN.search(prefix)
    return match.group(0) if match else None


def _should_suspend_nav_for_tag(text: str, cursor: int, available_tags: set[str]) -> bool:
    """Return True if nav keys should be suspended because a tag is being typed that isn't yet valid."""
    token = _active_tag_token(text, cursor)
    if not token:
        return False
    tag = token.lstrip("@")
    return tag not in available_tags


class DebugTaskTree(QTreeWidget):
    """QTreeWidget that logs mouse events for debugging."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from PySide6.QtCore import QTimer
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._emit_deferred_double_click)
        self._pending_task_data = None
    
    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        debug = os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", "")
        if debug:
            print(f"[DEBUG_TREE] mouseDoubleClickEvent: button={event.button()}, pos={event.pos()}")
        item = self.itemAt(event.pos())
        column = self.columnAt(event.pos().x())
        if debug:
            print(f"[DEBUG_TREE] Column at click: {column}")
        
        if item and event.button() == Qt.LeftButton:
            if debug:
                print(f"[DEBUG_TREE] Item at pos: {item.text(1)[:50]}")
            # Extract task data immediately before item might become invalid
            task_data = item.data(0, Qt.UserRole)
            if task_data:
                if debug:
                    print(f"[DEBUG_TREE] Task data: {task_data.get('path')}:{task_data.get('line')}")
                self._pending_task_data = task_data
                self._timer.start(0)
                event.accept()
                return
        
        # Only call super if we didn't handle it
        if debug:
            print(f"[DEBUG_TREE] Calling super().mouseDoubleClickEvent()")
        super().mouseDoubleClickEvent(event)
        if debug:
            print(f"[DEBUG_TREE] After super().mouseDoubleClickEvent(), event.isAccepted()={event.isAccepted()}")
    
    def _emit_deferred_double_click(self):
        if self._pending_task_data:
            if os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", ""):
                print(f"[DEBUG_TREE] Emitting task activation for {self._pending_task_data.get('path')}")
            # Find the parent TaskPanel and emit through it
            parent = self.parent()
            while parent and not hasattr(parent, 'taskActivated'):
                parent = parent.parent()
            if parent and hasattr(parent, 'taskActivated'):
                parent.taskActivated.emit(self._pending_task_data['path'], self._pending_task_data.get('line') or 1)
            self._pending_task_data = None
    
    def mousePressEvent(self, event):  # type: ignore[override]
        if os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", ""):
            print(f"[DEBUG_TREE] mousePressEvent: button={event.button()}, pos={event.pos()}")
        super().mousePressEvent(event)
        if os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", ""):
            print(f"[DEBUG_TREE] After super().mousePressEvent(), event.isAccepted()={event.isAccepted()}")


class TaskPanel(QWidget):
    taskActivated = Signal(str, int)
    focusGained = Signal()
    filterClearRequested = Signal()

    def __init__(
        self,
        parent=None,
        *,
        font_size_key: str = "task_font_size_tabbed",
        splitter_key: str = "task_splitter_tabbed",
        header_state_key: str = "task_header_tabbed",
    ) -> None:
        super().__init__(parent)
        self._font_size_key = font_size_key
        self._font_size = config.load_panel_font_size(self._font_size_key, max(8, self.font().pointSize() or 12))
        self._splitter_key = splitter_key
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setInterval(200)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self._header_state_key = header_state_key
        self._header_save_timer = QTimer(self)
        self._header_save_timer.setInterval(200)
        self._header_save_timer.setSingleShot(True)
        self._header_save_timer.timeout.connect(self._save_header_state)

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
        icon_size = QSize(20, 20)

        def _build_toggle(icon, tooltip, slot):
            toggle = QToolButton(self)
            toggle.setCheckable(True)
            toggle.setIcon(icon)
            toggle.setIconSize(icon_size)
            toggle.setToolTip(tooltip)
            toggle.setAutoRaise(True)
            toggle.setFixedSize(26, 26)
            toggle.toggled.connect(slot)
            # Subtle styling to show pressed/depressed states
            toggle.setStyleSheet(
                """
                QToolButton {
                    border: 1px solid transparent;
                    border-radius: 4px;
                    padding: 2px;
                    background: transparent;
                }
                QToolButton:hover {
                    border: 1px solid #666;
                    background: rgba(255,255,255,0.06);
                }
                QToolButton:checked {
                    border: 1px solid #4a90e2;
                    background: rgba(74,144,226,0.22);
                }
                """
            )
            return toggle

        self.show_completed = _build_toggle(
            style.standardIcon(QStyle.SP_DialogApplyButton),
            "Include tasks marked as done.",
            self._refresh_tasks,
        )

        self.show_future = _build_toggle(
            style.standardIcon(QStyle.SP_MediaSeekForward),
            "Include tasks that start in the future (e.g., ( ) task >YYYY-mm-dd).",
            self._on_show_future_toggled,
        )

        self.show_actionable = _build_toggle(
            style.standardIcon(QStyle.SP_MediaPlay),
            "Show tasks you can act on now (not done, no open subtasks, parents inherit).",
            self._refresh_tasks,
        )

        self.task_tree = DebugTaskTree()
        self.task_tree.setColumnCount(4)
        self.task_tree.setHeaderLabels(["!", "Task", "Due", "Path"])
        self.task_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_tree.setRootIsDecorated(True)
        self.task_tree.itemActivated.connect(self._emit_task_activation)
        self.task_tree.itemDoubleClicked.connect(lambda item, col: self._emit_task_activation(item))
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
        
        # Debug: Log when tree signals fire (if enabled)
        if os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", ""):
            self.task_tree.itemActivated.connect(lambda item: print(f"[TASK_TREE] itemActivated signal fired"))
            self.task_tree.itemDoubleClicked.connect(lambda item, col: print(f"[TASK_TREE] itemDoubleClicked signal fired, col={col}"))
        
        saved_header = config.load_header_state(self._header_state_key)
        if saved_header:
            try:
                self.task_tree.header().restoreState(QByteArray.fromBase64(saved_header.encode("ascii")))
            except Exception:
                pass
        self.task_tree.header().sectionMoved.connect(lambda *_: self._header_save_timer.start())
        self.task_tree.header().sectionResized.connect(lambda *_: self._header_save_timer.start())

        sidebar = QVBoxLayout()
        # Tags row with filter indicators
        tags_row = QHBoxLayout()
        tags_row.addWidget(QLabel("Tags"))
        self.filter_label = QLabel("Filtered")
        self.filter_label.setVisible(False)
        self.filter_label.setCursor(Qt.PointingHandCursor)
        self.filter_label.setToolTip("Click to clear navigation filter")
        self.filter_label.mousePressEvent = lambda event: self._on_filter_label_clicked(event)
        tags_row.addSpacing(6)
        tags_row.addWidget(self.filter_label)
        self.filter_checkbox = QCheckBox()
        self.filter_checkbox.setChecked(True)
        self.filter_checkbox.setVisible(False)
        self.filter_checkbox.setToolTip("Limit tasks to the filtered navigation subtree.")
        self.filter_checkbox.toggled.connect(self._on_filter_checkbox_toggled)
        tags_row.addWidget(self.filter_checkbox)
        self.journal_checkbox = QCheckBox("Journal?")
        self.journal_checkbox.setChecked(True)
        self.journal_checkbox.setVisible(False)
        self.journal_checkbox.setToolTip("Include tasks from the Journal subtree while filtered.")
        self.journal_checkbox.toggled.connect(self._on_journal_checkbox_toggled)
        tags_row.addWidget(self.journal_checkbox)
        tags_row.addStretch(1)
        sidebar.addLayout(tags_row)
        sidebar.addWidget(self.tag_list)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)

        splitter = QSplitter()
        splitter.addWidget(sidebar_widget)
        splitter.addWidget(self.task_tree)
        splitter.setSizes([180, 360])
        self.splitter = splitter
        sizes = config.load_splitter_sizes(self._splitter_key)
        if sizes:
            try:
                self.splitter.setSizes(sizes)
            except Exception:
                pass
        self.splitter.splitterMoved.connect(lambda *_: self._splitter_save_timer.start())

        # Header row with horizontal toggles then search
        header_row = QHBoxLayout()
        for cb in (self.show_completed, self.show_future, self.show_actionable):
            header_row.addWidget(cb)
        header_row.addSpacing(6)
        header_row.addWidget(self.search, 1)
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Decrease font size")
        self.zoom_out_btn.setAutoRaise(True)
        self.zoom_out_btn.clicked.connect(lambda: self._adjust_font_size(-1))
        header_row.addWidget(self.zoom_out_btn)
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Increase font size")
        self.zoom_in_btn.setAutoRaise(True)
        self.zoom_in_btn.clicked.connect(lambda: self._adjust_font_size(1))
        header_row.addWidget(self.zoom_in_btn)

        layout = QVBoxLayout()
        layout.addLayout(header_row)
        layout.addWidget(splitter, 1)
        self.setLayout(layout)
        
        self.vault_root = None
        self._nav_filter_prefix: Optional[str] = None
        self._nav_filter_enabled = True
        self._include_journal = True
        self._visible_tasks: list[dict] = []
        self._tag_source_tasks: list[dict] = []
        self._setup_focus_defaults()
        self._update_filter_indicator()
        self._apply_font_size()

    def _format_task_text(self, text: str) -> str:
        """Return plain text with link labels (or URLs) inlined, no markup."""
        if not text:
            return ""

        def _replace_md(match: re.Match[str]) -> str:
            label = (match.group("label") or "").strip()
            url = (match.group("url") or "").strip()
            return label or url

        def _replace_wiki(match: re.Match[str]) -> str:
            link = (match.group("link") or "").strip()
            label = (match.group("label") or "").strip()
            return label or link

        rendered = re.sub(
            r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^\s)]+)\)",
            _replace_md,
            text,
        )
        rendered = re.sub(
            r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]+)\]",
            _replace_wiki,
            rendered,
        )
        return rendered

    def _setup_focus_defaults(self) -> None:
        """Ensure sensible default focus inside the Tasks tab."""
        self.search.setFocusPolicy(Qt.StrongFocus)
        self.setFocusPolicy(Qt.StrongFocus)
        self.search.setFocus()
        self.task_tree.setFocusPolicy(Qt.StrongFocus)
        self.search.installEventFilter(self)
        self.task_tree.installEventFilter(self)

    def _on_filter_checkbox_toggled(self, checked: bool) -> None:
        if not self._nav_filter_prefix:
            self.filter_checkbox.blockSignals(True)
            self.filter_checkbox.setChecked(True)
            self.filter_checkbox.blockSignals(False)
            return
        self._nav_filter_enabled = bool(checked)
        self._update_filter_indicator()
        self._refresh_tasks()

    def _on_filter_label_clicked(self, event) -> None:
        """Request clearing the navigation filter when the label is clicked."""
        self.filterClearRequested.emit()

    def _on_journal_checkbox_toggled(self, checked: bool) -> None:
        if not self._nav_filter_prefix:
            self.journal_checkbox.blockSignals(True)
            self.journal_checkbox.setChecked(True)
            self.journal_checkbox.blockSignals(False)
            return
        self._include_journal = bool(checked)
        self._refresh_tasks()

    def _update_filter_indicator(self) -> None:
        active = bool(self._nav_filter_prefix)
        self.filter_label.setVisible(active)
        self.filter_checkbox.setVisible(active)
        self.journal_checkbox.setVisible(active)
        if not active:
            self.filter_label.setStyleSheet("")
            self.filter_checkbox.blockSignals(True)
            self.filter_checkbox.setChecked(True)
            self.filter_checkbox.blockSignals(False)
            self.journal_checkbox.blockSignals(True)
            self.journal_checkbox.setChecked(True)
            self.journal_checkbox.blockSignals(False)
            self.journal_checkbox.setEnabled(False)
            self._nav_filter_enabled = True
            self._include_journal = True
            return
        self.filter_checkbox.blockSignals(True)
        self.filter_checkbox.setChecked(self._nav_filter_enabled)
        self.filter_checkbox.blockSignals(False)
        self.journal_checkbox.blockSignals(True)
        self.journal_checkbox.setChecked(self._include_journal)
        self.journal_checkbox.blockSignals(False)
        self.journal_checkbox.setEnabled(self._nav_filter_enabled)
        if self._nav_filter_enabled:
            self.filter_label.setStyleSheet(
                "color: #ffffff; background-color: #c62828; padding: 1px 6px; border-radius: 4px;"
            )
        else:
            self.filter_label.setStyleSheet("")

    def _adjust_font_size(self, delta: int) -> None:
        """Bump panel font size (Ctrl +/-) in tabs or popouts."""
        new_size = max(8, min(24, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        self._apply_font_size()
        config.save_panel_font_size(self._font_size_key, self._font_size)

    def adjust_font_size(self, delta: int) -> None:
        """Public wrapper used by parent containers to adjust fonts."""
        self._adjust_font_size(delta)

    def _apply_font_size(self) -> None:
        font = self.font()
        font.setPointSize(self._font_size)
        for widget in (
            self.search,
            self.tag_list,
            self.task_tree,
            self.filter_label,
            self.filter_checkbox,
            self.journal_checkbox,
            self.show_completed,
            self.show_future,
            self.show_actionable,
            self.zoom_in_btn,
            self.zoom_out_btn,
        ):
            try:
                widget.setFont(font)
            except Exception:
                pass

    def _save_splitter_sizes(self) -> None:
        try:
            sizes = self.splitter.sizes()
        except Exception:
            return
        config.save_splitter_sizes(self._splitter_key, sizes)

    def _save_header_state(self) -> None:
        try:
            state = bytes(self.task_tree.header().saveState().toBase64()).decode("ascii")
        except Exception:
            return
        config.save_header_state(self._header_state_key, state)

    @staticmethod
    def _normalize_task_path(path: Optional[str]) -> str:
        if not path:
            return ""
        norm = path.replace("\\", "/")
        if not norm.startswith("/"):
            norm = "/" + norm.lstrip("/")
        return norm

    def _task_matches_filter(self, task_path: str, prefix: str) -> bool:
        if not task_path or not prefix:
            return False
        if prefix == "/":
            return True
        if prefix.endswith(PAGE_SUFFIX):
            return task_path == prefix
        base = prefix.rstrip("/")
        if not base:
            return True
        if task_path.startswith(base + "/"):
            return True
        file_target = base + PAGE_SUFFIX
        return task_path == file_target

    def _is_journal_path(self, task_path: str) -> bool:
        if not task_path:
            return False
        norm = task_path
        journal_root = "/Journal"
        if norm == journal_root:
            return True
        if norm == journal_root + PAGE_SUFFIX:
            return True
        return norm.startswith(journal_root + "/")

    def _apply_nav_filter(self, tasks: list[dict]) -> list[dict]:
        if not tasks:
            return []
        if not self._nav_filter_prefix or not self._nav_filter_enabled:
            return tasks
        prefix = self._normalize_task_path(self._nav_filter_prefix)
        include_journal = self._include_journal
        filtered: list[dict] = []
        seen_ids: set = set()
        for task in tasks:
            task_path = self._normalize_task_path(task.get("path"))
            task_id = task.get("id") or task_path
            if self._task_matches_filter(task_path, prefix):
                if task_id not in seen_ids:
                    filtered.append(task)
                    seen_ids.add(task_id)
                continue
            if include_journal and self._is_journal_path(task_path):
                if task_id not in seen_ids:
                    filtered.append(task)
                    seen_ids.add(task_id)
        return filtered

    def focusInEvent(self, event):  # type: ignore[override]
        super().focusInEvent(event)
        # Don't auto-focus search - let user click what they want
        # Auto-focusing search was interfering with task tree double-clicks
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
        if getattr(self, "tag_list", None):
            viewport = self.tag_list.viewport()
            if obj is viewport and event.type() == QEvent.MouseButtonPress:
                if self.tag_list.itemAt(event.pos()) is None:
                    self.active_tags.clear()
                    self._refresh_tasks()
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
        tags = TAG_PATTERN.findall(text)
        if not tags:
            return text, None, []
        found = [t for t in tags if t in self._available_tags]
        missing = [t for t in tags if t not in self._available_tags]
        # Remove tags from query
        query = TAG_PATTERN.sub("", text).strip()
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
        self._refresh_tasks()

    def refresh(self) -> None:
        if not config.has_active_vault():
            self.clear()
            return
        self._refresh_tasks()

    def clear(self) -> None:
        self.active_tags.clear()
        self.tag_list.clear()
        self.task_tree.clear()
        self._visible_tasks = []
        self._tag_source_tasks = []
        self._nav_filter_prefix = None
        self._nav_filter_enabled = True
        self._include_journal = True
        self._update_filter_indicator()

    def _refresh_tags(self) -> None:
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        include_done = self.show_completed.isChecked()

        def _count_tags(tasks: list[dict]) -> list[tuple[str, int]]:
            counts: dict[str, int] = {}
            for task in tasks:
                if not include_done and task.get("status") == "done":
                    continue
                tag_set = set(task.get("tags", []))
                text = task.get("text", "") or ""
                for token in re.findall(r"@[A-Za-z0-9_]+", text):
                    tag_set.add(token)
                for tag in tag_set:
                    counts[tag] = counts.get(tag, 0) + 1
            return sorted(counts.items())

        if self._tag_source_tasks:
            tag_items = _count_tags(self._tag_source_tasks)
        elif self._nav_filter_prefix and self._nav_filter_enabled:
            tag_items = _count_tags(self._visible_tasks)
        else:
            try:
                all_tasks = config.fetch_tasks(
                    "",
                    [],
                    include_done=include_done,
                    include_ancestors=True,
                    actionable_only=False,
                )
                tag_items = _count_tags(list(all_tasks))
            except Exception:
                tag_items = [
                    (tag, count)
                    for tag, count in config.fetch_task_tags()
                    if include_done or count > 0
                ]
        self._available_tags = {tag for tag, _ in tag_items}
        # Drop active tags that are no longer available in the current view
        if self.active_tags:
            unavailable = {tag for tag in self.active_tags if tag not in self._available_tags}
            if unavailable:
                self.active_tags.difference_update(unavailable)
        for tag, count in tag_items:
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
        tasks = self._apply_nav_filter(tasks)

        if include_done:
            self._tag_source_tasks = list(tasks)
        else:
            extra_tasks = config.fetch_tasks(
                query,
                sorted(effective_tags),
                include_done=True,
                include_ancestors=True,
                actionable_only=False,
            )
            if self._nav_filter_prefix and self._nav_filter_enabled:
                extra_tasks = self._apply_nav_filter(extra_tasks)
            tag_source_map = {task.get("id") or task.get("path"): task for task in extra_tasks}
            for task in tasks:
                tag_source_map.setdefault(task.get("id") or task.get("path"), task)
            self._tag_source_tasks = list(tag_source_map.values())

        self.task_tree.clear()
        self._visible_tasks = []
        if not tasks:
            self._refresh_tags()
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
        visible_tasks: list[dict] = []
        for task in sorted(tasks, key=self._task_sort_key):
            if task["id"] not in visible_ids:
                continue
            visible_tasks.append(task)
            priority_level = min(task.get("priority", 0) or 0, 3)
            priority = "!" * priority_level
            due = task.get("due", "") or ""
            text = task["text"]
            display_text = self._format_task_text(text)
            display_path = self._present_path(task["path"])
            item = QTreeWidgetItem([priority, "", due, display_path])
            item.setText(1, display_text)
            item.setToolTip(1, text)
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
        self._visible_tasks = visible_tasks
        self.task_tree.expandAll()
        self.task_tree.sortItems(self.sort_column, self.sort_order)
        self._refresh_tags()

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
        self._refresh_tasks()

    def set_navigation_filter(self, prefix: Optional[str], refresh: bool = True) -> None:
        normalized = self._normalize_task_path(prefix) if prefix else None
        self._nav_filter_prefix = normalized
        self._nav_filter_enabled = True
        self._include_journal = True
        self._update_filter_indicator()
        if refresh:
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
            if os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", ""):
                print(f"[TASK_PANEL] _emit_task_activation: no task data on item")
            return
        if os.getenv("ZIMX_DEBUG_TASKS", "0") not in ("0", "false", "False", ""):
            print(f"[TASK_PANEL] _emit_task_activation: emitting signal for {task['path']}:{task.get('line') or 1}")
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
