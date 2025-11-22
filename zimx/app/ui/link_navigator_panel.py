from __future__ import annotations

import math

from dataclasses import dataclass
from typing import Optional, Sequence

from PySide6.QtGui import QBrush, QPen

from PySide6.QtCore import QPointF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPen, QBrush, QPainter
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QGraphicsItem,
    QGraphicsItem,
    QHBoxLayout,
    QMenu,
    QTextBrowser,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
    QToolButton,
)

from zimx.app import config
from .path_utils import path_to_colon


@dataclass
class _LinkNode:
    path: str
    label: str
    direction: str  # "incoming" or "outgoing"


class _LinkNodeItem(QGraphicsEllipseItem):
    """Ellipse node that keeps track of the underlying page path."""

    def __init__(self, path: str, label: str, radius: float, color: QColor) -> None:
        super().__init__(-radius, -radius, radius * 2, radius * 2)
        self.page_path = path
        self.setBrush(QBrush(color))
        self.setPen(QPen(QColor("#222222"), 2))
        self.setToolTip(label)

        # Center the label inside the node
        text = QGraphicsTextItem(label, self)
        font = text.font()
        font.setPointSize(13)
        font.setBold(True)
        text.setFont(font)
        text.setDefaultTextColor(QColor("#222222"))  # dark for contrast
        text.setZValue(10)
        text.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        # Center label after font is set
        text_rect = text.boundingRect()
        text.setPos(-text_rect.width() / 2, -text_rect.height() / 2)
        self.text_item = text

        # Draw dots for each depth layer (number of colons in path)
        depth = path.count(":") + path.count("/")
        if depth > 0:
            from PySide6.QtWidgets import QGraphicsEllipseItem
            dot_radius = 3
            spacing = 8
            total_width = (depth - 1) * spacing
            y_offset = radius - 10  # place dots near bottom inside node
            for i in range(depth):
                x = -total_width / 2 + i * spacing
                dot = QGraphicsEllipseItem(-dot_radius, -dot_radius, dot_radius * 2, dot_radius * 2, self)
                dot.setPos(x, y_offset)
                dot.setBrush(QBrush(QColor("#f8f8f8")))
                dot.setPen(QPen(QColor("#222222"), 1))


