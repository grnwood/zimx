from __future__ import annotations

import re
from pathlib import Path
import hashlib
from typing import List, Set

from zimx.app import config

TAG_PATTERN = re.compile(r"@(\w+)")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
TASK_PATTERN = re.compile(r"^(?P<indent>\s*)\((?P<state>[xX ])?\)\s+(?P<body>.+)$")
DUE_PATTERN = re.compile(r"<([0-9]{4}-[0-9]{2}-[0-9]{2})")
START_PATTERN = re.compile(r">([0-9]{4}-[0-9]{2}-[0-9]{2})")
PRIORITY_PATTERN = re.compile(r"(!{1,5})")


def index_page(path: str, content: str) -> bool:
    """Index page metadata into the per-vault database.

    Returns True if the index was updated (content changed), False if skipped.
    """
    if not config.has_active_vault():
        return False
    # Fast short-circuit: if content hash unchanged, skip heavy parsing and DB writes
    digest = hashlib.md5(content.encode("utf-8")).hexdigest()
    prev = config.get_page_hash(path)
    if prev == digest:
        return False

    tags = sorted(set(TAG_PATTERN.findall(content)))
    link_targets = {normalize_link(match) for match in LINK_PATTERN.findall(content) if match}
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


def normalize_link(link: str) -> str:
    cleaned = link.strip()
    if not cleaned:
        return "/"
    if cleaned.startswith("/"):
        return cleaned
    return "/" + cleaned


def extract_tasks(path: str, content: str) -> List[dict]:
    results: List[dict] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        match = TASK_PATTERN.match(line)
        if not match:
            continue
        body = match.group("body")
        state = match.group("state") or " "
        tags = sorted(set(TAG_PATTERN.findall(body)))
        due = _first_match(DUE_PATTERN, body)
        start = _first_match(START_PATTERN, body)
        priority_match = PRIORITY_PATTERN.search(body)
        priority = len(priority_match.group(1)) if priority_match else 0
        clean_text = TAG_PATTERN.sub("", body)
        clean_text = DUE_PATTERN.sub("", clean_text)
        clean_text = START_PATTERN.sub("", clean_text)
        clean_text = clean_text.replace("!", "").strip()
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
