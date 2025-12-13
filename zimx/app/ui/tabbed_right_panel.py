from __future__ import annotations

import httpx
import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QTabWidget, QWidget, QMenu
from PySide6.QtCore import Qt
from zimx.app import config

from .ai_chat_panel import AIChatPanel
from .task_panel import TaskPanel
from .attachments_panel import AttachmentsPanel
from .link_navigator_panel import LinkNavigatorPanel
from .calendar_panel import CalendarPanel

_PAGE_LOGGING = os.getenv("ZIMX_DETAILED_PAGE_LOGGING", "0") not in ("0", "false", "False", "", None)


class TabbedRightPanel(QWidget):
    """Tabbed panel containing Tasks, Calendar, Attachments, and Link views."""
    
    # Forward signals from child panels
    taskActivated = Signal(str, int)  # path, line (from TaskPanel)
    dateActivated = Signal(int, int, int)  # year, month, day (from Calendar tab)
    linkActivated = Signal(str)  # page path from Link Navigator
    calendarPageActivated = Signal(str)  # page path from Calendar tab
    calendarTaskActivated = Signal(str, int)  # path, line from Calendar tab task list
    aiChatNavigateRequested = Signal(str)  # page path from AI Chat tab
    aiChatResponseCopied = Signal(str)  # status text when chat response copied
    aiOverlayRequested = Signal(str, object)  # text, anchor QPoint
    openInWindowRequested = Signal(str)  # page path to open in single-page editor
    openTaskWindowRequested = Signal()
    openLinkWindowRequested = Signal()
    openAiWindowRequested = Signal()
    openCalendarWindowRequested = Signal()
    filterClearRequested = Signal()
    
    def __init__(
        self,
        parent=None,
        enable_ai_chats: bool = False,
        ai_chat_font_size: int = 13,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(parent)
        
        # Create tab widget
        self.tabs = QTabWidget()
        self.ai_chat_panel = None
        self.ai_chat_index = None
        self._ai_chat_font_size = self._clamp_ai_font(ai_chat_font_size)
        self._http_client = http_client
        
        # Create Tasks tab (now includes calendar)
        self.task_panel = TaskPanel(font_size_key="task_font_size_tabbed", splitter_key="task_splitter_tabbed")
        self.tabs.addTab(self.task_panel, "Tasks")

        # Create Calendar tab
        self.calendar_panel = CalendarPanel(
            font_size_key="calendar_font_size_tabbed",
            splitter_key="calendar_splitter_tabbed",
            http_client=http_client,
            api_base=self._http_client.base_url if self._http_client else None,
        )
        self.tabs.addTab(self.calendar_panel, "Calendar")
        
        # Create Attachments tab
        self.attachments_panel = AttachmentsPanel(api_client=http_client)
        self.tabs.addTab(self.attachments_panel, "Attachments")

        # Create Link Navigator tab
        self.link_panel = LinkNavigatorPanel()
        self.tabs.addTab(self.link_panel, "Link Navigator")

        # Create AI Chat tab if enabled
        if enable_ai_chats:
            self._add_ai_chat_tab()
        
        # Set Tasks as default tab (index 0)
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(self._focus_current_tab)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._open_tab_context_menu)
        
        # Forward signals
        self.task_panel.taskActivated.connect(self.taskActivated)
        self.task_panel.filterClearRequested.connect(self.filterClearRequested)
        self.calendar_panel.dateActivated.connect(self.dateActivated)
        self.calendar_panel.pageActivated.connect(self.calendarPageActivated)
        self.calendar_panel.taskActivated.connect(self.calendarTaskActivated)
        self.calendar_panel.openInWindowRequested.connect(self.openInWindowRequested)
        self.link_panel.pageActivated.connect(self.linkActivated)
        self.link_panel.openInWindowRequested.connect(self.openInWindowRequested)
        
        # Layout
        from PySide6.QtWidgets import QVBoxLayout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self._focus_current_tab()
    
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
            self.calendar_panel.set_vault_root(vault_root)
        self.attachments_panel.set_vault_root(vault_root)
        try:
            self.link_panel.reload_mode_from_config()
            self.link_panel.reload_layout_from_config()
        except Exception:
            pass
        if self.ai_chat_panel:
            self.ai_chat_panel.set_vault_root(vault_root)
    
    def refresh_calendar(self) -> None:
        """Refresh the calendar to update bold dates."""
        self.calendar_panel.refresh()
    
    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Set the calendar to show a specific date."""
        self.calendar_panel.set_calendar_date(year, month, day)
    
    def set_current_page(self, page_path, relative_path=None) -> bool:
        """Update panels with the current page."""
        t0 = time.perf_counter()
        self.attachments_panel.set_page(page_path)
        t1 = time.perf_counter()
        self.link_panel.set_page(relative_path)
        t2 = time.perf_counter()
        try:
            if self.calendar_panel and relative_path:
                self.calendar_panel.set_current_page(relative_path)
        except Exception:
            pass
        if _PAGE_LOGGING:
            print(
                f"[PageLoadAndRender] right panel update attachments={(t1-t0)*1000:.1f}ms links={(t2-t1)*1000:.1f}ms"
            )
        if self.ai_chat_panel:
            self.ai_chat_panel.set_current_page(relative_path)
        t3 = time.perf_counter()
        if _PAGE_LOGGING:
            print(
                f"[PageLoadAndRender] right panel ai chat {(t3-t2)*1000:.1f}ms"
            )
        self._update_attachments_tab_label()
        if self.ai_chat_panel and hasattr(self.ai_chat_panel, "has_chat_for_path"):
            return self.ai_chat_panel.has_chat_for_path(relative_path)
        return False

    def set_page_text_provider(self, provider) -> None:
        """Provide calendar panel with live editor text for AI summaries."""
        try:
            self.calendar_panel.set_page_text_provider(provider)
        except Exception:
            pass

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
                # Ensure content is fresh whenever the tab gains focus
                self.link_panel.refresh(page_path)
                break

    def _open_tab_context_menu(self, pos) -> None:
        """Offer 'Open in New Window' for select tabs."""
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        widget = self.tabs.widget(index)
        menu = QMenu(self)
        if widget == self.task_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openTaskWindowRequested.emit)
        elif widget == self.calendar_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openCalendarWindowRequested.emit)
        elif widget == self.link_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openLinkWindowRequested.emit)
        elif widget == self.ai_chat_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openAiWindowRequested.emit)
        else:
            return
        global_pos = bar.mapToGlobal(pos)
        menu.exec(global_pos)

    def _focus_current_tab(self) -> None:
        """Ensure the active tab gains focus when selected."""
        widget = self.tabs.currentWidget()
        if widget:
            # If Tasks tab, focus its search bar for quick typing
            if widget == self.task_panel and hasattr(self.task_panel, "focus_search"):
                self.task_panel.focus_search()
            else:
                widget.setFocus(Qt.OtherFocusReason)

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

    def send_text_to_chat(self, text: str) -> bool:
        """Send raw text into the active chat session (prefers the currently open AI tab)."""
        if not self.ai_chat_panel or self.ai_chat_index is None:
            return False
        if not text.strip():
            return False
        self.ai_chat_panel.send_text_message(text.strip())
        return True

    def get_active_chat_path(self) -> Optional[str]:
        """Folder path of the currently loaded chat session."""
        if not self.ai_chat_panel:
            return None
        return self.ai_chat_panel.get_active_chat_path()

    def is_active_chat_for_page(self, rel_path: Optional[str]) -> bool:
        """Return True if the active chat matches the given page's folder."""
        if not rel_path or not self.ai_chat_panel:
            return False
        active_path = self.get_active_chat_path() or ""
        folder_path = "/" + Path(rel_path.lstrip("/")).parent.as_posix()
        return folder_path == active_path

    def focus_ai_chat_input(self) -> None:
        if not self.ai_chat_panel or self.ai_chat_index is None:
            return
        self.tabs.setCurrentIndex(self.ai_chat_index)
        QTimer.singleShot(0, self.ai_chat_panel.focus_input)

    def _emit_chat_navigation(self, path: str) -> None:
        """Forward AI chat navigation requests."""
        self.aiChatNavigateRequested.emit(path)

    def _emit_ai_overlay_request(self, text: str, anchor) -> None:
        """Forward AI overlay requests from the chat panel."""
        self.aiOverlayRequested.emit(text, anchor)

    def _add_ai_chat_tab(self) -> None:
        if self.ai_chat_panel:
            return
        self.ai_chat_panel = AIChatPanel(font_size=self._ai_chat_font_size, api_client=self._http_client)
        self.tabs.addTab(self.ai_chat_panel, "AI Chat")
        self.ai_chat_index = self.tabs.indexOf(self.ai_chat_panel)
        self.ai_chat_panel.chatNavigateRequested.connect(self._emit_chat_navigation)
        self.ai_chat_panel.responseCopied.connect(self.aiChatResponseCopied)
        self.ai_chat_panel.aiOverlayRequested.connect(self._emit_ai_overlay_request)

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
