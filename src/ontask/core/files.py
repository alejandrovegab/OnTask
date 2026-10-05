"""Writing OnTask's own files: privately, and all-or-nothing.

Settings, statistics and the little signal files between OnTask's processes
say what someone works on and when, so they are readable by their owner only:
files 0600, OnTask's folder 0700.

A save goes to a temporary file that is private from the moment it exists
(`mkstemp` creates it 0600), is flushed to disk, and is then swapped into place
with `os.replace`. Nothing is ever briefly readable by other accounts, and a
crash leaves either the old file or the new one, never half of each.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

PRIVATE_DIR = 0o700


def ensure_private_dir(path: Path, tighten_existing: bool = False) -> None:
    """Create `path` owner-only if it is missing.

    An existing folder is only tightened when asked: OnTask's config can live
    anywhere ($ONTASK_CONFIG), and changing the permissions of a folder the
    user chose - their Documents, say - would be a surprise.
    """
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        # mkdir's mode is filtered through the umask; set it outright.
        _chmod_if_owned(path, PRIVATE_DIR)
    elif tighten_existing:
        _chmod_if_owned(path, PRIVATE_DIR)


def write_private(path: Path, text: str) -> None:
    """Replace `path` with `text` atomically, readable by this user only."""
    path = Path(path)
    ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_capped(path: Path, limit: int) -> str:
    """Read a text file, refusing one larger than `limit` bytes.

    OnTask's own files are small; one far past that is damaged or not ours,
    and reading it whole would only waste memory.
    """
    path = Path(path)
    size = path.stat().st_size
    if size > limit:
        raise ValueError(f"{path.name} is {size} bytes, over the {limit}-byte limit")
    return path.read_text(encoding="utf-8")


def _chmod_if_owned(path: Path, mode: int) -> None:
    if os.name == "nt":
        # %APPDATA% is already per-user; POSIX modes do not apply.
        return
    with contextlib.suppress(OSError):
        if path.stat().st_uid == os.getuid():
            os.chmod(path, mode)
