from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .heading_utils import heading_slug


class TableOfContentsWidget(QFrame):
    """Floating outline widget that lists headings inside the editor."""

    headingActivated = Signal(int)  # Absolute cursor position for the heading
    collapsedChanged = Signal(bool)
    linkCopied = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tocWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            QFrame#tocWidget {
                background-color: rgba(20, 20, 20, 0.85);
                border: 1px solid #444;
                border-radius: 6px;
            }
            QTreeWidget#tocTree::item {
                padding: 0px 2px;
            }
            """
        )
        self._collapsed = False
        self._expanded_width = 220
        self._base_path = ""
        self._headings: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        self.toggle_button = QToolButton()
        self.toggle_button.setText("ToC")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.clicked.connect(self._handle_toggle_clicked)

        header_layout.addWidget(self.toggle_button)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self.tree = QTreeWidget()
        self.tree.setObjectName("tocTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.itemClicked.connect(self._on_item_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)

        self.setFixedWidth(self._expanded_width)

    # --- Public API -----------------------------------------------------
    def set_headings(self, headings: Iterable[dict]) -> None:
        """Populate outline tree with heading entries."""
        self._headings = list(headings or [])
        self.tree.clear()
        parents: dict[int, QTreeWidgetItem] = {0: self.tree.invisibleRootItem()}
        for entry in self._headings:
            level = int(entry.get("level", 1))
            level = max(1, min(5, level))
            text = entry.get("title") or "(untitled heading)"
            item = QTreeWidgetItem([text])
            data = {
                "position": entry.get("position", 0),
                "line": entry.get("line", 1),
                "title": text,
                "level": level,
                "anchor": heading_slug(text),
            }
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            parent = parents.get(level - 1, self.tree.invisibleRootItem())
            parent.addChild(item)
            parents[level] = item
            # Remove deeper levels so future headings attach correctly
            for key in list(parents.keys()):
                if key > level:
                    parents.pop(key, None)
        self.tree.expandAll()
        self._update_placeholder()
        self._update_geometry()

    def set_base_path(self, colon_path: str) -> None:
        """Set the base colon path for copy-link actions."""
        self._base_path = colon_path or ""

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self.tree.setVisible(not collapsed)
        self.setFixedWidth(70 if collapsed else self._expanded_width)
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(not collapsed)
        self.toggle_button.blockSignals(False)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)
        self.collapsedChanged.emit(collapsed)
        self._update_geometry()

    def collapsed(self) -> bool:
        return self._collapsed

    # --- Internal helpers -----------------------------------------------
    def _handle_toggle_clicked(self, checked: bool) -> None:
        # Checked means expanded
        self.set_collapsed(not checked)

    def _on_item_activated(self, item: QTreeWidgetItem) -> None:
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        position = int(data.get("position", 0))
        self.headingActivated.emit(position)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self)
        copy_action = QAction("Copy Link Location", self)
        copy_action.triggered.connect(lambda: self._copy_link(data))
        menu.addAction(copy_action)
        global_pos = self.tree.viewport().mapToGlobal(pos)
        menu.exec(global_pos)

    def _copy_link(self, data: dict) -> None:
        anchor = data.get("anchor") or ""
        if not anchor:
            return
        base = self._base_path or ""
        link = f"{base}#{anchor}" if base else f"#{anchor}"
        QApplication.clipboard().setText(link)
        self.linkCopied.emit(link)

    def _update_placeholder(self) -> None:
        if self._headings:
            return
        item = QTreeWidgetItem(["(No headings)"])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.tree.addTopLevelItem(item)

    def _update_geometry(self) -> None:
        self.adjustSize()
        self.updateGeometry()
        self.raise_()
