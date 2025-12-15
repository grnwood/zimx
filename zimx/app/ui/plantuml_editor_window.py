"""Standalone PlantUML editor window with split view and rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QSize, QMimeData, QRect
from PySide6.QtGui import QKeySequence, QShortcut, QPixmap, QImage, QTextCursor, QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QLabel,
    QScrollArea,
    QSplitter,
    QToolButton,
    QMenu,
    QApplication,
    QMessageBox,
    QFileDialog,
    QComboBox,
    QLineEdit,
    QPlainTextDocumentLayout,
    QDialog,
    QPushButton,
    QTextEdit,
)
from PySide6.QtGui import QPainter, QColor, QTextFormat

from zimx.app.plantuml_renderer import PlantUMLRenderer, RenderResult
from .ai_chat_panel import ApiWorker, ServerManager
from zimx.app import config

_LOGGING = os.getenv("ZIMX_PLANTUML_DEBUG", "0") not in ("0", "false", "False", "", None)


class LineNumberArea(QWidget):
    """Line number area for editor."""
    
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        # Slightly lighter than editor background (editor is ~30, line numbers ~40)
        painter.fillRect(event.rect(), QColor(40, 40, 40))
        
        block = self.editor.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
        bottom = top + self.editor.blockBoundingRect(block).height()
        
        # Line number text color: light gray
        painter.setPen(QColor(128, 128, 128))
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                lineNum = blockNumber + 1
                painter.drawText(5, int(top), self.editor.lineNumberAreaWidth() - 10, 
                                int(self.editor.blockBoundingRect(block).height()),
                                Qt.AlignRight, str(lineNum))
            block = block.next()
            top = bottom
            bottom = top + self.editor.blockBoundingRect(block).height()
            blockNumber += 1


class PlainTextEditWithLineNumbers(QPlainTextEdit):
    """PlainTextEdit with integrated line number area."""
    
    def __init__(self):
        super().__init__()
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_numbers)
        self._update_line_number_area_width(0)

    def lineNumberAreaWidth(self):
        # Always allocate space for 3 digits (999 lines max before wrapping display)
        digits = 3
        space = 3 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def _update_line_number_area_width(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def _update_line_numbers(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))


class ZoomablePreviewLabel(QLabel):
    """Custom label that handles Ctrl+MouseWheel for zooming and left-click drag for panning."""
    
    zoomRequested = Signal(int)  # delta (positive = zoom in, negative = zoom out)
    
    def __init__(self):
        super().__init__()
        self.pan_start_pos = None
        self.is_panning = False
    
    def wheelEvent(self, event) -> None:
        """Handle mouse wheel - zoom on Ctrl modifier."""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoomRequested.emit(1)  # Zoom in
            elif delta < 0:
                self.zoomRequested.emit(-1)  # Zoom out
            event.accept()
        else:
            super().wheelEvent(event)
    
    def mousePressEvent(self, event) -> None:
        """Start pan operation on left mouse button."""
        if event.button() == Qt.LeftButton and self.pixmap() and self.pixmap().width() > self.width():
            self.is_panning = True
            self.pan_start_pos = event.globalPos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event) -> None:
        """Handle panning when dragging."""
        if self.is_panning and self.pan_start_pos:
            delta = event.globalPos() - self.pan_start_pos
            # Find the scroll area parent and adjust scroll bars
            parent = self.parent()
            while parent:
                if isinstance(parent, QScrollArea):
                    h_bar = parent.horizontalScrollBar()
                    v_bar = parent.verticalScrollBar()
                    h_bar.setValue(h_bar.value() - delta.x())
                    v_bar.setValue(v_bar.value() - delta.y())
                    self.pan_start_pos = event.globalPos()
                    event.accept()
                    return
                parent = parent.parent()
            event.accept()
        else:
            super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event) -> None:
        """End pan operation."""
        if event.button() == Qt.LeftButton and self.is_panning:
            self.is_panning = False
            self.pan_start_pos = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


def _generate_error_svg(error_msg: str, line_num: int = 0) -> str:
    """Generate a PlantUML-style error diagram SVG."""
    # Escape HTML entities in error message
    error_display = error_msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    
    line_info = f" (line {line_num})" if line_num > 0 else ""
    
    svg = f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" contentScriptType="application/ecmascript" contentStyleType="text/css" height="400px" preserveAspectRatio="none" style="width:800px;height:400px;background:#ffeeee" version="1.1" viewBox="0 0 800 400" width="800px" zoomAndPan="magnify">
    <defs>
        <style type="text/css"><![CDATA[
            * {{ margin: 0; padding: 0; }}
            .error-title {{ font-size: 24px; font-weight: bold; fill: #cc0000; font-family: monospace; }}
            .error-message {{ font-size: 14px; fill: #333333; font-family: monospace; word-wrap: break-word; }}
            .error-line {{ font-size: 12px; fill: #666666; font-family: monospace; }}
            .error-box {{ fill: #ffe6e6; stroke: #ff9999; stroke-width: 2; }}
        ]]></style>
    </defs>
    <rect class="error-box" x="20" y="20" width="760" height="360" rx="5" ry="5"/>
    <text class="error-title" x="40" y="60">⚠ PlantUML Render Error{line_info}</text>
    <foreignObject x="40" y="90" width="720" height="280">
        <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: monospace; font-size: 13px; color: #333; white-space: pre-wrap; word-break: break-word; line-height: 1.4;">
            {error_display}
        </div>
    </foreignObject>
</svg>"""
    return svg


