"""
Codec of the "master" firmware: checksummed frames with state reports.

Every frame starts with ``a5``; byte 2 is a checksum such that the XOR of the
whole frame is zero. There is no length byte: the type byte (index 4) gives
the length. Decoded on the Enersol showroom bus and on the owner's bus on
2026-09-14.

State frame, 9 bytes, broadcast by every module every 3 s and within a second
of any change (timers included)::

    a5 <xor> 7f <0x40 | module> 81 01 00 <relays 1-8> <relays 9-10>

Event frame, 6 bytes, emitted on a button press or release and replayable as a
command (press, then release ~200 ms later)::

    a5 <xor> <target module> <0x80 | emitter module> 84 <code>

with ``code`` = ``0x40 | button`` when pressed, ``button`` when released.

The parser is a state machine fed one byte at a time, so the way the serial
layer splits its reads is irrelevant. A frame that fails its checksum or has
an unknown type is dropped and the bytes after its start byte are scanned
again for the next ``a5``: at most one frame is lost per corrupted byte.
"""

from __future__ import annotations

from functools import reduce
from operator import xor
from typing import ClassVar

from .frames import Codec, EventFrame, Frame, Protocol, StateFrame
from .model import BUTTONS_PER_MODULE, MAX_MODULE_ADDRESS

START = 0xA5
TYPE_INDEX = 4
TYPE_STATE = 0x81
TYPE_EVENT = 0x84
FRAME_LENGTHS = {TYPE_STATE: 9, TYPE_EVENT: 6}
CHECKSUM_INDEX = 1

BROADCAST = 0x7F
STATE_MODULE_FLAG = 0x40
STATE_RESERVED = (0x01, 0x00)
EVENT_EMITTER_FLAG = 0x80
PRESSED_FLAG = 0x40
BUTTON_MASK = 0x0F
MODULE_MASK = 0x07


def xor_checksum(data: bytes) -> int:
    """Return the XOR of every byte; zero for a well-formed frame."""
    return reduce(xor, data, 0)


class MasterCodec(Codec):
    """Decoder and encoder of the ``a5`` protocol."""

    protocol: ClassVar[Protocol] = Protocol.MASTER
    reports_state: ClassVar[bool] = True

    def __init__(self) -> None:
        """Start idle: no partial frame buffered."""
        super().__init__()
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        """Decode the frames completed by ``data``."""
        frames: list[Frame] = []
        for byte in data:
            self._feed_byte(byte, frames)
        return frames

    def _feed_byte(self, byte: int, frames: list[Frame]) -> None:
        buffer = self._buffer
        if not buffer:
            if byte == START:
                buffer.append(byte)
            else:
                self._stats.noise_bytes += 1
            return
        buffer.append(byte)
        if len(buffer) <= TYPE_INDEX:
            return
        length = FRAME_LENGTHS.get(buffer[TYPE_INDEX])
        if length is None:
            self._stats.unknown_types += 1
            self._resync(frames)
        elif len(buffer) == length:
            self._complete(frames)

    def _complete(self, frames: list[Frame]) -> None:
        frame = bytes(self._buffer)
        if xor_checksum(frame) != 0:
            self._stats.checksum_errors += 1
            self._resync(frames)
            return
        self._buffer.clear()
        decoded = (
            _decode_state(frame)
            if frame[TYPE_INDEX] == TYPE_STATE
            else _decode_event(frame)
        )
        if decoded is None:
            self._stats.invalid_frames += 1
        else:
            self._stats.frames += 1
            frames.append(decoded)

    def _resync(self, frames: list[Frame]) -> None:
        """Drop the start byte and scan the rest of the buffer for the next frame."""
        rest = bytes(self._buffer[1:])
        self._buffer.clear()
        self._stats.resyncs += 1
        for byte in rest:
            self._feed_byte(byte, frames)

    def encode_button(
        self, target: int, button: int, *, pressed: bool, emitter: int | None = None
    ) -> bytes:
        """Return the six-byte event frame that presses or releases a button."""
        body = bytes(
            (
                START,
                target,
                EVENT_EMITTER_FLAG | (target if emitter is None else emitter),
                TYPE_EVENT,
                (PRESSED_FLAG if pressed else 0) | button,
            )
        )
        return (
            body[:CHECKSUM_INDEX] + bytes((xor_checksum(body),)) + body[CHECKSUM_INDEX:]
        )


def _decode_state(frame: bytes) -> StateFrame | None:
    module_byte = frame[3]
    if module_byte & ~MODULE_MASK != STATE_MODULE_FLAG:
        return None
    return StateFrame(
        module=module_byte & MODULE_MASK,
        relays=frame[7] | frame[8] << 8,
        reserved=frame[5] << 8 | frame[6],
    )


def _decode_event(frame: bytes) -> EventFrame | None:
    target, emitter_byte, code = frame[2], frame[3], frame[5]
    button = code & BUTTON_MASK
    if (
        target > MAX_MODULE_ADDRESS
        or emitter_byte & ~MODULE_MASK != EVENT_EMITTER_FLAG
        or code & ~(PRESSED_FLAG | BUTTON_MASK)
        or button >= BUTTONS_PER_MODULE
    ):
        return None
    return EventFrame(
        target=target,
        emitter=emitter_byte & MODULE_MASK,
        button=button,
        pressed=bool(code & PRESSED_FLAG),
    )
