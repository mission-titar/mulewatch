from mulewatch.domain.download.policy import DownloadVerdict, download_policy


def _verdict(
    *,
    tier: str = "download",
    target_status: str = "lost",
    already_downloaded: bool = False,
    free_bytes: int = 10_000,
    outstanding_bytes: int = 0,
    file_size: int = 100,
    min_free_bytes: int = 1_000,
) -> DownloadVerdict:
    return download_policy(
        tier=tier,
        target_status=target_status,
        already_downloaded=already_downloaded,
        free_bytes=free_bytes,
        outstanding_bytes=outstanding_bytes,
        file_size=file_size,
        min_free_bytes=min_free_bytes,
    )


def test_verdict_is_a_closed_enum() -> None:
    assert set(DownloadVerdict) == {
        DownloadVerdict.DOWNLOAD,
        DownloadVerdict.SKIP_COMPLETE,
        DownloadVerdict.SKIP_DEDUP,
        DownloadVerdict.SKIP_DISK_CAP,
    }


def test_nominal_lost_target_downloads() -> None:
    assert _verdict() is DownloadVerdict.DOWNLOAD


def test_non_download_tier_is_a_conservative_guard() -> None:
    # DECISION D5: conservative guard ("do not download") — never triggered in prod
    # (the application only passes tier=download decisions), but an out-of-contract caller
    # does not crash and downloads nothing.
    assert _verdict(tier="catalog") is DownloadVerdict.SKIP_COMPLETE
    assert _verdict(tier="notify") is DownloadVerdict.SKIP_COMPLETE


def test_complete_target_skips() -> None:
    assert _verdict(target_status="complete") is DownloadVerdict.SKIP_COMPLETE


def test_partial_and_poor_targets_still_download() -> None:
    assert _verdict(target_status="partial") is DownloadVerdict.DOWNLOAD
    assert _verdict(target_status="poor") is DownloadVerdict.DOWNLOAD


def test_already_downloaded_is_deduped() -> None:
    assert _verdict(already_downloaded=True) is DownloadVerdict.SKIP_DEDUP


def test_dedup_takes_precedence_over_the_disk_floor() -> None:
    # already downloaded AND under the floor → SKIP_DEDUP (nothing to re-download).
    assert (
        _verdict(already_downloaded=True, free_bytes=1_000, file_size=100)
        is DownloadVerdict.SKIP_DEDUP
    )


def test_a_candidate_that_would_break_the_floor_defers() -> None:
    assert (
        _verdict(free_bytes=1_050, outstanding_bytes=0, file_size=100, min_free_bytes=1_000)
        is DownloadVerdict.SKIP_DISK_CAP
    )


def test_landing_exactly_on_the_floor_is_allowed() -> None:
    # free - outstanding - size == min_free: allowed (the floor is an inclusive MIN).
    assert (
        _verdict(free_bytes=1_100, outstanding_bytes=0, file_size=100, min_free_bytes=1_000)
        is DownloadVerdict.DOWNLOAD
    )


def test_one_byte_under_the_floor_defers() -> None:
    assert (
        _verdict(free_bytes=1_099, outstanding_bytes=0, file_size=100, min_free_bytes=1_000)
        is DownloadVerdict.SKIP_DISK_CAP
    )


def test_outstanding_bytes_count_against_the_floor() -> None:
    # Free space alone would admit this candidate: 10 GB free, a 1 GB file, a 5 GB floor.
    # The 8 GB still coming for downloads already running is what refuses it.
    assert (
        _verdict(
            free_bytes=10_000,
            outstanding_bytes=8_000,
            file_size=1_000,
            min_free_bytes=5_000,
        )
        is DownloadVerdict.SKIP_DISK_CAP
    )


def test_complete_takes_precedence_over_dedup() -> None:
    # complete target: we skip for COMPLETE even if already downloaded (status wins the order).
    assert (
        _verdict(target_status="complete", already_downloaded=True) is DownloadVerdict.SKIP_COMPLETE
    )


def test_found_target_still_downloads_a_new_file() -> None:
    # Product invariant (spec search-simplification, Batch C): an already-"found" episode is
    # re-downloaded when a NEW file matches it (intended archival redundancy).
    # Only target_status == "complete" skips; "found" never does in PROD.
    verdict = download_policy(
        tier="download",
        target_status="found",
        already_downloaded=False,
        free_bytes=100_000_000_000,
        outstanding_bytes=0,
        file_size=100_000_000,
        min_free_bytes=10_737_418_240,
    )
    assert verdict is DownloadVerdict.DOWNLOAD
