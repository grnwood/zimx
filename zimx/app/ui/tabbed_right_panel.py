from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QWidget
from zimx.app import config

from .ai_chat_panel import AIChatPanel
from .task_panel import TaskPanel
from .attachments_panel import AttachmentsPanel
from .link_navigator_panel import LinkNavigatorPanel


class TabbedRightPanel(QWidget):
    """Tabbed panel containing Tasks and Attachments views."""
    
    # Forward signals from child panels
    taskActivated = Signal(str, int)  # path, line (from TaskPanel)
    dateActivated = Signal(int, int, int)  # year, month, day (from TaskPanel calendar)
    linkActivated = Signal(str)  # page path from Link Navigator
    aiChatNavigateRequested = Signal(str)  # page path from AI Chat tab
    
    def __init__(self, parent=None, enable_ai_chats: bool = False, ai_chat_font_size: int = 13) -> None:
        super().__init__(parent)
        
        # Create tab widget
        self.tabs = QTabWidget()
        self.ai_chat_panel = None
        self.ai_chat_index = None
        self._ai_chat_font_size = self._clamp_ai_font(ai_chat_font_size)
        
        # Create Tasks tab (now includes calendar)
        self.task_panel = TaskPanel()
        self.tabs.addTab(self.task_panel, "Tasks")
        
        # Create Attachments tab
        self.attachments_panel = AttachmentsPanel()
        self.tabs.addTab(self.attachments_panel, "Attachments")

        # Create Link Navigator tab
        self.link_panel = LinkNavigatorPanel()
        self.tabs.addTab(self.link_panel, "Link Navigator")

        # Create AI Chat tab if enabled
        if enable_ai_chats:
            self._add_ai_chat_tab()
        
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
        if self.ai_chat_panel:
            self.ai_chat_panel.set_vault_root(vault_root)
    
    def refresh_calendar(self) -> None:
        """Refresh the calendar to update bold dates."""
        self.task_panel._update_calendar_dates()
    
    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Set the calendar to show a specific date."""
        from PySide6.QtCore import QDate
        date = QDate(year, month, day)
        self.task_panel.calendar.setSelectedDate(date)
    
    def set_current_page(self, page_path, relative_path=None) -> bool:
        """Update panels with the current page."""
        self.attachments_panel.set_page(page_path)
        self.link_panel.set_page(relative_path)
        if self.ai_chat_panel:
            self.ai_chat_panel.set_current_page(relative_path)
        self._update_attachments_tab_label()
        if self.ai_chat_panel and hasattr(self.ai_chat_panel, "has_chat_for_path"):
            return self.ai_chat_panel.has_chat_for_path(relative_path)
        return False

    def set_font_size(self, size: int) -> None:
        """Propagate font size changes to AI chat."""
        if self.ai_chat_panel:
            self.ai_chat_panel.set_font_size(size)
            self._ai_chat_font_size = self.ai_chat_panel.get_font_size()
        else:
            self._ai_chat_font_size = self._clamp_ai_font(size)
        try:
            config.save_ai_chat_font_size(self._ai_chat_font_size)
        except Exception:
            pass

    def get_ai_font_size(self) -> int:
        """Return current AI chat font size."""
        if self.ai_chat_panel:
            return self.ai_chat_panel.get_font_size()
        return self._ai_chat_font_size

    @staticmethod
    def _clamp_ai_font(size: int) -> int:
        return max(6, min(24, size))
    
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

    def focus_ai_chat(self, page_path=None, create=False) -> None:
        """Switch to AI Chat tab and sync to the given page."""
        if not self.ai_chat_panel or self.ai_chat_index is None:
            return
        self.tabs.setCurrentIndex(self.ai_chat_index)
        if create:
            self.ai_chat_panel.open_chat_for_page(page_path)
        else:
            self.ai_chat_panel.set_current_page(page_path)

    def send_ai_action(self, action: str, prompt: str, text: str) -> None:
        """Forward external AI action to the chat panel."""
        if self.ai_chat_panel:
            self.ai_chat_panel.send_action_message(action, prompt, text)

    def _emit_chat_navigation(self, path: str) -> None:
        """Forward AI chat navigation requests."""
        self.aiChatNavigateRequested.emit(path)

    def _add_ai_chat_tab(self) -> None:
        if self.ai_chat_panel:
            return
        self.ai_chat_panel = AIChatPanel(font_size=self._ai_chat_font_size)
        self.tabs.addTab(self.ai_chat_panel, "AI Chat")
        self.ai_chat_index = self.tabs.indexOf(self.ai_chat_panel)
        self.ai_chat_panel.chatNavigateRequested.connect(self._emit_chat_navigation)

    def _remove_ai_chat_tab(self) -> None:
        if not self.ai_chat_panel:
            return
        idx = self.tabs.indexOf(self.ai_chat_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        try:
            self.ai_chat_panel.chatNavigateRequested.disconnect(self._emit_chat_navigation)
        except Exception:
            pass
        self.ai_chat_panel.deleteLater()
        self.ai_chat_panel = None
        self.ai_chat_index = None

    def set_ai_enabled(self, enabled: bool) -> None:
        """Enable or disable the AI Chat tab."""
        if enabled:
            self._add_ai_chat_tab()
        else:
            self._remove_ai_chat_tab()
    
    def _update_attachments_tab_label(self) -> None:
        """Update the Attachments tab label with the count of attachments."""
        count = self.attachments_panel.attachments_list.count()
        # Find the attachments tab (should be index 1)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.attachments_panel:
                self.tabs.setTabText(i, f"Attachments ({count})")
                break
