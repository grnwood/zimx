from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn
from PySide6.QtWidgets import QApplication

from zimx.server.api import get_app
from zimx.app import config
from .ui.main_window import MainWindow


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
    port, _ = _start_api_server()
    qt_app = QApplication(sys.argv)
    window = MainWindow(api_base=f"http://127.0.0.1:{port}")
    window.resize(1200, 800)
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()
