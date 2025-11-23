from __future__ import annotations

import re
from pathlib import Path
import hashlib
from typing import List, Set, Optional

from zimx.app import config
from zimx.app.ui.path_utils import colon_to_path, normalize_link_target
from zimx.server.adapters.files import PAGE_SUFFIX

# Bump this when task parsing logic changes to force re-index even if file hash is unchanged.
INDEX_SCHEMA_VERSION = "task-parse-v2"

TAG_PATTERN = re.compile(r"@(\w+)")
# Markdown-style links: [label](target)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
# Wiki-style links used by the editor's storage format: [target|label]
WIKI_LINK_PATTERN = re.compile(r"\[(?P<link>[^\]|]+)\|[^\]]*\]")
# Tasks: support "- [ ]", "- [x]", "( )", "(x)", "(X)", and Unicode checkboxes "☐"/"☑"
TASK_PATTERN = re.compile(
    r"^(?P<indent>\s*)"
    r"(?:(?:-\s*\[(?P<state1>[ xX])\])|(?:\((?P<state2>[xX ])?\))|(?P<box>[☐☑]))"
    r"\s+(?P<body>.+)$"
)
DUE_PATTERN = re.compile(r"<([0-9]{4}-[0-9]{2}-[0-9]{2})")
START_PATTERN = re.compile(r">([0-9]{4}-[0-9]{2}-[0-9]{2})")
PRIORITY_PATTERN = re.compile(r"!{1,3}")


def index_page(path: str, content: str) -> bool:
    """Index page metadata into the per-vault database.

    Returns True if the index was updated (content changed), False if skipped.
    """
    if not config.has_active_vault():
        return False
    # Fast short-circuit: if content hash unchanged, skip heavy parsing and DB writes
    digest = hashlib.md5((INDEX_SCHEMA_VERSION + content).encode("utf-8")).hexdigest()
    prev = config.get_page_hash(path)
    if prev == digest:
        return False

    tags = sorted(set(TAG_PATTERN.findall(content)))
    link_targets = _extract_link_targets(content)
    links = sorted(link_targets)
    tasks = extract_tasks(path, content)
    title = derive_title(path, content)
    config.update_page_index(path, title, tags, links, tasks)
    config.set_page_hash(path, digest)
    return True


def derive_title(path: str, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ")
    return Path(path).stem or Path(path).name


def _extract_link_targets(content: str) -> Set[str]:
    """Extract page link targets from markdown and wiki-style links."""
    targets: Set[str] = set()
    for raw in MARKDOWN_LINK_PATTERN.findall(content):
        normalized = _normalize_page_link(raw)
        if normalized:
            targets.add(normalized)
    for match in WIKI_LINK_PATTERN.finditer(content):
        raw = match.group("link")
        # Skip wiki-like text that is immediately followed by "(...)" to avoid
        # counting markdown links twice.
        end = match.end()
        if end < len(content) and content[end] == "(":
            continue
        normalized = _normalize_page_link(raw)
        if normalized:
            targets.add(normalized)

    # Extract CamelCase/plus-prefixed links: +PageName
    camel_pattern = re.compile(r"\+(?P<link>[A-Za-z][\w]*)")
    for match in camel_pattern.finditer(content):
        link = match.group("link")
        # Convert CamelCase to a vault-relative path (relative to current page's folder)
        # Here, we treat CamelCase as a page in the same folder, so just add the .txt suffix
        if link:
            # This will be normalized to "/PageName/PageName.txt"
            page_path = f"/{link}/{link}{PAGE_SUFFIX}"
            targets.add(page_path)
    return targets


def _normalize_page_link(link: str) -> Optional[str]:
    """Normalize a link target to a vault-relative page path with .txt suffix.

    Returns None for external URLs or non-page resources.
    """
    cleaned = normalize_link_target(link or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith(("http://", "https://", "mailto:", "ftp://")):
        return None
    base = cleaned.split("#", 1)[0]
    if not base:
        return None

    # Colon notation (PageA:PageB) is the preferred storage format
    if ":" in base and not base.startswith("/"):
        return colon_to_path(base)

    # Slash paths - ensure they are anchored at root
    path = base if base.startswith("/") else f"/{base}"
    path_obj = Path(path)
    # Skip obvious non-page assets (images, docs, etc.)
    if path_obj.suffix and path_obj.suffix.lower() != PAGE_SUFFIX:
        return None
    if path_obj.suffix.lower() != PAGE_SUFFIX:
        leaf = path_obj.name or path_obj.parent.name
        if not leaf:
            return None
        path_obj = path_obj / f"{leaf}{PAGE_SUFFIX}"
    normalized = path_obj.as_posix()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def extract_tasks(path: str, content: str) -> List[dict]:
    results: List[dict] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        match = TASK_PATTERN.match(line)
        if not match:
            continue
        body = match.group("body")
        state = match.group("state1") or match.group("state2") or ("x" if match.group("box") == "☑" else " ")
        tags = sorted(set(TAG_PATTERN.findall(body)))
        due = _first_match(DUE_PATTERN, body)
        start = _first_match(START_PATTERN, body)
        pri_matches = PRIORITY_PATTERN.findall(body)
        priority = min(max((len(m) for m in pri_matches), default=0), 3)
        clean_text = TAG_PATTERN.sub(" ", body)
        clean_text = DUE_PATTERN.sub(" ", clean_text)
        clean_text = START_PATTERN.sub(" ", clean_text)
        clean_text = PRIORITY_PATTERN.sub(" ", clean_text)
        clean_text = re.sub(r"\s{2,}", " ", clean_text).strip()
        results.append(
            {
                "id": f"{path}:{line_no}",
                "line": line_no,
                "text": clean_text,
                "status": "done" if state.lower() == "x" else "todo",
                "priority": priority,
                "due": due,
                "start": start,
                "tags": tags,
            }
        )
    return results


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None
