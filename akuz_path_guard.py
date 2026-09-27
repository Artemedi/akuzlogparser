"""Reject pre-existing redirecting cache/lock path components.

`Path.is_symlink()` alone does not identify Windows NTFS junctions.
This is a static guard, not atomic anti-TOCTOU containment.
"""
from __future__ import annotations
from pathlib import Path
import stat


def is_redirected_path(path: Path) -> bool:
    path = Path(path)
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
