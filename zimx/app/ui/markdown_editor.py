from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional, Callable
from html.parser import HTMLParser

from PySide6.QtCore import QEvent, QMimeData, Qt, QRegularExpression, Signal, QUrl, QPoint, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QTextCharFormat,
    QTextCursor,
    QSyntaxHighlighter,
    QTextImageFormat,
    QTextFormat,
    QDesktopServices,
    QGuiApplication,
    QPainter,
    QPen,
    QKeyEvent,
    QKeySequence,
    QTextDocument,
    QShortcut,
    QAction,
)
from PySide6.QtWidgets import (
    QTextEdit,
    QMenu,
    QInputDialog,
    QDialog,
    QApplication,
    QWidget,
    QVBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
)
from shiboken6 import Shiboken
from .path_utils import path_to_colon, colon_to_path, ensure_root_colon_link
from .heading_utils import heading_slug
from .page_load_logger import PageLoadLogger
from .ai_actions_data import AI_ACTION_GROUPS
from zimx.app import config


logger = logging.getLogger(__name__)


class SearchEngine:
    """Lightweight search/replace helper bound to a MarkdownEditor."""

    def __init__(self, editor: "MarkdownEditor") -> None:
        self.editor = editor
        self.last_query: str = ""
        self.last_replacement: str = ""
        self.last_forward: bool = True
        self.last_case_sensitive: bool = False

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\u2029", "\n")

    def find_next(
        self,
        query: str,
        *,
        backwards: bool = False,
        wrap: bool = True,
        case_sensitive: bool = False,
    ) -> tuple[bool, bool]:
        query = query or self.last_query
        if not query:
            return False, False
        doc = self.editor.document()
        cursor = QTextCursor(self.editor.textCursor())
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindBackward
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        match = doc.find(query, cursor, flags)
        wrapped = False
        if match.isNull() and wrap:
            restart = QTextCursor(doc.end() if backwards else doc.begin())
            match = doc.find(query, restart, flags)
            wrapped = not match.isNull()
        if match.isNull():
            return False, False
        self.editor.setTextCursor(match)
        self.editor.ensureCursorVisible()
        self.last_query = query
        self.last_forward = not backwards
        self.last_case_sensitive = case_sensitive
        return True, wrapped

    def repeat_last(self, reverse: bool = False) -> tuple[bool, bool, bool]:
        if not self.last_query:
            return False, False, False
        backwards = not self.last_forward
        if reverse:
            backwards = not backwards
        found, wrapped = self.find_next(
            self.last_query,
            backwards=backwards,
            wrap=True,
            case_sensitive=self.last_case_sensitive,
        )
        return found, wrapped, backwards

    def replace_current(self, replacement: str) -> bool:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return False
        selected = self._normalize_text(cursor.selectedText())
        if self.last_query and selected != self.last_query:
            return False
        cursor.beginEditBlock()
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.last_replacement = replacement
        return True

    def replace_all(self, query: str, replacement: str, *, case_sensitive: bool = False) -> int:
        query = query or self.last_query
        if not query:
            return 0
        doc = self.editor.document()
        edit_cursor = QTextCursor(doc)
        cursor = QTextCursor(doc)
        original_cursor = QTextCursor(self.editor.textCursor())
        flags = QTextDocument.FindFlag(0)
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        count = 0
        edit_cursor.beginEditBlock()
        while True:
            match = doc.find(query, cursor, flags)
            if match.isNull():
                break
            match.insertText(replacement)
            # Continue searching after the replacement to avoid re-visiting the same span
            cursor = QTextCursor(doc)
            cursor.setPosition(match.selectionStart() + len(replacement))
            count += 1
        edit_cursor.endEditBlock()
        if count:
            self.last_query = query
            self.last_replacement = replacement
            self.last_forward = True
            self.last_case_sensitive = case_sensitive
            try:
                self.editor.setTextCursor(original_cursor)
            except Exception:
                pass
        return count


TAG_PATTERN = QRegularExpression(r"@(\w+)")
TASK_PATTERN = QRegularExpression(r"^(?P<indent>\s*)\((?P<state>[xX ])?\)(?P<body>\s+.*)$")
TASK_LINE_PATTERN = re.compile(r"^(\s*)\(([ xX])\)(\s+)", re.MULTILINE)
DISPLAY_TASK_PATTERN = re.compile(r"^(\s*)([☐☑])(\s+)", re.MULTILINE)
# Bullet patterns for storage and display
BULLET_STORAGE_PATTERN = re.compile(r"^(\s*)\* ", re.MULTILINE)
BULLET_DISPLAY_PATTERN = re.compile(r"^(\s*)• ", re.MULTILINE)
# Plus-prefixed link pattern: +PageName or +Projects (CamelCase style, no trailing spaces)
CAMEL_LINK_PATTERN = QRegularExpression(r"\+(?P<link>[A-Za-z][\w]*)")

# CamelCase link pattern: +PageName (deprecated but kept for compatibility)
CAMEL_LINK_PATTERN = QRegularExpression(r"\+(?P<link>[A-Za-z][\w]*)")

# File link pattern for attachments: [text](./file.ext) or [text](file.ext)
WIKI_FILE_LINK_PATTERN = QRegularExpression(
    r"\[(?P<text>[^\]]+)\]\s*\((?P<file>(?:\./)?[^)\n]+\.[A-Za-z0-9]{1,8})\)"
)

# Unified wiki-style link format: [link|label]
# Works for both HTTP URLs and page links (colon notation)
# Plain HTTP URL pattern (for highlighting plain URLs without labels)
HTTP_URL_PATTERN = QRegularExpression(r"(?P<url>https?://[^\s<>\"{}|\\^`\[\]]+)")
# Plain colon link pattern (for highlighting plain colon links without labels)
COLON_LINK_PATTERN = QRegularExpression(r"(?P<link>:[^\s\[\]]+(?:#[^\s\[\]]+)?)")

# Unified wiki-style link storage format: [link|label]
# Matches both HTTP and page links (label can be empty)
WIKI_LINK_STORAGE_PATTERN = re.compile(
    r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]*)\]",
    re.MULTILINE
)

# Handles a rare duplication bug where a wiki link tail is re-appended after decoding,
# e.g. [link|label]tail|label] where tail is a suffix of link.
WIKI_LINK_DUPLICATE_TAIL_PATTERN = re.compile(
    r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]*)\](?P<tail>[^\s\]]+)\|\s*(?P=label)\]"
)

# Display pattern for rendered links (sentinel + link + sentinel + label + sentinel)
# Uses \x00 sentinel for all links (both HTTP and page links)
WIKI_LINK_DISPLAY_PATTERN = re.compile(r"\x00(?P<link>[^\x00\n]+)\x00(?P<label>[^\x00\n]*)\x00")

TABLE_ROW_PATTERN = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_PATTERN = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")

HEADING_MAX_LEVEL = 5


