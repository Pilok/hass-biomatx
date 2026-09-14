"""
Tests for the "master" firmware codec, without Home Assistant or a serial port.

Frames come from the captures of 2026-09-14 (``frames_master.py``). The parser
is fed byte by byte in production, so every behaviour is also checked with the
input split at arbitrary boundaries.
"""

from __future__ import annotations

import random

import pytest

from custom_components.biomatx.protocol.frames import (
    EventFrame,
    ParserStats,
    Protocol,
    StateFrame,
)
from custom_components.biomatx.protocol.master import (
    MasterCodec,
    xor_checksum,
)

from . import frames_master as fm


def feed(codec: MasterCodec, *hex_frames: str) -> list[EventFrame | StateFrame]:
    """Feed hex strings to the codec in one read and return the decoded frames."""
    return codec.feed(bytes.fromhex(" ".join(hex_frames)))


# --- checksum -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frame",
    [
        fm.STATE_M1_ALL_OFF,
        fm.STATE_M1_R2_R9_ON,
        fm.STATE_HOUSE_M4,
        fm.PRESS_M1_R1,
        fm.WALL_PRESS_M2_TO_M1_R5,
        fm.SCENARIO_1_PRESS,
    ],
)
def test_xor_of_a_captured_frame_is_zero(frame: str) -> None:
    """Byte 2 makes the XOR of the whole frame vanish: the checksum rule."""
    assert xor_checksum(bytes.fromhex(frame)) == 0


def test_xor_checksum_of_a_corrupted_frame_is_not_zero() -> None:
    """One flipped bit is caught."""
    assert xor_checksum(bytes.fromhex(fm.STATE_M1_BAD_CHECKSUM)) != 0


# --- state frames ---------------------------------------------------------------------


def test_state_frame_all_off_decodes_module_and_empty_bitmap() -> None:
    """``40`` is module index 0 (front panel "1"); no relay bit is set."""
    frames = feed(MasterCodec(), fm.STATE_M1_ALL_OFF)
    assert frames == [StateFrame(module=0, relays=0)]


def test_state_frame_bitmap_bit_n_is_relay_n() -> None:
    """Relay 2 (index 1) and relay 9 (index 8): bits 1 and 8, over both bytes."""
    (frame,) = feed(MasterCodec(), fm.STATE_M1_R2_R9_ON)
    assert isinstance(frame, StateFrame)
    assert frame.module == 0
    assert frame.relays == 0b1_0000_0010
    assert [frame.is_on(relay) for relay in range(10)] == [
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
    ]


def test_state_frames_of_the_house_decode_four_modules() -> None:
    """The four modules at home report ``40`` to ``43`` with their real states."""
    frames = feed(
        MasterCodec(),
        fm.STATE_HOUSE_M1,
        fm.STATE_HOUSE_M2,
        fm.STATE_HOUSE_M3,
        fm.STATE_HOUSE_M4,
    )
    assert frames == [
        StateFrame(module=0, relays=0b0000_0010),
        StateFrame(module=1, relays=0b0100_0100),
        StateFrame(module=2, relays=0b0000_0110),
        StateFrame(module=3, relays=0b11_1100_0000),
    ]


def test_state_frame_keeps_the_reserved_bytes_for_diagnostics() -> None:
    """Bytes ``01 00`` never changed on two installations; keep them observable."""
    (frame,) = feed(MasterCodec(), fm.STATE_M1_ALL_OFF)
    assert isinstance(frame, StateFrame)
    assert frame.reserved == 0x0100


def test_state_frame_with_a5_inside_is_decoded_whole() -> None:
    """``a5`` is only a start byte between frames, never inside one."""
    frames = feed(MasterCodec(), fm.STATE_M1_RELAYS_A5, fm.STATE_M2_ALL_OFF)
    assert frames == [
        StateFrame(module=0, relays=0xA5),
        StateFrame(module=1, relays=0),
    ]


# --- event frames ---------------------------------------------------------------------


def test_front_panel_press_and_release_decode() -> None:
    """Code ``40`` is button 1 pressed, ``00`` released; emitter == target."""
    frames = feed(MasterCodec(), fm.PRESS_M1_R1, fm.RELEASE_M1_R1)
    assert frames == [
        EventFrame(target=0, emitter=0, button=0, pressed=True),
        EventFrame(target=0, emitter=0, button=0, pressed=False),
    ]


