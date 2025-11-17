from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QWidget

from .task_panel import TaskPanel


class TabbedRightPanel(QWidget):
    """Tabbed panel containing Tasks and Attachments views."""
    
    # Forward signals from child panels
    taskActivated = Signal(str, int)  # path, line (from TaskPanel)
    dateActivated = Signal(int, int, int)  # year, month, day (from TaskPanel calendar)
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        
        # Create tab widget
        self.tabs = QTabWidget()
        
        # Create Tasks tab (now includes calendar)
        self.task_panel = TaskPanel()
        self.tabs.addTab(self.task_panel, "Tasks")
        
        # Create Attachments placeholder tab
        self.attachments_placeholder = QWidget()
        self.tabs.addTab(self.attachments_placeholder, "Attachments")
        
        # Set Tasks as default tab (index 0)
        self.tabs.setCurrentIndex(0)
        
        # Forward signals
        self.task_panel.taskActivated.connect(self.taskActivated)
        self.task_panel.dateActivated.connect(self.dateActivated)
        
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
