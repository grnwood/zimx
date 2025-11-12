from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QMimeData, Qt, QRegularExpression, Signal, QUrl, QPoint
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
)
from PySide6.QtWidgets import QTextEdit, QMenu, QInputDialog

TAG_PATTERN = QRegularExpression(r"@(\w+)")
TASK_PATTERN = QRegularExpression(r"^(?P<indent>\s*)\((?P<state>[xX ])?\)(?P<body>\s+.*)$")
TASK_LINE_PATTERN = re.compile(r"^(\s*)\(([ xX])\)(\s+)", re.MULTILINE)
DISPLAY_TASK_PATTERN = re.compile(r"^(\s*)([☐☑])(\s+)", re.MULTILINE)
CAMEL_LINK_PATTERN = QRegularExpression(r"\+(?P<link>[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)+)")
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
        self.code_format.setFontFamily("Fira Code")

        self.quote_format = QTextCharFormat()
        self.quote_format.setForeground(QColor("#7fdbff"))

        self.list_format = QTextCharFormat()
        self.list_format.setForeground(QColor("#ffffff"))

        self.code_block = QTextCharFormat()
        self.code_block.setBackground(QColor("#2a2a2a"))
        self.code_block.setFontFamily("Fira Code")

        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor("#ffa657"))

        self.checkbox_format = QTextCharFormat()
        self.checkbox_format.setForeground(QColor("#c8c8c8"))
        self.checkbox_format.setFontFamily("Segoe UI Symbol")

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        if stripped:
            level = heading_level_from_char(stripped[0])
        else:
            level = 0
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

        if text.strip().startswith(">"):
            self.setFormat(0, len(text), self.quote_format)

        code_pattern = QRegularExpression(r"`[^`]+`")
        iterator = code_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.code_format)

        bold_pattern = QRegularExpression(r"\*\*([^*]+)\*\*")
        iterator = bold_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.bold_format)

        italic_pattern = QRegularExpression(r"\*([^*]+)\*")
        iterator = italic_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.italic_format)

        if text.startswith("```"):
            self.setFormat(0, len(text), self.code_block)

        iterator = TAG_PATTERN.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.tag_format)
        stripped = text.lstrip()
        if stripped.startswith("☐") or stripped.startswith("☑"):
            offset = len(text) - len(stripped)
            self.setFormat(offset, 1, self.checkbox_format)

        camel_iter = CAMEL_LINK_PATTERN.globalMatch(text)
        camel_format = QTextCharFormat()
        camel_format.setForeground(QColor("#4fa3ff"))
        camel_format.setFontUnderline(True)
        while camel_iter.hasNext():
            match = camel_iter.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), camel_format)


