"""Test insert link dialog with selected text."""
import sys
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from zimx.app.ui.markdown_editor import MarkdownEditor
from zimx.app.ui.insert_link_dialog import InsertLinkDialog
from zimx.app import config


@pytest.fixture(scope="module")
def app():
    return QApplication([])


def test_insert_link_replaces_selected_text(app, tmp_path):
    """Test that inserting a link replaces selected text without duplication."""
    # Setup vault
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    config.set_active_vault(str(vault_root))
    
    editor = MarkdownEditor()
    editor.set_context(str(vault_root), ":TestPage")
    
    # Set initial content with text to select
    editor.set_markdown("This is settings.db file")
    
    # Select "settings.db" text
    cursor = editor.textCursor()
    # Position 8 to 19 should be "settings.db"
    cursor.setPosition(8)
    cursor.setPosition(19, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    
    selected_text = cursor.selectedText()
    assert selected_text == "settings.db", f"Expected 'settings.db', got '{selected_text}'"
    
    # Save selection range
    selection_range = (cursor.selectionStart(), cursor.selectionEnd())
    
    # Simulate dialog blocking
    editor.begin_dialog_block()
    
    # Check that selection is still present after dialog block
    cursor_after_block = editor.textCursor()
    assert cursor_after_block.hasSelection(), "Selection should be preserved during dialog block"
    assert cursor_after_block.selectedText() == "settings.db"
    
    # End dialog block
    editor.end_dialog_block()
    
    # Now simulate the insert link workflow from main_window._insert_link
    colon_path = "/home/user/settings.db"
    link_name = "settings.db"
    
    # Remove selected text (as done in main_window._insert_link)
    if selection_range:
        cursor = editor.textCursor()
        start, end = selection_range
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        editor.setTextCursor(cursor)
    
    # Insert link
    label = link_name or selected_text or colon_path
    editor.insert_link(colon_path, label)
    
    # Get final content in storage format (with raw markdown)
    final_content = editor.to_markdown()
    print(f"Final content: {repr(final_content)}")
    
    # Should have link with label, but NOT duplicate text
    assert "[/home/user/settings.db|settings.db]" in final_content
    
    # Count occurrences of "settings.db" - should appear exactly ONCE in the link
    # (once as part of path, once as label)
    # NOT three times (path + label + duplicate text)
    settings_count = final_content.count("settings.db")
    assert settings_count == 2, f"Expected 2 occurrences of 'settings.db' (in path and label), got {settings_count}"
    
    # Make sure there's no trailing duplicate like "settings.db]settings.db]"
    assert "settings.db]settings.db" not in final_content
    
    # Final text should be: "This is [/home/user/settings.db|settings.db] file"
    assert "This is [/home/user/settings.db|settings.db] file" == final_content


def test_insert_link_file_path_no_leading_colon(app, tmp_path):
    """Test that file paths don't get a leading colon."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    config.set_active_vault(str(vault_root))
    
    editor = MarkdownEditor()
    editor.set_context(str(vault_root), ":TestPage")
    editor.set_markdown("")
    
    # Insert a file path link
    file_path = "/home/user/documents/file.txt"
    editor.insert_link(file_path, "My File")
    
    content = editor.to_markdown()
    print(f"Content after insert: {repr(content)}")
    
    # Should NOT have leading colon
    assert ":/home" not in content, "File path should not have leading colon"
    assert "[/home/user/documents/file.txt|My File]" in content


def test_insert_link_wiki_page_has_leading_colon(app, tmp_path):
    """Test that wiki page links DO get a leading colon."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    config.set_active_vault(str(vault_root))
    
    editor = MarkdownEditor()
    editor.set_context(str(vault_root), ":TestPage")
    editor.set_markdown("")
    
    # Insert a wiki page link
    editor.insert_link("Project:Meeting", "Meeting Notes")
    
    content = editor.to_markdown()
    print(f"Content after insert: {repr(content)}")
    
    # Should have leading colon
    assert "[:Project:Meeting|Meeting Notes]" in content
