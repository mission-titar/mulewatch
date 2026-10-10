"""Maps the amuleapi payloads onto the ports' DTOs. An unknown key is never an error.

Typed JSON does not preserve the capture-all invariant on its own, so every key no structured
field consumes lands in ``raw_meta`` - except ``ecid``, a session-local id we never persist.
"""

import json
from typing import Any

from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation, fold_raw_meta
from mulewatch.ports.client_status import ChannelStatus, ClientStatus
from mulewatch.ports.download_client import DownloadStatus, FailureReason, WaitingReason

_HASH_LENGTH = 32

# Result keys consumed by a structured field, hence EXCLUDED from raw_meta.
_MAPPED_KEYS = frozenset(
    {"hash", "name", "size_bytes", "sources", "media", "file_type", "alternate_names"}
)
_MAPPED_MEDIA_KEYS = frozenset({"duration_seconds", "bitrate_kilobits_per_second", "codec"})

# amuleapi download statuses that say why a download waits, before its sources are read (D12).
# `waiting` is a local hash wait, not a remote queue; `completing` hashes then moves the file.
_WAITING_STATUSES = {
    "insufficient_disk": WaitingReason.DISK_FULL,
    "paused": WaitingReason.PAUSED,
    "stopped": WaitingReason.PAUSED,
    "waiting": WaitingReason.LOCAL,
    "hashing": WaitingReason.LOCAL,
    "allocating": WaitingReason.LOCAL,
    "completing": WaitingReason.LOCAL,
}


def map_search_results(results: object, keyword: str) -> tuple[tuple[FileObservation, ...], int]:
    """The ``results[]`` of a search → ``(observations, skipped_count)``."""
    observations: list[FileObservation] = []
    skipped = 0
    if not isinstance(results, list):
        return (), 0  # unexpected envelope: tolerated, ignored (not a result set)
    for result in results:
        mapped, result_skipped = _map_result(result, keyword)
        observations.extend(mapped)
        skipped += result_skipped
    return tuple(observations), skipped


def map_download_status(row: object) -> DownloadStatus | None:
    """A /downloads row, or ``None`` if the hash is unusable. Only ``completed`` completes: it
    is the one status amuleapi reserves for a file verified and moved (spec stage 2, D10)."""
    if not isinstance(row, dict):
        return None
    ed2k_hash = _hash_hex(row.get("hash"))
    if ed2k_hash is None:
        return None
    status = row.get("status")
    completed = status == "completed"
    failure = FailureReason.ERROR if status == "erroneous" else None
    waiting = None if completed or failure else _waiting_reason(status, row.get("sources"))
    return DownloadStatus(
        file=FileKey(Network.ED2K, ed2k_hash),
        bytes_done=_int(row.get("completed_bytes")),
        bytes_total=_int(row.get("size_bytes")),
        completed=completed,
        waiting_reason=waiting,
        failure_reason=failure,
    )


def map_shared_download(row: object) -> DownloadStatus | None:
    """A /shared row as a download completed then cleared, or ``None`` if the hash is unusable.
    Partfiles are shared too, so the caller reads it only for a file the queue no longer lists."""
    if not isinstance(row, dict):
        return None
    ed2k_hash = _hash_hex(row.get("hash"))
    if ed2k_hash is None:
        return None
    size = _int(row.get("size_bytes"))
    return DownloadStatus(FileKey(Network.ED2K, ed2k_hash), size, size, True, None, None)


def map_client_status(status: object, version: object) -> ClientStatus:
    """A /status and a /version body → ``ClientStatus``. Never raises: missing reads unknown."""
    body = _object(status)
    ed2k, kad = _object(body.get("ed2k")), _object(body.get("kad"))
    high_id, firewalled = ed2k.get("high_id"), kad.get("firewalled_tcp")
    channels = (
        _channel("ed2k", ed2k, high_id if isinstance(high_id, bool) else None),
        _channel("kad", kad, not firewalled if isinstance(firewalled, bool) else None),
    )
    return ClientStatus(version=_string(_object(version).get("daemon_version")), channels=channels)


def _channel(name: str, network: dict[str, Any], connectable: bool | None) -> ChannelStatus:
    """Off the network the flag tells nothing: there ``high_id`` false means "no id yet"."""
    on_network = network.get("state") == "connected"
    return ChannelStatus(name, on_network, connectable if on_network else None)


def _map_result(result: object, keyword: str) -> tuple[list[FileObservation], int]:
    """One result row → one observation PER FILENAME, plus the skipped count."""
    if not isinstance(result, dict):
        return [], 1
    ed2k_hash = _hash_hex(result.get("hash"))
    size_bytes = result.get("size_bytes")
    if ed2k_hash is None or not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        return [], 1
    media = _object(result.get("media"))
    raw_meta = _raw_meta(result, media)
    codec, file_type = _string(media.get("codec")), _string(result.get("file_type"))
    alternates = result.get("alternate_names")
    entries: list[object] = [result, *(alternates if isinstance(alternates, list) else [])]
    observations: list[FileObservation] = []
    skipped = 0
    for entry in entries:
        fields = _object(entry)
        name = _string(fields.get("name"))
        if name is None:
            skipped += 1
            continue
        total, complete = _sources(fields.get("sources"))
        observations.append(
            FileObservation(
                file=FileKey(Network.ED2K, ed2k_hash),
                filename=name,
                size_bytes=size_bytes,
                source_count=total,
                keyword=keyword,
                media_length_sec=_optional_int(media.get("duration_seconds")),
                bitrate_kbps=_optional_int(media.get("bitrate_kilobits_per_second")),
                raw_meta=fold_raw_meta(raw_meta, codec, file_type, complete),
            )
        )
    return observations, skipped


def _raw_meta(result: dict[str, Any], media: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Every unmapped key → ``(key, rendered_value)``, in wire order."""
    collected = [(key, _render(value)) for key, value in result.items() if key not in _MAPPED_KEYS]
    collected += [
        (f"media.{key}", _render(value))
        for key, value in media.items()
        if key not in _MAPPED_MEDIA_KEYS
    ]
    return tuple(collected)


def _render(value: object) -> str:
    """Rendering that NEVER raises: text as-is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    """A blank string reads as absent: an empty name is as unusable as a missing one."""
    return value if isinstance(value, str) and value else None


def _hash_hex(value: object) -> str | None:
    """Lowercased MD4 hash, or ``None``: without it the row has no identifier at all."""
    if not isinstance(value, str) or len(value) != _HASH_LENGTH:
        return None
    lowered = value.lower()
    return lowered if all(character in "0123456789abcdef" for character in lowered) else None


def _int(value: object) -> int:
    """Absent or malformed reads as 0, and ``bool`` is not a count though it is an ``int``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_int(value: object) -> int | None:
    """Keeps "the daemon reported nothing" distinct from a reported zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sources(value: object) -> tuple[int, int]:
    sources = _object(value)
    return _int(sources.get("total")), _int(sources.get("complete"))


def _waiting_reason(status: object, sources: object) -> WaitingReason | None:
    """The status's own reason first, then the sources: none at all, or none sending."""
    reason = _WAITING_STATUSES.get(status) if isinstance(status, str) else None
    if reason is not None:
        return reason
    counts = _object(sources)
    if _int(counts.get("total")) == 0:
        return WaitingReason.NO_SOURCE
    return WaitingReason.REMOTE_QUEUE if _int(counts.get("transferring")) == 0 else None
