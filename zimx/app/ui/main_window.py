from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable
import json
import os
import socket
import subprocess
import sys
import time
import faulthandler

import httpx
from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QPoint,
    Qt,
    Signal,
    QTimer,
    QObject,
    QElapsedTimer,
    QAbstractEventDispatcher,
    QByteArray,
    QUrl,
    QPropertyAnimation,
)
from PySide6.QtGui import (
    QAction,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
    QTextCursor,
    QKeyEvent,
    QPainter,
    QColor,
    QFont,
    QPen,
    QPalette,
    QBrush,
    QDesktopServices,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTreeView,
    QDialog,
    QProgressDialog,
    QWidget,
    QVBoxLayout,
    QFrame,
    QLabel,
    QHBoxLayout,
    QToolButton,
)

from zimx.app import config, indexer
from zimx.server.adapters.files import PAGE_SUFFIX

from .markdown_editor import MarkdownEditor
from .tabbed_right_panel import TabbedRightPanel
from .task_panel import TaskPanel
from .link_navigator_panel import LinkNavigatorPanel
from .ai_chat_panel import AIChatPanel
from .jump_dialog import JumpToPageDialog
from .toc_widget import TableOfContentsWidget
from .heading_utils import heading_slug
from .preferences_dialog import PreferencesDialog
from .insert_link_dialog import InsertLinkDialog
from .new_page_dialog import NewPageDialog
from .path_utils import colon_to_path, path_to_colon, ensure_root_colon_link
from .date_insert_dialog import DateInsertDialog
from .open_vault_dialog import OpenVaultDialog
from .page_editor_window import PageEditorWindow
from .page_load_logger import PageLoadLogger, PAGE_LOGGING_ENABLED


PATH_ROLE = int(Qt.ItemDataRole.UserRole)
TYPE_ROLE = PATH_ROLE + 1
OPEN_ROLE = TYPE_ROLE + 1
FILTER_BANNER = "__NAV_FILTER_BANNER__"
_DETAILED_LOGGING = os.getenv("ZIMX_DETAILED_LOGGING", "0") not in ("0", "false", "False", "", None)
_VI_KEY_LOGGING = os.getenv("VI_KEY_DETAILED", "0") not in ("0", "false", "False", "", None)

class InlineNameEdit(QLineEdit):
    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.returnPressed.connect(self._emit_submit)

    def _emit_submit(self) -> None:
        self.submitted.emit(self.text())

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.cancelled.emit()
            self.deleteLater()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self.cancelled.emit()
        self.deleteLater()


