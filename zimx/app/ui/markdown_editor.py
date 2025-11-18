from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QMimeData, Qt, QRegularExpression, Signal, QUrl, QPoint, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
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
)
from PySide6.QtWidgets import QTextEdit, QMenu, QInputDialog, QDialog, QApplication
from .path_utils import path_to_colon, colon_to_path, ensure_root_colon_link
from .heading_utils import heading_slug


TAG_PATTERN = QRegularExpression(r"@(\w+)")
TASK_PATTERN = QRegularExpression(r"^(?P<indent>\s*)\((?P<state>[xX ])?\)(?P<body>\s+.*)$")
TASK_LINE_PATTERN = re.compile(r"^(\s*)\(([ xX])\)(\s+)", re.MULTILINE)
DISPLAY_TASK_PATTERN = re.compile(r"^(\s*)([☐☑])(\s+)", re.MULTILINE)
# Bullet patterns for storage and display
BULLET_STORAGE_PATTERN = re.compile(r"^(\s*)\* ", re.MULTILINE)
BULLET_DISPLAY_PATTERN = re.compile(r"^(\s*)• ", re.MULTILINE)
# Plus-prefixed link pattern: +PageName or +Projects (CamelCase style, no trailing spaces)
CAMEL_LINK_PATTERN = QRegularExpression(r"\+(?P<link>[A-Za-z][\w]*)")

_COLON_SEGMENT = r"[\w ]+"
_ROOT_COLON_PATH = rf":{_COLON_SEGMENT}(?::{_COLON_SEGMENT})*"
_MULTI_COLON_PATH = rf"{_COLON_SEGMENT}:{_COLON_SEGMENT}(?::{_COLON_SEGMENT})*"
_ANCHOR_SUFFIX = r"(?:#[A-Za-z0-9_-]+)?"
_SINGLE_WITH_ANCHOR = rf":?{_COLON_SEGMENT}#[A-Za-z0-9_-]+"
COLON_LINK_BODY_PATTERN = (
    rf"(?:{_ROOT_COLON_PATH}{_ANCHOR_SUFFIX}|{_MULTI_COLON_PATH}{_ANCHOR_SUFFIX}|{_SINGLE_WITH_ANCHOR})"
)

