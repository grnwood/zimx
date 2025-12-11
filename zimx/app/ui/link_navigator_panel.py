from __future__ import annotations

import math

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from PySide6.QtGui import QBrush, QPen, QFont

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QColor, QPen, QBrush, QPainter
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
    QGraphicsItem,
    QGraphicsItem,
    QGraphicsRectItem,
    QHBoxLayout,
    QGraphicsOpacityEffect,
    QMenu,
    QTextBrowser,
    QLabel,
    QCheckBox,
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
        self._radius = radius
        self._base_color = QColor(color)
        self._base_brush = QBrush(color)
        self.setBrush(self._base_brush)
        self._base_pen = QPen(QColor("#222222"), 2)
        self._focus_pen = QPen(QColor("#ffffff"), 3.4)
        self.setPen(self._base_pen)
        self.setToolTip(label)
        self._rect_mode = False
        self._depth_dots: list[QGraphicsEllipseItem] = []

        # Center the label inside the node with a crisp outline for readability
        text = QGraphicsSimpleTextItem(label, self)
        font = QFont(text.font())
        font.setPointSize(14)
        font.setWeight(QFont.Weight.Black)
        text.setFont(font)
        text.setBrush(QBrush(QColor("#ffffff")))
        text.setPen(QPen(QColor("#000000"), 2))
        text.setZValue(10)
        text.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        # Center label after font is set
        text_rect = text.boundingRect()
        text.setPos(-text_rect.width() / 2, -text_rect.height() / 2)
        self.text_item = text
        self._add_depth_dots()
        self._rect_overlay = QGraphicsRectItem(-radius, -radius, radius * 2, radius * 2, self)
        self._rect_overlay.setVisible(False)
        self._rect_overlay.setZValue(3)

    def set_focused(self, focused: bool) -> None:
        self.setPen(self._focus_pen if focused else self._base_pen)
        if not focused and not self._rect_mode:
            self.setBrush(self._base_brush)

    def _add_depth_dots(self) -> None:
        """Render small dots indicating depth; keeps constructors lean."""
        depth = self.page_path.count(":") + self.page_path.count("/")
        if depth <= 0:
            return
        from PySide6.QtWidgets import QGraphicsEllipseItem
        dot_radius = 3
        spacing = 8
        total_width = (depth - 1) * spacing
        y_offset = self._radius - 10  # place dots near bottom inside node
        for i in range(depth):
            x = -total_width / 2 + i * spacing
            dot = QGraphicsEllipseItem(-dot_radius, -dot_radius, dot_radius * 2, dot_radius * 2, self)
            dot.setPos(x, y_offset)
            dot.setBrush(QBrush(QColor("#f8f8f8")))
            dot.setPen(QPen(QColor("#222222"), 1))
            self._depth_dots.append(dot)

    def show_rect_overlay(self, width: float, height: float, color: QColor) -> None:
        self._rect_mode = True
        w = max(24.0, width)
        h = max(18.0, height)
        self._rect_overlay.setRect(-w / 2, -h / 2, w, h)
        rect_color = QColor(color)
        fill = QColor(rect_color)
        fill.setAlpha(min(255, max(60, int(rect_color.alpha() or 255))))
        self._rect_overlay.setBrush(QBrush(fill))
        pen = QPen(rect_color.darker(140), 1.6)
        self._rect_overlay.setPen(pen)
        self._rect_overlay.setVisible(True)
        self.setBrush(QBrush(Qt.transparent))
        self.setPen(QPen(Qt.transparent))
        self.text_item.setZValue(5)
        for dot in self._depth_dots:
            dot.setVisible(False)

    def hide_rect_overlay(self) -> None:
        if not self._rect_mode:
            return
        self._rect_mode = False
        self._rect_overlay.setVisible(False)
        self.setBrush(self._base_brush)
        self.setPen(self._base_pen)
        for dot in self._depth_dots:
            dot.setVisible(True)


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
        # Soft, spacey backdrop
        self.setStyleSheet(
            "background: qradialgradient(cx:0.5, cy:0.5, radius:0.9, "
            "fx:0.5, fy:0.45, stop:0 rgba(18,18,26,230), stop:1 rgba(10,10,14,255));"
        )
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setFocusPolicy(Qt.StrongFocus)
        self._zoom = 1.0
        self._scene_rect = None
        self._edges: list[tuple[QGraphicsLineItem, _LinkNodeItem, _LinkNodeItem]] = []
        self._nodes: list[_LinkNodeItem] = []
        self._base_positions: dict[_LinkNodeItem, QPointF] = {}
        self._wiggle_timer = QTimer(self)
        # Fast cadence for a "vibrate" feel
        self._wiggle_timer.setInterval(22)
        self._wiggle_timer.timeout.connect(self._apply_wiggle)
        self._wiggle_target: Optional[_LinkNodeItem] = None
        self._wiggle_phase = 0
        self._press_pos: Optional[QPointF] = None
        self._press_item: Optional[_LinkNodeItem] = None
        self._dragging = False
        self._label_hide_threshold = 0.9
        self._center_item: Optional[_LinkNodeItem] = None
        self._incoming_items: list[_LinkNodeItem] = []
        self._outgoing_items: list[_LinkNodeItem] = []
        self._focused_item: Optional[_LinkNodeItem] = None
        self._keyboard_nav_used = False
        self._mode_toggle_handler: Optional[Callable[[], None]] = None
        self._selection_anim_timer = QTimer(self)
        self._selection_anim_timer.setInterval(16)
        self._selection_anim_timer.timeout.connect(self._advance_selection_animation)
        self._selection_anim_running = False
        self._selection_anim_steps = 0
        self._selection_anim_step = 0
        self._selection_anim_start = QPointF()
        self._selection_anim_target = QPointF()
        self._selection_last_zoom = 1.0
        self._selection_overlay_opacity = 0.0
        self._selection_overlay_start = QPointF()
        self._selection_overlay_target = QPointF()
        self._selection_overlay_radius = 0.0
        self._selection_overlay_current = QPointF()
        self._pending_fade_in = False
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._fade_opacity = 1.0
        self._opacity_effect.setOpacity(self._fade_opacity)
        self.viewport().setGraphicsEffect(self._opacity_effect)
        self._fade_target = 1.0
        self._fade_step = 0.05
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._advance_fade)
        self._fade_direction = 0
        self._hover_item: Optional[_LinkNodeItem] = None
        self._labels_congested = False
        self._repulse_timer = QTimer(self)
        self._repulse_timer.setInterval(16)
        self._repulse_timer.timeout.connect(self._apply_repulse_step)
        self._repulse_goal = 0.0
        self._repulse_progress = 0.0
        self._repulse_offsets: dict[_LinkNodeItem, QPointF] = {}
        self._repulse_target: Optional[_LinkNodeItem] = None
        self._repulse_radius = 160.0
        self._repulse_strength = 32.0
        self._layered_mode = False
        self._treemap_mode = False
        self._ring_items: list[QGraphicsEllipseItem] = []
        self._center_path: str = ""
        self._node_depths: dict[_LinkNodeItem, int] = {}
        self._ring_layout: dict[int, list[_LinkNodeItem]] = {}
        self._ring_depths: list[int] = []

    def set_graph(
        self,
        center: _LinkNode,
        incoming: Sequence[_LinkNode],
        outgoing: Sequence[_LinkNode],
    ) -> None:
        self._scene.clear()
        self._edges.clear()
        self._nodes.clear()
        self._incoming_items.clear()
        self._outgoing_items.clear()
        self._node_depths.clear()
        self._ring_layout.clear()
        self._ring_depths.clear()
        self._focused_item = None
        self._center_item = None
        center_radius = 42
        center_item = _LinkNodeItem(center.path, center.label, center_radius, QColor("#4A90E2"))
        center_item.setPos(0, 0)
        self._scene.addItem(center_item)
        self._nodes.append(center_item)
        self._base_positions[center_item] = QPointF(0, 0)
        self._center_item = center_item
        self._node_depths[center_item] = 0

        self._center_path = center.path
        neighbors = list(incoming) + list(outgoing)
        if not neighbors:
            placeholder = QGraphicsTextItem("No links yet")
            placeholder.setDefaultTextColor(QColor("#999999"))
            placeholder.setPos(-placeholder.boundingRect().width() / 2, -8)
            self._scene.addItem(placeholder)

        for node in incoming:
            item = self._add_neighbor(center_item, node, QPointF(0, 0), QColor("#7BD88F"))
            self._incoming_items.append(item)
        for node in outgoing:
            item = self._add_neighbor(center_item, node, QPointF(0, 0), QColor("#F5A623"))
            self._outgoing_items.append(item)

        self._apply_layout()

        bounds = self._scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        self._scene_rect = bounds
        self._scene.setSceneRect(bounds)
        self._hover_item = None
        self._repulse_target = None
        self._repulse_progress = 0.0
        self._repulse_goal = 0.0
        self._repulse_offsets.clear()
        self._repulse_timer.stop()
        self._labels_congested = self._detect_congestion()
        self._selection_overlay_opacity = 0.0
        if self._pending_fade_in:
            self._fade_opacity = 0.05
            self._update_opacity_effect()
            self._begin_fade(1, 1.0)
        else:
            self._fade_opacity = 1.0
            self._update_opacity_effect()
        self._apply_zoom()
        self._wiggle_target = None
        self._wiggle_timer.stop()
        # Default focus to the center node
        if self._center_item:
            self._set_focused_item(self._center_item)

    def _add_neighbor(self, center_item: _LinkNodeItem, node: _LinkNode, pos: QPointF, color: QColor) -> _LinkNodeItem:
        item = _LinkNodeItem(node.path, node.label, 28, color)
        item.setPos(pos)
        self._scene.addItem(item)
        self._nodes.append(item)
        self._base_positions[item] = QPointF(pos.x(), pos.y())
        depth = self._depth_hint(node.path)
        self._node_depths[item] = depth

        line = QGraphicsLineItem()
        pen = QPen(QColor("#777777"), 1.5)
        line.setPen(pen)
        self._scene.addItem(line)
        self._edges.append((line, center_item, item))
        self._update_edge(line, center_item, item)
        return item

    def set_layered_mode(self, enabled: bool) -> None:
        if self._treemap_mode and enabled:
            return
        if self._layered_mode == enabled:
            return
        self._layered_mode = enabled
        self._apply_layout()
        self._apply_zoom()

    def set_treemap_mode(self, enabled: bool) -> None:
        if self._treemap_mode == enabled:
            return
        self._treemap_mode = enabled
        if enabled and self._layered_mode:
            self._layered_mode = False
        self._apply_layout()
        self._apply_zoom()

    def _apply_layout(self) -> None:
        if not self._center_item:
            return
        self._clear_rings()
        nodes = self._incoming_items + self._outgoing_items
        if self._treemap_mode:
            self._ring_layout.clear()
            self._ring_depths.clear()
            for item in nodes:
                self._base_positions[item] = QPointF(item.pos())
            rects = self._compute_treemap_rects()
            for item in nodes:
                rect = rects.get(item)
                if rect:
                    self._apply_treemap_rect(item, rect)
            for line, _, _ in self._edges:
                line.setVisible(False)
            self._labels_congested = False
        else:
            if not self._layered_mode:
                self._ring_layout.clear()
                self._ring_depths.clear()
            positions: dict[_LinkNodeItem, QPointF] = {}
            for item in nodes:
                item.hide_rect_overlay()
                item.setOpacity(1.0)
            for line, _, _ in self._edges:
                line.setVisible(True)
            if self._layered_mode:
                positions, radii, ring_layout = self._compute_layered_positions()
                self._ring_layout = ring_layout
                self._ring_depths = sorted(self._ring_layout.keys())
                self._render_layer_rings(radii)
            else:
                positions = self._compute_arc_positions()
            for item, pos in positions.items():
                item.setPos(pos)
                self._base_positions[item] = QPointF(pos.x(), pos.y())
            self._refresh_edges()
            self._labels_congested = self._detect_congestion()

    def _compute_arc_positions(self) -> dict[_LinkNodeItem, QPointF]:
        positions: dict[_LinkNodeItem, QPointF] = {}
        radius = 170
        incoming_positions = self._spread_positions(
            len(self._incoming_items), math.radians(-150), math.radians(-30), radius
        )
        outgoing_positions = self._spread_positions(
            len(self._outgoing_items), math.radians(30), math.radians(150), radius
        )
        for item, pos in zip(self._incoming_items, incoming_positions):
            positions[item] = pos
        for item, pos in zip(self._outgoing_items, outgoing_positions):
            positions[item] = pos
        return positions

    def _compute_layered_positions(
        self,
    ) -> tuple[dict[_LinkNodeItem, QPointF], list[float], dict[int, list[_LinkNodeItem]]]:
        positions: dict[_LinkNodeItem, QPointF] = {}
        nodes = self._incoming_items + self._outgoing_items
        if not nodes:
            return positions, [], {}
        ring_gap = 130.0
        base_radius = 140.0
        ring_map: dict[int, list[_LinkNodeItem]] = {}
        for node in nodes:
            depth = self._node_depths.get(node, self._depth_hint(node.page_path))
            ring_map.setdefault(depth, []).append(node)
        radii: list[float] = []
        angle_cache: dict[_LinkNodeItem, float] = {}
        for depth in sorted(ring_map):
            ring_nodes = ring_map[depth]
            radius = base_radius + (depth - 1) * ring_gap
            radii.append(radius)
            count = len(ring_nodes)
            if count == 1:
                angles = [math.radians(-90)]
            else:
                angles = [
                    (2 * math.pi * idx) / count
                    for idx in range(count)
                ]
            for angle, node in zip(angles, ring_nodes):
                x = radius * math.cos(angle)
                y = radius * math.sin(angle)
                positions[node] = QPointF(x, y)
                angle_cache[node] = angle
                fade = max(0.45, 1.0 - (depth - 1) * 0.22)
                node.setOpacity(fade)
        ordered_rings: dict[int, list[_LinkNodeItem]] = {}
        for depth, ring_nodes in ring_map.items():
            ordered = sorted(ring_nodes, key=lambda node: angle_cache.get(node, 0.0))
            ordered_rings[depth] = ordered
        return positions, radii, ordered_rings

    def _render_layer_rings(self, radii: list[float]) -> None:
        for radius in radii:
            ring = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
            ring.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
            ring.setBrush(Qt.NoBrush)
            ring.setZValue(-2)
            self._scene.addItem(ring)
            self._ring_items.append(ring)

    def _compute_treemap_rects(self) -> dict[_LinkNodeItem, QRectF]:
        nodes = self._incoming_items + self._outgoing_items
        rects: dict[_LinkNodeItem, QRectF] = {}
        if not nodes:
            return rects
        values: dict[_LinkNodeItem, float] = {node: self._treemap_value(node) for node in nodes}
        groups: dict[str, list[_LinkNodeItem]] = {}
        for node in nodes:
            key = self._treemap_group_key(node.page_path)
            groups.setdefault(key, []).append(node)
        total = sum(sum(values[n] for n in members) for members in groups.values()) or 1.0
        full_width = 640.0
        full_height = 420.0
        x_cursor = -full_width / 2
        for key in sorted(groups.keys()):
            members = groups[key]
            group_value = sum(values[n] for n in members) or 1.0
            group_width = max(80.0, full_width * (group_value / total))
            y_cursor = -full_height / 2
            for node in members:
                node_value = values[node]
                rect_height = max(30.0, full_height * (node_value / group_value))
                rects[node] = QRectF(x_cursor, y_cursor, group_width, rect_height)
                y_cursor += rect_height
            x_cursor += group_width
        return rects

    def _apply_treemap_rect(self, item: _LinkNodeItem, rect: QRectF) -> None:
        color = QColor("#7BD88F") if item in self._incoming_items else QColor("#F5A623")
        item.setPos(rect.center())
        self._base_positions[item] = QPointF(rect.center())
        item.show_rect_overlay(rect.width(), rect.height(), color)

    def _treemap_group_key(self, path: str) -> str:
        rel = self._relative_parts(path)
        if rel:
            return rel[0]
        if path == self._center_path:
            return "center"
        return "incoming" if any(node.page_path == path for node in self._incoming_items) else "outgoing"

    def _treemap_value(self, node: _LinkNodeItem) -> float:
        label = getattr(node, "label", node.page_path)
        base = max(6.0, float(len(label)) * 1.5)
        depth = self._depth_hint(node.page_path)
        return base * (1.0 + depth * 0.15)

    def _relative_parts(self, path: str) -> list[str]:
        center_parts = self._path_parts(self._center_path)
        parts = self._path_parts(path)
        if not center_parts:
            return parts
        idx = 0
        while idx < len(center_parts) and idx < len(parts) and center_parts[idx] == parts[idx]:
            idx += 1
        return parts[idx:]

    def _clear_rings(self) -> None:
        if not self._ring_items:
            return
        for ring in self._ring_items:
            try:
                self._scene.removeItem(ring)
            except Exception:
                pass
        self._ring_items.clear()

    def _depth_hint(self, path: str) -> int:
        center_depth = self._path_depth(self._center_path)
        node_depth = self._path_depth(path)
        depth = abs(node_depth - center_depth)
        return max(1, min(4, depth or 1))

    @staticmethod
    def _path_depth(path: str) -> int:
        return len(LinkGraphView._path_parts(path))

    @staticmethod
    def _path_parts(path: str) -> list[str]:
        if not path:
            return []
        if ":" in path:
            return [chunk for chunk in path.split(":") if chunk]
        stripped = path.strip("/")
        if not stripped:
            return []
        return [chunk for chunk in stripped.split("/") if chunk]

    def clear(self) -> None:
        self._scene.clear()
        self._scene_rect = None
        self._edges.clear()
        self._nodes.clear()
        self._base_positions.clear()
        self._keyboard_nav_used = False
        self._wiggle_timer.stop()
        self._wiggle_target = None
        self._hover_item = None
        self._labels_congested = False
        self._repulse_timer.stop()
        self._repulse_offsets.clear()
        self._repulse_progress = 0.0
        self._repulse_goal = 0.0
        self._repulse_target = None
        self._pending_fade_in = False
        self._begin_fade(0, self._fade_opacity)
        self._fade_opacity = 1.0
        self._update_opacity_effect()
        self._clear_rings()
        self._center_path = ""
        self._node_depths.clear()
        self._ring_layout.clear()
        self._ring_depths.clear()

    def wheelEvent(self, event):  # type: ignore[override]
        """Use mouse wheel/trackpad to zoom the graph directly."""
        # Prefer high-resolution pixel delta (trackpads), fall back to standard angle delta
        delta = event.pixelDelta().y() if not event.pixelDelta().isNull() else event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x() or event.pixelDelta().x()
        if delta:
            self._reset_fade()
            # Scale factor is mild for smooth trackpad scrolling
            factor = 1.0 + (abs(delta) / 960.0)
            if delta > 0:
                self._adjust_zoom(factor)
            else:
                self._adjust_zoom(1.0 / factor)
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_in(self) -> None:
        self._adjust_zoom(1.1)

    def zoom_out(self) -> None:
        self._adjust_zoom(1 / 1.1)

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._apply_zoom()
        self._reset_fade()

    def _adjust_zoom(self, factor: float) -> None:
        self._zoom = min(2.5, max(0.4, self._zoom * factor))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if not self._scene_rect:
            return
        self.resetTransform()
        self.fitInView(self._scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.scale(self._zoom, self._zoom)
        self._update_label_visibility()
        self._reset_fade()

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

    def _detect_congestion(self) -> bool:
        if len(self._nodes) <= 1:
            return False
        rects: list[QRectF] = []
        scale = max(0.2, self._zoom)
        padding = 6.0 / scale
        for node in self._nodes:
            text_rect = node.text_item.boundingRect()
            width = text_rect.width() / scale
            height = text_rect.height() / scale
            rect = QRectF(-width / 2, -height / 2, width, height)
            base = self._base_positions.get(node, node.pos())
            rect.translate(base)
            rects.append(rect.adjusted(-padding, -padding, padding, padding))
        for idx, rect in enumerate(rects):
            for other in rects[idx + 1 :]:
                if rect.intersects(other):
                    return True
        return False

    def mousePressEvent(self, event):  # type: ignore[override]
        self._press_pos = event.pos()
        self._press_item = self._resolve_node_item(self.itemAt(event.pos()))
        self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        item = self.itemAt(event.pos())
        node_item = self._resolve_node_item(item)
        self._set_hover_target(node_item)
        # When hovering, temporarily focus the hovered node for clarity without stealing keyboard focus
        if node_item:
            self._set_hover_focus(node_item)
        if event.buttons() & Qt.LeftButton and self._press_pos is not None:
            # Mark as dragging once the cursor moves a little to allow panning the scene
            if (event.pos() - self._press_pos).manhattanLength() > 6:
                self._dragging = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if not self._dragging and self._press_item and event.button() == Qt.LeftButton:
            # Treat as click (no drag) -> activate
            self._activate_node(self._press_item)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self._press_pos = None
        self._press_item = None
        self._dragging = False

    def keyPressEvent(self, event):  # type: ignore[override]
        handled = False
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            handled = self._move_focus(key)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            if self._focused_item:
                self._activate_node(self._focused_item)
                handled = True
        elif key == Qt.Key_Space:
            if self._mode_toggle_handler:
                self._mode_toggle_handler()
                handled = True
        if handled:
            event.accept()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]
        self._set_hover_target(None)
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
        # Higher-frequency, small-amplitude shake to feel energetic without obscuring text
        amplitude = 1.4
        offset_x = amplitude * math.sin(self._wiggle_phase * 1.6)
        offset_y = (amplitude * 0.65) * math.cos(self._wiggle_phase * 2.1)
        base = self._base_positions.get(self._wiggle_target, self._wiggle_target.pos())
        self._wiggle_target.setPos(base.x() + offset_x, base.y() + offset_y)
        self._wiggle_phase = (self._wiggle_phase + 1) % 24
        if self._wiggle_phase == 0:
            # Reset to base to avoid drift
            self._wiggle_target.setPos(base)
        self._refresh_edges()

    def _configure_repulse(self, target: Optional[_LinkNodeItem]) -> None:
        if not self._labels_congested or not target:
            self._repulse_target = None
            self._repulse_goal = 0.0
            if self._repulse_progress == 0.0:
                if not self._repulse_offsets:
                    self._repulse_timer.stop()
                self._apply_repulse_positions()
                self._repulse_timer.stop()
            else:
                if not self._repulse_timer.isActive():
                    self._repulse_timer.start()
            return

        self._repulse_target = target
        self._repulse_offsets = self._compute_repulse_offsets(target)
        self._repulse_progress = 0.0
        self._repulse_goal = 1.0 if self._repulse_offsets else 0.0
        if self._repulse_goal == 0.0:
            self._repulse_timer.stop()
            self._apply_repulse_positions()
            return
        if not self._repulse_timer.isActive():
            self._repulse_timer.start()

    def _compute_repulse_offsets(self, target: _LinkNodeItem) -> dict[_LinkNodeItem, QPointF]:
        offsets: dict[_LinkNodeItem, QPointF] = {}
        base_target = self._base_positions.get(target, target.pos())
        for node in self._nodes:
            if node is target:
                continue
            base = self._base_positions.get(node, node.pos())
            dx = base.x() - base_target.x()
            dy = base.y() - base_target.y()
            distance = math.hypot(dx, dy)
            if distance <= 0.1 or distance > self._repulse_radius:
                continue
            intensity = (self._repulse_radius - distance) / self._repulse_radius
            magnitude = intensity * self._repulse_strength
            offsets[node] = QPointF((dx / distance) * magnitude, (dy / distance) * magnitude)
        return offsets

    def _apply_repulse_step(self) -> None:
        if not self._repulse_offsets and self._repulse_goal == 0.0 and self._repulse_progress == 0.0:
            self._repulse_timer.stop()
            return
        step = 0.12
        if self._repulse_progress < self._repulse_goal:
            self._repulse_progress = min(self._repulse_goal, self._repulse_progress + step)
        elif self._repulse_progress > self._repulse_goal:
            self._repulse_progress = max(self._repulse_goal, self._repulse_progress - step)
        else:
            if self._repulse_goal == 0.0:
                self._repulse_offsets.clear()
            self._repulse_timer.stop()
            return
        self._apply_repulse_positions()
        if self._repulse_progress == self._repulse_goal == 0.0:
            self._repulse_offsets.clear()
            self._repulse_timer.stop()

    def _apply_repulse_positions(self) -> None:
        if not self._nodes:
            return
        ease = self._ease_out_cubic(self._repulse_progress)
        for node in self._nodes:
            base = self._base_positions.get(node, node.pos())
            offset = self._repulse_offsets.get(node)
            if not offset or (self._repulse_goal == 0.0 and self._repulse_progress == 0.0):
                node.setPos(base)
            else:
                node.setPos(QPointF(base.x() + offset.x() * ease, base.y() + offset.y() * ease))
        if self._repulse_goal == 0.0 and self._repulse_progress == 0.0:
            self._repulse_offsets.clear()
        self._refresh_edges()

    @staticmethod
    def _ease_out_cubic(progress: float) -> float:
        clamped = max(0.0, min(1.0, progress))
        return 1 - pow(1 - clamped, 3)

    def _refresh_edges(self) -> None:
        for line, a, b in self._edges:
            self._update_edge(line, a, b)

    def _update_edge(self, line: QGraphicsLineItem, a: _LinkNodeItem, b: _LinkNodeItem) -> None:
        start_pos = a.pos()
        end_pos = b.pos()
        dx = end_pos.x() - start_pos.x()
        dy = end_pos.y() - start_pos.y()
        angle = math.atan2(dy, dx) if dx or dy else 0.0
        start_radius = a.rect().width() / 2
        end_radius = b.rect().width() / 2
        start_x = start_pos.x() + math.cos(angle) * start_radius
        start_y = start_pos.y() + math.sin(angle) * start_radius
        end_x = end_pos.x() - math.cos(angle) * end_radius
        end_y = end_pos.y() - math.sin(angle) * end_radius
        line.setLine(start_x, start_y, end_x, end_y)
        neighbor = b if a is self._center_item else a
        opacity = neighbor.opacity() if neighbor else 1.0
        color = QColor("#777777")
        color.setAlphaF(0.4 + 0.6 * max(0.2, min(1.0, opacity)))
        pen = line.pen()
        pen.setColor(color)
        line.setPen(pen)

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
            # Ensure hovered node (and its label) stay visually on top
            if target and node is target:
                node.setZValue(5)
                node.text_item.setZValue(6)
            else:
                node.setZValue(0)
                node.text_item.setZValue(1)
        self._update_label_visibility()

    def _set_focused_item(self, target: Optional[_LinkNodeItem], force_show_label: bool = True) -> None:
        if self._focused_item is target:
            return
        # Clear previous focus
        if self._focused_item:
            self._focused_item.set_focused(False)
        self._focused_item = target
        if self._focused_item:
            self._focused_item.set_focused(True)
            self._focused_item.setZValue(6)
            self._focused_item.text_item.setZValue(7)
            self._focused_item.text_item.setVisible(True)
        if self._focused_item and force_show_label:
            if self._labels_congested:
                self._configure_repulse(self._focused_item)
            else:
                self._configure_hover_wiggle(self._focused_item if self._keyboard_nav_used else None)
        elif not self._focused_item:
            self._wiggle_timer.stop()
            self._wiggle_target = None
            if self._labels_congested:
                self._configure_repulse(None)
        # Always refresh highlight state to raise focused node/edges
        self._highlight_node(self._focused_item)
        self._update_label_visibility()

    def _set_hover_focus(self, node: _LinkNodeItem) -> None:
        # Do not steal keyboard focus; just keep hovered label visible and on top
        if node is not self._focused_item:
            node.text_item.setVisible(True)
            node.text_item.setZValue(7)

    def _set_hover_target(self, node: Optional[_LinkNodeItem]) -> None:
        prev = self._hover_item
        changed = prev is not node
        self._hover_item = node
        if self._labels_congested:
            if changed:
                self._configure_repulse(node)
            self._wiggle_timer.stop()
            self._wiggle_target = None
        else:
            self._configure_hover_wiggle(node if changed else self._hover_item)
        if changed:
            self._highlight_node(node)
        self._update_label_visibility()

    def set_mode_toggle_handler(self, handler: Callable[[], None]) -> None:
        self._mode_toggle_handler = handler

    def _configure_hover_wiggle(self, node: Optional[_LinkNodeItem]) -> None:
        if self._labels_congested:
            self._wiggle_timer.stop()
            self._wiggle_target = None
            return
        if node:
            self._wiggle_target = node
            self._wiggle_phase = 0
            self._wiggle_timer.start()
        else:
            self._wiggle_timer.stop()
            self._wiggle_target = None

    def _activate_node(self, node: _LinkNodeItem) -> None:
        self._animate_selection_to_node(node)
        self.nodeActivated.emit(node.page_path)

    def _animate_selection_to_node(self, node: _LinkNodeItem) -> None:
        if not node:
            return
        if self._selection_anim_running:
            self._selection_anim_timer.stop()
            self._selection_anim_running = False
            if abs(self._selection_last_zoom - 1.0) > 1e-3:
                self.scale(1.0 / self._selection_last_zoom, 1.0 / self._selection_last_zoom)
                self._selection_last_zoom = 1.0
        center_scene = self.mapToScene(self.viewport().rect().center())
        self._selection_anim_start = center_scene
        self._selection_anim_target = node.scenePos()
        self._selection_anim_steps = 20
        self._selection_anim_step = 0
        self._selection_last_zoom = 1.0
        self._selection_anim_running = True
        self._pending_fade_in = True
        self._selection_overlay_start = node.scenePos()
        self._selection_overlay_target = center_scene
        self._selection_overlay_radius = node.rect().width() / 2
        self._selection_overlay_opacity = 1.0
        self._selection_overlay_current = node.scenePos()
        self._begin_fade(-1, 0.08)
        self._selection_anim_timer.start()

    def _advance_selection_animation(self) -> None:
        if not self._selection_anim_running:
            self._selection_anim_timer.stop()
            return
        self._selection_anim_step += 1
        progress = self._selection_anim_step / max(1, self._selection_anim_steps)
        clamped = min(1.0, progress)
        ease = self._ease_out_cubic(clamped)
        interp_x = self._selection_anim_start.x() + (self._selection_anim_target.x() - self._selection_anim_start.x()) * ease
        interp_y = self._selection_anim_start.y() + (self._selection_anim_target.y() - self._selection_anim_start.y()) * ease
        self.centerOn(QPointF(interp_x, interp_y))
        overlay_ease = self._ease_out_cubic(min(1.0, clamped * 1.4))
        overlay_x = self._selection_overlay_start.x() + (self._selection_overlay_target.x() - self._selection_overlay_start.x()) * overlay_ease
        overlay_y = self._selection_overlay_start.y() + (self._selection_overlay_target.y() - self._selection_overlay_start.y()) * overlay_ease
        self._selection_overlay_current = QPointF(overlay_x, overlay_y)
        self._selection_overlay_opacity = max(0.0, 1.0 - clamped * 0.85)
        self.viewport().update()
        if self._selection_overlay_opacity < 0.0:
            self._selection_overlay_opacity = 0.0
        if self._selection_anim_step >= self._selection_anim_steps:
            self._selection_anim_running = False
            self._selection_anim_timer.stop()
            if abs(self._selection_last_zoom - 1.0) > 1e-3:
                self.scale(1.0 / self._selection_last_zoom, 1.0 / self._selection_last_zoom)
            self._selection_last_zoom = 1.0
            self.centerOn(self._selection_anim_target)
            self._selection_overlay_opacity = 0.0

    def _begin_fade(self, direction: int, target: float) -> None:
        self._fade_direction = direction
        self._fade_target = max(0.0, min(1.0, target))
        if direction == 0:
            self._fade_timer.stop()
            return
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def _reset_fade(self) -> None:
        """Ensure the graph isn't dimmed during zoom gestures."""
        self._fade_timer.stop()
        self._fade_direction = 0
        self._fade_target = 1.0
        self._fade_opacity = 1.0
        self._pending_fade_in = False
        self._update_opacity_effect()

    def _advance_fade(self) -> None:
        if self._fade_direction == 0:
            self._fade_timer.stop()
            return
        step = self._fade_step * (1 if self._fade_direction > 0 else -1)
        self._fade_opacity = max(0.0, min(1.0, self._fade_opacity + step))
        reached = False
        if self._fade_direction < 0 and self._fade_opacity <= self._fade_target + 1e-3:
            self._fade_opacity = self._fade_target
            reached = True
        elif self._fade_direction > 0 and self._fade_opacity >= self._fade_target - 1e-3:
            self._fade_opacity = self._fade_target
            reached = True
        self._update_opacity_effect()
        if reached:
            if self._fade_direction > 0:
                self._pending_fade_in = False
            self._fade_direction = 0
            self._fade_timer.stop()

    def _update_opacity_effect(self) -> None:
        if self._opacity_effect:
            self._opacity_effect.setOpacity(self._fade_opacity)

    def _current_group_and_index(self) -> tuple[str, int]:
        if not self._focused_item:
            return ("center", 0)
        if self._focused_item is self._center_item:
            return ("center", 0)
        if self._focused_item in self._incoming_items:
            return ("incoming", self._sorted_ring(self._incoming_items).index(self._focused_item))
        if self._focused_item in self._outgoing_items:
            return ("outgoing", self._sorted_ring(self._outgoing_items).index(self._focused_item))
        return ("center", 0)

    def _sorted_ring(self, items: list[_LinkNodeItem]) -> list[_LinkNodeItem]:
        """Order nodes by their layout (left-to-right) for predictable arrow traversal."""
        return sorted(items, key=lambda i: self._base_positions.get(i, i.pos()).x())

    def _move_focus(self, key: int) -> bool:
        # Mark that keyboard navigation is in use so focus animation can engage
        self._keyboard_nav_used = True
        if self._treemap_mode:
            moved = self._move_focus_treemap(key)
            if moved:
                return True
        if self._layered_mode and not self._treemap_mode and self._ring_layout:
            moved = self._move_focus_layered(key)
            if moved:
                return True
        group, idx = self._current_group_and_index()
        def pick(lst: list[_LinkNodeItem], new_idx: int, prefer_left: bool = False, prefer_right: bool = False) -> bool:
            if not lst:
                return False
            ordered = self._sorted_ring(lst)
            # When jumping from center, choose leftmost/rightmost intentionally
            if prefer_left:
                target = ordered[0]
            elif prefer_right:
                target = ordered[-1]
            else:
                target = ordered[new_idx % len(ordered)]
            self._set_focused_item(target)
            return True

        if key == Qt.Key_Left:
            if group == "incoming":
                return pick(self._incoming_items, idx - 1)
            if group == "outgoing":
                return pick(self._outgoing_items, idx - 1)
            # center: prefer incoming ring
            if self._incoming_items:
                return pick(self._incoming_items, 0, prefer_left=True)
            if self._outgoing_items:
                return pick(self._outgoing_items, 0, prefer_left=True)
        elif key == Qt.Key_Right:
            if group == "incoming":
                return pick(self._incoming_items, idx + 1)
            if group == "outgoing":
                return pick(self._outgoing_items, idx + 1)
            # center: prefer outgoing ring
            if self._outgoing_items:
                return pick(self._outgoing_items, 0, prefer_right=True)
            if self._incoming_items:
                return pick(self._incoming_items, 0, prefer_right=True)
        elif key == Qt.Key_Up:
            if group == "center":
                return pick(self._incoming_items, 0, prefer_left=True)
            if group == "incoming":
                return pick(self._incoming_items, idx - 1)
            if group == "outgoing":
                # move toward center from outgoing
                if self._center_item:
                    self._set_focused_item(self._center_item)
                    return True
        elif key == Qt.Key_Down:
            if group == "center":
                return pick(self._outgoing_items, 0)
            if group == "outgoing":
                return pick(self._outgoing_items, idx + 1)
            if group == "incoming":
                # move toward center from incoming
                if self._center_item:
                    self._set_focused_item(self._center_item)
                    return True
        return False

    def _move_focus_treemap(self, key: int) -> bool:
        direction_map = {
            Qt.Key_Left: QPointF(-1.0, 0.0),
            Qt.Key_Right: QPointF(1.0, 0.0),
            Qt.Key_Up: QPointF(0.0, -1.0),
            Qt.Key_Down: QPointF(0.0, 1.0),
        }
        direction = direction_map.get(key)
        if direction is None:
            return False
        current = self._focused_item or self._center_item
        if not current:
            return False
        target = self._directional_neighbor(current, direction, strict=True)
        if not target:
            target = self._directional_neighbor(current, direction, strict=False)
        if target:
            self._set_focused_item(target)
            return True
        return False

    def _directional_neighbor(
        self,
        current: _LinkNodeItem,
        direction: QPointF,
        strict: bool,
    ) -> Optional[_LinkNodeItem]:
        candidates = [
            node
            for node in (self._incoming_items + self._outgoing_items)
            if node is not current
        ]
        if current is not self._center_item and self._center_item:
            candidates.append(self._center_item)
        if not candidates:
            return None
        dir_length = math.hypot(direction.x(), direction.y())
        if dir_length <= 0.0:
            return None
        dx = direction.x() / dir_length
        dy = direction.y() / dir_length
        base = self._base_positions.get(current, current.pos())
        best_node = None
        best_dot = -float("inf")
        best_distance = float("inf")
        min_dot = 0.3 if strict else -1.0
        for node in candidates:
            pos = self._base_positions.get(node, node.pos())
            vec_x = pos.x() - base.x()
            vec_y = pos.y() - base.y()
            distance = math.hypot(vec_x, vec_y)
            if distance <= 1e-3:
                continue
            vx = vec_x / distance
            vy = vec_y / distance
            dot = vx * dx + vy * dy
            if dot < min_dot:
                continue
            if dot > best_dot + 1e-4 or (abs(dot - best_dot) <= 1e-4 and distance < best_distance):
                best_dot = dot
                best_distance = distance
                best_node = node
        return best_node

    def _move_focus_layered(self, key: int) -> bool:
        current = self._focused_item or self._center_item
        if not current:
            return False
        depth = self._node_depths.get(current, 0)
        direction_vectors = {
            Qt.Key_Left: QPointF(-1.0, 0.0),
            Qt.Key_Right: QPointF(1.0, 0.0),
            Qt.Key_Up: QPointF(0.0, -1.0),
            Qt.Key_Down: QPointF(0.0, 1.0),
        }

        if depth == 0 and key in direction_vectors:
            next_depth = self._nearest_outer_depth(0)
            if next_depth is None:
                return False
            candidates = self._ring_layout.get(next_depth, [])
            target = self._closest_in_direction(candidates, direction_vectors[key])
            if target:
                self._set_focused_item(target)
                return True
            return False

        if key in (Qt.Key_Left, Qt.Key_Right) and depth > 0:
            ring_nodes = self._ring_layout.get(depth, [])
            if len(ring_nodes) <= 1 or current not in ring_nodes:
                return False
            idx = ring_nodes.index(current)
            delta = 1 if key == Qt.Key_Right else -1
            target = ring_nodes[(idx + delta) % len(ring_nodes)]
            self._set_focused_item(target)
            return True

        if key == Qt.Key_Up and depth > 0:
            if depth == 1 and self._center_item:
                self._set_focused_item(self._center_item)
                return True
            inner_depth = self._nearest_inner_depth(depth)
            if inner_depth is None:
                return False
            if inner_depth == 0 and self._center_item:
                self._set_focused_item(self._center_item)
                return True
            ring_nodes = self._ring_layout.get(inner_depth, [])
            target = self._closest_by_angle(ring_nodes, self._node_angle(current))
            if target:
                self._set_focused_item(target)
                return True
            return False

        if key == Qt.Key_Down:
            next_depth = self._nearest_outer_depth(depth)
            if next_depth is None:
                return False
            ring_nodes = self._ring_layout.get(next_depth, [])
            if not ring_nodes:
                return False
            if depth == 0:
                target = self._closest_in_direction(ring_nodes, direction_vectors[Qt.Key_Down])
            else:
                target = self._closest_by_angle(ring_nodes, self._node_angle(current))
            if target:
                self._set_focused_item(target)
                return True
            return False
        return False

    def _node_angle(self, node: _LinkNodeItem) -> float:
        pos = self._base_positions.get(node, node.pos())
        return math.atan2(pos.y(), pos.x())

    def _closest_by_angle(self, nodes: list[_LinkNodeItem], target_angle: float) -> Optional[_LinkNodeItem]:
        if not nodes:
            return None
        best_node = None
        best_delta = float("inf")
        for node in nodes:
            delta = abs((self._node_angle(node) - target_angle + math.pi) % (2 * math.pi) - math.pi)
            if delta < best_delta:
                best_delta = delta
                best_node = node
        return best_node

    def _closest_in_direction(self, nodes: list[_LinkNodeItem], direction: QPointF) -> Optional[_LinkNodeItem]:
        if not nodes:
            return None
        dir_length = math.hypot(direction.x(), direction.y())
        if dir_length <= 0.0:
            return None
        dx = direction.x() / dir_length
        dy = direction.y() / dir_length
        best_node = None
        best_score = -float("inf")
        for node in nodes:
            pos = self._base_positions.get(node, node.pos())
            length = math.hypot(pos.x(), pos.y())
            if length <= 0.0:
                continue
            vx = pos.x() / length
            vy = pos.y() / length
            score = vx * dx + vy * dy
            if score > best_score:
                best_score = score
                best_node = node
        return best_node

    def _nearest_inner_depth(self, depth: int) -> Optional[int]:
        inner = [d for d in self._ring_depths if d < depth]
        if not inner:
            return None
        return inner[-1]

    def _nearest_outer_depth(self, depth: int) -> Optional[int]:
        for candidate in self._ring_depths:
            if candidate > depth:
                return candidate
        return None

    def reset_keyboard_focus_state(self) -> None:
        """Reset keyboard navigation effects (used after opening a new page)."""
        self._keyboard_nav_used = False
        self._wiggle_timer.stop()
        self._wiggle_target = None
        if self._labels_congested:
            self._configure_repulse(None)

    def _update_label_visibility(self) -> None:
        congested_now = self._detect_congestion()
        if congested_now != self._labels_congested:
            self._labels_congested = congested_now
            if not congested_now:
                self._configure_repulse(None)
            elif self._hover_item:
                self._configure_repulse(self._hover_item)
        hide_for_zoom = self._zoom < self._label_hide_threshold
        for node in self._nodes:
            show = not hide_for_zoom and not self._labels_congested
            if node is self._focused_item:
                show = True
            elif self._hover_item is node:
                show = True
            elif hide_for_zoom:
                show = False
            elif self._labels_congested:
                show = False
            node.text_item.setVisible(show)

    def drawForeground(self, painter, rect):  # type: ignore[override]
        super().drawForeground(painter, rect)
        if self._selection_overlay_opacity <= 0.01:
            return
        painter.save()
        size_factor = 1.0 + (1.0 - self._selection_overlay_opacity) * 1.4
        radius = self._selection_overlay_radius * max(1.0, size_factor)
        color = QColor("#4A90E2")
        color.setAlphaF(min(1.0, max(0.0, self._selection_overlay_opacity)))
        pen = QPen(color, 3)
        pen.setCosmetic(True)
        painter.setPen(pen)
        fill = QColor(color)
        fill.setAlphaF(color.alphaF() * 0.35)
        painter.setBrush(QBrush(fill))
        rect = QRectF(
            self._selection_overlay_current.x() - radius,
            self._selection_overlay_current.y() - radius,
            radius * 2,
            radius * 2,
        )
        painter.drawEllipse(rect)
        painter.restore()