def test_button_index_is_the_low_nibble_of_the_code() -> None:
    """``48`` / ``08`` is relay 9 (index 8)."""
    frames = feed(MasterCodec(), fm.PRESS_M1_R9, fm.RELEASE_M1_R9)
    assert frames == [
        EventFrame(target=0, emitter=0, button=8, pressed=True),
        EventFrame(target=0, emitter=0, button=8, pressed=False),
    ]


def test_wall_switch_frame_carries_target_and_emitter_apart() -> None:
    """A switch wired on module 2 driving module 1 relay 5: target 0, emitter 1."""
    (frame,) = feed(MasterCodec(), fm.WALL_PRESS_M2_TO_M1_R5)
    assert frame == EventFrame(target=0, emitter=1, button=4, pressed=True)


def test_scenario_frame_targets_module_7() -> None:
    """Scenario buttons live on the virtual module 7; ``07 ... 40`` is scenario 1."""
    frames = feed(MasterCodec(), fm.SCENARIO_1_PRESS, fm.SCENARIO_1_RELEASE)
    assert frames == [
        EventFrame(target=7, emitter=1, button=0, pressed=True),
        EventFrame(target=7, emitter=1, button=0, pressed=False),
    ]


def test_module_2_front_panel_uses_index_and_flagged_index() -> None:
    """``01 81``: target index 1, emitter ``0x80 | 1``."""
    (frame,) = feed(MasterCodec(), fm.PRESS_M2_R1)
    assert frame == EventFrame(target=1, emitter=1, button=0, pressed=True)


# --- encoding -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "emitter", "button", "pressed", "expected"),
    [
        (0, 0, 0, True, fm.PRESS_M1_R1),
        (0, 0, 0, False, fm.RELEASE_M1_R1),
        (0, 0, 1, True, fm.PRESS_M1_R2),
        (0, 0, 8, False, fm.RELEASE_M1_R9),
        (0, 1, 4, True, fm.WALL_PRESS_M2_TO_M1_R5),
        (7, 1, 0, True, fm.SCENARIO_1_PRESS),
    ],
)
def test_encode_button_reproduces_the_captured_frames(
    target: int,
    emitter: int,
    button: int,
    pressed: bool,  # noqa: FBT001  # parametrized test value
    expected: str,
) -> None:
    """A command is the event frame the module would emit, checksum included."""
    codec = MasterCodec()
    encoded = codec.encode_button(target, button, pressed=pressed, emitter=emitter)
    assert encoded.hex(" ") == expected


def test_encode_button_defaults_the_emitter_to_the_target() -> None:
    """Without an emitter the command looks like a front-panel press."""
    encoded = MasterCodec().encode_button(1, 0, pressed=True)
    assert encoded.hex(" ") == fm.PRESS_M2_R1


def test_encoded_frames_decode_back_to_the_same_event() -> None:
    """Round trip over every target, emitter and button the bus can address."""
    codec = MasterCodec()
    for target in range(8):
        for emitter in range(8):
            for button in range(10):
                for pressed in (True, False):
                    data = codec.encode_button(
                        target, button, pressed=pressed, emitter=emitter
                    )
                    assert codec.feed(data) == [
                        EventFrame(target, emitter, button, pressed)
                    ]


# --- rejection, resynchronisation, counters -------------------------------------------


def test_new_codec_has_zeroed_stats_and_declares_itself() -> None:
    """The codec reports its protocol, that modules report state, and no traffic yet."""
    codec = MasterCodec()
    assert codec.protocol is Protocol.MASTER
    assert codec.reports_state is True
    assert codec.stats == ParserStats()


def test_valid_frames_are_counted() -> None:
    """Each decoded frame increments ``frames``; nothing else moves."""
    codec = MasterCodec()
    feed(codec, fm.STATE_M1_ALL_OFF, fm.PRESS_M1_R1)
    assert codec.stats == ParserStats(frames=2)


def test_bad_checksum_rejects_the_frame_and_counts_it() -> None:
    """A collision produces a frame whose XOR is not zero: nothing is decoded."""
    codec = MasterCodec()
    frames = feed(codec, fm.STATE_M1_BAD_CHECKSUM)
    assert frames == []
    assert codec.stats.checksum_errors == 1
    assert codec.stats.frames == 0


def test_bad_checksum_does_not_delay_the_following_frame() -> None:
    """After a rejected frame the parser resynchronises on the next ``a5``."""
    codec = MasterCodec()
    frames = feed(codec, fm.EVENT_BAD_CHECKSUM, fm.STATE_M2_ALL_OFF, fm.PRESS_M1_R1)
    assert frames == [
        StateFrame(module=1, relays=0),
        EventFrame(target=0, emitter=0, button=0, pressed=True),
    ]
    assert codec.stats.checksum_errors == 1
    assert codec.stats.resyncs == 1
    assert codec.stats.frames == 2


