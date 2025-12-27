from __future__ import annotations

from pathlib import Path
from typing import Optional

from zimx.app import config

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)


class NewPageDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New Page")
        self.setModal(True)
        self.resize(400, 150)
        
        layout = QVBoxLayout()
        form_layout = QFormLayout()
        
        # Page name input
        self.page_name_edit = QLineEdit()
        self.page_name_edit.setPlaceholderText("Enter page name...")
        form_layout.addRow("Page Name:", self.page_name_edit)
        
        # Template selection
        self.template_combo = QComboBox()
        self._load_templates()
        form_layout.addRow("Template:", self.template_combo)
        
        layout.addLayout(form_layout)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
        self.page_name_edit.setFocus()
    
    def _load_templates(self) -> None:
        """Load available templates from the templates folder."""
        builtin_dir = Path(__file__).parent.parent.parent / "templates"
        user_dir = Path.home() / ".zimx" / "templates"
        default_name = "Default"
        try:
            default_name = config.load_default_page_template()
        except Exception:
            default_name = "Default"
        default_index = 0
        
        seen = set()
        idx = 0
        for tpl_dir in (user_dir, builtin_dir):
            if tpl_dir.exists():
                for suffix in (".md", ".txt"):
                    for template_file in sorted(tpl_dir.glob(f"*{suffix}")):
                        template_name = template_file.stem
                        if template_name in seen:
                            continue
                        seen.add(template_name)
                        self.template_combo.addItem(template_name, str(template_file))
                        if template_name == default_name:
                            default_index = idx
                        idx += 1
        
        # If no templates found, add a "None" option
        if self.template_combo.count() == 0:
            self.template_combo.addItem("None", "")
        else:
            self.template_combo.setCurrentIndex(default_index)
    
    def get_page_name(self) -> str:
        """Return the entered page name."""
        return self.page_name_edit.text().strip()
    
    def get_template_path(self) -> Optional[str]:
        """Return the path to the selected template file."""
        return self.template_combo.currentData()
