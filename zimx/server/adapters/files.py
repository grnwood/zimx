from __future__ import annotations

import datetime as dt
from pathlib import Path
import shutil
from typing import Dict, List


PAGE_SUFFIX = ".txt"


class FileAccessError(RuntimeError):
    pass


def _page_file_for(directory: Path) -> Path:
    return directory / f"{directory.name}{PAGE_SUFFIX}"


def _ensure_page_file(path: Path) -> None:
    if path.suffix.lower() != PAGE_SUFFIX:
        raise FileAccessError("Only page text files (.txt) are supported.")


def _ensure_valid_page_name(path: Path) -> None:
    _ensure_page_file(path)
    parent = path.parent
    expected = f"{parent.name}{PAGE_SUFFIX}"
    if path.name != expected:
        raise FileAccessError("Page files must share the same name as their parent folder.")


def _ensure_page_scaffold(directory: Path) -> Path:
    page_file = _page_file_for(directory)
    if not page_file.exists():
        directory.mkdir(parents=True, exist_ok=True)
        page_file.write_text(f"# {directory.name}\n\n", encoding="utf-8")
    return page_file


def _resolve(root: Path, relative_path: str) -> Path:
    if not relative_path:
        raise FileAccessError("Path must not be empty")
    rel = relative_path.lstrip("/")
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        raise FileAccessError("Attempted access outside the vault root")
    return target


def read_file(root: Path, path: str) -> str:
    target = _resolve(root, path)
    if target.is_dir():
        target = _page_file_for(target)
    if not target.exists():
        parent = target.parent
        if not parent.exists():
            raise FileNotFoundError(target)
        _ensure_valid_page_name(target)
        target.write_text(f"# {parent.name}\n\n", encoding="utf-8")
    _ensure_valid_page_name(target)
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FileAccessError("File is not UTF-8 encoded text.") from exc


def write_file(root: Path, path: str, content: str) -> None:
    target = _resolve(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_valid_page_name(target)
    target.write_text(content, encoding="utf-8")


def list_dir(root: Path, subpath: str = "/", recursive: bool = True) -> List[Dict]:
    """List directories under the given subpath.

    Args:
        root: vault root
        subpath: vault-relative folder ("/" for root)
        recursive: when False, only include direct children and mark has_children
    """
    _ensure_page_scaffold(root)
    try:
        target = _resolve(root, subpath) if subpath and subpath != "/" else root
    except FileNotFoundError:
        return []
    if not target.exists() or not target.is_dir():
        return []

    def build(directory: Path) -> Dict:
        page_name = directory.name if directory != root else root.name
        rel_dir = directory.relative_to(root).as_posix() if directory != root else ""
        children = []
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if recursive:
                children.append(build(child))
            else:
                grand_dirs = [
                    d
                    for d in child.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                ]
                page_file = _page_file_for(child)
                rel_file = page_file.relative_to(root).as_posix()
                children.append(
                    {
                        "name": child.name,
                        "path": f"/{child.relative_to(root).as_posix()}",
                        "is_dir": bool(grand_dirs),
                        "has_children": bool(grand_dirs),
                        "open_path": f"/{rel_file}",
                        "children": [],
                    }
                )
        page_file = _page_file_for(directory)
        rel_file = page_file.relative_to(root).as_posix()
        has_children = bool(children)
        node = {
            "name": page_name,
            "path": f"/{rel_dir}" if rel_dir else "/",
            "is_dir": has_children,
            "has_children": has_children,
            "open_path": f"/{rel_file}",
            "children": children,
        }
        return node

    return [build(target)]


def ensure_journal_today(root: Path, template: str | None = None) -> tuple[Path, bool]:
    """Ensure today's journal page exists and return its file path and creation flag.

    If a template string is provided and the page does not yet exist, the file
    will be created with that template content. Otherwise a simple default stub
    is used for first-time creation.

    Returns:
        tuple[Path, bool]: (page file path, True if the page was created)
    """
    today = dt.datetime.now()
    rel = Path("Journal") / f"{today:%Y}" / f"{today:%m}" / f"{today:%d}"
    page_dir = root / rel
    page_dir.mkdir(parents=True, exist_ok=True)
    page_file = page_dir / f"{page_dir.name}{PAGE_SUFFIX}"
    created = not page_file.exists()
    if created:
        content = template if template is not None else f"# {today:%A, %B %d, %Y}\n\n"
        page_file.write_text(content, encoding="utf-8")
    return page_file, created


def create_directory(root: Path, path: str) -> None:
    target = _resolve(root, path)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True, exist_ok=False)
    _ensure_page_scaffold(target)


def create_markdown_file(root: Path, path: str, content: str = "") -> None:
    target = _resolve(root, path)
    if target.exists():
        raise FileExistsError(target)
    _ensure_valid_page_name(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def delete_path(root: Path, path: str) -> None:
    target = _resolve(root, path)
    if target == root:
        raise FileAccessError("Cannot delete the vault root")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        _ensure_valid_page_name(target)
        parent = target.parent
        if parent == root:
            raise FileAccessError("Cannot delete the root page")
        shutil.rmtree(parent, ignore_errors=False)
