"""
Unit tests for the serial hub on the master protocol, without Home Assistant.

Master modules report the state of their relays every 3 s and after every
change, so the hub stops inferring: relay state comes from the bus, a command
is confirmed by the state report that follows it, and a silent module becomes
unavailable. Frames come from the 2026-09-14 captures (``frames_master.py``).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from custom_components.biomatx.hub import (
    BiomatxCommandError,
    BiomatxHub,
    BiomatxLinkError,
    BiomatxModuleUnavailableError,
    BiomatxNotSupportedError,
    BiomatxProtocolUnknownError,
)
from custom_components.biomatx.protocol import ParserStats, Protocol
from custom_components.biomatx.protocol.master import MasterCodec

from . import frames as legacy, frames_master as fm
from .conftest import URL
from .fake_serial import FakeSerialLink, settle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

CONFIRM_TIMEOUT = 0.2
MODULE_TIMEOUT = 0.3
COMMAND_FRAME_LENGTH = 6


def make_hub(
    module_count: int = 4,
    all_off_address: int | None = 0,
    protocol: Protocol | None = Protocol.MASTER,
    frame_gap: float = 0,
) -> BiomatxHub:
    """Build a master hub with short timeouts, so tests run fast."""
    return BiomatxHub(
        URL,
        module_count,
        all_off_address,
        protocol=protocol,
        frame_gap=frame_gap,
        reconnect_delays=(0,),
        confirm_timeout=CONFIRM_TIMEOUT,
        module_timeout=MODULE_TIMEOUT,
    )


async def report(
    hub: BiomatxHub, fake_serial: FakeSerialLink, *hex_frames: str
) -> None:
    """Feed state reports and let the hub apply them."""
    fake_serial.feed(" ".join(hex_frames))
    await settle()
    assert hub.frames_received >= 1


async def run_hub(hub: BiomatxHub) -> asyncio.Task[None]:
    """Connect ``hub`` and start its reader loop in the background."""
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    return task


async def stop_hub(hub: BiomatxHub, task: asyncio.Task[None]) -> None:
    """Close ``hub`` and wait for its reader loop to end."""
    await hub.async_close()
    await asyncio.wait_for(task, timeout=2)


@pytest.fixture
async def running(fake_serial: FakeSerialLink) -> AsyncIterator[BiomatxHub]:
    """Provide a connected master hub whose reader loop runs in the background."""
    hub = make_hub()
    task = await run_hub(hub)
    yield hub
    await stop_hub(hub, task)


def recorder(events: list[str], label: str) -> Callable[[], None]:
    """Return a listener that appends ``label`` to ``events``."""

    def _record() -> None:
        events.append(label)

    return _record


def command(
    target: int, button: int, *, pressed: bool, emitter: int | None = None
) -> str:
    """Return the hex of the master command frame the hub is expected to write."""
    codec = MasterCodec()
    return codec.encode_button(target, button, pressed=pressed, emitter=emitter).hex(
        " "
    )


# --- state reports -----------------------------------------------------------------


async def test_hub_declares_its_protocol_and_state_reports() -> None:
    """A master hub knows its protocol before any byte arrives."""
    hub = make_hub()
    assert hub.protocol is Protocol.MASTER
    assert hub.reports_state is True
    assert hub.stats == ParserStats()


async def test_state_report_sets_relays_and_notifies_only_the_changed_ones(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Module 2 reports relays 3 and 7 on: those two flip, nothing else moves."""
    fake_serial.feed(fm.STATE_M2_ALL_OFF)  # first report: the module becomes available
    await settle()
    events: list[str] = []
    running.add_listener(("relay", 1, 2), recorder(events, "m2r3"))
    running.add_listener(("relay", 1, 6), recorder(events, "m2r7"))
    running.add_listener(("relay", 1, 0), recorder(events, "m2r1"))
    fake_serial.feed(fm.STATE_HOUSE_M2)
    await settle()
    assert running.relay(1, 2).on is True
    assert running.relay(1, 6).on is True
    assert running.relay(1, 0).on is False
    assert sorted(events) == ["m2r3", "m2r7"]
    fake_serial.feed(fm.STATE_HOUSE_M2)
    await settle()
    assert sorted(events) == ["m2r3", "m2r7"]  # an identical report changes nothing
    assert running.frames_received == 3


