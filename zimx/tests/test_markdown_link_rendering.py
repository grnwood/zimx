import pytest
from PySide6.QtWidgets import QApplication
from zimx.app.ui.markdown_editor import MarkdownEditor

@pytest.fixture(scope="module")
def app():
    return QApplication([])

@pytest.fixture
def editor(app):
    ed = MarkdownEditor()
    ed.show()  # Needed for rendering
    return ed

def test_paste_link_from_buffer(editor):
    # Simulate pasting a wiki-style link from buffer
    link = "[:Journal:2025:11:20:MintSoundFix#1-your-fix-lives-in-etcmodprobed-kernel-agnostic|inline link]"
    editor.setPlainText("")
    editor.insertPlainText(link)
    editor._refresh_display()
    assert "inline link" in editor.toPlainText() or "inline link" in editor.toHtml()


def test_paste_colon_link_with_slug(editor):
    # Simulate pasting a colon link with slug and spaces
    link = "[PageA:PageB:PageC#slug-with-spaces|Label With Spaces]"
    editor.setPlainText("")
    editor.insertPlainText(link)
    editor._refresh_display()
    assert "Label With Spaces" in editor.toPlainText() or "Label With Spaces" in editor.toHtml()


def test_edit_link_inline(editor):
    # Insert a sentence with a link, then edit the label
    sentence = "This is a [PageA:PageB|OriginalLabel] in a sentence."
    editor.setPlainText(sentence)
    editor._refresh_display()
    # Simulate editing the label inline
    new_sentence = "This is a [PageA:PageB|NewLabel] in a sentence."
    editor.setPlainText(new_sentence)
    editor._refresh_display()
    assert "NewLabel" in editor.toPlainText() or "NewLabel" in editor.toHtml()
    # Ensure only the label is shown, not the raw link
    assert "PageA:PageB" not in editor.toPlainText() or "NewLabel" in editor.toHtml()


def test_camelcase_link(editor):
    # Simulate inserting a CamelCase link
    link = "+CamelCasePage"
    editor.setPlainText("")
    editor.insertPlainText(link)
    editor._refresh_display()
    # Should render as a link (the label is the page name)
    assert "CamelCasePage" in editor.toPlainText() or "CamelCasePage" in editor.toHtml()
