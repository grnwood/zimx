# PyInstaller spec file for ZimX Server (FastAPI only)
# Usage:
#   pyinstaller -y packaging/zimx-server.spec
#   (Set ZIMX_VERSION env var for version stamping if desired.)

import os
from PyInstaller.utils.hooks import collect_submodules


def _find_root():
    cand = os.getcwd()
    for _ in range(4):
        probe = os.path.join(cand, "zimx", "server", "api.py")
        if os.path.exists(probe):
            return cand
        cand = os.path.dirname(cand)
    return os.getcwd()


ROOT = _find_root()
MAIN = os.path.join(ROOT, "zimx", "server", "api.py")

hidden = (
    [
        "multipart",
        "fastapi",
        "httpx",
        "pydantic",
        "uvicorn",
        "anyio",
        "starlette",
        "argon2",
        "passlib",
        "jose",
        "jose.jwt",
        "chromadb.api.rust",
    ]
    + collect_submodules("chromadb")
    + collect_submodules("tokenizers")
    + collect_submodules("onnxruntime")
)

ZIMX_VERSION = os.getenv("ZIMX_VERSION", "0.1.0")

datas = []

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
    excludes=["tkinter", "pytest", "tests", "unittest", "PySide6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ZimXServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="ZimXServer",
)
