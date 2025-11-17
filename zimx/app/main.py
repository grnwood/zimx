from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from zimx.server.api import get_app
from zimx.app import config
from zimx.app.ui.main_window import MainWindow


def _qt_message_handler(mode: QtMsgType, context, message: str) -> None:
    """Custom Qt message handler to suppress known harmless warnings."""
    # Suppress DirectWrite font warning on Windows
    if "QWindowsFontEngineDirectWrite::recalcAdvances" in message:
        return
    # Suppress other known harmless warnings if needed
    if "GetDesignGlyphMetrics failed" in message:
        return
    # Let other messages through to the default handler
    if mode == QtMsgType.QtDebugMsg:
        print(f"Qt Debug: {message}", file=sys.stderr)
    elif mode == QtMsgType.QtWarningMsg:
        print(f"Qt Warning: {message}", file=sys.stderr)
    elif mode == QtMsgType.QtCriticalMsg:
        print(f"Qt Critical: {message}", file=sys.stderr)
    elif mode == QtMsgType.QtFatalMsg:
        print(f"Qt Fatal: {message}", file=sys.stderr)
        sys.exit(1)


def _start_api_server() -> tuple[int, uvicorn.Server]:
    port = int(os.getenv("ZIMX_PORT", "8765"))
    config = uvicorn.Config(
        get_app(),
        host="127.0.0.1",
        port=port,
        log_level=os.getenv("UVICORN_LOG_LEVEL", "debug"),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Give the event loop a moment to bind the socket before the UI fires requests.
    time.sleep(0.2)
    return port, server


def main() -> None:
    config.init_settings()
    # Install custom message handler to suppress harmless Qt warnings
    qInstallMessageHandler(_qt_message_handler)
    port, _ = _start_api_server()
    qt_app = QApplication(sys.argv)
    window = MainWindow(api_base=f"http://127.0.0.1:{port}")
    window.resize(1200, 800)
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()
