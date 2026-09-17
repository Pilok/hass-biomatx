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
again for the next ``a5``: at most one frame is lost per corrupted byte. A
frame that passes its checksum but carries a field the format cannot (button
index 10 or more, module 8...) is delivered as an ``InvalidFrame``: the
detectors emit one such frame on purpose (``a5 e8 03 80 84 4a``, module 4,
output 11) and the hub must be able to report it.
"""

from __future__ import annotations

from functools import reduce
import logging
from operator import xor
from typing import ClassVar

from .frames import Codec, EventFrame, Frame, InvalidFrame, Protocol, StateFrame
from .model import BUTTONS_PER_MODULE, MAX_MODULE_ADDRESS, SCENARIO_MODULE_ADDRESS

_LOGGER = logging.getLogger(__name__)

START = 0xA5
TYPE_INDEX = 4
TYPE_STATE = 0x81
TYPE_EVENT = 0x84
FRAME_LENGTHS = {TYPE_STATE: 9, TYPE_EVENT: 6}
CHECKSUM_INDEX = 1

STATE_MODULE_FLAG = 0x40
RELAYS_HIGH_MASK = 0x03
"""Bits of the last state byte that exist: relays 9 and 10."""
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

    def reset(self) -> None:
        """Drop a partially received frame; the next ``a5`` opens a new one."""
        self._buffer.clear()

    @property
    def in_frame(self) -> bool:
        """Return whether bytes of an unfinished frame are buffered."""
        return bool(self._buffer)

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
            _LOGGER.debug("unknown frame type in %s, resynchronising", buffer.hex(" "))
            self._resync(frames)
        elif len(buffer) == length:
            self._complete(frames)

    def _complete(self, frames: list[Frame]) -> None:
        frame = bytes(self._buffer)
        if xor_checksum(frame) != 0:
            self._stats.checksum_errors += 1
            _LOGGER.debug("checksum error in %s, resynchronising", frame.hex(" "))
            self._resync(frames)
            return
        self._buffer.clear()
        decoded = (
            _decode_state(frame)
            if frame[TYPE_INDEX] == TYPE_STATE
            else _decode_event(frame)
        )
        if isinstance(decoded, InvalidFrame):
            self._stats.invalid_frames += 1
            _LOGGER.debug(
                "frame %s has fields the format cannot carry: %s",
                frame.hex(" "),
                decoded.reason,
            )
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
        self._check_addresses(target, button, emitter)
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


def _decode_state(frame: bytes) -> StateFrame | InvalidFrame:
    module_byte = frame[3]
    module = module_byte & MODULE_MASK
    if module_byte & ~MODULE_MASK != STATE_MODULE_FLAG:
        return InvalidFrame(frame, "module byte without its flag in a state report")
    if module == SCENARIO_MODULE_ADDRESS:
        return InvalidFrame(
            frame, "state report from the scenario module", target=module
        )
    if frame[8] & ~RELAYS_HIGH_MASK:
        return InvalidFrame(
            frame, "bits beyond relay 10 in a state report", target=module
        )
    return StateFrame(
        module=module,
        relays=frame[7] | frame[8] << 8,
        reserved=frame[5] << 8 | frame[6],
    )


def _decode_event(frame: bytes) -> EventFrame | InvalidFrame:
    target, emitter_byte, code = frame[2], frame[3], frame[5]
    button = code & BUTTON_MASK
    pressed = bool(code & PRESSED_FLAG)
    emitter_ok = emitter_byte & ~MODULE_MASK == EVENT_EMITTER_FLAG
    emitter = emitter_byte & MODULE_MASK if emitter_ok else None
    if target > MAX_MODULE_ADDRESS:
        return InvalidFrame(
            frame,
            "target module out of range",
            target=target,
            emitter=emitter,
            button=button,
            pressed=pressed,
        )
    if not emitter_ok:
        return InvalidFrame(
            frame,
            "emitter byte without its flag",
            target=target,
            button=button,
            pressed=pressed,
        )
    if code & ~(PRESSED_FLAG | BUTTON_MASK):
        return InvalidFrame(
            frame, "unknown bits in the event code", target=target, emitter=emitter
        )
    if button >= BUTTONS_PER_MODULE:
        return InvalidFrame(
            frame,
            "button out of range",
            target=target,
            emitter=emitter,
            button=button,
            pressed=pressed,
        )
    return EventFrame(
        target=target,
        emitter=emitter_byte & MODULE_MASK,
        button=button,
        pressed=pressed,
    )
