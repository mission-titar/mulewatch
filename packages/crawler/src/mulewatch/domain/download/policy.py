"""PURE auto-download policy (spec download §6 — DECISION D4/D5).

PURE domain: no I/O, no repo, no ``NetworkStatus``. ``download_policy`` returns a
``DownloadVerdict`` (enum, not bool → explainability + future metric) from PRIMITIVES:
the ``target_id → status`` lookup is done by the APPLICATION (from the loaded ``targets``)
and passed as a bool/string, exactly as ``effective_coverage`` receives bools (the
domain never imports a port).

Guard order (spec §6): a non-``download`` is a conservative guard (DECISION D5:
never download — the application should not call the policy outside download, but we do
not crash); a ``complete`` target no longer needs the file; an already-downloaded hash
is deduplicated; a candidate that would push the filesystem below its free-space floor is
DEFERRED (the decision stays in the journal, retried when space frees up, spec §7);
otherwise we download.
"""

from enum import StrEnum


class DownloadVerdict(StrEnum):
    """Verdict of the auto-download policy (closed enum, spec §6)."""

    DOWNLOAD = "download"
    SKIP_COMPLETE = "skip_complete"
    SKIP_DEDUP = "skip_dedup"
    SKIP_DISK_CAP = "skip_disk_cap"


def download_policy(
    *,
    tier: str,
    target_status: str,
    already_downloaded: bool,
    free_bytes: int,
    outstanding_bytes: int,
    file_size: int,
    min_free_bytes: int,
) -> DownloadVerdict:
    """Decide the fate of a download candidate (spec §6). All branches tested.

    Both space terms are MEASURED, never declared (2026-09-13 spec §1). ``free_bytes`` = what
    the filesystem reports free; ``outstanding_bytes`` = what amuled's queue still has to
    transfer; ``file_size`` = the candidate's size; ``min_free_bytes`` = the configured floor,
    an inclusive MIN (landing exactly on it is allowed).

    Free space alone would be wrong: ten downloads totalling 20 GB with 2 GB written look cheap
    to the filesystem, which knows nothing of the 18 GB still coming.
    """
    if tier != "download":
        return DownloadVerdict.SKIP_COMPLETE  # conservative guard (DECISION D5)
    if target_status == "complete":
        return DownloadVerdict.SKIP_COMPLETE
    if already_downloaded:
        return DownloadVerdict.SKIP_DEDUP
    if free_bytes - outstanding_bytes - file_size < min_free_bytes:
        return DownloadVerdict.SKIP_DISK_CAP
    return DownloadVerdict.DOWNLOAD
