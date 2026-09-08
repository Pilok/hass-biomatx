"""
Serial hub: owns the RS485 link to the BioMatX modules.

The hub opens the serial port, reads the two-byte frames the modules emit,
keeps the inferred state of every relay and button, notifies listeners,
sends frames to trigger relays and scenarios, and reopens the port when the
link drops. It has no Home Assistant dependency so it can be unit tested with
an in-memory link.

The ``biomatx`` package is used only as a data model (``Packet``, ``Module``,
``Relay``, ``Switch``); its own transport (``Bus.connect``, ``Bus.loop``) is
never used.

Transport notes. ``serial_asyncio`` opens USB serial devices synchronously
(a few milliseconds) and reports write errors through the reader, never from
``write()``: the hub therefore checks the link before writing and treats any
reader error as a lost link. Ethernet gateways (``socket://host:port``) are
reached with a plain asyncio TCP connection, which avoids the blocking calls
of pyserial's socket handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Literal

import serial
from serial import SerialException
import serial_asyncio

import biomatx
from biomatx import SCENARIO_MODULE_ADDRESS, Packet

from .const import RELAYS_PER_MODULE

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_LOGGER = logging.getLogger(__name__)

BAUDRATE = 19200
FRAME_GAP = 0.2
"""Seconds to wait after each frame, so the modules have time to process it."""
RECONNECT_DELAYS: tuple[float, ...] = (1, 2, 5, 10, 30, 60)
"""Seconds between reconnection attempts; the last value repeats."""
CLOSE_TIMEOUT = 2
"""Seconds to wait for the reader task and the transport to finish closing."""
READ_CHUNK = 64
MAX_SWITCH_ADDRESS = RELAYS_PER_MODULE - 1
START_NIBBLES = (0x50, 0xA0)
"""High nibbles that open a frame; the low nibble is the emitting module."""
SOCKET_URL_PREFIX = "socket://"

type DeviceKind = Literal["relay", "switch"]
type DeviceKey = tuple[DeviceKind, int, int]
"""(kind, module address, switch address), all 0-based like the frames."""
type Listener = Callable[[], None]
type LinkListener = Callable[[bool], None]


class BiomatxError(Exception):
    """Base class for hub errors."""


class BiomatxConnectionError(BiomatxError):
    """The serial port could not be opened."""


class BiomatxLinkError(BiomatxError):
    """The link is down or a write failed."""


class BiomatxNotConfiguredError(BiomatxError):
    """The requested operation needs the all-off scenario, which is not set."""


class BiomatxHub:
    """Owner of the serial link and of the inferred bus state."""

    def __init__(
        self,
        url: str,
        module_count: int,
        all_off_address: int | None,
        *,
        frame_gap: float | None = None,
        reconnect_delays: Sequence[float] | None = None,
    ) -> None:
        """
        Model ``module_count`` modules plus the scenario module.

        ``url`` is a serial device path, a pyserial URL, or ``socket://host:port``
        for an Ethernet gateway. ``all_off_address`` is the 0-based scenario
        button that turns every relay off, or ``None`` when no such scenario
        exists. ``frame_gap`` and ``reconnect_delays`` default to the module
        constants, read when the hub is built so tests can shorten them.
        """
        self.url = url
        self.module_count = module_count
        self.all_off_address = all_off_address
        self._frame_gap = FRAME_GAP if frame_gap is None else frame_gap
        self._reconnect_delays = tuple(
            RECONNECT_DELAYS if reconnect_delays is None else reconnect_delays
        )
        self._bus = biomatx.Bus(module_count)
        self._relays: list[biomatx.Relay] = self._bus.relays
        self._switches: list[biomatx.Switch] = self._bus.switches
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._closed = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None
        self._retry_index = 0
        self._send_lock = asyncio.Lock()
        self._listeners: dict[DeviceKey, list[Listener]] = {}
        self._link_listeners: list[LinkListener] = []
        self._frames_received = 0
        self._frames_dropped = 0
        self._bytes_dropped = 0
        self._noise_run = 0

    # --- state exposed to entities and diagnostics --------------------------

    @property
    def connected(self) -> bool:
        """Return whether the serial link is currently open."""
        return self._connected

    @property
    def frames_received(self) -> int:
        """Return the number of valid frames decoded since start."""
        return self._frames_received

    @property
    def frames_dropped(self) -> int:
        """Return the number of complete frames rejected since start."""
        return self._frames_dropped

    @property
    def bytes_dropped(self) -> int:
        """Return the number of stray bytes seen outside a frame since start."""
        return self._bytes_dropped

    @property
    def modules(self) -> list[biomatx.Module]:
        """Return the configured modules, scenario module excluded."""
        return self._bus.modules

    @property
    def scenario_module(self) -> biomatx.Module:
        """Return the virtual module that carries the scenarios."""
        return self._bus.scenarios

    @property
    def relays(self) -> list[biomatx.Relay]:
        """Return every relay of the configured modules."""
        return self._relays

    @property
    def switches(self) -> list[biomatx.Switch]:
        """Return every button, scenario buttons included."""
        return self._switches

    def relay(self, module: int, address: int) -> biomatx.Relay:
        """Return one relay by 0-based module and relay address."""
        return self._bus.relay(module, address)

    def switch(self, module: int, address: int) -> biomatx.Switch:
        """Return one button by 0-based module and button address."""
        return self._bus.switch(module, address)

    def _is_known_module(self, address: int) -> bool:
        return address < self.module_count or address == SCENARIO_MODULE_ADDRESS

    # --- listeners ------------------------------------------------------------

    def add_listener(self, key: DeviceKey, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` when the device's state changes; return an unsubscribe."""
        self._listeners.setdefault(key, []).append(listener)

        def _unsubscribe() -> None:
            self._listeners[key].remove(listener)

        return _unsubscribe

    def add_link_listener(self, listener: LinkListener) -> Callable[[], None]:
        """Call ``listener(connected)`` when the link goes down or up."""
        self._link_listeners.append(listener)

        def _unsubscribe() -> None:
            self._link_listeners.remove(listener)

        return _unsubscribe

    def _notify(self, key: DeviceKey) -> None:
        for listener in list(self._listeners.get(key, ())):
            try:
                listener()
            except Exception:
                _LOGGER.exception("listener for %s failed", key)

    def _set_connected(self, *, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        for listener in list(self._link_listeners):
            try:
                listener(connected)
            except Exception:
                _LOGGER.exception("link listener failed")

    # --- connection lifecycle -------------------------------------------------

    async def async_connect(self) -> None:
        """
        Open the link; raise ``BiomatxConnectionError`` on failure.

        Nothing is written on the bus: connecting is silent by design.
        """
        try:
            if self.url.startswith(SOCKET_URL_PREFIX):
                host, _, port = self.url.removeprefix(SOCKET_URL_PREFIX).rpartition(":")
                reader, writer = await asyncio.open_connection(host, int(port))
            else:
                reader, writer = await serial_asyncio.open_serial_connection(
                    url=self.url,
                    baudrate=BAUDRATE,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    xonxoff=False,
                    rtscts=False,
                )
        except (OSError, SerialException, ValueError) as err:
            msg = f"cannot open {self.url}: {err}"
            raise BiomatxConnectionError(msg) from err
        self._reader = reader
        self._writer = writer
        self._set_connected(connected=True)

    async def async_run(self) -> None:
        """Read frames until ``async_close``; reconnect whenever the link drops."""
        self._run_task = asyncio.current_task()
        try:
            while not self._closed.is_set():
                if self._reader is None:
                    try:
                        await self.async_connect()
                    except BiomatxConnectionError as err:
                        _LOGGER.debug("reconnection failed: %s", err)
                        await self._wait_before_retry()
                        continue
                    if self._closed.is_set():
                        break
                    _LOGGER.info("connected to %s", self.url)
                error: BaseException | None = None
                try:
                    await self._read_frames(self._reader)
                except (OSError, SerialException) as err:
                    error = err
                except Exception as err:  # the reader must survive anything
                    _LOGGER.exception("unexpected error while reading the bus")
                    error = err
                if self._closed.is_set():
                    break
                self._on_link_lost(error)
                await self._wait_before_retry()
        finally:
            self._release_link()
            self._set_connected(connected=False)
            self._run_task = None

    async def async_close(self) -> None:
        """Close the link and stop the reader loop without reconnecting."""
        self._closed.set()
        writer = self._release_link()
        self._set_connected(connected=False)
        task = self._run_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            # The reader exits by itself once the link is released; cancelling is
            # the fallback for a reader stuck in a pending connection attempt.
            await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)
            if not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)
        if writer is not None:
            with contextlib.suppress(Exception, asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), timeout=CLOSE_TIMEOUT)

    async def _wait_before_retry(self) -> None:
        delays = self._reconnect_delays
        delay = delays[min(self._retry_index, len(delays) - 1)]
        self._retry_index += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._closed.wait(), timeout=delay)

    def _release_link(self) -> asyncio.StreamWriter | None:
        reader, writer = self._reader, self._writer
        self._reader = self._writer = None
        if writer is not None and not writer.is_closing():
            writer.close()
        if reader is not None:
            # Unblock a pending read even if the transport was already closing.
            reader.feed_eof()
        return writer

    def _on_link_lost(self, error: BaseException | None) -> None:
        if not self._connected:
            return
        self._release_link()
        _LOGGER.warning(
            "link to %s lost (%s), reconnecting",
            self.url,
            error if error is not None else "end of stream",
        )
        self._set_connected(connected=False)

    # --- receiving --------------------------------------------------------------

    async def _read_frames(self, reader: asyncio.StreamReader) -> None:
        """Decode frames until end of stream; raise on I/O errors."""
        pending: int | None = None
        while True:
            chunk = await reader.read(READ_CHUNK)
            if not chunk:
                return
            # Data flows: the link is proven, the next outage starts a new backoff.
            self._retry_index = 0
            for byte in chunk:
                if pending is None:
                    if byte & 0xF0 in START_NIBBLES:
                        pending = byte
                        self._flush_noise()
                    else:
                        self._bytes_dropped += 1
                        self._noise_run += 1
                    continue
                self._handle_frame(pending, byte)
                pending = None

    def _flush_noise(self) -> None:
        if self._noise_run:
            _LOGGER.debug(
                "dropped %d bytes outside a frame before resynchronising",
                self._noise_run,
            )
            self._noise_run = 0

    def _handle_frame(self, first: int, second: int) -> None:
        packet = Packet.from_bytes(bytes((first, second)))
        emitter = first & 0x0F
        if (
            packet.switch > MAX_SWITCH_ADDRESS
            or not self._is_known_module(packet.module)
            or not self._is_known_module(emitter)
        ):
            self._frames_dropped += 1
            _LOGGER.debug(
                "dropping frame %02x %02x: module %d button %d from module %d "
                "(unconfigured module or bus collision)",
                first,
                second,
                packet.module,
                packet.switch,
                emitter,
            )
            return
        self._frames_received += 1
        _LOGGER.debug(
            "frame %02x %02x: module %d button %d %s (emitted by module %d)",
            first,
            second,
            packet.module,
            packet.switch,
            "released" if packet.released else "pressed",
            emitter,
        )
        switch = self._bus.switch(packet.module, packet.switch)
        switch.released = packet.released
        if packet.module == SCENARIO_MODULE_ADDRESS:
            if packet.pressed and packet.switch == self.all_off_address:
                self._mark_all_off()
        elif packet.pressed:
            relay = self._bus.relay(packet.module, packet.switch)
            relay.on = not relay.on
            self._notify(("relay", packet.module, packet.switch))
        self._notify(("switch", packet.module, packet.switch))

    def _mark_all_off(self) -> None:
        for relay in self._relays:
            if relay.on:
                relay.on = False
                self._notify(("relay", relay.module.address, relay.address))

    # --- sending ----------------------------------------------------------------

    async def _send(self, packet: Packet) -> None:
        writer = self._writer
        if not self._connected or writer is None or writer.is_closing():
            self._on_link_lost(None)
            msg = f"link to {self.url} is down"
            raise BiomatxLinkError(msg)
        try:
            writer.write(bytes(packet))
            await writer.drain()
        except (OSError, SerialException) as err:
            self._on_link_lost(err)
            msg = f"write to {self.url} failed: {err}"
            raise BiomatxLinkError(msg) from err
        await asyncio.sleep(self._frame_gap)

    async def _press_and_release(
        self, module: int, switch: int, on_pressed: Callable[[], None] | None = None
    ) -> None:
        """
        Send a press then a release; ``on_pressed`` runs once the press is out.

        The modules act on the press frame, so the inferred state must change
        as soon as that frame is written, even if the release then fails.
        Callers hold ``_send_lock``.
        """
        await self._send(Packet(module, switch, released=False))
        if on_pressed is not None:
            on_pressed()
        await self._send(Packet(module, switch, released=True))

    def _flip(self, relay: biomatx.Relay) -> Callable[[], None]:
        def _apply() -> None:
            relay.on = not relay.on
            self._notify(("relay", relay.module.address, relay.address))

        return _apply

    async def async_toggle(self, relay: biomatx.Relay) -> None:
        """Simulate a button press for ``relay`` and flip its inferred state."""
        async with self._send_lock:
            await self._press_and_release(
                relay.module.address, relay.address, self._flip(relay)
            )

    async def async_set_relay(self, relay: biomatx.Relay, *, on: bool) -> None:
        """
        Bring ``relay`` to ``on``; a no-op when it is already believed there.

        The check and the press happen under the same lock, so two concurrent
        commands for one relay cannot toggle it twice.
        """
        async with self._send_lock:
            if relay.on == on:
                return
            await self._press_and_release(
                relay.module.address, relay.address, self._flip(relay)
            )

    async def async_activate_scenario(self, address: int) -> None:
        """
        Trigger the scenario button ``address`` (0-based) of module 7.

        Triggering the all-off scenario marks every relay off, as observing it
        on the bus does.
        """
        async with self._send_lock:
            on_pressed = self._mark_all_off if address == self.all_off_address else None
            await self._press_and_release(SCENARIO_MODULE_ADDRESS, address, on_pressed)

    async def async_all_off(self) -> None:
        """Fire the all-off scenario and mark every relay off."""
        await self.async_activate_scenario(self._require_all_off_address())

    async def async_reset(self) -> None:
        """
        Resynchronise the modules with the inferred state.

        Fires the all-off scenario (every relay is then physically off), then
        presses every relay believed on. If a press fails midway, the relays
        not reached stay off in the model, like on the bus.
        """
        address = self._require_all_off_address()
        async with self._send_lock:
            believed_on = [relay for relay in self._relays if relay.on]
            await self._press_and_release(
                SCENARIO_MODULE_ADDRESS, address, self._mark_all_off
            )
            for relay in believed_on:
                await self._press_and_release(
                    relay.module.address, relay.address, self._flip(relay)
                )

    def _require_all_off_address(self) -> int:
        if self.all_off_address is None:
            msg = "no all-off scenario configured"
            raise BiomatxNotConfiguredError(msg)
        return self.all_off_address
