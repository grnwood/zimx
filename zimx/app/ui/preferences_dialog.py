from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox, 
    QLabel, QPushButton, QHBoxLayout
)

from zimx.app import config


class PreferencesDialog(QDialog):
    rebuildIndexRequested = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(450, 250)
        
        layout = QVBoxLayout(self)
        
        # Vi-mode block cursor setting
        vi_section = QLabel("<b>Vi Mode</b>")
        layout.addWidget(vi_section)
        
        self.vi_block_cursor_checkbox = QCheckBox("Use Vi Mode Block Cursor")
        self.vi_block_cursor_checkbox.setChecked(config.load_vi_block_cursor_enabled())
        self.vi_block_cursor_checkbox.setToolTip(
            "Show a colored block cursor when vi-mode is active.\n"
            "Disable this if you experience flickering on Linux/Cinnamon."
        )
        layout.addWidget(self.vi_block_cursor_checkbox)

        # Features
        layout.addSpacing(10)
        features_label = QLabel("<b>Features</b>")
        layout.addWidget(features_label)
        self.enable_ai_chats_checkbox = QCheckBox("Enable AI Chats")
        self.enable_ai_chats_checkbox.setChecked(config.load_enable_ai_chats())
        self.enable_ai_chats_checkbox.stateChanged.connect(self._warn_restart_required)
        layout.addWidget(self.enable_ai_chats_checkbox)
        
        layout.addSpacing(15)
        
        # Vault maintenance section
        vault_section = QLabel("<b>Vault Maintenance</b>")
        layout.addWidget(vault_section)
        
        rebuild_layout = QHBoxLayout()
        rebuild_label = QLabel("Rebuild search index:")
        rebuild_layout.addWidget(rebuild_label)
        
        self.rebuild_button = QPushButton("Rebuild Index")
        self.rebuild_button.setToolTip(
            "Rebuild the search index for all pages in the vault.\n"
            "Use this if search results are incorrect or after manual file changes."
        )
        self.rebuild_button.clicked.connect(self._on_rebuild_clicked)
        rebuild_layout.addWidget(self.rebuild_button)
        rebuild_layout.addStretch(1)
        
        layout.addLayout(rebuild_layout)
        
        layout.addStretch(1)
        
        # OK/Cancel buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def _on_rebuild_clicked(self):
        """Handle rebuild index button click."""
        self.rebuildIndexRequested.emit()
        self.rebuild_button.setEnabled(False)
        self.rebuild_button.setText("Rebuilding...")
    
    def accept(self):
        """Save preferences when OK is clicked."""
        config.save_vi_block_cursor_enabled(self.vi_block_cursor_checkbox.isChecked())
        config.save_enable_ai_chats(self.enable_ai_chats_checkbox.isChecked())
        super().accept()

    def _warn_restart_required(self) -> None:
        QMessageBox.information(self, "Restart Required", "This setting requires a restart.")