class AIActionOverlay(QWidget):
    """Overlay widget showing AI action categories and actions."""

    actionTriggered = Signal(str, str)
    startChat = Signal()
    loadChat = Signal()
    sendSelection = Signal()
    closed = Signal()

    class Entry:
        def __init__(self, label: str, kind: str, group=None, action=None):
            self.label = label
            self.kind = kind
            self.group = group
            self.action = action

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""
        self._current_group = None
        self._entries: list[AIActionOverlay.Entry] = []
        self._groups = AI_ACTION_GROUPS
        self._has_chat = False
        self._chat_active = False
        self.setStyleSheet("background: #000000; color: white; border-radius: 10px; border: 1px solid #222222;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        self._selection_label = QLabel()
        self._selection_label.setStyleSheet("color: #dfe6fa; font-size: 16px;")
        self._selection_label.setVisible(False)
        layout.addWidget(self._selection_label)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Send Selection to AI Chat or type action…")
        self._search.setStyleSheet(
            "font-size: 18px; color: white; background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.5); padding: 8px; border-radius: 6px;"
        )
        self._search.textChanged.connect(self._refresh_list)
        layout.addWidget(self._search)
        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.setStyleSheet("font-size: 18px; color: white; background: transparent; padding: 4px;")
        self._list.itemActivated.connect(self._activate_current_item)
        self._list.itemClicked.connect(lambda *_: self._activate_current_item())
        layout.addWidget(self._list)
        self._list.setMinimumHeight(220)
        self._search.installEventFilter(self)
        self._update_header()

    def text(self) -> str:
        return self._text

    def set_chat_state(self, *, has_chat: bool, chat_active: bool) -> None:
        self._has_chat = bool(has_chat)
        self._chat_active = bool(chat_active)

    def open(self, text: str, *, has_chat: Optional[bool] = None, chat_active: Optional[bool] = None, anchor: Optional[QPoint] = None) -> None:
        if not text:
            return
        if has_chat is not None:
            self._has_chat = bool(has_chat)
        if chat_active is not None:
            self._chat_active = bool(chat_active)
        self._text = text
        self._current_group = None
        self._update_entries()
        self._search.clear()
        self._search.setFocus()
        parent = self.parent()
        if parent:
            geo = parent.rect()
            width = min(max(420, geo.width() - 80), geo.width())
            height = min(280, max(200, geo.height() - 100))
            if anchor:
                screen_geo = QGuiApplication.primaryScreen().availableGeometry()
                left = max(screen_geo.left(), min(anchor.x() - width // 2, screen_geo.right() - width))
                top = max(screen_geo.top(), min(anchor.y() - height // 2, screen_geo.bottom() - height))
            else:
                center = parent.mapToGlobal(geo.center())
                left = center.x() - width // 2
                top = center.y() - height // 2
            self.setGeometry(left, top, width, height)
        self._update_header()
        self.show()
        self.raise_()
        self._refresh_list()
        if self._list.count():
            self._list.setCurrentRow(0)

    def _update_entries(self) -> None:
        self._entries = []
        if not self._current_group:
            if not self._has_chat:
                self._entries.append(AIActionOverlay.Entry("Start AI chat with this page", "start_chat"))
            elif self._has_chat and not self._chat_active:
                self._entries.append(AIActionOverlay.Entry("Load the chat for this page", "load_chat"))
            self._entries.append(AIActionOverlay.Entry("Send Selection to AI Chat", "default"))
            # One-shot prompt: send selected text directly to the configured model
            # and replace the selection with the model response (does not add to chat history).
            self._entries.append(AIActionOverlay.Entry("One-Shot Prompt Selection", "one_shot"))
            for group in self._groups:
                label = f"{group.title}..."
                self._entries.append(AIActionOverlay.Entry(label, "group", group=group))
        else:
            for action in self._current_group.actions:
                self._entries.append(AIActionOverlay.Entry(action.title, "action", action=action))
        self._update_header()

    def _update_header(self) -> None:
        if self._current_group:
            self._selection_label.setText(f"{self._current_group.title}")
            self._selection_label.setVisible(True)
            self._search.setPlaceholderText(f"Type an action in {self._current_group.title}…")
        else:
            self._selection_label.setVisible(False)
            self._search.setPlaceholderText("Send selection, choose an action, or type a custom prompt…")

    def _refresh_list(self) -> None:
        raw_text = self._search.text()
        search_text = raw_text.lower().strip()
        custom_prompt = raw_text.strip()
        self._list.clear()
        matches = 0
        for entry in self._entries:
            if search_text and search_text not in entry.label.lower():
                continue
            item = QListWidgetItem(entry.label)
            item.setData(Qt.UserRole, entry)
            self._list.addItem(item)
            matches += 1
        if custom_prompt:
            custom_entry = AIActionOverlay.Entry(custom_prompt, "custom_prompt")
            custom_item = QListWidgetItem(f"Use custom prompt: {custom_prompt}")
            custom_item.setData(Qt.UserRole, custom_entry)
            self._list.addItem(custom_item)
        count = self._list.count()
        if count:
            self._list.setCurrentRow(0 if matches else count - 1)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj == self._search and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                delta = 1 if event.key() == Qt.Key_Down else -1
                self._move_selection(delta)
                return True
            if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                if event.key() == Qt.Key_J:
                    self._move_selection(1)
                    return True
                if event.key() == Qt.Key_K:
                    self._move_selection(-1)
                    return True
            if event.key() == Qt.Key_Right:
                current = self._list.currentItem()
                if current:
                    entry = current.data(Qt.UserRole)
                    if entry and entry.kind == "group":
                        self._current_group = entry.group
                        self._update_entries()
                        self._refresh_list()
                        return True
            if event.key() in (Qt.Key_Left, Qt.Key_Backspace) and not self._search.text():
                if self._current_group:
                    self._current_group = None
                    self._update_entries()
                    self._refresh_list()
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Space):
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._activate_current_item()
                    return True
        return super().eventFilter(obj, event)

    def _move_selection(self, delta: int) -> None:
        count = self._list.count()
        if not count:
            return
        row = self._list.currentRow()
        row = max(0, min(count - 1, row + delta))
        self._list.setCurrentRow(row)

    def _activate_current_item(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        if entry.kind == "default":
            self.sendSelection.emit()
            self.hide()
        elif entry.kind == "start_chat":
            self.startChat.emit()
            self.hide()
        elif entry.kind == "load_chat":
            self.loadChat.emit()
            self.hide()
        elif entry.kind == "group":
            self._current_group = entry.group
            self._update_entries()
            self._refresh_list()
        elif entry.kind == "action":
            self.actionTriggered.emit(entry.action.title, entry.action.prompt)
            self.hide()
        elif entry.kind == "one_shot":
            # Signal a one-shot action; main window will perform a direct API call
            self.actionTriggered.emit("One-Shot Prompt Selection", "")
            self.hide()
        elif entry.kind == "custom_prompt":
            self.actionTriggered.emit("Custom Prompt", entry.label)
            self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_current_item()
            return
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        if event.key() == Qt.Key_Backspace and not self._search.text() and self._current_group:
            self._current_group = None
            self._update_entries()
            self._refresh_list()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.hide()
        super().focusOutEvent(event)

    def hide(self) -> None:  # type: ignore[override]
        super().hide()
        self.closed.emit()

    def is_visible(self) -> bool:
        """Helper so parents can detect when the overlay is open."""
        return self.isVisible()
HEADING_SENTINEL_BASE = 0xE000
HEADING_MARK_PATTERN = re.compile(r"^(\s*)(#{1,5})(\s+)(.+)$", re.MULTILINE)
HEADING_SENTINEL_CHARS = "".join(chr(HEADING_SENTINEL_BASE + lvl) for lvl in range(1, HEADING_MAX_LEVEL + 1))
HEADING_DISPLAY_PATTERN = re.compile(rf"^(\s*)([{HEADING_SENTINEL_CHARS}])(.*)$", re.MULTILINE)
IMAGE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)\)(?:\{width=(?P<width>\d+)\})?", re.MULTILINE
)

IMAGE_PROP_ALT = int(QTextFormat.UserProperty)
IMAGE_PROP_ORIGINAL = IMAGE_PROP_ALT + 1
IMAGE_PROP_WIDTH = IMAGE_PROP_ALT + 2
IMAGE_PROP_NATURAL_WIDTH = IMAGE_PROP_ALT + 3
IMAGE_PROP_NATURAL_HEIGHT = IMAGE_PROP_ALT + 4

_DETAILED_LOGGING = os.getenv("ZIMX_DETAILED_LOGGING", "0") not in ("0", "false", "False", "", None)


def heading_sentinel(level: int) -> str:
    level = max(1, min(HEADING_MAX_LEVEL, level))
    return chr(HEADING_SENTINEL_BASE + level)


def heading_level_from_char(char: str) -> int:
    if not char:
        return 0
    code = ord(char) - HEADING_SENTINEL_BASE
    if 1 <= code <= HEADING_MAX_LEVEL:
        return code
    return 0

class MarkdownHighlighter(QSyntaxHighlighter):
    CODE_BLOCK_STATE = 1

    def __init__(self, parent) -> None:  # type: ignore[override]
        super().__init__(parent)
        # Precompile regex patterns (avoid per-block construction)
        self._code_pattern = QRegularExpression(r"`[^`]+`")
        self._bold_italic_pattern = QRegularExpression(r"\*\*\*([^*]+)\*\*\*")
        self._bold_pattern = QRegularExpression(r"\*\*([^*]+)\*\*")
        self._italic_pattern = QRegularExpression(r"(?<!\*)\*([^*]+)\*(?!\*)")
        self._strikethrough_pattern = QRegularExpression(r"~~([^~]+)~~")
        self._highlight_pattern = QRegularExpression(r"==([^=]+)==")
        # Timing instrumentation
        self._timing_enabled = False
        self._timing_total = 0.0
        self._timing_blocks = 0
        self.heading_format = QTextCharFormat()
        self.heading_format.setForeground(QColor("#6cb4ff"))
        self.heading_format.setFontWeight(QFont.Weight.DemiBold)
        self.hidden_format = QTextCharFormat()
        transparent = QColor(0, 0, 0, 0)
        self.hidden_format.setForeground(transparent)
        # Tiny size so hidden sentinels don't create visible gaps
        self.hidden_format.setFontPointSize(0.01)

        self.heading_styles = []
        for size in (26, 22, 18, 16, 14):
            fmt = QTextCharFormat(self.heading_format)
            fmt.setFontPointSize(size)
            self.heading_styles.append(fmt)

        self.bold_format = QTextCharFormat()
        self.bold_format.setForeground(QColor("#ffd479"))

        self.italic_format = QTextCharFormat()
        self.italic_format.setForeground(QColor("#ffa7c4"))

        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if mono_font.family():
            mono_family = mono_font.family()
        else:
            mono_family = "Courier New"
        self.code_format = QTextCharFormat()
        self.code_format.setForeground(QColor("#a3ffab"))
        self.code_format.setBackground(QColor("#2a2a2a"))
        self.code_format.setFontFamily(mono_family)
        self.code_format.setFontFixedPitch(True)
        self.code_format.setFontStyleHint(QFont.StyleHint.Monospace)

        self.quote_format = QTextCharFormat()
        self.quote_format.setForeground(QColor("#7fdbff"))
        self.quote_format.setFontItalic(True)

        self.list_format = QTextCharFormat()
        self.list_format.setForeground(QColor("#ffffff"))

        self.code_block = QTextCharFormat()
        self.code_block.setBackground(QColor("#2a2a2a"))
        self.code_block.setForeground(QColor("#a3ffab"))
        self.code_block.setFontFamily(mono_family)
        self.code_block.setFontFixedPitch(True)
        self.code_block.setFontStyleHint(QFont.StyleHint.Monospace)
        
        self.code_fence_format = QTextCharFormat()
        self.code_fence_format.setForeground(QColor("#555555"))

        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor("#ffa657"))

        self.checkbox_format = QTextCharFormat()
        self.checkbox_format.setForeground(QColor("#c8c8c8"))
        self.checkbox_format.setFontFamily("Segoe UI Symbol")

        self.hr_format = QTextCharFormat()
        self.hr_format.setForeground(QColor("#555555"))
        self.hr_format.setBackground(QColor("#333333"))

        self.table_format = QTextCharFormat()
        self.table_format.setFontFamily(mono_family)
        self.table_format.setFontFixedPitch(True)
        self.table_format.setFontStyleHint(QFont.StyleHint.Monospace)
        try:
            base_pt = parent.document().defaultFont().pointSizeF()
            if base_pt <= 0:
                base_pt = parent.document().defaultFont().pointSize()
            if base_pt <= 0:
                base_pt = 14
        except Exception:
            base_pt = 14
        self._table_font_size = max(6.0, base_pt - 2.0)
        self.table_format.setFontPointSize(self._table_font_size)
        
        # Strikethrough format
        self.strikethrough_format = QTextCharFormat()
        self.strikethrough_format.setForeground(QColor("#888888"))
        self.strikethrough_format.setFontStrikeOut(True)
        
        # Highlight format
        self.highlight_format = QTextCharFormat()
        self.highlight_format.setBackground(QColor("#ffff00"))
        self.highlight_format.setForeground(QColor("#000000"))
        
        # Bold+Italic combined format
        self.bold_italic_format = QTextCharFormat()
        self.bold_italic_format.setForeground(QColor("#ffb8d1"))
        self.bold_italic_format.setFontWeight(QFont.Weight.Bold)
        self.bold_italic_format.setFontItalic(True)

        self._reset_code_block_cache()
        self._init_pygments(config.load_pygments_style("monokai"))

    def _reset_code_block_cache(self) -> None:
        self._code_block_spans: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}
        self._active_code_lang: Optional[str] = None

    def _init_pygments(self, style_name: Optional[str] = None) -> None:
        self._pygments_enabled = False
        try:
            from pygments.formatters.html import HtmlFormatter
            from pygments.lexers import get_lexer_by_name, TextLexer
            from pygments import lex
        except Exception as exc:
            logger.warning("Pygments unavailable; code fences stay monospace only: %s", exc)
            return

        self._pygments_enabled = True
        chosen_style = style_name or "monokai"
        self._pygments_style_name = chosen_style
        self._pygments_lex = lex
        try:
            self._pygments_formatter = HtmlFormatter(style=chosen_style)
        except Exception:
            self._pygments_formatter = HtmlFormatter(style="monokai")
            self._pygments_style_name = "monokai"
        self._pygments_get_lexer = get_lexer_by_name
        self._pygments_text_lexer = TextLexer
        self._pygments_format_cache: dict[str, QTextCharFormat] = {}
        self._pygments_lexer_cache: dict[str, object] = {}

    def set_pygments_style(self, style_name: str) -> None:
        """Update the Pygments style and rehighlight."""
        self._init_pygments(style_name)
        self._reset_code_block_cache()
        try:
            self.rehighlight()
        except Exception:
            pass

    def _extract_fence_language(self, text: str) -> Optional[str]:
        if not text.startswith("```"):
            return None
        lang = text[3:].strip()
        return lang or None

    def _lexer_for_language(self, lang: Optional[str]):
        if not self._pygments_enabled:
            return None
        cache_key = (lang or "").lower()
        if cache_key in self._pygments_lexer_cache:
            return self._pygments_lexer_cache[cache_key]
        try:
            lexer = self._pygments_get_lexer(cache_key) if cache_key else self._pygments_text_lexer()
        except Exception:
            lexer = self._pygments_text_lexer()
        self._pygments_lexer_cache[cache_key] = lexer
        return lexer

    def _format_for_token(self, token) -> QTextCharFormat:
        if not self._pygments_enabled:
            return self.code_block

        key = str(token)
        fmt = self._pygments_format_cache.get(key)
        if fmt:
            return fmt

        style = self._pygments_formatter.style.style_for_token(token)
        fmt = QTextCharFormat(self.code_block)
        if style.get("color"):
            fmt.setForeground(QColor(f"#{style['color']}"))
        if style.get("bgcolor"):
            fmt.setBackground(QColor(f"#{style['bgcolor']}"))
        if style.get("bold"):
            fmt.setFontWeight(QFont.Weight.Bold)
        if style.get("italic"):
            fmt.setFontItalic(True)
        if style.get("underline"):
            fmt.setFontUnderline(True)
        self._pygments_format_cache[key] = fmt
        return fmt

    def _cache_code_block_spans(self, start_block, lang: Optional[str]) -> None:
        if not self._pygments_enabled:
            return
        lexer = self._lexer_for_language(lang)
        if lexer is None:
            return

        blocks = []
        lines = []
        block = start_block
        while block.isValid():
            text = block.text()
            if text.startswith("```"):
                break
            blocks.append(block)
            lines.append(text)
            block = block.next()

        if not blocks:
            return

        code = "\n".join(lines)
        try:
            tokens = self._pygments_lex(code, lexer)
        except Exception as exc:
            logger.debug("Pygments lexing failed for %s: %s", lang or "plain", exc)
            return

        line_idx = 0
        col = 0
        for token_type, value in tokens:
            remaining = value
            while remaining:
                newline = remaining.find("\n")
                if newline == -1:
                    part = remaining
                    remaining = ""
                else:
                    part = remaining[:newline]
                    remaining = remaining[newline + 1 :]
                if line_idx >= len(blocks):
                    break
                if part:
                    fmt = self._format_for_token(token_type)
                    block_number = blocks[line_idx].blockNumber()
                    self._code_block_spans.setdefault(block_number, []).append((col, len(part), fmt))
                    col += len(part)
                if newline != -1:
                    line_idx += 1
                    col = 0

    def _ensure_code_block_cache(self, block) -> None:
        """If a rehighlight starts mid-block, rebuild cached spans by scanning backward to the fence."""
        if not self._pygments_enabled or block.blockNumber() in self._code_block_spans:
            return
        fence = block.previous()
        while fence.isValid():
            text = fence.text()
            if text.startswith("```"):
                lang = self._extract_fence_language(text)
                self._cache_code_block_spans(fence.next(), lang)
                break
            fence = fence.previous()

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        import time
        t0 = time.perf_counter() if self._timing_enabled else 0.0

        block = self.currentBlock()
        if not block.previous().isValid():
            self._reset_code_block_cache()

        prev_state = self.previousBlockState()
        in_code_block = (prev_state == self.CODE_BLOCK_STATE)
        
        # Check if this line starts or ends a code block
        if text.startswith("```"):
            if in_code_block:
                self._active_code_lang = None
            else:
                self._active_code_lang = self._extract_fence_language(text)
                self._cache_code_block_spans(block.next(), self._active_code_lang)
            in_code_block = not in_code_block
            self.setCurrentBlockState(self.CODE_BLOCK_STATE if in_code_block else 0)
            self.setFormat(0, len(text), self.code_fence_format)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return
        elif in_code_block:
            self.setCurrentBlockState(self.CODE_BLOCK_STATE)
            self.setFormat(0, len(text), self.code_block)
            self._ensure_code_block_cache(block)
            spans = self._code_block_spans.get(block.blockNumber())
            if spans:
                for start, length, fmt in spans:
                    if length > 0 and start < len(text):
                        self.setFormat(start, min(length, len(text) - start), fmt)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return
        else:
            self.setCurrentBlockState(0)

        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        level = heading_level_from_char(stripped[0]) if stripped else 0
        heading_applied = False
        if level:
            fmt = self.heading_styles[min(level, len(self.heading_styles)) - 1]
            self.setFormat(indent + 1, max(0, len(stripped) - 1), fmt)
            self.setFormat(indent, 1, self.hidden_format)
            heading_applied = True
        elif stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= HEADING_MAX_LEVEL and stripped[hashes:hashes + 1] == " ":
                fmt = self.heading_styles[min(hashes, len(self.heading_styles)) - 1]
                self.setFormat(indent + hashes + 1, len(stripped) - hashes - 1, fmt)
                heading_applied = True

        # If we styled a heading, stop here so later rules (links, tags, etc.) don't override
        # the heading font size/color and leave trailing characters unstyled.
        if heading_applied:
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return

        is_table = bool(TABLE_ROW_PATTERN.match(text) or TABLE_SEP_PATTERN.match(text))

        if text.strip().startswith(("- ", "* ", "+ ", "• ")):
            self.setFormat(0, len(text), self.list_format)
        
        # Blockquotes - handle > and >> (nested)
        stripped_for_quote = text.lstrip()
        if stripped_for_quote.startswith(">"):
            # Count the number of > markers
            idx = 0
            while idx < len(stripped_for_quote) and stripped_for_quote[idx] == '>':
                idx += 1
            # Skip optional space after >
            if idx < len(stripped_for_quote) and stripped_for_quote[idx] == ' ':
                idx += 1

            # Hide the > markers and optional spaces
            quote_start = len(text) - len(stripped_for_quote)
            self.setFormat(quote_start, idx, self.hidden_format)

            # Style the remaining text as quote
            remaining_length = len(text) - quote_start - idx
            if remaining_length > 0:
                self.setFormat(quote_start + idx, remaining_length, self.quote_format)
        
        stripped_hr = text.strip()
        if stripped_hr == "---" or stripped_hr == "***" or stripped_hr == "___":
            self.setFormat(0, len(text), self.hr_format)
        
        # Apply monospace + compact font to table rows last so pipes align
        if is_table:
            self.setFormat(0, len(text), self.table_format)
        
        # Inline code - hide backticks and style content
        iterator = self._code_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            
            # Pattern: `content`
            if length >= 2:  # At least `x`
                content_start = start + 1  # Skip opening `
                content_length = length - 2  # Exclude both ` markers
                
                # Hide opening `
                self.setFormat(start, 1, self.hidden_format)
                
                # Apply code format to content
                if content_length > 0:
                    self.setFormat(content_start, content_length, self.code_format)
                
                # Hide closing `
                self.setFormat(start + length - 1, 1, self.hidden_format)
        
        # Bold+Italic (must be checked before bold and italic separately)
        iterator = self._bold_italic_pattern.globalMatch(text)
        bold_italic_ranges = []
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            bold_italic_ranges.append((start, start + length))
            
            # Hide the *** markers and style the content
            # Pattern: ***content***
            content_start = start + 3  # Skip opening ***
            content_length = length - 6  # Exclude both *** markers
            
            # Hide opening ***
            self.setFormat(start, 3, self.hidden_format)
            
            # Apply format to content with actual bold+italic styling
            if content_length > 0:
                fmt = QTextCharFormat()
                fmt.setFontWeight(QFont.Weight.Bold)
                fmt.setFontItalic(True)
                fmt.setForeground(QColor("#ffb8d1"))
                self.setFormat(content_start, content_length, fmt)
            
            # Hide closing ***
            self.setFormat(start + length - 3, 3, self.hidden_format)
        
        # Bold (skip ranges already formatted as bold+italic)
        iterator = self._bold_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            # Check if this range overlaps with bold+italic
            overlaps = any(bi_start <= start < bi_end or bi_start < start + length <= bi_end 
                          for bi_start, bi_end in bold_italic_ranges)
            if not overlaps:
                # Hide the ** markers and style the content
                # Pattern: **content**
                content_start = start + 2  # Skip opening **
                content_length = length - 4  # Exclude both ** markers
                
                # Hide opening **
                self.setFormat(start, 2, self.hidden_format)
                
                # Apply format to content with actual bold font weight
                if content_length > 0:
                    fmt = QTextCharFormat()
                    fmt.setFontWeight(QFont.Weight.Bold)
                    fmt.setForeground(QColor("#ffd479"))
                    self.setFormat(content_start, content_length, fmt)
                
                # Hide closing **
                self.setFormat(start + length - 2, 2, self.hidden_format)
        
        # Italic (skip ranges already formatted as bold+italic)
        iterator = self._italic_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            # Check if this range overlaps with bold+italic
            overlaps = any(bi_start <= start < bi_end or bi_start < start + length <= bi_end 
                          for bi_start, bi_end in bold_italic_ranges)
            if not overlaps:
                # Hide the * markers and style the content
                # Pattern: *content*
                content_start = start + 1  # Skip opening *
                content_length = length - 2  # Exclude both * markers
                
                # Hide opening *
                self.setFormat(start, 1, self.hidden_format)
                
                # Apply format to content with actual italic styling
                if content_length > 0:
                    fmt = QTextCharFormat()
                    fmt.setFontItalic(True)
                    fmt.setForeground(QColor("#ffa7c4"))
                    self.setFormat(content_start, content_length, fmt)
                
                # Hide closing *
                self.setFormat(start + length - 1, 1, self.hidden_format)
        
        # Strikethrough
        iterator = self._strikethrough_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            
            # Hide the ~~ markers and style the content
            # Pattern: ~~content~~
            content_start = start + 2  # Skip opening ~~
            content_length = length - 4  # Exclude both ~~ markers
            
            # Hide opening ~~
            self.setFormat(start, 2, self.hidden_format)
            
            # Apply strikethrough format to content
            if content_length > 0:
                self.setFormat(content_start, content_length, self.strikethrough_format)
            
            # Hide closing ~~
            self.setFormat(start + length - 2, 2, self.hidden_format)
        
        # Highlight
        iterator = self._highlight_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            
            # Hide the == markers and style the content
            # Pattern: ==content==
            content_start = start + 2  # Skip opening ==
            content_length = length - 4  # Exclude both == markers
            
            # Hide opening ==
            self.setFormat(start, 2, self.hidden_format)
            
            # Apply highlight format to content
            if content_length > 0:
                self.setFormat(content_start, content_length, self.highlight_format)
            
            # Hide closing ==
            self.setFormat(start + length - 2, 2, self.hidden_format)

        iterator = TAG_PATTERN.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.tag_format)

        stripped2 = text.lstrip()
        if stripped2.startswith("☐") or stripped2.startswith("☑"):
            offset = len(text) - len(stripped2)
            self.setFormat(offset, 1, self.checkbox_format)

        # Link formatting
        link_format = QTextCharFormat()
        link_format.setForeground(QColor("#4fa3ff"))
        link_format.setFontUnderline(True)

        display_link_spans: list[tuple[int, int]] = []
        idx = 0
        while idx < len(text):
            if text[idx] == "\x00":
                link_start = idx + 1
                link_end = text.find("\x00", link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find("\x00", label_start)
                    if label_end >= label_start:  # Changed: >= instead of > to handle empty labels
                        # Hide opening sentinel and link
                        self.setFormat(idx, label_start - idx, self.hidden_format)
                        # If label is empty, show the link; otherwise show the label
                        if label_end == label_start:  # Empty label
                            # Skip the sentinel pipe we inject for empty labels.
                            visible_end = link_end
                            if link_end > link_start and text[link_end - 1] == "|":
                                visible_end -= 1
                            if visible_end > link_start:
                                self.setFormat(link_start, visible_end - link_start, link_format)
                        else:  # Non-empty label
                            self.setFormat(label_start, label_end - label_start, link_format)
                        self.setFormat(label_end, 1, self.hidden_format)  # Hide closing sentinel
                        display_link_spans.append((idx, label_end + 1))
                        idx = label_end + 1
                        continue
            idx += 1

        # Wiki-style links in storage format: [link|label]
        wiki_pattern = r"\[([^\]|]+)\|([^\]]*)\]"
        import re as regex_module
        wiki_spans: list[tuple[int, int]] = []
        for match in regex_module.finditer(wiki_pattern, text):
            start = match.start()
            end = match.end()
            wiki_spans.append((start, end))
            link = match.group(1)
            label = match.group(2)
            # Highlight the label part
            label_start = start + 1 + len(link) + 1  # After '[link|'
            self.setFormat(label_start, len(label), link_format)

        # CamelCase links: +PageName
        camel_iter = CAMEL_LINK_PATTERN.globalMatch(text)
        while camel_iter.hasNext():
            match = camel_iter.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            inside_wiki = any(ws <= start and end <= we for (ws, we) in wiki_spans)
            inside_display = any(ds <= start and end <= de for (ds, de) in display_link_spans)
            if inside_wiki or inside_display:
                continue
            self.setFormat(start, end - start, link_format)

        # Plain colon links: :Page:Name
        colon_iter = COLON_LINK_PATTERN.globalMatch(text)
        while colon_iter.hasNext():
            match = colon_iter.next()
            s = match.capturedStart(); e = s + match.capturedLength()
            inside_wiki = any(ws <= s and e <= we for (ws, we) in wiki_spans)
            inside_display = any(ds <= s and e <= de for (ds, de) in display_link_spans)
            if not inside_wiki and not inside_display:
                self.setFormat(s, e - s, link_format)

        # File links: [text](./file.ext)
        file_iter = WIKI_FILE_LINK_PATTERN.globalMatch(text)
        while file_iter.hasNext():
            fm = file_iter.next()
            start = fm.capturedStart(); end = start + fm.capturedLength()
            overlap = any(ws <= start and end <= we for (ws, we) in wiki_spans)
            if overlap:
                continue
            label = fm.captured("text")
            label_start = start + 1
            label_len = len(label)
            if label_start > start:
                self.setFormat(start, label_start - start, self.hidden_format)
            self.setFormat(label_start, label_len, link_format)
            label_end = label_start + label_len
            if end > label_end:
                self.setFormat(label_end, end - label_end, self.hidden_format)

        # Plain HTTP URLs (not in wiki-style links)
        http_iter = HTTP_URL_PATTERN.globalMatch(text)
        while http_iter.hasNext():
            match = http_iter.next()
            s = match.capturedStart(); e = s + match.capturedLength()
            inside_wiki = any(ws <= s and e <= we for (ws, we) in wiki_spans)
            inside_display = any(ds <= s and e <= de for (ds, de) in display_link_spans)
            if not inside_wiki and not inside_display:
                self.setFormat(s, e - s, link_format)



        if self._timing_enabled:
            self._timing_total += (time.perf_counter() - t0)
            self._timing_blocks += 1

    def reset_timing(self):
        self._timing_total = 0.0
        self._timing_blocks = 0

    def enable_timing(self, enabled: bool):
        self._timing_enabled = enabled


class MarkdownEditor(QTextEdit):
    def _convert_camelcase_links(self, text: str) -> str:
        """Convert +CamelCase links to colon-style links [:Path:Path:Page|+CamelCase] using current page context, but only if not already inside a [link|label]."""
        import re
        from pathlib import Path
        from zimx.server.adapters.files import PAGE_SUFFIX
        from .path_utils import path_to_colon
        current_path = self.current_relative_path() if hasattr(self, "current_relative_path") else None
        base_dir: Optional[Path] = None
        if current_path:
            try:
                current = Path(current_path)
                # When pointed at /Page/Page.txt, use the containing folder as the base
                base_dir = current.parent if current.suffix == PAGE_SUFFIX else current
            except Exception:
                base_dir = None
        # Find all [link|label] spans so we can skip +CamelCase in the label part
        link_spans = []
        for m in re.finditer(r'\[([^\]|]+)\|([^\]]*)\]', text):
            # Mark the label part (after the |)
            link_start = m.start()
            pipe = text.find('|', link_start, m.end())
            if pipe != -1:
                label_start = pipe + 1
                link_spans.append((label_start, m.end() - 1))  # exclude the closing ]
        def is_in_label(pos):
            return any(start <= pos < end for start, end in link_spans)
        allowed_prefixes = {"(", "[", "{", "<", "'", '"'}
        def replacer(match):
            start = match.start()
            if is_in_label(start):
                return match.group(0)  # Don't replace if in label part
            if start > 0:
                prev = text[start - 1]
                # Only convert when +CamelCase appears after whitespace or opening punctuation.
                if not prev.isspace() and prev not in allowed_prefixes:
                    return match.group(0)
            link = match.group('link')
            label = link  # Just the page name, no plus
            if base_dir:
                target_path = (base_dir / link / f"{link}{PAGE_SUFFIX}").as_posix()
                if not target_path.startswith("/"):
                    target_path = f"/{target_path}"
                colon_path = path_to_colon(target_path)
            else:
                colon_path = path_to_colon(f"/{link}/{link}.txt")
            return f"[:{colon_path}|{label}]"
        # Replace +CamelCase only if not in the label part of a [link|label]
        return re.sub(r'\+(?P<link>[A-Z][\w]*)', replacer, text)
    imageSaved = Signal(str)
    focusLost = Signal()
    cursorMoved = Signal(int)
    linkActivated = Signal(str)
    linkHovered = Signal(str)  # Emits link path when hovering/cursor over a link
    linkCopied = Signal(str)  # Emits link text when a link is copied via context menu
    headingsChanged = Signal(list)
    viewportResized = Signal()
    editPageSourceRequested = Signal(str)  # Emits file path when user wants to edit page source
    openFileLocationRequested = Signal(str)  # Emits file path when user wants to open file location
    insertDateRequested = Signal()
    attachmentDropped = Signal(str)  # Emits filename when a file is dropped into the editor
    backlinksRequested = Signal(str)  # Emits current page path when backlinks are requested
    aiChatRequested = Signal(str)  # Emits current page path when AI Chat is requested
    aiChatSendRequested = Signal(str)  # Send selected/whole text to the open chat
    aiChatPageFocusRequested = Signal(str)  # Request the chat tab focused on this page
    aiActionRequested = Signal(str, str, str)  # title, prompt, text
    findBarRequested = Signal(bool, bool, str)  # replace_mode, backwards_first, seed_query
    viInsertModeChanged = Signal(bool)  # Emits True when editor is in insert mode
    headingPickerRequested = Signal(object, bool)  # QPoint(global), prefer_above
    _VI_EXTRA_KEY = QTextFormat.UserProperty + 1
    _FLASH_EXTRA_KEY = QTextFormat.UserProperty + 2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_path: Optional[str] = None
        self._vault_root: Optional[Path] = None
        self._vi_mode_active: bool = False
        self._vi_block_cursor_enabled: bool = True  # default on, controlled by preferences
        self._vi_saved_flash_time: Optional[int] = None
        self._vi_last_cursor_pos: int = -1
        self._vi_feature_enabled: bool = False
        self._vi_insert_mode: bool = False
        self._vi_replace_pending: bool = False
        self._vi_last_edit: Optional[Callable[[], None]] = None
        self._vi_clipboard: str = ""
        self._vi_pending_activation: bool = False
        self._vi_has_painted: bool = False
        self._vi_paint_in_progress: bool = False
        self._vi_activation_timer: Optional[QTimer] = None
        self._heading_outline: list[dict] = []
        self._dialog_block_input: bool = False
        self._ai_actions_enabled: bool = True
        self._ai_chat_available: bool = False
        self._ai_chat_active: bool = False
        self._page_load_logger: Optional[PageLoadLogger] = None
        self._open_in_window_callback: Optional[Callable[[str], None]] = None
        self._filter_nav_callback: Optional[Callable[[str], None]] = None
        self._search_engine = SearchEngine(self)
        self.setPlaceholderText("Open a Markdown file to begin editing…")
        self.setAcceptRichText(True)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self._indent_unit = " " * 4
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.highlighter = MarkdownHighlighter(self.document())
        self.cursorPositionChanged.connect(self._emit_cursor)
        self.cursorPositionChanged.connect(self._maybe_update_vi_cursor)
        self.cursorPositionChanged.connect(self._ensure_cursor_margin)
        self._display_guard = False
        self.textChanged.connect(self._enforce_display_symbols)
        self.viewport().installEventFilter(self)
        self._heading_timer = QTimer(self)
        self._heading_timer.setInterval(250)
        self._heading_timer.setSingleShot(True)
        self._heading_timer.timeout.connect(self._emit_heading_outline)
        self.textChanged.connect(self._schedule_heading_outline)
        # Timer for CamelCase link conversion; explicitly started on key triggers
        self._camel_refresh_timer = QTimer(self)
        self._camel_refresh_timer.setInterval(120)
        self._camel_refresh_timer.setSingleShot(True)
        self._camel_refresh_timer.timeout.connect(self._refresh_camel_links)
        self._last_camel_trigger: Optional[str] = None
        self._last_camel_cursor_pos: Optional[int] = None
        # Enable mouse tracking for hover cursor changes
        self.viewport().setMouseTracking(True)
        # Enable drag and drop for file attachments
        self.setAcceptDrops(True)
        # Configure scroll-past-end margin initially
        QTimer.singleShot(0, self._apply_scroll_past_end_margin)

        self._ai_send_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        try:
            self._ai_send_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        except Exception:
            pass
        self._ai_send_shortcut.activated.connect(self._show_ai_action_overlay)
        self._ai_focus_shortcut = QShortcut(QKeySequence("Ctrl+Shift+["), self)
        self._ai_focus_shortcut.activated.connect(self._emit_ai_chat_focus)

        self._ai_action_overlay = AIActionOverlay(self)
        self._ai_action_overlay.actionTriggered.connect(self._handle_ai_action_overlay)
        self._ai_action_overlay.sendSelection.connect(self._emit_ai_chat_send)
        self._ai_action_overlay.startChat.connect(self._emit_ai_chat_start)
        self._ai_action_overlay.loadChat.connect(self._emit_ai_chat_focus)
        self._ai_action_overlay.closed.connect(self._restore_vi_after_overlay)
        self._overlay_vi_mode_before: Optional[bool] = None
        self._document_alive = True
        self._editor_alive = True
        self._layout_alive = True
        self._viewport_alive = True
        self.destroyed.connect(self._on_editor_destroyed)
        self._connect_document_signals(self.document())
        viewport = self.viewport()
        if viewport is not None:
            viewport.destroyed.connect(self._on_viewport_destroyed)

    def _status_message(self, msg: str, duration: int = 2000) -> None:
        window = self.window()
        try:
            if window and hasattr(window, "statusBar"):
                window.statusBar().showMessage(msg, duration)
        except Exception:
            pass

    def _search_seed_query(self) -> str:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        return ""

    def search_find_next(self, query: str, *, backwards: bool = False, wrap: bool = True, case_sensitive: bool = False) -> bool:
        found, wrapped = self._search_engine.find_next(
            query,
            backwards=backwards,
            wrap=wrap,
            case_sensitive=case_sensitive,
        )
        if not found:
            self._status_message("No match found.")
            return False
        if wrapped:
            self._status_message(f"Wrapped to {'end' if backwards else 'beginning'} of document.")
        return True

    def search_replace_current(self, replacement: str) -> bool:
        if not self._search_engine.last_query:
            self._status_message("Find text before replacing.")
            return False
        replaced = self._search_engine.replace_current(replacement)
        if not replaced:
            self._status_message("No current match to replace.")
        return replaced

    def search_replace_all(self, query: str, replacement: str, *, case_sensitive: bool = False) -> int:
        count = self._search_engine.replace_all(query, replacement, case_sensitive=case_sensitive)
        if count == 0:
            self._status_message("No matches replaced.")
        else:
            plural = "occurrence" if count == 1 else "occurrences"
            self._status_message(f"Replaced {count} {plural}.", 2500)
        return count

    def search_repeat_last(self, reverse: bool = False) -> bool:
        found, wrapped, backwards = self._search_engine.repeat_last(reverse=reverse)
        if not found:
            self._status_message("No previous search.")
            return False
        if wrapped:
            self._status_message(f"Wrapped to {'end' if backwards else 'beginning'} of document.")
        return True

    def search_word_under_cursor(self, *, backwards: bool = False) -> bool:
        cursor = self.textCursor()
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText().replace("\u2029", "\n").strip()
        if not word:
            self._status_message("No word under cursor.")
            return False
        return self.search_find_next(word, backwards=backwards, wrap=True, case_sensitive=False)

    def last_search_query(self) -> str:
        return self._search_engine.last_query

    def request_find_bar(self, *, replace: bool, backwards: bool = False, seed: Optional[str] = None) -> None:
        query = seed if seed is not None else self._search_seed_query()
        self.findBarRequested.emit(replace, backwards, query)

    def set_pygments_style(self, style: str) -> None:
        """Update the code-fence highlighting style."""
        try:
            self.highlighter.set_pygments_style(style)
        except Exception:
            pass

    def is_ai_overlay_visible(self) -> bool:
        return self._ai_action_overlay.is_visible()

    def _connect_document_signals(self, document: Optional[QTextDocument]) -> None:
        if document is None:
            self._document_alive = False
            return
        if not self._is_alive(document):
            self._document_alive = False
            return
        document.destroyed.connect(self._on_document_destroyed)
        self._document_alive = True
        layout = document.documentLayout()
        if layout is not None:
            try:
                layout.destroyed.connect(self._on_layout_destroyed)
                self._layout_alive = True
            except Exception:
                pass

    def _on_layout_destroyed(self) -> None:
        self._layout_alive = False

    def _on_document_destroyed(self) -> None:
        self._document_alive = False

    def _on_editor_destroyed(self) -> None:
        self._editor_alive = False

    def _on_viewport_destroyed(self) -> None:
        self._viewport_alive = False

    def _is_alive(self, obj) -> bool:
        return bool(obj) and Shiboken.isValid(obj)

    def paintEvent(self, event):  # type: ignore[override]
        """Custom paint to draw horizontal rules as visual lines."""
        self._vi_paint_in_progress = True
        painter: Optional[QPainter] = None
        try:
            super().paintEvent(event)
            if not self._vi_has_painted:
                self._vi_has_painted = True

            try:
                if not self._editor_alive or not self._document_alive or not self._is_alive(self):
                    return
                document = self.document()
                if not self._is_alive(document):
                    return
                layout = document.documentLayout()
                if not self._layout_alive or not self._is_alive(layout):
                    return
                viewport = self.viewport()
                if not self._is_alive(viewport):
                    return
                if not self._viewport_alive:
                    try:
                        viewport.destroyed.connect(self._on_viewport_destroyed)
                        self._viewport_alive = True
                    except Exception:
                        return
                painter = QPainter(viewport)
                pen = QPen(QColor("#555555"))
                pen.setWidth(2)
                painter.setPen(pen)
                scroll_bar = self.verticalScrollBar()
                vsb = scroll_bar.value() if self._is_alive(scroll_bar) else 0
                block = document.begin()
                while block.isValid():
                    if not self._document_alive or not self._editor_alive:
                        break
                    if not self._is_alive(document) or not self._is_alive(layout):
                        break
                    if block.text().strip() == "---":
                        try:
                            br = layout.blockBoundingRect(block)
                        except RuntimeError as exc:
                            logger.warning("Aborting markdown rule overlay; layout gone: %s", exc)
                            break
                        viewport_height = viewport.height() if self._is_alive(viewport) else 0
                        y = int(br.top() - vsb + br.height() / 2)
                        if 0 <= y <= viewport_height:
                            painter.drawLine(0, y, viewport.width(), y)
                    block = block.next()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping Markdown paint overlay due to error: %s", exc, exc_info=True)
            finally:
                if painter is not None and painter.isActive():
                    painter.end()
        finally:
            self._vi_paint_in_progress = False

    def set_context(self, vault_root: Optional[str], relative_path: Optional[str]) -> None:
        self._vault_root = Path(vault_root) if vault_root else None
        self._current_path = relative_path

    def setDocument(self, document: QTextDocument) -> None:  # type: ignore[override]
        old_document = self.document()
        if old_document is not None:
            try:
                old_document.destroyed.disconnect(self._on_document_destroyed)
            except (TypeError, RuntimeError):
                pass
            old_layout = old_document.documentLayout()
            if old_layout is not None:
                try:
                    old_layout.destroyed.disconnect(self._on_layout_destroyed)
                except (TypeError, RuntimeError):
                    pass
        super().setDocument(document)
        self._connect_document_signals(document)

    def current_relative_path(self) -> Optional[str]:
        return self._current_path

    def set_page_load_logger(self, logger: Optional[PageLoadLogger]) -> None:
        """Attach a page load logger for the next render cycle."""
        # The logger itself knows whether logging is enabled.
        self._page_load_logger = logger

    def set_open_in_window_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Provide a handler to open the current page in a separate window (main editor only)."""
        self._open_in_window_callback = callback

    def set_filter_nav_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Provide a handler to filter the navigation tree by the current page's subtree."""
        self._filter_nav_callback = callback

    def _mark_page_load(self, label: str) -> None:
        if self._page_load_logger:
            self._page_load_logger.mark(label)

    def _complete_page_load_logging(self, label: str) -> None:
        if self._page_load_logger:
            self._page_load_logger.end(label)
            self._page_load_logger = None

    def set_markdown(self, content: str) -> None:
        import time
        from os import getenv
        t0 = time.perf_counter()
        self._mark_page_load("render start")
        
        normalized = self._normalize_markdown_images(content)
        t1 = time.perf_counter()
        self._mark_page_load("normalize images")
        
        display = self._to_display(normalized)
        t2 = time.perf_counter()
        self._mark_page_load("convert to display text")
        
        # Enable highlighter timing instrumentation (will be disabled at end)
        self.highlighter.enable_timing(True)
        self.highlighter.reset_timing()
        self._display_guard = True
        self.setUpdatesEnabled(False)
        self.document().clear()
        
        # Temporarily disconnect expensive textChanged handlers during bulk loading
        self.textChanged.disconnect(self._enforce_display_symbols)
        self.textChanged.disconnect(self._schedule_heading_outline)
        
        # Also temporarily disable highlighter for very slow documents
        highlighter_disabled = False
        if getenv("ZIMX_DISABLE_HIGHLIGHTER_LOAD") == "1":
            highlighter_disabled = True
            self.highlighter.setDocument(None)
        
        incremental = False
        batch_ms_total = 0.0
        batches = 0
        if getenv("ZIMX_INCREMENTAL_LOAD") == "1":
            # Incremental batch insertion to compare performance with setPlainText
            incremental = True
            lines = display.splitlines(keepends=True)
            batch_size = 50  # tune if needed
            buf = []
            import time as _time
            for i, line in enumerate(lines):
                buf.append(line)
                if len(buf) >= batch_size or i == len(lines) - 1:
                    b0 = _time.perf_counter()
                    self.insertPlainText("".join(buf))
                    b1 = _time.perf_counter()
                    batch_ms_total += (b1 - b0) * 1000.0
                    batches += 1
                    buf = []
            t3 = time.perf_counter()
        else:
            self.setPlainText(display)
            t3 = time.perf_counter()
        self._mark_page_load("document populated")
        
        # Reconnect the textChanged handlers
        self.textChanged.connect(self._enforce_display_symbols)
        self.textChanged.connect(self._schedule_heading_outline)
        
        # Re-enable highlighter if it was disabled
        if highlighter_disabled:
            self.highlighter.setDocument(self.document())
        self.setUpdatesEnabled(True)
        
        # Lazy load images after a short delay to let the UI render first
        scheduled_at = time.perf_counter()
        QTimer.singleShot(0, lambda: self._render_images(display, scheduled_at))
        t4 = time.perf_counter()
        self._mark_page_load("queued image render")
        
        self._display_guard = False
        self._schedule_heading_outline()
        # Ensure scroll-past-end margin is applied after new content
        self._apply_scroll_past_end_margin()
        t5 = time.perf_counter()
        self._mark_page_load("outline + margin scheduled")
        
        if _DETAILED_LOGGING:
            print(f"[TIMING] set_markdown breakdown:")
            print(f"  normalize_images: {(t1-t0)*1000:.1f}ms")
            print(f"  to_display: {(t2-t1)*1000:.1f}ms")
            print(f"  setPlainText: {(t3-t2)*1000:.1f}ms")
            print(f"  render_images: {(t4-t3)*1000:.1f}ms (lazy - deferred)")
            print(f"  schedule_outline+margin: {(t5-t4)*1000:.1f}ms")
            print(f"  TOTAL: {(t5-t0)*1000:.1f}ms")
        
            # Warn about potential performance issues
            setPlainText_time_ms = (t3-t2)*1000
            if setPlainText_time_ms > 1000:
                print(f"[PERF WARNING] setPlainText took {setPlainText_time_ms:.1f}ms - unusually slow!")
                print("  This may indicate regex backtracking or signal cascade issues.")
                print("  Try environment variable ZIMX_DISABLE_HIGHLIGHTER_LOAD=1 for testing.")
            if incremental:
                print(f"[TIMING] Incremental batches={batches} cumulative_insert={batch_ms_total:.1f}ms avg_batch={(batch_ms_total/max(batches,1)):.1f}ms")
            # Report highlighter timing
            if self.highlighter._timing_blocks:
                avg = (self.highlighter._timing_total / self.highlighter._timing_blocks) * 1000.0
                total = self.highlighter._timing_total * 1000.0
                print(f"[TIMING] Highlighter: blocks={self.highlighter._timing_blocks} total={total:.1f}ms avg={avg:.2f}ms")
        # Disable timing to avoid overhead for subsequent edits
        self.highlighter.enable_timing(False)
        self._mark_page_load("editor focus ready")

        # Ensure the editor has focus after loading and rendering
        self.setFocus()

    def to_markdown(self) -> str:
        markdown = self._doc_to_markdown()
        markdown = self._normalize_markdown_images(markdown)
        # Convert +CamelCase links to colon-style links before saving
        markdown = self._convert_camelcase_links(markdown)
        return self._from_display(markdown)

    def _schedule_camel_refresh(self) -> None:
        """Schedule a quick refresh to render +CamelCase links into colon format."""
        if self._display_guard:
            return
        text = self.toPlainText()
        # Fast path: skip if there's no +CamelCase pattern
        if "+" not in text:
            return
        import re
        if not re.search(r"\+[A-Za-z][\w]*", text):
            return
        self._camel_refresh_timer.start()

    def _refresh_camel_links(self) -> None:
        """Convert any +CamelCase links in the document and re-render display."""
        if self._display_guard:
            return
        current_text = self.toPlainText()
        storage_text = self._from_display(current_text)
        converted = self._convert_camelcase_links(storage_text)
        if converted == storage_text:
            self._last_camel_trigger = None
            self._last_camel_cursor_pos = None
            return
        # Re-render with updated links
        self._display_guard = True
        display_text = self._to_display(converted)
        cursor_pos = self.textCursor().position()
        self.document().setPlainText(display_text)
        new_cursor = self.textCursor()
        new_cursor.setPosition(min(cursor_pos, len(display_text)))
        # After conversion, move cursor appropriately based on trigger
        self._position_cursor_after_camel(new_cursor, display_text)
        self.setTextCursor(new_cursor)
        self._render_images(display_text)
        self._display_guard = False
        self._schedule_heading_outline()
        self._apply_scroll_past_end_margin()

    def _position_cursor_after_camel(self, cursor: QTextCursor, text: str) -> None:
        """Place cursor after the converted link based on last trigger (space/enter)."""
        trigger = self._last_camel_trigger
        origin_pos = self._last_camel_cursor_pos
        self._last_camel_trigger = None
        self._last_camel_cursor_pos = None
        if not trigger:
            return

        # Find the last converted link at or before the original cursor position
        chosen = None
        for m in WIKI_LINK_DISPLAY_PATTERN.finditer(text):
            if origin_pos is None or m.start() <= origin_pos:
                chosen = m
        if not chosen:
            return

        # Position after the closing sentinel of the chosen link
        target_pos = chosen.end()
        cursor.setPosition(min(target_pos, len(text)))

        if trigger == "space":
            # Land after the space the user just typed (or insert one if missing)
            if target_pos < len(text) and text[target_pos] == " ":
                cursor.setPosition(min(target_pos + 1, len(text)))
            else:
                cursor.insertText(" ")
        elif trigger == "enter":
            # Land on the next line (or insert a newline if missing)
            if target_pos < len(text) and text[target_pos] == "\n":
                cursor.setPosition(min(target_pos + 1, len(text)))
            else:
                cursor.insertText("\n")

    def set_font_point_size(self, size: int) -> None:
        # Clamp to a sensible, positive point size to avoid Qt warnings
        try:
            safe_size = max(6, int(size))
        except Exception:
            safe_size = 13
        font = self.font()
        font.setPointSize(safe_size)
        self.setFont(font)

    def begin_dialog_block(self) -> None:
        """Completely freeze editor input and interaction while a modal dialog is open."""
        self._dialog_block_input = True
        self.setReadOnly(True)
        self.setEnabled(False)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.viewport().setCursor(Qt.ArrowCursor)
        # Optionally, clear selection to avoid visual confusion
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

    def end_dialog_block(self) -> None:
        """Re-enable editor input and interaction after a modal dialog closes."""
        self._dialog_block_input = False
        self.setReadOnly(False)
        self.setEnabled(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.viewport().unsetCursor()

    def insert_link(self, colon_path: str, link_name: str | None = None) -> None:
        """Insert a link at the current cursor position.
        
        Always inserts in unified [link|label] format (label may be empty).
        """
        if not colon_path:
            return
        
        is_http_url = colon_path.startswith(("http://", "https://"))
        target = self._normalize_external_link(colon_path) if is_http_url else ensure_root_colon_link(colon_path)
        
        cursor = self.textCursor()
        pos_before = cursor.position()
        
        # Always use [link|label] format; default label is empty when it matches the link
        label = ""
        if link_name and link_name.strip():
            candidate = link_name.strip()
            match_left = candidate.lstrip(":/")
            target_left = target.lstrip(":/")
            if match_left != target_left:
                label = candidate
        link_text = f"[{target}|{label}]"
        
        # Insert the storage format text
        cursor.insertText(link_text)
        pos_after_insert = cursor.position()
        
        # Full refresh ensures wiki links convert to hidden-display format immediately.
        self._refresh_display()
        
        # After refresh, find the link in display format and position cursor after it
        # The cursor should be after the link's closing sentinel
        block = self.document().findBlock(pos_before)
        if block.isValid():
            text = block.text()
            block_pos = block.position()
            rel_pos = pos_before - block_pos
            
            # Find the display link that starts at or near our insertion point
            idx = 0
            found = False
            while idx < len(text):
                if text[idx] == '\x00':
                    link_start = idx + 1
                    link_end = text.find('\x00', link_start)
                    if link_end > link_start:
                        label_start = link_end + 1
                        label_end = text.find('\x00', label_start)
                        if label_end >= label_start:
                            # Check if this link is at or near our insertion point
                            if idx <= rel_pos <= label_end + 1:
                                # Position cursor after the closing sentinel
                                new_cursor = QTextCursor(self.document())
                                new_cursor.setPosition(block_pos + label_end + 1)
                                self.setTextCursor(new_cursor)
                                found = True
                                break
                            idx = label_end + 1
                            continue
                idx += 1
            
            if found:
                return
        
        # Fallback: position at a safe location
        # Use the original position after insert, but cap it to document length
        safe_pos = min(pos_after_insert, self.document().characterCount() - 1)
        new_cursor = QTextCursor(self.document())
        new_cursor.setPosition(max(0, safe_pos))
        self.setTextCursor(new_cursor)

    def toggle_task_state(self) -> None:
        cursor = self.textCursor()
        initial_position = cursor.position()
        block = cursor.block()
        text = block.text()
        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        if stripped.startswith("☐") or stripped.startswith("☑"):
            symbol = stripped[0]
            new_symbol = "☑" if symbol == "☐" else "☐"
            block_cursor = QTextCursor(block)
            block_cursor.setPosition(block.position() + indent)
            block_cursor.setPosition(block.position() + indent + 1, QTextCursor.KeepAnchor)
            block_cursor.insertText(new_symbol)
            self._enforce_display_symbols()
            self._restore_cursor_position(initial_position)
            return
        match = TASK_PATTERN.match(text)
        if not match:
            return
        start = len(match.captured("indent"))
        state = match.captured("state") or " "
        new_state = "x" if state.strip().lower() != "x" else " "
        block_cursor = QTextCursor(block)
        block_cursor.setPosition(block.position() + start)
        block_cursor.setPosition(block.position() + start + 3, QTextCursor.KeepAnchor)
        block_cursor.insertText(f"({new_state})")
        self._enforce_display_symbols()
        self._restore_cursor_position(initial_position)

    def insertFromMimeData(self, source: QMimeData) -> None:  # type: ignore[override]
        # 1) Images → save and embed
        if source.hasImage() and self._vault_root and self._current_path:
            image = source.imageData()
            if isinstance(image, QImage):
                saved = self._save_image(image)
                if saved:
                    self._insert_image_from_path(saved.name, alt=saved.stem)
                    self.imageSaved.emit(saved.name)
                    return

        # 2) Rich HTML → prefer plain text if available to avoid style noise
        if source.hasHtml() and source.hasText():
            plain = source.text()
            if plain:
                self.textCursor().insertText(plain)
                return

        # 3) Rich HTML → markdown (avoid pasting styled fragments)
        if source.hasHtml():
            html = source.html()
            plain_from_html = self._html_to_plaintext_with_links(html)
            if not plain_from_html and source.hasText():
                plain_from_html = source.text()
            if plain_from_html:
                self.textCursor().insertText(plain_from_html)
                if "[" in plain_from_html and "|" in plain_from_html:
                    self._refresh_display()
                return

        # 4) Default paste without auto-link munging
        super().insertFromMimeData(source)

    def _html_to_plaintext_with_links(self, html: str) -> str:
        """Strip HTML to plain text, converting anchors to [url|label] links."""
        if not html:
            return ""

        class _PlainLinkParser(HTMLParser):
            block_tags = {
                "p", "div", "section", "article", "header", "footer",
                "blockquote", "pre", "li", "ul", "ol",
                "table", "tr", "td", "th",
                "h1", "h2", "h3", "h4", "h5", "h6",
            }

            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []
                self._link_href: Optional[str] = None
                self._link_text: list[str] = []

            def _last_char(self) -> str:
                return self.parts[-1][-1] if self.parts else ""

            def _ensure_newline(self) -> None:
                if self._last_char() != "\n":
                    self.parts.append("\n")

            def handle_starttag(self, tag: str, attrs) -> None:
                tag = tag.lower()
                if tag == "a":
                    self._link_href = dict(attrs).get("href", "")
                    self._link_text = []
                    return
                if tag == "br":
                    self._ensure_newline()
                    return
                if tag in self.block_tags:
                    self._ensure_newline()
                    if tag == "li":
                        self.parts.append("- ")

            def handle_endtag(self, tag: str) -> None:
                tag = tag.lower()
                if tag == "a":
                    label = "".join(self._link_text).strip()
                    href = self._link_href or ""
                    if href:
                        link_target = self._normalize_external_link(href)
                        display = label or ""
                        self.parts.append(f"[{link_target}|{display}]")
                    else:
                        self.parts.append(label)
                    self._link_href = None
                    self._link_text = []
                    return
                if tag in self.block_tags:
                    self._ensure_newline()

            def handle_data(self, data: str) -> None:
                if self._link_href is not None:
                    self._link_text.append(data)
                else:
                    self.parts.append(data)

        parser = _PlainLinkParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception:
            return ""

        text = "".join(parser.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip("\n")

    def _normalize_external_link(self, link: str) -> str:
        """Preserve full external links while stripping whitespace and hidden sentinels."""
        text = (link or "").strip()
        if "\x00" in text:
            text = text.split("\x00", 1)[0]
        return text

    def _wrap_plain_http_links(self, text: str) -> str:
        """Convert bare HTTP(S) URLs into wiki format [url|] to enable sentinel rendering."""
        if not text or "http" not in text:
            return text

        pattern = re.compile(r"(?<!\[)(?<!\()(https?://[^\s<>\[\]\(\)\x00]+)")

        def repl(match: re.Match[str]) -> str:
            url = match.group(1)
            normalized = self._normalize_external_link(url)
            return f"[{normalized}|]"

        return pattern.sub(repl, text)

    def _has_markdown_syntax(self, text: str) -> bool:
        """Heuristic to decide if text is markdown (headings, lists, code, etc.)."""
        if not text:
            return False
        patterns = [
            r"^\s{0,3}#{1,6}\s+\S",           # headings
            r"^\s{0,3}[-*+]\s+\S",            # bullets
            r"^\s{0,3}\d+\.\s+\S",            # ordered list
            r"^\s{0,3}>\s?\S",                # blockquote
            r"```",                           # fenced code
            r"~~",                            # strikethrough markers
            r"`[^`]+`",                       # inline code
            r"\*\*[^*]+\*\*",                 # bold
            r"\*[^\s][^*]*\*",                # italic
            r"\[([^\]]+)\]\([^)]+\)",         # link
            r"^\s{0,3}[-*_]{3,}\s*$",         # horizontal rule
        ]
        for pat in patterns:
            if re.search(pat, text, re.MULTILINE):
                return True
        return False

    def _save_image(self, image: QImage) -> Optional[Path]:
        """Save a pasted image next to the current page and return its absolute Path.

        Images are stored in the same folder as the current page
        using sequential names like paste_image_001.png, paste_image_002.png, etc.
        """
        if not (self._vault_root and self._current_path):
            return None
        rel_file_path = self._current_path.lstrip("/")
        folder = (self._vault_root / rel_file_path).resolve().parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        index = 1
        while True:
            candidate = folder / f"paste_image_{index:03d}.png"
            if not candidate.exists():
                break
            index += 1
        if image.save(str(candidate), "PNG"):
            return candidate
        return None
    
    
    # (Removed old _copy_link_to_location; newer implementation exists later in file)

    def _toggle_markdown_format(self, prefix: str, suffix: str = None) -> None:
        """Toggle markdown formatting around selected text or word at cursor.
        
        Args:
            prefix: The markdown prefix (e.g., '**' for bold)
            suffix: The markdown suffix (defaults to prefix if None)
        """
        if suffix is None:
            suffix = prefix
        
        cursor = self.textCursor()
        
        # If no selection, select the word under cursor
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        
        selected_text = cursor.selectedText()
        if not selected_text:
            return
        
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        
        # Get the full document text to check surrounding characters
        doc_text = self.toPlainText()
        
        # Check if selection is already wrapped with these markers
        prefix_len = len(prefix)
        suffix_len = len(suffix)
        
        already_wrapped = False
        if (start >= prefix_len and end + suffix_len <= len(doc_text)):
            before = doc_text[start - prefix_len:start]
            after = doc_text[end:end + suffix_len]
            if before == prefix and after == suffix:
                already_wrapped = True
        
        cursor.beginEditBlock()
        
        if already_wrapped:
            # Remove the wrapping markers
            # First remove suffix
            cursor.setPosition(end)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, suffix_len)
            cursor.removeSelectedText()
            
            # Then remove prefix
            cursor.setPosition(start - prefix_len)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, prefix_len)
            cursor.removeSelectedText()
            
            # Restore selection without the markers
            cursor.setPosition(start - prefix_len)
            cursor.setPosition(start - prefix_len + len(selected_text), QTextCursor.KeepAnchor)
        else:
            # Add the wrapping markers
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            wrapped_text = f"{prefix}{selected_text}{suffix}"
            cursor.insertText(wrapped_text)
            
            # Select the content (without the markers)
            cursor.setPosition(start + prefix_len)
            cursor.setPosition(start + prefix_len + len(selected_text), QTextCursor.KeepAnchor)
        
        cursor.endEditBlock()
        self.setTextCursor(cursor)
    
    def _toggle_bold(self) -> None:
        """Toggle bold formatting (**text**)."""
        cursor = self.textCursor()
        selected = cursor.selectedText()
        
        # Check if already italic with * - if so, upgrade to bold+italic
        if selected and len(selected) >= 2:
            if selected[0] == '*' and selected[-1] == '*' and not (selected.startswith('**') or selected.startswith('***')):
                # Already italic with *, upgrade to ***
                cursor.beginEditBlock()
                new_text = f"**{selected}*"
                cursor.insertText(new_text)
                cursor.endEditBlock()
                return
        
        self._toggle_markdown_format('**')
    
    def _toggle_italic(self) -> None:
        """Toggle italic formatting (*text*)."""
        cursor = self.textCursor()
        selected = cursor.selectedText()
        
        # Check if already bold with ** - if so, upgrade to bold+italic
        if selected and len(selected) >= 4:
            if selected.startswith('**') and selected.endswith('**'):
                # Already bold, upgrade to ***
                cursor.beginEditBlock()
                new_text = f"*{selected}*"
                cursor.insertText(new_text)
                cursor.endEditBlock()
                return
        
        self._toggle_markdown_format('*')
    
    def _toggle_strikethrough(self) -> None:
        """Toggle strikethrough formatting (~~text~~)."""
        self._toggle_markdown_format('~~')
    
    def _toggle_highlight(self) -> None:
        """Toggle highlight formatting (==text==)."""
        self._toggle_markdown_format('==')

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self.focusLost.emit()

    def keyPressEvent(self, event):  # type: ignore[override]
        if self._dialog_block_input:
            event.ignore()
            return
        # Markdown formatting shortcuts and undo/redo (Ctrl+Z/Ctrl+Y)
        if event.modifiers() == Qt.ControlModifier:
            if event.key() == Qt.Key_B:
                self._toggle_bold()
                event.accept()
                return
            elif event.key() == Qt.Key_I:
                self._toggle_italic()
                event.accept()
                return
            elif event.key() == Qt.Key_K:
                self._toggle_strikethrough()
                event.accept()
                return
            elif event.key() == Qt.Key_Z:
                self._undo_or_status()
                event.accept()
                return
            elif event.key() == Qt.Key_Y:
                self._redo_or_status()
                event.accept()
                return
        if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
            if event.key() == Qt.Key_H:
                self._toggle_highlight()
                event.accept()
                return
        
        if self._vi_feature_enabled and self._handle_vi_keypress(event):
            event.accept()
            return

        if (event.modifiers() & Qt.ControlModifier) and (event.modifiers() & Qt.ShiftModifier):
            if event.key() == Qt.Key_K:
                self._vi_page_up()
                event.accept()
                return
            if event.key() == Qt.Key_J:
                self._vi_page_down()
                event.accept()
                return
        # Check for meaningful modifiers (ignore KeypadModifier which Qt may add on some platforms)
        # This is used throughout the keyPressEvent for cross-platform compatibility
        meaningful_modifiers = event.modifiers() & ~Qt.KeypadModifier
        
        # Bullet/task mode key handling
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        is_bullet, indent, content = self._is_bullet_line(text)
        is_task, task_indent, task_state, task_content = self._is_task_line(text)
        # Ctrl+E: edit link under cursor
        if event.key() == Qt.Key_E and event.modifiers() == Qt.ControlModifier:
            self._edit_link_at_cursor(cursor)
            event.accept()
            return
        # Tab: indent current line or selection
        if event.key() == Qt.Key_Tab and not meaningful_modifiers:
            if cursor.hasSelection() and self._apply_indent_to_selection(dedent=False):
                event.accept()
                return
            if is_bullet and self._handle_bullet_indent():
                event.accept()
                return
            if is_task and self._handle_task_indent(task_indent):
                event.accept()
                return
        # Shift-Tab / Ctrl+Shift+Tab: dedent
        if event.key() == Qt.Key_Backtab and not (meaningful_modifiers & ~(Qt.ControlModifier | Qt.ShiftModifier)):
            if cursor.hasSelection() and self._apply_indent_to_selection(dedent=True):
                event.accept()
                return
            if meaningful_modifiers & Qt.ControlModifier:
                # Ctrl+Shift+Tab without a selection is reserved for navigation elsewhere
                pass
            else:
                if is_bullet and self._handle_bullet_dedent():
                    event.accept()
                    return
                if is_task and self._handle_task_dedent(task_indent):
                    event.accept()
                    return
                if self._handle_generic_dedent():
                    event.accept()
                    return
        # Enter: continue bullet or terminate if empty
        if is_bullet and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            if self._handle_bullet_enter():
                event.accept()
                return
        # Enter: continue task checkbox lines
        if is_task and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            # If the caret sits inside a link on a task line, activate the link instead of inserting a new task
            if self._is_cursor_at_link_activation_point(cursor):
                link = self._link_under_cursor(cursor)
                if link:
                    self.linkActivated.emit(link)
                    event.accept()
                    return
            if self._handle_task_enter(task_indent, task_content):
                event.accept()
                return
        # Esc: vi-mode exit or terminate bullet mode when vi is disabled
        if event.key() == Qt.Key_Escape:
            if self._handle_vi_escape():
                event.accept()
                return
            cursor.beginEditBlock()
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText("")
            cursor.setPosition(block.position())
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            event.accept()
            return
        # ...existing code...
        # When at end-of-buffer, Down should still scroll the viewport
        if event.key() == Qt.Key_Down and not meaningful_modifiers:
            if self.textCursor().atEnd():
                self._scroll_one_line_down()
                event.accept()
                return
        # Handle Left/Right arrow keys for proper link boundary navigation
        if event.key() in (Qt.Key_Left, Qt.Key_Right) and not meaningful_modifiers:
            if self._handle_link_boundary_navigation(event.key()):
                event.accept()
                return
        
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            # Check if cursor is within a link - if so, just insert newline, don't activate
            cursor = self.textCursor()
            if not self._is_cursor_at_link_activation_point(cursor):
                # Handle bullet list continuation (non-bullet lines)
                if self._handle_bullet_enter():
                    event.accept()
                    return
                # For non-bullets: carry over leading indentation on new line
                if self._handle_enter_indent_same_level():
                    event.accept()
                    return
            else:
                # Cursor is at a link activation point - activate the link
                link = self._link_under_cursor()
                if link:
                    self.linkActivated.emit(link)
                    return
        # Handle quick checkbox typing: "()" -> "( ) ", "(+)" -> "(x) "
        if event.text() == ")" and not meaningful_modifiers:
            super().keyPressEvent(event)
            if self._maybe_expand_checkbox():
                event.accept()
                return
            return
        if event.key() == Qt.Key_Space and not meaningful_modifiers:
            if self._maybe_expand_checkbox():
                super().keyPressEvent(event)
                event.accept()
                return
            # fall through to default handling

        # ...existing code...
        super().keyPressEvent(event)

    def _vi_page_up(self):
        # Simulate PageUp: move cursor up by visible lines
        lines = max(1, self.viewport().height() // self.fontMetrics().lineSpacing())
        c = self.textCursor()
        c.movePosition(QTextCursor.Up, QTextCursor.MoveAnchor, lines)
        self.setTextCursor(c)

    def _vi_page_down(self):
        # Simulate PageDown: move cursor down by visible lines
        lines = max(1, self.viewport().height() // self.fontMetrics().lineSpacing())
        c = self.textCursor()
        c.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, lines)
        self.setTextCursor(c)

    def keyReleaseEvent(self, event):  # type: ignore[override]
        super().keyReleaseEvent(event)
        # Only schedule CamelCase conversion when space/enter are released (typing flow)
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & ~Qt.KeypadModifier):
            self._last_camel_trigger = "enter" if event.key() in (Qt.Key_Return, Qt.Key_Enter) else "space"
            self._last_camel_cursor_pos = self.textCursor().position()
            self._schedule_camel_refresh()

    def contextMenuEvent(self, event):  # type: ignore[override]
        menu = None
        # Check if right-clicking on an image
        if _DETAILED_LOGGING:
            print(
                f"[AI Menu Debug] contextMenuEvent path={self._current_path!r} "
                f"ai_actions_enabled={self._ai_actions_enabled} "
                f"ai_chat_available={self._ai_chat_available} "
                f"text_len={len(self.toPlainText())}"
            )
        image_hit = self._image_at_position(event.pos())
        if image_hit:
            cursor, fmt = image_hit
            # Store the image name as unique identifier instead of position
            image_name = fmt.name()
            menu = QMenu(self)
            self._add_ai_actions_entry(menu)
            for width in (300, 600, 900):
                action = menu.addAction(f"{width}px")
                action.triggered.connect(lambda checked=False, w=width, name=image_name: self._resize_image_by_name(name, w))
            menu.addSeparator()
            reset_action = menu.addAction("Original Size")
            reset_action.triggered.connect(lambda checked=False, name=image_name: self._resize_image_by_name(name, None))
            menu.addSeparator()
            custom_action = menu.addAction("Custom…")
            custom_action.triggered.connect(lambda checked=False, name=image_name: self._prompt_image_width_by_name(name))
            menu.exec(event.globalPos())
            return
        
        # Check if right-clicking on a link
        click_cursor = self.cursorForPosition(event.pos())
        md_link = self._markdown_link_at_cursor(click_cursor)
        plain_link = self._link_under_cursor(click_cursor)
        if md_link or plain_link:
            menu = QMenu(self)
            self._add_ai_actions_entry(menu)
            # Edit Link option
            edit_action = menu.addAction("Edit Link…")
            edit_action.triggered.connect(lambda: self._edit_link_at_cursor(click_cursor))
            menu.addSeparator()
            # Remove Link option
            remove_action = menu.addAction("Remove Link")
            remove_action.triggered.connect(lambda: self._remove_link_at_cursor(click_cursor))
            
            # Copy Link to Location option (copy the linked page's path)
            menu.addSeparator()
            copy_action = menu.addAction("Copy Link to Location")
            link_for_copy = md_link[3] if md_link else plain_link
            copy_action.triggered.connect(lambda: self._copy_link_to_location(link_for_copy))
            insert_date_action = menu.addAction("Insert Date…")
            insert_date_action.triggered.connect(self.insertDateRequested)
            if self._filter_nav_callback and self._current_path:
                filter_action = menu.addAction("Filter navigator to this subtree")
                filter_action.triggered.connect(lambda: self._filter_nav_callback(self._current_path or ""))
            menu.exec(event.globalPos())
            return
        
        # Check if right-clicking anywhere in the editor (for Copy Link to Location)
        if self._current_path:
            menu = self.createStandardContextMenu()
            try:
                self._install_copy_actions(menu)
            except Exception:
                pass
            self._add_ai_actions_entry(menu)
            menu.addSeparator()
            # Get heading text if right-click is on a heading line
            click_cursor = self.cursorForPosition(event.pos())
            line_no = click_cursor.blockNumber() + 1
            heading_text = None
            for entry in self._heading_outline:
                if int(entry.get("line", 0)) == line_no:
                    heading_text = entry.get("title", "") or None
                    break
            copy_label = "Copy Link to this heading" if heading_text else "Copy Link to this Page"
            copy_action = menu.addAction(copy_label)
            copy_action.triggered.connect(
                lambda: self._copy_link_to_location(link_text=None, anchor_text=heading_text)
            )
            backlinks_action = menu.addAction("Backlinks / Navigator")
            backlinks_action.triggered.connect(
                lambda: self.backlinksRequested.emit(self._current_path or "")
            )
            insert_date_action = menu.addAction("Insert Date…")
            insert_date_action.triggered.connect(self.insertDateRequested)
            
            # Add Edit Page Source action (delegates to main window)
            edit_src_action = menu.addAction("Edit Page Source")
            edit_src_action.triggered.connect(lambda: self.editPageSourceRequested.emit(self._current_path))
            
            # Add Open File Location action (delegates to main window)
            open_loc_action = menu.addAction("Open File Location")
            open_loc_action.triggered.connect(lambda: self.openFileLocationRequested.emit(self._current_path))
            if self._open_in_window_callback:
                open_popup_action = menu.addAction("Open in New Editor")
                open_popup_action.triggered.connect(lambda: self._open_in_window_callback(self._current_path or ""))
            if self._filter_nav_callback and self._current_path:
                filter_action = menu.addAction("Filter navigator to this subtree")
                filter_action.triggered.connect(lambda: self._filter_nav_callback(self._current_path or ""))
            menu.exec(event.globalPos())
            return
       
        super().contextMenuEvent(event)

    def _ai_chat_payload_text(self) -> str:
        cursor = self.textCursor()
        selected = cursor.selection().toPlainText()
        text = selected if selected.strip() else self.toPlainText()
        normalized = text.replace("\u2029", "\n").strip()
        return normalized

    def _sanitize_for_clipboard(self, text: str) -> str:
        """Normalize text for copying to the system clipboard.

        - Replace Unicode paragraph separators with newlines.
        - Remove non-printable control characters except tab/newline/carriage return.
        - Trim trailing/leading whitespace.
        """
        if text is None:
            return ""
        text = text.replace("\u2029", "\n")
        # Remove low-control characters but keep common whitespace (\n,\r,\t)
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]+", "", text)
        # Remove BOM, object-replacement, zero-width and bidi control characters,
        # and private-use-area glyphs that can appear as weird symbols when pasted.
        try:
            text = re.sub(
                r"[\u200B-\u200F\u2028\u202A-\u202E\uFEFF\uFFFC\uE000-\uF8FF]",
                "",
                text,
            )
        except Exception:
            pass
        return text.strip()

    def _install_copy_actions(self, menu: QMenu) -> None:
        """Replace the default Copy action with sanitized versions and add 'Copy As Markdown'."""
        if not menu:
            return
        actions = menu.actions()
        copy_act = None
        for a in actions:
            t = (a.text() or "").lower()
            if t.startswith("copy"):
                copy_act = a
                break
        # Create sanitized copy action
        def do_copy():
            cursor = self.textCursor()
            if cursor.hasSelection():
                txt = cursor.selection().toPlainText()
            else:
                txt = self.toPlainText()
            QApplication.clipboard().setText(self._sanitize_for_clipboard(txt))

        # Create 'Copy As Markdown' action
        def do_copy_md():
            cursor = self.textCursor()
            if cursor.hasSelection():
                # Convert the selected display text back to storage markdown
                display_text = cursor.selection().toPlainText()
                try:
                    txt = self._from_display(display_text)
                except Exception:
                    txt = display_text
            else:
                # Use full-document conversion which handles images and link conversions
                txt = self.to_markdown()
            # For markdown, keep markup characters but still normalize control chars
            QApplication.clipboard().setText(self._sanitize_for_clipboard(txt))

        new_copy = menu.addAction("Copy")
        new_copy.triggered.connect(lambda checked=False: do_copy())
        md_action = menu.addAction("Copy As Markdown")
        md_action.triggered.connect(lambda checked=False: do_copy_md())
        # Remove original copy action if present
        if copy_act:
            menu.removeAction(copy_act)

    def _emit_ai_chat_send(self) -> None:
        payload = self._ai_chat_payload_text()
        if not payload:
            return
        self.aiChatSendRequested.emit(payload)

    def _emit_ai_chat_start(self) -> None:
        if not self._current_path:
            return
        self.aiChatRequested.emit(self._current_path)

    def _emit_ai_chat_focus(self) -> None:
        if not self._current_path:
            return
        self.aiChatPageFocusRequested.emit(self._current_path)

    def _show_ai_action_overlay(
        self,
        *,
        anchor: Optional[QPoint] = None,
        text_override: Optional[str] = None,
        has_chat: Optional[bool] = None,
        chat_active: Optional[bool] = None,
    ) -> None:
        if not self._ai_actions_enabled or not config.load_enable_ai_chats():
            return
        text = text_override if text_override is not None else self._ai_chat_payload_text()
        if not text:
            return
        self._suspend_vi_for_overlay()
        self._ai_action_overlay.open(
            text,
            has_chat=self._ai_chat_available if has_chat is None else has_chat,
            chat_active=getattr(self, "_ai_chat_active", False) if chat_active is None else chat_active,
            anchor=anchor,
        )

    def show_ai_overlay_with_text(
        self, text: str, *, anchor: Optional[QPoint] = None, has_chat: bool = True, chat_active: bool = True
    ) -> None:
        """Expose overlay for external callers (e.g., AI chat panel)."""
        self._show_ai_action_overlay(anchor=anchor, text_override=text, has_chat=has_chat, chat_active=chat_active)

    def _handle_ai_action_overlay(self, title: str, prompt: str) -> None:
        text = self._ai_action_overlay.text()
        self.aiActionRequested.emit(title, prompt, text)

    def _suspend_vi_for_overlay(self) -> None:
        if self._overlay_vi_mode_before is not None:
            return
        self._overlay_vi_mode_before = self._vi_mode_active
        if self._vi_mode_active:
            self.set_vi_mode(False)

    def _restore_vi_after_overlay(self) -> None:
        if self._overlay_vi_mode_before is None:
            return
        self.set_vi_mode(self._overlay_vi_mode_before)
        self._overlay_vi_mode_before = None

    def _add_ai_actions_entry(self, menu: QMenu) -> None:
        """Insert AI Actions at the top of a context menu (no chat shortcuts)."""
        if not (self._ai_actions_enabled and config.load_enable_ai_chats()):
            return
        ai_action = QAction("AI Actions...\tCtrl+Shift+P", self)
        ai_action.triggered.connect(self._show_ai_action_overlay)
        first = menu.actions()[0] if menu.actions() else None
        menu.insertAction(first, ai_action)
    
    def dragEnterEvent(self, event):  # type: ignore[override]
        """Accept drag events with file URLs."""
        mime = event.mimeData()
        print(f"[DragEnter] hasUrls: {mime.hasUrls()}, hasText: {mime.hasText()}")
        if mime.hasUrls():
            print(f"[DragEnter] URLs: {[url.toLocalFile() for url in mime.urls()]}")
            event.acceptProposedAction()
        elif mime.hasText():
            print(f"[DragEnter] Text: {mime.text()}")
            event.acceptProposedAction()
        else:
            print(f"[DragEnter] Formats: {mime.formats()}")
            super().dragEnterEvent(event)
    
    def dragMoveEvent(self, event):  # type: ignore[override]
        """Accept drag move events with file URLs."""
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
    
    def dropEvent(self, event):  # type: ignore[override]
        """Handle dropped files - insert as image or file link."""
        from pathlib import Path
        
        mime = event.mimeData()
        print(f"[Drop] hasUrls: {mime.hasUrls()}, hasText: {mime.hasText()}")
        
        file_path = None
        
        if mime.hasUrls():
            urls = mime.urls()
            print(f"[Drop] URLs: {[url.toLocalFile() for url in urls]}")
            if urls:
                file_path = Path(urls[0].toLocalFile())
        elif mime.hasText():
            # Try to parse text as file path
            text = mime.text().strip()
            print(f"[Drop] Text: {text}")
            if text.startswith('file://'):
                file_path = Path(text[7:])
            else:
                file_path = Path(text)
        
        if file_path and file_path.exists() and file_path.is_file():
            print(f"[Drop] Processing file: {file_path}")
            # Check if it's an image
            if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg']:
                # Insert as image
                print(f"[Drop] Inserting as image")
                self._insert_image_from_path(file_path.name, alt=file_path.stem)
            else:
                # Insert as file link
                print(f"[Drop] Inserting as file link")
                cursor = self.cursorForPosition(event.pos())
                self.setTextCursor(cursor)
                link_text = f"[{file_path.name}](./{file_path.name})"
                cursor.insertText(link_text)
            
            event.acceptProposedAction()
            try:
                self.attachmentDropped.emit(file_path.name)
            except Exception:
                pass
            return
        else:
            print(f"[Drop] File not found or not valid: {file_path}")
        
        super().dropEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        # Show pointing hand cursor when hovering over a link
        cursor = self.cursorForPosition(event.pos())
        link = self._link_under_cursor(cursor)
        if link:
            self.viewport().setCursor(Qt.PointingHandCursor)
            self.linkHovered.emit(link)
        else:
            self.viewport().setCursor(Qt.IBeamCursor)
            self.linkHovered.emit("")  # Empty string to clear status bar
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # type: ignore[override]
        # Single click on links to activate them
        if event.button() == Qt.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            md_info = self._markdown_link_at_cursor(cursor)
            link = md_info[3] if md_info else self._link_under_cursor(cursor)
            if link:
                self.linkActivated.emit(link)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        hit = self._image_at_position(event.pos())
        if hit:
            _, fmt = hit
            original = fmt.property(IMAGE_PROP_ORIGINAL) or fmt.name()
            resolved = self._resolve_image_path(str(original))
            if resolved and resolved.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved)))
            return
        # Double-click on links also activates them
        cursor = self.cursorForPosition(event.pos())
        link = self._link_under_cursor(cursor)
        if link:
            self.linkActivated.emit(link)
            return
        super().mouseDoubleClickEvent(event)

    def _toggle_task_at_cursor(self, pos=None) -> bool:
        cursor = self.cursorForPosition(pos) if pos is not None else self.textCursor()
        block = cursor.block()
        text = block.text()
        match = TASK_PATTERN.match(text)
        if not match:
            return False
        state = match.captured("state") or " "
        if pos is not None:
            rel = cursor.position() - block.position()
            if rel > len(match.captured("indent")) + 3:
                return False
        new_state = "x" if state.strip().lower() != "x" else " "
        start = len(match.captured("indent"))
        new_line = text[:start] + f"({new_state})" + text[start + 3 :]
        block_cursor = QTextCursor(block)
        block_cursor.select(QTextCursor.LineUnderCursor)
        block_cursor.insertText(new_line)
        return True

    def _handle_link_boundary_navigation(self, key: int) -> bool:
        """Handle Left/Right arrow navigation over link boundaries. Returns True if handled."""
        cursor = self.textCursor()
        block = cursor.block()
        rel_pos = cursor.position() - block.position()
        text = block.text()
        
        # Find link boundaries in display format: \x00link\x00label\x00
        idx = 0
        while idx < len(text):
            if text[idx] == '\x00':
                link_start = idx + 1
                link_end = text.find('\x00', link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find('\x00', label_start)
                    if label_end >= label_start:  # >= to handle empty labels
                        # Determine visible region: if label empty, show link; otherwise show label
                        if label_end == label_start:  # Empty label - link is visible
                            visible_start = link_start
                            visible_end = link_end
                        else:  # Non-empty label - label is visible
                            visible_start = label_start
                            visible_end = label_end
                        
                        if key == Qt.Key_Right:
                            # Moving right: if cursor is in the hidden part, jump to visible start
                            if idx <= rel_pos < visible_start:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + visible_start)
                                self.setTextCursor(new_cursor)
                                return True
                            # If at the end of visible part, move past the closing sentinel
                            elif rel_pos == visible_end:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + label_end + 1)
                                self.setTextCursor(new_cursor)
                                return True
                        
                        elif key == Qt.Key_Left:
                            # Moving left: if cursor is in the visible part, jump to before the link
                            if visible_start < rel_pos <= visible_end:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + idx)
                                self.setTextCursor(new_cursor)
                                return True
                            # If at start of visible part, jump before the link
                            elif rel_pos == visible_start:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + idx)
                                self.setTextCursor(new_cursor)
                                return True
                        
                        idx = label_end + 1
                        continue
            idx += 1
        
        return False

    def _trigger_history_navigation(self, qt_key: int) -> None:
        """Simulate Alt+Left/Right to leverage MainWindow history shortcuts."""
        window = self.window()
        if not window:
            return
        # Prefer calling the navigation helpers directly to avoid focus edge cases
        try:
            if qt_key == Qt.Key_Left and hasattr(window, "_navigate_history_back"):
                window._navigate_history_back()
                return
            if qt_key == Qt.Key_Right and hasattr(window, "_navigate_history_forward"):
                window._navigate_history_forward()
                return
        except Exception:
            pass
        press = QKeyEvent(QEvent.KeyPress, qt_key, Qt.AltModifier)
        release = QKeyEvent(QEvent.KeyRelease, qt_key, Qt.AltModifier)
        QApplication.sendEvent(window, press)
        QApplication.sendEvent(window, release)
    
    def _link_region_at_cursor(self, cursor: QTextCursor | None = None) -> Optional[tuple[str, int, int]]:
        """Return (link_text, start_pos, end_pos) for the link under the cursor, if any."""
        cursor = cursor or self.textCursor()
        block = cursor.block()
        rel = cursor.position() - block.position()
        text = block.text()
        
        # Check display-format links: \x00link\x00label\x00 (unified for HTTP and page links)
        idx = 0
        while idx < len(text):
            if text[idx] == '\x00':
                link_start = idx + 1
                link_end = text.find('\x00', link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find('\x00', label_start)
                    if label_end >= label_start:  # >= to handle empty labels
                        # Determine visible region
                        if label_end == label_start:  # Empty label - link is visible
                            visible_start = link_start
                            visible_end = link_end
                            if visible_end > visible_start and text[visible_end - 1] == "|":
                                visible_end -= 1
                            raw_link = text[link_start:link_end]
                            if raw_link.endswith("|"):
                                raw_link = raw_link[:-1]
                        else:  # Non-empty label - label is visible
                            visible_start = label_start
                            visible_end = label_end
                            raw_link = text[link_start:link_end]
                        
                        if visible_start <= rel < visible_end:
                            return (raw_link, visible_start, visible_end)
                        
                        idx = label_end + 1
                        continue
            idx += 1
        
        # Check storage-format wiki-style links: [link|label]
        # Find all [link|label] patterns (label can be empty)
        wiki_pattern = r"\[([^\]|]+)\|([^\]]*)\]"
        import re as regex_module
        for match in regex_module.finditer(wiki_pattern, text):
            link = match.group(1)
            label = match.group(2)
            # Label is visible part
            label_start = match.start() + 1 + len(link) + 1  # After '[link|'
            label_end = label_start + len(label)
            if label_start <= rel < label_end:
                return (link, label_start, label_end)

        # Check file links: [text](./file.ext) or [text](file.ext)
        file_iter = WIKI_FILE_LINK_PATTERN.globalMatch(text)
        while file_iter.hasNext():
            fm = file_iter.next()
            start = fm.capturedStart()
            label = fm.captured("text")
            label_start = start + 1
            label_end = label_start + len(label)
            if label_start <= rel < label_end:
                return (fm.captured("file"), label_start, label_end)
        
        # Check for plain HTTP URLs
        http_iter = HTTP_URL_PATTERN.globalMatch(text)
        while http_iter.hasNext():
            match = http_iter.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                return (match.captured("url"), start, end)
        
        # Check for CamelCase links
        iterator = CAMEL_LINK_PATTERN.globalMatch(block.text())
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                return (match.captured("link"), start, end)
        
        # Check for colon notation links (PageA:PageB:PageC)
        colon_iterator = COLON_LINK_PATTERN.globalMatch(block.text())
        while colon_iterator.hasNext():
            match = colon_iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                return (match.captured("link"), start, end)
        
        return None

    def _is_cursor_at_link_activation_point(self, cursor: QTextCursor) -> bool:
        """Check if cursor is positioned where Enter should activate a link vs insert newline."""
        region = self._link_region_at_cursor(cursor)
        if not region:
            return False
        _, start, end = region
        rel_pos = cursor.position() - cursor.block().position()
        # Only treat as activation when cursor is strictly inside the link (not touching the ends)
        return start < rel_pos < end

    def _link_under_cursor(self, cursor: QTextCursor | None = None) -> Optional[str]:
        region = self._link_region_at_cursor(cursor)
        return region[0] if region else None

    def _markdown_link_at_cursor(self, cursor: QTextCursor) -> Optional[tuple[int,int,str,str]]:
        """Return (start, end, text, link) for a wiki-style link under cursor, or None."""
        block = cursor.block()
        rel = cursor.position() - block.position()
        text = block.text()
        
        # Check display-format: \x00link\x00label\x00 (unified for both HTTP and page links)
        idx = 0
        while idx < len(text):
            if text[idx] == '\x00':
                link_start = idx + 1
                link_end = text.find('\x00', link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find('\x00', label_start)
                    if label_end >= label_start:  # >= to handle empty labels
                        raw_link = text[link_start:link_end]
                        visible_label = text[label_start:label_end]
                        if label_end == label_start:  # Empty label - link is visible
                            visible_start = link_start
                            visible_end = link_end
                            if visible_end > visible_start and text[visible_end - 1] == "|":
                                visible_end -= 1
                            visible_text = text[link_start:visible_end]
                            clean_link = raw_link[:-1] if raw_link.endswith("|") else raw_link
                        else:  # Non-empty label - label is visible
                            visible_start = label_start
                            visible_end = label_end
                            visible_text = visible_label
                            clean_link = raw_link

                        # Check if cursor is in the visible portion
                        if visible_start <= rel <= visible_end:
                            display_text = visible_text or clean_link
                            return (idx, label_end + 1, display_text, clean_link)

                        idx = label_end + 1
                        continue
            idx += 1
        
        # Check storage-format wiki-style links: [link|label]
        wiki_pattern = r"\[([^\]|]+)\|([^\]]*)\]"
        import re as regex_module
        for match in regex_module.finditer(wiki_pattern, text):
            start = match.start()
            end = match.end()
            link = match.group(1)
            label = match.group(2)
            # Determine visible part (label if non-empty, otherwise link)
            if label:
                visible_start = start + 1 + len(link) + 1  # After '[link|'
                visible_end = visible_start + len(label)
                visible_text = label
            else:
                visible_start = start + 1  # After '['
                visible_end = start + 1 + len(link)
                visible_text = link
            
            if visible_start <= rel <= visible_end:
                return (start, end, visible_text, link)
        
        # Check file links: [text](./file.ext) or [text](file.ext)
        fit = WIKI_FILE_LINK_PATTERN.globalMatch(text)
        while fit.hasNext():
            fm = fit.next()
            start = fm.capturedStart()
            end = start + fm.capturedLength()
            text_val = fm.captured("text")
            text_start = start + 1
            text_end = text_start + len(text_val)
            if text_start <= rel < text_end:
                return (start, end, text_val, fm.captured("file"))
        return None

    def _remove_link_at_cursor(self, cursor: QTextCursor) -> None:
        """Remove a link at the cursor position (remove the + prefix or convert colon notation to plain text)."""
        block = cursor.block()
        rel = cursor.position() - block.position()
        block_text = block.text()
        # If a markdown link, unwrap to just the display text
        md = self._markdown_link_at_cursor(cursor)
        if md:
            start, end, text_val, _ = md
            tc = QTextCursor(block)
            tc.setPosition(block.position() + start)
            tc.setPosition(block.position() + end, QTextCursor.KeepAnchor)
            tc.insertText(text_val)
            return
        
        # Check for CamelCase/plus-prefixed links (+PageName or +Projects)
        iterator = CAMEL_LINK_PATTERN.globalMatch(block_text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                # Remove the + prefix
                text_cursor = QTextCursor(block)
                text_cursor.setPosition(block.position() + start)
                text_cursor.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                # Replace with just the link text (without +)
                text_cursor.insertText(match.captured("link"))
                return
        
        # Check for colon notation links (PageA:PageB:PageC)
        colon_iterator = COLON_LINK_PATTERN.globalMatch(block_text)
        while colon_iterator.hasNext():
            match = colon_iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                # Convert colon notation to forward slash notation to break the link pattern
                text_cursor = QTextCursor(block)
                text_cursor.setPosition(block.position() + start)
                text_cursor.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                link_text = match.captured("link")
                text_cursor.insertText(link_text.replace(":", "/"))
                return

    def _edit_link_at_cursor(self, cursor: QTextCursor) -> None:
        """Open edit link dialog and replace link under cursor (supports markdown, plain colon, or HTTP link)."""
        from .edit_link_dialog import EditLinkDialog
        from PySide6.QtWidgets import QApplication
        # Find the main window to use as parent
        main_window = None
        widget = self
        while widget is not None:
            if widget.metaObject().className().endswith("MainWindow"):
                main_window = widget
                break
            widget = widget.parent()
        parent = main_window if main_window is not None else self.window()

        # Suspend Vi mode while dialog is open
        vi_was_active = self._vi_mode_active
        if vi_was_active:
            self.set_vi_mode(False)

        block = cursor.block()
        md = self._markdown_link_at_cursor(cursor)
        if md:
            start, end, text_val, link_val = md
        else:
            # Fallback: plain colon, CamelCase, or HTTP link
            link_val = self._link_under_cursor(cursor)
            if not link_val:
                return
            text_val = link_val
            # determine start/end of the match to replace
            rel = cursor.position() - block.position()
            # Check HTTP URL first
            http_it = HTTP_URL_PATTERN.globalMatch(block.text())
            rng = None
            while http_it.hasNext():
                m = http_it.next()
                s = m.capturedStart(); e = s + m.capturedLength()
                if s <= rel < e:
                    rng = (s,e)
                    break
            # Check colon notation
            if rng is None:
                it = COLON_LINK_PATTERN.globalMatch(block.text())
                while it.hasNext():
                    m = it.next()
                    s = m.capturedStart(); e = s + m.capturedLength()
                    if s <= rel < e:
                        rng = (s,e)
                        break
            # Check CamelCase
            if rng is None:
                it = CAMEL_LINK_PATTERN.globalMatch(block.text())
                while it.hasNext():
                    m = it.next()
                    s = m.capturedStart(); e = s + m.capturedLength()
                    if s <= rel < e:
                        rng = (s,e)
                        break
            if rng is None:
                return
            start, end = rng
        dlg = EditLinkDialog(link_to=link_val, link_text=text_val, parent=parent)
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.activateWindow()
        dlg.raise_()
        dlg.search_edit.setFocus()
        self.begin_dialog_block()
        try:
            if dlg.exec() == QDialog.Accepted:
                new_to = dlg.link_to() or link_val
                raw_label = dlg.link_text().strip()
                # Normalize target for both HTTP and colon links
                if new_to and new_to.startswith(("http://", "https://")):
                    new_to = self._normalize_external_link(new_to)
                elif new_to:
                    match = COLON_LINK_PATTERN.match(new_to)
                    if match.hasMatch():
                        new_to = ensure_root_colon_link(new_to)

                # Only keep a label if it differs from the target
                link_label = ""
                if raw_label:
                    match_left = raw_label.lstrip(":/")
                    target_left = new_to.lstrip(":/") if new_to else ""
                    if match_left != target_left:
                        link_label = raw_label
                tc = QTextCursor(block)
                tc.setPosition(block.position() + start)
                tc.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                tc.removeSelectedText()
                self.setTextCursor(tc)
                self.insert_link(new_to, link_label)
        finally:
            self.end_dialog_block()
            # Always restore focus to the editor after dialog closes
            QTimer.singleShot(0, self.setFocus)
            # Restore Vi mode if it was active
            if vi_was_active:
                self.set_vi_mode(True)
    
    def _copy_link_to_location(self, link_text: str | None = None, anchor_text: Optional[str] = None) -> Optional[str]:
        """Copy a link location as colon-notation to clipboard.

        Args:
            link_text: The link text (e.g., 'PageName' from +PageName, or 'PageA:PageB:PageC' from colon link).
                      If None, copies the current page's location.
            anchor_text: Optional heading text to append as anchor when copying current page.
        """
        if link_text:
            # If it's a colon notation link, use it as-is
            if ":" in link_text:
                colon_path = link_text
            else:
                # It's a relative link (+PageName), resolve it relative to current page
                if not self._current_path:
                    return None
                # Get current page's colon path
                current_colon = path_to_colon(self._current_path)
                if not current_colon:
                    # We're at root, just use the link text
                    colon_path = link_text
                else:
                    # Append the link to current page's path
                    colon_path = f"{current_colon}:{link_text}"
        else:
            # Copy current page's location
            if not self._current_path:
                return None
            colon_path = path_to_colon(self._current_path)

        if colon_path:
            colon_path = ensure_root_colon_link(colon_path)
            if anchor_text and "#" not in colon_path:
                # Slugify the anchor text (convert spaces to dashes, lowercase)
                slugified_anchor = heading_slug(anchor_text)
                colon_path = f"{colon_path}#{slugified_anchor}"
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(colon_path)
            self.linkCopied.emit(colon_path)
            # Keep vi clipboard in sync so 'p' in vi mode pastes this link
            self._vi_clipboard = colon_path
            return colon_path
        return None

    def _copy_link_or_heading(self) -> Optional[str]:
        """Copy link under cursor, otherwise current heading slugged link."""
        cursor = self.textCursor()
        # Prefer the raw link under cursor (preserves anchors)
        plain_link = self._link_under_cursor(cursor)
        if plain_link:
            return self._copy_link_to_location(link_text=plain_link)
        md_link = self._markdown_link_at_cursor(cursor)
        if md_link:
            # md_link = (start, end, text, link_target)
            target = md_link[3]
            return self._copy_link_to_location(link_text=target)
        heading_text = self.current_heading_text()
        if heading_text:
            return self._copy_link_to_location(link_text=None, anchor_text=heading_text)
        return None

    def copy_current_page_link(self) -> Optional[str]:
        """Copy current page (or heading) link and return the copied text."""
        heading_text = self.current_heading_text()
        return self._copy_link_to_location(link_text=None, anchor_text=heading_text)

    def _emit_cursor(self) -> None:
        cursor = self.textCursor()
        self.cursorMoved.emit(cursor.position())
        # Check if cursor is over a link and emit link path
        link = self._link_under_cursor(cursor)
        if link:
            self.linkHovered.emit(link)
        else:
            self.linkHovered.emit("")  # Empty string to clear status bar

    # --- Vi-mode cursor -------------------------------------------------
    def set_vi_block_cursor_enabled(self, enabled: bool) -> None:
        """Set whether vi-mode should show a block cursor. Does not affect vi-mode navigation."""
        self._vi_block_cursor_enabled = enabled
        # Refresh cursor display if currently in vi-mode
        if self._vi_mode_active:
            self._update_vi_cursor()

    def set_vi_mode_enabled(self, enabled: bool) -> None:
        """Globally enable or disable vi-style navigation."""
        if self._vi_feature_enabled == enabled:
            return
        self._vi_feature_enabled = enabled
        self._vi_replace_pending = False
        self._vi_pending_activation = False
        if enabled:
            if self.isVisible() and self._vi_has_painted:
                self._enter_vi_navigation_mode(force_emit=True)
            else:
                self._vi_pending_activation = True
                self._schedule_vi_activation()
        else:
            self._vi_insert_mode = False
            self.set_vi_mode(False)
            self.viInsertModeChanged.emit(False)

    def _schedule_vi_activation(self) -> None:
        if not self._vi_pending_activation or not self._vi_feature_enabled:
            return
        if self._vi_activation_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._activate_pending_vi)
            self._vi_activation_timer = timer
        if not self._vi_activation_timer.isActive():
            self._vi_activation_timer.start(0)

    def _activate_pending_vi(self) -> None:
        if not self._vi_feature_enabled or not self._vi_pending_activation:
            return
        if self._vi_paint_in_progress:
            if self._vi_activation_timer is not None:
                self._vi_activation_timer.start(10)
            return
        if not self._vi_has_painted or not self.isVisible():
            if self._vi_activation_timer is not None:
                self._vi_activation_timer.start(10)
            return
        self._vi_pending_activation = False
        self._enter_vi_navigation_mode(force_emit=True)

    def _enter_vi_navigation_mode(self, force_emit: bool = False) -> None:
        if not self._vi_feature_enabled:
            return
        emit_needed = force_emit or self._vi_insert_mode
        self._vi_insert_mode = False
        self._vi_replace_pending = False
        self.set_vi_mode(True)
        if emit_needed:
            self.viInsertModeChanged.emit(False)

    def _enter_vi_insert_mode(self) -> None:
        if not self._vi_feature_enabled:
            return
        if self._vi_insert_mode and not self._vi_replace_pending:
            return
        self._vi_insert_mode = True
        self.set_vi_mode(False)
        self.viInsertModeChanged.emit(True)

    def _handle_vi_escape(self) -> bool:
        if not self._vi_feature_enabled:
            return False
        if self._vi_replace_pending:
            self._vi_replace_pending = False
            self._enter_vi_navigation_mode()
            return True
        if self._vi_insert_mode:
            self._enter_vi_navigation_mode()
            return True
        return False

    def _handle_vi_keypress(self, event: QKeyEvent) -> bool:
        if not self._vi_feature_enabled:
            return False
        mods = event.modifiers() & ~Qt.KeypadModifier
        # Copy link/heading (Ctrl+Shift+L) even in vi navigation mode
        key = event.key()
        text_char = event.text() or ""
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) and key == Qt.Key_L:
            copied = self._copy_link_or_heading()
            window = self.window()
            try:
                if copied and window and hasattr(window, "statusBar"):
                    window.statusBar().showMessage(f"Copied link: {copied}", 2000)
            except Exception:
                pass
            if copied:
                self._vi_clipboard = copied
            return True
        # Allow Alt+H/J/K/L navigation even in vi mode
        if mods == Qt.AltModifier:
            if key == Qt.Key_H:
                self._trigger_history_navigation(Qt.Key_Left)
                return True
            if key == Qt.Key_L:
                self._trigger_history_navigation(Qt.Key_Right)
                return True
            if key == Qt.Key_J:
                self._trigger_history_navigation(Qt.Key_Down)
                return True
            if key == Qt.Key_K:
                self._trigger_history_navigation(Qt.Key_Up)
                return True
            return False
        if mods & Qt.ControlModifier:
            return False
        shift = bool(mods & Qt.ShiftModifier)
        other_mods = mods & ~(Qt.ShiftModifier)
        if other_mods:
            return False

        if self._vi_replace_pending:
            if key == Qt.Key_Escape:
                self._vi_replace_pending = False
                self._enter_vi_navigation_mode()
                return True
            text = event.text()
            if text:
                char = text[0]
                self._vi_replace_char(char)
                self._vi_last_edit = lambda ch=char: self._vi_replace_char(ch)
            self._vi_replace_pending = False
            self._enter_vi_navigation_mode()
            return True

        if self._vi_insert_mode:
            if key == Qt.Key_Escape and not shift:
                self._enter_vi_navigation_mode()
                return True
            return False

        # Navigation mode commands
        if key == Qt.Key_Escape:
            return True
        if key == Qt.Key_G:
            if shift:
                self._vi_move_to_file_end()
            else:
                self._vi_move_to_file_start()
            return True

        if shift and key == Qt.Key_N:
            # If we're at the last line of the document, move the selection
            # to the end of the current line instead of attempting to move down.
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid() or not block.next().isValid() or cursor.atEnd():
                # We're at the last line: select to the absolute end of document
                self._vi_move_cursor(QTextCursor.End, select=True)
            else:
                self._vi_move_cursor(QTextCursor.Down, select=True)
            return True

        if key == Qt.Key_Slash:
            self.request_find_bar(replace=False, backwards=False, seed=self._search_seed_query())
            return True
        if key == Qt.Key_Question:
            self.request_find_bar(replace=False, backwards=True, seed=self._search_seed_query())
            return True
        if key == Qt.Key_T and not shift:
            cursor_rect = self.cursorRect()
            viewport = self.viewport()
            prefer_above = False
            try:
                prefer_above = cursor_rect.center().y() > (viewport.height() // 2)
            except Exception:
                prefer_above = False
            global_point = viewport.mapToGlobal(cursor_rect.bottomLeft())
            self.headingPickerRequested.emit(global_point, prefer_above)
            return True
        if key == Qt.Key_N:
            self.search_repeat_last(reverse=False)
            return True
        if key == Qt.Key_Asterisk:
            self.search_word_under_cursor(backwards=False)
            return True
        if key == Qt.Key_NumberSign:
            self.search_word_under_cursor(backwards=True)
            return True

        if shift and key == Qt.Key_U:
            # If we're at the first line of the document, select to the absolute
            # start of the document (match Shift+Up behavior at top-of-file).
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid() or not block.previous().isValid() or cursor.atStart():
                self._vi_move_cursor(QTextCursor.Start, select=True)
            else:
                self._vi_move_cursor(QTextCursor.Up, select=True)
            return True

        if key == Qt.Key_Semicolon:
            self._vi_move_to_line_end(select=shift)
            return True
        if key == Qt.Key_Colon:
            self._open_vi_command_prompt()
            return True

        if key == Qt.Key_J and not shift:
            self._vi_move_cursor(QTextCursor.Down)
            return True
        if key == Qt.Key_K and not shift:
            self._vi_move_cursor(QTextCursor.Up)
            return True
        if key == Qt.Key_H:
            self._vi_move_cursor(QTextCursor.Left, select=shift)
            return True
        if key == Qt.Key_L:
            self._vi_move_cursor(QTextCursor.Right, select=shift)
            return True

        if key == Qt.Key_0 and not shift:
            self._vi_move_to_line_start()
            return True
        if key == Qt.Key_Q and not shift:
            self._vi_move_to_line_start()
            return True
        if key == Qt.Key_AsciiCircum and not shift:
            self._vi_move_to_first_nonblank()
            return True
        if key == Qt.Key_Dollar:
            self._vi_move_to_line_end(select=False)
            return True

        if key == Qt.Key_W and not shift:
            self._vi_move_word(forward=True)
            return True
        if key == Qt.Key_B and not shift:
            self._vi_move_word(forward=False)
            return True
        if key == Qt.Key_C and not shift:
            self._vi_copy_to_buffer()
            return True

        if key == Qt.Key_I and not shift:
            self._vi_insert_before_cursor()
            return True
        if key == Qt.Key_A and not shift:
            self._vi_insert_after_cursor()
            return True
        if key == Qt.Key_O:
            if shift:
                self._vi_open_line_above()
            else:
                self._vi_open_line_below()
            return True

        if (key == Qt.Key_P or text_char.lower() == "p") and not shift:
            inserted = self._vi_paste_buffer()
            if inserted:
                self._vi_last_edit = lambda text=inserted: self._vi_insert_text(text)
            return True

        if key == Qt.Key_X and not shift:
            self._vi_cut_selection_or_char()
            self._vi_last_edit = self._vi_cut_selection_or_char
            return True
        if key == Qt.Key_D and not shift:
            self._vi_delete_selection_or_line()
            self._vi_last_edit = self._vi_delete_selection_or_line
            return True
        if key == Qt.Key_R and not shift:
            self._vi_replace_pending = True
            self._enter_vi_insert_mode()
            return True
        if key == Qt.Key_U and not shift:
            self._undo_or_status()
            return True
        if key == Qt.Key_Y and not shift:
            self._redo_or_status()
            return True
        if key == Qt.Key_Period and not shift:
            self._vi_repeat_last_edit()
            return True

        if key in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Return, Qt.Key_Enter):
            if key in (Qt.Key_Return, Qt.Key_Enter):
                cursor = self.textCursor()
                if self._is_cursor_at_link_activation_point(cursor):
                    link = self._link_under_cursor(cursor)
                    if link:
                        self.linkActivated.emit(link)
            return True

        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            return False

        text = event.text()
        if text:
            return True
        return False

    def _vi_move_cursor(self, op: QTextCursor.MoveOperation, select: bool = False, count: int = 1) -> None:
        cursor = self.textCursor()
        mode = QTextCursor.KeepAnchor if select else QTextCursor.MoveAnchor
        cursor.movePosition(op, mode, max(1, count))
        cursor = self._clamp_cursor(cursor)
        self.setTextCursor(cursor)
        if not select and op in (QTextCursor.Left, QTextCursor.Right):
            # Skip hidden link sentinels so vi-mode can enter/exit links cleanly
            self._handle_link_boundary_navigation(Qt.Key_Left if op == QTextCursor.Left else Qt.Key_Right)

    def _vi_move_to_line_start(self) -> None:
        self._vi_move_cursor(QTextCursor.StartOfLine)

    def _vi_move_to_line_end(self, select: bool) -> None:
        self._vi_move_cursor(QTextCursor.EndOfLine, select=select)

    def _vi_move_to_first_nonblank(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        stripped = text.lstrip(" \t")
        offset = len(text) - len(stripped)
        cursor.setPosition(block.position() + offset)
        cursor = self._clamp_cursor(cursor)
        self.setTextCursor(cursor)

    def _vi_move_to_file_start(self) -> None:
        self._vi_move_cursor(QTextCursor.Start)

    def _vi_move_to_file_end(self) -> None:
        self._vi_move_cursor(QTextCursor.End)

    def _open_vi_command_prompt(self) -> None:
        """Handle minimal vi-style command input (:%s/old/new/g)."""
        try:
            cmd, ok = QInputDialog.getText(self, "Command", ":")
        except Exception:
            return
        if not ok:
            return
        cmd_str = (cmd or "").strip()
        if cmd_str.startswith("%s/") and cmd_str.endswith("/g"):
            body = cmd_str[3:-2]
            if "/" not in body:
                self._status_message("Invalid substitution command.")
                return
            old, new = body.split("/", 1)
            if not old:
                self._status_message("Empty search pattern.")
                return
            self.search_replace_all(old, new)
            return
        self._status_message("Unknown command.")

    def _vi_move_word(self, forward: bool) -> None:
        op = QTextCursor.WordRight if forward else QTextCursor.WordLeft
        self._vi_move_cursor(op)

    def _system_clipboard_text(self) -> str:
        try:
            return QGuiApplication.clipboard().text() or ""
        except Exception:
            return ""

    def _system_clipboard_set(self, text: str) -> None:
        try:
            QGuiApplication.clipboard().setText(text)
        except Exception:
            pass

    def _undo_or_status(self) -> None:
        try:
            doc = self.document()
            if not doc or not doc.isUndoAvailable():
                self._status_message("Nothing to undo.")
                return
            cursor_before = QTextCursor(self.textCursor())
            # Perform the normal undo first
            self.undo()
            # If a one-shot placeholder remains (it was inserted outside the undo stack),
            # replace it with the original prompt so Undo fully restores the prior state.
            try:
                ph_start = getattr(self, "_one_shot_placeholder_start", None)
                ph_len = getattr(self, "_one_shot_placeholder_len", None)
                orig = getattr(self, "_one_shot_original_text", None)
                if ph_start is not None and ph_len is not None:
                    full = self.toPlainText()
                    # Ensure indices are within range
                    if ph_start >= 0 and ph_start + ph_len <= len(full):
                        snippet = full[ph_start: ph_start + ph_len]
                        if snippet == "Executing one shot prompt...":
                            # Replace placeholder with original text without adding to undo stack
                            try:
                                doc.setUndoRedoEnabled(False)
                            except Exception:
                                pass
                            try:
                                sel = QTextCursor(doc)
                                sel.setPosition(ph_start)
                                sel.setPosition(ph_start + ph_len, QTextCursor.KeepAnchor)
                                sel.beginEditBlock()
                                sel.removeSelectedText()
                                if orig:
                                    sel.insertText(orig)
                                sel.endEditBlock()
                            except Exception:
                                pass
                            try:
                                # restore undo state
                                doc.setUndoRedoEnabled(True)
                            except Exception:
                                pass
                            # Clear one-shot markers
                            try:
                                del self._one_shot_placeholder_start
                            except Exception:
                                pass
                            try:
                                del self._one_shot_placeholder_len
                            except Exception:
                                pass
                            try:
                                del self._one_shot_original_text
                            except Exception:
                                pass
                            return
            except Exception:
                pass
            if not doc.isUndoAvailable():
                # Reached the start of the stack; keep cursor stable to avoid jumps.
                self.setTextCursor(cursor_before)
        except Exception:
            pass

    def _redo_or_status(self) -> None:
        try:
            doc = self.document()
            if not doc or not doc.isRedoAvailable():
                self._status_message("Nothing to redo.")
                return
            cursor_before = QTextCursor(self.textCursor())
            self.redo()
            if not doc.isRedoAvailable():
                # Reached the end of the stack; keep cursor stable to avoid jumps.
                self.setTextCursor(cursor_before)
        except Exception:
            pass

    def _vi_copy_to_buffer(self) -> bool:
        text = self._vi_selected_text_or_line()
        if text is None:
            return False
        self._vi_clipboard = text
        self._system_clipboard_set(text)
        return True

    def _vi_selected_text_or_line(self) -> Optional[str]:
        cursor = QTextCursor(self.textCursor())
        if cursor.hasSelection():
            text = cursor.selectedText()
        else:
            block = cursor.block()
            if not block.isValid():
                return None
            cursor.select(QTextCursor.LineUnderCursor)
            text = cursor.selectedText()
        normalized = text.replace("\u2029", "\n")
        return normalized if normalized else None

    def _vi_cut_selection_or_char(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        text = ""
        if cursor.hasSelection():
            text = cursor.selectedText()
            cursor.removeSelectedText()
        else:
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
            text = cursor.selectedText()
            if text:
                cursor.removeSelectedText()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        normalized = text.replace("\u2029", "\n") if text else ""
        if normalized:
            self._vi_clipboard = normalized
            self._system_clipboard_set(normalized)

    def _vi_insert_text(self, text: str) -> None:
        if not text:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _vi_paste_buffer(self) -> Optional[str]:
        sys_clip = self._system_clipboard_text()
        if sys_clip:
            self._vi_clipboard = sys_clip
        if not self._vi_clipboard:
            return None
        self._vi_insert_text(self._vi_clipboard)
        return self._vi_clipboard

    def _vi_delete_line(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.LineUnderCursor)
        cursor.removeSelectedText()
        if not cursor.atEnd():
            cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
    
    def _vi_delete_selection_or_line(self) -> None:
        """Delete current selection; if none, delete the current line. Does not yank."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        else:
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            if not cursor.atEnd():
                cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _vi_replace_char(self, char: str) -> None:
        if not char:
            return
        cursor = self.textCursor()
        if cursor.atEnd():
            return
        cursor.beginEditBlock()
        cursor.deleteChar()
        cursor.insertText(char)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _vi_open_line_below(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.EndOfLine)
        self.setTextCursor(cursor)
        if not self._handle_enter_indent_same_level():
            cursor = self.textCursor()
            cursor.insertBlock()
            self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _vi_open_line_above(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        indent = text[: len(text) - len(text.lstrip(" \t"))]
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.insertText(f"{indent}\n")
        cursor.movePosition(QTextCursor.Up)
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, len(indent))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _vi_insert_before_cursor(self) -> None:
        self._enter_vi_insert_mode()

    def _vi_insert_after_cursor(self) -> None:
        cursor = self.textCursor()
        if not cursor.atEnd():
            cursor.movePosition(QTextCursor.Right)
            self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _vi_repeat_last_edit(self) -> None:
        if self._vi_last_edit:
            self._vi_last_edit()

    def set_vi_mode(self, active: bool) -> None:
        """Enable or disable vi-mode cursor styling (pink block)."""
        if active and not self._vi_feature_enabled:
            active = False
        if self._vi_mode_active == active:
            return
        self._vi_mode_active = active
        # Disable cursor blinking while in vi-mode to avoid flicker with overlay (only if block cursor enabled)
        if active and self._vi_block_cursor_enabled:
            if self._vi_saved_flash_time is None:
                try:
                    self._vi_saved_flash_time = QGuiApplication.cursorFlashTime()
                except Exception:
                    self._vi_saved_flash_time = 1000
            try:
                QGuiApplication.setCursorFlashTime(0)
            except Exception:
                pass
        else:
            if self._vi_saved_flash_time is not None:
                try:
                    QGuiApplication.setCursorFlashTime(self._vi_saved_flash_time)
                except Exception:
                    pass
            self._vi_saved_flash_time = None
            self._vi_last_cursor_pos = -1
        self._update_vi_cursor()
        # Ensure editor focus when vi mode is toggled and no dialog is open
        if not self._dialog_block_input:
            self.setFocus()

    def _maybe_update_vi_cursor(self) -> None:
        if not self._vi_mode_active or not self._vi_block_cursor_enabled:
            return
        pos = self.textCursor().position()
        if pos == self._vi_last_cursor_pos:
            return
        self._vi_last_cursor_pos = pos
        self._update_vi_cursor()

    def _clamp_cursor(self, cursor: QTextCursor) -> QTextCursor:
        """Clamp cursor position to the document length to avoid out-of-range warnings."""
        try:
            length = len(self.toPlainText())
        except Exception:
            length = cursor.document().characterCount()
        safe = max(0, min(cursor.position(), max(0, length)))
        if safe != cursor.position():
            cursor.setPosition(safe)
        return cursor

    def _ensure_cursor_margin(self) -> None:
        """Keep a small margin at the bottom of the viewport for visibility while editing."""
        sb = self.verticalScrollBar()
        if not sb:
            return
        rect = self.cursorRect()
        viewport = self.viewport()
        if not viewport:
            return
        view_h = viewport.height()
        margin = 48
        overshoot = rect.bottom() - (view_h - margin)
        if overshoot > 0:
            sb.setValue(min(sb.maximum(), sb.value() + overshoot))
    def _update_vi_cursor(self) -> None:
        if not self._vi_mode_active or not self._vi_block_cursor_enabled:
            # Clear any vi-mode selection overlay but preserve other selections (e.g., flashes)
            remaining = [s for s in self.extraSelections() if s.format.property(self._VI_EXTRA_KEY) is None]
            self.setExtraSelections(remaining)
            return
        cursor = self.textCursor()
        # Don't draw block cursor overlay while there's an active selection
        if cursor.hasSelection():
            remaining = [s for s in self.extraSelections() if s.format.property(self._VI_EXTRA_KEY) is None]
            self.setExtraSelections(remaining)
            return
        block_cursor = QTextCursor(cursor)
        if not block_cursor.atEnd():
            # Select the character under the caret to form a block
            block_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        extra = QTextEdit.ExtraSelection()
        extra.cursor = block_cursor
        fmt = extra.format
        fmt.setBackground(QColor("#b259ff"))  # purple block
        fmt.setForeground(QColor("#111"))     # dark text for contrast
        fmt.setProperty(QTextFormat.FullWidthSelection, False)
        fmt.setProperty(self._VI_EXTRA_KEY, True)
        existing = [s for s in self.extraSelections() if s.format.property(self._VI_EXTRA_KEY) is None]
        existing.append(extra)
        self.setExtraSelections(existing)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.viewport():
            if event.type() in (QEvent.Paint, QEvent.UpdateRequest, QEvent.FocusIn, QEvent.FocusOut):
                if self._vi_mode_active:
                    self._update_vi_cursor()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self.viewportResized.emit()
        # Reapply scroll-past-end margin on resize
        self._apply_scroll_past_end_margin()

    def _to_display(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            symbol = "☑" if match.group(2).strip().lower() == "x" else "☐"
            return f"{match.group(1)}{symbol}{match.group(3)}"

        converted = TASK_LINE_PATTERN.sub(repl, text)
        converted = HEADING_MARK_PATTERN.sub(self._encode_heading, converted)
        # Transform wiki-style links: [link|label] → \x00link\x00label\x00
        converted = WIKI_LINK_STORAGE_PATTERN.sub(self._encode_wiki_link, converted)
        # Transform bullets: * → •
        converted = BULLET_STORAGE_PATTERN.sub(r"\1• ", converted)
        return converted

    def _from_display(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            state = "x" if match.group(2) == "☑" else " "
            return f"{match.group(1)}({state}){match.group(3)}"

        # Restore wiki-style links: \x00link\x00label\x00 → [link|label]
        restored = WIKI_LINK_DISPLAY_PATTERN.sub(self._decode_wiki_link, text)
        # Drop duplicated link tails that sometimes get re-appended after decoding.
        def _dedupe_tail(m: re.Match[str]) -> str:
            link = m.group("link")
            label = m.group("label")
            tail = m.group("tail")
            if tail and link.endswith(tail):
                return f"[{link}|{label}]"
            return m.group(0)

        restored = WIKI_LINK_DUPLICATE_TAIL_PATTERN.sub(_dedupe_tail, restored)
        restored = HEADING_DISPLAY_PATTERN.sub(self._decode_heading, restored)
        restored = DISPLAY_TASK_PATTERN.sub(repl, restored)
        # Restore bullets: • → *
        restored = BULLET_DISPLAY_PATTERN.sub(r"\1* ", restored)
        return restored

    def _encode_heading(self, match: re.Match[str]) -> str:
        indent, hashes, _, body = match.groups()
        level = min(len(hashes), HEADING_MAX_LEVEL)
        sentinel = heading_sentinel(level)
        return f"{indent}{sentinel}{body}"

    def _decode_heading(self, match: re.Match[str]) -> str:
        indent, marker, body = match.groups()
        level = heading_level_from_char(marker)
        if not level:
            return match.group(0)
        clean_body = body.lstrip()
        spacer = " " if clean_body else ""
        hashes = "#" * level
        return f"{indent}{hashes}{spacer}{clean_body}"

    def _encode_wiki_link(self, match: re.Match[str]) -> str:
        """Convert [link|label] to hidden format, preserving empty-label marker."""
        link = match.group("link")
        label = match.group("label")
        if not label:
            # Inject a trailing pipe so plain-text round trips keep the wiki delimiter.
            return f"\x00{link}|\x00\x00"
        return f"\x00{link}\x00{label}\x00"

    def _decode_wiki_link(self, match: re.Match[str]) -> str:
        """Convert hidden format \x00link\x00label\x00 back to [link|label]"""
        link = match.group("link")
        label = match.group("label")
        if not label and link.endswith("|"):
            link = link[:-1]
        return f"[{link}|{label}]"

    def refresh_heading_outline(self) -> None:
        """Force computation of heading outline immediately."""
        self._emit_heading_outline()

    def _schedule_heading_outline(self) -> None:
        if self._display_guard:
            return
        self._heading_timer.start()

    def _emit_heading_outline(self) -> None:
        outline: list[dict] = []
        block = self.document().firstBlock()
        while block.isValid():
            text = block.text()
            stripped = text.lstrip()
            if stripped:
                level = heading_level_from_char(stripped[0])
                if level:
                    title = stripped[1:].strip()
                    cursor = QTextCursor(block)
                    outline.append(
                        {
                            "level": level,
                            "title": title,
                            "line": block.blockNumber() + 1,
                            "position": cursor.position(),
                        }
                    )
            block = block.next()
        self._heading_outline = outline
        self.headingsChanged.emit(outline)

    def jump_to_anchor(self, anchor: str) -> bool:
        slug = heading_slug(anchor)
        if not slug:
            return False
        for entry in self._heading_outline:
            if heading_slug(entry.get("title", "")) == slug:
                cursor = self.textCursor()
                cursor.setPosition(int(entry.get("position", 0)))
                self.setTextCursor(cursor)
                self.ensureCursorVisible()
                return True
        return False

    def current_heading_slug(self) -> Optional[str]:
        """Return slug of heading on current line, if any."""
        if not self._heading_outline:
            return None
        line_no = self.textCursor().blockNumber() + 1
        for entry in self._heading_outline:
            if int(entry.get("line", 0)) == line_no:
                slug = heading_slug(entry.get("title", ""))
                return slug or None
        return None

    def current_heading_text(self) -> Optional[str]:
        """Return original text of heading on current line, if any."""
        if not self._heading_outline:
            return None
        line_no = self.textCursor().blockNumber() + 1
        for entry in self._heading_outline:
            if int(entry.get("line", 0)) == line_no:
                return entry.get("title", "") or None
        return None

    def _refresh_display(self) -> None:
        """Force full document re-render to apply display transformations.
        
        This converts storage format (markdown syntax) to display format (with hidden syntax).
        Used after inserting/editing links or pasting content that may contain links.
        """
        self._display_guard = True
        current_text = self.toPlainText()
        # First convert FROM display back to storage (in case text is already partially in display format)
        storage_text = self._from_display(current_text)
        # Convert +CamelCase links immediately so display is updated without waiting for save
        storage_text = self._convert_camelcase_links(storage_text)
        storage_text = self._wrap_plain_http_links(storage_text)
        # Then convert to display format
        display_text = self._to_display(storage_text)
        old_cursor_pos = self.textCursor().position()
        self.document().setPlainText(display_text)
        # Restore cursor position (approximately)
        new_cursor = self.textCursor()
        new_cursor.setPosition(min(old_cursor_pos, len(display_text)))
        self.setTextCursor(new_cursor)
        self._render_images(display_text)
        self._display_guard = False
        self._schedule_heading_outline()
        self._apply_scroll_past_end_margin()
        self._apply_scroll_past_end_margin()

    def _enforce_display_symbols(self) -> None:
        """Safely render display symbols on the current line only.

        Avoid full-document rewrites to preserve inline image fragments and
        prevent cursor jumps or spurious newlines when typing.
        """
        if self._display_guard:
            return

        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return

        original = block.text()

        # 1) Checkbox: ( ) / (x) at start-of-line → ☐ / ☑
        def task_repl(match: re.Match[str]) -> str:
            symbol = "☑" if match.group(2).strip().lower() == "x" else "☐"
            return f"{match.group(1)}{symbol}{match.group(3)}"

        line = TASK_LINE_PATTERN.sub(task_repl, original)

        # 2) Heading marks: #'s → sentinel on this line only
        line = HEADING_MARK_PATTERN.sub(self._encode_heading, line)

        # 3) Wiki-style links: [link|label] → \x00link\x00label\x00
        line = WIKI_LINK_STORAGE_PATTERN.sub(self._encode_wiki_link, line)
        
        # 4) Bullet conversion: Convert "* " at start of line (after whitespace) to bullet
        # Only convert when user types "* " followed by space
        stripped = line.lstrip()
        if stripped.startswith("* ") and len(stripped) > 2:
            # Check if this is a new bullet being typed (cursor should be after "* ")
            abs_pos = cursor.position()
            line_start = block.position()
            rel_pos = abs_pos - line_start
            indent = line[:len(line) - len(stripped)]
            bullet_pos = len(indent) + 2  # Position after "* "
            
            # Only convert if cursor is near the bullet marker position
            # This prevents conversion when just navigating through existing bullets
            if abs(rel_pos - bullet_pos) <= 2:
                # Convert the * to a bullet point (•)
                line = indent + "• " + stripped[2:]

        if line == original:
            return

        # Preserve caret relative to line start
        abs_pos = cursor.position()
        line_start = block.position()
        rel_pos = max(0, abs_pos - line_start)
        delta = len(line) - len(original)

        self._display_guard = True
        try:
            line_cursor = QTextCursor(block)
            line_cursor.select(QTextCursor.LineUnderCursor)
            line_cursor.insertText(line)

            # Restore caret with delta applied and clamped to line length
            new_block = self.document().findBlock(line_start)
            new_len = max(0, new_block.length() - 1)  # exclude implicit newline
            new_rel = min(max(0, rel_pos + delta), new_len)
            new_abs = line_start + new_rel
            c = self.textCursor()
            c.setPosition(new_abs)
            self.setTextCursor(c)
        finally:
            self._display_guard = False
        self._schedule_heading_outline()

    def _restore_cursor_position(self, position: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(min(position, len(self.toPlainText())))
        self.setTextCursor(cursor)
    
    # --- Bullet list handling ---
    
    def _is_bullet_line(self, text: str) -> tuple[bool, str, str]:
        """Check if line is a bullet and return (is_bullet, indent, content_after_bullet).
        
        Returns:
            (True, indent_str, content) if bullet line
            (False, "", "") otherwise
        """
        stripped = text.lstrip()
        indent = text[:len(text) - len(stripped)]
        
        # Check for bullet patterns: "• " or "* " only (exclude -/+ to avoid false bullets)
        if stripped.startswith(("• ", "* ")):
            content = stripped[2:]
            return (True, indent, content)
        
        return (False, "", "")
    
    def _handle_bullet_enter(self) -> bool:
        """Handle Enter key in bullet mode. Returns True if handled."""
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        
        is_bullet, indent, content = self._is_bullet_line(text)
        if not is_bullet:
            return False
        
        # Get cursor position relative to line start
        rel_pos = cursor.position() - block.position()
        bullet_start = len(indent) + 2  # After bullet marker
        
        # If bullet line is empty (just the bullet), exit bullet mode
        if not content.strip():
            # Remove the bullet marker and stay on same line
            cursor.beginEditBlock()
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(indent)  # Keep indent but remove bullet
            cursor.endEditBlock()
            return True
        
        # Insert new line with bullet (use • for visual consistency)
        cursor.beginEditBlock()
        cursor.insertText("\n" + indent + "• ")
        cursor.endEditBlock()
        return True

    def _is_task_line(self, text: str) -> tuple[bool, str, str, str]:
        import re as _re
        m = _re.match(r"^(\s*)\((?P<state>[ xX]?)\)\s*(.*)$", text)
        if m:
            indent = m.group(1) or ""
            state = m.group("state") or " "
            content = m.group(3) or ""
            return True, indent, state, content
        m = _re.match(r"^(\s*)([☐☑])\s*(.*)$", text)
        if m:
            indent = m.group(1) or ""
            state = "x" if m.group(2) == "☑" else " "
            content = m.group(3) or ""
            return True, indent, state, content
        return False, "", "", ""

    def _handle_task_enter(self, indent: str, content: str) -> bool:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if not content.strip():
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(indent)
            cursor.setPosition(cursor.block().position() + len(indent))
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        cursor.movePosition(QTextCursor.EndOfBlock)
        cursor.insertBlock()
        marker = "☐ "
        cursor.insertText(indent + marker)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _maybe_expand_checkbox(self) -> bool:
        import re as _re
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        pos = cursor.positionInBlock()
        prefix = text[:pos]
        state_char = " "
        m = _re.match(r"^(\s*)\(\)\s*$", prefix) or _re.match(r"^(\s*)\(\)\s+\s*$", prefix)
        if not m:
            m = _re.match(r"^(\s*)\(\+\)\s*$", prefix) or _re.match(r"^(\s*)\(\+\)\s+\s*$", prefix)
            if m:
                state_char = "x"
        if not m:
            return False
        indent = m.group(1) or ""
        remainder = text[pos:]
        marker = "☐ " if state_char == " " else "☑ "
        new_prefix = f"{indent}{marker}"
        new_text = new_prefix + remainder.lstrip()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.LineUnderCursor)
        cursor.removeSelectedText()
        cursor.insertText(new_text)
        cursor.setPosition(block.position() + len(new_prefix))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _handle_task_indent(self, indent: str) -> bool:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.LineUnderCursor)
        line_text = cursor.selectedText()
        cursor.removeSelectedText()
        new_indent = indent + "  "
        marker_len = 2  # '☐ '
        cursor.insertText(new_indent + line_text[len(indent):])
        cursor.setPosition(cursor.block().position() + len(new_indent) + marker_len)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _handle_task_dedent(self, indent: str) -> bool:
        if len(indent) < 2:
            return False
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.LineUnderCursor)
        line_text = cursor.selectedText()
        dedent = indent[:-2]
        cursor.removeSelectedText()
        marker_len = 2
        cursor.insertText(dedent + line_text[len(indent):])
        cursor.setPosition(cursor.block().position() + len(dedent) + marker_len)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True
    
    def _handle_bullet_indent(self) -> bool:
        """Handle Tab key for bullet indentation. Returns True if handled.
        
        If the bullet has child bullets (more indented bullets following it),
        they will be indented along with the parent.
        """
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        
        is_bullet, indent, content = self._is_bullet_line(text)
        if not is_bullet:
            return False
        
        # Get current indent level
        current_indent_len = len(indent)
        
        # Find all child bullets (lines with greater indent following this line)
        children_blocks = []
        next_block = block.next()
        while next_block.isValid():
            next_text = next_block.text()
            next_is_bullet, next_indent, next_content = self._is_bullet_line(next_text)
            
            if next_is_bullet and len(next_indent) > current_indent_len:
                # This is a child bullet
                children_blocks.append(next_block)
                next_block = next_block.next()
            else:
                # No longer a child (same or less indent, or not a bullet)
                break
        
        # Save cursor position relative to end of line
        rel_from_end = len(text) - (cursor.position() - block.position())
        
        # Begin edit block to make all changes atomic
        cursor.beginEditBlock()
        
        # Indent the current line
        new_indent = indent + "  "
        new_line = new_indent + "• " + content
        
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        
        # Indent all child bullets
        for child_block in children_blocks:
            child_text = child_block.text()
            child_is_bullet, child_indent, child_content = self._is_bullet_line(child_text)
            if child_is_bullet:
                new_child_indent = child_indent + "  "
                new_child_line = new_child_indent + "• " + child_content
                
                child_cursor = QTextCursor(child_block)
                child_cursor.select(QTextCursor.LineUnderCursor)
                child_cursor.insertText(new_child_line)
        
        # Restore cursor position (adjusted for new indent)
        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + 2, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True
    
    def _handle_bullet_dedent(self) -> bool:
        """Handle Shift+Tab key for bullet dedentation. Returns True if handled.
        
        If the bullet has child bullets (more indented bullets following it),
        they will be dedented along with the parent.
        """
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        
        is_bullet, indent, content = self._is_bullet_line(text)
        if not is_bullet:
            return False
        
        # Can't dedent if already at zero indent
        if len(indent) == 0:
            return True  # Still consume the event
        
        # Get current indent level
        current_indent_len = len(indent)
        
        # Find all child bullets (lines with greater indent following this line)
        children_blocks = []
        next_block = block.next()
        while next_block.isValid():
            next_text = next_block.text()
            next_is_bullet, next_indent, next_content = self._is_bullet_line(next_text)
            
            if next_is_bullet and len(next_indent) > current_indent_len:
                # This is a child bullet
                children_blocks.append(next_block)
                next_block = next_block.next()
            else:
                # No longer a child (same or less indent, or not a bullet)
                break
        
        # Remove up to two spaces from indent
        if len(indent) >= 2:
            new_indent = indent[:-2]
        else:
            new_indent = ""
        
        # Save cursor position relative to end of line
        rel_from_end = len(text) - (cursor.position() - block.position())
        
        # Begin edit block to make all changes atomic
        cursor.beginEditBlock()
        
        # Dedent the current line
        new_line = new_indent + "• " + content
        
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        
        # Dedent all child bullets (if they have at least 2 spaces to remove)
        for child_block in children_blocks:
            child_text = child_block.text()
            child_is_bullet, child_indent, child_content = self._is_bullet_line(child_text)
            if child_is_bullet and len(child_indent) >= 2:
                new_child_indent = child_indent[:-2]
                new_child_line = new_child_indent + "• " + child_content
                
                child_cursor = QTextCursor(child_block)
                child_cursor.select(QTextCursor.LineUnderCursor)
                child_cursor.insertText(new_child_line)
        
        # Restore cursor position (adjusted for new indent)
        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + 2, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True

    # --- Generic indentation helpers (non-bullet) ---

    def _handle_enter_indent_same_level(self) -> bool:
        """On Enter, continue the next line at the same leading indentation as the current line.

        Applies only when not in a bullet context. Returns True if handled.
        """
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return False
        text = block.text()
        # Determine leading whitespace (tabs and/or spaces)
        stripped = text.lstrip(" \t")
        indent = text[: len(text) - len(stripped)]
        # Simply insert a newline plus the indent
        cursor.beginEditBlock()
        cursor.insertText("\n" + indent)
        cursor.endEditBlock()
        return True

    def _dedent_line_text(self, text: str, indent_unit: Optional[str] = None) -> tuple[str, int]:
        """Return (new_text, removed_chars) after removing one indent unit from the start."""
        indent_unit = indent_unit or self._indent_unit or "    "
        if not text:
            return text, 0
        if text.startswith("\t"):
            return text[1:], 1
        if indent_unit and text.startswith(indent_unit):
            return text[len(indent_unit) :], len(indent_unit)
        removed = 0
        max_remove = len(indent_unit) if indent_unit else 4
        while removed < max_remove and removed < len(text) and text[removed] == " ":
            removed += 1
        if removed:
            return text[removed:], removed
        return text, 0

    def _apply_indent_to_selection(self, dedent: bool) -> bool:
        """Indent or dedent all blocks touched by the current selection."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.document()
        start_block = doc.findBlock(start)
        end_block = doc.findBlock(max(start, end - 1))
        if not start_block.isValid() or not end_block.isValid():
            return False
        blocks: list = []
        block = start_block
        while block.isValid():
            blocks.append(block)
            if block == end_block:
                break
            block = block.next()
        if not blocks:
            return False
        indent_unit = self._indent_unit or "    "
        cursor.beginEditBlock()
        start_delta = 0
        end_delta = 0
        modified = False
        for idx, blk in enumerate(blocks):
            line_cursor = QTextCursor(self.document())
            line_start = blk.position()
            text = blk.text()
            if dedent:
                new_line, removed = self._dedent_line_text(text, indent_unit)
                if removed == 0:
                    continue
                line_cursor.setPosition(line_start)
                line_cursor.setPosition(line_start + removed, QTextCursor.KeepAnchor)
                line_cursor.removeSelectedText()
                modified = True
                end_delta -= removed
                if idx == 0:
                    start_delta -= removed
            else:
                line_cursor.setPosition(line_start)
                line_cursor.insertText(indent_unit)
                modified = True
                end_delta += len(indent_unit)
                if idx == 0:
                    start_delta += len(indent_unit)
        cursor.endEditBlock()
        if not modified:
            return True
        new_start = max(0, start + start_delta)
        new_end = max(new_start, end + end_delta)
        new_cursor = self.textCursor()
        new_cursor.setPosition(new_start)
        new_cursor.setPosition(new_end, QTextCursor.KeepAnchor)
        self.setTextCursor(new_cursor)
        return True

    def _handle_generic_dedent(self) -> bool:
        """Handle Shift+Tab on non-bullet lines by removing one leading indent unit.

        Dedent strategy:
        - If line starts with a tab (\t), remove one tab.
        - Else if starts with two spaces, remove two spaces.
        - Else if starts with one space, remove that single space.
        Always consumes the event even if nothing to dedent to prevent focus shifts.
        Returns True when the event should be consumed.
        """
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return True  # consume to avoid focus change

        text = block.text()
        # If it's a bullet, let bullet handler manage it
        is_bullet, _, _ = self._is_bullet_line(text)
        if is_bullet:
            return False

        new_line, removed = self._dedent_line_text(text)
        if removed == 0:
            return True

        # Preserve caret relative position
        rel = cursor.position() - block.position()
        cursor.beginEditBlock()
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        # Restore cursor position moved left by 'removed', not going before line start
        new_block = self.document().findBlock(block.position())
        new_pos = max(new_block.position(), block.position() + rel - removed)
        c = self.textCursor()
        c.setPosition(new_pos)
        self.setTextCursor(c)
        cursor.endEditBlock()
        return True

    # --- Scrolling helpers ---

    def _apply_scroll_past_end_margin(self) -> None:
        """Add bottom margin to the document so the view can scroll past the last line."""
        try:
            root = self.document().rootFrame()
            fmt = root.frameFormat()
            # Use a fraction of the viewport height for a comfortable cushion
            margin = max(0, int(self.viewport().height() * 0.4))
            if fmt.bottomMargin() != margin:
                fmt.setBottomMargin(margin)
                root.setFrameFormat(fmt)
        except Exception:
            # Be defensive—failure to set margin shouldn't break editing
            pass

    def _scroll_one_line_down(self) -> None:
        """Scroll the viewport down by roughly one line height."""
        sb = self.verticalScrollBar()
        step = max(1, int(self.fontMetrics().lineSpacing()))
        sb.setValue(sb.value() + step)

    def _doc_to_markdown(self) -> str:
        parts: list[str] = []
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if not fragment.isValid():
                    it += 1
                    continue
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    parts.append(self._markdown_from_image_format(fmt.toImageFormat()))
                else:
                    parts.append(fragment.text())
                it += 1
            block = block.next()
            if block.isValid():
                parts.append("\n")
        return "".join(parts)

    def _markdown_from_image_format(self, img_fmt: QTextImageFormat) -> str:
        """Return markdown representation for an inline image fragment.

        Reconstructs the original (possibly relative) path, preserving an
        optional stored width attribute as `{width=NNN}` so round-trips from
        markdown → display → markdown are stable.
        """
        alt = img_fmt.property(IMAGE_PROP_ALT) or ""
        original = img_fmt.property(IMAGE_PROP_ORIGINAL) or img_fmt.name()
        if not isinstance(original, str):  # defensive
            original = str(original)
        original = self._normalize_image_path(original)
        width_prop = int(img_fmt.property(IMAGE_PROP_WIDTH) or 0)
        suffix = f"{{width={width_prop}}}" if width_prop else ""
        return f"![{alt}]({original}){suffix}"

    def _render_images(self, display_text: str, scheduled_at: Optional[float] = None) -> None:
        """Replace markdown image patterns in the given display text with inline images.

        This operates on the current document by selecting each pattern range
        and inserting a QTextImageFormat created from the resolved path.
        """
        import time
        delay_ms = (time.perf_counter() - scheduled_at) * 1000.0 if scheduled_at else 0.0
        matches = list(IMAGE_PATTERN.finditer(display_text))
        if not matches:
            self._mark_page_load(f"render images skipped (0) delay={delay_ms:.1f}ms")
            QTimer.singleShot(
                0,
                lambda: self._complete_page_load_logging(
                    f"qt idle after images delay={(time.perf_counter() - (scheduled_at or time.perf_counter()))*1000:.1f}ms"
                ),
            )
            return
        
        if _DETAILED_LOGGING:
            print(f"[TIMING] Rendering {len(matches)} images...")
        self._mark_page_load(f"render images start count={len(matches)} delay={delay_ms:.1f}ms")
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            for idx, match in enumerate(reversed(matches)):
                t_img_start = time.perf_counter()
                start, end = match.span()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                
                path = match.group("path")
                fmt = self._create_image_format(
                    path,
                    match.group("alt") or "",
                    match.group("width"),
                )
                t_img_end = time.perf_counter()
                
                if fmt is None:
                    # If the image can't be resolved, leave the markdown text as-is
                    print(f"  Image {idx+1}/{len(matches)} ({path}): FAILED")
                    continue
                
                cursor.removeSelectedText()
                cursor.insertImage(fmt)
                print(f"  Image {idx+1}/{len(matches)} ({path}): {(t_img_end - t_img_start)*1000:.1f}ms")
        finally:
            cursor.endEditBlock()
        self._mark_page_load(f"render images done count={len(matches)}")
        end_at = time.perf_counter()
        QTimer.singleShot(
            0,
            lambda: self._complete_page_load_logging(
                f"qt idle after images delay={(time.perf_counter() - end_at)*1000:.1f}ms"
            ),
        )

    def _insert_image_from_path(self, raw_path: str, alt: str = "", width: Optional[int] = None) -> None:
        fmt = self._create_image_format(raw_path, alt, str(width) if width else None)
        if fmt is None:
            self.insertPlainText(f"![{alt}]({raw_path})")
            return
        cursor = self.textCursor()
        cursor.insertImage(fmt)

    def _create_image_format(self, raw_path: str, alt: str, width: Optional[str]) -> Optional[QTextImageFormat]:
        resolved = self._resolve_image_path(raw_path)
        if resolved is None or not resolved.exists():
            return None
        image = QImage(str(resolved))
        if image.isNull():
            return None
        fmt = QTextImageFormat()
        fmt.setName(str(resolved))
        fmt.setProperty(IMAGE_PROP_ALT, alt)
        fmt.setProperty(IMAGE_PROP_ORIGINAL, raw_path)
        fmt.setProperty(IMAGE_PROP_NATURAL_WIDTH, image.width())
        fmt.setProperty(IMAGE_PROP_NATURAL_HEIGHT, image.height())
        width_val = int(width) if width else 0
        if width_val:
            fmt.setWidth(width_val)
            if image.width():
                ratio = image.height() / image.width()
                fmt.setHeight(width_val * ratio)
            fmt.setProperty(IMAGE_PROP_WIDTH, width_val)
        else:
            fmt.setProperty(IMAGE_PROP_WIDTH, 0)
        return fmt

    def _resolve_image_path(self, raw_path: str) -> Optional[Path]:
        if not raw_path:
            return None
        raw_path = raw_path.strip()
        if raw_path.startswith("http://") or raw_path.startswith("https://"):
            return None
        base_dir: Optional[Path] = None
        if self._vault_root and self._current_path:
            base_dir = (self._vault_root / self._current_path.lstrip("/")).parent
        elif self._vault_root:
            base_dir = self._vault_root
        path_obj = Path(raw_path)
        if path_obj.is_absolute():
            return path_obj.resolve()
        if raw_path.startswith("/"):
            if self._vault_root:
                return (self._vault_root / raw_path.lstrip("/")).resolve()
            return Path(raw_path).resolve()
        if raw_path.startswith("./"):
            raw_path = raw_path[2:]
        if base_dir:
            return (base_dir / raw_path).resolve()
        return Path(raw_path).resolve()

    def _normalize_image_path(self, path: str) -> str:
        path = (path or "").strip()
        if not path or path.startswith(("http://", "https://", "/", "./", "../")):
            return path
        return f"./{path}"

    def _normalize_markdown_images(self, markdown: str) -> str:
        if not markdown:
            return markdown

        def repl(match: re.Match[str]) -> str:
            alt = match.group("alt") or ""
            path = self._normalize_image_path(match.group("path") or "")
            width = match.group("width") or ""
            suffix = f"{{width={width}}}" if width else ""
            return f"![{alt}]({path}){suffix}"

        normalized = IMAGE_PATTERN.sub(repl, markdown)
        return normalized

    def _image_at_position(self, pos: Optional[QPoint]) -> Optional[tuple[QTextCursor, QTextImageFormat]]:
        cursor = self.cursorForPosition(pos) if pos is not None else self.textCursor()
        fmt = cursor.charFormat()
        if fmt.isImageFormat():
            return cursor, fmt.toImageFormat()
        if cursor.position() > 0:
            probe = QTextCursor(cursor)
            probe.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, 1)
            fmt = probe.charFormat()
            if fmt.isImageFormat():
                return probe, fmt.toImageFormat()
        return None

    def _find_image_by_name(self, image_name: str) -> Optional[tuple[QTextCursor, QTextImageFormat]]:
        """Find an image in the document by its filename."""
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        img_fmt = fmt.toImageFormat()
                        if img_fmt.name() == image_name:
                            # Create cursor at this fragment
                            cursor = QTextCursor(self.document())
                            cursor.setPosition(fragment.position())
                            return cursor, img_fmt
                it += 1
            block = block.next()
        return None

    def _resize_image_by_name(self, image_name: str, width: Optional[int]) -> None:
        """Find and resize an image by its filename."""
        result = self._find_image_by_name(image_name)
        if not result:
            return
        
        cursor, img_fmt = result
        
        # Compute new size properties
        natural_w = float(img_fmt.property(IMAGE_PROP_NATURAL_WIDTH) or 0)
        natural_h = float(img_fmt.property(IMAGE_PROP_NATURAL_HEIGHT) or 0)
        if width:
            img_fmt.setProperty(IMAGE_PROP_WIDTH, int(width))
            img_fmt.setWidth(int(width))
            if natural_w:
                ratio = natural_h / natural_w if natural_w else 0
                img_fmt.setHeight(int(width) * ratio if ratio else int(width))
        else:
            # Reset to natural size - clear the width property and use natural dimensions
            img_fmt.setProperty(IMAGE_PROP_WIDTH, 0)
            # For display, use the natural dimensions
            if natural_w and natural_h:
                img_fmt.setWidth(int(natural_w))
                img_fmt.setHeight(int(natural_h))
            else:
                img_fmt.setWidth(0)
                img_fmt.setHeight(0)
        
        # Replace the image at cursor position
        img_pos = cursor.position()
        cursor.beginEditBlock()
        cursor.setPosition(img_pos)
        cursor.setPosition(img_pos + 1, QTextCursor.KeepAnchor)
        cursor.insertImage(img_fmt)
        cursor.endEditBlock()

    def _prompt_image_width_by_name(self, image_name: str) -> None:
        """Prompt for custom width for an image identified by name."""
        result = self._find_image_by_name(image_name)
        if not result:
            return
        _, fmt = result
        current = int(fmt.property(IMAGE_PROP_WIDTH) or 0)
        if not current:
            current = int(fmt.property(IMAGE_PROP_NATURAL_WIDTH) or 300)
        width, ok = QInputDialog.getInt(self, "Image Width", "Width (px):", current, 50, 4096)
        if not ok:
            return
        self._resize_image_by_name(image_name, width)

    def set_ai_actions_enabled(self, enabled: bool) -> None:
        """Enable/disable AI actions menu entries."""
        self._ai_actions_enabled = bool(enabled)

    def set_ai_chat_available(self, available: bool, *, active: Optional[bool] = None) -> None:
        """Toggle whether a chat already exists for the current page."""
        self._ai_chat_available = bool(available)
        if active is not None:
            self._ai_chat_active = bool(active)

    def _prompt_custom_translation(self, text: str) -> None:
        lang, ok = QInputDialog.getText(self, "Custom Language", "Translate to which language?")
        if not ok or not lang.strip():
            return
        self.aiActionRequested.emit("Custom Translation", f"Translate to {lang.strip()}.", text)

    def _prompt_compare_note(self, text: str) -> None:
        other, ok = QInputDialog.getText(self, "Compare Against", "Paste or type the other note to compare against:")
        if not ok or not other.strip():
            return
        combined = f"Compare this note to the following note:\n\nOTHER NOTE:\n{other.strip()}\n\nORIGINAL NOTE:\n{text}"
        self.aiActionRequested.emit("Compare Against Another Note", "Compare against the provided note.", combined)

    def _looks_like_code(self, text: str) -> bool:
        """Heuristic to decide if text is code-like."""
        if not text:
            return False
        indicators = ("def ", "class ", "import ", "{", "}", ";", "=>", "#include", "function ")
        if any(tok in text for tok in indicators):
            return True
        # many short lines with symbols
        lines = text.splitlines()
        symbol_lines = sum(1 for ln in lines if any(ch in ln for ch in "{}();<>"))
        return symbol_lines >= max(3, len(lines) // 4)