class LinkNavigatorPanel(QWidget):
    """Tabbed panel that renders a link graph or raw backlink data."""

    pageActivated = Signal(str)
    openInWindowRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self.current_page: Optional[str] = None
        self.mode = "graph"
        self.setFocusPolicy(Qt.StrongFocus)

        self.title_label = QLabel("Link Navigator")
        self.title_label.setStyleSheet("font-weight: bold; padding: 6px 8px;")

        self.graph_view = LinkGraphView()
        self.graph_view.set_mode_toggle_handler(self._toggle_mode)
        # Route panel focus to the graph so arrow keys work without extra clicks
        self.setFocusProxy(self.graph_view)
        self.graph_view.setMinimumHeight(320)
        self.graph_view.nodeActivated.connect(self.pageActivated.emit)
        self.graph_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.graph_view.customContextMenuRequested.connect(self._open_context_menu)
        self.legend_widget = self._build_legend_widget()
        self.layered_checkbox = QCheckBox("Layered / concentric")
        self.layered_checkbox.setChecked(False)
        self.layered_checkbox.setStyleSheet(
            "color:#f2f2f2; padding:2px 6px; font-size:12px;"
        )
        self.layered_checkbox.toggled.connect(self._on_layered_toggled)
        self.graph_view.set_layered_mode(self.layered_checkbox.isChecked())
        self.treemap_checkbox = QCheckBox("Treemap layout")
        self.treemap_checkbox.setChecked(False)
        self.treemap_checkbox.setStyleSheet(
            "color:#f2f2f2; padding:2px 6px; font-size:12px;"
        )
        self.treemap_checkbox.toggled.connect(self._on_treemap_toggled)
        self.graph_view.set_treemap_mode(self.treemap_checkbox.isChecked())

        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("-")
        self.zoom_out_btn.setToolTip("Zoom out (Ctrl + scroll down)")
        self.zoom_out_btn.clicked.connect(self.graph_view.zoom_out)

        self.zoom_reset_btn = QToolButton()
        self.zoom_reset_btn.setText("⟳")
        self.zoom_reset_btn.setToolTip("Reset zoom to fit")
        self.zoom_reset_btn.clicked.connect(self.graph_view.reset_zoom)

        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Zoom in (Ctrl + scroll up)")
        self.zoom_in_btn.clicked.connect(self.graph_view.zoom_in)

        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 8, 4)
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.zoom_out_btn)
        header.addWidget(self.zoom_reset_btn)
        header.addWidget(self.zoom_in_btn)

        self.raw_view = QTextBrowser()
        self.raw_view.setOpenLinks(False)
        self.raw_view.setOpenExternalLinks(False)
        self.raw_view.anchorClicked.connect(lambda url: self.pageActivated.emit(url.toString()))
        self.raw_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.raw_view.customContextMenuRequested.connect(self._open_context_menu)
        self.raw_view.setStyleSheet("font-family: monospace;")
        self.raw_view.installEventFilter(self)

        self.stack = QStackedLayout()
        graph_container = QWidget()
        graph_layout = QVBoxLayout()
        graph_layout.setContentsMargins(0, 0, 0, 0)
        graph_layout.setSpacing(0)
        control_row = QHBoxLayout()
        control_row.setContentsMargins(12, 6, 12, 0)
        control_row.setSpacing(6)
        self.raw_toggle_checkbox = QCheckBox("Show raw links")
        self.raw_toggle_checkbox.setStyleSheet("color:#f2f2f2; padding:2px 6px; font-size:12px;")
        self.raw_toggle_checkbox.toggled.connect(lambda checked: self.set_mode("raw" if checked else "graph", persist=True))
        control_row.addWidget(self.layered_checkbox)
        control_row.addWidget(self.treemap_checkbox)
        control_row.addWidget(self.raw_toggle_checkbox)
        control_row.addStretch(1)
        graph_layout.addLayout(control_row)
        graph_layout.addWidget(self.graph_view, 1)
        graph_layout.addWidget(self.legend_widget, 0)
        graph_container.setLayout(graph_layout)
        self.stack.addWidget(graph_container)
        self.stack.addWidget(self.raw_view)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addLayout(self.stack)
        self.setLayout(layout)
        self.legend_widget.setVisible(True)
        self.reload_mode_from_config()
        self.reload_layout_from_config()

    def set_page(self, page_path: Optional[str]) -> None:
        self.current_page = page_path
        self.refresh()

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.raw_view and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Space:
                self._toggle_mode()
                return True
        return super().eventFilter(obj, event)

    def refresh(self, page_path: Optional[str] = None) -> None:
        # Preserve focus only if the panel currently has it
        had_focus = self.hasFocus() or self.graph_view.hasFocus() or self.raw_view.hasFocus()
        try:
            self.reload_mode_from_config()
        except Exception:
            pass
        try:
            self.reload_layout_from_config()
        except Exception:
            pass
        if page_path is not None:
            self.current_page = page_path
        if not self.current_page or not config.has_active_vault():
            self.graph_view.clear()
            self.raw_view.setPlainText("No page selected.")
            self.title_label.setText("Link Navigator")
            if had_focus:
                target = self.graph_view if self.mode == "graph" else self.raw_view
                target.setFocus()
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
        # Fit the new graph without requiring a manual refresh click
        self.graph_view.reset_zoom()
        # Disable keyboard-driven animation until user navigates again
        self.graph_view.reset_keyboard_focus_state()
        if had_focus:
            target = self.graph_view if self.mode == "graph" else self.raw_view
            target.setFocus()
        self._update_raw_view(center, incoming_nodes, outgoing_nodes)
        self.title_label.setText(f"Link Navigator: {center.label}")

    def _display_label(self, path: str, titles: dict[str, str]) -> str:
        if path in titles and titles[path]:
            return titles[path]
        colon = path_to_colon(path)
        if colon:
            return colon.split(":")[-1] or colon
        leaf = path.rsplit("/", 1)[-1] or path
        # Drop trailing .txt if present
        if leaf.endswith(".txt"):
            leaf = leaf[:-4]
        return leaf

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
        target_path = None
        widget = self.sender()
        if widget is self.graph_view:
            item = self.graph_view.itemAt(pos)
            if hasattr(item, "page_path"):
                target_path = getattr(item, "page_path", None)
        elif widget is self.raw_view:
            href = self.raw_view.anchorAt(pos)
            if href:
                target_path = href

        menu = QMenu(self)
        if target_path:
            open_win = menu.addAction("Open in Editor Window")
            open_win.triggered.connect(lambda: self.openInWindowRequested.emit(target_path))
            menu.addSeparator()
        if self.mode == "graph":
            toggle = menu.addAction("Show Raw Links")
        else:
            toggle = menu.addAction("Show Graph View")
        toggle.triggered.connect(self._toggle_mode)
        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(self._refresh_and_reset_zoom)
        global_pos = widget.mapToGlobal(pos) if hasattr(widget, "mapToGlobal") else self.mapToGlobal(pos)
        menu.exec(global_pos)

    def _toggle_mode(self) -> None:
        self.set_mode("raw" if self.mode == "graph" else "graph", persist=True)

    def set_mode(self, mode: str, *, persist: bool = False) -> None:
        normalized = "raw" if (mode or "").lower() == "raw" else "graph"
        if self.mode == normalized and not persist:
            return
        had_focus = self.hasFocus() or self.graph_view.hasFocus() or self.raw_view.hasFocus()
        self.mode = normalized
        self.stack.setCurrentIndex(1 if self.mode == "raw" else 0)
        self.legend_widget.setVisible(self.mode == "graph")
        try:
            self.raw_toggle_checkbox.blockSignals(True)
            self.raw_toggle_checkbox.setChecked(self.mode == "raw")
        finally:
            self.raw_toggle_checkbox.blockSignals(False)
        if persist:
            try:
                config.save_link_navigator_mode(self.mode)
            except Exception:
                pass
        if had_focus:
            target = self.graph_view if self.mode == "graph" else self.raw_view
            target.setFocus()

    def reload_mode_from_config(self) -> None:
        try:
            saved = config.load_link_navigator_mode(self.mode)
        except Exception:
            saved = self.mode
        self.set_mode(saved)

    def _apply_layout_selection(self, layout: str, persist: bool = False) -> None:
        normalized = layout if layout in {"layered", "treemap"} else "default"
        # Update checkboxes without retriggering persistence when requested
        try:
            self.layered_checkbox.blockSignals(True)
            self.treemap_checkbox.blockSignals(True)
            self.layered_checkbox.setChecked(normalized == "layered")
            self.treemap_checkbox.setChecked(normalized == "treemap")
        finally:
            self.layered_checkbox.blockSignals(False)
            self.treemap_checkbox.blockSignals(False)
        # Apply to the view
        self.graph_view.set_layered_mode(normalized == "layered")
        self.graph_view.set_treemap_mode(normalized == "treemap")
        if persist:
            self._persist_layout_choice(normalized)

    def _persist_layout_choice(self, forced: Optional[str] = None) -> None:
        layout = forced
        if layout not in {"layered", "treemap"}:
            if self.treemap_checkbox.isChecked():
                layout = "treemap"
            elif self.layered_checkbox.isChecked():
                layout = "layered"
            else:
                layout = "default"
        try:
            config.save_link_navigator_layout(layout)
        except Exception:
            pass

    def reload_layout_from_config(self) -> None:
        try:
            layout = config.load_link_navigator_layout("default")
        except Exception:
            layout = "default"
        self._apply_layout_selection(layout, persist=False)

    def _on_layered_toggled(self, checked: bool) -> None:
        # Compute desired layout based on both checkboxes and reapply in one place
        layout = "layered" if checked else ("treemap" if self.treemap_checkbox.isChecked() else "default")
        self._apply_layout_selection(layout, persist=True)
        if self.mode == "graph":
            self.graph_view.setFocus()

    def _on_treemap_toggled(self, checked: bool) -> None:
        layout = "treemap" if checked else ("layered" if self.layered_checkbox.isChecked() else "default")
        self._apply_layout_selection(layout, persist=True)
        if self.mode == "graph":
            self.graph_view.setFocus()

    def _refresh_and_reset_zoom(self) -> None:
        self.refresh()
        self.graph_view.reset_zoom()
        self.legend_widget.setVisible(self.mode == "graph")

    def _build_legend_widget(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background: rgba(0,0,0,0.35); padding: 6px 8px; font-size: 12px;")
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(18)

        def chip(color: str, text: str) -> QWidget:
            entry = QWidget()
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(f"background:{color}; border-radius:3px;")
            label = QLabel(text)
            label.setStyleSheet("color:#f5f5f5;")
            row.addWidget(swatch)
            row.addWidget(label)
            entry.setLayout(row)
            return entry

        layout.addWidget(chip("#4A90E2", "Current page"))
        layout.addWidget(chip("#7BD88F", "Links to here"))
        layout.addWidget(chip("#F5A623", "Links from here"))
        dots = QLabel("Stacked dots = hierarchy depth cues")
        dots.setStyleSheet("color: #f0f0f0;")
        layout.addWidget(dots)
        layout.addStretch(1)
        widget.setLayout(layout)
        return widget
