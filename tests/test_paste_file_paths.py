"""Test pasting file paths auto-converts to links with filename labels."""
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData
from zimx.app.ui.markdown_editor import MarkdownEditor


@pytest.fixture(scope="module")
def app():
    return QApplication([])


@pytest.fixture
def editor(app):
    ed = MarkdownEditor()
    ed.show()
    return ed


def test_paste_linux_file_path_creates_link(editor):
    """Test that pasting a Linux file path creates a link with filename as label."""
    editor.setPlainText("")
    
    # Simulate pasting a file path
    file_path = "/home/user/documents/report.pdf"
    
    mime = QMimeData()
    mime.setText(file_path)
    editor.insertFromMimeData(mime)
    
    # Get the markdown content
    content = editor.to_markdown()
    print(f"Content after paste: {repr(content)}")
    
    # Should create link with filename as label
    assert "[/home/user/documents/report.pdf|report.pdf]" in content
    
    # Should NOT have leading colon
    assert ":/home" not in content
    
    # Should NOT have empty label
    assert "[/home/user/documents/report.pdf|]" not in content
    
    # Should NOT have duplication
    assert "report.pdf]report.pdf]" not in content
    assert content == "[/home/user/documents/report.pdf|report.pdf]"


def test_paste_windows_file_path_creates_link(editor):
    """Test that pasting a Windows file path creates a link with filename as label."""
    editor.setPlainText("")
    
    # Simulate pasting a Windows file path
    file_path = "C:\\Users\\john\\Documents\\file.docx"
    
    mime = QMimeData()
    mime.setText(file_path)
    editor.insertFromMimeData(mime)
    
    # Get the markdown content
    content = editor.to_markdown()
    print(f"Content after paste: {repr(content)}")
    
    # Should create link with normalized path and filename as label
    assert "[C:/Users/john/Documents/file.docx|file.docx]" in content
    
    # Should NOT have leading colon
    assert ":C:/" not in content


def test_paste_colon_link_still_works(editor):
    """Test that pasting colon notation links still works."""
    editor.setPlainText("")
    
    # Simulate pasting a colon link
    colon_link = ":Project:Meeting:Notes"
    
    mime = QMimeData()
    mime.setText(colon_link)
    editor.insertFromMimeData(mime)
    
    # Get the markdown content
    content = editor.to_markdown()
    print(f"Content after paste: {repr(content)}")
    
    # Should create link with leading colon and empty label (default)
    assert "[:Project:Meeting:Notes|]" in content


def test_paste_plain_text_unchanged(editor):
    """Test that pasting normal text doesn't convert to links."""
    editor.setPlainText("")
    
    # Simulate pasting plain text
    plain_text = "This is just some normal text"
    
    mime = QMimeData()
    mime.setText(plain_text)
    editor.insertFromMimeData(mime)
    
    # Get the markdown content
    content = editor.to_markdown()
    print(f"Content after paste: {repr(content)}")
    
    # Should be unchanged
    assert content == plain_text
    
    # Should NOT create a link
    assert "[" not in content
