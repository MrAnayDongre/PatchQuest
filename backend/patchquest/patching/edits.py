"""Exact-match search/replace edits: the edit format small models get right most often.

Unified diffs need correct line numbers and counts, which small models routinely get wrong.
A search/replace edit only needs a snippet that already exists in the file, so it can be
verified deterministically before anything is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class EditError(ValueError):
    """An edit could not be applied unambiguously."""


@dataclass
class SearchReplace:
    path: str
    search: str
    replace: str
    replace_all: bool = False


@dataclass
class CreateFile:
    path: str
    content: str


@dataclass
class DeleteFile:
    path: str


@dataclass
class WriteFile:
    """Create or overwrite ``path`` with ``content`` (used to promote a verified workspace state)."""

    path: str
    content: str


Change = SearchReplace | CreateFile | DeleteFile | WriteFile


def _rstrip_lines(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _match_ignoring_indent(text: str, edit: SearchReplace) -> str | None:
    """Last resort: the search matches line-for-line once leading whitespace is ignored and it is
    unambiguous. The replacement is re-indented from the model's indentation to the file's."""
    needle = [ln.strip() for ln in edit.search.strip("\n").split("\n")]
    if not any(needle):
        return None
    src = text.split("\n")
    hits = [i for i in range(len(src) - len(needle) + 1) if [ln.strip() for ln in src[i : i + len(needle)]] == needle]
    if len(hits) != 1:
        return None
    at = hits[0]
    model_indent = _indent(edit.search.strip("\n").split("\n")[0])
    file_indent = _indent(src[at])
    new = edit.replace.strip("\n").split("\n") if edit.replace.strip("\n") else []
    new = [file_indent + ln[len(model_indent):] if ln.startswith(model_indent) and ln.strip() else ln for ln in new]
    return "\n".join(src[:at] + new + src[at + len(needle):])


def apply_search_replace(text: str, edit: SearchReplace) -> str:
    """Return ``text`` with ``edit`` applied, or raise :class:`EditError`.

    Matching order: exact; then ignoring trailing whitespace per line; then ignoring indentation (unique only). The snippet must be
    unique unless ``replace_all`` is set, so an under-specified edit fails instead of
    silently landing in the wrong place.
    """
    if not edit.search:
        raise EditError(f"empty search block for {edit.path}")

    count = text.count(edit.search)
    if count == 1 or (count > 1 and edit.replace_all):
        return text.replace(edit.search, edit.replace)
    if count > 1:
        raise EditError(
            f"search block matches {count} places in {edit.path}; add surrounding lines to make it unique"
        )

    # Whitespace-tolerant fallback: compare after stripping trailing spaces on every line.
    norm_text, norm_search = _rstrip_lines(text), _rstrip_lines(edit.search).strip("\n")
    count = norm_text.count(norm_search)
    if count == 0:
        reindented = _match_ignoring_indent(text, edit)
        if reindented is not None:
            return reindented
        raise EditError(f"search block not found in {edit.path}: {edit.search[:120]!r}")
    if count > 1 and not edit.replace_all:
        raise EditError(f"search block matches {count} places in {edit.path}")
    # Map the normalised match back onto the original text line by line.
    src_lines = text.split("\n")
    needle = norm_search.split("\n")
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(src_lines):
        window = [ln.rstrip() for ln in src_lines[i : i + len(needle)]]
        if window == needle and (not replaced or edit.replace_all):
            out.extend(edit.replace.strip("\n").split("\n") if edit.replace.strip("\n") else [])
            i += len(needle)
            replaced = True
        else:
            out.append(src_lines[i])
            i += 1
    return "\n".join(out)


def changes_from_model_output(data: dict[str, Any]) -> list[Change]:
    """Extract structured edits from a role response (``edits``/``create``/``delete`` keys)."""
    changes: list[Change] = []
    for item in data.get("edits") or []:
        if not isinstance(item, dict) or "path" not in item:
            raise EditError(f"malformed edit: {item!r}")
        changes.append(
            SearchReplace(
                path=str(item["path"]),
                search=str(item.get("search", "")),
                replace=str(item.get("replace", "")),
                replace_all=bool(item.get("replace_all", False)),
            )
        )
    for item in data.get("create") or []:
        if not isinstance(item, dict) or "path" not in item:
            raise EditError(f"malformed create: {item!r}")
        changes.append(CreateFile(str(item["path"]), str(item.get("content", ""))))
    for path in data.get("delete") or []:
        changes.append(DeleteFile(str(path)))
    return changes
