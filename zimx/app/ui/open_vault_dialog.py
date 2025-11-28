from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from zimx.app import config


class AddVaultDialog(QDialog):
    """Dialog for capturing a vault name and folder path."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: Optional[dict[str, str]] = None
        self.setWindowTitle("Add Vault")
        self.setModal(True)
        self.resize(420, 180)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My Notes")
        form.addRow("Vault Name:", self.name_edit)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_btn)
        path_container = QWidget()
        path_container.setLayout(path_row)
        form.addRow("Vault Folder:", path_container)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Vault Folder", str(Path.home()))
        if directory:
            self.path_edit.setText(directory)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(directory).name)

    def accept(self) -> None:  # type: ignore[override]
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        if not name or not path:
            QMessageBox.warning(self, "Missing Info", "Please provide both a vault name and folder.")
            return
        path_obj = Path(path)
        if not path_obj.exists() or not path_obj.is_dir():
            QMessageBox.warning(self, "Folder Not Found", "Please choose an existing vault folder.")
            return
        self._result = {"name": name, "path": path}
        super().accept()

    def selected_vault(self) -> Optional[dict[str, str]]:
        return self._result


class OpenVaultDialog(QDialog):
    """Dialog for selecting, adding, and managing vaults."""

    def __init__(self, parent=None, current_vault: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Vault")
        self.setModal(True)
        self.resize(520, 520)

        self.vaults: list[dict[str, str]] = config.load_known_vaults()
        if not self.vaults and current_vault:
            self.vaults.append({"name": Path(current_vault).name, "path": current_vault})
        self.default_vault: Optional[str] = config.load_default_vault()
        self._selected: Optional[dict[str, str]] = None

        layout = QVBoxLayout(self)
        intro_row = QHBoxLayout()
        icon_label = QLabel()
        icon = QApplication.instance().windowIcon() if QApplication.instance() else None
        if icon:
            pixmap = icon.pixmap(48, 48)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap)
                icon_label.setAlignment(Qt.AlignTop)
                intro_row.addWidget(icon_label)
        intro = QLabel("Choose a vault to open. Double-click an entry to launch it immediately.")
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        layout.addLayout(intro_row)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._accept_current)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

        controls = QHBoxLayout()
        self.add_btn = QPushButton("Add Vault")
        self.add_btn.clicked.connect(self._add_vault)
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self._remove_selected)
        controls.addWidget(self.add_btn)
        controls.addWidget(self.remove_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("Default vault:"))
        self.default_combo = QComboBox()
        self.default_combo.currentIndexChanged.connect(self._on_default_changed)
        default_row.addWidget(self.default_combo, 1)
        layout.addLayout(default_row)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._accept_current)
        self.button_box.rejected.connect(self.reject)
        open_new_btn = self.button_box.addButton("Open in New Window", QDialogButtonBox.ActionRole)
        open_new_btn.clicked.connect(self._accept_new_window)
        layout.addWidget(self.button_box)

        self._refresh_list(select_path=current_vault or self.default_vault)

    def selected_vault(self) -> Optional[dict[str, str]]:
        return self._selected

    def selected_vault_new_window(self) -> Optional[dict[str, str]]:
        if getattr(self, "_open_new_window", False):
            return self._selected
        return None

    def _refresh_list(self, select_path: Optional[str] = None) -> None:
        self.list_widget.clear()
        for vault in self.vaults:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, vault)
            widget = self._build_item_widget(vault)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        if self.vaults:
            target_path = select_path or self.vaults[0]["path"]
            for idx in range(self.list_widget.count()):
                item = self.list_widget.item(idx)
                data = item.data(Qt.UserRole)
                if data and data.get("path") == target_path:
                    self.list_widget.setCurrentItem(item)
                    break
        self._refresh_default_combo()
        self._update_buttons()

    def _refresh_default_combo(self) -> None:
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("No default", None)
        for vault in self.vaults:
            self.default_combo.addItem(vault["name"], vault["path"])
        idx = self.default_combo.findData(self.default_vault)
        if idx != -1:
            self.default_combo.setCurrentIndex(idx)
        else:
            if self.default_vault is not None:
                config.save_default_vault(None)
            self.default_vault = None
            self.default_combo.setCurrentIndex(0)
        self.default_combo.blockSignals(False)

    def _build_item_widget(self, vault: dict[str, str]) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        name_label = QLabel(vault.get("name") or Path(vault["path"]).name)
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        layout.addWidget(name_label)

        path_label = QLabel(vault["path"])
        path_label.setWordWrap(True)
        path_font = path_label.font()
        path_font.setPointSize(max(path_font.pointSize() - 2, 8))
        path_label.setFont(path_font)
        path_label.setStyleSheet("color: #666;")
        layout.addWidget(path_label)

        return container

    def _on_selection_changed(self, current, previous) -> None:  # noqa: ARG002
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self.list_widget.currentItem() is not None
        self.remove_btn.setEnabled(has_selection)
        ok_button = self.button_box.button(QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setEnabled(has_selection)

    def _accept_current(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault:
            return
        self._selected = {"name": vault.get("name") or Path(vault["path"]).name, "path": vault["path"]}
        self._open_new_window = False
        self.accept()

    def _accept_new_window(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault:
            return
        self._selected = {"name": vault.get("name") or Path(vault["path"]).name, "path": vault["path"]}
        self._open_new_window = True
        self.accept()

    def _add_vault(self) -> None:
        dlg = AddVaultDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        result = dlg.selected_vault()
        if not result:
            return
        self.vaults = [v for v in self.vaults if v.get("path") != result["path"]]
        self.vaults.insert(0, result)
        config.remember_vault(result["path"], result["name"])
        self._refresh_list(select_path=result["path"])

    def _remove_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault:
            return
        path = vault.get("path")
        self.vaults = [v for v in self.vaults if v.get("path") != path]
        if path:
            config.delete_known_vault(path)
            if self.default_vault == path:
                self.default_vault = None
                config.save_default_vault(None)
        next_selection = self.vaults[0]["path"] if self.vaults else None
        self._refresh_list(select_path=next_selection)

    def _on_default_changed(self, index: int) -> None:
        path = self.default_combo.itemData(index)
        self.default_vault = path
        config.save_default_vault(path)