class LinkGraphView(QGraphicsView):
    """Lightweight graph view with clickable nodes."""

    nodeActivated = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._zoom = 1.0
        self._scene_rect = None
        self._edges: list[tuple[QGraphicsLineItem, _LinkNodeItem, _LinkNodeItem]] = []
        self._nodes: list[_LinkNodeItem] = []
        self._base_positions: dict[_LinkNodeItem, QPointF] = {}
        self._wiggle_timer = QTimer(self)
        self._wiggle_timer.setInterval(140)
        self._wiggle_timer.timeout.connect(self._apply_wiggle)
        self._wiggle_target: Optional[_LinkNodeItem] = None
        self._wiggle_phase = 0

    def set_graph(
        self,
        center: _LinkNode,
        incoming: Sequence[_LinkNode],
        outgoing: Sequence[_LinkNode],
    ) -> None:
        self._scene.clear()
        self._edges.clear()
        self._nodes.clear()
        center_radius = 42
        center_item = _LinkNodeItem(center.path, center.label, center_radius, QColor("#4A90E2"))
        center_item.setPos(0, 0)
        self._scene.addItem(center_item)
        self._nodes.append(center_item)
        self._base_positions[center_item] = QPointF(0, 0)

        neighbors = list(incoming) + list(outgoing)
        if not neighbors:
            placeholder = QGraphicsTextItem("No links yet")
            placeholder.setDefaultTextColor(QColor("#999999"))
            placeholder.setPos(-placeholder.boundingRect().width() / 2, -8)
            self._scene.addItem(placeholder)

        radius = 170
        incoming_positions = self._spread_positions(len(incoming), math.radians(-150), math.radians(-30), radius)
        outgoing_positions = self._spread_positions(len(outgoing), math.radians(30), math.radians(150), radius)

        for node, pos in zip(incoming, incoming_positions):
            self._add_neighbor(center_item, node, pos, QColor("#7BD88F"))
        for node, pos in zip(outgoing, outgoing_positions):
            self._add_neighbor(center_item, node, pos, QColor("#F5A623"))

        bounds = self._scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        self._scene_rect = bounds
        self._scene.setSceneRect(bounds)
        self._apply_zoom()
        self._wiggle_target = None
        self._wiggle_timer.stop()

    def _add_neighbor(self, center_item: _LinkNodeItem, node: _LinkNode, pos: QPointF, color: QColor) -> None:
        item = _LinkNodeItem(node.path, node.label, 28, color)
        item.setPos(pos)
        self._scene.addItem(item)
        self._nodes.append(item)
        self._base_positions[item] = QPointF(pos.x(), pos.y())

        # Draw line from edge of center node to edge of neighbor node
        from math import atan2, cos, sin
        center_pos = center_item.pos()
        neighbor_pos = pos
        dx = neighbor_pos.x() - center_pos.x()
        dy = neighbor_pos.y() - center_pos.y()
        angle = atan2(dy, dx)
        center_radius = center_item.rect().width() / 2
        neighbor_radius = item.rect().width() / 2
        start_x = center_pos.x() + cos(angle) * center_radius
        start_y = center_pos.y() + sin(angle) * center_radius
        end_x = neighbor_pos.x() - cos(angle) * neighbor_radius
        end_y = neighbor_pos.y() - sin(angle) * neighbor_radius
        line = QGraphicsLineItem(start_x, start_y, end_x, end_y)
        pen = QPen(QColor("#777777"), 1.5)
        line.setPen(pen)
        self._scene.addItem(line)
        self._edges.append((line, center_item, item))

    def clear(self) -> None:
        self._scene.clear()
        self._scene_rect = None
        self._edges.clear()
        self._nodes.clear()
        self._base_positions.clear()

    def wheelEvent(self, event):  # type: ignore[override]
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_in(self) -> None:
        self._adjust_zoom(1.1)

    def zoom_out(self) -> None:
        self._adjust_zoom(1 / 1.1)

    def _adjust_zoom(self, factor: float) -> None:
        self._zoom = min(2.5, max(0.4, self._zoom * factor))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if not self._scene_rect:
            return
        self.resetTransform()
        self.fitInView(self._scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.scale(self._zoom, self._zoom)

    def _spread_positions(self, count: int, start_angle: float, end_angle: float, radius: float) -> list[QPointF]:
        if count <= 0:
            return []
        span = end_angle - start_angle
        return [
            QPointF(
                radius * math.cos(start_angle + span * (idx + 1) / (count + 1)),
                radius * math.sin(start_angle + span * (idx + 1) / (count + 1)),
            )
            for idx in range(count)
        ]

    def mousePressEvent(self, event):  # type: ignore[override]
        item = self.itemAt(event.pos())
        node_item = self._resolve_node_item(item)
        if node_item:
            self.nodeActivated.emit(node_item.page_path)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        item = self.itemAt(event.pos())
        node_item = self._resolve_node_item(item)
        if node_item != self._wiggle_target:
            self._wiggle_target = node_item
            self._wiggle_phase = 0
            if node_item:
                self._wiggle_timer.start()
                self._highlight_node(node_item)
            else:
                self._wiggle_timer.stop()
                self._highlight_node(None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]
        self._wiggle_timer.stop()
        self._wiggle_target = None
        self._highlight_node(None)
        super().leaveEvent(event)

    def _resolve_node_item(self, item):
        current = item
        while current and not isinstance(current, _LinkNodeItem):
            current = current.parentItem()
        return current

    def _add_arrow(self, line_item: QGraphicsLineItem, towards_node: bool, color: QColor) -> None:
        """Draw an arrowhead on the line; towards_node=True means pointing to neighbor, else to center."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        from PySide6.QtWidgets import QGraphicsPolygonItem

        line = line_item.line()
        arrow_size = 10.0
        # Place arrow near the target end
        pos = line.pointAt(0.85 if towards_node else 0.15)
        angle = math.atan2(line.dy(), line.dx())
        sin_a = math.sin(angle)
        cos_a = math.cos(angle)

        left = QPointF(
            pos.x() + sin_a * arrow_size - cos_a * arrow_size * 0.6,
            pos.y() - cos_a * arrow_size - sin_a * arrow_size * 0.6,
        )
        right = QPointF(
            pos.x() - sin_a * arrow_size - cos_a * arrow_size * 0.6,
            pos.y() + cos_a * arrow_size - sin_a * arrow_size * 0.6,
        )
        polygon = QPolygonF([pos, left, right])
        arrow = QGraphicsPolygonItem(polygon)
        arrow.setBrush(QBrush(color))
        arrow.setPen(QPen(color))
        self._scene.addItem(arrow)

    def _apply_wiggle(self) -> None:
        if not self._wiggle_target:
            return
        amplitude = 2.5
        offset = amplitude * math.sin(self._wiggle_phase * math.pi / 4)
        base = self._base_positions.get(self._wiggle_target, self._wiggle_target.pos())
        self._wiggle_target.setPos(base.x() + offset, base.y())
        self._wiggle_phase = (self._wiggle_phase + 1) % 8
        if self._wiggle_phase == 0:
            # Reset to base to avoid drift
            self._wiggle_target.setPos(base)

    def _highlight_node(self, target: Optional[_LinkNodeItem]) -> None:
        default_pen = QPen(QColor("#555555"), 1.5)
        highlight_pen = QPen(QColor("#f0f0f0"), 2.6)
        for line, a, b in self._edges:
            if target and target in (a, b):
                line.setPen(highlight_pen)
                line.setZValue(1)
            else:
                line.setPen(default_pen)
                line.setZValue(0)
        for node in self._nodes:
            brush = node.brush()
            color = brush.color()
            color.setAlpha(255 if target and node is target else 200)
            brush.setColor(color)
            node.setBrush(brush)


class LinkNavigatorPanel(QWidget):
    """Tabbed panel that renders a link graph or raw backlink data."""

    pageActivated = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self.current_page: Optional[str] = None
        self.mode = "graph"

        self.title_label = QLabel("Link Navigator")
        self.title_label.setStyleSheet("font-weight: bold; padding: 6px 8px;")

        self.graph_view = LinkGraphView()
        self.graph_view.setMinimumHeight(320)
        self.graph_view.nodeActivated.connect(self.pageActivated.emit)
        self.graph_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.graph_view.customContextMenuRequested.connect(self._open_context_menu)

        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("-")
        self.zoom_out_btn.setToolTip("Zoom out (Ctrl + scroll down)")
        self.zoom_out_btn.clicked.connect(self.graph_view.zoom_out)

        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Zoom in (Ctrl + scroll up)")
        self.zoom_in_btn.clicked.connect(self.graph_view.zoom_in)

        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 8, 4)
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.zoom_out_btn)
        header.addWidget(self.zoom_in_btn)

        self.raw_view = QTextBrowser()
        self.raw_view.setOpenLinks(False)
        self.raw_view.setOpenExternalLinks(False)
        self.raw_view.anchorClicked.connect(lambda url: self.pageActivated.emit(url.toString()))
        self.raw_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.raw_view.customContextMenuRequested.connect(self._open_context_menu)
        self.raw_view.setStyleSheet("font-family: monospace;")

        self.stack = QStackedLayout()
        self.stack.addWidget(self.graph_view)
        self.stack.addWidget(self.raw_view)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addLayout(self.stack)
        self.setLayout(layout)

    def set_page(self, page_path: Optional[str]) -> None:
        self.current_page = page_path
        self.refresh()

    def refresh(self, page_path: Optional[str] = None) -> None:
        if page_path is not None:
            self.current_page = page_path
        if not self.current_page or not config.has_active_vault():
            self.graph_view.clear()
            self.raw_view.setPlainText("No page selected.")
            self.title_label.setText("Link Navigator")
            return

        relations = config.fetch_link_relations(self.current_page)
        all_paths = set([self.current_page] + relations["incoming"] + relations["outgoing"])
        titles = config.fetch_page_titles(all_paths)

        center = _LinkNode(
            path=self.current_page,
            label=self._display_label(self.current_page, titles),
            direction="center",
        )
        incoming_nodes = [
            _LinkNode(path=p, label=self._display_label(p, titles), direction="incoming")
            for p in relations["incoming"]
        ]
        outgoing_nodes = [
            _LinkNode(path=p, label=self._display_label(p, titles), direction="outgoing")
            for p in relations["outgoing"]
        ]
        self.graph_view.set_graph(center, incoming_nodes, outgoing_nodes)
        self._update_raw_view(center, incoming_nodes, outgoing_nodes)
        self.title_label.setText(f"Link Navigator: {center.label}")

    def _display_label(self, path: str, titles: dict[str, str]) -> str:
        if path in titles and titles[path]:
            return titles[path]
        colon = path_to_colon(path)
        if colon:
            return colon
        return path.rsplit("/", 1)[-1] or path

    def _update_raw_view(self, center: _LinkNode, incoming: Sequence[_LinkNode], outgoing: Sequence[_LinkNode]) -> None:
        def _link_html(node: _LinkNode, arrow: str) -> str:
            colon = path_to_colon(node.path) or node.path
            return f"{arrow} <a href=\"{node.path}\">:{colon}</a> ({node.label})"

        parts = [f"<b>Page:</b> {center.label}", "<br><b>Links from here:</b>"]
        if outgoing:
            parts.extend(_link_html(node, "→") for node in outgoing)
        else:
            parts.append("(none)")
        parts.append("<br><b>Links to here:</b>")
        if incoming:
            parts.extend(_link_html(node, "←") for node in incoming)
        else:
            parts.append("(none)")
        html = "<br>".join(parts)
        self.raw_view.setHtml(html)

    def _open_context_menu(self, pos) -> None:
        menu = QMenu(self)
        if self.mode == "graph":
            toggle = menu.addAction("Show Raw Links")
        else:
            toggle = menu.addAction("Show Graph View")
        toggle.triggered.connect(self._toggle_mode)
        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(lambda: self.refresh())
        widget = self.sender()
        global_pos = widget.mapToGlobal(pos) if hasattr(widget, "mapToGlobal") else self.mapToGlobal(pos)
        menu.exec(global_pos)

    def _toggle_mode(self) -> None:
        self.mode = "raw" if self.mode == "graph" else "graph"
        self.stack.setCurrentIndex(1 if self.mode == "raw" else 0)
