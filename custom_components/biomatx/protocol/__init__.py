"""
Bus protocols of the BioMatX 2110 modules, without any Home Assistant import.

Two firmwares exist: the original one (``legacy``, two-byte frames, no state
reports) and the ``master`` one (checksummed frames, state reports every 3 s).
A bus runs one of them; ``detect`` tells which from a sample of its traffic.
"""

from __future__ import annotations

from .frames import Codec, EventFrame, Frame, ParserStats, Protocol, StateFrame
from .legacy import LegacyCodec
from .master import MasterCodec

__all__ = [
    "Codec",
    "EventFrame",
    "Frame",
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
    Return the protocol spoken in ``data``, or ``None`` if no frame is valid.

    The master codec is tried first: its frames carry a checksum, whereas a
    master frame such as ``a5 18 ...`` would pass for a legacy button frame.
    """
    if MasterCodec().feed(data):
        return Protocol.MASTER
    if LegacyCodec().feed(data):
        return Protocol.LEGACY
    return None
