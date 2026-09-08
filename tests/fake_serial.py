"""
In-memory stand-in for ``serial_asyncio.open_serial_connection``.

The hub only needs an ``asyncio.StreamReader`` to read from and an object with
``write`` / ``drain`` / ``close`` to write to. This fake provides both, records
every byte written and every connection attempt, and lets a test feed frames,
drop the link (EOF, as when the adapter is unplugged) or fail it with an
exception (as pyserial does on I/O errors).
"""

from __future__ import annotations

import asyncio
from typing import Any


class FakeStreamWriter:
    """Writer half of the fake link."""

    def __init__(self, link: FakeSerialLink, reader: asyncio.StreamReader) -> None:
        """Bind the writer to its link and to the reader it closes."""
        self._link = link
        self._reader = reader
        self.closed = False

    def write(self, data: bytes) -> None:
        """Record written bytes, or raise the failure a test armed."""
        if self._link.fail_write is not None:
            exc, self._link.fail_write = self._link.fail_write, None
            raise exc
        self._link.written.extend(data)

    async def drain(self) -> None:
        """Nothing is buffered in the fake."""

    def close(self) -> None:
        """Close the link: the reader sees EOF, like a closed serial port."""
        self.closed = True
        if not self._reader.at_eof():
            self._reader.feed_eof()

    def is_closing(self) -> bool:
        """Return whether ``close`` was called."""
        return self.closed

    async def wait_closed(self) -> None:
        """Nothing to wait for in the fake."""


class FakeSerialLink:
    """Replacement for the serial port, driven by the tests."""

    def __init__(self) -> None:
        """Start with no connection and nothing written."""
        self.opens: list[dict[str, Any]] = []
        self.written = bytearray()
        self.fail_open: Exception | None = None
        self.fail_write: Exception | None = None
        self.reader: asyncio.StreamReader | None = None
        self.writer: FakeStreamWriter | None = None

    async def open_serial_connection(
        self, **kwargs: Any
    ) -> tuple[asyncio.StreamReader, FakeStreamWriter]:
        """Mimic ``serial_asyncio.open_serial_connection``."""
        self.opens.append(kwargs)
        if self.fail_open is not None:
            exc, self.fail_open = self.fail_open, None
            raise exc
        self.reader = asyncio.StreamReader()
        self.writer = FakeStreamWriter(self, self.reader)
        return self.reader, self.writer

    def feed(self, hex_frames: str) -> None:
        """Make bytes arrive on the bus, e.g. ``feed("50 00 50 80")``."""
        if self.reader is None:
            msg = "no open connection to feed"
            raise RuntimeError(msg)
        self.reader.feed_data(bytes.fromhex(hex_frames))

    def drop_link(self) -> None:
        """Simulate an unplugged adapter: the reader hits EOF."""
        if self.reader is not None:
            self.reader.feed_eof()

    def fail_link(self, exc: Exception) -> None:
        """Simulate an I/O error surfacing from the reader."""
        if self.reader is not None:
            self.reader.set_exception(exc)

    def frames_written(self) -> list[str]:
        """Return the written bytes grouped two by two, e.g. ``["50 00", "50 80"]``."""
        data = bytes(self.written)
        return [data[i : i + 2].hex(" ") for i in range(0, len(data), 2)]

    def clear(self) -> None:
        """Forget what was written so far."""
        self.written.clear()


async def settle(rounds: int = 10) -> None:
    """Yield to the event loop long enough for fed bytes to be processed."""
    for _ in range(rounds):
        await asyncio.sleep(0)
