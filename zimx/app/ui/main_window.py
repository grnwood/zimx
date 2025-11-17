from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt, Signal, QTimer, QObject
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
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
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
    QWidget,
    QVBoxLayout,
    QFrame,
    QLabel,
    QHBoxLayout,
)

from zimx.app import config, indexer
from zimx.server.adapters.files import PAGE_SUFFIX

from .markdown_editor import MarkdownEditor
from .tabbed_right_panel import TabbedRightPanel
from .jump_dialog import JumpToPageDialog
from .toc_widget import TableOfContentsWidget
from .heading_utils import heading_slug
from .preferences_dialog import PreferencesDialog
from .insert_link_dialog import InsertLinkDialog
from .new_page_dialog import NewPageDialog
from .path_utils import colon_to_path, path_to_colon


PATH_ROLE = int(Qt.ItemDataRole.UserRole)
TYPE_ROLE = PATH_ROLE + 1
OPEN_ROLE = TYPE_ROLE + 1


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

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            self.collapseAll()
            event.accept()
            return
        if event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Down, Qt.Key_Up):
            direction = 1 if event.key() == Qt.Key_Down else -1
            self._walk_tree(direction)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            super().keyPressEvent(event)
            self.enterActivated.emit()
            return
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


class MainWindow(QMainWindow):
    def __init__(self, api_base: str) -> None:
        super().__init__()
        self.setWindowTitle("ZimX Desktop")
        self.api_base = api_base.rstrip("/")
        self.http = httpx.Client(base_url=self.api_base, timeout=10.0)
        self.vault_root: Optional[str] = None
        self.vault_root_name: Optional[str] = None
        self.current_path: Optional[str] = None
        
        # Page navigation history
        self.page_history: list[str] = []
        self.history_index: int = -1
        # Guard to suppress auto-open on tree selection during programmatic navigation
        self._suspend_selection_open: bool = False
        
        # Track virtual (unsaved) pages
        self.virtual_pages: set[str] = set()
        # Track original content of virtual pages to detect actual edits
        self.virtual_page_original_content: dict[str, str] = {}
        
        # Bookmarks
        self.bookmarks: list[str] = []
        self.bookmark_buttons: dict[str, QPushButton] = {}

        self.tree_view = VaultTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["Vault"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setHeaderHidden(False)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._open_context_menu)
        self.tree_view.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.tree_view.enterActivated.connect(self._focus_editor_from_tree)
        self.dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        
        # Create custom header widget with "Show Journal" checkbox
        self.tree_header_widget = QWidget()
        tree_header_layout = QHBoxLayout()
        tree_header_layout.setContentsMargins(5, 2, 5, 2)
        tree_header_layout.setSpacing(10)
        
        tree_header_label = QLabel("Vault")
        tree_header_label.setStyleSheet("font-weight: bold;")
        tree_header_layout.addWidget(tree_header_label)
        
        self.show_journal_checkbox = QCheckBox("Show Journal")
        self.show_journal_checkbox.setChecked(True)
        self.show_journal_checkbox.toggled.connect(self._on_show_journal_toggled)
        tree_header_layout.addWidget(self.show_journal_checkbox)
        
        tree_header_layout.addStretch()
        self.tree_header_widget.setLayout(tree_header_layout)
        
        # Set the custom header widget
        self.tree_view.header().hide()
        self.tree_view.setHeaderHidden(True)

        self.editor = MarkdownEditor()
        self.editor.imageSaved.connect(self._on_image_saved)
        self.editor.textChanged.connect(lambda: self.autosave_timer.start())
        self.editor.focusLost.connect(lambda: self._save_current_file(auto=True))
        self.editor.linkActivated.connect(self._open_camel_link)
        self.editor.linkHovered.connect(self._on_link_hovered)
        self.font_size = 14
        self.editor.set_font_point_size(self.font_size)
        # Load vi-mode block cursor preference
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
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
        self.editor.headingsChanged.connect(self.toc_widget.set_headings)
        self.editor.viewportResized.connect(self._position_toc_widget)
        self.editor.verticalScrollBar().valueChanged.connect(self._position_toc_widget)

        self.right_panel = TabbedRightPanel()
        self.right_panel.refresh_tasks()
        self.right_panel.taskActivated.connect(self._open_task_from_panel)
        self.right_panel.dateActivated.connect(self._open_journal_date)
        self._inline_editor: Optional[InlineNameEdit] = None
        self._pending_selection: Optional[str] = None
        self._suspend_autosave = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30_000)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True))

        # Vi-mode state
        self._vi_mode_active = False
        # Optional vi debug logging (set True to enable noisy logs)
        self._vi_debug = False

        editor_split = QSplitter()
        editor_split.addWidget(self.editor)
        editor_split.addWidget(self.right_panel)
        editor_split.setStretchFactor(0, 4)
        editor_split.setStretchFactor(1, 2)

        # Create tree container with custom header
        tree_container = QWidget()
        tree_layout = QVBoxLayout()
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(0)
        tree_layout.addWidget(self.tree_header_widget)
        tree_layout.addWidget(self.tree_view)
        tree_container.setLayout(tree_layout)
        
        splitter = QSplitter()
        splitter.addWidget(tree_container)
        splitter.addWidget(editor_split)
        splitter.setStretchFactor(1, 5)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Container (no vi-mode banner)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(container)
        self._position_toc_widget()

        # No overlay/indicator widgets; vi-mode is represented by editor cursor style

        # Build toolbar and main menus
        self._build_toolbar()
        # Add 'Vault' menu with required actions
        vault_menu = self.menuBar().addMenu("Vault")
        open_vault_action = QAction("Open Vault", self)
        open_vault_action.setToolTip("Open an existing vault")
        open_vault_action.triggered.connect(self._select_vault)
        vault_menu.addAction(open_vault_action)
        new_vault_action = QAction("New Vault", self)
        new_vault_action.setToolTip("Create a new vault")
        new_vault_action.triggered.connect(self._create_vault)
        vault_menu.addAction(new_vault_action)
        view_vault_disk_action = QAction("View Vault on Disk", self)
        view_vault_disk_action.setToolTip("Open the vault folder in your system file manager")
        view_vault_disk_action.triggered.connect(self._open_vault_on_disk)
        vault_menu.addAction(view_vault_disk_action)

        self._register_shortcuts()
        self._vi_filter_targets: list[QObject] = []
        self._install_vi_mode_filters()
        # Update focus borders when focus moves between widgets
        app = QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.connect(lambda old, now: self._apply_focus_borders())
            except Exception:
                pass
        # Apply initial border state
        self._apply_focus_borders()
        self.statusBar().showMessage("Select a vault to get started")
        self._default_status_stylesheet = self.statusBar().styleSheet()
        last_vault = config.load_last_vault()
        if last_vault:
            self._set_vault(last_vault)

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
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        # vault_root holds absolute path to the vault root directory
        vault_path = self.vault_root
        if not vault_path:
            self.statusBar().showMessage("No vault selected.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(vault_path)))
        self.statusBar().showMessage(f"Opened vault folder: {vault_path}")

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
        link_shortcut.activated.connect(self._insert_link)
        copy_link_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        copy_link_shortcut.activated.connect(self._copy_current_page_link)
        focus_toggle = QShortcut(QKeySequence("Ctrl+Space"), self)
        focus_toggle.activated.connect(self._toggle_focus_between_tree_and_editor)
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
        nav_back.activated.connect(self._navigate_history_back)
        nav_forward.activated.connect(self._navigate_history_forward)
        nav_up.activated.connect(self._navigate_hierarchy_up)
        nav_down.activated.connect(self._navigate_hierarchy_down)
        nav_pg_up.activated.connect(lambda: self._navigate_tree(-1, leaves_only=True))
        nav_pg_down.activated.connect(lambda: self._navigate_tree(1, leaves_only=True))

    # --- Vault actions -------------------------------------------------
    def _select_vault(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Vault")
        if not directory:
            return
        self._set_vault(directory)

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
        self._set_vault(str(target))

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

    def _set_vault(self, directory: str) -> None:
        self.right_panel.clear_tasks()
        config.set_active_vault(None)
        try:
            resp = self.http.post("/api/vault/select", json={"path": directory})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert(f"Failed to set vault: {exc}")
            return
        self.vault_root = resp.json().get("root")
        self.vault_root_name = Path(self.vault_root).name if self.vault_root else None
        if self.vault_root:
            config.set_active_vault(self.vault_root)
            config.save_last_vault(self.vault_root)
            self.font_size = config.load_font_size(self.font_size)
            self.editor.set_font_point_size(self.font_size)
            # Load show_journal setting
            show_journal = config.load_show_journal()
            self.show_journal_checkbox.setChecked(show_journal)
        self.editor.set_context(self.vault_root, None)
        self.editor.set_markdown("")
        self.current_path = None
        self.statusBar().showMessage(f"Vault: {self.vault_root}")
        self._update_window_title()
        self._populate_vault_tree()
        self._reindex_vault()
        self._load_bookmarks()
        if self.vault_root:
            self.right_panel.set_vault_root(self.vault_root)

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
        
        # Check if Journal should be filtered
        show_journal = self.show_journal_checkbox.isChecked()
        
        for node in data:
            # Hide the root vault node (path == "/") and render only its children
            if node.get("path") == "/":
                for child in node.get("children", []):
                    # Filter out Journal folder if checkbox is unchecked
                    if not show_journal and child.get("name") == "Journal":
                        continue
                    self._add_tree_node(self.tree_model.invisibleRootItem(), child)
            else:
                self._add_tree_node(self.tree_model.invisibleRootItem(), node)
        self.tree_view.expandAll()
        if self._pending_selection:
            self._select_tree_path(self._pending_selection)
            self._pending_selection = None
        self.right_panel.refresh_tasks()
        self.right_panel.refresh_calendar()

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

    def _on_selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self._debug(
            f"Tree selection changed: current={self._describe_index(current)}, previous={self._describe_index(previous)}"
        )
        if previous.isValid():
            prev_target = previous.data(OPEN_ROLE) or previous.data(PATH_ROLE)
            if prev_target and prev_target == self.current_path:
                # Check if leaving an unsaved virtual page
                if self.current_path in self.virtual_pages:
                    self._cleanup_virtual_page_if_unchanged(self.current_path)
                self._save_current_file(auto=True)
        if not current.isValid():
            self._debug("Tree selection cleared (no valid index).")
            return
        # If we're programmatically changing selection (history/hierarchy nav), don't auto-open here
        if self._suspend_selection_open:
            self._debug("Selection change suppressed (programmatic nav).")
            return
        open_target = current.data(OPEN_ROLE) or current.data(PATH_ROLE)
        self._debug(f"Tree selection target resolved to: {open_target!r}")
        if not open_target:
            self._debug("Tree selection skipped: no open target.")
            return
        if open_target == self.current_path:
            self._debug("Tree selection skipped: already editing this path.")
            return
        try:
            self._open_file(open_target)
        except Exception as exc:
            self._debug(f"Tree selection crash while opening {open_target!r}: {exc!r}")
            raise

    def _open_file(self, path: str, retry: bool = False, add_to_history: bool = True, force: bool = False, cursor_at_end: bool = False) -> None:
        if not path or (path == self.current_path and not force):
            return
        
        # Clean up current page if it's an unchanged virtual page
        if self.current_path and self.current_path in self.virtual_pages:
            self._cleanup_virtual_page_if_unchanged(self.current_path)
        
        self.autosave_timer.stop()
        
        # Add to page history (unless we're navigating through history)
        if add_to_history and path != self.current_path:
            # Remove any forward history when opening a new page
            if self.history_index < len(self.page_history) - 1:
                self.page_history = self.page_history[:self.history_index + 1]
            # Add new page if not duplicate of last
            if not self.page_history or self.page_history[-1] != path:
                self.page_history.append(path)
                self.history_index = len(self.page_history) - 1
        
        try:
            resp = self.http.post("/api/file/read", json={"path": path})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._alert(f"Failed to open {path}: {exc}")
            return
        except httpx.HTTPError as exc:
            self._alert(f"Failed to open {path}: {exc}")
            return
        content = resp.json().get("content", "")
        self.editor.set_context(self.vault_root, path)
        self.current_path = path
        self._suspend_autosave = True
        self.editor.set_markdown(content)
        self._suspend_autosave = False
        updated = indexer.index_page(path, content)
        if updated:
            self.right_panel.refresh_tasks()
        move_cursor_to_end = cursor_at_end or self._should_focus_hr_tail(content)
        if move_cursor_to_end:
            cursor = self.editor.textCursor()
            display_length = len(self.editor.toPlainText())
            cursor.setPosition(display_length)
            self.editor.setTextCursor(cursor)
        else:
            self.editor.moveCursor(QTextCursor.Start)
        # Always show editing status; vi-mode banner is separate
        display_path = path_to_colon(path) or path
        if hasattr(self, "toc_widget"):
            self.toc_widget.set_base_path(display_path)
            self.editor.refresh_heading_outline()
        self.statusBar().showMessage(f"Editing {display_path}")
        self._update_window_title()
        
        # Update calendar if this is a journal page
        self._update_calendar_for_journal_page(path)

    def _save_current_file(self, auto: bool = False) -> None:
        if self._suspend_autosave:
            self._debug("Autosave suppressed (suspend flag set).")
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
        path = resp.json().get("path")
        if path:
            # Repopulate tree so newly created nested year/month/day nodes appear
            self._pending_selection = path
            self._populate_vault_tree()
            # Apply journal templates (year/month/day) if newly created
            self._apply_journal_templates(path)
            # Open with cursor at end for immediate typing
            self._open_file(path, cursor_at_end=True)
            self.statusBar().showMessage("Journal: today", 4000)
            # Ensure focus returns to editor (tree selection may have taken focus)
            self.editor.setFocus()
            self._apply_focus_borders()

    def _apply_journal_templates(self, day_file_path: str) -> None:
        """Ensure year/month/day journal pages exist and apply templates with variable substitution.

        day_file_path: relative file path like /Journal/2025/11/12/12.txt (from API)
        Templates: JournalYear.txt, JournalMonth.txt, JournalDay.txt
        Variables: {{YYYY}}, {{Month}}, {{DOW}}, {{dd}}
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
        if needs_write(year_page) and year_tpl.exists():
            content = render(year_tpl)
            if content:
                year_page.write_text(content, encoding="utf-8")
        # Month
        if needs_write(month_page) and month_tpl.exists():
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
        if needs_day_write(day_page) and day_tpl.exists():
            content = render(day_tpl)
            if content:
                day_page.write_text(content, encoding="utf-8")
        # Always perform a substitution pass on existing day page if placeholders remain
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

    def _jump_to_page(self) -> None:
        if not config.has_active_vault():
            return
        dlg = JumpToPageDialog(self)
        if dlg.exec() == QDialog.Accepted:
            target = dlg.selected_path()
            if target:
                self._select_tree_path(target)
                self._open_file(target)

    def _insert_link(self) -> None:
        """Open insert link dialog and insert selected link at cursor."""
        if not config.has_active_vault():
            return
        # Save current page before inserting link to ensure it's indexed
        if self.current_path:
            self._save_current_file(auto=True)
        dlg = InsertLinkDialog(self)
        if dlg.exec() == QDialog.Accepted:
            colon_path = dlg.selected_colon_path()
            link_name = dlg.selected_link_name()
            if colon_path:
                self.editor.insert_link(colon_path, link_name)
                self.editor.setFocus()

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
                self.statusBar().showMessage(f"Copied link: {colon_path}", 3000)

    def _show_new_page_dialog(self) -> None:
        """Show dialog to create a new page with template selection (Ctrl+N)."""
        if not self.vault_root:
            self._alert("Select a vault before creating pages.")
            return
        
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
        dlg = PreferencesDialog(self)
        if dlg.exec() == QDialog.Accepted:
            # Reload vi-mode cursor setting and apply to editor
            self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
            # Re-apply vi-mode state to refresh cursor
            if self._vi_mode_active:
                self.editor.set_vi_mode(True)

    def _open_task_from_panel(self, path: str, line: int) -> None:
        self._select_tree_path(path)
        self._open_file(path)
        # Focus first, then go to line so the selection isn't cleared
        self.editor.setFocus()
        self._goto_line(line, select_line=True)
    
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
            self.toc_widget.set_base_path(display_path)
            self.editor.refresh_heading_outline()
        self.statusBar().showMessage(f"Editing (unsaved) {display_path}")
        self._update_window_title()
        
        # Update calendar to show this date
        self._update_calendar_for_journal_page(rel_path)
        
        # Refresh tree to show italicized entry
        self._populate_vault_tree()
    
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
    
    def _open_camel_link(self, name: str) -> None:
        """Open a link - handles both CamelCase (relative) and colon notation (absolute)."""
        if not self.current_path:
            self._alert("Open a page before following links.")
            return
        
        # Save current page before following link to ensure it's indexed
        self._save_current_file(auto=True)
        target_name, anchor = self._split_link_anchor(name)
        anchor_slug = self._anchor_slug(anchor)
        
        # Check if this is a colon notation link (PageA:PageB:PageC)
        if ":" in target_name:
            # Colon notation is absolute - convert directly to path
            target_file = colon_to_path(target_name, self.vault_root_name)
            if not target_file:
                self._alert(f"Invalid link format: {name}")
                return
            folder_path = self._file_path_to_folder(target_file)
            # Check if file already exists before creating
            file_existed = self.vault_root and Path(self.vault_root, target_file.lstrip("/")).exists()
            if not self._ensure_page_folder(folder_path, allow_existing=True):
                return
            # Apply template to newly created page
            is_new_page = not file_existed
            if is_new_page:
                page_name = target_name.split(":")[-1]  # Get last part for page name
                self._apply_new_page_template(target_file, page_name)
            self._pending_selection = target_file
            self._populate_vault_tree()
            self._open_file(target_file, cursor_at_end=is_new_page)
            self._scroll_to_anchor_slug(anchor_slug)
        else:
            # CamelCase link is relative to current page
            rel_current = Path(self.current_path.lstrip("/"))
            parent_folder = rel_current.parent
            target_rel = parent_folder / target_name if parent_folder.parts else Path(target_name)
            rel_string = target_rel.as_posix()
            folder_path = f"/{rel_string}" if rel_string else "/"
            target_file = self._folder_to_file_path(folder_path)
            if not target_file:
                return
            # Check if file already exists before creating
            file_existed = self.vault_root and Path(self.vault_root, target_file.lstrip("/")).exists()
            if not self._ensure_page_folder(folder_path, allow_existing=True):
                return
            # Apply template to newly created page
            is_new_page = not file_existed
            if is_new_page:
                self._apply_new_page_template(target_file, target_name)
            self._pending_selection = target_file
            self._populate_vault_tree()
            self._open_file(target_file, cursor_at_end=is_new_page)
            self._scroll_to_anchor_slug(anchor_slug)

    def _adjust_font_size(self, delta: int) -> None:
        new_size = max(10, min(36, self.font_size + delta))
        if new_size == self.font_size:
            return
        self.font_size = new_size
        self.editor.set_font_point_size(self.font_size)
        if config.has_active_vault():
            config.save_font_size(self.font_size)

    def _focus_editor_from_tree(self) -> None:
        self._focus_editor()

    def _focus_editor(self) -> None:
        self.editor.setFocus()

    # --- Focus toggle & visual indication ---------------------------
    def _toggle_focus_between_tree_and_editor(self) -> None:
        """Toggle focus between navigation tree and editor (Ctrl+Space)."""
        fw = self.focusWidget()
        if fw is self.editor or (self.editor and self.editor.isAncestorOf(fw)):
            # Go to tree
            self.tree_view.setFocus()
        else:
            # Go to editor
            self.editor.setFocus()
        self._apply_focus_borders()

    def _apply_focus_borders(self) -> None:
        """Apply a subtle border around the widget that currently has focus."""
        focused = self.focusWidget()
        editor_has = focused is self.editor or (self.editor and self.editor.isAncestorOf(focused))
        tree_has = focused is self.tree_view or self.tree_view.isAncestorOf(focused)
        # Styles: subtle 1px border with accent color; remove when unfocused
        editor_style = "QTextEdit { border: 1px solid #4A90E2; border-radius:3px; }" if editor_has else "QTextEdit { border: 1px solid transparent; }"
        tree_style = "QTreeView { border: 1px solid #4A90E2; border-radius:3px; }" if tree_has else "QTreeView { border: 1px solid transparent; }"
        # Preserve existing styles by appending (simple approach)
        self.editor.setStyleSheet(editor_style)
        self.tree_view.setStyleSheet(tree_style)

    def _goto_line(self, line: int, select_line: bool = False) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        if line > 1:
            cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, line - 1)
        if select_line:
            cursor.select(QTextCursor.LineUnderCursor)
        self.editor.setTextCursor(cursor)

    def _ensure_page_folder(self, folder_path: str, allow_existing: bool = False) -> bool:
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
        if folder_path == "/":
            self.statusBar().showMessage("Cannot delete the root page.", 4000)
            return
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("Delete")
        confirm.setText(f"Delete {folder_path}? This cannot be undone.")
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
        except httpx.HTTPError as exc:
            self._alert(f"Failed to delete {folder_path}: {exc}")
            return
        
        # Clean up database: delete all pages under this folder
        # folder_path is like "/PageA/PageB" (folder) not "/PageA/PageB/PageB.txt" (file)
        config.delete_folder_index(folder_path)
        
        # Clear editor if we just deleted the currently open page
        if self.current_path and open_path and self.current_path == open_path:
            self.current_path = None
            self.editor.set_markdown("")
        
        self._populate_vault_tree()
        self.right_panel.refresh_tasks()
        self.right_panel.refresh_calendar()

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
        if self.history_index <= 0:
            return
        self.history_index -= 1
        target_path = self.page_history[self.history_index]
        self._suspend_selection_open = True
        try:
            self._select_tree_path(target_path)
        finally:
            self._suspend_selection_open = False
        self._open_file(target_path, add_to_history=False)

    def _navigate_history_forward(self) -> None:
        """Navigate to next page in history (Alt+Right)."""
        if self.history_index >= len(self.page_history) - 1:
            return
        self.history_index += 1
        target_path = self.page_history[self.history_index]
        self._suspend_selection_open = True
        try:
            self._select_tree_path(target_path)
        finally:
            self._suspend_selection_open = False
        self._open_file(target_path, add_to_history=False)

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

        QTimer.singleShot(0, jump)

    def _toc_jump_to_position(self, position: int) -> None:
        cursor = self.editor.textCursor()
        cursor.setPosition(max(0, position))
        self.editor.setFocus()
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

    def _on_toc_collapsed_changed(self, collapsed: bool) -> None:
        config.save_toc_collapsed(collapsed)
        self._position_toc_widget()

    def _position_toc_widget(self) -> None:
        if not hasattr(self, "toc_widget") or not self.toc_widget:
            return
        viewport = self.editor.viewport()
        if viewport is None:
            return
        margin = 12
        width = self.toc_widget.width()
        x = max(margin, viewport.width() - width - margin)
        y = margin
        self.toc_widget.move(x, y)
        self.toc_widget.raise_()

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
            self._select_tree_path(parent_path)
            self._open_file(parent_path)
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
            self._select_tree_path(child_path)
            self._open_file(child_path)

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

    def _reindex_vault(self) -> None:
        if not self.vault_root or not config.has_active_vault():
            return
        root = Path(self.vault_root)
        for path in root.rglob(f"*{PAGE_SUFFIX}"):
            rel = f"/{path.relative_to(root).as_posix()}"
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            indexer.index_page(rel, content)
        self.right_panel.refresh_tasks()

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
        suffix = "ZimX Desktop"
        title = " | ".join(parts + [suffix]) if parts else suffix
        self.setWindowTitle(title)

    def _toggle_vi_mode(self) -> None:
        """Toggle vi-mode navigation on/off with visual status bar indicator."""
        self._vi_mode_active = not self._vi_mode_active
        self._apply_vi_mode_statusbar_style()

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
            if event.key() == Qt.Key_CapsLock:
                # Toggle vi-mode and consume the event to prevent CapsLock from activating
                self._toggle_vi_mode()
                # After Qt processes this, ensure OS CapsLock state is OFF
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._neutralize_capslock)
                return True  # Block the event completely
            if self._vi_mode_active:
                target = self._vi_mode_target_widget()
                if target:
                    mapping = self._translate_vi_key_event(event)
                    if mapping:
                        if self._vi_debug:
                            key_name = chr(event.key()) if Qt.Key_A <= event.key() <= Qt.Key_Z else f"Key_{event.key()}"
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
            # Also consume CapsLock release to fully prevent OS from toggling caps
            if event.key() == Qt.Key_CapsLock:
                return True
        return super().eventFilter(obj, event)

    def _neutralize_capslock(self) -> None:
        """Best-effort attempt to revert CapsLock state AFTER using it as an internal vi-mode toggle.

        Qt does not provide an API to directly change keyboard LED / lock states. We consume
        the CapsLock key events so the application treats the key purely as a mode toggle.
        However, the OS will typically still flip the hardware CapsLock state before delivery
        of the event. Fully preventing the change requires platform or driver level hooks not
        exposed by Qt.

        We implement a lightweight Windows-only fallback using the Win32 `keybd_event` (via
        ctypes) to send another CapsLock press/release if the key is left engaged. This will
        effectively toggle it back off. On Linux (X11/Wayland) there is no portable solution
        via Qt alone; XKB calls or compositor protocols would require extra native bindings.

        This function fails silently if anything is unsupported; vi-mode still works.
        """
        try:
            import sys
            # WINDOWS fallback: send synthetic CapsLock to undo state if engaged
            if sys.platform.startswith('win'):
                import ctypes
                user32 = ctypes.windll.user32
                VK_CAPITAL = 0x14
                KEYEVENTF_KEYUP = 0x0002
                state = user32.GetKeyState(VK_CAPITAL) & 0x0001
                if state:  # CapsLock is ON, send another press to turn OFF
                    user32.keybd_event(VK_CAPITAL, 0, 0, 0)
                    user32.keybd_event(VK_CAPITAL, 0, KEYEVENTF_KEYUP, 0)
            # Linux / others: no-op (documented limitation)
        except Exception:
            pass

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
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        alt = bool(event.modifiers() & Qt.AltModifier)

        # Allow Alt (for Alt+h / Alt+l mappings) plus Shift; block everything else
        disallowed = event.modifiers() & ~(Qt.ShiftModifier | Qt.KeypadModifier | Qt.AltModifier)
        if disallowed:
            return None

        target_key = None
        target_modifiers = Qt.KeyboardModifiers(Qt.NoModifier)

        if key == Qt.Key_J:
            target_key = Qt.Key_Down if not shift else Qt.Key_PageDown
        elif key == Qt.Key_K:
            target_key = Qt.Key_Up if not shift else Qt.Key_PageUp
        elif key == Qt.Key_N and shift:
            target_key = Qt.Key_Down
            target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_U and not shift:
            target_key = Qt.Key_Z
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_A:
            target_key = Qt.Key_Home
            if shift:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_H:
            if alt and not shift:
                # Alt+h -> Alt+Left (word-left style navigation)
                target_key = Qt.Key_Left
                target_modifiers = Qt.KeyboardModifiers(Qt.AltModifier)
            elif shift:
                target_key = Qt.Key_Home
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
            else:
                target_key = Qt.Key_Left
        elif key == Qt.Key_L and not shift:
            if alt:
                # Alt+l -> Alt+Right (word-right style navigation)
                target_key = Qt.Key_Right
                target_modifiers = Qt.KeyboardModifiers(Qt.AltModifier)
            else:
                target_key = Qt.Key_Right
        elif key == Qt.Key_Semicolon:
            target_key = Qt.Key_End
            if shift:
                target_modifiers = Qt.KeyboardModifiers(Qt.ShiftModifier)
        elif key == Qt.Key_X and not shift:
            target_key = Qt.Key_X
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_P and not shift:
            target_key = Qt.Key_V
            target_modifiers = Qt.KeyboardModifiers(Qt.ControlModifier)
        elif key == Qt.Key_D and not shift:
            # Delete current line: use custom handler with Alt+Delete as marker
            target_key = Qt.Key_Delete
            target_modifiers = Qt.KeyboardModifiers(Qt.AltModifier)

        if target_key is None:
            return None

        return target_key, target_modifiers

    def _dispatch_vi_navigation(self, target: QWidget, mapping: tuple[int, Qt.KeyboardModifiers]) -> None:
        key, modifiers = mapping

        # Special handling for delete line (d key maps to a special marker)
        if key == Qt.Key_Delete and modifiers == Qt.KeyboardModifiers(Qt.AltModifier):
            # This is our custom "delete line" command
            if hasattr(target, 'textCursor'):
                cursor = target.textCursor()
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                    target.setTextCursor(cursor)
                    return
                # Move to start of the current line
                cursor.movePosition(QTextCursor.StartOfLine)
                # Select to the start of the next line (includes newline)
                cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor)
                # If we're at the last line, make sure we still delete it
                if not cursor.hasSelection():
                    cursor.movePosition(QTextCursor.StartOfLine)
                    cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
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
        press_event = QKeyEvent(QEvent.KeyPress, key, modifiers)
        release_event = QKeyEvent(QEvent.KeyRelease, key, modifiers)
        QApplication.sendEvent(target, press_event)
        QApplication.sendEvent(target, release_event)

    def _apply_vi_mode_statusbar_style(self) -> None:
        # Switch editor cursor styling for vi-mode; no banners/overlays
        self.editor.set_vi_mode(self._vi_mode_active)

    # (Removed move/resize overlays; not used)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_toc_widget()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_current_file(auto=True)
        self.http.close()
        config.set_active_vault(None)
        return super().closeEvent(event)

    def _describe_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return "<invalid>"
        return (
            f"path={index.data(PATH_ROLE)}, open={index.data(OPEN_ROLE)}, is_dir={bool(index.data(TYPE_ROLE))}"
        )

    def _debug(self, message: str) -> None:
        print(f"[ZimX] {message}")
