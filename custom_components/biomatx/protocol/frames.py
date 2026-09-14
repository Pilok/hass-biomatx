"""
What the bus carries, independently of the firmware that carries it.

Two firmwares exist for the BioMatX 2110 modules. The original one ("legacy")
emits two-byte button frames and never reports state; the "master" one emits
checksummed frames that also report the state of every relay. Both are decoded
into the same two frame types so the hub can treat them alike.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar

from .model import BUTTONS_PER_MODULE, MAX_MODULE_ADDRESS


class Protocol(StrEnum):
    """Firmware family spoken on the bus; the value is stored in config entries."""

    LEGACY = "legacy"
    MASTER = "master"


@dataclass(frozen=True, slots=True)
class EventFrame:
    """A button pressed or released, on a module or on the scenario module."""

    target: int
    """0-based module whose button (relay or scenario) is concerned."""
    emitter: int
    """0-based module the physical button or detector is wired on."""
    button: int
    """0-based button index on the target module."""
    pressed: bool


@dataclass(frozen=True, slots=True)
class StateFrame:
    """The state of every relay of one module (master firmware only)."""

    module: int
    """0-based module address."""
    relays: int
    """Bitmap: bit ``n`` set when relay ``n`` (0-based) is on."""
    reserved: int = 0x0100
    """Two bytes of unknown meaning, constant so far; kept for diagnostics."""

    def is_on(self, relay: int) -> bool:
        """Return whether relay ``relay`` (0-based) is on."""
        return bool(self.relays >> relay & 1)


type Frame = EventFrame | StateFrame


@dataclass(slots=True)
class ParserStats:
    """Counters of a codec since it was created; exposed in diagnostics."""

    frames: int = 0
    """Frames decoded and delivered."""
    noise_bytes: int = 0
    """Bytes seen while waiting for a start byte."""
    checksum_errors: int = 0
    """Complete frames whose checksum did not match (collisions, lost bytes)."""
    invalid_frames: int = 0
    """Well-formed frames whose fields the format cannot carry (button 15...)."""
    unknown_types: int = 0
    """Frames whose type byte the codec does not know."""
    resyncs: int = 0
    """Times a start byte was dropped to look for the next one."""


class Codec(ABC):
    """Decoder and encoder of one protocol, fed byte by byte."""

    protocol: ClassVar[Protocol]
    reports_state: ClassVar[bool]
    """Whether modules broadcast their relay states on this protocol."""

    def __init__(self) -> None:
        """Start with no partial frame and zeroed counters."""
        self._stats = ParserStats()

    @property
    def stats(self) -> ParserStats:
        """Return a snapshot of the counters."""
        return replace(self._stats)

    @abstractmethod
    def feed(self, data: bytes) -> list[Frame]:
        """Consume ``data`` (any split of the stream) and return completed frames."""

    @abstractmethod
    def reset(self) -> None:
        """Forget a partially received frame (the link was reopened); keep counters."""

    @property
    @abstractmethod
    def in_frame(self) -> bool:
        """Return whether a frame has started and is still being received."""

    @staticmethod
    def _check_addresses(target: int, button: int, emitter: int | None) -> None:
        """Raise ``ValueError`` when a field does not fit its bits in the frame."""
        if not 0 <= target <= MAX_MODULE_ADDRESS:
            msg = f"target module {target} out of range 0-{MAX_MODULE_ADDRESS}"
            raise ValueError(msg)
        if not 0 <= button < BUTTONS_PER_MODULE:
            msg = f"button {button} out of range 0-{BUTTONS_PER_MODULE - 1}"
            raise ValueError(msg)
        if emitter is not None and not 0 <= emitter <= MAX_MODULE_ADDRESS:
            msg = f"emitter module {emitter} out of range 0-{MAX_MODULE_ADDRESS}"
            raise ValueError(msg)

    @abstractmethod
    def encode_button(
        self, target: int, button: int, *, pressed: bool, emitter: int | None = None
    ) -> bytes:
        """
        Return the frame that presses or releases ``button`` of module ``target``.

        ``emitter`` is the module the frame claims to come from; it defaults to
        the target, like a front-panel press.
        """
