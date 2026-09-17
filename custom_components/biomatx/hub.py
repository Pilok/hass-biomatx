"""
Serial hub: owns the RS485 link to the BioMatX modules.

The hub opens the serial port, feeds the bytes to the codec of the bus
protocol, keeps the state of every relay and button, notifies listeners, sends
commands and reopens the port when the link drops. It has no Home Assistant
dependency so it can be unit tested with an in-memory link.

Two protocols exist (``protocol/``). On the **legacy** firmware the modules
never report state: the hub infers it from the presses it sees and sends, and
a command flips the inferred state as soon as the press frame is written. On
the **master** firmware every module reports its relays every 3 s and after
each change: the hub takes state from those reports only, a command waits for
the report that confirms it (the module's next report decides when none comes
in time: late confirmation, one more press if the relay did not move, error if
the module fell silent), and a module silent for ``MODULE_TIMEOUT`` is
unavailable, with one more ``MODULE_TIMEOUT`` of grace during which a command
waits for its return instead of being refused. When the protocol is not known
yet, the hub listens and detects it from the first valid frame.

Transport notes. ``serialx`` (the serial library of Home Assistant core)
opens USB serial devices with exclusive access and reports write errors
through the reader, never from ``write()``: the hub therefore checks the link
before writing and treats any reader error as a lost link. Ethernet gateways
(``socket://host:port``) are reached with a plain asyncio TCP connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Literal

import serialx
from serialx import SerialException

from .protocol import (
    Codec,
    EventFrame,
    Frame,
    InvalidFrame,
    ParserStats,
    Protocol,
    StateFrame,
    codec_for,
    detect,
)
from .protocol.model import (
    SCENARIO_MODULE_ADDRESS,
    Installation,
    Module,
    Relay,
    Switch,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_LOGGER = logging.getLogger(__name__)

BAUDRATE = 19200
FRAME_GAP = 0.2
"""Seconds to wait after each frame, so the modules have time to process it."""
RECONNECT_DELAYS: tuple[float, ...] = (1, 2, 5, 10, 30, 60)
"""Seconds between reconnection attempts; the last value repeats."""
STATE_REPORT_PERIOD = 3.0
"""Seconds between two unsolicited state reports of a master module."""
CONFIRM_TIMEOUT = STATE_REPORT_PERIOD + 0.5
"""
Seconds to wait for the state report that confirms a command (master).

A module usually reports within a second of a change, but some only answer at
their next periodic report: the wait covers one full period plus a margin.
Measured on 2026-09-14: three commands confirmed 2 to 3 s after the press.
"""
MODULE_TIMEOUT = 10.0
"""Seconds without a state report before a master module is unavailable."""
INVALID_FRAME_LOG_INTERVAL = 60.0
"""
Seconds between two WARNING lines about frames the format cannot carry.

The first such frame of a burst is a WARNING; the following ones are DEBUG
until the interval has passed, and the next WARNING says how many were skipped.
Read when a frame arrives, so a test can shorten it.
"""
CLOSE_TIMEOUT = 2
"""Seconds to wait for the reader task and the transport to finish closing."""
READ_CHUNK = 64
DETECT_BUFFER = 256
"""Bytes kept while the protocol is unknown; older bytes are dropped."""
SCENARIO_EMITTER = 0
"""
Emitter module claimed by master scenario commands.

Unverified on hardware: the showroom's wall scenario button emitted as the
module it was wired on (``07 81 84 40``); whether the virtual module 7 is
accepted as emitter is unknown, so the first real module is used. Checked at
verification stage 5 of the plan.
"""
SOCKET_URL_PREFIX = "socket://"

type DeviceKind = Literal["relay", "switch"]
type DeviceKey = tuple[DeviceKind, int, int]
"""(kind, module address, switch address), all 0-based like the frames."""
type Listener = Callable[[], None]
type LinkListener = Callable[[bool], None]
type ProtocolListener = Callable[[Protocol], None]
type InvalidFrameListener = Callable[[InvalidFrame], None]
type Confirmation = Callable[[StateFrame], bool]


def _any_report(_frame: StateFrame) -> bool:
    """Accept any state report: used to wait for a module to speak at all."""
    return True


class BiomatxError(Exception):
    """Base class for hub errors."""


class BiomatxConnectionError(BiomatxError):
    """The serial port could not be opened."""


class BiomatxLinkError(BiomatxError):
    """The link is down or a write failed."""


class BiomatxProtocolUnknownError(BiomatxLinkError):
    """No frame has been seen yet, so the hub cannot encode a command."""


class BiomatxCommandError(BiomatxError):
    """Two presses left the relay unmoved according to the module's own reports."""


