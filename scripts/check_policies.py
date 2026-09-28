"""Validate front matter and heading structure of the policy docs.

Usage: uv run python scripts/check_policies.py [--dir PATH]

Each data/policies/*.md (except NON_POLICY_FILES) must start with YAML front
matter holding exactly REQUIRED_FIELDS, and its body must be a clean heading
tree: no H1 (the title lives in the front matter), first heading an H2, no
skipped levels, no duplicate sibling headings, no empty sections. The RAG
indexer later chunks by heading and stores file#heading, so these are the
properties it relies on. Prints one row per file with its word count; exits 1
if any file is invalid.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "data" / "policies"

REQUIRED_FIELDS = ("title", "category", "version", "last_updated")
# Notes about the policy set, not policy content, so no front matter to check.
NON_POLICY_FILES = {"TRAPS.md"}

HEADING = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
CATEGORY = re.compile(r"[a-z][a-z0-9_-]*")


def split_front_matter(lines: list[str]) -> tuple[object, list[str], int, list[str]]:
    """Return (metadata, body lines, 1-based line number of body[0], errors)."""
    if not lines or lines[0].strip() != "---":
        return None, lines, 1, ["front matter missing (file must start with '---')"]
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, lines, 1, ["front matter is never closed (no second '---')"]
    body = lines[end + 1 :]
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        reason = str(exc).strip().splitlines()[0]
        return None, body, end + 2, [f"front matter is not valid YAML: {reason}"]
    return meta, body, end + 2, []


def check_front_matter(meta: object) -> list[str]:
    if not isinstance(meta, dict):
        return ["front matter must be a set of `key: value` lines"]
    errors = []
    missing = [f for f in REQUIRED_FIELDS if meta.get(f) in (None, "")]
    if missing:
        errors.append(f"front matter missing: {', '.join(missing)}")
    extra = sorted(str(k) for k in meta if k not in REQUIRED_FIELDS)
    if extra:
        errors.append(f"unexpected front matter field(s): {', '.join(extra)}")
    for field in ("title", "category", "version"):
        value = meta.get(field)
        if value not in (None, "") and not isinstance(value, str):
            errors.append(
                f'{field} must be a quoted string (got {value!r}, e.g. "1.0")'
            )
    category = meta.get("category")
    if isinstance(category, str) and not CATEGORY.fullmatch(category):
        errors.append(f"category {category!r} must be lowercase (a-z, 0-9, _ or -)")
    updated = meta.get("last_updated")
    if updated not in (None, "") and not isinstance(updated, datetime.date):
        errors.append(f"last_updated must be a YYYY-MM-DD date (got {updated!r})")
    return errors


def check_headings(body: list[str], first_line: int) -> list[str]:
    # (line number, level, text) for every heading outside code fences.
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    for offset, line in enumerate(body):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and (m := HEADING.match(line)):
            headings.append((first_line + offset, len(m.group(1)), m.group(2)))

    if not headings:
        return ["no headings found (need at least one H2)"]

    errors = []
    seen: set[tuple[str, ...]] = set()
    path: list[str] = []
    prev_level = 1
    for i, (line_no, level, text) in enumerate(headings):
        if level == 1:
            errors.append(f"line {line_no}: H1 not allowed (title is in front matter)")
        elif level > prev_level + 1:
            errors.append(
                f"line {line_no}: H{level} {text!r} skips a level (after H{prev_level})"
            )
        prev_level = level

        path = [*path[: level - 1], text.lower()]
        if tuple(path) in seen:
            errors.append(f"line {line_no}: duplicate heading {text!r}")
        seen.add(tuple(path))

        # A heading whose section has no text, and isn't just a parent of a
        # deeper heading, would index as an empty chunk.
        end = headings[i + 1][0] - first_line if i + 1 < len(headings) else len(body)
        has_text = any(line.strip() for line in body[line_no - first_line + 1 : end])
        next_is_child = i + 1 < len(headings) and headings[i + 1][1] > level
        if not has_text and not next_is_child:
            errors.append(f"line {line_no}: section {text!r} is empty")

    if headings[0][1] != 2:
        errors.append(
            f"line {headings[0][0]}: first heading must be H2 (got H{headings[0][1]})"
        )
    return errors


def word_count(body: list[str]) -> int:
    # Tokens with a letter or digit, so table pipes and rules don't count.
    return sum(
        1
        for line in body
        for token in line.lstrip("#").split()
        if any(ch.isalnum() for ch in token)
    )


def check_file(path: Path) -> tuple[object, int, list[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return None, 0, [f"cannot read file: {exc}"]
    meta, body, first_line, errors = split_front_matter(lines)
    if not errors:
        errors = check_front_matter(meta)
    errors += check_headings(body, first_line)
    return meta, word_count(body), errors


def field(meta: object, name: str) -> str:
    return str(meta.get(name, "-")) if isinstance(meta, dict) else "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir", type=Path, default=DEFAULT_DIR, help=f"default: {DEFAULT_DIR}"
    )
    args = parser.parse_args()

    if not args.dir.is_dir():
        print(f"FAIL  policy folder not found: {args.dir}")
        return 1
    files = sorted(p for p in args.dir.glob("*.md") if p.name not in NON_POLICY_FILES)
    if not files:
        print(f"FAIL  no policy .md files in {args.dir}")
        return 1

    rows = []
    failures: list[tuple[str, list[str]]] = []
    for path in files:
        meta, words, errors = check_file(path)
        rows.append(
            (
                path.name,
                field(meta, "category"),
                field(meta, "version"),
                field(meta, "last_updated"),
                str(words),
                "FAIL" if errors else "OK",
            )
        )
        if errors:
            failures.append((path.name, errors))

    header = ("file", "category", "version", "last_updated", "words", "status")
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        cells = [
            cell.rjust(w) if i == 4 else cell.ljust(w)
            for i, (cell, w) in enumerate(zip(row, widths, strict=True))
        ]
        print("  ".join(cells).rstrip())

    for name, errors in failures:
        print(f"\nFAIL  {name}")
        for error in errors:
            print(f"      - {error}")

    total_words = sum(int(r[4]) for r in rows)
    valid = len(rows) - len(failures)
    print(f"\n{valid}/{len(rows)} files valid, {total_words} words total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
