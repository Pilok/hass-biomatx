"""
Codec of the original firmware: two-byte button frames, no state reports.

Byte 1 is ``0x5e`` or ``0xAe`` where ``e`` is the **emitting** module (0-7).
Byte 2 is bit 7 released (1) / pressed (0), bits 4-6 the **target** module,
bits 0-3 the target button (0-9). Wall buttons and front panels emit frames
with emitter == target; a detector wired on one module and driving a relay of
another produces different module fields.

This path is frozen: fixes only, no features.
"""

from __future__ import annotations

from typing import ClassVar

from .frames import Codec, EventFrame, Frame, Protocol
from .model import BUTTONS_PER_MODULE, MAX_MODULE_ADDRESS

START_NIBBLES = (0x50, 0xA0)
"""High nibbles that open a frame; the low nibble is the emitting module."""
COMMAND_START = 0x50
RELEASED_BIT = 0x80
TARGET_SHIFT = 4
TARGET_MASK = 0x07
BUTTON_MASK = 0x0F
EMITTER_MASK = 0x0F


class LegacyCodec(Codec):
    """Decoder and encoder of the two-byte protocol."""

    protocol: ClassVar[Protocol] = Protocol.LEGACY
    reports_state: ClassVar[bool] = False

    def __init__(self) -> None:
        """Start idle: no start byte pending."""
        super().__init__()
        self._pending: int | None = None

    def reset(self) -> None:
        """Drop a pending start byte; the next byte opens a new frame or is noise."""
        self._pending = None

    def feed(self, data: bytes) -> list[Frame]:
        """Decode the two-byte frames completed by ``data``."""
        frames: list[Frame] = []
        for byte in data:
            if self._pending is None:
                if byte & 0xF0 in START_NIBBLES:
                    self._pending = byte
                else:
                    self._stats.noise_bytes += 1
                continue
            first, self._pending = self._pending, None
            frame = self._decode(first, byte)
            if frame is None:
                self._stats.invalid_frames += 1
            else:
                self._stats.frames += 1
                frames.append(frame)
        return frames

    @staticmethod
    def _decode(first: int, second: int) -> EventFrame | None:
        emitter = first & EMITTER_MASK
        button = second & BUTTON_MASK
        if emitter > MAX_MODULE_ADDRESS or button >= BUTTONS_PER_MODULE:
            return None
        return EventFrame(
            target=second >> TARGET_SHIFT & TARGET_MASK,
            emitter=emitter,
            button=button,
            pressed=not second & RELEASED_BIT,
        )

    def encode_button(
        self, target: int, button: int, *, pressed: bool, emitter: int | None = None
    ) -> bytes:
        """Return the two bytes a button press or release puts on the bus."""
        self._check_addresses(target, button, emitter)
        first = COMMAND_START | (target if emitter is None else emitter)
        second = (0 if pressed else RELEASED_BIT) | target << TARGET_SHIFT | button
        return bytes((first, second))