class PlantUMLEditorWindow(QMainWindow):
    """Non-modal editor window for PlantUML diagrams with split editor/preview."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        
        self.file_path = Path(file_path)
        self.renderer = PlantUMLRenderer()
        self.setWindowTitle(f"PlantUML Editor - {self.file_path.name}")
        self.setGeometry(100, 100, 1400, 800)
        
        # Create main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Create toolbar with zoom buttons and export
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(5, 5, 5, 5)
        
        # Editor section with Save button
        editor_section = QHBoxLayout()
        
        self.save_btn = QToolButton()
        self.save_btn.setText("💾 Save")
        self.save_btn.setToolTip("Save file (Ctrl+S or Ctrl+Enter)")
        self.save_btn.clicked.connect(self._save_file)
        editor_section.addWidget(self.save_btn)
        
        # Editor zoom controls
        editor_zoom_label = QLabel("Zoom:")
        editor_section.addWidget(editor_zoom_label)
        
        self.editor_zoom_out_btn = QToolButton()
        self.editor_zoom_out_btn.setText("−")
        self.editor_zoom_out_btn.setToolTip("Zoom out editor")
        self.editor_zoom_out_btn.clicked.connect(self._zoom_out_editor)
        editor_section.addWidget(self.editor_zoom_out_btn)
        
        self.editor_zoom_in_btn = QToolButton()
        self.editor_zoom_in_btn.setText("+")
        self.editor_zoom_in_btn.setToolTip("Zoom in editor")
        self.editor_zoom_in_btn.clicked.connect(self._zoom_in_editor)
        editor_section.addWidget(self.editor_zoom_in_btn)
        
        toolbar_layout.addLayout(editor_section)
        toolbar_layout.addStretch()
        
        # Preview section with zoom and export (right-aligned)
        preview_section = QHBoxLayout()
        
        # Render button
        self.render_btn = QToolButton()
        self.render_btn.setText("⟲ Render")
        self.render_btn.setToolTip("Render diagram (Ctrl+S)")
        self.render_btn.clicked.connect(self._render)
        preview_section.addWidget(self.render_btn)
        
        preview_section.addSpacing(10)
        
        # Preview zoom controls
        preview_zoom_label = QLabel("Zoom:")
        preview_section.addWidget(preview_zoom_label)
        
        self.preview_zoom_out_btn = QToolButton()
        self.preview_zoom_out_btn.setText("−")
        self.preview_zoom_out_btn.setToolTip("Zoom out preview")
        self.preview_zoom_out_btn.clicked.connect(self._zoom_out_preview)
        preview_section.addWidget(self.preview_zoom_out_btn)
        
        self.preview_zoom_in_btn = QToolButton()
        self.preview_zoom_in_btn.setText("+")
        self.preview_zoom_in_btn.setToolTip("Zoom in preview")
        self.preview_zoom_in_btn.clicked.connect(self._zoom_in_preview)
        preview_section.addWidget(self.preview_zoom_in_btn)
        
        preview_section.addSpacing(10)
        
        # Export button
        self.export_btn = QToolButton()
        self.export_btn.setText("↓ Export")
        self.export_btn.setToolTip("Export diagram as SVG or PNG")
        self.export_btn.clicked.connect(self._show_export_menu)
        preview_section.addWidget(self.export_btn)
        
        toolbar_layout.addLayout(preview_section)
        
        main_layout.addLayout(toolbar_layout)
        
        # Create center widget with shortcuts dropdown above split view
        center_widget = QWidget()
        center_layout = QVBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        # Shortcuts and AI server/model selection row
        shortcuts_layout = QHBoxLayout()
        shortcuts_layout.setContentsMargins(5, 5, 5, 5)
        
        shortcuts_label = QLabel("Shortcuts:")
        self.shortcuts_dropdown = QComboBox()
        self.shortcuts_dropdown.addItem("-- Select a diagram template --", None)
        self._load_shortcuts()
        self.shortcuts_dropdown.currentIndexChanged.connect(self._on_shortcut_selected)
        shortcuts_layout.addWidget(shortcuts_label)
        shortcuts_layout.addWidget(self.shortcuts_dropdown)
        
        # Add separator
        shortcuts_layout.addSpacing(20)
        
        # Server dropdown
        server_label = QLabel("Server:")
        self.ai_server_combo = QComboBox()
        self.ai_server_combo.setMaximumWidth(150)
        self.ai_server_combo.currentTextChanged.connect(self._on_ai_server_changed)
        shortcuts_layout.addWidget(server_label)
        shortcuts_layout.addWidget(self.ai_server_combo)
        
        # Model dropdown
        model_label = QLabel("Model:")
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setMaximumWidth(150)
        shortcuts_layout.addWidget(model_label)
        shortcuts_layout.addWidget(self.ai_model_combo)
        
        shortcuts_layout.addStretch()
        
        # Load servers and models
        self._load_ai_servers_models()
        
        center_layout.addLayout(shortcuts_layout)
        
        # Create vertical splitter for editor/preview and AI chat panel
        main_vertical_splitter = QSplitter(Qt.Vertical)
        
        # Top: editor + preview split
        splitter = QSplitter(Qt.Horizontal)
        
        # Left panel: PlantUML code editor (35%)
        self.editor = PlainTextEditWithLineNumbers()
        self.editor.setPlaceholderText("Enter PlantUML diagram code here...")
        self.editor.setFont(self._get_monospace_font())
        splitter.addWidget(self.editor)
        
        # Right panel: Preview (65%)
        preview_container = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setStyleSheet("QScrollArea { background-color: #f0f0f0; }")
        
        self.preview_label = ZoomablePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("QLabel { background-color: white; border: 1px solid #ccc; }")
        self.preview_label.zoomRequested.connect(self._on_preview_wheel_zoom)
        self.preview_scroll.setWidget(self.preview_label)
        
        preview_layout.addWidget(self.preview_scroll)
        preview_container.setLayout(preview_layout)
        
        # Add both to horizontal splitter
        splitter.addWidget(preview_container)
        
        # Set splitter sizes (35% editor, 65% preview)
        splitter.setSizes([490, 910])  # ~35%, ~65% of 1400px
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        
        # Connect splitter changes to update chat panel width
        self.editor_preview_splitter = splitter
        splitter.splitterMoved.connect(self._on_splitter_moved)
        
        main_vertical_splitter.addWidget(splitter)
        
        # Bottom: AI chat panel (full width)
        self.ai_panel = self._create_ai_chat_panel()
        main_vertical_splitter.addWidget(self.ai_panel)
        
        # Set initial vertical split: give editor 3 lines height (~60-70px for 3 lines)
        # Approximately 750-100 = 650 for top, 100 for chat
        main_vertical_splitter.setSizes([750, 100])
        main_vertical_splitter.setCollapsible(0, False)
        main_vertical_splitter.setCollapsible(1, True)  # Chat can be collapsed
        
        center_layout.addWidget(main_vertical_splitter)
        center_widget.setLayout(center_layout)
        main_layout.addWidget(center_widget)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        
        # Load file content
        self._load_file()
        
        # Setup debounce timer for rendering
        self.render_timer = QTimer()
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(1000)  # 1 second debounce
        self.render_timer.timeout.connect(self._render)
        
        # Now connect editor changes (after timer is created)
        self.editor.textChanged.connect(self._on_editor_changed)
        
        # Setup Ctrl+S shortcut for rendering
        QShortcut(QKeySequence.Save, self, self._render)
        
        # Setup Ctrl+Enter for save
        QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Return), self, self._save_file)
        
        # Zoom levels
        self.editor_zoom_level = 0
        self.preview_zoom_level = 0
        self.preview_pixmap: Optional[QPixmap] = None
        
        self._render()

    def _get_monospace_font(self):
        """Get a monospace font suitable for code editing."""
        from PySide6.QtGui import QFont
        font = QFont()
        font.setFamily("Courier New" if os.name == "nt" else "Courier")
        font.setPointSize(11)
        font.setFixedPitch(True)
        return font

    # --- AI prompt handling ----------------------------------------------------

    def _load_shortcuts(self) -> None:
        """Load PlantUML diagram templates from puml_shortcuts.json."""
        try:
            shortcuts_path = Path(__file__).resolve().parents[1] / "puml_shortcuts.json"
            if shortcuts_path.exists():
                with open(shortcuts_path, 'r', encoding='utf-8') as f:
                    shortcuts = json.load(f)
                    for item in shortcuts:
                        name = item.get("name", "")
                        code = item.get("sample puml", "")  # Use "sample puml" key
                        self.shortcuts_dropdown.addItem(name, code)
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Failed to load shortcuts: {exc}")

    def _on_shortcut_selected(self, index: int) -> None:
        """Insert selected shortcut template at cursor position."""
        if index <= 0:  # Skip default item
            return
        code = self.shortcuts_dropdown.itemData(index)
        if not code:
            return
        
        cursor = self.editor.textCursor()
        pos = cursor.position()
        text = self.editor.toPlainText()
        
        # If mid-line, prepend newline
        if pos > 0 and text and text[pos - 1] != '\n':
            cursor.insertText('\n')
        
        # Insert template
        cursor.insertText(code)
        cursor.insertText('\n')
        
        # Reset dropdown to default
        self.shortcuts_dropdown.setCurrentIndex(0)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Handle splitter moves (currently not needed for full-width chat panel)."""
        pass

    def _create_ai_chat_panel(self) -> QWidget:
        """Create AI chat panel with message input and send button."""
        panel = QWidget()
        panel.setStyleSheet("QWidget { background-color: #1e1e1e; border-top: 1px solid #444; } QLineEdit { background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #444; padding: 4px; }")
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Chat input field
        self.ai_input = QLineEdit()
        self.ai_input.setPlaceholderText("Describe the diagram you want to generate...")
        layout.addWidget(self.ai_input)
        
        # Send button
        self.ai_send_btn = QToolButton()
        self.ai_send_btn.setText("📤 Send")
        self.ai_send_btn.setToolTip("Send message to AI (Ctrl+Enter)")
        self.ai_send_btn.clicked.connect(self._on_ai_send)
        layout.addWidget(self.ai_send_btn)
        
        # Setup Ctrl+Enter in input to send
        QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Return), self.ai_input, self._on_ai_send)
        
        panel.setLayout(layout)
        return panel

    def _load_ai_servers_models(self) -> None:
        """Load available servers and models from configuration."""
        try:
            from zimx.app.ui.ai_chat_panel import ServerManager, get_available_models
            
            mgr = ServerManager()
            servers = mgr.load_servers()
            server_names = [srv["name"] for srv in servers]
            
            self.ai_server_combo.clear()
            self.ai_server_combo.addItems(server_names)
            
            # Set to current default or first available
            try:
                default_server = config.load_default_ai_server()
                if default_server and default_server in server_names:
                    self.ai_server_combo.setCurrentText(default_server)
                elif server_names:
                    self.ai_server_combo.setCurrentIndex(0)
            except Exception:
                if server_names:
                    self.ai_server_combo.setCurrentIndex(0)
            
            # Load models for selected server
            self._refresh_ai_models()
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Failed to load AI servers: {exc}")

    def _on_ai_server_changed(self, server_name: str) -> None:
        """Refresh models when server selection changes."""
        self._refresh_ai_models()

    def _refresh_ai_models(self) -> None:
        """Refresh available models for the selected server."""
        try:
            from zimx.app.ui.ai_chat_panel import ServerManager, get_available_models
            
            mgr = ServerManager()
            server_name = self.ai_server_combo.currentText()
            if not server_name:
                self.ai_model_combo.clear()
                return
            
            server = mgr.get_server(server_name)
            if not server:
                self.ai_model_combo.clear()
                return
            
            models = get_available_models(server)
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems(models)
            
            # Set to current default if available
            try:
                default_model = config.load_default_ai_model()
                if default_model and default_model in models:
                    self.ai_model_combo.setCurrentText(default_model)
                elif models:
                    self.ai_model_combo.setCurrentIndex(0)
            except Exception:
                if models:
                    self.ai_model_combo.setCurrentIndex(0)
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Failed to refresh AI models: {exc}")

    def _on_ai_send(self) -> None:
        """Send chat message to AI and display response."""
        user_message = self.ai_input.text().strip()
        if not user_message:
            return
        
        self.ai_input.clear()
        self.ai_input.setEnabled(False)
        self.ai_send_btn.setEnabled(False)
        
        try:
            # Get selected server and model from dropdowns
            server_name = self.ai_server_combo.currentText()
            model_name = self.ai_model_combo.currentText()
            
            if not server_name or not model_name:
                QMessageBox.warning(self, "Error", "Please select a server and model")
                self.ai_input.setEnabled(True)
                self.ai_send_btn.setEnabled(True)
                return
            
            # Get server config
            try:
                from zimx.app.ui.ai_chat_panel import ServerManager
                mgr = ServerManager()
                server_config = mgr.get_server(server_name)
                if not server_config:
                    QMessageBox.warning(self, "Error", f"Server '{server_name}' not found")
                    self.ai_input.setEnabled(True)
                    self.ai_send_btn.setEnabled(True)
                    return
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Failed to get server config: {exc}")
                self.ai_input.setEnabled(True)
                self.ai_send_btn.setEnabled(True)
                return
            
            # Load system prompt
            try:
                prompt_path = Path(__file__).resolve().parents[1] / "puml_prompt.txt"
                system_prompt = prompt_path.read_text(encoding="utf-8")
            except Exception:
                system_prompt = "You are a helpful assistant. You generate PlantUML diagrams."
            
            # Get current editor content
            editor_content = self.editor.toPlainText()
            
            # Construct messages
            user_content = f"Current diagram:\n```\n{editor_content}\n```\n\nUser request: {user_message}"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            # Launch streaming worker with selected model (NOT persisted)
            worker = ApiWorker(server_config, messages, model_name, stream=True, parent=self)
            self._ai_worker = worker
            self._ai_response_buffer = ""
            worker.chunk.connect(self._on_ai_response_chunk)
            worker.finished.connect(self._on_ai_response_finished)
            worker.failed.connect(self._on_ai_response_failed)
            worker.start()
            
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] AI send error: {exc}")
            QMessageBox.critical(self, "Error", f"AI request failed: {exc}")
            self.ai_input.setEnabled(True)
            self.ai_send_btn.setEnabled(True)

    def _on_ai_response_chunk(self, chunk: str) -> None:
        """Accumulate AI response chunks."""
        try:
            if not hasattr(self, '_ai_response_buffer'):
                self._ai_response_buffer = ""
            self._ai_response_buffer += chunk or ""
            if _LOGGING and chunk:
                print(f"[PlantUML] Received chunk: {len(chunk)} chars")
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Chunk error: {exc}")

    def _on_ai_response_finished(self, content: str) -> None:
        """Display AI response with accept/decline buttons."""
        self.ai_input.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        
        try:
            response = self._ai_response_buffer or content or ""
            if _LOGGING:
                print(f"[PlantUML] Response finished. Buffer len: {len(self._ai_response_buffer)}, Content len: {len(content)}, Final response len: {len(response)}")
            
            if not response:
                if _LOGGING:
                    print("[PlantUML] No response received")
                return
            
            # Extract PlantUML code from response (remove markdown code blocks if present)
            if "```plantuml" in response.lower():
                start = response.lower().find("```plantuml") + 11
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
            
            if _LOGGING:
                print(f"[PlantUML] Extracted response len: {len(response)}")
                print(f"[PlantUML] First 100 chars: {response[:100]}")
            
            # Show response dialog with accept/decline
            self._show_ai_response_dialog(response)
            
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Response finish error: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                self._ai_worker = None
            except Exception:
                pass

    def _on_ai_response_failed(self, message: str) -> None:
        """Handle AI error."""
        self.ai_input.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        QMessageBox.warning(self, "AI Error", f"Failed to get AI response: {message}")
        try:
            self._ai_worker = None
        except Exception:
            pass

    def _show_ai_response_dialog(self, ai_text: str) -> None:
        """Show side-by-side diff of original vs AI-generated PlantUML."""
        if _LOGGING:
            print(f"[PlantUML] Showing response dialog with {len(ai_text)} chars")
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Review AI Generated Diagram - Accept or Decline")
        dialog.setGeometry(50, 50, 1400, 800)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
            }
            QLabel {
                color: #e0e0e0;
            }
            QPushButton {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #444;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
            }
        """)
        
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("Review Changes - Left: Current | Right: AI Generated")
        title.setStyleSheet("color: #e0e0e0; font-weight: bold; font-size: 12px; padding: 5px;")
        layout.addWidget(title)
        
        # Side-by-side diff comparison
        diff_layout = QHBoxLayout()
        
        # Left panel: Original
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("Current PlantUML")
        left_label.setStyleSheet("color: #e0e0e0; font-weight: bold;")
        left_layout.addWidget(left_label)
        
        original_text = self.editor.toPlainText()
        left_display = self._create_diff_display(original_text, ai_text, is_original=True)
        left_layout.addWidget(left_display)
        left_panel.setLayout(left_layout)
        
        # Right panel: AI Generated
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("AI Generated PlantUML")
        right_label.setStyleSheet("color: #e0e0e0; font-weight: bold;")
        right_layout.addWidget(right_label)
        
        right_display = self._create_diff_display(original_text, ai_text, is_original=False)
        right_layout.addWidget(right_display)
        right_panel.setLayout(right_layout)
        
        # Add both panels
        diff_layout.addWidget(left_panel, stretch=1)
        diff_layout.addWidget(right_panel, stretch=1)
        layout.addLayout(diff_layout, stretch=1)
        
        # Accept/Decline buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        accept_btn = QPushButton("✓ Accept Changes")
        accept_btn.setMinimumWidth(150)
        accept_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e5c1e;
                color: #90ee90;
                border: 1px solid #4caf50;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2d7a2d;
            }
        """)
        accept_btn.clicked.connect(lambda: self._accept_ai_response(ai_text, dialog))
        button_layout.addWidget(accept_btn)
        
        decline_btn = QPushButton("✗ Decline")
        decline_btn.setMinimumWidth(150)
        decline_btn.setStyleSheet("""
            QPushButton {
                background-color: #5c1e1e;
                color: #ff6b6b;
                border: 1px solid #f44336;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #7a2d2d;
            }
        """)
        decline_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(decline_btn)
        
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        
        if _LOGGING:
            print("[PlantUML] Displaying dialog...")
        
        # Keep a reference to prevent garbage collection
        self._active_diff_dialog = dialog
        
        # Show the dialog
        dialog.exec()

    def _create_diff_display(self, original: str, modified: str, is_original: bool) -> QTextEdit:
        """Create a text display with diff highlighting."""
        display = QTextEdit()
        display.setReadOnly(True)
        display.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #444;
                padding: 5px;
                font-family: Courier, monospace;
                font-size: 10px;
            }
        """)
        
        if is_original:
            # Show original text with removed lines highlighted in red
            display.setPlainText(original)
            self._highlight_diff_lines(display, original, modified, is_added=False)
        else:
            # Show modified text with added lines highlighted in green
            display.setPlainText(modified)
            self._highlight_diff_lines(display, original, modified, is_added=True)
        
        return display

    def _highlight_diff_lines(self, text_edit: QTextEdit, original: str, modified: str, is_added: bool) -> None:
        """Highlight added/removed/changed lines in the text edit."""
        from PySide6.QtGui import QTextCharFormat, QTextBlockFormat, QBrush
        from PySide6.QtGui import QTextCursor as QtgQTextCursor
        
        original_lines = original.splitlines()
        modified_lines = modified.splitlines()
        
        cursor = text_edit.textCursor()
        cursor.movePosition(QtgQTextCursor.MoveOperation.Start)
        
        if is_added:
            # Show modified version with additions highlighted
            for i, line in enumerate(modified_lines):
                if i < len(original_lines):
                    if line != original_lines[i]:
                        # Changed line - orange/yellow
                        fmt = QTextCharFormat()
                        fmt.setBackground(QBrush(QColor(100, 80, 0)))  # Dark orange
                        cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                        cursor.setCharFormat(fmt)
                else:
                    # New line - green
                    fmt = QTextCharFormat()
                    fmt.setBackground(QBrush(QColor(0, 80, 0)))  # Dark green
                    cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt)
                
                # Move to next line
                if not cursor.movePosition(QtgQTextCursor.MoveOperation.Down):
                    break
        else:
            # Show original version with removals highlighted
            for i, line in enumerate(original_lines):
                if i < len(modified_lines):
                    if line != modified_lines[i]:
                        # Changed line - orange/yellow
                        fmt = QTextCharFormat()
                        fmt.setBackground(QBrush(QColor(100, 80, 0)))  # Dark orange
                        cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                        cursor.setCharFormat(fmt)
                else:
                    # Removed line - red
                    fmt = QTextCharFormat()
                    fmt.setBackground(QBrush(QColor(80, 0, 0)))  # Dark red
                    cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt)
                
                # Move to next line
                if not cursor.movePosition(QtgQTextCursor.MoveOperation.Down):
                    break

    def _accept_ai_response(self, ai_text: str, dialog: QDialog) -> None:
        """Accept AI response: replace buffer, save, render."""
        try:
            # Replace entire editor buffer
            self.editor.setPlainText(ai_text)
            
            # Save file
            self._save_file()
            
            # Render diagram
            self._render()
            
            dialog.accept()
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Accept error: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to accept response: {exc}")

    def _resolve_ai_server_and_model(self) -> Optional[tuple[dict, str]]:
        """Resolve AI server and model configuration."""
        try:
            server_mgr = ServerManager()
        except Exception:
            return None
        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        if default_server_name:
            try:
                server_config = server_mgr.get_server(default_server_name) or {}
            except Exception:
                server_config = {}
        if not server_config:
            try:
                active = server_mgr.get_active_server_name()
                if active:
                    server_config = server_mgr.get_server(active) or {}
            except Exception:
                server_config = {}
        if not server_config:
            try:
                servers = server_mgr.load_servers()
                if servers:
                    server_config = servers[0]
            except Exception:
                server_config = {}
        if not server_config:
            return None
        try:
            model = config.load_default_ai_model()
        except Exception:
            model = None
        if not model:
            model = server_config.get("default_model") or "gpt-3.5-turbo"
        return server_config, model

    def _load_file(self) -> None:
        """Load PlantUML file content into editor."""
        if self.file_path.exists():
            try:
                content = self.file_path.read_text(encoding="utf-8")
                self.editor.setPlainText(content)
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Failed to load file: {exc}")
        else:
            # New file
            template = """@startuml
