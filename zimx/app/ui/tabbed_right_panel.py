from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QWidget

from .task_panel import TaskPanel
from .attachments_panel import AttachmentsPanel
from .link_navigator_panel import LinkNavigatorPanel


class TabbedRightPanel(QWidget):
    """Tabbed panel containing Tasks and Attachments views."""
    
    # Forward signals from child panels
    taskActivated = Signal(str, int)  # path, line (from TaskPanel)
    dateActivated = Signal(int, int, int)  # year, month, day (from TaskPanel calendar)
    linkActivated = Signal(str)  # page path from Link Navigator
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        
        # Create tab widget
        self.tabs = QTabWidget()
        
        # Create Tasks tab (now includes calendar)
        self.task_panel = TaskPanel()
        self.tabs.addTab(self.task_panel, "Tasks")
        
        # Create Attachments tab
        self.attachments_panel = AttachmentsPanel()
        self.tabs.addTab(self.attachments_panel, "Attachments")

        # Create Link Navigator tab
        self.link_panel = LinkNavigatorPanel()
        self.tabs.addTab(self.link_panel, "Link Navigator")
        
        # Set Tasks as default tab (index 0)
        self.tabs.setCurrentIndex(0)
        
        # Forward signals
        self.task_panel.taskActivated.connect(self.taskActivated)
        self.task_panel.dateActivated.connect(self.dateActivated)
        self.link_panel.pageActivated.connect(self.linkActivated)
        
        # Layout
        from PySide6.QtWidgets import QVBoxLayout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.setLayout(layout)
    
    def refresh_tasks(self) -> None:
        """Refresh the task panel."""
        self.task_panel.refresh()
    
    def clear_tasks(self) -> None:
        """Clear the task panel."""
        self.task_panel.clear()
    
    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Set vault root for calendar in task panel."""
        if vault_root:
            self.task_panel.set_vault_root(vault_root)
    
    def refresh_calendar(self) -> None:
        """Refresh the calendar to update bold dates."""
        self.task_panel._update_calendar_dates()
    
    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Set the calendar to show a specific date."""
        from PySide6.QtCore import QDate
        date = QDate(year, month, day)
        self.task_panel.calendar.setSelectedDate(date)
    
    def set_current_page(self, page_path, relative_path=None) -> None:
        """Update panels with the current page."""
        self.attachments_panel.set_page(page_path)
        self.link_panel.set_page(relative_path)
        self._update_attachments_tab_label()
    
    def refresh_attachments(self) -> None:
        """Refresh the attachments panel."""
        self.attachments_panel.refresh()
        self._update_attachments_tab_label()

    def refresh_links(self, page_path=None) -> None:
        """Refresh the link navigator for the given page (or current)."""
        self.link_panel.refresh(page_path)

    def focus_link_tab(self, page_path=None) -> None:
        """Switch to the Link Navigator tab and optionally set its page."""
        if page_path is not None:
            self.link_panel.set_page(page_path)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.link_panel:
                self.tabs.setCurrentIndex(i)
                break
    
    def _update_attachments_tab_label(self) -> None:
        """Update the Attachments tab label with the count of attachments."""
        count = self.attachments_panel.attachments_list.count()
        # Find the attachments tab (should be index 1)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.attachments_panel:
                self.tabs.setTabText(i, f"Attachments ({count})")
                break
