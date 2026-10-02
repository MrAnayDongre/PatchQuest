"""Unified-diff parsing and context-verified application.

The applier never trusts line numbers: every hunk's context and removed lines must
match the file exactly (trailing whitespace is tolerated), searched outward from the
line number the diff claims. A hunk that cannot be located is an error, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NULL = "/dev/null"
# A hunk is never allowed to drift further than this from the line it claims.
MAX_HUNK_DRIFT = 200


class PatchApplyError(ValueError):
    """A diff could not be applied cleanly."""


@dataclass
class Hunk:
    old_start: int
    old_lines: list[str] = field(default_factory=list)  # context + removed, in order
    new_lines: list[str] = field(default_factory=list)  # context + added, in order
    no_newline_at_eof: bool = False


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return self.old_path is None

    @property
    def is_delete(self) -> bool:
        return self.new_path is None

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _clean_path(raw: str) -> str | None:
    raw = raw.split("\t")[0].strip()
    if raw == _NULL:
        return None
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    return raw


def _is_file_header(lines: list[str], i: int) -> bool:
    return (
        lines[i].startswith("--- ")
        and i + 1 < len(lines)
        and lines[i + 1].startswith("+++ ")
    )


def parse_unified_diff(text: str) -> list[FilePatch]:
    """Parse a unified diff (git or plain ``diff -u`` style) into per-file patches."""
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()

    patches: list[FilePatch] = []
    i = 0
    while i < len(lines):
        if not _is_file_header(lines, i):
            i += 1
            continue
        patch = FilePatch(_clean_path(lines[i][4:]), _clean_path(lines[i + 1][4:]))
        i += 2
        while i < len(lines) and not _is_file_header(lines, i) and not lines[i].startswith("diff "):
            m = _HUNK_RE.match(lines[i])
            if not m:
                i += 1
                continue
            hunk = Hunk(old_start=int(m.group(1)))
            i += 1
            while i < len(lines):
                line = lines[i]
                if line.startswith("@@") or line.startswith("diff ") or _is_file_header(lines, i):
                    break
                if line.startswith("\\"):  # "\ No newline at end of file"
                    hunk.no_newline_at_eof = True
                elif line.startswith("+"):
                    hunk.new_lines.append(line[1:])
                elif line.startswith("-"):
                    hunk.old_lines.append(line[1:])
                elif line.startswith(" "):
                    hunk.old_lines.append(line[1:])
                    hunk.new_lines.append(line[1:])
                elif line == "":  # models routinely drop the space on blank context lines
                    hunk.old_lines.append("")
                    hunk.new_lines.append("")
                else:
                    break
                i += 1
            patch.hunks.append(hunk)
        patches.append(patch)
    return patches


def _norm(line: str) -> str:
    return line.rstrip()


def _find_block(lines: list[str], block: list[str], hint: int) -> int | None:
    """Index where ``block`` matches ``lines`` closest to ``hint``; None when absent."""
    if not block:
        return max(0, min(hint, len(lines)))
    n, m = len(lines), len(block)
    if m > n:
        return None
    for compare in (lambda a, b: a == b, lambda a, b: _norm(a) == _norm(b)):
        best: int | None = None
        for start in range(0, n - m + 1):
            if all(compare(lines[start + k], block[k]) for k in range(m)):
                if best is None or abs(start - hint) < abs(best - hint):
                    best = start
                if start >= hint:  # candidates only get farther from here
                    break
        if best is not None and abs(best - hint) <= MAX_HUNK_DRIFT:
            return best
    return None


def apply_hunks(original: list[str], hunks: list[Hunk], path: str = "") -> list[str]:
    """Apply ``hunks`` to ``original`` (lines without terminators)."""
    result = list(original)
    delta = 0
    for number, hunk in enumerate(hunks, 1):
        hint = max(0, hunk.old_start - 1) + delta
        if not hunk.old_lines and hunk.old_start == 0:
            hint = 0  # pure insertion into an empty/new file
        at = _find_block(result, hunk.old_lines, hint)
        if at is None:
            preview = "\n".join(hunk.old_lines[:6])
            raise PatchApplyError(
                f"hunk {number} for {path or 'file'} does not match the current file "
                f"(expected near line {hunk.old_start}):\n{preview}"
            )
        result[at : at + len(hunk.old_lines)] = hunk.new_lines
        delta += len(hunk.new_lines) - len(hunk.old_lines)
    return result