' New PlantUML diagram

@enduml
"""
            self.editor.setPlainText(template)

    def _save_file(self) -> None:
        """Save editor content to file."""
        try:
            content = self.editor.toPlainText()
            self.file_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save file: {exc}")

    def _on_editor_changed(self) -> None:
        """Debounce rendering when editor text changes."""
        self.render_timer.start()

    def _render(self) -> None:
        """Render the PlantUML diagram."""
        self.render_timer.stop()
        
        # Check if window still exists
        try:
            if not self or not hasattr(self, 'editor'):
                return
            test_attr = self.editor  # Test if object is still valid
        except RuntimeError:
            return
        
        puml_text = self.editor.toPlainText()
        
        try:
            result = self.renderer.render_svg(puml_text)
        except Exception as exc:
            print(f"[PlantUML Editor] Render exception: {exc}", file=__import__('sys').stdout, flush=True)
            return
        
        # Check again after async operation
        try:
            if not self or not hasattr(self, 'preview_label'):
                return
            test_attr = self.preview_label  # Test if object is still valid
        except RuntimeError:
            return
        
        try:
            if result.success and result.svg_content:
                # Store SVG for export/copy
                self._last_svg = result.svg_content
                
                # Convert SVG to image for display
                try:
                    self.preview_pixmap = self._svg_to_pixmap(result.svg_content)
                    if self.preview_pixmap:
                        self._update_preview_display()
                        self.render_btn.setText("✓ Render")
                    else:
                        # Show error diagram instead of text
                        error_svg = _generate_error_svg("Failed to convert SVG to image")
                        self._last_svg = error_svg
                        self.preview_pixmap = self._svg_to_pixmap(error_svg)
                        if self.preview_pixmap:
                            self._update_preview_display()
                        self.render_btn.setText("✗ Render")
                except Exception as svg_exc:
                    print(f"[PlantUML Editor] SVG error: {svg_exc}", file=__import__('sys').stdout, flush=True)
                    # Show error diagram
                    error_svg = _generate_error_svg(f"SVG rendering error:\n{str(svg_exc)}")
                    self._last_svg = error_svg
                    try:
                        self.preview_pixmap = self._svg_to_pixmap(error_svg)
                        if self.preview_pixmap:
                            self._update_preview_display()
                    except RuntimeError:
                        pass
                    self.render_btn.setText("✗ Render")
            else:
                error_msg = result.error_message or result.stderr or "Unknown error"
                # Extract line number if present (e.g., "line 5: Error description")
                line_num = 0
                try:
                    if "line" in error_msg.lower():
                        import re
                        match = re.search(r'line\s+(\d+)', error_msg.lower())
                        if match:
                            line_num = int(match.group(1))
                except Exception:
                    pass
                
                # Show error diagram with stderr content
                error_display = f"PlantUML Error\n\n{error_msg}"
                if result.stderr and result.stderr != error_msg:
                    error_display += f"\n\nDetails:\n{result.stderr[:500]}"
                
                error_svg = _generate_error_svg(error_display, line_num)
                self._last_svg = error_svg
                try:
                    self.preview_pixmap = self._svg_to_pixmap(error_svg)
                    if self.preview_pixmap:
                        self._update_preview_display()
                except RuntimeError:
                    pass
                self.render_btn.setText("✗ Render")
        except Exception as exc:
            print(f"[PlantUML Editor] Render error: {exc}", file=__import__('sys').stdout, flush=True)
            error_svg = _generate_error_svg(f"Internal error:\n{str(exc)}")
            self._last_svg = error_svg
            try:
                self.preview_pixmap = self._svg_to_pixmap(error_svg)
                if self.preview_pixmap:
                    self._update_preview_display()
            except RuntimeError:
                pass
            self.render_btn.setText("✗ Render")

    def _svg_to_pixmap(self, svg_content: str) -> Optional[QPixmap]:
        """Convert SVG string to QPixmap."""
        try:
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtCore import QByteArray
            
            svg_bytes = QByteArray(svg_content.encode("utf-8"))
            renderer = QSvgRenderer(svg_bytes)
            
            if not renderer.isValid():
                return None
            
            size = renderer.defaultSize()
            if not size.isValid():
                size = QSize(800, 600)
            
            pixmap = QPixmap(size)
            pixmap.fill(Qt.white)
            
            from PySide6.QtGui import QPainter
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            
            return pixmap
        except Exception:
            return None

    def _update_preview_display(self) -> None:
        """Update the preview label with current pixmap and zoom level."""
        # Check if object still exists
        try:
            if not self or not hasattr(self, 'preview_label') or not self.preview_pixmap:
                return
            test_attr = self.preview_label  # Test if object is still valid
        except RuntimeError:
            return
        
        # Apply zoom
        zoom_factor = 1.0 + (self.preview_zoom_level * 0.1)
        size = self.preview_pixmap.size()
        new_size = QSize(int(size.width() * zoom_factor), int(size.height() * zoom_factor))
        
        scaled_pixmap = self.preview_pixmap.scaledToWidth(
            new_size.width(),
            Qt.SmoothTransformation
        )
        
        try:
            self.preview_label.setPixmap(scaled_pixmap)
        except RuntimeError:
            pass  # Widget was deleted, ignore

    def _zoom_in_editor(self) -> None:
        """Zoom in editor text."""
        self.editor_zoom_level += 1
        font = self.editor.font()
        font.setPointSize(11 + self.editor_zoom_level)
        self.editor.setFont(font)

    def _zoom_out_editor(self) -> None:
        """Zoom out editor text."""
        if self.editor_zoom_level > -5:  # Min size 6pt
            self.editor_zoom_level -= 1
            font = self.editor.font()
            font.setPointSize(11 + self.editor_zoom_level)
            self.editor.setFont(font)

    def _zoom_in_preview(self) -> None:
        """Zoom in preview image."""
        self.preview_zoom_level += 1
        try:
            self._update_preview_display()
        except RuntimeError:
            pass

    def _zoom_out_preview(self) -> None:
        """Zoom out preview image."""
        if self.preview_zoom_level > -10:  # Min zoom
            self.preview_zoom_level -= 1
            try:
                self._update_preview_display()
            except RuntimeError:
                pass

    def _on_preview_wheel_zoom(self, delta: int) -> None:
        """Handle Ctrl+MouseWheel zoom on preview."""
        if delta > 0:
            self._zoom_in_preview()
        else:
            self._zoom_out_preview()

    def _show_export_menu(self) -> None:
        """Show export options menu."""
        menu = QMenu(self)
        
        export_svg = menu.addAction("Export as SVG...")
        export_svg.triggered.connect(self._export_svg)
        
        export_png = menu.addAction("Export as PNG...")
        export_png.triggered.connect(self._export_png)
        
        menu.addSeparator()
        
        copy_svg = menu.addAction("Copy SVG")
        copy_svg.triggered.connect(self._copy_svg)
        
        copy_png = menu.addAction("Copy PNG")
        copy_png.triggered.connect(self._copy_png)
        
        menu.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def _export_svg(self) -> None:
        """Export diagram as SVG file."""
        if not hasattr(self, '_last_svg'):
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as SVG",
            str(self.file_path.with_suffix(".svg")),
            "SVG Files (*.svg)"
        )
        
        if file_path:
            try:
                Path(file_path).write_text(self._last_svg, encoding="utf-8")
                QMessageBox.information(self, "Exported", f"Saved to {file_path}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Failed to export: {exc}")

    def _export_png(self) -> None:
        """Export diagram as PNG file."""
        if not self.preview_pixmap:
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as PNG",
            str(self.file_path.with_suffix(".png")),
            "PNG Files (*.png)"
        )
        
        if file_path:
            try:
                self.preview_pixmap.save(file_path, "PNG")
                QMessageBox.information(self, "Exported", f"Saved to {file_path}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Failed to export: {exc}")

    def _copy_svg(self) -> None:
        """Copy SVG to clipboard."""
        if not hasattr(self, '_last_svg'):
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        clipboard = QApplication.clipboard()
        clipboard.setText(self._last_svg)
        QMessageBox.information(self, "Copied", "SVG copied to clipboard")

    def _copy_png(self) -> None:
        """Copy PNG to clipboard."""
        if not self.preview_pixmap:
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        clipboard = QApplication.clipboard()
        clipboard.setPixmap(self.preview_pixmap)
        QMessageBox.information(self, "Copied", "PNG copied to clipboard")

    def closeEvent(self, event) -> None:
        """Save file before closing."""
        self._save_file()
        super().closeEvent(event)
