#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import textwrap
from pathlib import Path
from datetime import date, timedelta

PAGE_SUFFIX = ".txt"
LOREM_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis nostrud "
    "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat duis aute "
    "irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur excepteur sint occaecat cupidatat non proident sunt in culpa qui officia "
    "deserunt mollit anim id est laborum"
).split()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a sample ZimX vault with deep nesting, links, and tasks."
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default="vault-sample",
        help="Path where the vault should be created (default: vault-sample)",
    )
    args = parser.parse_args()

    dest = Path(args.destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    random.seed(42)

    root_name = dest.name
    pages: list[list[str]] = []

    # Include the vault root page
    pages.append([])

    # One very deep chain (10 levels)
    deep_root = "DeepDive"
    deep_chain = [deep_root] + [f"Layer{idx:02d}" for idx in range(1, 11)]
    pages.extend(_build_chain(deep_chain))

    # Nine other root pages with random depth 1-5
    root_topics = [
        "Overview",
        "Architecture",
        "Features",
        "Roadmap",
        "Workflows",
        "Testing",
        "Integrations",
        "Performance",
        "Ideas",
    ]
    for topic in root_topics:
        depth = random.randint(1, 5)
        chain = [topic] + [f"{topic}Sub{idx}" for idx in range(1, depth + 1)]
        pages.extend(_build_chain(chain))

    # Precompute colon names for linking
    colon_map = {tuple(parts): _colon_name(parts, root_name) for parts in pages}
    all_colon_targets = list(colon_map.values())

    for parts in pages:
        title = root_name if not parts else parts[-1]
        colon_path = colon_map[tuple(parts)]
        links = _pick_links(colon_path, all_colon_targets, count=4)
        tasks = _sample_tasks()
        paragraphs = random.randint(2, 5)
        long_bonus = 0
        if len(parts) == 0:
            long_bonus = 2
        elif parts[:1] == [deep_root] and len(parts) > 5:
            long_bonus = 2
        content = _build_content(title, colon_path, links, tasks, paragraphs + long_bonus)
        _write_page(dest, root_name, parts, content)

    print(f"Sample vault created at: {dest}")


def _build_chain(names: list[str]) -> list[list[str]]:
    """Return a list of path parts for every level in a chain."""
    path_parts: list[list[str]] = []
    current: list[str] = []
    for name in names:
        current.append(name)
        path_parts.append(current.copy())
    return path_parts


def _colon_name(parts: list[str], root_name: str) -> str:
    if not parts:
        return root_name
    return ":".join(parts)


def _pick_links(current: str, all_targets: list[str], count: int = 3) -> list[str]:
    pool = [t for t in all_targets if t != current]
    random.shuffle(pool)
    return pool[:count]


def _sample_tasks() -> list[str]:
    today = date.today()
    due_dates = [
        today - timedelta(days=7),
        today - timedelta(days=1),
        today + timedelta(days=2),
        today + timedelta(days=10),
        today + timedelta(days=45),
    ]
    templates = [
        ("Review link graph edges", "@backlog", 1),
        ("Refine backlink queries", "@research", 2),
        ("Write UI polish notes", "@ui", 0),
        ("Benchmark reindexing", "@perf", 3),
        ("Document task flow", "@docs", 1),
    ]
    tasks: list[str] = []
    for text, tag, priority in templates:
        due = random.choice(due_dates)
        start = due - timedelta(days=random.randint(0, 5))
        state = " " if random.random() > 0.35 else "x"
        bangs = "!" * max(0, min(priority, 3))
        start_part = f" >{start.isoformat()}" if start else ""
        tasks.append(f"({state})  {text} {bangs} {tag} <{due.isoformat()}{start_part}")
    return tasks


def _build_content(
    title: str, colon_path: str, links: list[str], tasks: list[str], paragraphs: int
) -> str:
    anchor_slugs = ["overview", "ideas", "tasks", "workflow", "graph", "api", "perf", "faq"]
    rich_links: list[str] = []
    for target in links:
        rich_links.append(target)
        if random.random() > 0.5:
            slug = random.choice(anchor_slugs)
            rich_links.append(f"{target}#{slug}")
    # Ensure uniqueness but keep order
    seen = set()
    dedup_links: list[str] = []
    for link in rich_links:
        if link in seen:
            continue
        seen.add(link)
        dedup_links.append(link)

    inline_refs = []
    if len(dedup_links) >= 2:
        inline_refs.append(
            f"See also :{dedup_links[0]} for background and :{dedup_links[1]} for related notes."
        )
    if len(dedup_links) >= 3:
        inline_refs.append(f"Jump to :{dedup_links[2]} for the task breakdown.")

    section_headings = [
        "## Overview",
        "## Ideas",
        "## Tasks",
    ]

    intro = [
        f"# {title}",
        "",
        f"This page lives at :{colon_path} in the ZimX vault.",
        "It describes features, workflows, and ideas for ZimX while acting as link data.",
        "",
        "Links to explore:",
        *[f"- [:{link}|{link}]" for link in dedup_links],
        "",
        "Tasks:",
        *tasks,
        "",
        "Notes:",
        *section_headings,
        *inline_refs,
    ]
    body = "\n\n".join(_paragraph() for _ in range(paragraphs))
    return "\n".join(intro) + "\n\n" + body + "\n"


def _paragraph(word_count: int | None = None) -> str:
    words = word_count or random.randint(80, 160)
    chosen = [random.choice(LOREM_WORDS) for _ in range(words)]
    text = " ".join(chosen)
    return textwrap.fill(text, width=86)


def _write_page(root: Path, root_name: str, parts: list[str], content: str) -> None:
    if not parts:
        # Root page lives directly under the vault root
        file_path = root / f"{root_name}{PAGE_SUFFIX}"
        file_path.write_text(content, encoding="utf-8")
        return

    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{parts[-1]}{PAGE_SUFFIX}"
    file_path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
