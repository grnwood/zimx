# ZimX

ZimX is a local-first, Zim-style note system with a PySide6 desktop app and an embedded FastAPI backend. It is built around a folder-per-page vault structure, fast navigation, and Markdown-first editing.

## Highlights

- Local-first vaults on disk (folders + Markdown files).
- Fast tree navigation, history popup, and heading switcher.
- Markdown editor with formatting shortcuts, task parsing, and inline images.
- Journaling workflows with date navigation and templates.
- Optional vi-mode navigation/editing.
- Built-in help vault and keyboard shortcuts guide.
- AI chat panel, one-shot prompts, and AI actions when configured.
- Focus/Audience modes for distraction-free writing and reading.
- Link graph / navigator for contextual browsing and filtered views.
- PlantUML diagramming with AI-assisted generation and templates.

## Getting Started

1. Create / activate a virtual environment (optional):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r zimx/requirements.txt
   ```
3. Run the desktop app:
   ```bash
   python -m zimx.app.main
   ```

The embedded FastAPI server boots automatically and listens on `127.0.0.1:${ZIMX_PORT:-8765}`.

## Vault Structure

A vault is a normal folder on disk. Each page is a folder containing a same-named Markdown file:

```
MyVault/
  Projects/
    Project Phoenix/
      Project Phoenix.md
      attachments/
        diagram.png
```

- Page files use `.md` (legacy `.txt` still works).
- Attachments live in `attachments/` alongside the page file.
- Journal pages are stored under `Journal/YYYY/MM/DD/DD.md`.

## Desktop App Overview

The app lives in `zimx/app` and is centered around `zimx/app/ui/main_window.py` and the custom editor in `zimx/app/ui/markdown_editor.py`.

Key UI features:

- Vault picker, New Vault flow, and multi-window support.
- Left tree navigator with inline rename, create, and delete.
- History popup (Ctrl+Tab) and heading switcher (Ctrl+Shift+Tab).
- Task panel with tag filtering and search.
- Calendar panel and "Today" journal actions.
- Attachments, link navigator, and AI panels (optional).
- Focus/Audience modes for distraction-free reading.

## Keyboard Shortcuts

The built-in help vault includes a full shortcuts guide:

- Help menu: **Help → Keyboard Shortcuts**
- File in repo: `zimx/help-vault/Keyboard Shortcuts/Keyboard Shortcuts.md`

The help vault is copied to `~/.zimx/help-vault` on first open. To refresh it from the repo, delete or rename that folder and reopen Help → Documentation.

## FastAPI Backend

The API lives in `zimx/server/api.py` and is embedded in the desktop app. It handles vault file access, tree listing, tasks, search, and journal utilities. Requests expect vault-relative paths starting with `/` and are validated to stay within the selected vault root.

## Templates

Template files live in `zimx/templates` and user templates are stored under `~/.zimx/templates`. Templates currently use `.txt` names (by design).

## Tests

Tests live in `tests/`:

```bash
pytest tests
```

## Packaging (PyInstaller)

Build scripts and spec live under `packaging/`.

```bash
pyinstaller -y packaging/zimx.spec
```

Artifacts land in `dist/ZimX/`.

## Install into OS
If you want to install fully into the OS there are some helper scripts in packaging/

### Windows
Open powershell

```bash
> .\venv\Scripts\Activate.ps1
> pyinstaller.exe -y .\packaging\zimx.spec
> cd .\packaging\win32\
> .\install.ps1
```

Zimx should be installed in menus, etc.

### Linux
```bash
~/code/zimx$ cd packaging/linux-desktop/
~/code/zimx/packaging/linux-desktop$ sudo ./install-app.sh 
📦 Installing ZimX...
➡️  Creating install dir: /opt/zimx
➡️  Copying files...
➡️  Creating symlink: /usr/local/bin/zimx
➡️  Installing icon to /usr/share/icons/zimx.png
➡️  Creating desktop entry at /usr/share/applications/zimx.desktop

🎉 ZimX installed successfully!
You can now launch it from: Menu → Accessories → ZimX
Or run from terminal: zimx
```
## Repo Layout

- `zimx/app/` - Desktop app (PySide6)
- `zimx/server/` - Embedded FastAPI backend
- `zimx/help-vault/` - Bundled help vault content
- `zimx/templates/` - Default templates
- `tests/` - pytest suite
- `packaging/` - PyInstaller spec and assets

## Notes

ZimX stores settings per-vault in `.zimx/settings.db` (SQLite). Vault contents always live where the user chooses and remain plain files on disk.
