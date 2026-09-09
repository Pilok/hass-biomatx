"""
Unit tests for the serial hub, without Home Assistant.

The hub owns the serial transport: it opens the port, decodes frames, keeps the
inferred relay and button states, notifies listeners, sends frames, and
reconnects when the link drops.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest
from serial import SerialException

from custom_components.biomatx import hub as hub_module
from custom_components.biomatx.hub import (
    BiomatxConnectionError,
    BiomatxHub,
    BiomatxLinkError,
    BiomatxNotConfiguredError,
)

from . import frames
from .fake_serial import FakeSerialLink, settle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

URL = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_TEST-if00-port0"


def make_hub(module_count: int = 4, all_off_address: int | None = None) -> BiomatxHub:
    """Build a hub with no waits, so tests run instantly."""
    return BiomatxHub(
        URL,
        module_count,
        all_off_address,
        frame_gap=0,
        reconnect_delays=(0,),
    )


@pytest.fixture
async def running(fake_serial: FakeSerialLink) -> AsyncIterator[BiomatxHub]:
    """Provide a connected hub whose reader loop runs in the background."""
    hub = make_hub(all_off_address=5)
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    yield hub
    await hub.async_close()
    await asyncio.wait_for(task, timeout=2)


def recorder(events: list[str], label: str) -> Callable[[], None]:
    """Return a listener that appends ``label`` to ``events``."""

    def _record() -> None:
        events.append(label)

    return _record


# --- connection -------------------------------------------------------------


async def test_connect_opens_port_at_19200_8n1(fake_serial: FakeSerialLink) -> None:
    """The BioMatX bus speaks 19200 baud, 8 data bits, no parity, 1 stop bit."""
    hub = make_hub()
    await hub.async_connect()
    assert hub.connected is True
    assert len(fake_serial.opens) == 1
    opened = fake_serial.opens[0]
    assert opened["url"] == URL
    assert opened["baudrate"] == 19200
    assert opened["bytesize"] == 8
    assert opened["parity"] == "N"
    assert opened["stopbits"] == 1


async def test_connect_sends_no_bytes(fake_serial: FakeSerialLink) -> None:
    """Connecting must be silent: it enables an observe-only deployment stage."""
    hub = make_hub()
    await hub.async_connect()
    assert fake_serial.frames_written() == []


async def test_connect_failure_raises_connection_error(
    fake_serial: FakeSerialLink,
) -> None:
    """A port that cannot be opened is reported as a connection error."""
    fake_serial.fail_open = SerialException("could not open port")
    hub = make_hub()
    with pytest.raises(BiomatxConnectionError):
        await hub.async_connect()
    assert hub.connected is False


async def test_hub_models_configured_modules_and_scenarios() -> None:
    """Four modules give 40 relays, 40 buttons and 10 scenario buttons."""
    hub = make_hub(module_count=4)
    assert [module.address for module in hub.modules] == [0, 1, 2, 3]
    assert hub.scenario_module.address == 7
    assert len(hub.relays) == 40
    assert len(hub.switches) == 50


# --- decoding ---------------------------------------------------------------


async def test_press_frame_toggles_relay_and_notifies_listener(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A wall press flips the inferred relay state and wakes its listener."""
    events: list[str] = []
    running.add_listener(("relay", 0, 0), recorder(events, "relay"))
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    assert running.relay(0, 0).on is True
    assert events == ["relay"]
    fake_serial.feed(frames.RELEASE_M1_R1 + frames.PRESS_M1_R1)
    await settle()
    assert running.relay(0, 0).on is False
    assert events == ["relay", "relay"]