def test_unknown_type_byte_is_counted_and_skipped() -> None:
    """A frame type the codec does not know is dropped, the stream continues."""
    codec = MasterCodec()
    frames = feed(codec, fm.UNKNOWN_TYPE, fm.STATE_M1_ALL_OFF)
    assert frames == [StateFrame(module=0, relays=0)]
    assert codec.stats.unknown_types == 1
    assert codec.stats.resyncs == 1


def test_truncated_frame_costs_at_most_itself() -> None:
    """A frame cut on the wire is lost; the very next complete frame is decoded."""
    codec = MasterCodec()
    frames = feed(codec, fm.TRUNCATED_STATE, fm.STATE_M1_ALL_OFF, fm.PRESS_M1_R1)
    assert frames == [
        StateFrame(module=0, relays=0),
        EventFrame(target=0, emitter=0, button=0, pressed=True),
    ]
    assert codec.stats.checksum_errors == 1
    assert codec.stats.frames == 2


def test_orphan_start_byte_costs_no_frame() -> None:
    """A lone ``a5`` is dropped at the type byte; the frame after it is decoded."""
    codec = MasterCodec()
    frames = feed(codec, fm.ORPHAN_START, fm.STATE_M1_ALL_OFF, fm.PRESS_M2_R1)
    assert frames == [
        StateFrame(module=0, relays=0),
        EventFrame(target=1, emitter=1, button=0, pressed=True),
    ]
    assert codec.stats.frames == 2
    assert codec.stats.unknown_types == 1
    assert codec.stats.resyncs == 1


def test_bytes_outside_a_frame_are_counted_as_noise() -> None:
    """Line noise and legacy two-byte frames are neither frames nor errors."""
    codec = MasterCodec()
    frames = feed(
        codec,
        "00 ff 12",
        fm.LEGACY_PRESS_M1_R1,
        fm.LEGACY_RELEASE_M1_R1,
        fm.PRESS_M1_R1,
    )
    assert frames == [EventFrame(target=0, emitter=0, button=0, pressed=True)]
    assert codec.stats.noise_bytes == 7
    assert codec.stats.checksum_errors == 0


def test_event_with_impossible_button_is_invalid_not_delivered() -> None:
    """Valid checksum but button index 15: the format cannot mean anything."""
    codec = MasterCodec()
    frames = feed(codec, fm.EVENT_INVALID_BUTTON, fm.PRESS_M1_R1)
    assert frames == [EventFrame(target=0, emitter=0, button=0, pressed=True)]
    assert codec.stats.invalid_frames == 1
    assert codec.stats.frames == 1


def test_state_with_unflagged_module_byte_is_invalid() -> None:
    """The module byte always carries ``0x40``; anything else is not a state."""
    codec = MasterCodec()
    assert feed(codec, fm.STATE_INVALID_MODULE) == []
    assert codec.stats.invalid_frames == 1


@pytest.mark.parametrize(
    "hex_frame",
    [
        "a5 e9 08 80 84 40",  # target 8 does not exist
        "a5 65 00 04 84 40",  # emitter byte without the 0x80 flag
        "a5 21 00 80 84 80",  # code with a bit outside pressed/button
    ],
)
def test_malformed_event_fields_are_invalid(hex_frame: str) -> None:
    """Every fixed bit of an event frame is checked, not only the checksum."""
    codec = MasterCodec()
    assert xor_checksum(bytes.fromhex(hex_frame)) == 0
    assert feed(codec, hex_frame) == []
    assert codec.stats.invalid_frames == 1


def test_stats_are_independent_copies() -> None:
    """Reading the stats does not expose the codec's mutable state."""
    codec = MasterCodec()
    snapshot = codec.stats
    feed(codec, fm.STATE_M1_ALL_OFF)
    assert snapshot.frames == 0
    assert codec.stats.frames == 1


# --- replay of the whole hall capture -------------------------------------------------

HALL_STATES = 653
HALL_EVENTS = 22


def test_hall_capture_replays_without_a_single_error() -> None:
    """3 500 s of real bus, two modules, every test of the morning: all clean."""
    codec = MasterCodec()
    frames = codec.feed(fm.hall_capture())
    states = [frame for frame in frames if isinstance(frame, StateFrame)]
    events = [frame for frame in frames if isinstance(frame, EventFrame)]
    assert len(states) == HALL_STATES
    assert len(events) == HALL_EVENTS
    assert codec.stats == ParserStats(frames=HALL_STATES + HALL_EVENTS)


