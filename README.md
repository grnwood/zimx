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

## Packaging (PyInstaller)

The repository includes a starter spec file at `packaging/zimx.spec` plus placeholder icons in `assets/`. Replace `assets/icon.png` and `assets/icon.ico` with real artwork before distributing.

### 1. Install build dependencies (Linux & Windows)

Make sure you are in a fresh virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r zimx/requirements.txt
pip install pyinstaller
```

### 2. Build

Run PyInstaller with the provided spec:

```bash
pyinstaller -y packaging/zimx.spec
```

Artifacts will appear in `dist/ZimX/` (folder build). Test the executable before shipping.

### 3. Optional: Single-file build

Single-file executables start slower (temporary unpack). If desired:

```bash
pyinstaller -y --onefile --windowed --icon assets/icon.ico zimx/app/main.py
```

### 4. Windows Notes

* The spec sets `console=False`, so no console window appears. (Equivalent to using `pythonw`.)
* Replace `assets/icon.ico` with a multi-resolution icon (16, 32, 48, 256 px).
* For SmartScreen reputation, code-sign the EXE (outside this repo):
   ```powershell
   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 dist\ZimX\ZimX.exe
   ```
* Distribute via a zip or an installer (e.g. Inno Setup) once stable.

### 5. Linux Notes

* No console suppression needed; `console=False` is ignored if already windowed.
* To integrate with desktop environments, create a `.desktop` file pointing to the executable:
   ```ini
   [Desktop Entry]
   Type=Application
   Name=ZimX
   Exec=/opt/ZimX/ZimX
   Icon=/opt/ZimX/assets/icon.png
   Categories=Office;Utility;
   ```
   Place it in `~/.local/share/applications/` (user) or `/usr/share/applications/` (system).
* You can convert the `dist/ZimX` folder into a `.deb`/`.rpm` using `fpm` if desired.

### 6. Data & Settings Location

Runtime settings (SQLite DB, cached geometry, etc.) should live outside the bundled directory. If you introduce a platform-dependent path, prefer [`platformdirs`](https://pypi.org/project/platformdirs/):

```python
from platformdirs import user_data_dir
from pathlib import Path
store = Path(user_data_dir("ZimX", "ZimX"))
store.mkdir(exist_ok=True)
settings_path = store / "settings.db"
```

Vault contents remain wherever the user selects—never inside `dist/`.

### 7. Environment Flags (Optional)

Performance experimentation flags you can set before launching the binary:

```bash
export ZIMX_INCREMENTAL_LOAD=1   # incremental text load batches
export ZIMX_PORT=9000            # override API port
```

### 8. Verifying the Build

Run through this quick checklist on both platforms:

1. Launch (cold & warm) time acceptable.
2. Open a vault; pages render; links clickable.
3. Paste an image; it appears inline.
4. Autosave triggers on navigation.
5. Window geometry persists across restarts.
6. API health: `curl http://127.0.0.1:${ZIMX_PORT:-8765}/api/health` returns `{"ok": true}`.

### 9. Updating / Versioning

Set a version when building:

```bash
ZIMX_VERSION=0.1.1 pyinstaller -y packaging/zimx.spec
```

Include a `CHANGELOG.md` and optionally an auto-update check comparing a remote JSON manifest to the local version.

---

For a more optimized build later, consider trying Nuitka:

```bash
pip install nuitka
python -m nuitka --standalone --enable-plugin=pyside6 --windows-disable-console --output-dir=dist_nuitka zimx/app/main.py
```

But start with the PyInstaller baseline above.