# Colon link pattern with optional anchor; now supports leading ':' for root links.
COLON_LINK_PATTERN = QRegularExpression(rf"(?P<link>{COLON_LINK_BODY_PATTERN})")
# Markdown-style link with colon target (optionally with anchor)
MARKDOWN_COLON_LINK_PATTERN = QRegularExpression(
    rf"\[(?P<text>[^\]]+)\]\s*\((?P<link>{COLON_LINK_BODY_PATTERN})\)"
)
# Generic markdown link for files (with an extension) e.g. [Report](report.pdf) or [Img](./image.png)
# Accept optional leading ./ and subfolder segments; require a dot-extension 1-8 chars
FILE_MARKDOWN_LINK_PATTERN = QRegularExpression(
    # Allow spaces in filenames by excluding only closing paren and newlines; still require an extension
    r"\[(?P<text>[^\]]+)\]\s*\((?P<file>(?:\./)?[^)\n]+\.[A-Za-z0-9]{1,8})\)"
)
# Storage pattern for markdown links (using Python re for easier replacement)
# Limit whitespace to prevent catastrophic backtracking with malformed links
# Now allows spaces in page names and supports single page with anchor/root prefix
MARKDOWN_LINK_STORAGE_PATTERN = re.compile(
    rf"\[(?P<text>[^\]]+)\][ \t]*\((?P<link>{COLON_LINK_BODY_PATTERN})\)",
    re.MULTILINE,
)
# Display pattern for rendered links (sentinel + null separator + label)
# Now allows spaces in link paths
MARKDOWN_LINK_DISPLAY_PATTERN = re.compile(r"\x00(?P<link>[\w :# \-]+)\x00(?P<text>[^\x00]+)")
HEADING_MAX_LEVEL = 5
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
        self.hidden_format.setFontPointSize(0.1)

        self.heading_styles = []
        for size in (26, 22, 18, 16, 14):
            fmt = QTextCharFormat(self.heading_format)
            fmt.setFontPointSize(size)
            self.heading_styles.append(fmt)

        self.bold_format = QTextCharFormat()
        self.bold_format.setForeground(QColor("#ffd479"))

        self.italic_format = QTextCharFormat()
        self.italic_format.setForeground(QColor("#ffa7c4"))

        self.code_format = QTextCharFormat()
        self.code_format.setForeground(QColor("#a3ffab"))
        self.code_format.setBackground(QColor("#2a2a2a"))
        self.code_format.setFontFamily("Fira Code")

        self.quote_format = QTextCharFormat()
        self.quote_format.setForeground(QColor("#7fdbff"))
        self.quote_format.setFontItalic(True)

        self.list_format = QTextCharFormat()
        self.list_format.setForeground(QColor("#ffffff"))

        self.code_block = QTextCharFormat()
        self.code_block.setBackground(QColor("#2a2a2a"))
        self.code_block.setForeground(QColor("#a3ffab"))
        self.code_block.setFontFamily("Fira Code")
        
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

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        import time
        t0 = time.perf_counter() if self._timing_enabled else 0.0

        # Block states: 0 = normal, 1 = inside code block
        prev_state = self.previousBlockState()
        in_code_block = (prev_state == 1)
        
        # Check if this line starts or ends a code block
        if text.startswith("```"):
            # Toggle code block state
            in_code_block = not in_code_block
            self.setCurrentBlockState(1 if in_code_block else 0)
            # Dim the fence line
            self.setFormat(0, len(text), self.code_fence_format)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return
        elif in_code_block:
            # Inside code block - style everything as code
            self.setCurrentBlockState(1)
            self.setFormat(0, len(text), self.code_block)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return
        else:
            self.setCurrentBlockState(0)

        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        level = heading_level_from_char(stripped[0]) if stripped else 0
        if level:
            fmt = self.heading_styles[min(level, len(self.heading_styles)) - 1]
            self.setFormat(indent + 1, max(0, len(stripped) - 1), fmt)
            self.setFormat(indent, 1, self.hidden_format)
        elif stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= HEADING_MAX_LEVEL and stripped[hashes:hashes + 1] == " ":
                fmt = self.heading_styles[min(hashes, len(self.heading_styles)) - 1]
                self.setFormat(indent + hashes + 1, len(stripped) - hashes - 1, fmt)

        if text.strip().startswith(("- ", "* ", "+ ")):
            self.setFormat(0, len(text), self.list_format)
        
        # Blockquotes - handle > and >> (nested)
        stripped_for_quote = text.lstrip()
        if stripped_for_quote.startswith(">"):
            # Count the number of > markers
            quote_depth = 0
            idx = 0
            while idx < len(stripped_for_quote) and stripped_for_quote[idx] == '>':
                quote_depth += 1
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
        
        if text.strip() == "---":
            self.setFormat(0, len(text), self.hr_format)
        
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
                    label_end = label_start
                    while label_end < len(text) and text[label_end] not in ("\x00", "\n"):
                        label_end += 1
                    if label_end > label_start:
                        self.setFormat(idx, label_start - idx, self.hidden_format)
                        self.setFormat(label_start, label_end - label_start, link_format)
                        display_link_spans.append((idx, label_end))
                        idx = label_end
                        continue
            idx += 1

        md_iter = MARKDOWN_COLON_LINK_PATTERN.globalMatch(text)
        md_spans: list[tuple[int, int]] = []
        while md_iter.hasNext():
            m = md_iter.next()
            start = m.capturedStart(); end = start + m.capturedLength()
            md_spans.append((start, end))
            text_val = m.captured("text")
            text_start = start + 1
            self.setFormat(text_start, len(text_val), link_format)

        camel_iter = CAMEL_LINK_PATTERN.globalMatch(text)
        while camel_iter.hasNext():
            match = camel_iter.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), link_format)

        colon_iter = COLON_LINK_PATTERN.globalMatch(text)
        while colon_iter.hasNext():
            match = colon_iter.next()
            s = match.capturedStart(); e = s + match.capturedLength()
            inside_md = any(ms <= s and e <= me for (ms, me) in md_spans)
            inside_display = any(ds <= s and e <= de for (ds, de) in display_link_spans)
            if not inside_md and not inside_display:
                self.setFormat(s, e - s, link_format)

        file_iter = FILE_MARKDOWN_LINK_PATTERN.globalMatch(text)
        while file_iter.hasNext():
            fm = file_iter.next()
            start = fm.capturedStart(); end = start + fm.capturedLength()
            overlap_colon = any(ms <= start and end <= me for (ms, me) in md_spans)
            if overlap_colon:
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

        if self._timing_enabled:
            self._timing_total += (time.perf_counter() - t0)
            self._timing_blocks += 1

    def reset_timing(self):
        self._timing_total = 0.0
        self._timing_blocks = 0

    def enable_timing(self, enabled: bool):
        self._timing_enabled = enabled