def test_hall_capture_events_follow_the_test_protocol() -> None:
    """The 11 press/release pairs of the morning, in order (T1, T1b, T1c, T4, T7)."""
    frames = MasterCodec().feed(fm.hall_capture())
    events = [frame for frame in frames if isinstance(frame, EventFrame)]
    presses = [(e.target, e.emitter, e.button) for e in events if e.pressed]
    releases = [(e.target, e.emitter, e.button) for e in events if not e.pressed]
    assert presses == releases
    assert presses == [
        (0, 0, 0),  # T1 front panel M1R1
        (0, 0, 1),  # T1b M1R2
        (0, 0, 8),  # T1b M1R9
        (1, 1, 0),  # T1c M2R1
        (0, 1, 4),  # T4 wall switch on module 2 driving M1R5
        (7, 1, 0),  # scenario button (all off)
        (0, 0, 0),  # T7 preparation
        (0, 0, 1),
        (0, 0, 2),
        (7, 1, 0),
        (7, 1, 0),  # T7 scenario
    ]


def test_hall_capture_state_follows_the_first_press_within_the_next_report() -> None:
    """T1: M1R1 pressed on the front panel, the next module 1 state shows it on."""
    frames = MasterCodec().feed(fm.hall_capture())
    first_press = next(
        i for i, f in enumerate(frames) if isinstance(f, EventFrame) and f.pressed
    )
    before = [f for f in frames[:first_press] if isinstance(f, StateFrame)]
    assert all(f.relays == 0 for f in before)
    after = next(
        f for f in frames[first_press:] if isinstance(f, StateFrame) and f.module == 0
    )
    assert after.relays == 0b1


def test_hall_capture_ends_with_every_relay_off() -> None:
    """T7 fired the all-off scenario; both modules then report zero."""
    frames = MasterCodec().feed(fm.hall_capture())
    last = {}
    for frame in frames:
        if isinstance(frame, StateFrame):
            last[frame.module] = frame.relays
    assert last == {0: 0, 1: 0}


def chunked(data: bytes, sizes: list[int]) -> list[bytes]:
    """Split ``data`` into chunks whose lengths cycle through ``sizes``."""
    chunks = []
    index = 0
    while index < len(data):
        size = sizes[len(chunks) % len(sizes)]
        chunks.append(data[index : index + size])
        index += size
    return chunks


@pytest.mark.parametrize(
    "sizes",
    [[1], [2], [3], [4], [5], [6], [7], [8], [9], [10], [11], [13], [64], [1, 2, 3, 5]],
)
def test_hall_capture_decodes_the_same_at_every_read_boundary(
    sizes: list[int],
) -> None:
    """The parser is a byte machine: how ``read()`` splits the stream is irrelevant."""
    data = fm.hall_capture()
    reference = MasterCodec()
    expected = reference.feed(data)
    codec = MasterCodec()
    frames = []
    for chunk in chunked(data, sizes):
        frames.extend(codec.feed(chunk))
    assert frames == expected
    assert codec.stats == reference.stats


def test_hall_capture_decodes_the_same_at_random_read_boundaries() -> None:
    """Random chunk sizes, fixed seed: same frames, same counters."""
    data = fm.hall_capture()
    expected = MasterCodec().feed(data)
    rng = random.Random(20260914)  # noqa: S311  # test data, not security
    sizes = [rng.randint(1, 40) for _ in range(500)]
    codec = MasterCodec()
    frames = []
    for chunk in chunked(data, sizes):
        frames.extend(codec.feed(chunk))
    assert frames == expected
    assert codec.stats.frames == HALL_STATES + HALL_EVENTS


def test_corrupted_stream_decodes_the_same_at_every_read_boundary() -> None:
    """Resynchronisation must not depend on read boundaries either."""
    data = bytes.fromhex(
        f"{fm.STATE_M1_ALL_OFF} {fm.EVENT_BAD_CHECKSUM} 00 00 {fm.TRUNCATED_STATE} "
        f"{fm.UNKNOWN_TYPE} {fm.ORPHAN_START} {fm.PRESS_M1_R1} "
        f"{fm.EVENT_INVALID_BUTTON} {fm.STATE_M2_ALL_OFF}"
    )
    reference = MasterCodec()
    expected = reference.feed(data)
    assert expected[-1] == StateFrame(module=1, relays=0)
    for size in range(1, len(data) + 1):
        codec = MasterCodec()
        frames = []
        for chunk in chunked(data, [size]):
            frames.extend(codec.feed(chunk))
        assert frames == expected, size
        assert codec.stats == reference.stats, size