class MarkdownEditor(QTextEdit):
    imageSaved = Signal(str)
    focusLost = Signal()
    cursorMoved = Signal(int)
    linkActivated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_path: Optional[str] = None
        self._vault_root: Optional[Path] = None
        self._vi_mode_active: bool = False
        self._vi_saved_flash_time: Optional[int] = None
        self._vi_last_cursor_pos: int = -1
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

    def set_context(self, vault_root: Optional[str], relative_path: Optional[str]) -> None:
        self._vault_root = Path(vault_root) if vault_root else None
        self._current_path = relative_path

    def current_relative_path(self) -> Optional[str]:
        return self._current_path

    def set_markdown(self, content: str) -> None:
        normalized = self._normalize_markdown_images(content)
        display = self._to_display(normalized)
        self._display_guard = True
        self.document().clear()
        self.setPlainText(display)
        self._render_images(display)
        self._display_guard = False

    def to_markdown(self) -> str:
        markdown = self._doc_to_markdown()
        markdown = self._normalize_markdown_images(markdown)
        return self._from_display(markdown)

    def set_font_point_size(self, size: int) -> None:
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)

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
        super().insertFromMimeData(source)

    def _save_image(self, image: QImage) -> Optional[Path]:
        file_path = self._current_path.lstrip("/") if self._current_path else None
        if not file_path:
            return None
        folder = (self._vault_root / file_path).resolve().parent if self._vault_root else None
        if folder is None:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        index = 1
        while True:
            candidate = folder / f"paste_image_{index:03d}.png"
            if not candidate.exists():
                break
            index += 1
        if image.save(str(candidate), "PNG"):
            return candidate
        return None

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self.focusLost.emit()

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            link = self._link_under_cursor()
            if link:
                self.linkActivated.emit(link)
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):  # type: ignore[override]
        hit = self._image_at_position(event.pos())
        if hit:
            cursor, fmt = hit
            position = cursor.position()
            menu = QMenu(self)
            for width in (300, 600, 900):
                action = menu.addAction(f"{width}px")
                action.triggered.connect(lambda checked=False, w=width, pos=position: self._set_image_width(pos, w))
            menu.addSeparator()
            reset_action = menu.addAction("Original Size")
            reset_action.triggered.connect(lambda checked=False, pos=position: self._set_image_width(pos, None))
            menu.addSeparator()
            custom_action = menu.addAction("Custom…")
            custom_action.triggered.connect(lambda checked=False, pos=position: self._prompt_image_width(pos))
            menu.exec(event.globalPos())
            return
        super().contextMenuEvent(event)

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

    def _link_under_cursor(self, cursor: QTextCursor | None = None) -> Optional[str]:
        cursor = cursor or self.textCursor()
        block = cursor.block()
        rel = cursor.position() - block.position()
        iterator = CAMEL_LINK_PATTERN.globalMatch(block.text())
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel <= end:
                return match.captured("link")
        return None

    def _emit_cursor(self) -> None:
        self.cursorMoved.emit(self.textCursor().position())

    # --- Vi-mode cursor -------------------------------------------------
    def set_vi_mode(self, active: bool) -> None:
        """Enable or disable vi-mode cursor styling (pink block)."""
        if self._vi_mode_active == active:
            return
        self._vi_mode_active = active
        # Disable cursor blinking while in vi-mode to avoid flicker with overlay
        if active:
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
        if not self._vi_mode_active:
            return
        pos = self.textCursor().position()
        if pos == self._vi_last_cursor_pos:
            return
        self._vi_last_cursor_pos = pos
        self._update_vi_cursor()

    def _update_vi_cursor(self) -> None:
        if not self._vi_mode_active:
            # Clear any vi-mode selection overlay
            self.setExtraSelections([])
            return
        cursor = self.textCursor()
        block_cursor = QTextCursor(cursor)
        if not block_cursor.atEnd():
            # Select the character under the caret to form a block
            block_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        extra = QTextEdit.ExtraSelection()
        extra.cursor = block_cursor
        fmt = extra.format
        fmt.setBackground(QColor("#ff7acb"))  # pink background
        fmt.setForeground(QColor("#000"))     # dark text for contrast
        fmt.setProperty(QTextFormat.FullWidthSelection, True)
        self.setExtraSelections([extra])

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.viewport():
            if event.type() in (QEvent.Paint, QEvent.UpdateRequest, QEvent.FocusIn, QEvent.FocusOut):
                if self._vi_mode_active:
                    self._update_vi_cursor()
        return super().eventFilter(obj, event)

    def _to_display(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            symbol = "☑" if match.group(2).strip().lower() == "x" else "☐"
            return f"{match.group(1)}{symbol}{match.group(3)}"

        converted = TASK_LINE_PATTERN.sub(repl, text)
        return HEADING_MARK_PATTERN.sub(self._encode_heading, converted)

    def _from_display(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            state = "x" if match.group(2) == "☑" else " "
            return f"{match.group(1)}({state}){match.group(3)}"

        restored = HEADING_DISPLAY_PATTERN.sub(self._decode_heading, text)
        return DISPLAY_TASK_PATTERN.sub(repl, restored)

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

    def _restore_cursor_position(self, position: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(min(position, len(self.toPlainText())))
        self.setTextCursor(cursor)

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
        matches = list(IMAGE_PATTERN.finditer(display_text))
        if not matches:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            for match in reversed(matches):
                start, end = match.span()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                fmt = self._create_image_format(
                    match.group("path"),
                    match.group("alt") or "",
                    match.group("width"),
                )
                if fmt is None:
                    # If the image can't be resolved, leave the markdown text as-is
                    continue
                cursor.removeSelectedText()
                cursor.insertImage(fmt)
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

    def _set_image_width(self, position: int, width: Optional[int]) -> None:
        cursor = QTextCursor(self.textCursor())
        cursor.setPosition(position)
        self.setTextCursor(cursor)
        hit = self._image_at_position(None)
        if not hit:
            return
        img_cursor, img_fmt = hit
        natural_w = float(img_fmt.property(IMAGE_PROP_NATURAL_WIDTH) or 0)
        natural_h = float(img_fmt.property(IMAGE_PROP_NATURAL_HEIGHT) or 0)
        if width:
            img_fmt.setWidth(width)
            if natural_w:
                ratio = natural_h / natural_w
                img_fmt.setHeight(width * ratio if ratio else width)
            img_fmt.setProperty(IMAGE_PROP_WIDTH, int(width))
        else:
            img_fmt.setWidth(0)
            img_fmt.setHeight(0)
            img_fmt.setProperty(IMAGE_PROP_WIDTH, 0)
        img_cursor.beginEditBlock()
        img_cursor.deleteChar()
        img_cursor.insertImage(img_fmt)
        img_cursor.endEditBlock()

    def _prompt_image_width(self, position: int) -> None:
        cursor = QTextCursor(self.textCursor())
        cursor.setPosition(position)
        self.setTextCursor(cursor)
        hit = self._image_at_position(None)
        if not hit:
            return
        _, fmt = hit
        current = int(fmt.property(IMAGE_PROP_WIDTH) or 0)
        if not current:
            current = int(fmt.property(IMAGE_PROP_NATURAL_WIDTH) or 300)
        width, ok = QInputDialog.getInt(self, "Image Width", "Width (px):", current, 50, 4096)
        if not ok:
            return
        self._set_image_width(position, width)
