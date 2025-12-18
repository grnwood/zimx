from __future__ import annotations

import html
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeyEvent, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from markdown import markdown


class OneShotChatInput(QTextEdit):
    sendRequested = Signal()
    acceptRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Refine…  (Ctrl+Enter to send)")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
            event.accept()
            self.sendRequested.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ControlModifier):
            # Treat Enter on an empty refine box as "Accept" (insert last assistant reply).
            if not (self.toPlainText() or "").strip():
                event.accept()
                self.acceptRequested.emit()
                return
        super().keyPressEvent(event)


def _find_asset(name: str) -> Optional[Path]:
    rel = Path("assets") / name
    # PyInstaller layout
    try:
        import sys

        base = getattr(sys, "_MEIPASS", None)
        if base:
            for cand in (Path(base) / rel, Path(base) / "_internal" / rel):
                if cand.exists():
                    return cand
    except Exception:
        pass
    # Source layout
    try:
        pkg_root = Path(__file__).resolve().parents[2]  # .../zimx
        cand = pkg_root / rel
        if cand.exists():
            return cand
    except Exception:
        pass
    return None


class OneShotPromptOverlay(QDialog):
    """Small chat-like overlay for the editor's One-Shot prompt.

    Streams responses into the overlay. The latest assistant message can be accepted
    (inserting into the editor) or rejected (closing overlay).
    """

    def __init__(
        self,
        *,
        parent: QWidget,
        server_config: dict,
        model: str,
        system_prompt: str,
        on_accept: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        # Chrome-less, bubble-like popup.
        self.setWindowTitle("One‑Shot Prompt")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setModal(False)
        try:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        except Exception:
            pass

        self._server_config = server_config
        self._model = model
        self._system_prompt = system_prompt
        self._on_accept = on_accept

        self._worker = None
        self._streaming = False
        self._stream_buffer = ""
        self._render_pending = False

        self._messages: list[tuple[str, str]] = []
        self._api_messages: list[dict] = [{"role": "system", "content": system_prompt}]

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._render)

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("OneShotCard")
        card.setFrameShape(QFrame.NoFrame)
        try:
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 8)
            shadow.setColor(QColor(0, 0, 0, 140))
            card.setGraphicsEffect(shadow)
        except Exception:
            pass
        outer.addWidget(card, 1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("One‑Shot")
        title.setStyleSheet("font-weight: 600; font-size: 13px; color: #888;")
        title_row.addWidget(title, 0)
        title_row.addStretch(1)
        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 12px;")
        title_row.addWidget(self._status)
        layout.addLayout(title_row)

        self.chat_view = QTextBrowser(self)
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setOpenLinks(False)
        self.chat_view.anchorClicked.connect(self._on_anchor_clicked)
        self.chat_view.setStyleSheet(
            "QTextBrowser {"
            "  border: 1px solid rgba(0,0,0,0.18);"
            "  border-radius: 10px;"
            "  padding: 8px;"
            "  font-size: 12px;"
            "}"
        )
        layout.addWidget(self.chat_view, 1)

        input_row = QHBoxLayout()
        self.input = OneShotChatInput(self)
        self.input.setFixedHeight(54)
        self.input.setStyleSheet("font-size: 12px; padding: 6px;")
        self.input.sendRequested.connect(self._send_input)
        self.input.acceptRequested.connect(self._accept_last_message)
        input_row.addWidget(self.input, 1)
        self.send_btn = QToolButton(self)
        self.send_btn.setToolTip("Send (Ctrl+Enter)")
        icon_path = _find_asset("send-message.svg")
        if icon_path:
            self.send_btn.setIcon(QIcon(str(icon_path)))
        else:
            self.send_btn.setText("Send")
        self.send_btn.clicked.connect(self._send_input)
        self.send_btn.setFixedSize(40, 40)
        input_row.addWidget(self.send_btn)
        layout.addLayout(input_row)

        self.setStyleSheet(
            "QDialog { background: transparent; }"
            "QFrame#OneShotCard { background: palette(base); border-radius: 14px; }"
        )
        self.resize(680, 480)

    def open_with_selection(self, selected_text: str) -> None:
        selected_text = (selected_text or "").strip()
        if not selected_text:
            return
        self._append_user(selected_text)
        self._start_assistant_reply()
        self.show()
        self._focus_input_deferred()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._focus_input_deferred()

    def _focus_input_deferred(self) -> None:
        def _do() -> None:
            try:
                self.activateWindow()
                self.raise_()
            except Exception:
                pass
            try:
                self.input.setFocus(Qt.PopupFocusReason)
            except Exception:
                try:
                    self.input.setFocus()
                except Exception:
                    pass

        QTimer.singleShot(0, _do)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cancel_worker()
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.reject()
            return
        super().keyPressEvent(event)

    def reject(self) -> None:  # type: ignore[override]
        self._cancel_worker()
        super().reject()

    def _cancel_worker(self) -> None:
        worker = getattr(self, "_worker", None)
        if worker is not None:
            try:
                worker.request_cancel()
            except Exception:
                pass
        self._worker = None
        self._streaming = False
        self._stream_buffer = ""

    def _append_user(self, text: str) -> None:
        self._messages.append(("user", text))
        self._api_messages.append({"role": "user", "content": text})
        self._schedule_render()

    def _append_assistant_placeholder(self) -> None:
        self._messages.append(("assistant", ""))
        self._schedule_render()

    def _update_last_assistant(self, new_text: str) -> None:
        for idx in range(len(self._messages) - 1, -1, -1):
            role, _ = self._messages[idx]
            if role == "assistant":
                self._messages[idx] = ("assistant", new_text)
                break
        self._schedule_render()

    def _last_assistant_text(self) -> str:
        for role, content in reversed(self._messages):
            if role == "assistant":
                return content or ""
        return ""

    def _send_input(self) -> None:
        if self._streaming:
            return
        text = (self.input.toPlainText() or "").strip()
        if not text:
            return
        self.input.clear()
        self._append_user(text)
        self._start_assistant_reply()

    def _start_assistant_reply(self) -> None:
        if self._streaming:
            return
        try:
            from .ai_chat_panel import ApiWorker
        except Exception:
            self._status.setText("AI worker unavailable.")
            return

        self._cancel_worker()
        self._streaming = True
        self._stream_buffer = ""
        self._append_assistant_placeholder()
        # Keep the refine box enabled/focused so the user can type the next message
        # while streaming (sending is still blocked until streaming finishes).
        self._set_controls_enabled(False)
        self._status.setText("Streaming…")
        self._focus_input_deferred()

        worker = ApiWorker(self._server_config, list(self._api_messages), self._model, stream=True, parent=self)
        worker.chunk.connect(self._on_chunk)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_chunk(self, chunk: str) -> None:
        if not self._streaming:
            return
        self._stream_buffer += chunk
        self._update_last_assistant(self._stream_buffer)

    def _on_finished(self, full: str) -> None:
        if not self._streaming:
            return
        final = full or self._stream_buffer
        self._streaming = False
        self._worker = None
        self._stream_buffer = final
        self._update_last_assistant(final)
        self._api_messages.append({"role": "assistant", "content": final})
        self._set_controls_enabled(True)
        self._status.setText("Ready")
        self._focus_input_deferred()

    def _on_failed(self, err: str) -> None:
        self._streaming = False
        self._worker = None
        self._set_controls_enabled(True)
        self._status.setText(f"Failed: {err}")
        # Keep whatever we streamed so far in the UI.
        self._focus_input_deferred()

    def _set_controls_enabled(self, enabled: bool) -> None:
        # Keep input enabled so it can take focus while streaming; disable only send.
        self.input.setEnabled(True)
        self.send_btn.setEnabled(enabled)

    def _schedule_render(self) -> None:
        if self._render_timer.isActive():
            return
        self._render_timer.start()

    def _render(self) -> None:
        base_color = self.palette().color(QPalette.Base).name()
        text_color = self.palette().color(QPalette.Text).name()
        accent = self.palette().color(QPalette.Highlight).name()
        parts: list[str] = []
        parts.append(
            f"<style>"
            f"body {{ background:{base_color}; color:{text_color}; font-family: sans-serif; }}"
            f".bubble {{ border-radius:8px; padding:8px 10px; margin:8px 0; }}"
            f".user {{ background:rgba(80,120,200,0.10); }}"
            f".assistant {{ background:rgba(60,200,140,0.10); }}"
            f".role {{ font-weight:bold; color:{accent}; }}"
            f".actions {{ margin-top:8px; }}"
            f".actions a {{ margin-right:16px; text-decoration:none; color:{accent}; font-weight:bold; }}"
            f"</style>"
        )
        last_assistant_idx = None
        for idx in range(len(self._messages) - 1, -1, -1):
            if self._messages[idx][0] == "assistant":
                last_assistant_idx = idx
                break
        for idx, (role, content) in enumerate(self._messages):
            cls = "assistant" if role == "assistant" else "user"
            safe = content or ""
            rendered = markdown(safe, extensions=["fenced_code", "tables"])
            # If markdown library returns plain text (rare), escape it.
            if "<" not in rendered:
                rendered = "<p>" + html.escape(safe).replace("\n", "<br>") + "</p>"
            actions_html = ""
            if role == "assistant" and idx == last_assistant_idx and not self._streaming:
                actions_html = (
                    "<div class='actions'>"
                    "<a href='action:accept' title='Insert into editor'>Accept</a>"
                    "&nbsp;&nbsp;|&nbsp;&nbsp;"
                    "<a href='action:reject' title='Close without inserting'>Reject</a>"
                    "</div>"
                )
            parts.append(
                f"<div class='bubble {cls}'><span class='role'>{role.title()}:</span><br>{rendered}{actions_html}</div>"
            )
        self.chat_view.setHtml("".join(parts))
        try:
            cursor = self.chat_view.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.chat_view.setTextCursor(cursor)
        except Exception:
            pass

    def _on_anchor_clicked(self, url) -> None:
        try:
            href = url.toString()
        except Exception:
            return
        if href.startswith("action:accept"):
            self._accept_last_message()
            return
        if href.startswith("action:reject"):
            self.reject()
            return
        # Allow external links.
        if href.startswith(("http://", "https://")):
            try:
                QDesktopServices.openUrl(url)
            except Exception:
                pass

    def _accept_last_message(self) -> None:
        if self._streaming:
            return
        text = self._last_assistant_text()
        if text.strip():
            try:
                self._on_accept(text)
            except Exception:
                pass
        self.accept()
