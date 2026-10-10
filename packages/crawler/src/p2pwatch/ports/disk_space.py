"""``DiskSpace`` port: free space on the filesystem amuled writes to (disk-cap spec §1).

PORTS layer. The admission rule needs a MEASURED free-space figure, and ``statvfs`` is I/O:
the pure ``download_policy`` receives the number, it never reads it. Reading filesystem
metadata is not reading a file's bytes, so the "the crawler never reads the downloaded bytes"
invariant holds, and the mount that backs this is read-only. Stub on ONE line.
"""

from typing import Protocol


class DiskSpace(Protocol):
    def free_bytes(self) -> int: ...
