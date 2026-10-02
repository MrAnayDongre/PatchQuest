"""Verified, atomic patching (unified diffs and search/replace edits)."""

from patchquest.patching.edits import (
    Change,
    CreateFile,
    DeleteFile,
    EditError,
    SearchReplace,
    WriteFile,
    changes_from_model_output,
)
from patchquest.patching.engine import (
    PatchResult,
    apply_changes,
    changes_from_unified_diff,
    rollback,
    sha256_bytes,
)
from patchquest.patching.unified import PatchApplyError, parse_unified_diff

__all__ = [
    "Change", "CreateFile", "DeleteFile", "EditError", "PatchApplyError", "PatchResult",
    "SearchReplace", "WriteFile", "apply_changes", "changes_from_model_output", "changes_from_unified_diff",
    "parse_unified_diff", "rollback", "sha256_bytes",
]
