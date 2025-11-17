# PyInstaller spec file for ZimX
# Usage:
#   pyinstaller -y packaging/zimx.spec
#   (Set ZIMX_VERSION env var for version stamping if desired.)

import os
from PyInstaller.utils.hooks import collect_submodules

# Resolve project root regardless of where the spec file lives

def _find_root():
    cand = os.getcwd()
    for _ in range(4):
        probe = os.path.join(cand, 'zimx', 'app', 'main.py')
        if os.path.exists(probe):
            return cand
        cand = os.path.dirname(cand)
    # Fallback to current working dir
    return os.getcwd()

ROOT = _find_root()

# Entry script (absolute)
MAIN = os.path.join(ROOT, 'zimx', 'app', 'main.py')

# Hidden imports sometimes needed for PySide6 / FastAPI
hidden = collect_submodules('PySide6') + [
    'fastapi', 'httpx', 'pydantic', 'uvicorn', 'jinja2', 'anyio', 'starlette'
]

ZIMX_VERSION = os.getenv('ZIMX_VERSION','0.1.0')

# Data files: templates + optional icon.png for in‑app use
_datas = [
    (os.path.join(ROOT, 'zimx', 'templates'), 'zimx/templates'),
]
_icon_png = os.path.join(ROOT, 'assets', 'icon.png')
if os.path.exists(_icon_png):
    _datas.append((_icon_png, 'assets'))

datas = _datas

block_cipher = None

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

a = Analysis(
    [MAIN],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter','pytest','tests','unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Onedir bundle: exe + COLLECT for folder distribution (faster startup)
_icon_ico = os.path.join(ROOT, 'assets', 'icon.ico')
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ZimX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_icon_ico if os.path.exists(_icon_ico) else None,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='ZimX'
)

