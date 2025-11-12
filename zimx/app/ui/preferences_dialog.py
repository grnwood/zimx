from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox, QLabel

from zimx.app import config


class PreferencesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(400, 200)
        
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
        
        layout.addStretch(1)
        
        # OK/Cancel buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def accept(self):
        """Save preferences when OK is clicked."""
        config.save_vi_block_cursor_enabled(self.vi_block_cursor_checkbox.isChecked())
        super().accept()
