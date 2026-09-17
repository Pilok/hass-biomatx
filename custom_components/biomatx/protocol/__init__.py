"""
Bus protocols of the BioMatX 2110 modules, without any Home Assistant import.

Two firmwares exist: the original one (``legacy``, two-byte frames, no state
reports) and the ``master`` one (checksummed frames, state reports every 3 s).
A bus runs one of them; ``detect`` tells which from a sample of its traffic.
"""

from __future__ import annotations

from .frames import (
    Codec,
    EventFrame,
    Frame,
    InvalidFrame,
    InvalidReason,
    ParserStats,
    Protocol,
    StateFrame,
)
from .legacy import LegacyCodec
from .master import MasterCodec

__all__ = [
    "Codec",
    "EventFrame",
    "Frame",
    "InvalidFrame",
    "InvalidReason",
    "LegacyCodec",
    "MasterCodec",
    "ParserStats",
    "Protocol",
    "StateFrame",
    "codec_for",
    "detect",
]

_CODECS: dict[Protocol, type[Codec]] = {
    Protocol.LEGACY: LegacyCodec,
    Protocol.MASTER: MasterCodec,
}


def codec_for(protocol: Protocol) -> Codec:
    """Return a fresh codec for ``protocol``."""
    return _CODECS[protocol]()


def detect(data: bytes) -> Protocol | None:
    """
    Return the protocol spoken in ``data``, or ``None`` when nothing decides it.

    The master codec is tried first: its frames carry a checksum, whereas a
    master frame such as ``a5 18 ...`` would pass for a legacy button frame.
    Only a fully decoded master frame (a state report or an event) is proof:
    an ``InvalidFrame`` is a checksummed window whose fields the format cannot
    carry, which a legacy stream produces by chance about once in 256 windows,
    so it counts for nothing here; a real master bus reports every 3 s and
    decides by itself. For the same reason a sample that ends inside a master
    frame (a read that landed mid-frame) gives no verdict: the caller must wait
    for more bytes.
    """
    master = MasterCodec()
    if any(not isinstance(frame, InvalidFrame) for frame in master.feed(data)):
        return Protocol.MASTER
    if master.in_frame:
        return None
    if LegacyCodec().feed(data):
        return Protocol.LEGACY
    return None