class VaultTreeView(QTreeView):
    enterActivated = Signal()
    arrowNavigated = Signal()
    escapePressed = Signal()
    rowClicked = Signal(QModelIndex)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            self.escapePressed.emit()
            self.collapseAll()
            event.accept()
            return
        if event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Down, Qt.Key_Up):
            direction = 1 if event.key() == Qt.Key_Down else -1
            self._walk_tree(direction)
            self.arrowNavigated.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            super().keyPressEvent(event)
            self.enterActivated.emit()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            self.arrowNavigated.emit()
        super().keyPressEvent(event)

    def _walk_tree(self, direction: int) -> None:
        indexes = self._flatten()
        if not indexes:
            return
        current = self.currentIndex()
        try:
            idx = indexes.index(current)
        except ValueError:
            idx = -1 if direction > 0 else 0
        new_idx = idx + direction
        new_idx = max(0, min(len(indexes) - 1, new_idx))
        if new_idx == idx:
            return
        prev = current if current.isValid() else None
        target = indexes[new_idx]
        if prev and prev.data(TYPE_ROLE):
            self.collapse(prev)
        if target.data(TYPE_ROLE):
            self.expand(target)
        self.setCurrentIndex(target)
        self.scrollTo(target)

    def _flatten(self) -> list[QModelIndex]:
        model = self.model()
        if model is None:
            return []
        order: list[QModelIndex] = []

        def recurse(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                order.append(idx)
                recurse(idx)

        recurse(QModelIndex())
        return order

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            idx = self.indexAt(event.pos())
            if idx.isValid():
                self.rowClicked.emit(idx)
        self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):

    # --- Vi-mode dialog lockout ---
    def _lock_vi_mode_toggle(self):
        """Prevent vi mode from being toggled while a dialog is open."""
        self._vi_mode_locked = True

    def _unlock_vi_mode_toggle(self):
        """Allow vi mode to be toggled again after dialog closes."""
        self._vi_mode_locked = False

    def __init__(self, api_base: str) -> None:
        super().__init__()
        self._vi_debug =  _VI_KEY_LOGGING
        self.setWindowTitle("ZimX Desktop")
        self.api_base = api_base.rstrip("/")
        self.http = httpx.Client(base_url=self.api_base, timeout=10.0)
        self.vault_root: Optional[str] = None
        self.vault_root_name: Optional[str] = None
        self.current_path: Optional[str] = None
        self._nav_filter_path: Optional[str] = None
        self._full_tree_data: list[dict] = []
        self._skip_next_selection_open: bool = False
        self._history_popup: Optional[QWidget] = None
        self._history_popup_label: Optional[QLabel] = None
        self._popup_items: list = []
        self._popup_index: int = -1
        self._popup_mode: Optional[str] = None  # "history" or "heading"
        self._history_cursor_positions: dict[str, int] = {}
        
        # Page navigation history
        self.page_history: list[str] = []
        self.history_index: int = -1
        # Guard to suppress auto-open on tree selection during programmatic navigation
        self._suspend_selection_open: bool = False
        # Remember cursor positions for history navigation
        # Track last-saved content to detect dirty buffers
        self._last_saved_content: Optional[str] = None
        self._vi_suspended_for_tasks: bool = False
        self._scroll_anim: Optional[QPropertyAnimation] = None
        
        # Track virtual (unsaved) pages
        self.virtual_pages: set[str] = set()
        # Track original content of virtual pages to detect actual edits
        self.virtual_page_original_content: dict[str, str] = {}
        
        # Bookmarks
        self.bookmarks: list[str] = []
        self.bookmark_buttons: dict[str, QPushButton] = {}
        
        # History buttons
        self.history_buttons: list[QPushButton] = []

        self.tree_view = VaultTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["Vault"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setHeaderHidden(False)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._open_context_menu)
        self.tree_view.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.tree_view.enterActivated.connect(self._focus_editor_from_tree)
        self.tree_view.arrowNavigated.connect(self._mark_tree_arrow_nav)
        self.tree_view.escapePressed.connect(self._clear_nav_filter)
        self.tree_view.rowClicked.connect(self._on_tree_row_clicked)
        self.dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        self._tree_arrow_focus_pending = False
        
        # Create custom header widget with "Show Journal" checkbox
        self.tree_header_widget = QWidget()
        tree_header_layout = QHBoxLayout()
        tree_header_layout.setContentsMargins(8, 4, 8, 4)
        tree_header_layout.setSpacing(8)
        
        tree_header_label = QLabel("Vault")
        tree_header_label.setStyleSheet("font-weight: bold;")
        tree_header_layout.addWidget(tree_header_label)
        
        self.show_journal_button = QToolButton()
        self.show_journal_button.setCheckable(True)
        self.show_journal_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.show_journal_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.show_journal_button.setAutoRaise(True)
        pal = QApplication.instance().palette()
        tooltip_fg = pal.color(QPalette.ToolTipText).name()
        tooltip_bg = pal.color(QPalette.ToolTipBase).name()
        self.show_journal_button.setToolTip(
            f"<div style='color:{tooltip_fg}; background:{tooltip_bg}; padding:2px 4px;'>Toggle Journal in navigator</div>"
        )
        self.show_journal_button.toggled.connect(self._on_show_journal_toggled)
        tree_header_layout.addWidget(self.show_journal_button)
        
        tree_header_layout.addStretch()
        self.tree_header_widget.setLayout(tree_header_layout)
        self.tree_header_widget.setStyleSheet("background: palette(midlight); border-bottom: 1px solid #555;")
        
        # Set the custom header widget
        self.tree_view.header().hide()
        self.tree_view.setHeaderHidden(True)

        self.editor = MarkdownEditor()
        self.editor.imageSaved.connect(self._on_image_saved)
        self.editor.textChanged.connect(lambda: self.autosave_timer.start())
        self.editor.focusLost.connect(lambda: self._save_current_file(auto=True))
        self.editor.linkActivated.connect(lambda link: self._open_camel_link(link, focus_target="editor"))
        self.editor.linkHovered.connect(self._on_link_hovered)
        self.editor.linkCopied.connect(self._on_link_copied)
        self.editor.insertDateRequested.connect(self._insert_date)
        self.editor.editPageSourceRequested.connect(self._view_page_source)
        self.editor.openFileLocationRequested.connect(self._open_tree_file_location)
        self.editor.attachmentDropped.connect(self._on_attachment_dropped)
        self.editor.backlinksRequested.connect(
            lambda path="": self._show_link_navigator_for_path(path or self.current_path)
        )
        self.editor.aiChatRequested.connect(lambda path="": self._open_ai_chat_for_path(path or self.current_path, create=True))
        self.editor.aiChatSendRequested.connect(self._send_selection_to_ai_chat)
        self.editor.aiChatPageFocusRequested.connect(self._focus_ai_chat_for_page)
        self.editor.aiActionRequested.connect(self._handle_ai_action)
        self.editor.linkActivated.connect(self._open_link_in_context)
        self.editor.set_open_in_window_callback(self._open_page_editor_window)
        self.editor.set_filter_nav_callback(self._set_nav_filter)
        self.font_size = 14
        self.editor.set_font_point_size(self.font_size)
        # Load vi-mode block cursor preference
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
        self.editor.viModeStateChanged.connect(self._update_vi_badge)
        self.editor.set_vi_strict_mode_enabled(config.load_vi_strict_mode_enabled())
        self.toc_widget = TableOfContentsWidget(self.editor.viewport())
        self.toc_widget.set_headings([])
        self.toc_widget.set_base_path("")
        self.toc_widget.headingActivated.connect(self._toc_jump_to_position)
        self.toc_widget.collapsedChanged.connect(self._on_toc_collapsed_changed)
        self.toc_widget.linkCopied.connect(
            lambda link: self.statusBar().showMessage(f"Copied link: {link}", 2500)
        )
        self.toc_widget.set_collapsed(config.load_toc_collapsed())
        self.toc_widget.show()
        self._toc_headings: list[dict] = []
        self.editor.headingsChanged.connect(self._on_headings_changed)
        self.editor.viewportResized.connect(self._position_toc_widget)
        self.editor.verticalScrollBar().valueChanged.connect(lambda *_: (self._update_toc_visibility(), self._position_toc_widget()))
        self.editor.verticalScrollBar().rangeChanged.connect(lambda *_: (self._update_toc_visibility(), self._position_toc_widget()))

        self.right_panel = TabbedRightPanel(
            enable_ai_chats=config.load_enable_ai_chats(),
            ai_chat_font_size=config.load_ai_chat_font_size(),
            http_client=self.http,
        )
        self.right_panel.refresh_tasks()
        self.right_panel.taskActivated.connect(self._open_task_from_panel)
        self.right_panel.dateActivated.connect(self._open_journal_date)
        self.right_panel.linkActivated.connect(self._open_link_from_panel)
        self.right_panel.calendarPageActivated.connect(self._open_calendar_page)
        self.right_panel.aiChatNavigateRequested.connect(self._on_ai_chat_navigate)
        self.right_panel.openInWindowRequested.connect(self._open_page_editor_window)
        self.right_panel.openTaskWindowRequested.connect(self._open_task_panel_window)
        self.right_panel.openLinkWindowRequested.connect(self._open_link_panel_window)
        self.right_panel.openAiWindowRequested.connect(self._open_ai_chat_window)
        try:
            self.right_panel.task_panel.focusGained.connect(self._suspend_vi_for_tasks)
        except Exception:
            pass
        self._inline_editor: Optional[InlineNameEdit] = None
        self._pending_selection: Optional[str] = None
        self._suspend_autosave = False
        self._vault_lock_path: Optional[Path] = None
        self._vault_lock_owner: Optional[dict] = None
        self._read_only: bool = False
        self._page_windows: list[PageEditorWindow] = []
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30_000)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True))

        # Geometry save timer (debounce frequent resize/splitter move events)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)

        # Vi-mode state
        self._vi_mode_active = False
       
        self.editor_split = QSplitter()
        self.editor_split.addWidget(self.editor)
        self.editor_split.addWidget(self.right_panel)
        self.editor_split.setChildrenCollapsible(False)
        self.editor_split.setHandleWidth(8)
        # Allow the editor to shrink enough so the right panel can expand comfortably
        self.editor.setMinimumWidth(200)
        self.right_panel.setMinimumWidth(240)
        self.editor_split.setStretchFactor(0, 4)
        self.editor_split.setStretchFactor(1, 2)
        self.editor_split.splitterMoved.connect(self._on_splitter_moved)

        # Create tree container with custom header
        tree_container = QWidget()
        tree_layout = QVBoxLayout()
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(0)
        tree_layout.addWidget(self.tree_header_widget)
        tree_layout.addWidget(self.tree_view)
        tree_container.setLayout(tree_layout)
        
        self.main_splitter = QSplitter()
        self.main_splitter.addWidget(tree_container)
        self.main_splitter.addWidget(self.editor_split)
        self.main_splitter.setStretchFactor(1, 5)
        self.main_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)

        # Create history bar (separate row for history buttons)
        self.history_bar = QWidget()
        self.history_bar.setMaximumHeight(40)
        self.history_bar.setStyleSheet("border-top: 1px solid #555;")
        history_bar_layout = QHBoxLayout(self.history_bar)
        history_bar_layout.setContentsMargins(5, 2, 5, 2)
        history_bar_layout.setSpacing(4)
        
        # Add history buttons container
        self.history_container = QWidget()
        self.history_container.setStyleSheet("")  # Clear any inherited styles
        self.history_layout = QHBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(4)
        history_bar_layout.addWidget(self.history_container)
        
        # Add spacer to push buttons to the left
        history_spacer = QWidget()
        history_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        history_bar_layout.addWidget(history_spacer)

        # Container (no vi-mode banner)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.history_bar)
        layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(container)
        self._position_toc_widget()

        # No overlay/indicator widgets; vi-mode is represented by editor cursor style

        # Build toolbar and main menus
        self._build_toolbar()
        # Add 'Vault' menu with required actions
        vault_menu = self.menuBar().addMenu("Vault")
        open_vault_action = QAction("Open Vault", self)
        open_vault_action.setToolTip("Open an existing vault")
        open_vault_action.triggered.connect(lambda checked=False: self._select_vault(spawn_new_process=False))
        vault_menu.addAction(open_vault_action)
        open_vault_new_win_action = QAction("Open Vault in New Window", self)
        open_vault_new_win_action.setToolTip("Launch a separate ZimX process for a vault")
        open_vault_new_win_action.triggered.connect(lambda checked=False: self._select_vault(spawn_new_process=True))
        vault_menu.addAction(open_vault_new_win_action)
        new_vault_action = QAction("New Vault", self)
        new_vault_action.setToolTip("Create a new vault")
        new_vault_action.triggered.connect(self._create_vault)
        vault_menu.addAction(new_vault_action)
        view_vault_disk_action = QAction("View Vault on Disk", self)
        view_vault_disk_action.setToolTip("Open the vault folder in your system file manager")
        view_vault_disk_action.triggered.connect(self._open_vault_on_disk)
        vault_menu.addAction(view_vault_disk_action)
        vault_menu.addSeparator()
        task_window_action = QAction("Open Task Panel Window", self)
        task_window_action.triggered.connect(self._open_task_panel_window)
        vault_menu.addAction(task_window_action)
        link_window_action = QAction("Open Link Navigator Window", self)
        link_window_action.triggered.connect(self._open_link_panel_window)
        vault_menu.addAction(link_window_action)
        ai_window_action = QAction("Open AI Chat Window", self)
        ai_window_action.triggered.connect(self._open_ai_chat_window)
        vault_menu.addAction(ai_window_action)

        self._register_shortcuts()
        self._vi_filter_targets: list[QObject] = []
        self._install_vi_mode_filters()
        self._focus_recent = ["editor", "tree", "right"]
        # Update focus borders and focus history when focus moves between widgets
        app = QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.connect(lambda old, now: self._on_focus_changed(now))
            except Exception:
                pass
        # Apply initial border state
        self._apply_focus_borders()
        self.statusBar().showMessage("Select a vault to get started")
        self._default_status_stylesheet = self.statusBar().styleSheet()
        self._setup_eventloop_watchdog()

        # Create status badges (Dirty + VI)
        self._badge_base_style = "border: 1px solid #666; padding: 2px 6px; border-radius: 3px;"

        self._dirty_status_label = QLabel("")
        self._dirty_status_label.setObjectName("dirtyStatusLabel")
        self._dirty_status_label.setStyleSheet(self._badge_base_style + " background-color: transparent; margin-right: 6px;")
        self._dirty_status_label.setToolTip("Unsaved changes")
        self.statusBar().addPermanentWidget(self._dirty_status_label, 0)

        self._vi_status_label = QLabel("VI")
        self._vi_status_label.setObjectName("viStatusLabel")
        self._vi_badge_base_style = self._badge_base_style
        self._vi_status_label.setStyleSheet(self._vi_badge_base_style + " background-color: transparent;")
        self.statusBar().addPermanentWidget(self._vi_status_label, 0)
        self._update_vi_badge()

        self._detached_panels: list[QMainWindow] = []
        self._detached_link_panels: list[LinkNavigatorPanel] = []

        # Keep dirty indicator in sync with edits
        try:
            self.editor.document().modificationChanged.connect(lambda _: self._update_dirty_indicator())
        except Exception:
            pass
        self._update_dirty_indicator()

        # Startup vault selection is orchestrated by main.py via .startup()
        self.editor.set_ai_actions_enabled(config.load_enable_ai_chats())

    def _setup_eventloop_watchdog(self) -> None:
        """Log when the Qt event loop appears stalled (high timer drift)."""
        if not PAGE_LOGGING_ENABLED:
            return
        try:
            self._loop_timer = QElapsedTimer()
            self._loop_timer.start()
            self._loop_watchdog = QTimer(self)
            self._loop_watchdog.setInterval(250)
            self._loop_watchdog.timeout.connect(self._check_eventloop_drift)
            self._loop_watchdog.start()
            dispatcher = QAbstractEventDispatcher.instance()
            if dispatcher:
                dispatcher.aboutToBlock.connect(lambda: self._mark_eventloop("aboutToBlock"))
                dispatcher.awake.connect(lambda: self._mark_eventloop("awake"))
        except Exception:
            pass

    def _mark_eventloop(self, phase: str) -> None:
        if not PAGE_LOGGING_ENABLED or not hasattr(self, "_loop_timer"):
            return
        elapsed = self._loop_timer.elapsed()
        print(f"[PageLoadAndRender] eventloop {phase} dt={elapsed:.1f}ms")
        self._loop_timer.restart()

    def _check_eventloop_drift(self) -> None:
        if not PAGE_LOGGING_ENABLED or not hasattr(self, "_loop_timer"):
            return
        elapsed = self._loop_timer.elapsed()
        if elapsed > 500:  # 0.5s threshold suggests the loop was blocked
            print(f"[PageLoadAndRender] eventloop drift warning dt={elapsed:.1f}ms (loop stall?)")
            self._loop_timer.restart()

    # --- UI wiring -----------------------------------------------------
    def _build_toolbar(self) -> None:
        self.toolbar = self.addToolBar("Main")
        self.toolbar.setMovable(False)
        
        # Home button (navigate to vault root page)
        home_action = QAction("Home", self)
        home_action.setIcon(self.style().standardIcon(QStyle.SP_DirHomeIcon))
        home_action.setToolTip("Go to vault home page")
        home_action.triggered.connect(self._go_home)
        self.toolbar.addAction(home_action)
        
        # Bookmark button (bold blue plus symbol)
        self.bookmark_button = QAction("Add Bookmark", self)
        self.bookmark_button.triggered.connect(self._add_bookmark)
        # Style the button text to be a bold blue plus symbol
        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        self.bookmark_button.setFont(font)
        # Set text as plus symbol
        self.bookmark_button.setText("+")
        # We'll apply color via stylesheet after adding to toolbar
        self.toolbar.addAction(self.bookmark_button)

        # History navigation buttons
        self.nav_back_action = QAction(self)
        self.nav_back_action.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.nav_back_action.setToolTip("Back (Alt+Left)")
        self.nav_back_action.triggered.connect(self._navigate_history_back)
        self.toolbar.addAction(self.nav_back_action)

        self.nav_forward_action = QAction(self)
        self.nav_forward_action.setIcon(self.style().standardIcon(QStyle.SP_ArrowForward))
        self.nav_forward_action.setToolTip("Forward (Alt+Right)")
        self.nav_forward_action.triggered.connect(self._navigate_history_forward)
        self.toolbar.addAction(self.nav_forward_action)
        
        # Add bookmark display area (will be populated with bookmark buttons)
        self.bookmark_container = QWidget()
        self.bookmark_layout = QHBoxLayout(self.bookmark_container)
        self.bookmark_layout.setContentsMargins(0, 0, 0, 0)
        self.bookmark_layout.setSpacing(4)
        self.toolbar.addWidget(self.bookmark_container)
        
        # Right-aligned spacer before preferences icon
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)

        # Preferences/settings cog icon
        prefs_action = QAction("Preferences", self)
        prefs_action.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        prefs_action.triggered.connect(self._open_preferences)
        self.toolbar.addAction(prefs_action)

        # Store default style to restore later
        self._default_toolbar_stylesheet = self.toolbar.styleSheet()
        
        # Apply blue color to bookmark button via stylesheet
        self.toolbar.setStyleSheet("""
            QToolButton[text="+"] {
                color: #4A90E2;
                font-size: 20pt;
                font-weight: bold;
            }
        """)

    def _open_vault_on_disk(self):
        """Open the vault folder in the system file manager."""
        vault_path = self.vault_root
        if not vault_path:
            self.statusBar().showMessage("No vault selected.")
            return
        opened = self._open_in_file_manager(Path(vault_path))
        if opened:
            self.statusBar().showMessage(f"Opened vault folder: {vault_path}")
        else:
            self._alert(f"Could not open vault folder: {vault_path}")

    def _register_shortcuts(self) -> None:
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._save_current_file)
        zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        zoom_in.activated.connect(lambda: self._adjust_font_size(1))
        zoom_out.activated.connect(lambda: self._adjust_font_size(-1))
        jump_shortcut = QShortcut(QKeySequence("Ctrl+J"), self)
        jump_shortcut.activated.connect(self._jump_to_page)
        link_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        link_shortcut.setContext(Qt.ApplicationShortcut)
        link_shortcut.activated.connect(self._insert_link)
        copy_link_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        copy_link_shortcut.setContext(Qt.ApplicationShortcut)
        copy_link_shortcut.activated.connect(self._copy_current_page_link)
        focus_tasks_shortcut = QShortcut(QKeySequence("Ctrl+\\"), self)
        focus_tasks_shortcut.setContext(Qt.ApplicationShortcut)
        focus_tasks_shortcut.activated.connect(self._focus_tasks_search)
        focus_tasks_shortcut2 = QShortcut(QKeySequence("Ctrl+Backslash"), self)
        focus_tasks_shortcut2.setContext(Qt.ApplicationShortcut)
        focus_tasks_shortcut2.activated.connect(self._focus_tasks_search)
        date_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        date_shortcut.activated.connect(self._insert_date)
        open_vault_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        open_vault_shortcut.activated.connect(lambda: self._select_vault(spawn_new_process=False))
        open_vault_new_win_shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), self)
        open_vault_new_win_shortcut.activated.connect(lambda: self._select_vault(spawn_new_process=True))
        # Focus toggle moved to Ctrl+Shift+Space; Alt+Space now toggles vi-mode
        focus_toggle = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        focus_toggle.activated.connect(self._toggle_focus_between_tree_and_editor)
        # Explicit heading popup shortcut (Ctrl+Shift+Tab)
        heading_popup = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        heading_popup.setContext(Qt.ApplicationShortcut)
        heading_popup.activated.connect(lambda: self._cycle_popup("heading", reverse=False))
        new_page_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        new_page_shortcut.activated.connect(self._show_new_page_dialog)
        journal_shortcut = QShortcut(QKeySequence("Alt+D"), self)
        journal_shortcut.activated.connect(self._open_journal_today)
        # Home shortcut: Alt+Home (works regardless of vi-mode state)
        home_shortcut = QShortcut(QKeySequence("Alt+Home"), self)
        home_shortcut.activated.connect(self._go_home)
        task_cycle = QShortcut(QKeySequence(Qt.Key_F12), self)
        task_cycle.activated.connect(self.editor.toggle_task_state)
        # Navigation shortcuts
        nav_back = QShortcut(QKeySequence("Alt+Left"), self)
        nav_forward = QShortcut(QKeySequence("Alt+Right"), self)
        nav_up = QShortcut(QKeySequence("Alt+Up"), self)
        nav_down = QShortcut(QKeySequence("Alt+Down"), self)
        nav_pg_up = QShortcut(QKeySequence("Alt+PgUp"), self)
        nav_pg_down = QShortcut(QKeySequence("Alt+PgDown"), self)
        reload_page = QShortcut(QKeySequence("Ctrl+R"), self)
        toggle_left = QShortcut(QKeySequence("Ctrl+Shift+B"), self)
        toggle_right = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        nav_back.activated.connect(self._navigate_history_back)
        nav_forward.activated.connect(self._navigate_history_forward)
        nav_up.activated.connect(self._navigate_hierarchy_up)
        nav_down.activated.connect(self._navigate_hierarchy_down)
        nav_pg_up.activated.connect(lambda: self._navigate_tree(-1, leaves_only=True))
        nav_pg_down.activated.connect(lambda: self._navigate_tree(1, leaves_only=True))
        reload_page.activated.connect(self._reload_current_page)
        toggle_left.activated.connect(self._toggle_left_panel)
        toggle_right.activated.connect(self._toggle_right_panel)

    def startup(self, vault_hint: Optional[str] = None) -> bool:
        """Handle initial vault selection before the window is shown."""
        default_vault = vault_hint or config.load_default_vault()
        if default_vault:
            if self._set_vault(default_vault):
                QTimer.singleShot(100, self._auto_load_initial_file)
                return True
            # Fall through to prompt for another vault if lock/bind failed
        return self._select_vault(startup=True)

    # --- Vault actions -------------------------------------------------
    def _select_vault(self, checked: bool | None = None, startup: bool = False, spawn_new_process: bool = False) -> bool:  # noqa: ARG002
        seed_vault = self.vault_root or config.load_last_vault()
        dialog = OpenVaultDialog(self, current_vault=seed_vault)
        if dialog.exec() != QDialog.Accepted:
            return False
        selection = dialog.selected_vault()
        if not selection:
            return False
        if spawn_new_process or dialog.selected_vault_new_window():
            self._launch_vault_process(selection["path"])
            return True
        if self._set_vault(selection["path"], vault_name=selection.get("name")):
            self._restore_recent_history()
            QTimer.singleShot(100, self._auto_load_initial_file)
            return True
        return False

    def _launch_new_window(self) -> None:
        """Spawn a fresh ZimX process so it gets its own API server and vault."""
        try:
            cmd = self._build_launch_command()
            if self.vault_root:
                cmd.extend(["--vault", self.vault_root])
            # Ask the new process to pick an ephemeral port to avoid clashes
            cmd.extend(["--port", "0"])
            subprocess.Popen(cmd, start_new_session=True)
            self.statusBar().showMessage("Launching new window...", 2000)
        except Exception as exc:  # pragma: no cover - UI path
            self._alert(f"Failed to launch new window: {exc}")

    def _launch_vault_process(self, vault_path: str) -> None:
        """Launch a new ZimX process targeting the given vault."""
        try:
            cmd = self._build_launch_command()
            cmd.extend(["--vault", vault_path, "--port", "0"])
            subprocess.Popen(cmd, start_new_session=True)
            self.statusBar().showMessage(f"Opening {vault_path} in a new window...", 3000)
        except Exception as exc:
            self._alert(f"Failed to open vault in new window: {exc}")

    @staticmethod
    def _build_launch_command() -> list[str]:
        """Return the command to start a new ZimX instance using the current runtime."""
        if getattr(sys, "frozen", False):
            # Packaged app: the executable already bootstraps ZimX
            return [sys.executable]
        # Dev/venv: use the same interpreter to launch the module
        return [sys.executable, "-m", "zimx.app.main"]

    def _create_vault(self) -> None:
        target_path = QFileDialog.getExistingDirectory(self, "Select Folder for Vault", str(Path.home()))
        if not target_path:
            return
        target = Path(target_path)
        try:
            # Check if folder is empty or ask for confirmation
            if target.exists():
                existing_items = list(target.iterdir())
                if existing_items:
                    reply = QMessageBox.question(
                        self,
                        "Use Existing Folder",
                        f"{target.name} is not empty. Create vault here anyway?",
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
            else:
                target.mkdir(parents=True)
            
            self._seed_vault(target)
        except OSError as exc:
            self._alert(f"Failed to create vault: {exc}")
            return
        self._set_vault(str(target), vault_name=target.name)

    def _seed_vault(self, root: Path) -> None:
        root_page = root / f"{root.name}{PAGE_SUFFIX}"
        if not root_page.exists():
            root_page.write_text(
                f"# {root.name}\n\nWelcome to your vault. Use the tree to add new pages or jump into Inbox to capture ideas.\n",
                encoding="utf-8",
            )
        starter_pages = [
            ("Inbox", "# Inbox\n\nCapture quick notes here.\n"),
            ("Journal", "# Journal\n\nUse the New Today action to create a dated entry.\n"),
            ("README", "# README\n\nDescribe how you plan to use this space.\n"),
        ]
        for name, body in starter_pages:
            page_dir = root / name
            page_dir.mkdir(parents=True, exist_ok=True)
            page_file = page_dir / f"{name}{PAGE_SUFFIX}"
            if not page_file.exists():
                page_file.write_text(body, encoding="utf-8")

    def _is_pid_active(self, pid: int, host: str) -> bool:
        """Best-effort check if a PID is alive on this host."""
        if host != socket.gethostname():
            return False
        try:
            os.kill(pid, 0)  # Does not terminate; raises if not permitted or missing
            return True
        except OSError:
            return False

    def _ensure_writable(self, action: str, *, interactive: bool = True) -> bool:
        """Guard write operations when the vault is opened read-only."""
        if self._read_only:
            if not interactive:
                self._alert(f"Vault is read-only because another ZimX window holds the lock.\nCannot {action}.")
            return False
        return True

    def _check_and_acquire_vault_lock(self, directory: str, prefer_read_only: bool = False) -> bool:
        """Create a simple lockfile in the vault; prompt if locked or forced read-only."""
        self._read_only = False
        root = Path(directory)
        lock_path = root / ".zimx" / "zimx.lock"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        existing: Optional[dict] = None
        if lock_path.exists():
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {"raw": lock_path.read_text(errors="ignore")}
        if existing:
            pid = existing.get("pid")
            host = existing.get("host")
            active = False
            if isinstance(pid, int) and isinstance(host, str):
                active = self._is_pid_active(pid, host)
            owner_text = f"{host or '?'} (pid {pid})"
            if active:
                msg = QMessageBox(self)
                msg.setWindowTitle("Read-Only Vault")
                msg.setIcon(QMessageBox.Warning)
                info = f" (owner: {owner_text})"
                msg.setText("Database is read only via settings or due to another instance" + info + ".\n\nOpen in read-only mode?")
                readonly_btn = msg.addButton("Open Read-Only", QMessageBox.AcceptRole)
                cancel_btn = msg.addButton(QMessageBox.Cancel)
                msg.setDefaultButton(readonly_btn)
                msg.exec()
                if msg.clickedButton() is not readonly_btn:
                    return False
                self._read_only = True
                # Do not take over the lock file
                self._vault_lock_path = None
                self._vault_lock_owner = None
                self._update_dirty_indicator()
                return True
            else:
                # Stale lock; remove it
                try:
                    lock_path.unlink()
                except Exception:
                    pass
        if prefer_read_only:
            # Show the same warning even when forced by settings
            msg = QMessageBox(self)
            msg.setWindowTitle("Read-Only Vault")
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Database is read only via settings or due to another instance.\n\nOpen in read-only mode?")
            readonly_btn = msg.addButton("Open Read-Only", QMessageBox.AcceptRole)
            cancel_btn = msg.addButton(QMessageBox.Cancel)
            msg.setDefaultButton(readonly_btn)
            msg.exec()
            if msg.clickedButton() is not readonly_btn:
                return False
            self._read_only = True
            self._vault_lock_path = None
            self._vault_lock_owner = None
            self._update_dirty_indicator()
            return True
        owner = {"pid": os.getpid(), "host": socket.gethostname(), "ts": time.time()}
        try:
            lock_path.write_text(json.dumps(owner), encoding="utf-8")
            self._vault_lock_path = lock_path
            self._vault_lock_owner = owner
        except Exception:
            # If we cannot write the lock, continue but warn the user
            self.statusBar().showMessage("Warning: could not write vault lock.", 5000)
        self._update_dirty_indicator()
        return True

    def _release_vault_lock(self, reset_read_only: bool = True) -> None:
        """Release the lock if we own it."""
        if not self._vault_lock_path:
            return
        path = self._vault_lock_path
        owner = self._vault_lock_owner or {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            if (
                current.get("pid") == owner.get("pid")
                and current.get("host") == owner.get("host")
            ):
                try:
                    path.unlink()
                except Exception:
                    pass
        self._vault_lock_path = None
        self._vault_lock_owner = None
        if reset_read_only:
            self._read_only = False
        self._update_dirty_indicator()

    def _apply_vault_read_only_pref(self) -> None:
        """Toggle read-only mode immediately based on the per-vault preference."""
        if not self.vault_root:
            return
        try:
            desired_read_only = config.load_vault_force_read_only()
        except Exception:
            desired_read_only = False
        if desired_read_only:
            if not self._read_only:
                # Drop any lock we hold and switch to read-only
                self._release_vault_lock(reset_read_only=False)
                self._read_only = True
                self._update_dirty_indicator()
            return
        # Preference allows writes; try to acquire lock if currently read-only
        if self._read_only:
            if self._check_and_acquire_vault_lock(self.vault_root):
                pass
            else:
                # Failed to acquire lock (likely held elsewhere); stay read-only
                self._read_only = True
                self._update_dirty_indicator()

    def _set_vault(self, directory: str, vault_name: Optional[str] = None) -> bool:
        # Persist current history before switching away
        self._persist_recent_history()
        # Release any existing lock before switching vaults
        self._release_vault_lock()
        # Close any previous vault DB connection
        config.set_active_vault(None)
        # Persist history before clearing
        self._persist_recent_history()
        prefer_read_only = False
        try:
            config.set_active_vault(directory)
            prefer_read_only = config.load_vault_force_read_only()
        except Exception:
            prefer_read_only = False
        if not self._check_and_acquire_vault_lock(directory, prefer_read_only=prefer_read_only):
            return False
        self.right_panel.clear_tasks()
        try:
            resp = self.http.post("/api/vault/select", json={"path": directory})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to set vault: {exc}")
            self._release_vault_lock()
            return False
        self.vault_root = resp.json().get("root")
        self.vault_root_name = Path(self.vault_root).name if self.vault_root else None
        index_dir_missing = False
        if self.vault_root:
            index_dir = Path(self.vault_root) / ".zimx"
            if not index_dir.exists():
                reply = QMessageBox.question(
                    self,
                    "No Vault Detected",
                    "No Vault Detected, Create new Index?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply != QMessageBox.Yes:
                    self.statusBar().showMessage("Vault open cancelled (no index).", 4000)
                    self.vault_root = None
                    self.vault_root_name = None
                    return
                index_dir_missing = True
        if self.vault_root:
            # ensure DB connection is set (may already be set above)
            config.set_active_vault(self.vault_root)
            config.save_last_vault(self.vault_root)
            display_name = vault_name or Path(self.vault_root).name
            config.remember_vault(self.vault_root, display_name)
            # Restore recent history (including cursor positions) for this vault
            self._restore_recent_history()
            try:
                if config.load_vault_force_read_only():
                    # Respect per-vault read-only preference; release any lock we took.
                    self._release_vault_lock(reset_read_only=False)
                    self._read_only = True
                    self._update_dirty_indicator()
                    # Intentionally no warning/toast; this is a user preference.
            except Exception:
                pass
            self.font_size = config.load_font_size(self.font_size)
            self.editor.set_font_point_size(self.font_size)
            # Load show_journal setting
            show_journal = config.load_show_journal()
            self.show_journal_button.setChecked(show_journal)
        self.editor.set_context(self.vault_root, None)
        self.editor.set_markdown("")
        self.current_path = None
        self.right_panel.set_current_page(None, None)
        self.statusBar().showMessage(f"Vault: {self.vault_root}")
        self._update_window_title()
        self._populate_vault_tree()
        
        # Check if index is empty and rebuild if needed
        needs_index = index_dir_missing or config.is_vault_index_empty()
        self._reindex_vault(show_progress=needs_index)
        
        self._load_bookmarks()
        if self.vault_root:
            self.right_panel.set_vault_root(self.vault_root)
        
        # Restore window geometry and splitter positions
        self._restore_geometry()
        return True

    def _add_bookmark(self) -> None:
        """Add the current page to bookmarks."""
        if not self.current_path:
            self.statusBar().showMessage("No page open to bookmark", 3000)
            return
        
        # Check if already bookmarked
        if self.current_path in self.bookmarks:
            self.statusBar().showMessage("Page already bookmarked", 3000)
            return
        
        # Add to beginning of list (leftmost position)
        self.bookmarks.insert(0, self.current_path)
        config.save_bookmarks(self.bookmarks)
        self._refresh_bookmark_buttons()
        
        # Show feedback
        page_name = Path(self.current_path).stem
        self.statusBar().showMessage(f"Bookmarked: {page_name}", 3000)

    def _on_show_journal_toggled(self, checked: bool) -> None:
        """Handle Show Journal checkbox toggle."""
        if config.has_active_vault():
            config.save_show_journal(checked)
        self._populate_vault_tree()
    
    def _load_bookmarks(self) -> None:
        """Load bookmarks from config and refresh display."""
        if not config.has_active_vault():
            return
        self.bookmarks = config.load_bookmarks()
        self._refresh_bookmark_buttons()

    def _save_geometry(self) -> None:
        """Save window geometry and splitter positions."""
        if not config.has_active_vault():
            return
        
        # Save window geometry (size and position)
        geometry = self.saveGeometry().toBase64().data().decode('ascii')
        config.save_window_geometry(geometry)
        if _DETAILED_LOGGING:
            print(f"[Geometry] Saved window geometry: {len(geometry)} chars")
        # Persist history on close/resize save
        self._persist_recent_history()
        
        # Save main splitter state (tree vs editor+right panel)
        splitter_state = self.main_splitter.saveState().toBase64().data().decode('ascii')
        config.save_splitter_state(splitter_state)
        if _DETAILED_LOGGING:
            print(f"[Geometry] Saved main splitter state: {len(splitter_state)} chars")
        
        # Save editor splitter state (editor vs right panel)
        editor_splitter_state = self.editor_split.saveState().toBase64().data().decode('ascii')
        config.save_editor_splitter_state(editor_splitter_state)
        if _DETAILED_LOGGING:
            print(f"[Geometry] Saved editor splitter state: {len(editor_splitter_state)} chars")

    def _restore_geometry(self) -> None:
        """Restore window geometry and splitter positions."""
        if not config.has_active_vault():
            if _DETAILED_LOGGING:
                print("[Geometry] No active vault, skipping restore")
            return
        
        # Restore window geometry
        geometry_str = config.load_window_geometry()
        if geometry_str:
            if _DETAILED_LOGGING:
                print(f"[Geometry] Restoring window geometry: {len(geometry_str)} chars")
            from PySide6.QtCore import QByteArray
            geometry = QByteArray.fromBase64(geometry_str.encode('ascii'))
            result = self.restoreGeometry(geometry)
            if _DETAILED_LOGGING:
                print(f"[Geometry] Window geometry restore result: {result}")
        else:
            if _DETAILED_LOGGING:
                print("[Geometry] No saved window geometry found")
        
        # Restore main splitter state
        splitter_state_str = config.load_splitter_state()
        if splitter_state_str:
            if _DETAILED_LOGGING:
                print(f"[Geometry] Restoring main splitter state: {len(splitter_state_str)} chars")
            from PySide6.QtCore import QByteArray
            splitter_state = QByteArray.fromBase64(splitter_state_str.encode('ascii'))
            result = self.main_splitter.restoreState(splitter_state)
            if _DETAILED_LOGGING:
                print(f"[Geometry] Main splitter restore result: {result}")
        else:
            if _DETAILED_LOGGING:
                print("[Geometry] No saved main splitter state found")
        
        # Restore editor splitter state
        editor_splitter_state_str = config.load_editor_splitter_state()
        if editor_splitter_state_str:
            if _DETAILED_LOGGING:
                print(f"[Geometry] Restoring editor splitter state: {len(editor_splitter_state_str)} chars")
            from PySide6.QtCore import QByteArray
            editor_splitter_state = QByteArray.fromBase64(editor_splitter_state_str.encode('ascii'))
            result = self.editor_split.restoreState(editor_splitter_state)
            if _DETAILED_LOGGING:
                print(f"[Geometry] Editor splitter restore result: {result}")
        else:
            if _DETAILED_LOGGING:
                print("[Geometry] No saved editor splitter state found")

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Save splitter positions when moved (debounced)."""
        self.geometry_save_timer.start()

    def _refresh_bookmark_buttons(self) -> None:
        """Refresh the bookmark buttons in the toolbar."""
        # Clear existing buttons
        for btn in list(self.bookmark_buttons.values()):
            self.bookmark_layout.removeWidget(btn)
            btn.deleteLater()
        self.bookmark_buttons.clear()
        
        # Add buttons for each bookmark
        for bookmark_path in self.bookmarks:
            # Extract leaf node name (page name)
            page_name = Path(bookmark_path).stem
            
            # Create button as a QPushButton with context menu
            btn = QPushButton(page_name)
            btn.setToolTip(path_to_colon(bookmark_path) or bookmark_path)
            btn.clicked.connect(lambda checked=False, p=bookmark_path: self._open_bookmark(p))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, p=bookmark_path, b=btn: self._show_bookmark_context_menu(pos, p, b)
            )
            
            # Store button in dict for later removal
            self.bookmark_buttons[bookmark_path] = btn
            
            # Add to layout
            self.bookmark_layout.addWidget(btn)

    def _refresh_history_buttons(self) -> None:
        """Refresh the history buttons in the toolbar (last 10 pages visited)."""
        # Clear existing buttons
        for btn in self.history_buttons:
            self.history_layout.removeWidget(btn)
            btn.deleteLater()
        self.history_buttons.clear()
        
        # Get last 25 items from history (most recent last)
        recent_history = self.page_history[-18:] if len(self.page_history) > 18 else self.page_history[:]
        
        # Remove duplicates while preserving order (keep most recent occurrence)
        seen = set()
        unique_history = []
        for page_path in reversed(recent_history):
            if page_path not in seen:
                seen.add(page_path)
                unique_history.append(page_path)
        unique_history.reverse()  # Restore original order (oldest to newest)
        
        # Add buttons for each history item
        for page_path in unique_history:
            # Extract page name
            page_name = Path(page_path).stem
            
            # Create button with border styling
            btn = QPushButton(page_name)
            btn.setStyleSheet("QPushButton { border: 1px solid #555; padding: 2px 6px; border-radius: 3px; }")
            btn.setToolTip(path_to_colon(page_path) or page_path)
            btn.clicked.connect(lambda checked=False, p=page_path: self._open_history_page(p))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, p=page_path, b=btn: self._show_history_context_menu(pos, p, b))
            
            # Store button
            self.history_buttons.append(btn)
            
            # Add to layout
            self.history_layout.addWidget(btn)

    def _open_history_page(self, page_path: str) -> None:
        """Open a page from history and update tree selection."""
        # Path is already in colon format, just open it directly (same as bookmarks)
        self._remember_history_cursor()
        try:
            self._suspend_selection_open = True
            self._select_tree_path(page_path)
        finally:
            self._suspend_selection_open = False
        self._open_file(page_path, add_to_history=False, restore_history_cursor=True)  # Don't add to history again

    def _show_history_context_menu(self, pos: QPoint, page_path: str, button: QWidget) -> None:
        """Show context menu for a history button."""
        menu = QMenu(self)
        open_win = menu.addAction("Open in Editor Window")
        open_win.triggered.connect(lambda: self._open_page_editor_window(page_path))
        global_pos = button.mapToGlobal(pos)
        menu.exec(global_pos)

    def _auto_load_initial_file(self) -> None:
        """Auto-load the last opened file or vault home page on startup."""
        if not self.vault_root or not self.vault_root_name:
            return
        
        # Try to load the last opened file
        last_file = config.load_last_file()
        if last_file:
            # Verify the file still exists
            try:
                abs_path = Path(self.vault_root) / last_file.lstrip("/")
                if abs_path.exists():
                    self._select_tree_path(last_file)
                    self._open_file(last_file)
                    return
            except Exception:
                pass
        
        # Fall back to vault home page
        self._go_home()
    
    def _go_home(self) -> None:
        """Navigate to the vault's root page (page with same name as vault)."""
        if not self.vault_root or not self.vault_root_name:
            self.statusBar().showMessage("No vault selected", 3000)
            return
        
        # Construct path to root page: /VaultName/VaultName.txt
        home_path = f"/{self.vault_root_name}{PAGE_SUFFIX}"
        
        # Clear tree selection
        self.tree_view.clearSelection()
        
        # Open the home page
        self._open_file(home_path)
        self.statusBar().showMessage(f"Home: {self.vault_root_name}", 2000)

    def _open_bookmark(self, path: str) -> None:
        """Open a bookmarked page."""
        self._select_tree_path(path)
        self._open_file(path)

    def _show_bookmark_context_menu(self, pos: QPoint, bookmark_path: str, button: QWidget) -> None:
        """Show context menu for bookmark with Remove option."""
        menu = QMenu(self)
        open_win = menu.addAction("Open in Editor Window")
        open_win.triggered.connect(lambda: self._open_page_editor_window(bookmark_path))
        menu.addSeparator()
        remove_action = menu.addAction("Remove")
        remove_action.triggered.connect(lambda: self._remove_bookmark(bookmark_path))
        
        # Show menu at global position relative to button
        global_pos = button.mapToGlobal(pos)
        menu.exec(global_pos)

    def _remove_bookmark(self, path: str) -> None:
        """Remove a bookmark from the list."""
        if path in self.bookmarks:
            self.bookmarks.remove(path)
            config.save_bookmarks(self.bookmarks)
            self._refresh_bookmark_buttons()
            
            page_name = Path(path).stem
            self.statusBar().showMessage(f"Removed bookmark: {page_name}", 3000)

    def _set_nav_filter(self, path: str) -> None:
        """Enable tree filter for the given folder path."""
        if not path:
            return
        self._nav_filter_path = path if path.startswith("/") else f"/{path}"
        try:
            self.right_panel.task_panel.set_navigation_filter(self._nav_filter_path, refresh=False)
        except Exception:
            pass
        self._populate_vault_tree()
        self.tree_view.expandAll()
        self._apply_nav_filter_style()

    def _clear_nav_filter(self) -> None:
        """Disable tree filter and restore full view."""
        if not self._nav_filter_path:
            # Still collapse on escape even if no filter is active
            self.tree_view.collapseAll()
            return
        self._nav_filter_path = None
        try:
            self.right_panel.task_panel.set_navigation_filter(None, refresh=False)
        except Exception:
            pass
        self._populate_vault_tree()
        self.tree_view.collapseAll()
        self._apply_nav_filter_style()

    def _apply_nav_filter_style(self) -> None:
        """Refresh focus borders to reflect filter state."""
        self._apply_focus_borders()

    def _populate_vault_tree(self) -> None:
        self._cancel_inline_editor()
        if not self.vault_root:
            return
        try:
            resp = self.http.get("/api/vault/tree")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to load vault tree: {exc}")
            return
        data = resp.json().get("tree", [])
        self.tree_model.removeRows(0, self.tree_model.rowCount())

        # Add synthetic vault root node (fixed at top, opens vault home page) and nest tree under it
        synthetic_root_item = None
        add_synthetic_root = self.vault_root_name and not self._nav_filter_path
        if add_synthetic_root:
            synthetic_root = {
                "name": self.vault_root_name,
                "path": "/",  # use root path so delete isn't offered
                "open_path": f"/{self.vault_root_name}{PAGE_SUFFIX}",
                "children": [],
            }
            synthetic_root_item = self._add_tree_node(self.tree_model.invisibleRootItem(), synthetic_root)
        elif self._nav_filter_path:
            banner = QStandardItem("Filtered (Remove)")
            font = banner.font()
            font.setBold(True)
            banner.setFont(font)
            banner.setEditable(False)
            banner.setForeground(QBrush(QColor("#ffffff")))
            banner.setBackground(QBrush(QColor("#c62828")))
            banner.setData(FILTER_BANNER, PATH_ROLE)
            self.tree_model.invisibleRootItem().appendRow(banner)
        
        # Check if Journal should be filtered
        show_journal = self.show_journal_button.isChecked()
        
        self._full_tree_data = data
        filtered_data = data
        if self._nav_filter_path:
            filtered_data = self._filter_tree_data(data, self._nav_filter_path)

        for node in filtered_data:
            # Hide the root vault node (path == "/") and render only its children under synthetic root
            if node.get("path") == "/":
                for child in node.get("children", []):
                    # Filter out Journal folder if checkbox is unchecked
                    if not show_journal and child.get("name") == "Journal":
                        continue
                    parent_item = synthetic_root_item or self.tree_model.invisibleRootItem()
                    self._add_tree_node(parent_item, child)
            else:
                parent_item = synthetic_root_item or self.tree_model.invisibleRootItem()
                self._add_tree_node(parent_item, node)
        self.tree_view.expandAll()
        if self._pending_selection:
            self._select_tree_path(self._pending_selection)
            self._pending_selection = None
        self.right_panel.refresh_tasks()
        self.right_panel.refresh_calendar()
        self._apply_nav_filter_style()

    def _add_tree_node(self, parent: QStandardItem, node: dict) -> None:
        item = QStandardItem(node["name"])
        folder_path = node.get("path")
        open_path = node.get("open_path")
        has_children = bool(node.get("children"))
        item.setData(folder_path, PATH_ROLE)
        item.setData(has_children, TYPE_ROLE)
        item.setData(open_path, OPEN_ROLE)
        icon = self.dir_icon if has_children or folder_path == "/" else self.file_icon
        item.setIcon(icon)
        item.setEditable(False)
        
        # Check if this is a virtual (unsaved) page
        if open_path and open_path in self.virtual_pages:
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        
        parent.appendRow(item)
        for child in node.get("children", []):
            self._add_tree_node(item, child)

    def _filter_tree_data(self, nodes: list[dict], prefix: str) -> list[dict]:
        """Return a pruned copy of the vault tree limited to prefix and its descendants."""
        result: list[dict] = []
        for node in nodes:
            path = node.get("path") or ""
            children = node.get("children", [])
            filtered_children = self._filter_tree_data(children, prefix)
            if prefix == "/":
                include_as_node = True
            else:
                include_as_node = path and path.startswith(prefix)
            if include_as_node:
                clone = dict(node)
                clone["children"] = filtered_children
                result.append(clone)
            elif filtered_children:
                result.extend(filtered_children)
        return result

    def _on_selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self._debug(
            f"Tree selection changed: current={self._describe_index(current)}, previous={self._describe_index(previous)}"
        )
        restore_tree_focus = self._tree_arrow_focus_pending and self.tree_view.hasFocus()
        # One-shot flag: consume after evaluating
        self._tree_arrow_focus_pending = False
        if self._skip_next_selection_open:
            self._skip_next_selection_open = False
            return
        if previous.isValid():
            prev_target = previous.data(OPEN_ROLE) or previous.data(PATH_ROLE)
            if prev_target and prev_target == self.current_path:
                # Check if leaving an unsaved virtual page
                if self.current_path in self.virtual_pages:
                    self._cleanup_virtual_page_if_unchanged(self.current_path)
        if not current.isValid():
            self._debug("Tree selection cleared (no valid index).")
            return
        # If we're programmatically changing selection (history/hierarchy nav), don't auto-open here
        if self._suspend_selection_open:
            self._debug("Selection change suppressed (programmatic nav).")
            return
        open_target = current.data(OPEN_ROLE) or current.data(PATH_ROLE)
        if open_target == FILTER_BANNER:
            self._clear_nav_filter()
            return
        self._debug(f"Tree selection target resolved to: {open_target!r}")
        if not open_target:
            self._debug("Tree selection skipped: no open target.")
            return
        if open_target == self.current_path:
            self._debug("Tree selection skipped: already editing this path.")
            return
        try:
            self._open_file(open_target)
            if restore_tree_focus:
                self.tree_view.setFocus(Qt.OtherFocusReason)
                self._apply_focus_borders()
        except Exception as exc:
            self._debug(f"Tree selection crash while opening {open_target!r}: {exc!r}")
            raise

    def _open_file(self, path: str, retry: bool = False, add_to_history: bool = True, force: bool = False, cursor_at_end: bool = False, restore_history_cursor: bool = False) -> None:
        if not path or (path == self.current_path and not force):
            return
        tracer = PageLoadLogger(path) if PAGE_LOGGING_ENABLED else None
        # Save current page if dirty before switching
        if self.current_path and path != self.current_path:
            self._save_dirty_page()
        
        # Clean up current page if it's an unchanged virtual page
        if self.current_path and self.current_path in self.virtual_pages:
            self._cleanup_virtual_page_if_unchanged(self.current_path)
        
        self.autosave_timer.stop()
        if tracer:
            tracer.mark("api read start")
        
        # Add to page history (unless we're navigating through history)
        if add_to_history and path != self.current_path:
            # Remove any forward history when opening a new page
            if self.history_index < len(self.page_history) - 1:
                self.page_history = self.page_history[:self.history_index + 1]
            # Add new page if not duplicate of last
            if not self.page_history or self.page_history[-1] != path:
                self.page_history.append(path)
                self.history_index = len(self.page_history) - 1
                # Refresh history buttons
                self._refresh_history_buttons()
        
        try:
            resp = self.http.post("/api/file/read", json={"path": path})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            print(f"[ZimX] Failed to read page {path}: status={exc.response.status_code if exc.response else 'unknown'} body={exc.response.text if exc.response else ''}", file=sys.stderr)
            detail = exc.response.text if exc.response else str(exc)
            if tracer:
                tracer.mark(f"api read failed ({detail})")
            self._alert(f"Failed to open {path}: {detail}")
            return
        except httpx.HTTPError as exc:
            print(f"[ZimX] Failed to read page {path}: {exc}", file=sys.stderr)
            if tracer:
                tracer.mark(f"api read failed ({exc})")
            self._alert(f"Failed to open {path}: {exc}")
            return
        content = resp.json().get("content", "")
        if tracer:
            try:
                content_len = len(content.encode("utf-8"))
            except Exception:
                content_len = len(content or "")
            tracer.mark(f"api read complete bytes={content_len}")
        self.editor.set_context(self.vault_root, path)
        if tracer:
            tracer.mark("editor context set")
        # Hand logger to the editor so rendering steps are captured
        try:
            self.editor.set_page_load_logger(tracer)
        except Exception:
            pass
        self.current_path = path
        self._suspend_autosave = True
        self.editor.set_markdown(content)
        self._suspend_autosave = False
        if tracer:
            tracer.mark("editor content applied")
        # Mark buffer clean for dirty tracking
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        self._last_saved_content = self.editor.to_markdown()
        self._update_dirty_indicator()
        updated = indexer.index_page(path, content)
        if updated:
            self.right_panel.refresh_tasks()
        if tracer:
            tracer.mark(f"index refresh {'+ tasks' if updated else '(no task changes)'}")
        # Keep Link Navigator in sync when a page is opened or reloaded
        self.right_panel.refresh_links(path)
        self._refresh_detached_link_panels(path)
        if tracer:
            tracer.mark("right panel links refreshed")
        move_cursor_to_end = cursor_at_end or self._should_focus_hr_tail(content)
        restored_history_cursor = False
        if restore_history_cursor:
            saved_pos = self._history_cursor_positions.get(path)
            if saved_pos is not None:
                cursor = self.editor.textCursor()
                cursor.setPosition(min(saved_pos, len(self.editor.toPlainText())))
                self.editor.setTextCursor(cursor)
                restored_history_cursor = True
                move_cursor_to_end = False
        if move_cursor_to_end:
            cursor = self.editor.textCursor()
            display_length = len(self.editor.toPlainText())
            cursor.setPosition(display_length)
            self.editor.setTextCursor(cursor)
        elif not restored_history_cursor:
            self.editor.moveCursor(QTextCursor.Start)
        # Always show editing status; vi-mode banner is separate
        display_path = path_to_colon(path) or path
        if hasattr(self, "toc_widget"):
            root_base = ensure_root_colon_link(display_path) if display_path else ""
            self.toc_widget.set_base_path(root_base)
            self.editor.refresh_heading_outline()
        self.statusBar().showMessage(f"Editing {display_path}")
        self._update_window_title()
        
        # Save the last opened file
        if config.has_active_vault():
            config.save_last_file(path)
            # Refresh read-only badge if preference or lock state changed mid-session
            self._update_dirty_indicator()
        
        # Update calendar if this is a journal page
        self._update_calendar_for_journal_page(path)
        
        # Update attachments panel with current page
        from pathlib import Path
        if path:
            full_path = Path(self.vault_root) / path.lstrip("/")
            has_chat = self.right_panel.set_current_page(full_path, path)
            self.editor.set_ai_chat_available(has_chat)
        else:
            self.right_panel.set_current_page(None, None)
            self.editor.set_ai_chat_available(False)
        if tracer:
            tracer.end("ready for edit")
            # Set up a defensive stack dump if the Qt loop does not resume quickly.
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(5.0, repeat=False)
            loop_start = time.perf_counter()
            QTimer.singleShot(
                0,
                lambda: (
                    faulthandler.cancel_dump_traceback_later(),
                    tracer.mark(
                        f"qt loop resumed post-open delay={(time.perf_counter() - loop_start)*1000:.1f}ms"
                    ),
                ),
            )

    def _save_current_file(self, auto: bool = False) -> None:
        if self._suspend_autosave:
            self._debug("Autosave suppressed (suspend flag set).")
            return
        if auto and self._read_only:
            # In read-only mode, silently skip autosaves/background saves
            return
        # Autosave should silently skip when read-only; explicit Ctrl+S should warn.
        if not self._ensure_writable("save changes", interactive=not auto):
            return
        if not self.current_path:
            if not auto:
                self._alert("No file selected to save.")
            return
        editor_path = self.editor.current_relative_path()
        if editor_path and self.current_path and editor_path != self.current_path:
            self._debug(
                f"Autosave skipped due to path mismatch editor={editor_path} window={self.current_path}"
            )
            return
        
        # Check if this is a virtual page with unchanged content
        if self.current_path in self.virtual_pages:
            current_content = self.editor.to_markdown()
            original_content = self.virtual_page_original_content.get(self.current_path)
            
            # If content hasn't changed from the template, don't save
            if original_content is not None and current_content == original_content:
                self._debug(f"Virtual page {self.current_path} unchanged from template, skipping save.")
                # Still stop the timer to prevent repeated attempts
                self.autosave_timer.stop()
                self._last_saved_content = current_content
                try:
                    self.editor.document().setModified(False)
                except Exception:
                    pass
                self._update_dirty_indicator()
                return
            
            # Content has changed, ensure folders exist before saving
            folder_path = self._file_path_to_folder(self.current_path)
            if not self._ensure_page_folder(folder_path, allow_existing=True):
                if not auto:
                    self._alert(f"Failed to create folder for {self.current_path}")
                return
        
        payload = {"path": self.current_path, "content": self.editor.to_markdown()}
        try:
            resp = self.http.post("/api/file/write", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            if not auto:
                self._alert(f"Failed to save {self.current_path}: {exc}")
            return
        if config.has_active_vault():
            indexer.index_page(self.current_path, payload["content"])
            self.right_panel.refresh_tasks()
            self.right_panel.refresh_links(self.current_path)
        self._last_saved_content = payload["content"]
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        self._update_dirty_indicator()
        
        # Mark page as saved (remove from virtual pages)
        was_virtual = self.current_path in self.virtual_pages
        if was_virtual:
            self.virtual_pages.discard(self.current_path)
            self.virtual_page_original_content.pop(self.current_path, None)
            self._populate_vault_tree()  # Refresh to remove italics
            self.right_panel.refresh_calendar()  # Update calendar bold dates
        
        self.autosave_timer.stop()
        message = "Auto-saved" if auto else "Saved"
        display_path = path_to_colon(self.current_path) if self.current_path else ""
        self.statusBar().showMessage(f"{message} {display_path}", 2000 if auto else 4000)
        # Refresh any popup editors on the same page
        try:
            for win in list(getattr(self, "_page_windows", [])):
                if getattr(win, "_source_path", None) == self.current_path:
                    win._load_content()
        except Exception:
            pass

    def _is_editor_dirty(self) -> bool:
        """Return True if the buffer differs from last saved content."""
        if not self.current_path:
            return False
        current = self.editor.to_markdown()
        return current != (self._last_saved_content or "")

    def _save_dirty_page(self) -> None:
        """Save the current page if there are unsaved edits."""
        if self._read_only:
            return
        if self._is_editor_dirty():
            self._save_current_file(auto=True)

    def _open_journal_today(self) -> None:
        if not self.vault_root:
            self._alert("Select a vault before creating journal entries.")
            return
        # Build day template string from templates/JournalDay.txt with substitution
        day_template = ""
        try:
            templates_root = Path(__file__).parent.parent.parent / "templates"
            day_tpl = templates_root / "JournalDay.txt"
            if day_tpl.exists():
                from datetime import datetime
                now = datetime.now()
                vars_map = {
                    "{{YYYY}}": f"{now:%Y}",
                    "{{Month}}": now.strftime("%B"),
                    "{{DOW}}": now.strftime("%A"),
                    "{{dd}}": f"{now:%d}",
                }
                raw = day_tpl.read_text(encoding="utf-8")
                for k, v in vars_map.items():
                    raw = raw.replace(k, v)
                day_template = raw
        except Exception:
            day_template = ""

        try:
            resp = self.http.post("/api/journal/today", json={"template": day_template})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create journal entry: {exc}")
            return
        payload = resp.json()
        path = payload.get("path")
        created = payload.get("created", False)
        if path:
            # Repopulate tree so newly created nested year/month/day nodes appear
            self._pending_selection = path
            self._populate_vault_tree()
            # Apply journal templates (year/month/day) if newly created
            self._apply_journal_templates(path, allow_overwrite=created)
            # Open with cursor at end for immediate typing
            self._debug(f"Journal shortcut: forcing reload for {path}")
            self._open_file(path, force=True, cursor_at_end=True)
            self.statusBar().showMessage("Journal: today", 4000)
            # Ensure focus returns to editor (tree selection may have taken focus)
            self.editor.setFocus()
            self._apply_focus_borders()

    def _apply_journal_templates(self, day_file_path: str, allow_overwrite: bool = True) -> None:
        """Ensure year/month/day journal scaffolding exists and apply templates if allowed.

        day_file_path: relative file path like /Journal/2025/11/12/12.txt (from API)
        Templates: JournalYear.txt, JournalMonth.txt, JournalDay.txt
        Variables: {{YYYY}}, {{Month}}, {{DOW}}, {{dd}}
        allow_overwrite: when False, existing files are left untouched (no template writes)
        """
        if not self.vault_root:
            return
        from datetime import datetime
        now = datetime.now()
        year_str = f"{now:%Y}"
        month_num = f"{now:%m}"  # zero-padded numeric month (folder name)
        month_name = now.strftime("%B")  # English month name
        day_num = f"{now:%d}"  # zero-padded day
        dow_name = now.strftime("%A")

        vault_root = Path(self.vault_root)
        # Derive folders
        journal_root = vault_root / "Journal"
        year_dir = journal_root / year_str
        month_dir = year_dir / month_num
        day_dir = month_dir / day_num

        # Page files (name matches folder name)
        year_page = year_dir / f"{year_dir.name}{PAGE_SUFFIX}"
        month_page = month_dir / f"{month_dir.name}{PAGE_SUFFIX}"
        day_page = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"

        # Load templates
        templates_root = Path(__file__).parent.parent.parent / "templates"
        year_tpl = templates_root / "JournalYear.txt"
        month_tpl = templates_root / "JournalMonth.txt"
        day_tpl = templates_root / "JournalDay.txt"

        vars_map = {
            "{{YYYY}}": year_str,
            "{{Month}}": month_name,
            "{{DOW}}": dow_name,
            "{{dd}}": day_num,
        }

        def render(template_path: Path) -> str:
            try:
                raw = template_path.read_text(encoding="utf-8")
            except Exception:
                return ""
            out = raw
            for k, v in vars_map.items():
                out = out.replace(k, v)
            return out

        # Create missing directories
        year_dir.mkdir(parents=True, exist_ok=True)
        month_dir.mkdir(parents=True, exist_ok=True)
        day_dir.mkdir(parents=True, exist_ok=True)

        # Helper to decide if we overwrite (only when file absent or trivially small)
        def needs_write(path: Path) -> bool:
            if not path.exists():
                return True
            try:
                size = path.stat().st_size
            except OSError:
                return False
            return size < 20  # heuristic: very small stub header

        # Year
        if allow_overwrite and needs_write(year_page) and year_tpl.exists():
            content = render(year_tpl)
            if content:
                year_page.write_text(content, encoding="utf-8")
        # Month
        if allow_overwrite and needs_write(month_page) and month_tpl.exists():
            content = render(month_tpl)
            if content:
                month_page.write_text(content, encoding="utf-8")
        # Day
        def needs_day_write(path: Path) -> bool:
            if not path.exists():
                return True
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                return False
            stripped = text.strip()
            # Consider it a stub if very short OR only header lines (<=3 lines)
            if not stripped:
                return True
            lines = [ln for ln in stripped.splitlines() if ln.strip()]
            if len(lines) <= 3 and len(stripped) < 160:
                return True
            return False
        if allow_overwrite and needs_day_write(day_page) and day_tpl.exists():
            content = render(day_tpl)
            if content:
                day_page.write_text(content, encoding="utf-8")
        # Always perform a substitution pass on existing day page if placeholders remain
        if allow_overwrite:
            try:
                existing = day_page.read_text(encoding="utf-8")
                if any(token in existing for token in ("{{YYYY}}","{{Month}}","{{DOW}}","{{dd}}")):
                    replaced = existing
                    for k,v in vars_map.items():
                        replaced = replaced.replace(k, v)
                    if replaced != existing:
                        day_page.write_text(replaced, encoding="utf-8")
            except Exception:
                pass

    def _on_image_saved(self, filename: str) -> None:
        self.statusBar().showMessage(f"Image pasted as {filename}", 5000)
        # Refresh attachments panel to show the new image
        self.right_panel.refresh_attachments()

    def _on_attachment_dropped(self, filename: str) -> None:
        """Force-save the current page after a dropped attachment inserts content."""
        self._save_current_file(auto=True)
        self.statusBar().showMessage(f"Saved after dropping {filename}", 3000)

    def _jump_to_page(self) -> None:
        if not config.has_active_vault():
            return
        
        vi_was_active = self._suspend_vi_mode_for_dialog()
        filter_prefix = self._nav_filter_path
        filter_label = path_to_colon(filter_prefix) if filter_prefix else None
        dlg = JumpToPageDialog(
            self,
            filter_prefix=filter_prefix,
            filter_label=filter_label,
            clear_filter_cb=self._clear_nav_filter,
        )
        result = dlg.exec()
        
        if result == QDialog.Accepted:
            target = dlg.selected_path()
            if target:
                self._select_tree_path(target)
                self._open_file(target)
        
        self._restore_vi_mode_after_dialog(vi_was_active)

    def _insert_link(self) -> None:
        """Open insert link dialog and insert selected link at cursor."""
        if not config.has_active_vault():
            return
        # Save current page before inserting link to ensure it's indexed
        if self.current_path:
            self._save_current_file(auto=True)
        
        vi_was_active = self._suspend_vi_mode_for_dialog()
        
        # Get selected text if any
        editor_cursor = self.editor.textCursor()
        selection_range: tuple[int, int] | None = None
        selected_text = ""
        if editor_cursor.hasSelection():
            selection_range = (editor_cursor.selectionStart(), editor_cursor.selectionEnd())
            selected_text = editor_cursor.selectedText()
            # Clean up selected text - remove line breaks and paragraph separators
            # Qt returns paragraph separators as U+2029 which cause line breaks in links
            selected_text = selected_text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()
        
        filter_prefix = self._nav_filter_path
        filter_label = path_to_colon(filter_prefix) if filter_prefix else None
        dlg = InsertLinkDialog(
            self,
            selected_text=selected_text,
            filter_prefix=filter_prefix,
            filter_label=filter_label,
            clear_filter_cb=self._clear_nav_filter,
        )
        self.editor.begin_dialog_block()
        try:
            result = dlg.exec()
        finally:
            self.editor.end_dialog_block()
            # Always restore focus to the editor after dialog closes
            QTimer.singleShot(0, self.editor.setFocus)

        inserted = False
        if result == QDialog.Accepted:
            colon_path = dlg.selected_colon_path()
            link_name = dlg.selected_link_name()
            if colon_path:
                # If there was selected text, replace it with the link
                if selection_range:
                    cursor = self.editor.textCursor()
                    start, end = selection_range
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.KeepAnchor)
                    cursor.removeSelectedText()
                    self.editor.setTextCursor(cursor)
                label = link_name or selected_text or colon_path
                self.editor.insert_link(colon_path, label)
                inserted = True

        self._restore_vi_mode_after_dialog(vi_was_active)

    def _insert_date(self) -> None:
        """Show calendar/date dialog and insert selected date."""
        if not self.vault_root:
            self._alert("Select a vault before inserting dates.")
            return
        vi_was_active = self._suspend_vi_mode_for_dialog()
        cursor_rect = self.editor.cursorRect()
        anchor = self.editor.viewport().mapToGlobal(cursor_rect.bottomRight() + QPoint(0, 4))
        dlg = DateInsertDialog(self, anchor_pos=anchor)
        result = dlg.exec()
        if result == QDialog.Accepted:
            text = dlg.selected_date_text()
            if text:
                cursor = self.editor.textCursor()
                cursor.insertText(text)
                self.editor.setTextCursor(cursor)
                self.statusBar().showMessage(f"Inserted date: {text}", 3000)
        self._restore_vi_mode_after_dialog(vi_was_active)

    def _copy_current_page_link(self) -> None:
        """Copy the current page's link to clipboard (Ctrl+Shift+L)."""
        if not self.current_path:
            self.statusBar().showMessage("No page open to copy", 3000)
            return
        copied = self.editor.copy_current_page_link()
        if copied:
            self.statusBar().showMessage(f"Copied link: {copied}", 3000)
        else:
            colon_path = path_to_colon(self.current_path)
            if colon_path:
                rooted = ensure_root_colon_link(colon_path)
                self.statusBar().showMessage(f"Copied link: {rooted}", 3000)

    def _on_link_copied(self, link_text: str) -> None:
        """Show status when links are copied via editor context menu."""
        if link_text:
            self.statusBar().showMessage(f"Copied link: {link_text}", 3000)

    def _show_new_page_dialog(self) -> None:
        """Show dialog to create a new page with template selection (Ctrl+N)."""
        if not self.vault_root:
            self._alert("Select a vault before creating pages.")
            return
        if not self._ensure_writable("create new pages"):
            return
        
        vi_was_active = self._suspend_vi_mode_for_dialog()
        dlg = NewPageDialog(self)
        if dlg.exec() == QDialog.Accepted:
            page_name = dlg.get_page_name()
            if not page_name:
                self.statusBar().showMessage("Page name cannot be empty", 3000)
                return
            
            if "/" in page_name or ":" in page_name:
                self.statusBar().showMessage("Page name cannot contain '/' or ':'", 3000)
                return
            
            # Determine parent path based on current selection
            parent_path = self._get_current_parent_path()
            
            # Create the new page path
            target_path = self._join_paths(parent_path, page_name)
            
            try:
                # Create the page folder
                resp = self.http.post("/api/path/create", json={"path": target_path, "is_dir": True})
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 409:
                    self.statusBar().showMessage("Page already exists", 4000)
                else:
                    self._alert(f"Failed to create page: {exc}")
                return
            except httpx.HTTPError as exc:
                self._alert(f"Failed to create page: {exc}")
                return
            
            # Get the file path
            file_path = self._folder_to_file_path(target_path)
            if not file_path:
                return
            
            # Apply the selected template
            template_path = dlg.get_template_path()
            if template_path:
                self._apply_template_from_path(file_path, page_name, template_path)
            
            # Open the new page
            self._pending_selection = file_path
            self._populate_vault_tree()
            self._open_file(file_path, cursor_at_end=True)
        self._restore_vi_mode_after_dialog(vi_was_active)

    def _get_current_parent_path(self) -> str:
        """Get the parent path for creating new pages based on current selection."""
        # If we have a current file open, use its parent
        if self.current_path:
            rel_current = Path(self.current_path.lstrip("/"))
            parent_folder = rel_current.parent
            if parent_folder.parts:
                # Remove the filename to get the folder
                return f"/{parent_folder.as_posix()}"
        return "/"

    def _open_preferences(self) -> None:
        """Open the preferences dialog."""
        vi_was_active = self._suspend_vi_mode_for_dialog()
        dlg = PreferencesDialog(self)
        dlg.rebuildIndexRequested.connect(lambda: self._reindex_vault(show_progress=True))
        if dlg.exec() == QDialog.Accepted:
            # Reload vi-mode cursor setting and apply to editor
            self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
            self.editor.set_vi_strict_mode_enabled(config.load_vi_strict_mode_enabled())
            # Re-apply vi-mode state to refresh cursor
            if self._vi_mode_active:
                self.editor.set_vi_mode(True)
            self.right_panel.set_ai_enabled(config.load_enable_ai_chats())
            self.editor.set_ai_actions_enabled(config.load_enable_ai_chats())
            # Apply vault read-only preference immediately
            self._apply_vault_read_only_pref()
        self._restore_vi_mode_after_dialog(vi_was_active)

    def _open_task_from_panel(self, path: str, line: int) -> None:
        self._select_tree_path(path)
        self._open_file(path)
        # Focus first, then go to line so the selection isn't cleared
        self.editor.setFocus()
        self._restore_vi_if_suspended()
        self._goto_line(line, select_line=True)

    def _open_link_from_panel(self, path: str) -> None:
        if not path:
            return
        path = self._normalize_editor_path(path)
        # Special case: if the path matches the vault root name or is the vault root folder, open the main page
        if self.vault_root_name:
            # Accept /VaultRoot, VaultRoot, /VaultRoot/, or /VaultRoot/VaultRoot.txt as vault root
            normalized = path.strip().strip("/")
            if (
                normalized == self.vault_root_name
                or normalized == f"{self.vault_root_name}{PAGE_SUFFIX.strip()}"
                or normalized == f"{self.vault_root_name}{PAGE_SUFFIX}"
                or normalized == f"{self.vault_root_name}/{self.vault_root_name}{PAGE_SUFFIX}"
            ):
                main_page = f"/{self.vault_root_name}{PAGE_SUFFIX}"
                self._select_tree_path(main_page)
                self._open_file(main_page)
                self.right_panel.focus_link_tab(main_page)
                self._apply_navigation_focus("navigator")
                return
        self._select_tree_path(path)
        self._open_file(path)
        self.right_panel.focus_link_tab(path)
        self._apply_navigation_focus("navigator")

    def _open_calendar_page(self, path: str) -> None:
        """Open a page from the Calendar tab without changing tabs."""
        if not path:
            return
        norm = self._normalize_editor_path(path)
        self._select_tree_path(norm)
        self._open_file(norm)
        # Keep the Calendar tab active and return focus to its tree
        try:
            self.right_panel.tabs.setCurrentWidget(self.right_panel.calendar_panel)
            self.right_panel.calendar_panel.journal_tree.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _refresh_detached_link_panels(self, path: Optional[str]) -> None:
        """Keep detached Link Navigator windows in sync with the current page."""
        if not self._detached_link_panels:
            return
        if not path or not config.has_active_vault():
            for panel in list(self._detached_link_panels):
                panel.set_page(None)
            return
        norm = self._normalize_editor_path(path)
        for panel in list(self._detached_link_panels):
            try:
                panel.set_page(norm)
            except Exception:
                pass

    # --- Detached panel windows -------------------------------------------------

    def _register_detached_panel(self, window: QMainWindow) -> None:
        """Keep a reference to detached panels to prevent GC, and remove on close."""
        self._detached_panels.append(window)
        window.destroyed.connect(
            lambda: self._detached_panels.remove(window) if window in self._detached_panels else None
        )

    def _open_task_panel_window(self) -> None:
        if not config.has_active_vault():
            self._alert("Open a vault first.")
            return
        panel = TaskPanel()
        panel.set_vault_root(self.vault_root or "")
        try:
            panel.set_navigation_filter(self._nav_filter_path, refresh=False)
        except Exception:
            pass
        panel.refresh()
        panel.taskActivated.connect(self._open_task_from_panel)
        window = QMainWindow(self)
        window.setWindowTitle("Tasks")
        window.setCentralWidget(panel)
        window.resize(720, 640)
        self._apply_geometry_persistence(window, "task_panel_window")
        window.show()
        self._register_detached_panel(window)

    def _open_link_panel_window(self) -> None:
        if not config.has_active_vault():
            self._alert("Open a vault first.")
            return
        panel = LinkNavigatorPanel()
        current = self.current_path
        if current:
            panel.set_page(self._normalize_editor_path(current))
        panel.pageActivated.connect(self._open_link_from_panel)
        panel.openInWindowRequested.connect(self._open_page_editor_window)
        window = QMainWindow(self)
        window.setWindowTitle("Link Navigator")
        window.setCentralWidget(panel)
        window.resize(760, 680)
        self._apply_geometry_persistence(window, "link_navigator_window")
        window.show()
        self._register_detached_panel(window)
        self._detached_link_panels.append(panel)
        window.destroyed.connect(lambda: self._remove_detached_link_panel(panel))

    def _open_ai_chat_window(self) -> None:
        if not config.load_enable_ai_chats():
            self._alert("Enable AI Chat in settings to use this window.")
            return
        panel = AIChatPanel(font_size=self.right_panel.get_ai_font_size(), api_client=self.http)
        if self.vault_root:
            panel.set_vault_root(self.vault_root)
        if self.current_path:
            panel.set_current_page(self._normalize_editor_path(self.current_path))
        panel.chatNavigateRequested.connect(self._on_ai_chat_navigate)
        window = QMainWindow(self)
        window.setWindowTitle("AI Chat")
        window.setCentralWidget(panel)
        window.resize(820, 720)
        self._apply_geometry_persistence(window, "ai_chat_window")
        window.show()
        self._register_detached_panel(window)

    def _remove_detached_link_panel(self, panel: LinkNavigatorPanel) -> None:
        if panel in self._detached_link_panels:
            self._detached_link_panels.remove(panel)

    def _apply_geometry_persistence(self, window: QMainWindow, key: str) -> None:
        """Restore and persist window geometry for detached panels."""
        geom_b64 = config.load_dialog_geometry(key)
        if geom_b64:
            try:
                geometry = QByteArray.fromBase64(geom_b64.encode("ascii"))
                window.restoreGeometry(geometry)
            except Exception:
                pass

        class _GeometrySaver(QObject):
            def __init__(self, target: QMainWindow, name: str) -> None:
                super().__init__(target)
                self._target = target
                self._name = name
                self._timer = QTimer(self)
                self._timer.setSingleShot(True)
                self._timer.setInterval(200)
                self._timer.timeout.connect(self._save)
                target.installEventFilter(self)

            def eventFilter(self, obj, event):
                if obj is self._target and event.type() in (QEvent.Resize, QEvent.Move, QEvent.Close):
                    self._timer.start()
                return super().eventFilter(obj, event)

            def _save(self) -> None:
                try:
                    geom = (
                        self._target.saveGeometry().toBase64().data().decode("ascii")
                        if hasattr(self._target, "saveGeometry")
                        else None
                    )
                    if geom:
                        config.save_dialog_geometry(self._name, geom)
                except Exception:
                    pass

        saver = _GeometrySaver(window, key)
        window._geometry_saver = saver  # Keep reference

    def _open_page_editor_window(self, path: str) -> None:
        """Open a lightweight editor window for a single page (shared server)."""
        if not path or not self.vault_root:
            return
        rel_path = self._normalize_editor_path(path)
        try:
            window = PageEditorWindow(
                api_base=self.api_base,
                vault_root=self.vault_root,
                page_path=rel_path,
                read_only=self._read_only,
                open_in_main_callback=lambda target, **kw: self._open_link_in_context(target, **kw),
                parent=self,
            )
            window.show()
            self._page_windows.append(window)
            window.destroyed.connect(lambda: self._page_windows.remove(window) if window in self._page_windows else None)
        except Exception as exc:
            self._alert(f"Failed to open editor window: {exc}")

    def _toggle_left_panel(self) -> None:
        """Show/hide the navigation (tree) panel."""
        is_visible = self.main_splitter.sizes()[0] > 0
        if is_visible:
            self._saved_left_width = self.main_splitter.sizes()[0]
            self.main_splitter.setSizes([0, sum(self.main_splitter.sizes())])
        else:
            width = getattr(self, "_saved_left_width", 240)
            total = sum(self.main_splitter.sizes())
            self.main_splitter.setSizes([width, max(1, total - width)])

    def _toggle_right_panel(self) -> None:
        """Show/hide the right tabbed panel."""
        is_visible = self.editor_split.sizes()[1] > 0
        if is_visible:
            self._saved_right_width = self.editor_split.sizes()[1]
            self.editor_split.setSizes([sum(self.editor_split.sizes()), 0])
        else:
            width = getattr(self, "_saved_right_width", 360)
            total = sum(self.editor_split.sizes())
            self.editor_split.setSizes([max(1, total - width), width])

    def _ensure_right_panel_visible(self) -> None:
        """Ensure the right panel is visible (used before showing link/AI panes)."""
        sizes = self.editor_split.sizes()
        if len(sizes) >= 2 and sizes[1] == 0:
            width = getattr(self, "_saved_right_width", 360)
            total = sum(sizes)
            self.editor_split.setSizes([max(1, total - width), width])

    def _open_link_in_context(self, link: str, force: bool = False, refresh_only: bool = False) -> None:
        """Handle link activations from the editor (main or popup)."""
        if not link:
            return
        if "\x00" in link:
            link = link.split("\x00", 1)[0]
        if link.startswith(("http://", "https://")):
            try:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
            except Exception:
                return
            QDesktopServices.openUrl(QUrl(link))
            return
        # Absolute vault-relative path (starts with /): open directly without CamelCase heuristics
        if link.startswith("/"):
            target = self._normalize_editor_path(link)
            if refresh_only and self.current_path == target:
                self._open_file(target, add_to_history=False, force=True)
            elif not refresh_only:
                self._select_tree_path(target)
                self._open_file(target, force=force)
            return
        # Otherwise treat as page link
        self._open_camel_link(link, focus_target="editor", refresh_only=refresh_only, force=force)
    
    def _open_journal_date(self, year: int, month: int, day: int) -> None:
        """Open or create journal entry for the selected date."""
        if not self.vault_root:
            self._alert("Select a vault before creating journal entries.")
            return
        
        # Format paths: Journal/YYYY/MM/DD/DD.txt
        month_str = f"{month:02d}"
        day_str = f"{day:02d}"
        
        # Build the file path
        rel_path = f"/Journal/{year}/{month_str}/{day_str}/{day_str}{PAGE_SUFFIX}"
        
        # Check if file already exists
        from pathlib import Path
        abs_path = Path(self.vault_root) / rel_path.lstrip("/")
        file_exists = abs_path.exists()
        
        if file_exists:
            # File exists, open it normally
            self._pending_selection = rel_path
            self._populate_vault_tree()
            self._open_file(rel_path)
        else:
            # File doesn't exist yet - open virtual page
            self._open_virtual_journal_page(rel_path, year, month, day)
        
        self.editor.setFocus()
        self._apply_focus_borders()
    
    def _open_virtual_journal_page(self, rel_path: str, year: int, month: int, day: int) -> None:
        """Open a virtual (not yet saved) journal page."""
        # Generate template content but don't save to disk yet
        from datetime import date
        target_date = date(year, month, day)
        
        templates_root = Path(__file__).parent.parent.parent / "templates"
        day_tpl = templates_root / "JournalDay.txt"
        
        vars_map = {
            "{{YYYY}}": f"{year}",
            "{{Month}}": target_date.strftime("%B"),
            "{{DOW}}": target_date.strftime("%A"),
            "{{dd}}": f"{day:02d}",
        }
        
        content = ""
        if day_tpl.exists():
            try:
                raw = day_tpl.read_text(encoding="utf-8")
                content = raw
                for k, v in vars_map.items():
                    content = content.replace(k, v)
            except Exception:
                content = f"# {target_date.strftime('%A %d %B %Y')}\n\n"
        else:
            content = f"# {target_date.strftime('%A %d %B %Y')}\n\n"
        
        # Set up editor without saving to disk
        self.editor.set_context(self.vault_root, rel_path)
        self.current_path = rel_path
        self._suspend_autosave = True
        self.editor.set_markdown(content)
        self._suspend_autosave = False
        
        # Mark as virtual page and store original template content
        self.virtual_pages.add(rel_path)
        self.virtual_page_original_content[rel_path] = content
        
        # Move cursor to end for immediate typing
        cursor = self.editor.textCursor()
        display_length = len(self.editor.toPlainText())
        cursor.setPosition(display_length)
        self.editor.setTextCursor(cursor)
        
        # Update UI
        display_path = path_to_colon(rel_path) or rel_path
        if hasattr(self, "toc_widget"):
            root_base = ensure_root_colon_link(display_path) if display_path else ""
            self.toc_widget.set_base_path(root_base)
            self.editor.refresh_heading_outline()
        self.statusBar().showMessage(f"Editing (unsaved) {display_path}")
        self._update_window_title()
        
        # Update calendar to show this date
        self._update_calendar_for_journal_page(rel_path)
        
        # Refresh tree to show italicized entry
        self._populate_vault_tree()
        
        # Update attachments panel (virtual pages may still have folders)
        if rel_path:
            full_path = Path(self.vault_root) / rel_path.lstrip("/")
            has_chat = self.right_panel.set_current_page(full_path, rel_path)
            self.editor.set_ai_chat_available(has_chat)
        else:
            self.right_panel.set_current_page(None, None)
            self.editor.set_ai_chat_available(False)
    
    def _apply_journal_templates_for_date(self, day_file_path: str, year: int, month: int, day: int) -> None:
        """Apply journal templates for a specific date."""
        if not self.vault_root:
            return
        
        from datetime import date
        target_date = date(year, month, day)
        year_str = f"{year}"
        month_num = f"{month:02d}"
        month_name = target_date.strftime("%B")
        day_num = f"{day:02d}"
        dow_name = target_date.strftime("%A")
        
        vault_root = Path(self.vault_root)
        journal_root = vault_root / "Journal"
        year_dir = journal_root / year_str
        month_dir = year_dir / month_num
        day_dir = month_dir / day_num
        
        year_page = year_dir / f"{year_dir.name}{PAGE_SUFFIX}"
        month_page = month_dir / f"{month_dir.name}{PAGE_SUFFIX}"
        day_page = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"
        
        templates_root = Path(__file__).parent.parent.parent / "templates"
        year_tpl = templates_root / "JournalYear.txt"
        month_tpl = templates_root / "JournalMonth.txt"
        day_tpl = templates_root / "JournalDay.txt"
        
        vars_map = {
            "{{YYYY}}": year_str,
            "{{Month}}": month_name,
            "{{DOW}}": dow_name,
            "{{dd}}": day_num,
        }
        
        def render(template_path: Path) -> str:
            try:
                raw = template_path.read_text(encoding="utf-8")
            except Exception:
                return ""
            out = raw
            for k, v in vars_map.items():
                out = out.replace(k, v)
            return out
        
        year_dir.mkdir(parents=True, exist_ok=True)
        month_dir.mkdir(parents=True, exist_ok=True)
        day_dir.mkdir(parents=True, exist_ok=True)
        
        def needs_write(path: Path) -> bool:
            if not path.exists():
                return True
            try:
                size = path.stat().st_size
            except OSError:
                return False
            return size < 20
        
        if needs_write(year_page) and year_tpl.exists():
            content = render(year_tpl)
            if content:
                year_page.write_text(content, encoding="utf-8")
        
        if needs_write(month_page) and month_tpl.exists():
            content = render(month_tpl)
            if content:
                month_page.write_text(content, encoding="utf-8")
        
        if needs_write(day_page) and day_tpl.exists():
            content = render(day_tpl)
            if content:
                day_page.write_text(content, encoding="utf-8")
    
    def _cleanup_virtual_page_if_unchanged(self, path: str) -> None:
        """Remove virtual page tracking if it was never edited."""
        if path not in self.virtual_pages:
            return
        
        current_content = self.editor.to_markdown()
        original_content = self.virtual_page_original_content.get(path)
        
        # If content hasn't changed from template, clean up virtual tracking
        if original_content is not None and current_content == original_content:
            self.virtual_pages.discard(path)
            self.virtual_page_original_content.pop(path, None)
            self._debug(f"Cleaned up unchanged virtual page: {path}")
    
    def _extract_journal_date(self, path: str) -> Optional[tuple[int, int, int]]:
        """Extract year, month, day from a journal path like /Journal/2025/11/16/16.txt.
        
        Returns tuple of (year, month, day) or None if not a journal path.
        """
        if not path or not path.startswith("/Journal/"):
            return None
        
        try:
            # Split path: /Journal/YYYY/MM/DD/DD.txt
            parts = path.split("/")
            if len(parts) >= 5:  # ['', 'Journal', 'YYYY', 'MM', 'DD', ...]
                year = int(parts[2])
                month = int(parts[3])
                day = int(parts[4])
                return (year, month, day)
        except (ValueError, IndexError):
            pass
        
        return None
    
    def _update_calendar_for_journal_page(self, path: str) -> None:
        """Update calendar selection if opening a journal page."""
        date_tuple = self._extract_journal_date(path)
        if date_tuple:
            year, month, day = date_tuple
            self.right_panel.set_calendar_date(year, month, day)

    def _on_link_hovered(self, link: str) -> None:
        """Update status bar when hovering over a link."""
        if link:
            self.statusBar().showMessage(f"Link: {link}")
        else:
            # Restore default status message
            if self.current_path:
                display_path = path_to_colon(self.current_path) or self.current_path
                self.statusBar().showMessage(f"Editing {display_path}")
            else:
                self.statusBar().showMessage("")
    
    def _normalize_editor_path(self, path: str) -> str:
        """Normalize incoming page refs (folder, colon, bare) to file path with leading slash."""
        if not path:
            return path
        cleaned = path.strip()
        if cleaned.startswith(":"):
            cleaned = colon_to_path(cleaned, self.vault_root_name) or cleaned
        if not cleaned.startswith("/"):
            cleaned = "/" + cleaned.lstrip("/")
        rel = Path(cleaned.lstrip("/"))
        if rel.suffix != PAGE_SUFFIX:
            # Treat as folder; map to its page file
            file_path = self._folder_to_file_path(cleaned)
            if file_path:
                cleaned = file_path
        return cleaned

    def _open_camel_link(self, name: str, focus_target: str | None = None, refresh_only: bool = False, force: bool = False) -> None:
        """Open a link - handles both CamelCase (relative), colon notation (absolute), and HTTP URLs."""
        # Handle HTTP/HTTPS links
        if name.startswith("http://") or name.startswith("https://"):
            try:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(name))
                return
            except Exception as e:
                self._alert(f"Failed to open URL: {e}")
                return
        
        if not self.current_path:
            self._alert("Open a page before following links.")
            return
        
        # Save current page before following link to ensure it's indexed
        self._save_current_file(auto=True)

        # Attachment file link: detect filename with extension (non .txt) and open via OS
        if "." in name and ":" not in name and not name.endswith(".txt"):
            # Resolve relative to current page folder
            rel_current = Path(self.current_path.lstrip("/"))
            page_folder = rel_current.parent
            folder_path = Path(self.vault_root) / page_folder if self.vault_root else None
            if folder_path:
                # Strip optional leading ./
                clean_name = name[2:] if name.startswith("./") else name
                candidate = (folder_path / clean_name).resolve()
                if candidate.exists() and candidate.is_file():
                    try:
                        from PySide6.QtGui import QDesktopServices
                        from PySide6.QtCore import QUrl
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))
                        return
                    except Exception:
                        pass  # fall through to normal handling if OS open fails

        target_name, anchor = self._split_link_anchor(name)
        anchor_slug = self._anchor_slug(anchor)
        
        # Check if this is a colon notation link (PageA:PageB:PageC or :VaultRoot)
        if ":" in target_name:
            # Special case: :VaultRoot or :<vault_root_name> means open the vault's main page
            vault_root_colon = f":{self.vault_root_name}"
            if target_name.strip() in (":VaultRoot", vault_root_colon):
                # Open the vault's main page (fake root concept)
                main_page = f"/{self.vault_root_name}{PAGE_SUFFIX}"
                if refresh_only and self.current_path == main_page:
                    self._open_file(main_page, add_to_history=False, force=True)
                else:
                    self._open_file(main_page, force=force)
                    self._scroll_to_anchor_slug(anchor_slug)
                    self._apply_navigation_focus(focus_target)
                return
            # Colon notation is absolute - convert directly to path
            # Prevent duplicate vault root in path (e.g., VaultRoot/VaultRoot.txt)
            target_file = colon_to_path(target_name, self.vault_root_name)
            # If the resolved file is the vault root's main page, force it to /VaultRoot.txt
            vault_main_page = f"/{self.vault_root_name}{PAGE_SUFFIX}"
            if target_file.replace("\\", "/").strip("/") in (self.vault_root_name + PAGE_SUFFIX, vault_main_page.strip("/")):
                target_file = vault_main_page
            if not target_file:
                self._alert(f"Invalid link format: {name}")
                return
            target_file = self._resolve_case_insensitive_rel_path(target_file)
            folder_path = self._file_path_to_folder(target_file)
            # Check if file already exists before creating
            file_existed = self.vault_root and Path(self.vault_root, target_file.lstrip("/")).exists()
            if file_existed:
                is_new_page = False
            else:
                if self._read_only:
                    self.statusBar().showMessage("Cannot create new pages while vault is read-only.", 5000)
                    return
                if not self._ensure_page_folder(folder_path, allow_existing=True):
                    return
                is_new_page = True
                page_name = target_name.split(":")[-1]  # Get last part for page name
                self._apply_new_page_template(target_file, page_name)
            self._pending_selection = target_file
            if refresh_only and self.current_path == target_file:
                self._open_file(target_file, add_to_history=False, force=True)
            else:
                self._populate_vault_tree()
                self._open_file(target_file, cursor_at_end=is_new_page, force=force)
                self._scroll_to_anchor_slug(anchor_slug)
                self._apply_navigation_focus(focus_target)
        else:
            # CamelCase link is relative to current page
            # Special case: if the link target matches the vault root name, open /VaultRoot.txt
            if target_name == self.vault_root_name:
                target_file = f"/{self.vault_root_name}{PAGE_SUFFIX}"
                self._open_file(target_file)
                self._scroll_to_anchor_slug(anchor_slug)
                self._apply_navigation_focus(focus_target)
                return
            rel_current = Path(self.current_path.lstrip("/"))
            parent_folder = rel_current.parent
            # Always create a subfolder named after the link, and place the file inside it
            if parent_folder.parts:
                file_path = f"/{parent_folder.as_posix()}/{target_name}/{target_name}{PAGE_SUFFIX}"
            else:
                file_path = f"/{target_name}/{target_name}{PAGE_SUFFIX}"
            target_file = self._resolve_case_insensitive_rel_path(file_path)
            folder_path = self._file_path_to_folder(target_file)
            # Check if file already exists before creating
            file_existed = self.vault_root and Path(self.vault_root, target_file.lstrip("/")).exists()
            if file_existed:
                is_new_page = False
            else:
                if self._read_only:
                    self.statusBar().showMessage("Cannot create new pages while vault is read-only.", 5000)
                    return
                if not self._ensure_page_folder(folder_path, allow_existing=True):
                    return
                is_new_page = True
                self._apply_new_page_template(target_file, target_name)
            self._pending_selection = target_file
            if refresh_only and self.current_path == target_file:
                self._open_file(target_file, add_to_history=False, force=True)
            else:
                self._populate_vault_tree()
                self._open_file(target_file, cursor_at_end=is_new_page, force=force)
                self._scroll_to_anchor_slug(anchor_slug)
                self._apply_navigation_focus(focus_target)

    def _adjust_font_size(self, delta: int) -> None:
        new_size = max(6, min(24, self.font_size + delta))
        fw = self.focusWidget()
        ai_focus = False
        if fw and self.right_panel.ai_chat_panel:
            if fw is self.right_panel.ai_chat_panel or self.right_panel.ai_chat_panel.isAncestorOf(fw):
                ai_focus = True
        if ai_focus:
            self.right_panel.set_font_size(new_size)
            config.save_ai_chat_font_size(new_size)
        else:
            if new_size == self.font_size:
                return
            self.font_size = new_size
            self.editor.set_font_point_size(self.font_size)
            if config.has_active_vault():
                config.save_font_size(self.font_size)

    def _apply_navigation_focus(self, focus_target: str | None) -> None:
        """Set focus after navigation based on source (editor vs link navigator)."""
        if focus_target == "navigator":
            self.right_panel.focus_link_tab(self.current_path)
            try:
                self.right_panel.link_panel.graph_view.setFocus()
            except Exception:
                pass
        elif focus_target == "editor":
            self.editor.setFocus()

    def _focus_editor_from_tree(self) -> None:
        index = self.tree_view.currentIndex()
        target = index.data(OPEN_ROLE) or index.data(PATH_ROLE) if index.isValid() else None
        if target == FILTER_BANNER:
            self._clear_nav_filter()
            return
        if target and target != self.current_path:
            self._skip_next_selection_open = True
            self._open_file(target)
        self._focus_editor()

    def _on_tree_row_clicked(self, index: QModelIndex) -> None:
        """Open and focus editor when a tree row is clicked."""
        target = index.data(OPEN_ROLE) or index.data(PATH_ROLE)
        if target == FILTER_BANNER:
            self._clear_nav_filter()
            return
        if target:
            if target != self.current_path:
                self._skip_next_selection_open = True
                self._open_file(target)
            self._focus_editor()

    def _focus_editor(self) -> None:
        self.editor.setFocus()

    def _focus_tasks_search(self) -> None:
        """Focus the Tasks tab search bar."""
        # Ensure right panel is visible if hidden
        sizes = self.editor_split.sizes()
        if len(sizes) >= 2 and sizes[1] == 0:
            width = getattr(self, "_saved_right_width", 360)
            total = sum(sizes)
            self.editor_split.setSizes([max(1, total - width), width])
        try:
            # Switch to Tasks tab
            self.right_panel.tabs.setCurrentIndex(0)
            # Suspend vi-mode while interacting with tasks
            self._suspend_vi_for_tasks()
            # Explicitly focus the search box
            if hasattr(self.right_panel.task_panel, "focus_search"):
                self.right_panel.task_panel.focus_search()
            else:
                self.right_panel.task_panel.search.setFocus(Qt.ShortcutFocusReason)
        except Exception:
            pass

    def _suspend_vi_for_tasks(self) -> None:
        """Turn off vi-mode while interacting with Tasks, remembering prior state."""
        if getattr(self, "_vi_mode_active", False):
            self._vi_suspended_for_tasks = True
            self._vi_mode_active = False
            self._apply_vi_mode_statusbar_style()

    def _restore_vi_if_suspended(self) -> None:
        """Restore vi-mode if it was suspended for task interactions."""
        if self._vi_suspended_for_tasks:
            self._vi_suspended_for_tasks = False
            self._vi_mode_active = True
            self._apply_vi_mode_statusbar_style()

    def _mark_tree_arrow_nav(self) -> None:
        """Flag that tree navigation via arrow keys should keep focus on the tree."""
        self._tree_arrow_focus_pending = True

    # --- Focus toggle & visual indication ---------------------------
    def _toggle_focus_between_tree_and_editor(self) -> None:
        """Toggle focus between tree, editor, and right panel (Ctrl+Shift+Space) using MRU order."""
        current = self._focus_target_for_widget(self.focusWidget())
        if current in self._focus_recent:
            # Rotate MRU list so current moves to end, pick next
            self._focus_recent = [t for t in self._focus_recent if t != current] + [current]
        target = self._focus_recent[0] if self._focus_recent else "editor"
        self._set_focus_target(target)

    def _set_focus_target(self, target: str) -> None:
        """Move focus to target and update MRU list."""
        if target == "editor":
            self.editor.setFocus()
        elif target == "tree":
            self.tree_view.setFocus()
        elif target == "right":
            current_tab = self.right_panel.tabs.currentWidget()
            if current_tab:
                current_tab.setFocus()
            else:
                self.right_panel.setFocus()
        if target in self._focus_recent:
            self._focus_recent = [target] + [t for t in self._focus_recent if t != target]
        else:
            self._focus_recent.insert(0, target)
        self._apply_focus_borders()

    def _focus_target_for_widget(self, widget: Optional[QWidget]) -> Optional[str]:
        if not widget:
            return None
        if widget is self.editor or (self.editor and self.editor.isAncestorOf(widget)):
            return "editor"
        if widget is self.tree_view or self.tree_view.isAncestorOf(widget):
            return "tree"
        if widget is self.right_panel or self.right_panel.isAncestorOf(widget):
            return "right"
        return None

    def _on_focus_changed(self, widget: Optional[QWidget]) -> None:
        target = self._focus_target_for_widget(widget)
        if target:
            if target in self._focus_recent:
                self._focus_recent = [target] + [t for t in self._focus_recent if t != target]
            else:
                self._focus_recent.insert(0, target)
        self._apply_focus_borders()

    def _apply_focus_borders(self) -> None:
        """Apply a subtle border around the widget that currently has focus."""
        focused = self.focusWidget()
        editor_has = focused is self.editor or (self.editor and self.editor.isAncestorOf(focused))
        tree_has = focused is self.tree_view or self.tree_view.isAncestorOf(focused)
        right_has = focused is self.right_panel or self.right_panel.isAncestorOf(focused)
        # Styles: subtle border with accent color; remove when unfocused. Reset any filter tint to default background.
        editor_style = "QTextEdit { border: 1px solid #4A90E2; border-radius:3px; }" if editor_has else "QTextEdit { border: 1px solid transparent; }"
        tree_style = (
            "QTreeView { border: 1px solid #4A90E2; border-radius:3px; background: palette(base); }"
            if tree_has
            else "QTreeView { border: 1px solid transparent; background: palette(base); }"
        )
        right_style = "QTabWidget::pane { border: 1px solid #4A90E2; border-radius:3px; }" if right_has else ""
        # Preserve existing styles by appending (simple approach)
        self.editor.setStyleSheet(editor_style)
        self.tree_view.setStyleSheet(tree_style)
        if right_style:
            self.right_panel.tabs.setStyleSheet(right_style)
        else:
            self.right_panel.tabs.setStyleSheet("")

    def _goto_line(self, line: int, select_line: bool = False) -> None:
        # Convert line number (1-indexed) to block number (0-indexed)
        block_num = max(0, line - 1)
        doc = self.editor.document()
        block = doc.findBlockByNumber(block_num)
        
        if block.isValid():
            cursor = QTextCursor(block)
            # Move to start of block content (skip whitespace if selecting line)
            if select_line:
                cursor.select(QTextCursor.LineUnderCursor)
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()

    def _ensure_page_folder(self, folder_path: str, allow_existing: bool = False) -> bool:
        if not self._ensure_writable("create folders/pages"):
            return False
        payload = {"path": folder_path, "is_dir": True}
        try:
            resp = self.http.post("/api/path/create", json=payload)
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if allow_existing and exc.response is not None and exc.response.status_code == 409:
                return True
            self._alert(f"Failed to create page {folder_path}: {exc}")
            return False
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create page {folder_path}: {exc}")
            return False

    def _folder_to_file_path(self, folder_path: str) -> Optional[str]:
        if not self.vault_root_name:
            return None
        cleaned = (folder_path or "/").strip()
        if cleaned in ("", "/"):
            return f"/{self.vault_root_name}{PAGE_SUFFIX}"
        rel = Path(cleaned.lstrip("/"))
        name = rel.name or self.vault_root_name
        rel_file = rel / f"{name}{PAGE_SUFFIX}"
        return f"/{rel_file.as_posix()}"

    def _resolve_case_insensitive_rel_path(self, rel_path: str) -> str:
        """Resolve a vault-relative path by ignoring case in existing filesystem entries."""
        if not self.vault_root:
            return rel_path
        cleaned = (rel_path or "").strip().lstrip("/")
        if not cleaned:
            return rel_path
        current = Path(self.vault_root)
        resolved: list[str] = []
        parts = cleaned.split("/")
        for part in parts:
            try:
                match = next((child.name for child in current.iterdir() if child.name.lower() == part.lower()), None)
            except OSError:
                match = None
            name = match or part
            resolved.append(name)
            current = current / name
        return "/" + "/".join(resolved)

    def _file_path_to_folder(self, file_path: str) -> str:
        """Convert file path like /PageA/PageB/PageC/PageC.txt to folder path /PageA/PageB/PageC."""
        if not file_path or file_path == "/":
            return "/"
        # Remove the .txt file at the end
        path_obj = Path(file_path.lstrip("/"))
        if path_obj.suffix == PAGE_SUFFIX:  # Suffix includes the dot
            return f"/{path_obj.parent.as_posix()}"
        return file_path

    # --- Tree context menu -------------------------------------------
    def _open_context_menu(self, pos: QPoint) -> None:
        if not self.vault_root:
            return
        index = self.tree_view.indexAt(pos)
        global_pos = self.tree_view.viewport().mapToGlobal(pos)
        menu = QMenu(self)
        if index.isValid():
            path = index.data(PATH_ROLE) or "/"
            menu.addAction(
                "New Page",
                lambda checked=False, p=path, idx=index: self._start_inline_creation(p, global_pos, idx),
            )
            filter_action = menu.addAction("Filter to this subtree")
            filter_action.triggered.connect(lambda checked=False, p=path: self._set_nav_filter(p))
            if path:
                open_window_action = menu.addAction("Open in Editor Window")
                open_window_action.triggered.connect(lambda checked=False, p=path: self._open_page_editor_window(p))
            open_path = index.data(OPEN_ROLE)
            if path != "/":
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(
                    lambda checked=False, p=path, op=open_path: self._delete_path(p, op, global_pos)
                )
            # View Page Source (open underlying txt in external editor)
            file_path = open_path or self._folder_to_file_path(path)
            if file_path:
                view_src = menu.addAction("Edit Page Source")
                view_src.triggered.connect(lambda checked=False, fp=file_path: self._view_page_source(fp))
                
                # Open File Location
                open_loc = menu.addAction("Open File Location")
                open_loc.triggered.connect(lambda checked=False, fp=file_path: self._open_tree_file_location(fp))
                
                backlinks_action = menu.addAction("Backlinks…")
                backlinks_action.triggered.connect(
                    lambda checked=False, fp=file_path: self._show_link_navigator_for_path(fp)
                )
                ai_chat_action = menu.addAction("AI Chat…")
                ai_chat_action.triggered.connect(lambda checked=False, fp=file_path: self._open_ai_chat_for_path(fp, create=True))
        else:
            menu.addAction("New Page", lambda checked=False: self._start_inline_creation("/", global_pos, None))
        if menu.actions():
            menu.exec(global_pos)

    def _view_page_source(self, file_path: str) -> None:
        """Open the given page's txt file in the OS editor, show modal, and reload on OK."""
        if not self.vault_root:
            return
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
        except Exception:
            return
        abs_path = str((Path(self.vault_root) / file_path.lstrip("/")).resolve())
        # Launch in default editor
        QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path))
        # Block with modal until user confirms they're done
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Edit Page Source")
        dlg.setText("File being edited outside of ZimX.\nPress OK when finished.")
        dlg.setIcon(QMessageBox.Information)
        dlg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        dlg.setDefaultButton(QMessageBox.Ok)
        result = dlg.exec()
        if result == QMessageBox.Ok:
            # Reload and render page (force reload even if already current)
            self._select_tree_path(file_path)
            self._open_file(file_path, force=True)
    
    def _open_tree_file_location(self, file_path: str) -> None:
        """Open the folder containing the given page file."""
        if not self.vault_root:
            return
        
        abs_path = (Path(self.vault_root) / file_path.lstrip("/")).resolve()
        folder_path = abs_path.parent
        opened = self._open_in_file_manager(folder_path)
        if not opened:
            self._alert(f"Could not open folder: {folder_path}")

    def _show_link_navigator_for_path(self, file_path: Optional[str]) -> None:
        """Open the Link Navigator tab for the given page."""
        if not file_path:
            return
        self._ensure_right_panel_visible()
        if file_path != self.current_path:
            try:
                self._open_file(file_path)
            except Exception:
                return
        self.right_panel.focus_link_tab(file_path)

    def _open_ai_chat_for_path(self, file_path: Optional[str], create: bool = False) -> None:
        """Open the AI Chat tab and sync to the given page."""
        if not file_path or not self.right_panel.ai_chat_panel:
            return
        self._ensure_right_panel_visible()
        self.right_panel.focus_ai_chat(file_path, create=create)

    def _focus_ai_chat_for_page(self, path: str) -> None:
        """Ensure AI tab shows the requested page and give the input focus."""
        target_path = path or self.current_path
        if not target_path or not self.right_panel.ai_chat_panel:
            return
        self._ensure_right_panel_visible()
        self.right_panel.focus_ai_chat(target_path, create=True)
        self.right_panel.focus_ai_chat_input()

    def _handle_ai_action(self, action: str, prompt: str, text: str) -> None:
        """Send selected text to AI chat with the chosen action."""
        if not config.load_enable_ai_chats() or not self.right_panel.ai_chat_panel:
            QMessageBox.information(self, "AI Chat", "Enable AI Chats in Preferences to use AI actions.")
            return
        target_path = self.current_path
        self._ensure_right_panel_visible()
        self.right_panel.focus_ai_chat(target_path, create=True)
        self.right_panel.send_ai_action(action, prompt, text)

    def _send_selection_to_ai_chat(self, text: str) -> None:
        if not text.strip():
            return
        if not self.right_panel.send_text_to_chat(text):
            self.statusBar().showMessage("Enable AI chats to send text from the editor.", 4000)

    def _on_ai_chat_navigate(self, chat_folder: Optional[str]) -> None:
        """Handle 'Go To Page' from AI chat by focusing the matching page in the editor."""
        if not chat_folder:
            return
        file_path = self._folder_to_file_path(chat_folder)
        if not file_path:
            return
        if self.current_path == file_path:
            self.editor.setFocus()
            self._apply_focus_borders()
            return
        # Keep AI Chat tab visible while navigating
        self.right_panel.focus_ai_chat(chat_folder)
        try:
            self._select_tree_path(file_path)
            self._open_file(file_path, force=True)
            self.editor.setFocus()
            self._apply_focus_borders()
        except Exception:
            return

    def _open_in_file_manager(self, path: Path) -> bool:
        """Try to open a file or folder in the OS file manager."""
        try:
            if not path.exists():
                return False
            url = QUrl.fromLocalFile(str(path))
            if QDesktopServices.openUrl(url):
                return True
            # Fallback per-OS
            if sys.platform.startswith("darwin"):
                result = subprocess.run(["open", str(path)], check=False)
                return result.returncode == 0
            if sys.platform.startswith("win"):
                result = subprocess.run(["explorer", str(path)], check=False)
                return result.returncode == 0
            # Assume Linux/Unix
            result = subprocess.run(["xdg-open", str(path)], check=False)
            return result.returncode == 0
        except Exception as exc:
            self._alert(f"Failed to open file manager: {exc}")
            return False

    def _reload_current_page(self) -> None:
        """Reload the current page from disk without altering history."""
        if not self.current_path:
            self.statusBar().showMessage("No page to reload", 2000)
            return
        self._remember_history_cursor()
        self._open_file(self.current_path, add_to_history=False, force=True, restore_history_cursor=True)
        self.statusBar().showMessage("Reloaded current page", 2000)

    def _start_inline_creation(
        self,
        parent_path: str,
        global_pos: QPoint,
        anchor_index: Optional[QModelIndex] = None,
    ) -> None:
        self._cancel_inline_editor()
        editor = InlineNameEdit(self.tree_view.viewport())
        editor.setPlaceholderText("Page name")
        editor.submitted.connect(lambda name: self._handle_inline_submit(parent_path, name))
        editor.cancelled.connect(self._inline_editor_cancelled)
        self._inline_editor = editor
        if anchor_index and anchor_index.isValid():
            rect = self.tree_view.visualRect(anchor_index)
            viewport_pos = rect.bottomLeft()
            viewport_pos.setY(viewport_pos.y() + 4)
        else:
            viewport_pos = self.tree_view.viewport().mapFromGlobal(global_pos)
        editor.move(viewport_pos)
        width = max(200, self.tree_view.viewport().width() - 40)
        editor.resize(width, editor.sizeHint().height())
        editor.show()
        editor.setFocus()

    def _handle_inline_submit(self, parent_path: str, name: str) -> None:
        name = name.strip()
        if not name:
            self._cancel_inline_editor()
            return
        if not self._ensure_writable("create new pages"):
            self._cancel_inline_editor()
            return
        if "/" in name:
            self.statusBar().showMessage("Names cannot contain '/'", 4000)
            return
        target_path = self._join_paths(parent_path, name)
        try:
            resp = self.http.post("/api/path/create", json={"path": target_path, "is_dir": True})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                self.statusBar().showMessage("Name already exists", 4000)
            else:
                self._alert(f"Failed to create entry: {exc}")
            return
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create entry: {exc}")
            return
        self._cancel_inline_editor()
        file_path = self._folder_to_file_path(target_path)
        if file_path:
            # Apply NewPage.txt template to the newly created page
            self._apply_new_page_template(file_path, name)
            self._pending_selection = file_path
        self._populate_vault_tree()

    def _apply_new_page_template(self, file_path: str, page_name: str) -> None:
        """Apply the NewPage.txt template to a newly created page."""
        template_path = Path(__file__).parent.parent.parent / "templates" / "NewPage.txt"
        self._apply_template_from_path(file_path, page_name, str(template_path))

    def _apply_template_from_path(self, file_path: str, page_name: str, template_path: str) -> None:
        """Apply a specific template file to a newly created page."""
        if not self.vault_root:
            return
        if not self._ensure_writable("apply templates or write pages"):
            return
        
        # Load template
        template_file = Path(template_path)
        if not template_file.exists():
            return
        
        try:
            template_content = template_file.read_text(encoding="utf-8")
        except Exception:
            return
        
        # Process template variables
        content = self._process_template_variables(template_content, page_name)
        
        # Write to the new page file
        abs_path = Path(self.vault_root) / file_path.lstrip("/")
        try:
            abs_path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _process_template_variables(self, template: str, page_name: str) -> str:
        """Replace template variables with their values."""
        from datetime import datetime
        
        # Get current date in format: Tuesday 29 April 2025
        now = datetime.now()
        day_date_year = now.strftime("%A %d %B %Y")
        
        # Replace variables
        result = template.replace("{{PageName}}", page_name)
        result = result.replace("{{DayDateYear}}", day_date_year)
        
        return result

    def _inline_editor_cancelled(self) -> None:
        self._inline_editor = None

    def _cancel_inline_editor(self) -> None:
        if self._inline_editor:
            editor = self._inline_editor
            self._inline_editor = None
            try:
                editor.cancelled.disconnect(self._inline_editor_cancelled)
            except Exception:
                pass
            editor.deleteLater()

    def _delete_path(self, folder_path: str, open_path: Optional[str], global_pos: QPoint) -> None:
        try:
            if folder_path == "/":
                self.statusBar().showMessage("Cannot delete the root page.", 4000)
                return
            if not self._ensure_writable("delete pages or folders"):
                return
            confirm = QMessageBox(self)
            confirm.setIcon(QMessageBox.Warning)
            confirm.setWindowTitle("Delete")
            warning = ""
            store = None
            target_folder = folder_path
            if folder_path.lower().endswith(str(PAGE_SUFFIX)):
                target_folder = self._file_path_to_folder(folder_path)
            try:
                if self.right_panel.ai_chat_panel:
                    store = self.right_panel.ai_chat_panel.store  # type: ignore[attr-defined]
                if store and store.has_chats_under(target_folder):
                    warning = '<br><span style="color:red; font-weight:bold;">WARNING: this will delete any stored AI chats.</span>'
            except Exception:
                warning = ""
            confirm.setTextFormat(Qt.TextFormat.RichText)
            confirm.setText(f"Delete {folder_path}? This cannot be undone.{warning}")
            confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            confirm.setDefaultButton(QMessageBox.No)
            confirm.adjustSize()
            confirm.move(global_pos - QPoint(confirm.width() // 2, confirm.height() // 2))
            result = confirm.exec()
            if result != QMessageBox.Yes:
                return
            try:
                resp = self.http.post("/api/path/delete", json={"path": folder_path})
                resp.raise_for_status()
            except Exception as exc:
                self._alert(f"Failed to delete {folder_path}: {exc}")
                return
            if store:
                try:
                    store.delete_chats_under(target_folder)  # type: ignore[attr-defined]
                except Exception:
                    pass
            
            # Clean up database: delete all pages under this folder
            # folder_path is like "/PageA/PageB" (folder) not "/PageA/PageB/PageB.txt" (file)
            try:
                config.delete_folder_index(folder_path)
            except Exception as exc:
                self._alert(f"Deleted files but failed to update index for {folder_path}: {exc}")
            
            # Clear editor if we just deleted the currently open page
            if self.current_path and open_path and self.current_path == open_path:
                self.current_path = None
                self.editor.set_markdown("")
            
            self._populate_vault_tree()
            self.right_panel.refresh_tasks()
            self.right_panel.refresh_calendar()
            self.right_panel.refresh_links(self.current_path)
        except Exception as exc:
            # Catch-all to keep the UI alive; surface error to the user.
            try:
                self._alert(f"Unexpected error while deleting {folder_path}: {exc}")
            except Exception:
                pass

    def _parent_path(self, index: QModelIndex) -> str:
        parent = index.parent()
        if parent.isValid():
            return parent.data(PATH_ROLE) or "/"
        return "/"

    def _join_paths(self, parent_path: str, name: str) -> str:
        parent = (parent_path or "/").rstrip("/")
        if parent in ("", "/"):
            return f"/{name}"
        return f"{parent}/{name}"

    def _select_tree_path(self, target_path: str) -> None:
        match = self._find_item(self.tree_model.invisibleRootItem(), target_path)
        if match:
            index = match.index()
            self.tree_view.setCurrentIndex(index)
            self.tree_view.scrollTo(index)

    def _find_item(self, parent: QStandardItem, target: str) -> Optional[QStandardItem]:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            child_path = child.data(PATH_ROLE)
            child_open = child.data(OPEN_ROLE)
            if target in (child_path, child_open):
                return child
            found = self._find_item(child, target)
            if found:
                return found
        return None

    def _gather_indexes(self, leaves_only: bool) -> list[QModelIndex]:
        model = self.tree_model
        flat: list[QModelIndex] = []

        def recurse(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                is_dir = bool(idx.data(TYPE_ROLE))
                if not leaves_only or not is_dir:
                    flat.append(idx)
                recurse(idx)

        recurse(QModelIndex())
        return [idx for idx in flat if idx.isValid()]

    def _navigate_history_back(self) -> None:
        """Navigate to previous page in history (Alt+Left)."""
        import traceback
        print(f"[DEBUG] _navigate_history_back called from:")
        for line in traceback.format_stack()[-4:-1]:
            print(line.strip())
        if not self.page_history or self.history_index <= 0:
            return
        # Remember vi mode state before navigation - check MAIN WINDOW's state, not editor's
        print(f"[DEBUG] self._vi_mode_active = {getattr(self, '_vi_mode_active', 'ATTRIBUTE_MISSING')}")
        vi_mode_was_active = self._vi_mode_active
        self._remember_history_cursor()
        self.history_index -= 1
        target_path = self.page_history[self.history_index]
        self._suspend_selection_open = True
        try:
            self._select_tree_path(target_path)
        finally:
            self._suspend_selection_open = False
        self._open_file(target_path, add_to_history=False, restore_history_cursor=True)
        # Restore vi mode flag if set (no delayed timers needed anymore)
        self._restore_vi_mode_after_nav = False
        QTimer.singleShot(0, self.editor.setFocus)

    def _navigate_history_forward(self) -> None:
        """Navigate to next page in history (Alt+Right)."""
        if not self.page_history or self.history_index >= len(self.page_history) - 1:
            return
        # Remember vi mode state before navigation - check MAIN WINDOW's state, not editor's
        vi_mode_was_active = self._vi_mode_active
        self._remember_history_cursor()
        self.history_index += 1
        target_path = self.page_history[self.history_index]
        self._suspend_selection_open = True
        try:
            self._select_tree_path(target_path)
        finally:
            self._suspend_selection_open = False
        self._open_file(target_path, add_to_history=False, restore_history_cursor=True)
        # Clear vi mode restore flag; no timer needed now that vi-mode stays stable
        self._restore_vi_mode_after_nav = False
        QTimer.singleShot(0, self.editor.setFocus)

    def _history_can_go_back(self) -> bool:
        """Return True if history has a previous entry to navigate to."""
        return bool(self.page_history) and self.history_index > 0

    def _history_can_go_forward(self) -> bool:
        """Return True if history has a forward entry."""
        return bool(self.page_history) and self.history_index < len(self.page_history) - 1

    def _remember_history_cursor(self) -> None:
        """Remember the current cursor position for history restore."""
        if not self.current_path:
            return
        try:
            pos = self.editor.textCursor().position()
        except Exception:
            return
        self._history_cursor_positions[self.current_path] = pos

    def _should_focus_hr_tail(self, content: str) -> bool:
        """Return True if cursor should jump to trailing newline after a horizontal rule."""
        if not content:
            return False
        # Skip expensive work on very large files
        if len(content.encode("utf-8")) > 100_000:
            return False
        trimmed = content.rstrip("\n")
        if not trimmed:
            return False
        last_line = trimmed.splitlines()[-1]
        return last_line.strip() == "---"

    # --- History persistence & popup ---------------------------------

    def _persist_recent_history(self) -> None:
        """Persist recent history to the vault DB."""
        if not config.has_active_vault():
            return
        seen: set[str] = set()
        ordered: list[str] = []
        for path in self.page_history:
            if path and path not in seen:
                seen.add(path)
                ordered.append(path)
        config.save_recent_history(ordered[-50:])
        # Persist cursor positions for the same set
        positions: dict[str, int] = {}
        for path in ordered[-50:]:
            pos = self._history_cursor_positions.get(path)
            if isinstance(pos, int):
                positions[path] = pos
        config.save_recent_history_positions(positions)

    def _restore_recent_history(self) -> None:
        """Restore recent history from the vault DB."""
        if not config.has_active_vault():
            return
        history = config.load_recent_history()
        self.page_history = history[:50]
        self.history_index = len(self.page_history) - 1 if self.page_history else -1
        positions = config.load_recent_history_positions()
        # Only keep positions for known history paths
        self._history_cursor_positions.update({k: v for k, v in positions.items() if k in self.page_history})

    def _recent_history_candidates(self) -> list[str]:
        """Return MRU list (unique) for popup cycling."""
        seen: set[str] = set()
        result: list[str] = []
        for path in reversed(self.page_history):
            if path and path != self.current_path and path not in seen:
                seen.add(path)
                result.append(path)
        return result

    def _heading_popup_candidates(self) -> list[dict]:
        """Return headings for current page."""
        return [h for h in self._toc_headings if h]

    def _ensure_history_popup(self) -> None:
        if self._history_popup is None:
            popup = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
            popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            popup.setStyleSheet(
                "background: rgba(30,30,30,220); border: 1px solid #888; border-radius: 6px; padding: 8px;"
            )
            layout = QVBoxLayout(popup)
            layout.setContentsMargins(12, 8, 12, 8)
            self._history_popup_label = QLabel(popup)
            self._history_popup_label.setStyleSheet("color: #f5f5f5; font-weight: bold;")
            layout.addWidget(self._history_popup_label)
            self._history_popup_list = QListWidget(popup)
            self._history_popup_list.setStyleSheet(
                "QListWidget { background: transparent; color: #f5f5f5; border: none; }"
                "QListWidget::item { padding: 4px 6px; }"
                "QListWidget::item:selected { background: rgba(255,255,255,40); }"
            )
            layout.addWidget(self._history_popup_list)
            self._history_popup = popup

    def _show_history_popup(self) -> None:
        self._ensure_history_popup()
        if not self._history_popup or not self._history_popup_label or not self._history_popup_list:
            return
        self._history_popup_list.clear()
        if self._popup_mode == "history":
            for path in self._popup_items:
                display = self._history_leaf_label(path)
                item = QListWidgetItem(display)
                self._history_popup_list.addItem(item)
            label = "Recent pages"
        elif self._popup_mode == "heading":
            for heading in self._popup_items:
                title = heading.get("title") or "(heading)"
                line = heading.get("line", 1)
                level = max(1, min(5, int(heading.get("level", 1))))
                indent = "    " * (level - 1)
                item = QListWidgetItem(f"{indent}{title}  (line {line})")
                self._history_popup_list.addItem(item)
            label = "Headings"
        else:
            return
        if 0 <= self._popup_index < self._history_popup_list.count():
            self._history_popup_list.setCurrentRow(self._popup_index)
        self._history_popup_label.setText(label)
        editor_rect = self.editor.rect()
        top_left = self.editor.mapToGlobal(editor_rect.topLeft())
        popup_width = max(self._history_popup.sizeHint().width(), editor_rect.width() // 3)
        popup_height = self._history_popup.sizeHint().height()
        x = top_left.x() + editor_rect.width() // 2 - popup_width // 2
        y = top_left.y() + 24
        self._history_popup.resize(popup_width, popup_height)
        self._history_popup.move(x, y)
        self._history_popup.show()
        self._history_popup.raise_()

    def _cycle_popup(self, mode: str, reverse: bool = False) -> None:
        if mode == "history":
            items = self._recent_history_candidates()
        elif mode == "heading":
            items = self._heading_popup_candidates()
        else:
            return
        if not items:
            return
        if self._popup_mode != mode:
            self._popup_items = items
            self._popup_mode = mode
            self._popup_index = 0
        else:
            self._popup_items = items
            if self._popup_index < 0 or self._popup_index >= len(items):
                self._popup_index = 0
            else:
                delta = -1 if reverse else 1
                self._popup_index = (self._popup_index + delta) % len(items)
        self._show_history_popup()

    def _activate_history_popup_selection(self) -> None:
        if not self._popup_items or self._popup_index < 0 or not self._popup_mode:
            self._hide_history_popup()
            return
        target = self._popup_items[self._popup_index]
        mode = self._popup_mode
        self._hide_history_popup()
        if mode == "history" and target:
            self._remember_history_cursor()
            self._select_tree_path(target)
            self._open_file(target, force=True, restore_history_cursor=True)
        elif mode == "heading" and target:
            try:
                pos = int(target.get("position", 0))
            except Exception:
                pos = 0
            if pos <= 0:
                try:
                    line = int(target.get("line", 1))
                except Exception:
                    line = 1
                block = self.editor.document().findBlockByNumber(max(0, line - 1))
                if block.isValid():
                    pos = block.position()
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, pos))
            self._animate_or_flash_to_cursor(cursor)

    def _hide_history_popup(self) -> None:
        self._popup_items = []
        self._popup_index = -1
        self._popup_mode = None
        if self._history_popup:
            self._history_popup.hide()

    def _split_link_anchor(self, target: str) -> tuple[str, Optional[str]]:
        if "#" not in target:
            return target, None
        base, anchor = target.split("#", 1)
        return base or "", anchor or None

    def _anchor_slug(self, anchor: Optional[str]) -> Optional[str]:
        if not anchor:
            return None
        return heading_slug(anchor)

    def _scroll_to_anchor_slug(self, slug: Optional[str]) -> None:
        if not slug:
            return

        def jump() -> None:
            if not self.editor.jump_to_anchor(slug):
                self.statusBar().showMessage(f"Heading not found for anchor #{slug}", 4000)
                return
            cursor = self.editor.textCursor()
            self._animate_or_flash_to_cursor(cursor)

        QTimer.singleShot(0, jump)

    def _on_headings_changed(self, headings: list[dict]) -> None:
        self._toc_headings = list(headings or [])
        self._update_toc_visibility(force=True)

    def _update_toc_visibility(self, force: bool = False) -> None:
        """Show/hide/refresh the ToC based on headings and scrollability."""
        if not self.toc_widget:
            return
        scrollbar = self.editor.verticalScrollBar()
        scrollable = scrollbar and scrollbar.maximum() > 0
        enough_headings = len(self._toc_headings) > 1
        should_show = scrollable and enough_headings
        if not should_show:
            self.toc_widget.hide()
            return
        if not self.toc_widget.isVisible() or force:
            self.toc_widget.set_headings(self._toc_headings)
            self.toc_widget.show()
            try:
                # Reset to idle opacity when showing
                self.toc_widget._opacity_effect.setOpacity(self.toc_widget._idle_opacity)
            except Exception:
                pass

    def _toc_jump_to_position(self, position: int) -> None:
        cursor = self.editor.textCursor()
        cursor.setPosition(max(0, position))
        self._animate_or_flash_to_cursor(cursor)

    def _on_toc_collapsed_changed(self, collapsed: bool) -> None:
        config.save_toc_collapsed(collapsed)
        self._position_toc_widget()

    def _position_toc_widget(self) -> None:
        if not hasattr(self, "toc_widget") or not self.toc_widget:
            return
        viewport = self.editor.viewport()
        if viewport is None:
            return
        self._update_toc_visibility()
        margin = 12
        width = self.toc_widget.width()
        rect = viewport.rect()
        x = max(margin, rect.width() - width - margin)
        y = margin
        self.toc_widget.move(x, y)
        self.toc_widget.raise_()

    def _animate_or_flash_to_cursor(self, cursor: QTextCursor) -> None:
        """Smooth scroll to a heading; flash if already visible."""
        sb = self.editor.verticalScrollBar()
        if not sb:
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            return
        target_rect = self.editor.cursorRect(cursor)
        view_height = self.editor.viewport().height()
        current_val = sb.value()
        target_val = current_val + target_rect.top() - 10  # small top margin
        in_view = 0 <= target_rect.top() <= view_height - target_rect.height()
        if in_view:
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self._flash_heading(cursor)
            return
        if self._scroll_anim and self._scroll_anim.state() == QPropertyAnimation.Running:
            self._scroll_anim.stop()
        target_pos = cursor.position()
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(min(100, abs(target_val - current_val)))
        anim.setStartValue(current_val)
        anim.setEndValue(target_val)
        def _finish_flash() -> None:
            try:
                c = QTextCursor(self.editor.document())
                c.setPosition(target_pos)
                self.editor.setTextCursor(c)
                self.editor.ensureCursorVisible()
                self._flash_heading(c)
            except Exception:
                pass
        anim.finished.connect(_finish_flash)
        anim.start()
        self._scroll_anim = anim

    def _flash_heading(self, cursor: QTextCursor) -> None:
        """Briefly highlight the heading line."""
        try:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.cursor.clearSelection()
            sel.format.setBackground(QColor("#ffd54f"))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.format.setProperty(QTextFormat.UserProperty, 9991)
            current = self.editor.extraSelections()
            self.editor.setExtraSelections(current + [sel])

            def clear_flash() -> None:
                try:
                    keep = [
                        s
                        for s in self.editor.extraSelections()
                        if s.format.property(QTextFormat.UserProperty) != 9991
                    ]
                    self.editor.setExtraSelections(keep)
                except Exception:
                    pass

            QTimer.singleShot(220, clear_flash)
        except Exception:
            pass

    def _navigate_hierarchy_up(self) -> None:
        """Navigate up in page hierarchy (Alt+Up): Move up one level, stop at root."""
        if not self.current_path:
            return
        colon_path = path_to_colon(self.current_path)
        if not colon_path:
            return
        parts = colon_path.split(":")
        if len(parts) == 1:
            # Already at root vault
            self.statusBar().showMessage(f"At root: {colon_path}")
            return
        # Remove only the last segment
        parent_colon = ":".join(parts[:-1])
        parent_path = colon_to_path(parent_colon, self.vault_root_name)
        if parent_path:
            self._remember_history_cursor()
            self._select_tree_path(parent_path)
            self._open_file(parent_path, restore_history_cursor=True)
            if len(parts) == 2:
                # Just moved to root vault
                self.statusBar().showMessage(f"At root: {parent_colon}")
            else:
                self.statusBar().showMessage(f"Up: {parent_colon}")

    def _navigate_hierarchy_down(self) -> None:
        """Navigate down in page hierarchy (Alt+Down): Open first child page."""
        if not self.current_path:
            return
        # Get current folder path
        folder_path = self._file_path_to_folder(self.current_path)
        if not folder_path or not self.vault_root:
            return
        # Find first child page
        folder = Path(self.vault_root) / folder_path.lstrip("/")
        if not folder.exists() or not folder.is_dir():
            return
        # Get all subdirectories
        subdirs = sorted([d for d in folder.iterdir() if d.is_dir()])
        if not subdirs:
            return
        # Open first child page
        first_child = subdirs[0]
        child_file = first_child / f"{first_child.name}{PAGE_SUFFIX}"
        if child_file.exists():
            child_path = f"/{child_file.relative_to(Path(self.vault_root)).as_posix()}"
            self._remember_history_cursor()
            self._select_tree_path(child_path)
            self._open_file(child_path, restore_history_cursor=True)

    def _navigate_tree(self, delta: int, leaves_only: bool) -> None:
        indexes = self._gather_indexes(leaves_only)
        if not indexes:
            return
        current = self.tree_view.currentIndex()
        try:
            idx = indexes.index(current)
        except ValueError:
            idx = -1 if delta > 0 else 0
        new_idx = max(0, min(len(indexes) - 1, idx + delta))
        if new_idx == idx:
            return
        target = indexes[new_idx]
        self.tree_view.setCurrentIndex(target)
        self.tree_view.scrollTo(target)

    def _reindex_vault(self, show_progress: bool = False) -> None:
        """Reindex all pages in the vault."""
        if not self.vault_root or not config.has_active_vault():
            return
        if not self._ensure_writable("reindex the vault"):
            return
        
        root = Path(self.vault_root)
        txt_files = sorted(root.rglob(f"*{PAGE_SUFFIX}"))
        
        progress = None
        if show_progress:
            progress = QProgressDialog("Indexing vault...", None, 0, len(txt_files), self)
            progress.setWindowTitle("Reindexing")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()
            self.statusBar().showMessage("Building index...", 0)
        
        for idx, txt_file in enumerate(txt_files, start=1):
            rel_path = txt_file.relative_to(root)
            path_str = f"/{rel_path.as_posix()}"
            try:
                content = txt_file.read_text(encoding="utf-8")
                indexer.index_page(path_str, content)
            except Exception:
                continue
            if progress:
                progress.setValue(idx)
                QApplication.processEvents()
        
        self.right_panel.refresh_tasks()
        self.right_panel.refresh_links(self.current_path)
        
        if progress:
            progress.close()
            page_count = len(txt_files)
            self.statusBar().showMessage(f"Index rebuilt: {page_count} pages", 3000)

    # --- Utilities -----------------------------------------------------
    def _alert(self, message: str) -> None:
        QMessageBox.critical(self, "ZimX", message)

    def _update_window_title(self) -> None:
        parts: list[str] = []
        if self.current_path:
            colon = path_to_colon(self.current_path)
            if colon:
                parts.append(colon)
        if self.vault_root_name:
            parts.append(self.vault_root_name)
        if self._read_only:
            parts.append("Read-Only")
        suffix = "ZimX Desktop"
        title = " | ".join(parts + [suffix]) if parts else suffix
        self.setWindowTitle(title)

    def _suspend_vi_mode_for_dialog(self) -> bool:
        """Temporarily disable vi-mode while a modal dialog is open.

        Returns:
            True if vi-mode was active and was turned off, False otherwise.
        """
        was_active = self._vi_mode_active
        if was_active:
            self._vi_mode_active = False
            self.editor.set_vi_mode(False)
            self._apply_vi_mode_statusbar_style()
        self._lock_vi_mode_toggle()
        return was_active

    def _restore_vi_mode_after_dialog(self, was_active: bool) -> None:
        """Re-enable vi-mode if it was active before a dialog was shown."""
        self._unlock_vi_mode_toggle()
        if was_active:
            self._vi_mode_active = True
            self.editor.set_vi_mode(True)
            self._apply_vi_mode_statusbar_style()

    def _toggle_vi_mode(self) -> None:
        """Toggle vi-mode navigation on/off with visual status bar indicator."""
        if getattr(self, '_vi_mode_locked', False):
            # Ignore toggle requests while locked for dialog
            return
        prev_focus = self.focusWidget()
        self._vi_mode_active = not self._vi_mode_active
        self._apply_vi_mode_statusbar_style()
        # Restore focus to whichever widget had it before toggling (avoid forcing editor focus)
        try:
            if prev_focus:
                prev_focus.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _install_vi_mode_filters(self) -> None:
        for target in getattr(self, "_vi_filter_targets", []):
            target.removeEventFilter(self)
        self._vi_filter_targets = []
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._vi_filter_targets.append(app)

    def eventFilter(self, obj, event):  # type: ignore[override]
        # (Removed overlay repositioning logic; using status bar indicator)
        if event.type() == QEvent.KeyPress:
            # Ctrl+Tab history popup
            if event.key() == Qt.Key_Tab and (event.modifiers() & Qt.ControlModifier):
                # With Shift: cycle headings; without: history
                if event.modifiers() & Qt.ShiftModifier:
                    reverse = event.key() == Qt.Key_Backtab
                    self._cycle_popup("heading", reverse=reverse)
                else:
                    self._cycle_popup("history", reverse=event.key() == Qt.Key_Backtab)
                return True
            # Allow Esc to exit vi-mode explicitly
            if event.key() == Qt.Key_Escape:
                try:
                    if getattr(self, "editor", None) and self.editor.is_ai_overlay_visible():
                        return False
                except Exception:
                    pass
                if self._vi_mode_active:
                    self._vi_mode_active = False
                    self._apply_vi_mode_statusbar_style()
                    return True
            if event.key() == Qt.Key_Semicolon:
                mods = event.modifiers()
                base_mods = mods & ~Qt.KeypadModifier
                try:
                    base_mods &= ~Qt.GroupSwitchModifier  # type: ignore[attr-defined]
                except Exception:
                    pass
                if base_mods == Qt.AltModifier:
                    if getattr(self, "_vi_mode_locked", False):
                        return True
                    self._toggle_vi_mode()
                    return True
            if self._vi_mode_active:
                # Always let Control-modified shortcuts through (bold/italic/strike/etc.)
                if event.modifiers() & Qt.ControlModifier:
                    return super().eventFilter(obj, event)

                # Handle Alt+H and Alt+L for history navigation while preserving vi mode
                key = event.key()
                alt = bool(event.modifiers() & Qt.AltModifier)
                shift = bool(event.modifiers() & Qt.ShiftModifier) 
                

                
                if alt and not shift:
                    if key == Qt.Key_H:
                        if self._history_can_go_back():
                            self._restore_vi_mode_after_nav = True
                            self._navigate_history_back()
                        return True
                    elif key == Qt.Key_L:
                        if self._history_can_go_forward():
                            self._restore_vi_mode_after_nav = True
                            self._navigate_history_forward()
                        return True
                    elif key == Qt.Key_K:
                        self._restore_vi_mode_after_nav = True
                        self._navigate_hierarchy_up()
                        return True
                    elif key == Qt.Key_J:
                        self._restore_vi_mode_after_nav = True
                        self._navigate_hierarchy_down()
                        return True
                
                target = self._vi_mode_target_widget()
                if target:
                    if target is self.editor and self.editor.is_vi_strict_mode_enabled():
                        if self.editor.handle_vi_strict_key(event):
                            return True
                        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
                            return False
                        # Let strict-mode keys reach the editor without legacy translation
                        return False
                    mapping = self._translate_vi_key_event(event)
                    if mapping:
                        key_name = chr(event.key()) if Qt.Key_A <= event.key() <= Qt.Key_Z else f"Key_{event.key()}"
                        if self._vi_debug:
                           self._debug(f"Vi-mode: {key_name} -> {mapping[0]} with mods {mapping[1]}")
                        self._dispatch_vi_navigation(target, mapping)
                        return True
                    # Block unmapped letter keys ONLY if they don't have Control modifier
                    # (Control+key are commands that should be allowed through)
                    if Qt.Key_A <= event.key() <= Qt.Key_Z:
                        has_ctrl = bool(event.modifiers() & Qt.ControlModifier)
                        if not has_ctrl:
                            if self._vi_debug:
                                self._debug(f"Vi-mode: blocking unmapped key {chr(event.key())}")
                            return True
        elif event.type() == QEvent.KeyRelease:
            if event.key() == Qt.Key_Control and self._popup_items:
                self._activate_history_popup_selection()
                return True
        return super().eventFilter(obj, event)


    # Removed overlay helpers

    def _vi_mode_target_widget(self) -> QWidget | None:
        widget = self.focusWidget()
        if widget is None:
            return None
        if widget is self.editor or (self.editor and self.editor.isAncestorOf(widget)):
            return widget
        if widget is self.tree_view or self.tree_view.isAncestorOf(widget):
            return widget
        if widget is self.right_panel or self.right_panel.isAncestorOf(widget):
            return widget
        return None

    def _translate_vi_key_event(self, event: QKeyEvent) -> tuple[int, Qt.KeyboardModifiers] | None:
        # Check for disallowed modifiers (but allow keys with only Shift or no modifiers)
        key = event.key()
        text = event.text()
        mods = event.modifiers()
        shift = bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)
        strict_vi = getattr(self, "editor", None) is not None and self.editor.is_vi_strict_mode_enabled()
        if self._vi_debug:
            mods_val = mods
            try:
                mods_val = int(mods)  # type: ignore[arg-type]
            except Exception:
                try:
                    mods_val = int(getattr(mods, "value", mods))  # type: ignore[arg-type]
                except Exception:
                    pass
            self._debug(f"Vi-mode key event: key={key} text='{text}' shift={shift} alt={alt} mods={mods_val}")
        # AltGr on Windows sends Ctrl+Alt; if this produced ";" / ":" we should still map End.
        altgr_semicolon = text in (";", ":") and bool(mods & Qt.ControlModifier) and bool(mods & Qt.AltModifier)



        # Allow Alt (for Alt+h / Alt+l mappings) plus Shift; block everything else
        disallowed_mask = Qt.ShiftModifier | Qt.KeypadModifier | Qt.AltModifier
        if altgr_semicolon:
            # Allow Ctrl too when AltGr was used to type ";" / ":".
            disallowed_mask |= Qt.ControlModifier
        # Some layouts set GroupSwitchModifier; allow it.
        try:
            disallowed_mask |= Qt.GroupSwitchModifier  # type: ignore[attr-defined]
        except Exception:
            pass
        disallowed = mods & ~disallowed_mask
        if disallowed:
            print(f"[DEBUG] _translate_vi_key_event: Blocking key {key} due to disallowed modifiers: {disallowed}")
            return None

        target_key = None
        target_modifiers = Qt.KeyboardModifiers(Qt.NoModifier)

        # Basic motion (j/k/h/l) + shifted variants
        if key == Qt.Key_J:
            if alt and not shift:
                # Alt+J is handled directly in eventFilter for history navigation
                return None
            elif shift:
                # Shift+J -> Shift+Down (line selection)
                target_key = Qt.Key_Down
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            else:
                target_key = Qt.Key_Down
        elif key == Qt.Key_K:
            if alt and not shift:
                # Alt+K is handled directly in eventFilter for history navigation
                return None
            elif shift:
                # Shift+K -> Shift+Up (line selection)
                target_key = Qt.Key_Up
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            else:
                target_key = Qt.Key_Up
        elif key == Qt.Key_H:
            if shift:
                # Shift+H -> Shift+Left (select one char left)
                target_key = Qt.Key_Left
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            else:
                target_key = Qt.Key_Left
        elif key == Qt.Key_L:
            if shift:
                # Shift+L -> Shift+Right (select one char right)
                target_key = Qt.Key_Right
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            else:
                target_key = Qt.Key_Right
        # Line selection helper keys (+U/+N) for Shift+Up / Shift+Down
        elif key == Qt.Key_N and shift:
            target_key = Qt.Key_Down
            target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_U and shift:
            target_key = Qt.Key_Up
            target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        # Undo / Redo / Copy / Cut / Paste
        elif key == Qt.Key_U and not shift:
            target_key = Qt.Key_Z
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_R and not shift:
            target_key = Qt.Key_Y
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_C and not shift:
            target_key = Qt.Key_C
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_X and not shift:
            target_key = Qt.Key_X
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_P and not shift:
            target_key = Qt.Key_V
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        # Word motions (w forward, b backward)
        elif key == Qt.Key_W and not shift:
            target_key = Qt.Key_Right
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_B and not shift:
            target_key = Qt.Key_Left
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif strict_vi and key == Qt.Key_E and not shift:
            target_key = Qt.Key_Right
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        # Home/End and selection variants
        elif key == Qt.Key_A:
            target_key = Qt.Key_Home
            if shift:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key in (Qt.Key_Semicolon, Qt.Key_Colon) or text in (";", ":"):
            # Some Windows layouts report this physical key as Key_Colon, and some layouts
            # require Shift to produce ";" which would otherwise force Shift+End. Drive
            # selection only when the character is ":" so ";" always maps to a plain End.
            is_colon = key == Qt.Key_Colon or text == ":"
            target_key = Qt.Key_End
            if is_colon:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            if self._vi_debug:
                mods_val = mods
                try:
                    mods_val = int(mods)  # type: ignore[arg-type]
                except Exception:
                    try:
                        mods_val = int(getattr(mods, "value", mods))  # type: ignore[arg-type]
                    except Exception:
                        pass
                self._debug(f"Vi-mode: ';' key mapping -> End (colon={is_colon}, key={key}, text='{text}', mods={mods_val})")
        elif strict_vi and key == Qt.Key_0:
            target_key = Qt.Key_Home
        elif strict_vi and key == Qt.Key_Dollar:
            target_key = Qt.Key_End
        elif strict_vi and key == Qt.Key_AsciiCircum:
            target_key = Qt.Key_Home
            if shift:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif strict_vi and key == Qt.Key_G:
            target_key = Qt.Key_Home if not shift else Qt.Key_End
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        # Delete variants
        elif key == Qt.Key_D:
            if shift:
                # Shift+D -> delete whole line
                target_key = Qt.Key_Delete
                target_modifiers = Qt.KeyboardModifiers(Qt.AltModifier | Qt.ShiftModifier)
            else:
                # Plain 'd' -> Delete key
                target_key = Qt.Key_Delete
                target_modifiers = Qt.KeyboardModifiers(Qt.NoModifier)

        if target_key is None:
            return None

        return target_key, target_modifiers

    def _dispatch_vi_navigation(self, target: QWidget, mapping: tuple[int, Qt.KeyboardModifiers]) -> None:
        key, modifiers = mapping

        # Special handling for delete variants
        if key == Qt.Key_Delete and hasattr(target, 'textCursor'):
            # Shift+D: line delete (we encoded modifiers with Alt|Shift) OR Alt+Delete legacy
            is_line_delete = bool(modifiers & Qt.ShiftModifier) or bool(modifiers & Qt.AltModifier and modifiers & Qt.ShiftModifier)
            cursor = target.textCursor()
            if is_line_delete:
                # Delete whole current line
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                    target.setTextCursor(cursor)
                    return
                cursor.movePosition(QTextCursor.StartOfLine)
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                target.setTextCursor(cursor)
                return
            else:
                # Plain delete
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                else:
                    cursor.deleteChar()
                target.setTextCursor(cursor)
                return

        # Prefer direct cursor operations for the editor to reduce repaint churn
        if target is self.editor or (hasattr(target, 'textCursor') and hasattr(target, 'setTextCursor')):
            try:
                cur = target.textCursor()
                keep = QTextCursor.KeepAnchor if (modifiers & Qt.ShiftModifier) else QTextCursor.MoveAnchor
                if key == Qt.Key_Down:
                    cur.movePosition(QTextCursor.Down, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Up:
                    cur.movePosition(QTextCursor.Up, keep)
                    target.setTextCursor(cur)
                    return
                # DON'T intercept Left/Right for the editor - let them pass through to editor's keyPressEvent
                # so the special link boundary handling works correctly. The editor has logic to skip
                # over hidden null bytes in display-format links.
                # We'll let these fall through to synthetic event generation below.
                if key == Qt.Key_Home:
                    cur.movePosition(QTextCursor.StartOfLine, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_End:
                    cur.movePosition(QTextCursor.EndOfLine, keep)
                    target.setTextCursor(cur)
                    return
                if key == Qt.Key_Z and (modifiers & Qt.ControlModifier):
                    try:
                        target.undo()
                        return
                    except Exception:
                        pass
                if key == Qt.Key_X and (modifiers & Qt.ControlModifier):
                    try:
                        target.cut()
                        return
                    except Exception:
                        pass
                if key == Qt.Key_V and (modifiers & Qt.ControlModifier):
                    try:
                        target.paste()
                        return
                    except Exception:
                        pass
            except Exception:
                # Fallback to synthetic below
                pass

        # Fallback: synthesize key events for non-editor targets or unhandled keys
        # Use the full QKeyEvent constructor for cross-platform compatibility
        # QKeyEvent(type, key, modifiers, nativeScanCode, nativeVirtualKey, nativeModifiers, text, autorep, count)
        # For synthetic events, we pass empty string for text and defaults for native parameters
        press_event = QKeyEvent(QEvent.KeyPress, key, modifiers, "", False, 1)
        release_event = QKeyEvent(QEvent.KeyRelease, key, modifiers, "", False, 1)
        QApplication.sendEvent(target, press_event)
        QApplication.sendEvent(target, release_event)

    def _update_dirty_indicator(self) -> None:
        """Refresh the dirty badge next to the VI indicator."""
        if not hasattr(self, "_dirty_status_label"):
            return
        if self._read_only:
            self._dirty_status_label.setText("O/")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #9e9e9e; color: #f5f5f5; margin-right: 6px; text-decoration: line-through;"
            )
            self._dirty_status_label.setToolTip("Read-only: changes cannot be saved in this window")
            return
        dirty = self._is_editor_dirty()
        if dirty:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #e57373; color: #000; margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("Unsaved changes")
        else:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #81c784; color: #000; margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("All changes saved")

    def _update_vi_badge(self) -> None:
        """Refresh the VI/INS badge text and background."""
        if not hasattr(self, "_vi_status_label"):
            return
        strict_mode_enabled = False
        insert_mode = False
        try:
            strict_mode_enabled = bool(self.editor.is_strict_vi_mode_enabled())
            if strict_mode_enabled:
                insert_mode = bool(self.editor.is_vi_insert_mode_active())
        except Exception:
            strict_mode_enabled = False
            insert_mode = False

        label_text = "INS" if strict_mode_enabled else "VI"
        if strict_mode_enabled:
            suffix = " background-color: #ffd54d; color: #000;" if insert_mode else " background-color: transparent;"
        else:
            suffix = " background-color: #ffd54d; color: #000;" if self._vi_mode_active else " background-color: transparent;"

        try:
            self._vi_status_label.setText(label_text)
            self._vi_status_label.setStyleSheet(self._vi_badge_base_style + suffix)
        except Exception:
            pass

    def _apply_vi_mode_statusbar_style(self) -> None:
        # Switch editor cursor styling for vi-mode; no banners/overlays
        self.editor.set_vi_mode(self._vi_mode_active)
        # Keep strict vi insert/navigation state aligned with the active flag
        try:
            if self.editor.is_strict_vi_mode_enabled():
                self.editor.set_vi_insert_mode_active(not self._vi_mode_active)
            else:
                self.editor.set_vi_insert_mode_active(False)
        except Exception:
            pass

        # Show a brief status message when vi-mode toggles and refresh the badge
        try:
            state = "ON" if self._vi_mode_active else "OFF"
            self.statusBar().showMessage(f"Vi mode: {state}", 2000)
            self._update_vi_badge()
        except Exception:
            # Avoid breaking if status bar not yet initialized
            pass

    # (Removed move/resize overlays; not used)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Handle window resize: reposition TOC and save geometry."""
        super().resizeEvent(event)
        self._position_toc_widget()
        self.geometry_save_timer.start()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        """Persist window position changes (paired with resize)."""
        super().moveEvent(event)
        self.geometry_save_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Stop any pending timers
        self.autosave_timer.stop()
        self.geometry_save_timer.stop()
        
        # Disconnect editor signals to prevent save attempts after HTTP client is closed
        try:
            self.editor.focusLost.disconnect()
        except:
            pass
        
        # Save current file and geometry
        self._save_current_file(auto=True)
        self._save_geometry()
        self._persist_recent_history()
        
        # Close HTTP client and clean up
        self.http.close()
        config.set_active_vault(None)
        self._release_vault_lock()
        return super().closeEvent(event)

    def _describe_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return "<invalid>"
        return (
            f"path={index.data(PATH_ROLE)}, open={index.data(OPEN_ROLE)}, is_dir={bool(index.data(TYPE_ROLE))}"
        )

    def _debug(self, message: str) -> None:
        print(f"[ZimX] {message}")
    def _history_leaf_label(self, path: str) -> str:
        display = path_to_colon(path) or path
        if ":" in display:
            parts = [segment for segment in display.split(":") if segment]
            if parts:
                tail = parts[-1]
                return f"...{tail}" if len(parts) > 1 else tail
        normalized = path.lstrip("/")
        leaf = Path(normalized).stem or normalized
        return leaf