async def test_release_frame_updates_switch_not_relay(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Press then release: the button follows both, the relay only the press."""
    events: list[str] = []
    running.add_listener(("switch", 0, 0), recorder(events, "switch"))
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    assert running.switch(0, 0).pressed is True
    fake_serial.feed(frames.RELEASE_M1_R1)
    await settle()
    assert running.switch(0, 0).pressed is False
    assert running.relay(0, 0).on is True
    assert events == ["switch", "switch"]


async def test_cross_module_frame_targets_module_from_second_byte(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The corridor detector is wired on module 2 but drives module 1 relay 8."""
    fake_serial.feed(frames.DETECTOR_CORRIDOR_PRESS + frames.DETECTOR_CORRIDOR_RELEASE)
    await settle()
    assert running.relay(0, 7).on is True
    assert running.relay(1, 7).on is False
    assert running.frames_received == 2


async def test_frame_with_switch_above_nine_is_dropped_and_counted(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Collision garbage decodes to relay index 10: no relay exists, nothing moves."""
    fake_serial.feed(frames.COLLISION_INVALID_SWITCH)
    await settle()
    assert all(relay.on is False for relay in running.relays)
    assert running.frames_dropped == 1
    assert running.bytes_dropped == 0


async def test_frame_for_unconfigured_module_is_dropped(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A frame for module 7 (index 6) on a 4-module bus is ignored."""
    fake_serial.feed(frames.UNCONFIGURED_MODULE_PRESS)
    await settle()
    assert running.frames_dropped == 1
    assert all(relay.on is False for relay in running.relays)


async def test_alternate_start_nibble_is_accepted(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Frames starting with 0xA are decoded like 0x5 frames."""
    fake_serial.feed(frames.ALT_START_NIBBLE_PRESS)
    await settle()
    assert running.relay(0, 0).on is True


async def test_orphan_byte_desyncs_one_frame_then_resyncs(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A lone start byte swallows the next frame; the following one is decoded."""
    fake_serial.feed(frames.ORPHAN_START_BYTE + frames.PRESS_M1_R1 + frames.PRESS_M2_R8)
    await settle()
    assert running.relay(0, 0).on is False
    assert running.relay(1, 7).on is True
    assert running.frames_dropped == 1  # "50 50" targets module 6, unconfigured
    assert running.bytes_dropped == 1  # the stray "00"


async def test_scenario_frame_updates_scenario_switch(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Scenario buttons live on module 7 and have no relay."""
    events: list[str] = []
    running.add_listener(("switch", 7, 5), recorder(events, "scenario"))
    fake_serial.feed(frames.SCENARIO_6_PRESS)
    await settle()
    assert running.switch(7, 5).pressed is True
    assert events == ["scenario"]


async def test_all_off_scenario_press_marks_all_relays_off(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Seeing the all-off scenario on the bus means the modules just executed it."""
    fake_serial.feed(frames.PRESS_M1_R1 + frames.RELEASE_M1_R1 + frames.PRESS_M2_R8)
    await settle()
    assert running.relay(0, 0).on is True
    events: list[str] = []
    running.add_listener(("relay", 0, 0), recorder(events, "relay"))
    fake_serial.feed(frames.SCENARIO_6_PRESS + frames.SCENARIO_6_RELEASE)
    await settle()
    assert all(relay.on is False for relay in running.relays)
    assert events == ["relay"]


async def test_listener_unsubscribe_stops_notifications(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The callable returned by add_listener removes the listener."""
    events: list[str] = []
    unsubscribe = running.add_listener(("relay", 0, 0), recorder(events, "relay"))
    unsubscribe()
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    assert events == []


# --- sending ----------------------------------------------------------------


async def test_toggle_sends_press_then_release_and_flips_state(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Toggling a relay simulates a wall button press on the bus."""
    events: list[str] = []
    running.add_listener(("relay", 0, 0), recorder(events, "relay"))
    await running.async_toggle(running.relay(0, 0))
    assert fake_serial.frames_written() == [frames.PRESS_M1_R1, frames.RELEASE_M1_R1]
    assert running.relay(0, 0).on is True
    assert events == ["relay"]


async def test_concurrent_toggles_do_not_interleave_frames(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Two commands at once still put complete press/release pairs on the bus."""
    await asyncio.gather(
        running.async_toggle(running.relay(0, 0)),
        running.async_toggle(running.relay(1, 7)),
    )
    written = fake_serial.frames_written()
    assert sorted(written) == sorted(
        [
            frames.PRESS_M1_R1,
            frames.RELEASE_M1_R1,
            frames.PRESS_M2_R8,
            frames.RELEASE_M2_R8,
        ]
    )
    first_pair = written[:2]
    assert first_pair in (
        [frames.PRESS_M1_R1, frames.RELEASE_M1_R1],
        [frames.PRESS_M2_R8, frames.RELEASE_M2_R8],
    )


async def test_write_failure_marks_link_down_and_raises(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """An I/O error while writing is a lost link, reported to the caller."""
    link_events: list[bool] = []
    running.add_link_listener(link_events.append)
    fake_serial.fail_write_at = 0
    with pytest.raises(BiomatxLinkError):
        await running.async_toggle(running.relay(0, 0))
    assert running.connected is False
    assert link_events == [False]
    assert running.relay(0, 0).on is False


async def test_activate_scenario_sends_press_then_release_on_module_7(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Scenarios are triggered like buttons of the virtual module 7."""
    await running.async_activate_scenario(5)
    assert fake_serial.frames_written() == [
        frames.SCENARIO_6_PRESS,
        frames.SCENARIO_6_RELEASE,
    ]


# --- link loss and reconnection --------------------------------------------


async def test_eof_marks_link_down_then_reconnects(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Unplugging the adapter drops the link; the hub reopens the port by itself."""
    link_events: list[bool] = []
    running.add_link_listener(link_events.append)
    fake_serial.drop_link()
    await settle()
    assert link_events[0] is False
    await settle(50)
    assert running.connected is True
    assert link_events == [False, True]
    assert len(fake_serial.opens) == 2
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    assert running.relay(0, 0).on is True


async def test_reader_exception_is_treated_as_link_loss(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Pyserial raises on I/O errors; the hub treats that like EOF."""
    fake_serial.fail_link(
        SerialException("device reports readiness to read but returned no data")
    )
    await settle(50)
    assert running.connected is True
    assert len(fake_serial.opens) == 2


async def test_reconnect_keeps_relay_states(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Inferred states survive a link loss: nothing physical changed."""
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    fake_serial.drop_link()
    await settle(50)
    assert running.connected is True
    assert running.relay(0, 0).on is True


async def test_close_stops_reader_without_reconnecting(
    fake_serial: FakeSerialLink,
) -> None:
    """Closing the hub ends the reader loop; no reconnection is attempted."""
    hub = make_hub()
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    await hub.async_close()
    await asyncio.wait_for(task, timeout=1)
    assert hub.connected is False
    assert len(fake_serial.opens) == 1


async def test_run_connects_by_itself_when_not_connected(
    fake_serial: FakeSerialLink,
) -> None:
    """Starting the loop on an unconnected hub opens the port first."""
    hub = make_hub()
    task = asyncio.create_task(hub.async_run())
    await settle(20)
    assert hub.connected is True
    await hub.async_close()
    await task


async def test_send_while_link_down_raises_without_writing(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """While disconnected, a command fails fast instead of touching a dead port."""
    fake_serial.fail_write_at = 0
    with pytest.raises(BiomatxLinkError):
        await running.async_toggle(running.relay(0, 0))
    fake_serial.clear()
    with pytest.raises(BiomatxLinkError):
        await running.async_activate_scenario(5)
    assert fake_serial.frames_written() == []


# --- all off and reset ------------------------------------------------------


async def test_all_off_sends_scenario_and_clears_states(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """all_off fires the configured scenario and marks every relay off."""
    fake_serial.feed(frames.PRESS_M1_R1 + frames.PRESS_M2_R8)
    await settle()
    events: list[str] = []
    running.add_listener(("relay", 0, 0), recorder(events, "m1r1"))
    running.add_listener(("relay", 1, 7), recorder(events, "m2r8"))
    fake_serial.clear()
    await running.async_all_off()
    assert fake_serial.frames_written() == [
        frames.SCENARIO_6_PRESS,
        frames.SCENARIO_6_RELEASE,
    ]
    assert all(relay.on is False for relay in running.relays)
    assert sorted(events) == ["m1r1", "m2r8"]


async def test_all_off_without_scenario_raises_not_configured(
    fake_serial: FakeSerialLink,
) -> None:
    """Without an all-off scenario there is nothing to send: say so, send nothing."""
    hub = make_hub(all_off_address=None)
    await hub.async_connect()
    with pytest.raises(BiomatxNotConfiguredError):
        await hub.async_all_off()
    with pytest.raises(BiomatxNotConfiguredError):
        await hub.async_reset()
    assert fake_serial.frames_written() == []


async def test_reset_sends_all_off_then_reactivates_relays_believed_on(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Reset resynchronises the modules with Home Assistant's belief."""
    fake_serial.feed(frames.PRESS_M1_R1 + frames.PRESS_M2_R8)
    await settle()
    fake_serial.clear()
    await running.async_reset()
    assert fake_serial.frames_written() == [
        frames.SCENARIO_6_PRESS,
        frames.SCENARIO_6_RELEASE,
        frames.PRESS_M1_R1,
        frames.RELEASE_M1_R1,
        frames.PRESS_M2_R8,
        frames.RELEASE_M2_R8,
    ]
    assert running.relay(0, 0).on is True
    assert running.relay(1, 7).on is True
    assert running.relay(0, 1).on is False


async def test_link_listener_unsubscribe_stops_notifications(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The callable returned by add_link_listener removes the listener."""
    link_events: list[bool] = []
    unsubscribe = running.add_link_listener(link_events.append)
    unsubscribe()
    fake_serial.drop_link()
    await settle(50)
    assert link_events == []


async def test_reconnection_retries_until_the_port_reopens(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A port still missing after a link loss is retried, not given up on."""
    fake_serial.fail_open = SerialException("no such device")
    fake_serial.drop_link()
    await settle(50)
    assert running.connected is True
    assert len(fake_serial.opens) == 3


# --- findings of the 2026-09-08 review ------------------------------------------


async def test_frame_split_across_two_reads_is_decoded(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """At 19200 baud the two bytes of a frame often arrive in separate reads."""
    fake_serial.feed(frames.PRESS_M1_R1[:2])
    await settle()
    assert running.relay(0, 0).on is False
    fake_serial.feed(frames.PRESS_M1_R1[3:])
    await settle()
    assert running.relay(0, 0).on is True
    assert running.frames_dropped == 0
    assert running.bytes_dropped == 0


async def test_other_scenario_press_does_not_clear_relays(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Only the configured all-off scenario resets the inferred states."""
    fake_serial.feed(frames.PRESS_M1_R1 + frames.RELEASE_M1_R1)
    await settle()
    fake_serial.feed(frames.SCENARIO_4_PRESS + frames.SCENARIO_4_RELEASE)
    await settle()
    assert running.relay(0, 0).on is True
    assert running.switch(7, 3).pressed is False


async def test_garbage_run_is_counted_and_logged_once(
    running: BiomatxHub,
    fake_serial: FakeSerialLink,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A burst of line noise produces one debug record, not one per byte."""
    caplog.set_level(logging.DEBUG, logger="custom_components.biomatx.hub")
    fake_serial.feed("00 " * 40 + frames.PRESS_M1_R1)
    await settle()
    assert running.bytes_dropped == 40
    assert running.relay(0, 0).on is True
    noise_records = [r for r in caplog.records if "outside a frame" in r.getMessage()]
    assert len(noise_records) == 1
    assert "40" in noise_records[0].getMessage()


async def test_listener_exception_does_not_stop_the_reader(
    running: BiomatxHub,
    fake_serial: FakeSerialLink,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A misbehaving entity callback must not kill the bus reader."""

    def _boom() -> None:
        msg = "entity gone"
        raise RuntimeError(msg)

    running.add_listener(("relay", 0, 0), _boom)
    fake_serial.feed(frames.PRESS_M1_R1 + frames.PRESS_M2_R8)
    await settle()
    assert running.relay(0, 0).on is True
    assert running.relay(1, 7).on is True
    assert running.frames_received == 2
    assert running.connected is True
    assert any("entity gone" in r.getMessage() or r.exc_info for r in caplog.records)


async def test_toggle_flips_state_as_soon_as_the_press_is_written(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """The module acts on the press frame: a failed release must not undo the flip."""
    fake_serial.fail_write_at = 1
    with pytest.raises(BiomatxLinkError):
        await running.async_toggle(running.relay(0, 0))
    assert fake_serial.frames_written() == [frames.PRESS_M1_R1]
    assert running.relay(0, 0).on is True
    assert running.connected is False


async def test_send_on_closing_writer_raises_without_writing(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """A transport that is closing never reports write errors: check it first."""
    assert fake_serial.writer is not None
    fake_serial.writer.closed = True
    with pytest.raises(BiomatxLinkError):
        await running.async_toggle(running.relay(0, 0))
    assert fake_serial.frames_written() == []
    assert running.relay(0, 0).on is False


async def test_activate_all_off_scenario_marks_relays_off(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Sending the all-off scenario has the same effect as observing it."""
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    await running.async_activate_scenario(5)
    assert all(relay.on is False for relay in running.relays)


async def test_reset_failing_midway_leaves_a_consistent_state(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """After the all-off scenario every relay is off until its press succeeds."""
    fake_serial.feed(frames.PRESS_M1_R1 + frames.PRESS_M2_R8)
    await settle()
    fake_serial.clear()
    fake_serial.fail_write_at = (
        4  # scenario (2 writes), relay 1 (2 writes), relay 2 press
    )
    with pytest.raises(BiomatxLinkError):
        await running.async_reset()
    assert running.relay(0, 0).on is True
    assert running.relay(1, 7).on is False


async def test_backoff_only_resets_once_data_has_been_read(
    fake_serial: FakeSerialLink,
) -> None:
    """A port that opens then drops at once must not retry every second forever."""
    hub = BiomatxHub(URL, 4, None, frame_gap=0, reconnect_delays=(0, 30))
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    fake_serial.drop_link()
    await settle(30)
    assert len(fake_serial.opens) == 2  # immediate first retry succeeded
    fake_serial.drop_link()
    await settle(30)
    assert len(fake_serial.opens) == 2  # second retry waits 30 s: no third open
    assert hub.connected is False
    await hub.async_close()
    await asyncio.wait_for(task, timeout=2)


async def test_close_while_waiting_to_reconnect_stops_promptly(
    fake_serial: FakeSerialLink,
) -> None:
    """Closing must not wait for a pending reconnection delay to elapse."""
    hub = BiomatxHub(URL, 4, None, frame_gap=0, reconnect_delays=(30,))
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    fake_serial.drop_link()
    await settle(30)
    assert hub.connected is False
    await hub.async_close()
    await asyncio.wait_for(task, timeout=1)


async def test_close_during_a_pending_open_does_not_reopen_the_port(
    fake_serial: FakeSerialLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconnection in flight when the hub closes must not leave a port open."""
    hub = make_hub()
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    fake_serial.hold_open = asyncio.Event()
    fake_serial.drop_link()
    await settle(30)
    assert len(fake_serial.opens) == 2  # second open is pending
    monkeypatch.setattr(hub_module, "CLOSE_TIMEOUT", 0.05)
    await hub.async_close()
    fake_serial.hold_open.set()
    await settle(30)
    assert task.done()
    assert hub.connected is False
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is True


async def test_socket_url_uses_a_plain_tcp_connection(
    monkeypatch: pytest.MonkeyPatch, fake_serial: FakeSerialLink
) -> None:
    """Ethernet gateways are reached with asyncio, not pyserial's socket handler."""
    calls: list[tuple[str, int]] = []

    async def _open_connection(
        host: str, port: int
    ) -> tuple[asyncio.StreamReader, object]:
        calls.append((host, port))
        return await fake_serial.open_serial_connection(url=f"tcp://{host}:{port}")

    monkeypatch.setattr(asyncio, "open_connection", _open_connection)
    hub = BiomatxHub("socket://192.168.1.50:8899", 4, None, frame_gap=0)
    await hub.async_connect()
    assert calls == [("192.168.1.50", 8899)]
    assert hub.connected is True
    await hub.async_close()


async def test_link_listener_exception_is_logged_and_others_still_run(
    running: BiomatxHub,
    fake_serial: FakeSerialLink,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing link listener must not hide the link change from the others."""
    seen: list[bool] = []

    def _boom(connected: bool) -> None:  # noqa: FBT001  # hub callback signature
        del connected
        msg = "listener gone"
        raise RuntimeError(msg)

    running.add_link_listener(_boom)
    running.add_link_listener(seen.append)
    fake_serial.drop_link()
    await settle()
    assert seen[0] is False
    assert any("link listener failed" in r.getMessage() for r in caplog.records)


async def test_unexpected_reader_error_is_logged_and_the_link_reopened(
    running: BiomatxHub,
    fake_serial: FakeSerialLink,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug while decoding must not leave the hub deaf with connected=True."""
    original = running._handle_frame
    calls = 0

    def _flaky(first: int, second: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "decoder bug"
            raise RuntimeError(msg)
        original(first, second)

    monkeypatch.setattr(running, "_handle_frame", _flaky)
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle(50)
    assert running.connected is True
    assert len(fake_serial.opens) == 2
    assert any("unexpected error" in r.getMessage() for r in caplog.records)
    fake_serial.feed(frames.PRESS_M1_R1)
    await settle()
    assert running.relay(0, 0).on is True


async def test_set_relay_is_a_no_op_when_already_in_the_wanted_state(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Asking for the believed state sends nothing on the bus."""
    await running.async_set_relay(running.relay(0, 0), on=False)
    assert fake_serial.frames_written() == []
    await running.async_set_relay(running.relay(0, 0), on=True)
    assert fake_serial.frames_written() == [frames.PRESS_M1_R1, frames.RELEASE_M1_R1]
    assert running.relay(0, 0).on is True


async def test_concurrent_set_relay_commands_press_only_once(
    running: BiomatxHub, fake_serial: FakeSerialLink
) -> None:
    """Two turn_on calls in the same tick must not toggle the relay twice."""
    await asyncio.gather(
        running.async_set_relay(running.relay(0, 0), on=True),
        running.async_set_relay(running.relay(0, 0), on=True),
    )
    assert fake_serial.frames_written() == [frames.PRESS_M1_R1, frames.RELEASE_M1_R1]
    assert running.relay(0, 0).on is True


async def test_open_completing_after_close_is_released(
    fake_serial: FakeSerialLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A port opened by a late reconnection is closed again, not kept."""
    hub = make_hub()
    await hub.async_connect()
    task = asyncio.create_task(hub.async_run())
    await settle()
    fake_serial.hold_open = asyncio.Event()
    fake_serial.drop_link()
    await settle(30)
    monkeypatch.setattr(hub_module, "CLOSE_TIMEOUT", 0.5)
    asyncio.get_running_loop().call_later(0.01, fake_serial.hold_open.set)
    await hub.async_close()
    assert task.done()
    assert hub.connected is False
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is True
