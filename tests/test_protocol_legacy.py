"""
Tests for the legacy (two-byte) codec, moved out of ``hub.py`` unchanged.

The legacy path is frozen: these tests pin its behaviour so the V2 work cannot
alter it. Frames come from the 2026-09-08 capture (``frames.py``).
"""

from __future__ import annotations

import pytest

from custom_components.biomatx.protocol.frames import (
    EventFrame,
    ParserStats,
    Protocol,
)
from custom_components.biomatx.protocol.legacy import LegacyCodec

from . import frames


def feed(codec: LegacyCodec, *hex_frames: str) -> list[EventFrame]:
    """Feed hex strings to the codec in one read and return the decoded frames."""
    return codec.feed(bytes.fromhex(" ".join(hex_frames)))


def test_codec_declares_a_protocol_without_state_reports() -> None:
    """Legacy modules never report state; the hub must keep inferring it."""
    codec = LegacyCodec()
    assert codec.protocol is Protocol.LEGACY
    assert codec.reports_state is False
    assert codec.stats == ParserStats()


def test_press_and_release_decode_with_emitter_equal_to_target() -> None:
    """``50 00`` is module 0 button 0 pressed, ``50 80`` released."""
    frames_out = feed(LegacyCodec(), frames.PRESS_M1_R1, frames.RELEASE_M1_R1)
    assert frames_out == [
        EventFrame(target=0, emitter=0, button=0, pressed=True),
        EventFrame(target=0, emitter=0, button=0, pressed=False),
    ]


def test_cross_module_frame_reads_emitter_from_byte_1_and_target_from_byte_2() -> None:
    """The corridor detector: emitted by module 1, targets module 0 relay 7."""
    (frame,) = feed(LegacyCodec(), frames.DETECTOR_CORRIDOR_PRESS)
    assert frame == EventFrame(target=0, emitter=1, button=7, pressed=True)


def test_scenario_frame_targets_module_7() -> None:
    """``57 75``: scenario button 5 on the scenario module."""
    (frame,) = feed(LegacyCodec(), frames.SCENARIO_6_PRESS)
    assert frame == EventFrame(target=7, emitter=7, button=5, pressed=True)


def test_alternate_start_nibble_is_accepted() -> None:
    """Frames starting with ``0xA`` decode like ``0x5`` frames."""
    (frame,) = feed(LegacyCodec(), frames.ALT_START_NIBBLE_PRESS)
    assert frame == EventFrame(target=0, emitter=0, button=0, pressed=True)


def test_button_above_nine_is_invalid_and_counted() -> None:
    """Collision garbage decodes to button 10: no such button, nothing delivered."""
    codec = LegacyCodec()
    assert feed(codec, frames.COLLISION_INVALID_SWITCH) == []
    assert codec.stats == ParserStats(invalid_frames=1)


def test_emitter_above_seven_is_invalid() -> None:
    """``58 00`` names emitter 8: the bus has eight addresses, 0 to 7."""
    codec = LegacyCodec()
    assert feed(codec, "58 00") == []
    assert codec.stats.invalid_frames == 1


def test_bytes_outside_a_frame_are_noise() -> None:
    """Anything that is not a start byte while idle is counted, never decoded."""
    codec = LegacyCodec()
    frames_out = feed(codec, "00 ff 12", frames.PRESS_M1_R1)
    assert frames_out == [EventFrame(target=0, emitter=0, button=0, pressed=True)]
    assert codec.stats == ParserStats(frames=1, noise_bytes=3)


def test_orphan_start_byte_desyncs_one_frame_then_resyncs() -> None:
    """A lone start byte swallows the next frame; the following one is decoded."""
    codec = LegacyCodec()
    frames_out = feed(
        codec, frames.ORPHAN_START_BYTE, frames.PRESS_M1_R1, frames.PRESS_M2_R8
    )
    # "50 50" decodes as module 5 button 0 from emitter 0: the hub, not the
    # codec, knows module 5 is not configured. The stray "00" is noise.
    assert frames_out == [
        EventFrame(target=5, emitter=0, button=0, pressed=True),
        EventFrame(target=1, emitter=1, button=7, pressed=True),
    ]
    assert codec.stats == ParserStats(frames=2, noise_bytes=1)


def test_frame_split_across_two_reads_is_decoded() -> None:
    """At 19200 baud the two bytes often arrive in separate reads."""
    codec = LegacyCodec()
    assert codec.feed(bytes.fromhex(frames.PRESS_M1_R1[:2])) == []
    assert codec.feed(bytes.fromhex(frames.PRESS_M1_R1[3:])) == [
        EventFrame(target=0, emitter=0, button=0, pressed=True)
    ]


@pytest.mark.parametrize(
    ("target", "button", "pressed", "expected"),
    [
        (0, 0, True, frames.PRESS_M1_R1),
        (0, 0, False, frames.RELEASE_M1_R1),
        (1, 7, True, frames.PRESS_M2_R8),
        (1, 7, False, frames.RELEASE_M2_R8),
        (3, 9, True, frames.PRESS_M4_R10),
        (7, 5, True, frames.SCENARIO_6_PRESS),
        (7, 5, False, frames.SCENARIO_6_RELEASE),
    ],
)
def test_encode_button_reproduces_the_captured_frames(
    target: int,
    button: int,
    pressed: bool,  # noqa: FBT001  # parametrized test value
    expected: str,
) -> None:
    """Commands are front-panel presses: ``0x50 | module`` then the target byte."""
    encoded = LegacyCodec().encode_button(target, button, pressed=pressed)
    assert encoded.hex(" ") == expected


def test_encode_button_with_an_explicit_emitter_reproduces_detector_frames() -> None:
    """``51 07``: emitted by module 1, targets module 0 button 7."""
    encoded = LegacyCodec().encode_button(0, 7, pressed=True, emitter=1)
    assert encoded.hex(" ") == frames.DETECTOR_CORRIDOR_PRESS
