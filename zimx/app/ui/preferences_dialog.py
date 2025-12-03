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
)

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

        self.vi_block_cursor_checkbox = QCheckBox("Use Vi Mode Block Cursor")
        self.vi_block_cursor_checkbox.setChecked(config.load_vi_block_cursor_enabled())
        self.vi_block_cursor_checkbox.setToolTip(
            "Show a colored block cursor when vi-mode is active.\n"
            "Disable this if you experience flickering on Linux/Cinnamon."
        )
        self.layout.addWidget(self.vi_block_cursor_checkbox)

        self.vi_strict_mode_checkbox = QCheckBox("Enable Strict Vi Mode")
        self.vi_strict_mode_checkbox.setChecked(config.load_vi_strict_mode_enabled())
        self.vi_strict_mode_checkbox.setToolTip(
            "When enabled, vi-mode uses separate navigation/insert states with vi-like edits\n"
            "(i/a/o/O to insert, x/s/r/d for deletes, u undo, . to repeat last edit)."
        )
        self.layout.addWidget(self.vi_strict_mode_checkbox)

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
    
    def _on_rebuild_clicked(self):
        """Handle rebuild index button click."""
        self.rebuildIndexRequested.emit()
        self.rebuild_button.setEnabled(False)
        self.rebuild_button.setText("Rebuilding...")
    
    def accept(self):
        """Save preferences when OK is clicked."""
        config.save_vi_block_cursor_enabled(self.vi_block_cursor_checkbox.isChecked())
        config.save_vi_strict_mode_enabled(self.vi_strict_mode_checkbox.isChecked())
        print(f"[DEBUG] Saving enable_ai_chats: {self.enable_ai_chats_checkbox.isChecked()}")
        config.save_enable_ai_chats(self.enable_ai_chats_checkbox.isChecked())
        config.save_default_ai_server(self.default_server_combo.currentText() or None)
        config.save_default_ai_model(self.default_model_combo.currentText() or None)
        config.save_vault_force_read_only(self.force_read_only_checkbox.isChecked())
        config.save_non_actionable_task_tags(self.non_actionable_tags_edit.text())
        super().accept()

    def _warn_restart_required(self) -> None:
        QMessageBox.information(self, "Restart Required", "This setting requires a restart.")