async def test_state_report_turning_a_relay_off_notifies_it(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A timer expiry shows up as a state report: the relay follows, no event."""
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    events: list[str] = []
    running.add_listener(("relay", 0, 0), recorder(events, "m1r1"))
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert running.relay(0, 0).on is False
    assert events == ["m1r1"]


async def test_module_is_unavailable_until_its_first_state_report(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Nothing is known about a module that has not reported yet."""
    assert running.module_available(0) is False
    assert running.module_available(1) is False
    assert running.module_last_seen(0) is None
    events: list[str] = []
    running.add_listener(("relay", 0, 9), recorder(events, "relay"))
    running.add_listener(("switch", 0, 3), recorder(events, "switch"))
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert running.module_available(0) is True
    assert running.module_available(1) is False
    assert running.module_last_seen(0) is not None
    # Availability is a change for every entity of the module, even unchanged relays.
    assert sorted(events) == ["relay", "switch"]


async def test_silent_module_becomes_unavailable_then_returns(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Three missed reports (10 s in production) mark the module unavailable."""
    fake_serial.feed(fm.STATE_M1_ALL_OFF + fm.STATE_M2_ALL_OFF)
    await settle()
    events: list[str] = []
    running.add_listener(("relay", 0, 0), recorder(events, "m1"))
    running.add_listener(("relay", 1, 0), recorder(events, "m2"))
    fake_serial.feed(fm.STATE_M2_ALL_OFF)
    await asyncio.sleep(MODULE_TIMEOUT * 0.6)
    fake_serial.feed(fm.STATE_M2_ALL_OFF)  # module 2 keeps talking
    await asyncio.sleep(MODULE_TIMEOUT * 0.55)
    assert running.module_available(0) is False
    assert running.module_available(1) is True
    assert events == ["m1"]
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert running.module_available(0) is True
    assert events == ["m1", "m1"]


async def test_link_loss_makes_every_module_unavailable(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Without a link there is no bus to trust, however recent the last report."""
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert running.module_available(0) is True
    fake_serial.fail_open_always = OSError("unplugged")
    fake_serial.drop_link()
    await settle()
    assert running.connected is False
    assert running.module_available(0) is False


async def test_reconnection_forgets_module_availability_until_a_fresh_report(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """After an outage nothing is known again: no stale availability, no stale timer."""
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    fake_serial.drop_link()
    await settle(50)
    assert running.connected is True
    assert running.module_available(0) is False
    assert running._module_timers == {}
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    assert running.module_available(0) is True


async def test_close_right_after_a_report_leaves_no_timer_behind(
    fake_serial: FakeSerialLink,
) -> None:
    """Bytes decoded while closing must not arm a timer that fires on a dead hub."""
    hub = make_hub()
    task = await run_hub(hub)
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await stop_hub(hub, task)
    assert hub._module_timers == {}
    assert hub.module_available(0) is False


async def test_state_report_for_an_unconfigured_module_is_dropped(
    fake_serial: FakeSerialLink,
) -> None:
    """A two-module bus ignores a report from module 4, and counts it."""
    hub = make_hub(module_count=2)
    task = await run_hub(hub)
    fake_serial.feed(fm.STATE_HOUSE_M4)
    await settle()
    assert hub.frames_dropped == 1
    assert hub.frames_received == 0
    assert hub.module_available(1) is False
    await stop_hub(hub, task)


async def test_checksum_error_is_counted_and_the_stream_goes_on(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A collision is rejected by the checksum; the next report is decoded."""
    fake_serial.feed(fm.STATE_M1_BAD_CHECKSUM + fm.STATE_M2_ALL_OFF)
    await settle()
    assert running.stats.checksum_errors == 1
    assert running.frames_received == 1
    assert running.module_available(0) is False
    assert running.module_available(1) is True


async def test_reconnection_drops_a_half_received_frame(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A frame cut by the outage must not be glued to the new link's first bytes."""
    fake_serial.feed(fm.STATE_M1_ALL_OFF[:14])  # five bytes of a state report
    await settle()
    fake_serial.drop_link()
    await settle(50)
    assert running.connected is True
    fake_serial.feed(fm.STATE_M2_ALL_OFF)
    await settle()
    assert running.stats.checksum_errors == 0
    assert running.module_available(1) is True


# --- events --------------------------------------------------------------------------


async def test_event_updates_the_button_but_never_the_relay(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """On master the relay state comes from reports only; a press is just a press."""
    events: list[str] = []
    running.add_listener(("switch", 0, 0), recorder(events, "switch"))
    running.add_listener(("relay", 0, 0), recorder(events, "relay"))
    fake_serial.feed(fm.PRESS_M1_R1)
    await settle()
    assert running.switch(0, 0).pressed is True
    assert running.relay(0, 0).on is False
    assert events == ["switch"]
    fake_serial.feed(fm.RELEASE_M1_R1)
    await settle()
    assert running.switch(0, 0).pressed is False
    assert events == ["switch", "switch"]
    assert running.frames_received == 2


async def test_cross_module_event_updates_the_target_button(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A wall switch on module 2 driving module 1 relay 5 presses button (0, 4)."""
    fake_serial.feed(fm.WALL_PRESS_M2_TO_M1_R5)
    await settle()
    assert running.switch(0, 4).pressed is True
    assert running.switch(0, 4).emitter == 1
    assert running.switch(0, 4).events == 1
    assert running.switch(1, 4).pressed is False
    fake_serial.feed(fm.WALL_RELEASE_M2_TO_M1_R5)
    await settle()
    assert running.switch(0, 4).events == 2


async def test_scenario_module_is_available_whenever_the_link_is_up(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The virtual module never reports; its buttons live as long as the link does."""
    assert running.module_available(7) is True
    fake_serial.fail_open_always = OSError("unplugged")
    fake_serial.drop_link()
    await settle()
    assert running.module_available(7) is False


async def test_all_off_scenario_event_does_not_invent_state(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The modules will report zero within a second; the hub does not guess it."""
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    fake_serial.feed(fm.SCENARIO_1_PRESS + fm.SCENARIO_1_RELEASE)
    await settle()
    assert running.switch(7, 0).pressed is False
    assert running.relay(0, 0).on is True
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert running.relay(0, 0).on is False


async def test_event_for_an_unconfigured_module_is_dropped(
    fake_serial: FakeSerialLink,
) -> None:
    """Module 5 does not exist on a four-module bus."""
    hub = make_hub()
    task = await run_hub(hub)
    fake_serial.feed(command(4, 0, pressed=True))
    await settle()
    assert hub.frames_dropped == 1
    await stop_hub(hub, task)


# --- commands, never optimistic ----------------------------------------------------


async def test_set_relay_sends_press_release_and_waits_for_the_report(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The command goes out at once; the state changes when the module says so."""
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    task = asyncio.create_task(running.async_set_relay(running.relay(0, 0), on=True))
    await settle()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        fm.PRESS_M1_R1,
        fm.RELEASE_M1_R1,
    ]
    assert running.relay(0, 0).on is False
    assert not task.done()
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await asyncio.wait_for(task, timeout=1)
    assert running.relay(0, 0).on is True


async def test_report_arriving_during_the_frame_gap_confirms_the_command(
    fake_serial: FakeSerialLink,
) -> None:
    """The module answers within the press/release gap: that report must count."""
    hub = make_hub(frame_gap=0.05)
    task = await run_hub(hub)
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    cmd = asyncio.create_task(hub.async_set_relay(hub.relay(0, 0), on=True))
    await settle()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [fm.PRESS_M1_R1]
    fake_serial.feed(fm.STATE_M1_R1_ON)  # before the release is even written
    await asyncio.wait_for(cmd, timeout=1)
    assert hub.relay(0, 0).on is True
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        fm.PRESS_M1_R1,
        fm.RELEASE_M1_R1,
    ]
    await stop_hub(hub, task)


async def test_command_on_a_module_that_never_reported_is_refused(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A default or restored relay state is not a bus fact: do not act on it."""
    with pytest.raises(BiomatxModuleUnavailableError):
        await running.async_set_relay(running.relay(0, 0), on=False)
    with pytest.raises(BiomatxModuleUnavailableError):
        await running.async_toggle(running.relay(0, 0))
    assert fake_serial.frames_written() == []
    fake_serial.feed(fm.STATE_M2_ALL_OFF)  # another module reporting does not help
    await settle()
    with pytest.raises(BiomatxModuleUnavailableError):
        await running.async_set_relay(running.relay(0, 0), on=True)


async def test_link_loss_fails_a_pending_command_at_once(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Unplugging during a confirmation is a link error, not a two-second timeout."""
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    task = asyncio.create_task(running.async_set_relay(running.relay(0, 0), on=True))
    await settle()
    fake_serial.fail_open_always = OSError("unplugged")
    fake_serial.drop_link()
    with pytest.raises(BiomatxLinkError):
        await asyncio.wait_for(task, timeout=CONFIRM_TIMEOUT / 2)


async def test_set_relay_off_is_confirmed_by_a_report_without_the_bit(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Turning off waits for a report where the relay is off."""
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    task = asyncio.create_task(running.async_set_relay(running.relay(0, 0), on=False))
    await settle()
    assert running.relay(0, 0).on is True
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await asyncio.wait_for(task, timeout=1)
    assert running.relay(0, 0).on is False


async def test_set_relay_raises_when_no_report_confirms_it(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """No confirmation within the timeout is an error, and the state is not touched."""
    await report(running, fake_serial, fm.STATE_M1_ALL_OFF)
    with pytest.raises(BiomatxCommandError):
        await running.async_set_relay(running.relay(0, 0), on=True)
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        fm.PRESS_M1_R1,
        fm.RELEASE_M1_R1,
    ]
    assert running.relay(0, 0).on is False


async def test_set_relay_is_a_no_op_when_the_report_already_says_so(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Pressing an already-on relay would turn it off: send nothing."""
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    await running.async_set_relay(running.relay(0, 0), on=True)
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == []


async def test_confirmation_ignores_reports_of_other_modules_and_relays(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Only a report of the commanded module with the wanted bit confirms."""
    await report(running, fake_serial, fm.STATE_M1_ALL_OFF)
    task = asyncio.create_task(running.async_set_relay(running.relay(0, 0), on=True))
    await settle()
    fake_serial.feed(fm.STATE_M2_R1_ON + fm.STATE_M1_R5_ON)
    await settle()
    assert not task.done()
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await asyncio.wait_for(task, timeout=1)


async def test_toggle_is_confirmed_by_a_changed_report(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Toggle waits for the relay bit to differ from what it was."""
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    task = asyncio.create_task(running.async_toggle(running.relay(0, 0)))
    await settle()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        fm.PRESS_M1_R1,
        fm.RELEASE_M1_R1,
    ]
    fake_serial.feed(fm.STATE_M1_ALL_OFF)  # a report that did not change yet
    await settle()
    assert not task.done()
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await asyncio.wait_for(task, timeout=1)
    assert running.relay(0, 0).on is True


async def test_commands_are_serialised_until_confirmed(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The second command is not written before the first one is confirmed."""
    await report(running, fake_serial, fm.STATE_M1_ALL_OFF, fm.STATE_M2_ALL_OFF)
    first = asyncio.create_task(running.async_set_relay(running.relay(0, 0), on=True))
    second = asyncio.create_task(running.async_set_relay(running.relay(1, 0), on=True))
    await settle()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        fm.PRESS_M1_R1,
        fm.RELEASE_M1_R1,
    ]
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await asyncio.wait_for(first, timeout=1)
    await settle()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH)[2:] == [
        fm.PRESS_M2_R1,
        fm.RELEASE_M2_R1,
    ]
    fake_serial.feed(fm.STATE_M2_R1_ON)
    await asyncio.wait_for(second, timeout=1)


async def test_activate_scenario_sends_master_frames_as_module_1(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Scenario commands claim the first real module as emitter, like a wall button."""
    await running.async_activate_scenario(5)
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        command(7, 5, pressed=True, emitter=0),
        command(7, 5, pressed=False, emitter=0),
    ]


async def test_all_off_sends_the_scenario_and_leaves_state_to_the_bus(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The modules report zero by themselves; the hub does not mark relays off."""
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await settle()
    await running.async_all_off()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == [
        command(7, 0, pressed=True, emitter=0),
        command(7, 0, pressed=False, emitter=0),
    ]
    assert running.relay(0, 0).on is True
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert running.relay(0, 0).on is False


async def test_reset_is_not_supported_on_master(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """There is no inferred state to resynchronise; refuse and send nothing."""
    with pytest.raises(BiomatxNotSupportedError):
        await running.async_reset()
    assert fake_serial.frames_written(COMMAND_FRAME_LENGTH) == []


# --- protocol detection at runtime --------------------------------------------------


async def test_protocol_is_detected_from_the_first_master_report(
    fake_serial: FakeSerialLink,
) -> None:
    """Without a stored protocol the hub listens; bytes seen meanwhile are replayed."""
    hub = make_hub(protocol=None)
    task = await run_hub(hub)
    assert hub.protocol is None
    assert hub.reports_state is False
    assert hub.stats == ParserStats()
    fake_serial.feed("00 ff " + fm.STATE_M1_R1_ON)
    await settle()
    assert hub.protocol is Protocol.MASTER
    assert hub.reports_state is True
    assert hub.relay(0, 0).on is True
    assert hub.module_available(0) is True
    assert hub.bytes_dropped == 2
    await stop_hub(hub, task)


async def test_protocol_is_detected_from_legacy_frames(
    fake_serial: FakeSerialLink,
) -> None:
    """Two-byte frames and nothing else: the hub behaves as the legacy hub."""
    hub = make_hub(protocol=None)
    task = await run_hub(hub)
    fake_serial.feed(legacy.PRESS_M1_R1 + legacy.RELEASE_M1_R1)
    await settle()
    assert hub.protocol is Protocol.LEGACY
    assert hub.reports_state is False
    assert hub.relay(0, 0).on is True
    assert hub.module_available(0) is True
    await stop_hub(hub, task)


async def test_command_before_detection_is_refused_without_writing(
    fake_serial: FakeSerialLink,
) -> None:
    """The hub cannot encode a frame it does not know the format of."""
    hub = make_hub(protocol=None)
    task = await run_hub(hub)
    with pytest.raises(BiomatxProtocolUnknownError):
        await hub.async_set_relay(hub.relay(0, 0), on=True)
    with pytest.raises(BiomatxProtocolUnknownError):
        await hub.async_activate_scenario(0)
    assert fake_serial.frames_written() == []
    await stop_hub(hub, task)


async def test_detection_survives_a_long_run_of_noise(
    fake_serial: FakeSerialLink,
) -> None:
    """Garbage before the first frame is bounded, not accumulated forever."""
    hub = make_hub(protocol=None)
    task = await run_hub(hub)
    fake_serial.feed("00 " * 2000)
    await settle()
    assert hub.protocol is None
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await settle()
    assert hub.protocol is Protocol.MASTER
    assert hub.module_available(0) is True
    assert hub.bytes_dropped == 2000  # trimmed bytes are counted too
    await stop_hub(hub, task)


async def test_first_read_in_the_middle_of_a_master_report_does_not_latch_legacy(
    fake_serial: FakeSerialLink,
) -> None:
    """``a5 18`` looks like a legacy frame; a half master frame keeps us undecided."""
    hub = make_hub(protocol=None)
    task = await run_hub(hub)
    fake_serial.feed(fm.STATE_HOUSE_M1[:14])  # five bytes: a5 18 7f 40 81
    await settle()
    assert hub.protocol is None
    fake_serial.feed(fm.STATE_HOUSE_M1[15:] + " " + fm.STATE_HOUSE_M2)
    await settle()
    assert hub.protocol is Protocol.MASTER
    assert hub.relay(0, 1).on is True
    assert hub.module_available(1) is True
    await stop_hub(hub, task)
