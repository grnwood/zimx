from __future__ import annotations

from pathlib import Path
from typing import Optional
import os
import time

from PySide6.QtCore import Qt, QUrl, QSize, QMimeData
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices, QDrag
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QFileIconProvider,
)

from zimx.app import config

_DETAILED_LOGGING = os.getenv("ZIMX_DETAILED_LOGGING", "0") not in ("0", "false", "False", "", None)
_PAGE_LOGGING = os.getenv("ZIMX_DETAILED_PAGE_LOGGING", "0") not in ("0", "false", "False", "", None)


class AttachmentsListWidget(QListWidget):
    """Custom list widget that provides proper drag data for file attachments."""
    
    def mimeData(self, items):
        """Create mime data with file URLs for dragging."""
        mime = QMimeData()
        urls = []
        
        for item in items:
            file_path_str = item.data(Qt.UserRole)
            if file_path_str:
                url = QUrl.fromLocalFile(file_path_str)
                urls.append(url)
        
        if urls:
            mime.setUrls(urls)
        
        return mime


class AttachmentsPanel(QWidget):
    """Panel showing attachments (files) in the current page's folder."""
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        
        self.current_page_path: Optional[Path] = None
        self.zoom_level = 0  # 0=list, 1=small icons, 2=medium icons, 3=large icons
        self.icon_provider = QFileIconProvider()
        
        # Create toolbar with folder, refresh, and zoom buttons
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(5, 5, 5, 5)
        
        self.open_folder_button = QToolButton()
        self.open_folder_button.setText("📁")  # Folder icon
        self.open_folder_button.setToolTip("Open folder in file manager")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_folder_button.setEnabled(False)
        toolbar.addWidget(self.open_folder_button)
        
        self.refresh_button = QToolButton()
        self.refresh_button.setText("🔄")  # Refresh icon
        self.refresh_button.setToolTip("Refresh attachments list")
        self.refresh_button.clicked.connect(self._refresh_attachments)
        self.refresh_button.setEnabled(False)
        toolbar.addWidget(self.refresh_button)
        
        toolbar.addStretch()
        
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("−")  # Minus
        self.zoom_out_button.setToolTip("Zoom out (smaller icons)")
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.zoom_out_button.setEnabled(True)
        toolbar.addWidget(self.zoom_out_button)
        
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")  # Plus
        self.zoom_in_button.setToolTip("Zoom in (larger icons)")
        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.zoom_in_button.setEnabled(True)
        toolbar.addWidget(self.zoom_in_button)
        
        # Create list widget for attachments
        self.attachments_list = AttachmentsListWidget()
        self.attachments_list.itemDoubleClicked.connect(self._open_attachment)
        self.attachments_list.setDragEnabled(True)
        self.attachments_list.setDragDropMode(QListWidget.DragOnly)
        
        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.attachments_list)
        self.setLayout(layout)
    
    def set_page(self, page_path: Optional[Path]) -> None:
        """Set the current page and update the attachments list."""
        t0 = time.perf_counter()
        self.current_page_path = page_path
        self._refresh_attachments()
        if _PAGE_LOGGING:
            print(f"[PageLoadAndRender] attachments set_page elapsed={(time.perf_counter()-t0)*1000:.1f}ms")
    
    def _refresh_attachments(self) -> None:
        """Refresh the list of attachments for the current page."""
        t0 = time.perf_counter()
        self.attachments_list.clear()
        
        if not self.current_page_path:
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            if _PAGE_LOGGING:
                print(f"[PageLoadAndRender] attachments refresh skipped (no page) elapsed={(time.perf_counter()-t0)*1000:.1f}ms")
            return
        
        # Get the folder for this page
        # For a page like "/vault/Notes/MyPage/MyPage.txt", the folder is "/vault/Notes/MyPage/"
        # Images are stored in the same folder as the page text file
        page_folder = self.current_page_path.parent
        
        if _DETAILED_LOGGING:
            print(f"[Attachments] current_page_path: {self.current_page_path}")
            print(f"[Attachments] page_folder: {page_folder}")
            print(f"[Attachments] page_folder exists: {page_folder.exists()}")
            print(f"[Attachments] page_folder is_dir: {page_folder.is_dir()}")
        
        if not page_folder.exists() or not page_folder.is_dir():
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            return
        
        self.open_folder_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        
        # Update view mode based on zoom level
        self._update_view_mode()
        
        # List all files in the folder (excluding the page text file itself)
        try:
            files = sorted(page_folder.iterdir(), key=lambda p: p.name.lower())
            t_list = time.perf_counter()
            if _DETAILED_LOGGING:
                print(f"[Attachments] Found {len(files)} files in folder")
            for file_path in files:
                if _DETAILED_LOGGING:
                    print(f"[Attachments] Checking file: {file_path}")
                if file_path.is_file() and file_path != self.current_page_path:
                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, str(file_path))
                    
                    # Set icon based on view mode
                    if self.zoom_level == 0:
                        # List view with emoji icons
                        if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg']:
                            item.setText(f"🖼️ {file_path.name}")
                        elif file_path.suffix.lower() in ['.pdf']:
                            item.setText(f"📄 {file_path.name}")
                        elif file_path.suffix.lower() in ['.txt', '.md']:
                            item.setText(f"📝 {file_path.name}")
                        else:
                            item.setText(f"📎 {file_path.name}")
                    else:
                        # Icon view
                        item.setText(file_path.name)
                        icon = self._get_file_icon(file_path)
                        if icon:
                            item.setIcon(icon)
                    
                    self.attachments_list.addItem(item)
            if _PAGE_LOGGING:
                print(
                    f"[PageLoadAndRender] attachments refresh listed={len(files)} elapsed={(time.perf_counter()-t_list)*1000:.1f}ms total={(time.perf_counter()-t0)*1000:.1f}ms"
                )
        except (OSError, PermissionError):
            pass
        else:
            if _PAGE_LOGGING:
                print(f"[PageLoadAndRender] attachments refresh total={(time.perf_counter()-t0)*1000:.1f}ms")
    
    def _get_file_icon(self, file_path: Path) -> QIcon:
        """Get an icon for a file - thumbnail for images, OS icon for others."""
        if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            # Create thumbnail for image files
            load_t0 = time.perf_counter()
            pixmap = QPixmap(str(file_path))
            if not pixmap.isNull():
                # Scale based on zoom level
                if self.zoom_level == 1:  # Small
                    size = 48
                elif self.zoom_level == 2:  # Medium
                    size = 96
                else:  # Large
                    size = 144
                
                scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if _PAGE_LOGGING:
                    print(f"[PageLoadAndRender] attachments thumbnail {file_path.name} load+scale={(time.perf_counter()-load_t0)*1000:.1f}ms")
                return QIcon(scaled)
        
        # Use OS file icon for non-images or if thumbnail failed
        from PySide6.QtCore import QFileInfo
        return self.icon_provider.icon(QFileInfo(str(file_path)))
    
    def _update_view_mode(self) -> None:
        """Update the list widget view mode based on zoom level."""
        if self.zoom_level == 0:
            # List view
            self.attachments_list.setViewMode(QListWidget.ListMode)
            self.attachments_list.setIconSize(QSize(16, 16))
            self.attachments_list.setGridSize(QSize())
        else:
            # Icon view
            self.attachments_list.setViewMode(QListWidget.IconMode)
            self.attachments_list.setResizeMode(QListWidget.Adjust)
            self.attachments_list.setSpacing(10)
            
            if self.zoom_level == 1:  # Small
                self.attachments_list.setIconSize(QSize(48, 48))
                self.attachments_list.setGridSize(QSize(80, 80))
            elif self.zoom_level == 2:  # Medium
                self.attachments_list.setIconSize(QSize(96, 96))
                self.attachments_list.setGridSize(QSize(128, 128))
            else:  # Large
                self.attachments_list.setIconSize(QSize(144, 144))
                self.attachments_list.setGridSize(QSize(176, 176))
    
    def _open_folder(self) -> None:
        """Open the attachments folder in the system file manager."""
        if not self.current_page_path:
            return
        
        page_folder = self.current_page_path.parent
        
        if page_folder.exists() and page_folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(page_folder)))
    
    def _zoom_in(self) -> None:
        """Zoom in to show larger icons."""
        if self.zoom_level < 3:
            self.zoom_level += 1
            self._refresh_attachments()
    
    def _zoom_out(self) -> None:
        """Zoom out to show smaller icons or list view."""
        if self.zoom_level > 0:
            self.zoom_level -= 1
            self._refresh_attachments()
    
    def _open_attachment(self, item: QListWidgetItem) -> None:
        """Open the selected attachment with the default system handler."""
        file_path_str = item.data(Qt.UserRole)
        if file_path_str:
            file_path = Path(file_path_str)
            if file_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))
    
    def refresh(self) -> None:
        """Refresh the attachments list."""
        self._refresh_attachments()
