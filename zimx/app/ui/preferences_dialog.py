from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QCheckBox,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QMessageBox,
    QComboBox,
    QLineEdit,
    QSpinBox,
)
from pathlib import Path
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from zimx.app import config


class PreferencesDialog(QDialog):
    rebuildIndexRequested = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(450, 250)
        
        self.layout = QVBoxLayout(self)
        
        # Vi-mode block cursor setting
        vi_section = QLabel("<b>Vi Mode</b>")
        self.layout.addWidget(vi_section)

        self.vi_enable_checkbox = QCheckBox("Enable Vi Mode")
        self.vi_enable_checkbox.setChecked(config.load_vi_mode_enabled())
        self.vi_enable_checkbox.setToolTip(
            "Turn on vi-style navigation keys in the Markdown editor."
        )
        self.layout.addWidget(self.vi_enable_checkbox)
        
        self.vi_block_cursor_checkbox = QCheckBox("Use Vi Mode Block Cursor")
        self.vi_block_cursor_checkbox.setChecked(config.load_vi_block_cursor_enabled())
        self.vi_block_cursor_checkbox.setToolTip(
            "Show a colored block cursor when vi-mode is active.\n"
            "Disable this if you experience flickering on Linux/Cinnamon."
        )
        self.layout.addWidget(self.vi_block_cursor_checkbox)

        # Non Actionable Task Tags
        self.layout.addSpacing(10)
        non_actionable_label = QLabel("<b>Non Actionable Task Tags</b>")
        self.layout.addWidget(non_actionable_label)
        self.non_actionable_tags_edit = QLineEdit()
        self.non_actionable_tags_edit.setPlaceholderText("@wait @wt")
        try:
            val = config.load_non_actionable_task_tags()
        except Exception:
            val = None
        self.non_actionable_tags_edit.setText(val or "@wait @wt")
        self.layout.addWidget(self.non_actionable_tags_edit)
        # Features
        self.layout.addSpacing(10)
        features_label = QLabel("<b>Features</b>")
        self.layout.addWidget(features_label)
        fonts_label = QLabel("<b>Fonts</b>")
        self.layout.addWidget(fonts_label)
        self._font_families = sorted(QFontDatabase().families())

        row_fonts_app = QHBoxLayout()
        row_fonts_app.addWidget(QLabel("Application font:"))
        self.application_font_combo = self._build_font_combo("System Default")
        try:
            app_font = config.load_application_font()
        except Exception:
            app_font = None
        self._select_font(self.application_font_combo, app_font)
        self.application_font_combo.currentIndexChanged.connect(self._warn_restart_required)
        row_fonts_app.addWidget(self.application_font_combo, 1)
        self.layout.addLayout(row_fonts_app)

        row_fonts_size = QHBoxLayout()
        row_fonts_size.addWidget(QLabel("Application font size:"))
        self.application_font_size_spin = QSpinBox()
        self.application_font_size_spin.setRange(0, 72)
        try:
            size_val = config.load_application_font_size()
        except Exception:
            size_val = None
        default_size = QApplication.instance().font().pointSize() if QApplication.instance() else 12
        self.application_font_size_spin.setValue(size_val or max(6, default_size))
        self.application_font_size_spin.setToolTip("Set 0 to use system default size.")
        self.application_font_size_spin.editingFinished.connect(self._warn_restart_required)
        row_fonts_size.addWidget(self.application_font_size_spin, 1)
        self.layout.addLayout(row_fonts_size)

        row_fonts_md = QHBoxLayout()
        row_fonts_md.addWidget(QLabel("Default Markdown font:"))
        self.markdown_font_combo = self._build_font_combo("Editor default")
        try:
            md_font = config.load_default_markdown_font()
        except Exception:
            md_font = None
        self._select_font(self.markdown_font_combo, md_font)
        self.markdown_font_combo.currentIndexChanged.connect(self._warn_restart_required)
        row_fonts_md.addWidget(self.markdown_font_combo, 1)
        self.layout.addLayout(row_fonts_md)

        self.layout.addSpacing(6)
        self.minimal_font_scan_checkbox = QCheckBox("Use Minimal Font Scan (For Fast Window Startup)")
        try:
            self.minimal_font_scan_checkbox.setChecked(config.load_minimal_font_scan_enabled())
        except Exception:
            self.minimal_font_scan_checkbox.setChecked(True)
        self.minimal_font_scan_checkbox.setToolTip(
            "Limit Qt to a tiny font set to reduce startup time. Requires restart to take effect."
        )
        self.minimal_font_scan_checkbox.stateChanged.connect(self._warn_restart_required)
        self.layout.addWidget(self.minimal_font_scan_checkbox)
        self.enable_ai_chats_checkbox = QCheckBox("Enable AI Chats")
        self.enable_ai_chats_checkbox.setChecked(config.load_enable_ai_chats())
        self.enable_ai_chats_checkbox.stateChanged.connect(self._warn_restart_required)
        self.layout.addWidget(self.enable_ai_chats_checkbox)

        # Manage Server button (after Enable AI Chats)
        self.manage_server_btn = QPushButton("Manage Servers")
        self.manage_server_btn.clicked.connect(self._open_manage_server_dialog)
        self.layout.addWidget(self.manage_server_btn)

        # Default server/model section
        defaults_label = QLabel("<b>Default Server and Model</b>")
        self.layout.addWidget(defaults_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("Server:"))
        self.default_server_combo = QComboBox()
        self.default_server_combo.currentIndexChanged.connect(self._on_default_server_changed)
        row.addWidget(self.default_server_combo, 1)
        self.layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Model:"))
        self.default_model_combo = QComboBox()
        row2.addWidget(self.default_model_combo, 1)
        self.layout.addLayout(row2)

        self._load_default_server_model()

        # Code highlighting
        self.layout.addSpacing(10)
        code_label = QLabel("<b>Code Highlighting</b>")
        self.layout.addWidget(code_label)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Pygments style:"))
        self.pygments_style_combo = QComboBox()
        self._load_pygments_styles()
        row3.addWidget(self.pygments_style_combo, 1)
        self.layout.addLayout(row3)

        # Template defaults
        self.layout.addSpacing(10)
        template_label = QLabel("<b>Templates</b>")
        self.layout.addWidget(template_label)
        template_names = self._template_names()
        row_tpl_page = QHBoxLayout()
        row_tpl_page.addWidget(QLabel("Default Template for New Page:"))
        self.page_template_combo = QComboBox()
        self.page_template_combo.addItems(template_names)
        try:
            current_page_tpl = config.load_default_page_template()
        except Exception:
            current_page_tpl = "Default"
        if current_page_tpl in template_names:
            self.page_template_combo.setCurrentText(current_page_tpl)
        row_tpl_page.addWidget(self.page_template_combo, 1)
        self.layout.addLayout(row_tpl_page)

        row_tpl_journal = QHBoxLayout()
        row_tpl_journal.addWidget(QLabel("Default Template for New Journal Entry:"))
        self.journal_template_combo = QComboBox()
        self.journal_template_combo.addItems(template_names)
        try:
            current_journal_tpl = config.load_default_journal_template()
        except Exception:
            current_journal_tpl = "JournalDay"
        if current_journal_tpl in template_names:
            self.journal_template_combo.setCurrentText(current_journal_tpl)
        row_tpl_journal.addWidget(self.journal_template_combo, 1)
        self.layout.addLayout(row_tpl_journal)

        # Vault behavior
        vault_label = QLabel("<b>Vault</b>")
        self.layout.addWidget(vault_label)
        self.force_read_only_checkbox = QCheckBox("Force read-only mode for this vault")
        self.force_read_only_checkbox.setToolTip(
            "Open this vault without taking a lock or allowing writes from this window."
        )
        try:
            self.force_read_only_checkbox.setChecked(config.load_vault_force_read_only())
        except Exception:
            self.force_read_only_checkbox.setChecked(False)
        self.layout.addWidget(self.force_read_only_checkbox)

        # Link update handling
        link_label = QLabel("Link Update Handling:")
        row_link = QHBoxLayout()
        row_link.addWidget(link_label)
        self.link_update_combo = QComboBox()
        self.link_update_combo.addItems([
            "None (do nothing)",
            "Lazy (rewrite on open/save)",
            "Reindex (background)",
        ])
        mode = config.load_link_update_mode()
        mode_to_index = {"none": 0, "lazy": 1, "reindex": 2}
        self.link_update_combo.setCurrentIndex(mode_to_index.get(mode, 2))
        row_link.addWidget(self.link_update_combo, 1)
        self.layout.addLayout(row_link)
        self.update_links_on_index_checkbox = QCheckBox("Update vault page links on reindex")
        try:
            self.update_links_on_index_checkbox.setChecked(config.load_update_links_on_index())
        except Exception:
            self.update_links_on_index_checkbox.setChecked(True)
        self.layout.addWidget(self.update_links_on_index_checkbox)

        # Dialog buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        self.layout.addWidget(btn_box)

    def _open_manage_server_dialog(self):
        # Prevent duplicate Manage Server buttons by not adding UI elements here
        from zimx.app.ui.ai_chat_panel import ServerManager, ServerConfigDialog
        from PySide6.QtWidgets import QDialog, QComboBox, QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QWidget
        from PySide6.QtCore import Qt

        # Create dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Manage Servers")
        dlg.setModal(True)
        dlg.resize(480, 220)

        layout = QVBoxLayout(dlg)
        row = QHBoxLayout()
        label = QLabel("Server:")
        row.addWidget(label)
        server_manager = ServerManager()
        servers = server_manager.load_servers()
        server_names = [s["name"] for s in servers]
        combo = QComboBox()
        combo.addItems(server_names + ["Add New..."])
        row.addWidget(combo, 1)
        edit_btn = QPushButton("Edit")
        row.addWidget(edit_btn)
        add_btn = QPushButton("Add New")
        row.addWidget(add_btn)
        layout.addLayout(row)

        # Info label
        info_label = QLabel("")
        layout.addWidget(info_label)

        def open_server_dialog(existing=None):
            dialog = ServerConfigDialog(dlg, existing, existing_names=server_manager.list_server_names())
            if dialog.exec() == QDialog.Accepted and dialog.result:
                try:
                    new_server = server_manager.add_or_update_server(dialog.result)
                    # Refresh combo
                    combo.clear()
                    servers2 = server_manager.load_servers()
                    combo.addItems([s["name"] for s in servers2] + ["Add New..."])
                    combo.setCurrentText(new_server["name"])
                    info_label.setText(f"Saved server: {new_server['name']}")
                except Exception as exc:
                    info_label.setText(f"Error: {exc}")

        def on_combo_changed(idx):
            if combo.currentText() == "Add New...":
                open_server_dialog(None)
        combo.currentIndexChanged.connect(on_combo_changed)

        def on_edit():
            name = combo.currentText()
            if name == "Add New...":
                open_server_dialog(None)
            else:
                server = server_manager.get_server(name)
                open_server_dialog(server)
        edit_btn.clicked.connect(on_edit)
        add_btn.clicked.connect(lambda: open_server_dialog(None))

        # OK/Cancel
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        dlg.exec()

    def _load_default_server_model(self):
        """Populate default server/model dropdowns based on configured servers."""
        try:
            from zimx.app.ui.ai_chat_panel import ServerManager, get_available_models

            mgr = ServerManager()
            servers = mgr.load_servers()
            names = [srv["name"] for srv in servers]
            self.default_server_combo.clear()
            self.default_server_combo.addItems(names)
            desired_server = config.load_default_ai_server()
            if desired_server and desired_server in names:
                self.default_server_combo.setCurrentText(desired_server)
            elif names:
                self.default_server_combo.setCurrentIndex(0)
            self._refresh_default_models(mgr)
        except Exception:
            self.default_server_combo.clear()
            self.default_model_combo.clear()

    def _refresh_default_models(self, mgr=None):
        try:
            from zimx.app.ui.ai_chat_panel import ServerManager, get_available_models

            manager = mgr or ServerManager()
            server = manager.get_server(self.default_server_combo.currentText())
            models = get_available_models(server)
            self.default_model_combo.clear()
            self.default_model_combo.addItems(models)
            desired_model = config.load_default_ai_model()
            if desired_model and desired_model in models:
                self.default_model_combo.setCurrentText(desired_model)
            elif models:
                self.default_model_combo.setCurrentIndex(0)
        except Exception:
            self.default_model_combo.clear()

    def _on_default_server_changed(self):
        self._refresh_default_models()

    def _load_pygments_styles(self) -> None:
        styles = ["monokai"]
        try:
            from pygments.styles import get_all_styles

            styles = sorted(set(get_all_styles())) or styles
        except Exception:
            pass
        current = config.load_pygments_style("monokai")
        self.pygments_style_combo.clear()
        self.pygments_style_combo.addItems(styles)
        if current in styles:
            self.pygments_style_combo.setCurrentText(current)
    
    def _on_rebuild_clicked(self):
        """Handle rebuild index button click."""
        self.rebuildIndexRequested.emit()
        self.rebuild_button.setEnabled(False)
        self.rebuild_button.setText("Rebuilding...")
    
    def accept(self):
        """Save preferences when OK is clicked."""
        config.save_vi_mode_enabled(self.vi_enable_checkbox.isChecked())
        config.save_vi_block_cursor_enabled(self.vi_block_cursor_checkbox.isChecked())
        app_font = self._font_value(self.application_font_combo)
        config.save_application_font(app_font)
        size_val = self.application_font_size_spin.value()
        config.save_application_font_size(size_val if size_val > 0 else None)
        md_font = self._font_value(self.markdown_font_combo)
        config.save_default_markdown_font(md_font)
        config.save_minimal_font_scan_enabled(self.minimal_font_scan_checkbox.isChecked())
        print(f"[DEBUG] Saving enable_ai_chats: {self.enable_ai_chats_checkbox.isChecked()}")
        config.save_enable_ai_chats(self.enable_ai_chats_checkbox.isChecked())
        config.save_default_ai_server(self.default_server_combo.currentText() or None)
        config.save_default_ai_model(self.default_model_combo.currentText() or None)
        config.save_vault_force_read_only(self.force_read_only_checkbox.isChecked())
        config.save_non_actionable_task_tags(self.non_actionable_tags_edit.text())
        try:
            config.save_default_page_template(self.page_template_combo.currentText() or "Default")
            config.save_default_journal_template(self.journal_template_combo.currentText() or "JournalDay")
        except Exception:
            pass
        try:
            config.save_pygments_style(self.pygments_style_combo.currentText() or "monokai")
        except Exception:
            pass
        try:
            index_to_mode = {0: "none", 1: "lazy", 2: "reindex"}
            config.save_link_update_mode(index_to_mode.get(self.link_update_combo.currentIndex(), "reindex"))
        except Exception:
            pass
        try:
            config.save_update_links_on_index(self.update_links_on_index_checkbox.isChecked())
        except Exception:
            pass
        super().accept()

    def _warn_restart_required(self) -> None:
        QMessageBox.information(self, "Restart Required", "This setting requires a restart.")

    def _build_font_combo(self, default_label: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem(default_label, "")
        for family in self._font_families:
            combo.addItem(family, family)
        combo.setInsertPolicy(QComboBox.NoInsert)
        return combo
    
    def _template_names(self) -> list[str]:
        """Return available template names (stems) from built-in and user templates."""
        names: list[str] = []
        builtin_dir = Path(__file__).parent.parent.parent / "templates"
        user_dir = Path.home() / ".zimx" / "templates"
        for tpl_dir in (builtin_dir, user_dir):
            if tpl_dir.exists():
                for tpl in sorted(tpl_dir.glob("*.txt")):
                    names.append(tpl.stem)
        # Preserve order but drop duplicates
        seen = set()
        unique = []
        for n in names:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        return unique or ["Default"]

    def _select_font(self, combo: QComboBox, family: str | None) -> None:
        if not family:
            combo.setCurrentIndex(0)
            return
        idx = combo.findData(family)
        if idx == -1:
            combo.addItem(family, family)
            idx = combo.findData(family)
        combo.setCurrentIndex(max(0, idx))

    def _font_value(self, combo: QComboBox) -> str | None:
        value = combo.currentData()
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Fall back to text if user typed a custom font
        text = combo.currentText().strip()
        return text or None
