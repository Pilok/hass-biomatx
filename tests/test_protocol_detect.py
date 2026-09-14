"""Tests for protocol detection on a sample of bus traffic."""

from __future__ import annotations

import pytest

from custom_components.biomatx.protocol import Protocol, codec_for, detect
from custom_components.biomatx.protocol.legacy import LegacyCodec
from custom_components.biomatx.protocol.master import MasterCodec

from . import frames, frames_master as fm


def test_master_state_frame_detects_the_master_protocol() -> None:
    """One valid ``a5`` frame is enough: master modules report every 3 s."""
    assert detect(bytes.fromhex(fm.STATE_M1_ALL_OFF)) is Protocol.MASTER


def test_house_capture_detects_the_master_protocol() -> None:
    """The four-module bus at home, first seconds after reprogramming."""
    sample = bytes.fromhex(f"{fm.STATE_HOUSE_M4} {fm.STATE_HOUSE_M1}")
    assert detect(sample) is Protocol.MASTER


def test_legacy_frames_detect_the_legacy_protocol() -> None:
    """Two-byte frames and nothing else: old firmware."""
    sample = bytes.fromhex(f"{frames.PRESS_M1_R1} {frames.RELEASE_M1_R1}")
    assert detect(sample) is Protocol.LEGACY


def test_master_traffic_is_never_taken_for_legacy() -> None:
    """``a5 18`` would decode as a legacy frame; the master codec must win."""
    sample = bytes.fromhex(fm.STATE_HOUSE_M1)
    assert LegacyCodec().feed(sample) != []  # the trap is real
    assert detect(sample) is Protocol.MASTER


def test_master_frame_preceded_by_noise_still_detects_master() -> None:
    """Detection scans the whole sample, not only its first byte."""
    sample = bytes.fromhex(f"00 ff {fm.PRESS_M1_R1}")
    assert detect(sample) is Protocol.MASTER


@pytest.mark.parametrize(
    "sample",
    ["", "00 ff 12 34", fm.ORPHAN_START, fm.TRUNCATED_STATE, fm.STATE_M1_BAD_CHECKSUM],
)
def test_nothing_valid_detects_nothing(sample: str) -> None:
    """A silent or garbled bus gives no answer; the caller asks the user."""
    assert detect(bytes.fromhex(sample)) is None


def test_codec_for_returns_a_fresh_codec_of_the_protocol() -> None:
    """The hub builds its codec from the stored protocol."""
    assert isinstance(codec_for(Protocol.MASTER), MasterCodec)
    assert isinstance(codec_for(Protocol.LEGACY), LegacyCodec)
    assert codec_for(Protocol.MASTER) is not codec_for(Protocol.MASTER)


def test_protocol_values_are_the_strings_stored_in_config_entries() -> None:
    """Stable lowercase identifiers, round-trippable from entry data."""
    assert Protocol("master") is Protocol.MASTER
    assert Protocol("legacy") is Protocol.LEGACY
    assert Protocol.MASTER.value == "master"
