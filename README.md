# ZimX Desktop Scaffold

A local-first Zim-style note system combining a PySide6 desktop shell with a FastAPI backend. This repository contains the project skeleton plus the critical loops for vault navigation, editor load/save, API plumbing, and task extraction.

## Getting Started

1. **Create / activate a virtual environment** (optional):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. **Install dependencies**:
   ```bash
   pip install -r zimx/requirements.txt
   ```
3. **Run the desktop shell**:
   ```bash
   python -m zimx.app.main
   ```
   The FastAPI server is embedded and listens on `127.0.0.1:${ZIMX_PORT:-8765}`.

## FastAPI Contract

The API lives in `zimx/server/api.py` and exposes:

* `GET /api/health`
* `GET /api/search?q=&limit=` (stubbed)
* `GET /api/tasks?query=`
* `POST /api/file/read`
* `POST /api/file/write`
* `POST /api/journal/today`
* `POST /api/path/create`
* `POST /api/path/delete`
* `POST /api/ai/chat`
* `POST /api/vault/select` (Qt shell uses this to set the current vault root)
* `GET /api/vault/tree`

Requests expect vault-relative paths beginning with `/`. The API validates that resolved paths stay inside the currently selected vault before accessing the filesystem.

## Desktop Shell Overview

`zimx/app/main.py` boots FastAPI in a background thread, then launches a PySide6 `MainWindow` defined in `zimx/app/ui/main_window.py`. The UI features:

* Toolbar actions for creating/opening vaults, creating "Today" journal entries, refreshing the vault tree, and forcing a save.
* A `QTreeView` fed by `/api/vault/tree`; each entry represents a page folder, and selecting it loads the folder's `<PageName>.txt` file into the editor. Right-click to create/delete pages inline.
* A custom `QPlainTextEdit`-based editor (see `zimx/app/ui/markdown_editor.py`) with live Markdown-style highlighting (headings render inline, tasks flip between ☐ / ☑), autosave, CamelCase link navigation, and paste-to-image support.
* A dedicated Task rail that surfaces parsed tasks with tag filters and a fast search box, plus a jump dialog (`Ctrl+J`) for type-ahead navigation across indexed pages.

### Creating a Vault

Click **New Vault** in the toolbar, pick a parent directory, and enter a folder name. The app seeds the vault with:

* `<VaultName>/<VaultName>.txt` (home page)
* `Inbox/Inbox.txt`
* `Journal/Journal.txt`
* `README/README.txt`

Every page lives inside its own folder so future attachments can sit alongside the page’s text file (e.g., `ProjectPlan/ProjectPlan.txt`, `ProjectPlan/paste_image_001.png`, etc.). Use **Open Vault** whenever you want to switch to an existing folder.

### Inline Image Paste

Paste screenshots or copied images directly into the editor (Ctrl/Cmd+V). Each paste is saved next to the active page folder as `paste_image_XXX.png`, and the Markdown reference `![paste_image_XXX](./paste_image_XXX.png)` is inserted automatically. Images render inline as soon as they are created—you can double-click any image to open it in your system viewer, or right-click to pick a display width (300 px / 600 px / 900 px or a custom size). The chosen width is stored in the Markdown as `{width=…}` so it persists with the page.

### Navigator Context Menu

Right-click anywhere in the tree to create child pages relative to the clicked location or delete an existing page. New items prompt for a name inline near your cursor; Enter confirms and Esc cancels. Delete prompts for confirmation before removing the page folder and its attachments from disk.

### Navigator Shortcuts

* `↑` / `↓`: move through the next/previous entry; the editor loads that node's Markdown automatically and the previous page auto-saves.
* `Ctrl+↑` / `Ctrl+↓`: walk the tree depth-first, collapsing the previous folder and expanding the new one as you go.
* `Enter`: focuses the editor at the last caret position for the selected page.
* `Esc`: collapses the entire tree.

### Task Rail

The right-hand rail lists every parsed task (including nested subtasks) with instant filtering. Use the search field to match free text and select one or more tags to narrow the list. Clicking a row focuses the underlying page so you can edit it immediately.

### Autosave & Fonts

The editor auto-saves whenever you switch notes, lose focus, or after ~30 seconds of inactivity—manual `Ctrl/Cmd+S` still works for instant commits. Use `Ctrl++` / `Ctrl+-` (standard Zoom In/Out shortcuts) to adjust the editor font size. All preferences—including last-opened vault, font size, and caret positions—are stored in `~/.zimx/settings.db` (SQLite) so the app can restore your workspace across sessions.

### Supported Files

The vault tree only lists page folders (directories that contain a same-named `.txt` file). Each page is saved as UTF‑8 text, and binary files remain hidden in the navigator but can live alongside the page (for pasted images, documents, etc.). Attempting to open non-text files via the API is rejected so the editor never corrupts attachments.

## Tests

Unit tests for task parsing live in `zimx/tests/test_tasks.py` (pytest). Run them with:
```bash
pytest zimx/tests
```

## Next Steps

* Flesh out the FastAPI search/indexing logic (see `zimx/server/indexer.py`).
* Expand the Qt UI with dedicated panels for Tasks, Search, and AI interactions.
* Package the app with PyInstaller for cross-platform distribution.
