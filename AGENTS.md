# Repository Guidelines

## Project Structure & Module Organization
- `zimx/app/`: PySide6 desktop app; entry point is `zimx/app/main.py`.
- `zimx/server/`: embedded FastAPI backend used by the desktop app.
- `zimx/rag/`, `zimx/ai/`: RAG and AI integration modules.
- `zimx/webserver/`: optional web server and templates.
- `web-client/`: React + TypeScript PWA client (separate toolchain).
- `tests/`: pytest suite and Markdown fixtures.
- `zimx/templates/`, `zimx/help-vault/`: bundled templates and help content.
- `packaging/`: PyInstaller specs and install assets.
- `assets/`, `docs/`, `tools/`, `dev-assets/`: resources and helper scripts.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: create/activate a local venv.
- `pip install -r zimx/requirements.txt`: install desktop app deps.
- `python -m zimx.app.main`: run the desktop app (starts embedded API on `127.0.0.1:${ZIMX_PORT:-8765}`).
- `pytest tests`: run the Python test suite.
- `pyinstaller -y packaging/zimx.spec`: build the desktop app into `dist/ZimX/`.
- `cd web-client && npm install`: install web-client deps.
- `cd web-client && npm run dev`: run the PWA dev server (proxies to `localhost:8000`).
- `cd web-client && npm run build`: build the PWA bundle.

## Coding Style & Naming Conventions
- Python: 4-space indentation, snake_case for functions/variables, PascalCase for classes.
- Keep modules small and follow existing layout in `zimx/app/ui/` and `zimx/server/`.
- Web client: TypeScript + React; components/pages use PascalCase filenames.
- Linting: `web-client` uses ESLint (`npm run lint`). No repo-wide formatter is enforced.

## Testing Guidelines
- Python tests use `pytest` in `tests/`; new tests should follow `test_*.py` naming.
- Markdown fixtures live alongside tests for editor/link behavior.
- Web client currently has no automated tests; note any manual verification in PRs.

## Commit & Pull Request Guidelines
- Commit messages are short and imperative (e.g., “fix toc widget crash win32”).
- PRs should include: a brief summary, testing performed, and screenshots for UI changes (desktop or web-client).

## Security & Configuration Tips
- Vaults and user settings are local (`.zimx/settings.db` under the selected vault). Do not commit user data.
- Web client config lives in `web-client/.env.local` (e.g., `VITE_API_BASE_URL=http://localhost:8000`).
