"""Tests that right-click context menu does not clear the current selection."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zimx.app.ui.markdown_editor import MarkdownEditor


def test_right_click_preserves_selection(qapp):
    editor = MarkdownEditor()
    editor.setPlainText("Hello world")
    editor.show()
    QApplication.processEvents()

    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(5, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    QApplication.processEvents()
    assert editor.textCursor().hasSelection()

    # Right click somewhere (even outside selection) should not clear selection.
    QTest.mouseClick(editor.viewport(), Qt.RightButton, pos=editor.viewport().rect().center())
    QApplication.processEvents()

    assert editor.textCursor().hasSelection()

