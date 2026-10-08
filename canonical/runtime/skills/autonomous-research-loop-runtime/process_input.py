"""Prepare finite prompt input without a pipe writer or background thread."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO


@contextmanager
def prepared_stdin(
    prompt: str | None,
    *,
    directory: str | os.PathLike[str],
) -> Iterator[BinaryIO | int]:
    """Yield UTF-8 prompt input for ``Popen(stdin=...)``, or ``DEVNULL``.

    The caller supplies an existing private directory and keeps this context
    open until its child has exited. Nonempty prompts use a temporary regular
    file, so slow or absent readers cannot block a pipe writer. The file is
    closed on context exit; on POSIX it has no directory entry while open.
    Child launch, timeout polling, output limits, and termination belong to
    the caller. The prompt is never placed in command arguments or environment.
    """
    if prompt is not None and not isinstance(prompt, str):
        raise TypeError("prompt must be text or None")
    if not prompt:
        yield subprocess.DEVNULL
        return
    if directory is None:
        raise ValueError("nonempty prompt requires an explicit private directory")
    with tempfile.TemporaryFile(mode="w+b", dir=directory) as stream:
        stream.write(prompt.encode("utf-8"))
        stream.seek(0)
        yield stream
