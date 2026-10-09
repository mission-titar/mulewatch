"""Maps the amuleapi payloads onto the ports' DTOs. An unknown key is never an error.

Typed JSON does not preserve the capture-all invariant on its own, so every key no structured
field consumes lands in ``raw_meta`` - except ``ecid``, a session-local id we never persist.
"""

import json
from typing import Any

from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation, fold_raw_meta
from mulewatch.ports.client_status import ChannelStatus, ClientStatus
from mulewatch.ports.mule_client import KadStatus, NetworkStatus
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry

_HASH_LENGTH = 32

# Result keys consumed by a structured field, hence EXCLUDED from raw_meta.
_MAPPED_KEYS = frozenset(
    {"hash", "name", "size_bytes", "sources", "media", "file_type", "alternate_names"}
)
_MAPPED_MEDIA_KEYS = frozenset({"duration_seconds", "bitrate_kilobits_per_second", "codec"})

# Kad states with an equivalent in the port's closed enum; anything else is OFF (§7.6).
_KAD_STATES = {"connected": KadStatus.CONNECTED, "connecting": KadStatus.RUNNING}


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


def map_download_entry(row: object) -> DownloadEntry | None:
    """A /downloads row, or ``None`` if the hash is unusable. Reads ``completed_bytes``, what
    is on disk, never ``transferred_bytes``, which counts wire bytes corruption then discarded."""
    if not isinstance(row, dict):
        return None
    ed2k_hash = _hash_hex(row.get("hash"))
    if ed2k_hash is None:
        return None
    return DownloadEntry(
        ed2k_hash=ed2k_hash,
        size_done=_int(row.get("completed_bytes")),
        size_full=_int(row.get("size_bytes")),
    )


def map_shared_entry(row: object) -> SharedFileEntry | None:
    """A /shared row, hash only, or ``None`` if that hash is unusable."""
    if not isinstance(row, dict):
        return None
    ed2k_hash = _hash_hex(row.get("hash"))
    return None if ed2k_hash is None else SharedFileEntry(ed2k_hash=ed2k_hash)


def map_network_status(payload: object) -> NetworkStatus:
    """A /status body → ``NetworkStatus``. Never raises: a missing field degrades."""
    body = _object(payload)
    ed2k = _object(body.get("ed2k"))
    kad = _object(body.get("kad"))
    connected = ed2k.get("state") == "connected"
    user_id = ed2k.get("user_id")
    has_id = connected and isinstance(user_id, int) and not isinstance(user_id, bool)
    return NetworkStatus(
        # While disconnected the daemon reports user_id 0 and high_id false, so both are read
        # together with the state: "no id yet" must not read as a LowID (spec §6).
        ed2k_id=user_id if has_id else None,
        ed2k_high=connected and ed2k.get("high_id") is True,
        kad_status=_kad_status(kad),
        server_name=_string(ed2k.get("server_name")),
        server_addr=_server_addr(ed2k),
    )


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


def _kad_status(kad: dict[str, Any]) -> KadStatus:
    """An unrecognised state is a daemon configured differently, not an error: OFF."""
    reported = kad.get("state")
    state = _KAD_STATES.get(reported) if isinstance(reported, str) else None
    if state is None:
        return KadStatus.OFF
    if state is KadStatus.CONNECTED and kad.get("firewalled_tcp") is True:
        return KadStatus.FIREWALLED
    return state


def _server_addr(ed2k: dict[str, Any]) -> str | None:
    """``"a.b.c.d:port"``, the shape already in the catalog, or ``None`` if incomplete."""
    ip = _string(ed2k.get("server_ip"))
    port = _optional_int(ed2k.get("server_port"))
    return None if ip is None or port is None else f"{ip}:{port}"
