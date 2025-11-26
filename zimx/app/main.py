from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import traceback
import shutil
from pathlib import Path

import uvicorn
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from zimx.server.api import get_app
from zimx.app import config
from zimx.app.ui.main_window import MainWindow


def _resource_candidates(rel_path: str) -> list[str]:
    """Return likely absolute paths for a bundled resource.

    Handles PyInstaller onedir/onefile via sys._MEIPASS, alongside the
    executable, and package-relative source layout. The first existing
    path from this list should be used.
    """
    candidates: list[str] = []
    # PyInstaller staging directory (onefile and onedir)
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, rel_path))
        # Some PyInstaller layouts stage package data under _internal
        candidates.append(os.path.join(base, "_internal", rel_path))
    # Next to the executable (dist root)
    try:
        exe_dir = os.path.abspath(os.path.dirname(sys.argv[0]))
        candidates.append(os.path.join(exe_dir, rel_path))
        candidates.append(os.path.join(exe_dir, "_internal", rel_path))
    except Exception:
        pass
    # Package-relative (developer mode)
    pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates.append(os.path.join(pkg_root, rel_path))
    return candidates


def _set_app_icon(app: QApplication) -> None:
    """Attempt to set the application/window icon if an asset is bundled.

    On Linux, PyInstaller does not embed a binary icon into the ELF. We set the
    window icon at runtime using a PNG. On Windows/macOS the EXE/App icon is
    handled by PyInstaller, but this also ensures the window/icon in the titlebar
    matches.
    """
    for path in _resource_candidates(os.path.join("assets", "icon.png")):
        if os.path.exists(path):
            try:
                app.setWindowIcon(QIcon(path))
            except Exception:
                pass
            break


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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ZimX desktop entry point.")
    parser.add_argument("--vault", help="Path to a vault to open at startup.")
    parser.add_argument("--port", type=int, help="Preferred API port (0 = auto-select).")
    parser.add_argument("--host", default=os.getenv("ZIMX_HOST", "127.0.0.1"), help="Host/interface to bind the API server.")
    return parser.parse_args(argv)


def _maybe_use_minimal_fonts() -> None:
    """Optionally force Qt to see only a small font set to avoid long font scans.

    Enable via ZIMX_MINIMAL_FONT_SCAN=1. This writes a tiny fontconfig file under
    ~/.cache/zimx/fonts-minimal and points FONTCONFIG_FILE/FONTCONFIG_PATH/QT_QPA_FONTDIR
    to it, copying a single known font if needed.
    """
    if os.getenv("ZIMX_MINIMAL_FONT_SCAN", "0") in ("0", "false", "False", "", None):
        return
    cache_root = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "zimx" / "fonts-minimal"
    font_dir = cache_root / "fonts"
    cache_root.mkdir(parents=True, exist_ok=True)
    font_dir.mkdir(parents=True, exist_ok=True)

    # Pick a small, common font without walking the whole tree.
    if os.name == "nt":
        win_fonts = Path(os.getenv("WINDIR", "C:\\Windows")) / "Fonts"
        candidates = [
            win_fonts / "segoeui.ttf",
            win_fonts / "arial.ttf",
            win_fonts / "tahoma.ttf",
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        ]
    src = next((p for p in candidates if p.exists()), None)
    if not src:
        print("[ZimX] ZIMX_MINIMAL_FONT_SCAN set but no candidate font found; falling back to system fonts.", file=sys.stderr)
        return
    dest = font_dir / src.name
    try:
        if not dest.exists():
            shutil.copy2(src, dest)
    except Exception as exc:
        print(f"[ZimX] Failed to copy minimal font {src}: {exc}", file=sys.stderr)
        return

    fonts_conf = cache_root / "fonts.conf"
    try:
        fonts_conf.write_text(
            f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>{font_dir}</dir>
</fontconfig>
""",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[ZimX] Failed to write minimal fonts.conf: {exc}", file=sys.stderr)
        return

    os.environ["FONTCONFIG_FILE"] = str(fonts_conf)
    os.environ["FONTCONFIG_PATH"] = str(cache_root)
    os.environ["QT_QPA_FONTDIR"] = str(font_dir)
    print(f"[ZimX] Minimal font scan enabled; using {dest} via {fonts_conf}", file=sys.stderr)


def _find_open_port(host: str, preferred: int) -> int:
    """Try preferred port, otherwise fall back to an ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, preferred))
            return s.getsockname()[1]
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _start_api_server(host: str, preferred_port: int | None) -> tuple[int, uvicorn.Server]:
    env_port = os.getenv("ZIMX_PORT")
    preferred = preferred_port if preferred_port is not None else int(env_port or "8765")
    # Allow 0 to force ephemeral port selection
    preferred = 0 if preferred == 0 else preferred
    port = _find_open_port(host, preferred)
    # Disable uvicorn's logging config when bundled with PyInstaller
    # to avoid "Unable to configure formatter 'default'" errors
    log_config = None if getattr(sys, "frozen", False) else None
    config = uvicorn.Config(
        get_app(),
        host=host,
        port=port,
        log_level=os.getenv("UVICORN_LOG_LEVEL", "debug"),
        log_config=log_config,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Give the event loop a moment to bind the socket before the UI fires requests.
    time.sleep(0.2)
    return port, server

def _parse_vault_arg(argv: list[str]) -> str | None:
    """Return a vault path passed via --vault flag, if present."""
    for idx, arg in enumerate(argv):
        if arg == "--vault" and idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _diag(msg: str) -> None:
    """Lightweight diagnostic logger for startup/teardown events."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ZimxDiag {timestamp}] {msg}", file=sys.stderr)


def main() -> None:
    args = _parse_args(sys.argv[1:])
    start_ts = time.time()
    _diag("Application starting.")
    _maybe_use_minimal_fonts()
    config.init_settings()
    # Install custom message handler to suppress harmless Qt warnings
    qInstallMessageHandler(_qt_message_handler)
    port, server = _start_api_server(args.host, args.port)
    _diag(f"API server started on {args.host}:{port}.")
    qt_app = QApplication(sys.argv)
    qt_app.aboutToQuit.connect(lambda: _diag("QApplication aboutToQuit emitted."))
    # Set window/app icon if available (especially needed on Linux)
    _set_app_icon(qt_app)
    # Ensure server shutdown when the UI exits
    qt_app.aboutToQuit.connect(lambda: setattr(server, "should_exit", True))
    window = MainWindow(api_base=f"http://{args.host}:{port}")
    window.resize(1200, 800)
    windows = getattr(qt_app, "_zimx_windows", [])
    windows.append(window)
    qt_app._zimx_windows = windows
    vault_hint = args.vault or _parse_vault_arg(sys.argv[1:])
    try:
        if window.startup(vault_hint=vault_hint):
            window.show()
            _diag("Main window shown; entering Qt event loop.")
            rc = qt_app.exec()
            uptime = time.time() - start_ts
            _diag(f"Qt event loop exited with code {rc} after {uptime:.2f}s.")
            sys.exit(rc)
        else:
            _diag("Startup cancelled by user; quitting.")
            qt_app.quit()
    except BaseException as exc:
        uptime = time.time() - start_ts
        _diag(f"Unhandled exception after {uptime:.2f}s: {exc}")
        traceback.print_exc()
        try:
            qt_app.quit()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()