class BiomatxModuleUnavailableError(BiomatxError):
    """The module is not reporting (never did, or fell silent): no blind command."""


class BiomatxNotConfiguredError(BiomatxError):
    """The requested operation needs the all-off scenario, which is not set."""


class BiomatxNotSupportedError(BiomatxError):
    """The requested operation has no meaning on this protocol."""


class BiomatxHub:
    """Owner of the serial link and of the bus state."""

    def __init__(  # noqa: PLR0913  # three bus facts plus keyword-only timing knobs
        self,
        url: str,
        module_count: int,
        all_off_address: int | None,
        *,
        protocol: Protocol | None = None,
        frame_gap: float | None = None,
        reconnect_delays: Sequence[float] | None = None,
        confirm_timeout: float | None = None,
        module_timeout: float | None = None,
    ) -> None:
        """
        Model ``module_count`` modules plus the scenario module.

        ``url`` is a serial device path, a serialx URL, or ``socket://host:port``
        for an Ethernet gateway. ``all_off_address`` is the 0-based scenario
        button that turns every relay off, or ``None`` when no such scenario
        exists. ``protocol`` is the firmware family of the bus, or ``None`` to
        detect it from the first valid frame. The timing arguments default to
        the module constants, read when the hub is built so tests can shorten
        them.
        """
        self.url = url
        self.module_count = module_count
        self.all_off_address = all_off_address
        self._frame_gap = FRAME_GAP if frame_gap is None else frame_gap
        self._reconnect_delays = tuple(
            RECONNECT_DELAYS if reconnect_delays is None else reconnect_delays
        )
        self._confirm_timeout = (
            CONFIRM_TIMEOUT if confirm_timeout is None else confirm_timeout
        )
        self._module_timeout = (
            MODULE_TIMEOUT if module_timeout is None else module_timeout
        )
        self._codec: Codec | None = None if protocol is None else codec_for(protocol)
        self._detect_buffer = bytearray()
        self._installation = Installation(module_count)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._closed = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None
        self._retry_index = 0
        self._send_lock = asyncio.Lock()
        self._listeners: dict[DeviceKey, list[Listener]] = {}
        self._link_listeners: list[LinkListener] = []
        self._protocol_listeners: list[ProtocolListener] = []
        self._invalid_listeners: list[InvalidFrameListener] = []
        self._last_invalid_warning: float | None = None
        self._invalid_since_warning = 0
        self._frames_received = 0
        self._frames_rejected = 0
        self._bytes_discarded = 0
        self._noise_run = 0
        self._last_seen: dict[int, float] = {}
        self._module_up: dict[int, bool] = {}
        self._module_timers: dict[int, asyncio.TimerHandle] = {}
        self._confirmations: list[
            tuple[int, Confirmation, asyncio.Future[StateFrame]]
        ] = []

    # --- state exposed to entities and diagnostics --------------------------

    @property
    def connected(self) -> bool:
        """Return whether the serial link is currently open."""
        return self._connected

    @property
    def protocol(self) -> Protocol | None:
        """Return the protocol of the bus, ``None`` while not detected."""
        return None if self._codec is None else self._codec.protocol

    @property
    def reports_state(self) -> bool:
        """Return whether the modules report their relay states (master)."""
        return self._codec is not None and self._codec.reports_state

    @property
    def stats(self) -> ParserStats:
        """Return the codec counters (checksum errors, resyncs, noise...)."""
        return ParserStats() if self._codec is None else self._codec.stats

    @property
    def frames_received(self) -> int:
        """Return the number of frames decoded and applied since start."""
        return self._frames_received

    @property
    def frames_dropped(self) -> int:
        """Return the number of complete frames rejected since start."""
        return self._frames_rejected + self.stats.invalid_frames

    @property
    def bytes_dropped(self) -> int:
        """Return the number of stray bytes seen outside a frame since start."""
        return self.stats.noise_bytes + self._bytes_discarded

    @property
    def modules(self) -> list[Module]:
        """Return the configured modules, scenario module excluded."""
        return self._installation.modules

    @property
    def scenario_module(self) -> Module:
        """Return the virtual module that carries the scenarios."""
        return self._installation.scenario_module

    @property
    def relays(self) -> list[Relay]:
        """Return every relay of the configured modules."""
        return self._installation.relays

    @property
    def switches(self) -> list[Switch]:
        """Return every button, scenario buttons included."""
        return self._installation.switches

    def relay(self, module: int, address: int) -> Relay:
        """Return one relay by 0-based module and relay address."""
        return self._installation.relay(module, address)

    def switch(self, module: int, address: int) -> Switch:
        """Return one button by 0-based module and button address."""
        return self._installation.switch(module, address)

    def module_available(self, address: int) -> bool:
        """
        Return whether module ``address`` can be trusted right now.

        Legacy modules never report, so the link is the only signal. Master
        modules are available from their first state report until they stay
        silent for ``MODULE_TIMEOUT``. The scenario module is virtual and never
        reports: it is available whenever the link is.
        """
        if not self._connected:
            return False
        if not self.reports_state or address == SCENARIO_MODULE_ADDRESS:
            return True
        return self._module_up.get(address, False)

    def module_last_seen(self, address: int) -> float | None:
        """Return the monotonic time of the last state report of a module."""
        return self._last_seen.get(address)

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

    def add_protocol_listener(self, listener: ProtocolListener) -> Callable[[], None]:
        """Call ``listener(protocol)`` when the bus protocol is detected at runtime."""
        self._protocol_listeners.append(listener)

        def _unsubscribe() -> None:
            self._protocol_listeners.remove(listener)

        return _unsubscribe

    def add_invalid_frame_listener(
        self, listener: InvalidFrameListener
    ) -> Callable[[], None]:
        """Call ``listener(frame)`` for each checksummed frame the format cannot use."""
        self._invalid_listeners.append(listener)

        def _unsubscribe() -> None:
            self._invalid_listeners.remove(listener)

        return _unsubscribe

    def _notify(self, key: DeviceKey) -> None:
        for listener in list(self._listeners.get(key, ())):
            try:
                listener()
            except Exception:
                _LOGGER.exception("listener for %s failed", key)

    def _notify_module(self, address: int) -> None:
        """Wake every entity of a module (its availability changed)."""
        module = self._installation.module(address)
        for relay in module.relays:
            self._notify(("relay", address, relay.address))
        for switch in module.switches:
            self._notify(("switch", address, switch.address))

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
                reader, writer = await serialx.open_serial_connection(
                    url=self.url,
                    baudrate=BAUDRATE,
                    bytesize=serialx.EIGHTBITS,
                    parity=serialx.PARITY_NONE,
                    stopbits=serialx.STOPBITS_ONE,
                    xonxoff=False,
                    rtscts=False,
                )
        except (OSError, SerialException, ValueError) as err:
            msg = f"cannot open {self.url}: {err}"
            raise BiomatxConnectionError(msg) from err
        self._reader = reader
        self._writer = writer
        # A frame cut by the outage must not be glued to the new link's bytes.
        if self._codec is not None:
            self._codec.reset()
        self._detect_buffer.clear()
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
            self._forget_modules()
            self._set_connected(connected=False)
            self._run_task = None

    async def async_close(self) -> None:
        """Close the link and stop the reader loop without reconnecting."""
        self._closed.set()
        self._forget_modules()
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
        self._forget_modules()
        for _module, _confirmed, future in self._confirmations:
            if not future.done():
                future.set_exception(BiomatxLinkError(f"link to {self.url} lost"))
        self._set_connected(connected=False)

    def _forget_modules(self) -> None:
        """Forget the modules' availability; a fresh report earns it again."""
        for timer in self._module_timers.values():
            timer.cancel()
        self._module_timers.clear()
        self._module_up.clear()
        self._last_seen.clear()

    # --- receiving --------------------------------------------------------------

    async def _read_frames(self, reader: asyncio.StreamReader) -> None:
        """Decode frames until end of stream; raise on I/O errors."""
        while True:
            chunk = await reader.read(READ_CHUNK)
            if not chunk:
                return
            # Data flows: the link is proven, the next outage starts a new backoff.
            self._retry_index = 0
            codec = self._codec
            if codec is None:
                codec = self._detect_protocol(chunk)
                if codec is None:
                    continue
                chunk = bytes(self._detect_buffer)
                self._detect_buffer.clear()
            noise_before = codec.stats.noise_bytes
            frames = codec.feed(chunk)
            self._noise_run += codec.stats.noise_bytes - noise_before
            if frames:
                self._flush_noise()
            for frame in frames:
                self._handle_frame(frame)

    def _detect_protocol(self, chunk: bytes) -> Codec | None:
        """Buffer ``chunk`` until a valid frame names the protocol."""
        self._detect_buffer += chunk
        excess = len(self._detect_buffer) - DETECT_BUFFER
        if excess > 0:
            del self._detect_buffer[:excess]
            self._bytes_discarded += excess
        protocol = detect(bytes(self._detect_buffer))
        if protocol is None:
            return None
        _LOGGER.info("bus %s speaks the %s protocol", self.url, protocol.value)
        self._codec = codec_for(protocol)
        # The meaning of every entity's state and availability just changed.
        for address in range(self.module_count):
            self._notify_module(address)
        self._notify_module(SCENARIO_MODULE_ADDRESS)
        for listener in list(self._protocol_listeners):
            try:
                listener(protocol)
            except Exception:
                _LOGGER.exception("protocol listener failed")
        return self._codec

    def _flush_noise(self) -> None:
        if self._noise_run:
            _LOGGER.debug(
                "dropped %d bytes outside a frame before resynchronising",
                self._noise_run,
            )
            self._noise_run = 0

    def _handle_frame(self, frame: Frame) -> None:
        if isinstance(frame, StateFrame):
            self._handle_state(frame)
        elif isinstance(frame, EventFrame):
            self._handle_event(frame)
        else:
            self._handle_invalid(frame)

    def _handle_invalid(self, frame: InvalidFrame) -> None:
        """
        Report a checksummed frame the format cannot carry; touch no entity.

        The detectors emit one on purpose ("module 4, output 11", a virtual
        coordination relay according to Enersol) and the master firmware acts
        on it as a press on relay 1, so the frame is worth a WARNING, once per
        burst, and a notification to the integration. The codec already
        counted it in ``stats.invalid_frames``.
        """
        described = (
            " ".join(
                part
                for part in (
                    None if frame.target is None else f"module {frame.target + 1}",
                    None if frame.button is None else f"output {frame.button + 1}",
                    None
                    if frame.pressed is None
                    else ("pressed" if frame.pressed else "released"),
                    None
                    if frame.emitter is None
                    else f"emitted by module {frame.emitter + 1}",
                )
                if part is not None
            )
            or "unreadable fields"
        )
        now = time.monotonic()
        last = self._last_invalid_warning
        if last is None or now - last >= INVALID_FRAME_LOG_INTERVAL:
            skipped = self._invalid_since_warning
            _LOGGER.warning(
                "bus frame %s the format cannot carry (%s): %s%s",
                frame.raw.hex(" "),
                frame.reason,
                described,
                f", {skipped} more since the last warning" if skipped else "",
            )
            self._last_invalid_warning = now
            self._invalid_since_warning = 0
        else:
            self._invalid_since_warning += 1
            _LOGGER.debug(
                "bus frame %s the format cannot carry (%s): %s",
                frame.raw.hex(" "),
                frame.reason,
                described,
            )
        for listener in list(self._invalid_listeners):
            try:
                listener(frame)
            except Exception:
                _LOGGER.exception("invalid frame listener failed")

    def _handle_event(self, frame: EventFrame) -> None:
        if not (
            self._is_known_module(frame.target) and self._is_known_module(frame.emitter)
        ):
            self._frames_rejected += 1
            _LOGGER.debug(
                "dropping event for module %d button %d from module %d "
                "(unconfigured module or bus collision)",
                frame.target,
                frame.button,
                frame.emitter,
            )
            return
        self._frames_received += 1
        _LOGGER.debug(
            "module %d button %d %s (emitted by module %d)",
            frame.target,
            frame.button,
            "pressed" if frame.pressed else "released",
            frame.emitter,
        )
        switch = self._installation.switch(frame.target, frame.button)
        switch.pressed = frame.pressed
        switch.emitter = frame.emitter
        switch.events += 1
        if not self.reports_state:
            self._infer_from_press(frame)
        self._notify(("switch", frame.target, frame.button))

    def _infer_from_press(self, frame: EventFrame) -> None:
        """Legacy: a press flips its relay; the all-off scenario clears everything."""
        if not frame.pressed:
            return
        if frame.target == SCENARIO_MODULE_ADDRESS:
            if frame.button == self.all_off_address:
                self._mark_all_off()
            return
        relay = self._installation.relay(frame.target, frame.button)
        relay.on = not relay.on
        self._notify(("relay", frame.target, frame.button))

    def _handle_state(self, frame: StateFrame) -> None:
        if frame.module >= self.module_count:
            self._frames_rejected += 1
            _LOGGER.debug(
                "dropping state report of unconfigured module %d", frame.module
            )
            return
        self._frames_received += 1
        for relay in self._installation.module(frame.module).relays:
            on = frame.is_on(relay.address)
            if relay.on != on:
                relay.on = on
                self._notify(("relay", frame.module, relay.address))
        self._touch_module(frame.module)
        for module, confirmed, future in list(self._confirmations):
            if module == frame.module and not future.done() and confirmed(frame):
                future.set_result(frame)

    def _touch_module(self, address: int) -> None:
        """Record a state report: the module is alive for another timeout."""
        self._last_seen[address] = time.monotonic()
        if self._closed.is_set():
            return  # bytes decoded while closing must not arm a timer
        if (timer := self._module_timers.pop(address, None)) is not None:
            timer.cancel()
        self._module_timers[address] = asyncio.get_running_loop().call_later(
            self._module_timeout, self._module_silent, address
        )
        if not self._module_up.get(address, False):
            self._module_up[address] = True
            _LOGGER.debug("module %d is reporting", address + 1)
            self._notify_module(address)

    def _module_silent(self, address: int) -> None:
        self._module_timers.pop(address, None)
        self._module_up[address] = False
        _LOGGER.warning(
            "module %d sent no state report for %.0f s, marking it unavailable",
            address + 1,
            self._module_timeout,
        )
        self._notify_module(address)

    def _mark_all_off(self) -> None:
        for relay in self._installation.relays:
            if relay.on:
                relay.on = False
                self._notify(("relay", relay.module.address, relay.address))

    # --- sending ----------------------------------------------------------------

    def _require_codec(self) -> Codec:
        if self._codec is None:
            msg = f"protocol of {self.url} not detected yet, no frame seen"
            raise BiomatxProtocolUnknownError(msg)
        return self._codec

    async def _send(self, data: bytes) -> None:
        writer = self._writer
        if not self._connected or writer is None or writer.is_closing():
            self._on_link_lost(None)
            msg = f"link to {self.url} is down"
            raise BiomatxLinkError(msg)
        try:
            writer.write(data)
            await writer.drain()
        except (OSError, SerialException) as err:
            self._on_link_lost(err)
            msg = f"write to {self.url} failed: {err}"
            raise BiomatxLinkError(msg) from err
        await asyncio.sleep(self._frame_gap)

    async def _press_and_release(
        self,
        module: int,
        switch: int,
        on_pressed: Callable[[], None] | None = None,
        *,
        emitter: int | None = None,
    ) -> None:
        """
        Send a press then a release; ``on_pressed`` runs once the press is out.

        The modules act on the press frame, so an inferred state must change
        as soon as that frame is written, even if the release then fails.
        Callers hold ``_send_lock``.
        """
        codec = self._require_codec()
        await self._send(
            codec.encode_button(module, switch, pressed=True, emitter=emitter)
        )
        if on_pressed is not None:
            on_pressed()
        await self._send(
            codec.encode_button(module, switch, pressed=False, emitter=emitter)
        )

    async def _await_report(
        self, module: int, wanted: Confirmation, within: float
    ) -> StateFrame | None:
        """
        Wait up to ``within`` seconds for a report of ``module`` satisfying ``wanted``.

        Returns the report, or ``None`` on timeout. A link loss raises
        ``BiomatxLinkError`` at once. Callers hold ``_send_lock``.
        """
        future: asyncio.Future[StateFrame] = asyncio.get_running_loop().create_future()
        entry = (module, wanted, future)
        self._confirmations.append(entry)
        try:
            return await asyncio.wait_for(future, timeout=within)
        except TimeoutError:
            return None
        finally:
            self._confirmations.remove(entry)

    async def _ensure_module_reporting(self, module: int) -> None:
        """
        Make sure ``module`` has a current state before acting on it.

        A module that never reported (or not since the link came back) is
        refused: a default or restored relay state is not a bus fact. A module
        seen before but silent now (module 3 stayed quiet for 10 s under a
        burst of commands on 2026-09-14) is given a grace of one more
        ``MODULE_TIMEOUT`` after it was declared silent, counted from its last
        report so that queued commands share the same deadline instead of each
        waiting a full timeout; nothing is written meanwhile. Callers hold
        ``_send_lock``.
        """
        if self.module_available(module):
            return
        last_seen = self._last_seen.get(module)
        if last_seen is None:
            msg = f"module {module + 1} has not reported its state"
            raise BiomatxModuleUnavailableError(msg)
        deadline = last_seen + 2 * self._module_timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            msg = f"module {module + 1} is not reporting"
            raise BiomatxModuleUnavailableError(msg)
        _LOGGER.debug("module %d is silent, waiting for its next report", module + 1)
        if await self._await_report(module, _any_report, remaining) is None:
            msg = f"module {module + 1} is not reporting"
            raise BiomatxModuleUnavailableError(msg)

    async def _confirmed_command(self, relay: Relay, confirmed: Confirmation) -> None:
        """
        Press ``relay``'s button until a state report satisfies ``confirmed``.

        The confirmation is registered before the press is written: the module
        often answers within the press/release gap, and that report must count.
        Without a confirmation in ``CONFIRM_TIMEOUT``, the next report of the
        module decides, awaited for one more ``CONFIRM_TIMEOUT`` so a command
        never holds the bus lock for more than four periods: it may confirm
        late (pressing again would undo it), or show the press had no effect
        (a lost frame: press once more). A module that stops reporting is not
        pressed blind. Assumed: a report received 3.5 s after the press shows
        the post-press state, since the module acts on the press at once and a
        frame does not sit on a 19200 baud bus; a lost change report is then
        followed by a periodic one carrying the same state. Callers hold
        ``_send_lock``.
        """
        module = relay.module.address
        for attempt in range(2):
            if attempt:
                _LOGGER.warning(
                    "module %d relay %d did not move, pressing again",
                    module + 1,
                    relay.address + 1,
                )
            if (
                await self._press_and_await(module, relay.address, confirmed)
                is not None
            ):
                return
            fresh = await self._await_report(module, _any_report, self._confirm_timeout)
            if fresh is None:
                msg = (
                    f"module {module + 1} did not confirm the command "
                    "and stopped reporting"
                )
                raise BiomatxModuleUnavailableError(msg)
            if confirmed(fresh):
                return
        msg = f"module {module + 1} did not confirm the command after two presses"
        raise BiomatxCommandError(msg)

    async def _press_and_await(
        self, module: int, switch: int, confirmed: Confirmation
    ) -> StateFrame | None:
        """Register the confirmation, press and release, wait ``CONFIRM_TIMEOUT``."""
        future: asyncio.Future[StateFrame] = asyncio.get_running_loop().create_future()
        entry = (module, confirmed, future)
        self._confirmations.append(entry)
        try:
            await self._press_and_release(module, switch)
            try:
                return await asyncio.wait_for(future, timeout=self._confirm_timeout)
            except TimeoutError:
                return None
        finally:
            self._confirmations.remove(entry)
            if future.done() and not future.cancelled():
                # A write failure marks the link lost, which fails this future
                # too; read the exception so the loop does not log it as lost.
                future.exception()

    def _flip(self, relay: Relay) -> Callable[[], None]:
        def _apply() -> None:
            relay.on = not relay.on
            self._notify(("relay", relay.module.address, relay.address))

        return _apply

    async def async_toggle(self, relay: Relay) -> None:
        """
        Simulate a button press for ``relay``.

        Legacy: the inferred state flips with the press. Master: the call
        returns once the module reports the relay in the other state, with
        the same late-report, second-press and silent-module handling as
        ``async_set_relay``.
        """
        async with self._send_lock:
            if not self.reports_state:
                await self._press_and_release(
                    relay.module.address, relay.address, self._flip(relay)
                )
                return
            await self._ensure_module_reporting(relay.module.address)
            was_on = relay.on
            await self._confirmed_command(
                relay, lambda frame: frame.is_on(relay.address) != was_on
            )

    async def async_set_relay(self, relay: Relay, *, on: bool) -> None:
        """
        Bring ``relay`` to ``on``; a no-op when it is already there.

        The check and the press happen under the same lock, so two concurrent
        commands for one relay cannot toggle it twice. On master the call
        returns once the module reports the wanted state, or raises
        ``BiomatxCommandError`` when two presses leave it unmoved or the module
        goes silent.
        """
        async with self._send_lock:
            if not self.reports_state:
                if relay.on != on:
                    await self._press_and_release(
                        relay.module.address, relay.address, self._flip(relay)
                    )
                return
            await self._ensure_module_reporting(relay.module.address)
            if relay.on == on:
                return
            await self._confirmed_command(
                relay, lambda frame: frame.is_on(relay.address) == on
            )

    async def async_activate_scenario(self, address: int) -> None:
        """
        Trigger the scenario button ``address`` (0-based) of module 7.

        Legacy: triggering the all-off scenario marks every relay off, as
        observing it on the bus does. Master: the modules report their new
        states by themselves within a second.
        """
        async with self._send_lock:
            if self.reports_state:
                await self._press_and_release(
                    SCENARIO_MODULE_ADDRESS, address, emitter=SCENARIO_EMITTER
                )
                return
            on_pressed = self._mark_all_off if address == self.all_off_address else None
            await self._press_and_release(SCENARIO_MODULE_ADDRESS, address, on_pressed)

    async def async_all_off(self) -> None:
        """Fire the all-off scenario."""
        await self.async_activate_scenario(self._require_all_off_address())

    async def async_reset(self) -> None:
        """
        Legacy only: resynchronise the modules with the inferred state.

        Fires the all-off scenario (every relay is then physically off), then
        presses every relay believed on. If a press fails midway, the relays
        not reached stay off in the model, like on the bus.
        """
        if self.reports_state:
            msg = "the modules report their state; there is nothing to resynchronise"
            raise BiomatxNotSupportedError(msg)
        address = self._require_all_off_address()
        async with self._send_lock:
            believed_on = [relay for relay in self._installation.relays if relay.on]
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


__all__ = [
    "BiomatxCommandError",
    "BiomatxConnectionError",
    "BiomatxError",
    "BiomatxHub",
    "BiomatxLinkError",
    "BiomatxModuleUnavailableError",
    "BiomatxNotConfiguredError",
    "BiomatxNotSupportedError",
    "BiomatxProtocolUnknownError",
    "DeviceKey",
    "DeviceKind",
]