class MarkdownEditor(QTextEdit):
    imageSaved = Signal(str)
    focusLost = Signal()
    cursorMoved = Signal(int)
    linkActivated = Signal(str)
    linkHovered = Signal(str)  # Emits link path when hovering/cursor over a link
    headingsChanged = Signal(list)
    viewportResized = Signal()
    editPageSourceRequested = Signal(str)  # Emits file path when user wants to edit page source
    openFileLocationRequested = Signal(str)  # Emits file path when user wants to open file location

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_path: Optional[str] = None
        self._vault_root: Optional[Path] = None
        self._vi_mode_active: bool = False
        self._vi_block_cursor_enabled: bool = True  # default on, controlled by preferences
        self._vi_saved_flash_time: Optional[int] = None
        self._vi_last_cursor_pos: int = -1
        self._heading_outline: list[dict] = []
        self.setPlaceholderText("Open a Markdown file to begin editing…")
        self.setAcceptRichText(True)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.highlighter = MarkdownHighlighter(self.document())
        self.cursorPositionChanged.connect(self._emit_cursor)
        self.cursorPositionChanged.connect(self._maybe_update_vi_cursor)
        self._display_guard = False
        self.textChanged.connect(self._enforce_display_symbols)
        self.viewport().installEventFilter(self)
        self._heading_timer = QTimer(self)
        self._heading_timer.setInterval(250)
        self._heading_timer.setSingleShot(True)
        self._heading_timer.timeout.connect(self._emit_heading_outline)
        self.textChanged.connect(self._schedule_heading_outline)
        # Enable mouse tracking for hover cursor changes
        self.viewport().setMouseTracking(True)
        # Enable drag and drop for file attachments
        self.setAcceptDrops(True)
        # Configure scroll-past-end margin initially
        QTimer.singleShot(0, self._apply_scroll_past_end_margin)

    def paintEvent(self, event):  # type: ignore[override]
        """Custom paint to draw horizontal rules as visual lines."""
        super().paintEvent(event)
        
        # Draw horizontal rules (blocks containing exactly '---')
        painter = QPainter(self.viewport())
        pen = QPen(QColor("#555555"))
        pen.setWidth(2)
        painter.setPen(pen)
        layout = self.document().documentLayout()
        vsb = self.verticalScrollBar().value()
        block = self.document().begin()
        while block.isValid():
            if block.text().strip() == "---":
                br = layout.blockBoundingRect(block)
                y = int(br.top() - vsb + br.height() / 2)
                if 0 <= y <= self.viewport().height():
                    painter.drawLine(0, y, self.viewport().width(), y)
            block = block.next()
        painter.end()

    def set_context(self, vault_root: Optional[str], relative_path: Optional[str]) -> None:
        self._vault_root = Path(vault_root) if vault_root else None
        self._current_path = relative_path

    def current_relative_path(self) -> Optional[str]:
        return self._current_path

    def set_markdown(self, content: str) -> None:
        import time
        from os import getenv
        t0 = time.perf_counter()
        
        normalized = self._normalize_markdown_images(content)
        t1 = time.perf_counter()
        
        display = self._to_display(normalized)
        t2 = time.perf_counter()
        
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
        
        # Reconnect the textChanged handlers
        self.textChanged.connect(self._enforce_display_symbols)
        self.textChanged.connect(self._schedule_heading_outline)
        
        # Re-enable highlighter if it was disabled
        if highlighter_disabled:
            self.highlighter.setDocument(self.document())
        self.setUpdatesEnabled(True)
        
        # Lazy load images after a short delay to let the UI render first
        QTimer.singleShot(0, lambda: self._render_images(display))
        t4 = time.perf_counter()
        
        self._display_guard = False
        self._schedule_heading_outline()
        # Ensure scroll-past-end margin is applied after new content
        self._apply_scroll_past_end_margin()
        t5 = time.perf_counter()
        
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

    def to_markdown(self) -> str:
        markdown = self._doc_to_markdown()
        markdown = self._normalize_markdown_images(markdown)
        return self._from_display(markdown)

    def set_font_point_size(self, size: int) -> None:
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)

    def insert_link(self, colon_path: str, link_name: str | None = None) -> None:
        """Insert a link at the current cursor position.
        
        If link_name is provided, creates markdown-style link [link_name](colon_path).
        Otherwise inserts plain colon-notation link.
        """
        if not colon_path:
            return
        colon_path = ensure_root_colon_link(colon_path)
        cursor = self.textCursor()
        pos_before = cursor.position()
        
        # If link_name is provided, always use markdown syntax (even if same as path)
        if link_name:
            link_text = f"[{link_name}]({colon_path})"
        else:
            link_text = colon_path
            
        cursor.insertText(link_text)
        self.setTextCursor(cursor)
        # Full refresh ensures markdown links convert to hidden-display format immediately.
        self._refresh_display()

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
        if source.hasImage() and self._vault_root and self._current_path:
            image = source.imageData()
            if isinstance(image, QImage):
                saved = self._save_image(image)
                if saved:
                    self._insert_image_from_path(saved.name, alt=saved.stem)
                    self.imageSaved.emit(saved.name)
                    return
        
        # Remember position before paste
        pos_before = self.textCursor().position()
        super().insertFromMimeData(source)
        
        # After pasting text, check if it contains markdown links and re-render if needed
        if source.hasText():
            text = source.text()
            # Quick check: does pasted text contain markdown link pattern?
            if '[' in text and '](' in text and ':' in text:
                # Force full document re-render to apply display transformation
                self._refresh_display()

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
        # Markdown formatting shortcuts (Ctrl+B, Ctrl+I, Ctrl+K, Ctrl+H)
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
            elif event.key() == Qt.Key_H:
                self._toggle_highlight()
                event.accept()
                return
        
        # Vi-mode: Shift+H selects left, Shift+L selects right (like Shift+Arrow)
        if self._vi_mode_active:
            if (event.modifiers() & Qt.ShiftModifier) and not (event.modifiers() & Qt.ControlModifier):
                if event.key() == Qt.Key_H:
                    c = self.textCursor()
                    c.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, 1)
                    self.setTextCursor(c)
                    event.accept()
                    return
                if event.key() == Qt.Key_L:
                    c = self.textCursor()
                    c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 1)
                    self.setTextCursor(c)
                    event.accept()
                    return
            if event.modifiers() == Qt.AltModifier and event.key() in (Qt.Key_H, Qt.Key_L):
                qt_key = Qt.Key_Left if event.key() == Qt.Key_H else Qt.Key_Right
                # Set a flag on the window to restore vi mode after navigation
                window = self.window()
                if window:
                    window._restore_vi_mode_after_nav = True
                    print(f"[DEBUG] Editor: Alt+{'H' if event.key() == Qt.Key_H else 'L'} pressed, window={id(window)}, flag set to True, vi_mode={self._vi_mode_active}")
                self._trigger_history_navigation(qt_key)
                event.accept()
                return
            # ctrl-shift-j: PageUp
            if (event.modifiers() & Qt.ControlModifier) and (event.modifiers() & Qt.ShiftModifier):
                if event.key() == Qt.Key_K:
                    self._vi_page_up()
                    event.accept()
                    return
                if event.key() == Qt.Key_J:
                    self._vi_page_down()
                    event.accept()
                    return
        # Bullet mode key handling
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        is_bullet, indent, content = self._is_bullet_line(text)
        # Ctrl+E: edit link under cursor
        if event.key() == Qt.Key_E and event.modifiers() == Qt.ControlModifier:
            self._edit_link_at_cursor(cursor)
            event.accept()
            return
        # Tab: indent bullet
        if is_bullet and event.key() == Qt.Key_Tab and not event.modifiers():
            if self._handle_bullet_indent():
                event.accept()
                return
        # Shift-Tab: dedent bullet
        if is_bullet and event.key() == Qt.Key_Backtab:
            if self._handle_bullet_dedent():
                event.accept()
                return
        # Enter: continue bullet or terminate if empty
        if is_bullet and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            if self._handle_bullet_enter():
                event.accept()
                return
        # Esc: terminate bullet mode (remove bullet and all leading whitespace, move cursor to column 0)
        if event.key() == Qt.Key_Escape:
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
        if event.key() == Qt.Key_Down and not event.modifiers():
            if self.textCursor().atEnd():
                self._scroll_one_line_down()
                event.accept()
                return
        # Handle Left/Right arrow keys for proper link boundary navigation
        if event.key() in (Qt.Key_Left, Qt.Key_Right) and not event.modifiers():
            if self._handle_link_boundary_navigation(event.key()):
                event.accept()
                return
        
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
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

    def contextMenuEvent(self, event):  # type: ignore[override]
        # Check if right-clicking on an image
        image_hit = self._image_at_position(event.pos())
        if image_hit:
            cursor, fmt = image_hit
            # Store the image name as unique identifier instead of position
            image_name = fmt.name()
            menu = QMenu(self)
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
            
            menu.exec(event.globalPos())
            return
        
        # Check if right-clicking anywhere in the editor (for Copy Link to Location)
        if self._current_path:
            menu = self.createStandardContextMenu()
            menu.addSeparator()
            copy_action = menu.addAction("Copy Link to Location")
            # Get heading slug if cursor is on a heading line
            slug = self.current_heading_slug()
            copy_action.triggered.connect(lambda: self._copy_link_to_location(link_text=None, anchor_slug=slug))
            
            # Add Edit Page Source action (delegates to main window)
            edit_src_action = menu.addAction("Edit Page Source")
            edit_src_action.triggered.connect(lambda: self.editPageSourceRequested.emit(self._current_path))
            
            # Add Open File Location action (delegates to main window)
            open_loc_action = menu.addAction("Open File Location")
            open_loc_action.triggered.connect(lambda: self.openFileLocationRequested.emit(self._current_path))
            
            menu.exec(event.globalPos())
            return
        
        super().contextMenuEvent(event)
    
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
        if self._toggle_task_at_cursor(event.pos()):
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
        
        # Find link boundaries in display format (\x00Link\x00Label)
        idx = 0
        while idx < len(text):
            if text[idx] == '\x00':
                link_start = idx + 1
                link_end = text.find('\x00', link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = label_start
                    while label_end < len(text) and text[label_end] not in ('\x00', '\n'):
                        label_end += 1
                    
                    if key == Qt.Key_Right:
                        # Moving right: if cursor is in the hidden part, jump to label start
                        if idx <= rel_pos < label_start:
                            new_cursor = QTextCursor(cursor)
                            new_cursor.setPosition(block.position() + label_start)
                            self.setTextCursor(new_cursor)
                            return True
                        # If at the end of label, move past it
                        elif rel_pos == label_end and label_end < len(text):
                            new_cursor = QTextCursor(cursor)
                            new_cursor.setPosition(block.position() + label_end)
                            self.setTextCursor(new_cursor)
                            return True
                    
                    elif key == Qt.Key_Left:
                        # Moving left: if cursor is in the label, jump to before the link
                        if label_start < rel_pos <= label_end:
                            new_cursor = QTextCursor(cursor)
                            new_cursor.setPosition(block.position() + idx)
                            self.setTextCursor(new_cursor)
                            return True
                    
                    idx = label_end
                    continue
            idx += 1
        
        return False

    def _trigger_history_navigation(self, qt_key: int) -> None:
        """Simulate Alt+Left/Right to leverage MainWindow history shortcuts."""
        window = self.window()
        if not window:
            return
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
        
        # Check display-format markdown links first: \x00Link\x00Label
        idx = 0
        while idx < len(text):
            if text[idx] == '\x00':
                link_start = idx + 1
                link_end = text.find('\x00', link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = label_start
                    while label_end < len(text) and text[label_end] not in ('\x00', '\n'):
                        label_end += 1
                    if label_start <= rel < label_end:
                        return (text[link_start:link_end], label_start, label_end)
                    idx = label_end
                    continue
            idx += 1
        
        # Check storage-format markdown links (return target)
        md_iter = MARKDOWN_COLON_LINK_PATTERN.globalMatch(text)
        while md_iter.hasNext():
            m = md_iter.next()
            start = m.capturedStart()
            text_val = m.captured("text")
            text_start = start + 1
            text_end = text_start + len(text_val)
            if text_start <= rel < text_end:
                return (m.captured("link"), text_start, text_end)

        # Check generic file markdown links
        file_iter = FILE_MARKDOWN_LINK_PATTERN.globalMatch(text)
        while file_iter.hasNext():
            fm = file_iter.next()
            start = fm.capturedStart()
            label = fm.captured("text")
            label_start = start + 1
            label_end = label_start + len(label)
            if label_start <= rel < label_end:
                return (fm.captured("file"), label_start, label_end)
        
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
        """Return (start, end, text, link) for a markdown link under cursor, or None."""
        block = cursor.block()
        rel = cursor.position() - block.position()
        text = block.text()
        
        # Check display-format first: \x00Link\x00Label
        idx = 0
        while idx < len(text):
            if text[idx] == '\x00':
                link_start = idx + 1
                link_end = text.find('\x00', link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = label_start
                    while label_end < len(text) and text[label_end] not in ('\x00', '\n'):
                        label_end += 1
                    # Check if cursor is in the visible label portion (exclude end position)
                    if label_start <= rel < label_end:
                        link = text[link_start:link_end]
                        label = text[label_start:label_end]
                        return (idx, label_end, label, link)
                    idx = label_end
                    continue
            idx += 1
        
        # Check storage-format: [Label](Link)
        it = MARKDOWN_COLON_LINK_PATTERN.globalMatch(text)
        while it.hasNext():
            m = it.next()
            start = m.capturedStart()
            end = start + m.capturedLength()
            text_val = m.captured("text")
            text_start = start + 1
            text_end = text_start + len(text_val)
            if text_start <= rel < text_end:
                return (start, end, text_val, m.captured("link"))
        # Check generic file markdown links
        fit = FILE_MARKDOWN_LINK_PATTERN.globalMatch(text)
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
        """Open edit link dialog and replace link under cursor (supports markdown or plain colon link)."""
        from .edit_link_dialog import EditLinkDialog
        block = cursor.block()
        md = self._markdown_link_at_cursor(cursor)
        if md:
            start, end, text_val, link_val = md
        else:
            # Fallback: plain colon or CamelCase link
            link_val = self._link_under_cursor(cursor)
            if not link_val:
                return
            text_val = link_val
            # determine start/end of the match to replace
            rel = cursor.position() - block.position()
            # Check colon first
            it = COLON_LINK_PATTERN.globalMatch(block.text())
            rng = None
            while it.hasNext():
                m = it.next()
                s = m.capturedStart(); e = s + m.capturedLength()
                if s <= rel < e:
                    rng = (s,e)
                    break
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
        dlg = EditLinkDialog(link_to=link_val, link_text=text_val, parent=self)
        if dlg.exec() == QDialog.Accepted:
            new_to = dlg.link_to() or link_val
            raw_label = dlg.link_text()
            link_label = raw_label or None
            if new_to:
                match = COLON_LINK_PATTERN.match(new_to)
                if match.hasMatch():
                    new_to = ensure_root_colon_link(new_to)
            tc = QTextCursor(block)
            tc.setPosition(block.position() + start)
            tc.setPosition(block.position() + end, QTextCursor.KeepAnchor)
            tc.removeSelectedText()
            self.setTextCursor(tc)
            self.insert_link(new_to, link_label)
    
    def _copy_link_to_location(self, link_text: str | None = None, anchor_slug: Optional[str] = None) -> Optional[str]:
        """Copy a link location as colon-notation to clipboard.

        Args:
            link_text: The link text (e.g., 'PageName' from +PageName, or 'PageA:PageB:PageC' from colon link).
                      If None, copies the current page's location.
            anchor_slug: Optional heading slug to append when copying current page.
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
            if anchor_slug and "#" not in colon_path:
                colon_path = f"{colon_path}#{anchor_slug}"
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(colon_path)
            return colon_path
        return None

    def copy_current_page_link(self) -> Optional[str]:
        """Copy current page (or heading) link and return the copied text."""
        slug = self.current_heading_slug()
        return self._copy_link_to_location(link_text=None, anchor_slug=slug)

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

    def set_vi_mode(self, active: bool) -> None:
        """Enable or disable vi-mode cursor styling (pink block)."""
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

    def _maybe_update_vi_cursor(self) -> None:
        if not self._vi_mode_active or not self._vi_block_cursor_enabled:
            return
        pos = self.textCursor().position()
        if pos == self._vi_last_cursor_pos:
            return
        self._vi_last_cursor_pos = pos
        self._update_vi_cursor()

    def _update_vi_cursor(self) -> None:
        if not self._vi_mode_active or not self._vi_block_cursor_enabled:
            # Clear any vi-mode selection overlay
            self.setExtraSelections([])
            return
        cursor = self.textCursor()
        # Don't draw block cursor overlay while there's an active selection
        if cursor.hasSelection():
            self.setExtraSelections([])
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
        self.setExtraSelections([extra])

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
        # Transform markdown links: [Label](Link) → \x00Link\x00Label
        converted = MARKDOWN_LINK_STORAGE_PATTERN.sub(self._encode_link, converted)
        # Transform bullets: * → •
        converted = BULLET_STORAGE_PATTERN.sub(r"\1• ", converted)
        return converted

    def _from_display(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            state = "x" if match.group(2) == "☑" else " "
            return f"{match.group(1)}({state}){match.group(3)}"

        # Restore markdown links first: \x00Link\x00Label → [Label](Link)
        restored = MARKDOWN_LINK_DISPLAY_PATTERN.sub(self._decode_link, text)
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

    def _encode_link(self, match: re.Match[str]) -> str:
        """Convert [Label](Link) to hidden format: \x00Link\x00Label"""
        text = match.group("text")
        link = match.group("link")
        return f"\x00{link}\x00{text}"

    def _decode_link(self, match: re.Match[str]) -> str:
        """Convert hidden format \x00Link\x00Label back to [Label](Link)"""
        link = match.group("link")
        text = match.group("text")
        return f"[{text}]({link})"

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

    def _refresh_display(self) -> None:
        """Force full document re-render to apply display transformations.
        
        This converts storage format (markdown syntax) to display format (with hidden syntax).
        Used after inserting/editing links or pasting content that may contain links.
        """
        self._display_guard = True
        storage_text = self.toPlainText()
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

        # 3) Markdown links: [Label](Link) → \x00Link\x00Label
        line = MARKDOWN_LINK_STORAGE_PATTERN.sub(self._encode_link, line)
        
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
        
        # Check for bullet patterns: "• ", "* ", "- ", "+ "
        if stripped.startswith(("• ", "* ", "- ", "+ ")):
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

        original = text
        if text.startswith("\t"):
            new_line = text[1:]
            removed = 1
        elif text.startswith("  "):
            new_line = text[2:]
            removed = 2
        elif text.startswith(" "):
            new_line = text[1:]
            removed = 1
        else:
            # Nothing to dedent, but still consume the key to avoid focus change
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

    def _render_images(self, display_text: str) -> None:
        """Replace markdown image patterns in the given display text with inline images.

        This operates on the current document by selecting each pattern range
        and inserting a QTextImageFormat created from the resolved path.
        """
        import time
        matches = list(IMAGE_PATTERN.finditer(display_text))
        if not matches:
            return
        
        print(f"[TIMING] Rendering {len(matches)} images...")
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
