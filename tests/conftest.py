"""Shared pytest fixtures for the BioMatX integration tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import serial_asyncio

from custom_components.biomatx import hub as hub_module
from custom_components.biomatx.const import (
    CONF_ALL_OFF_ADDRESS,
    CONF_MODULE_COUNT,
    DOMAIN,
)

from .fake_serial import FakeSerialLink

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

URL = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_TEST-if00-port0"
MODULE_COUNT = 4
ALL_OFF_ADDRESS = 5

type SetupIntegration = Callable[[MockConfigEntry], Awaitable[MockConfigEntry]]


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load integrations from custom_components/."""


@pytest.fixture
def fake_serial(monkeypatch: pytest.MonkeyPatch) -> FakeSerialLink:
    """Replace pyserial's async opener with the in-memory link."""
    link = FakeSerialLink()
    monkeypatch.setattr(
        serial_asyncio, "open_serial_connection", link.open_serial_connection
    )
    monkeypatch.setattr(asyncio, "open_connection", link.open_tcp_connection)
    return link


@pytest.fixture
def _fast_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the frame gap and reconnection delays so tests run instantly."""
    monkeypatch.setattr(hub_module, "FRAME_GAP", 0)
    monkeypatch.setattr(hub_module, "RECONNECT_DELAYS", (0,))


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a version 2 config entry for a 4-module bus with an all-off scenario."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="BioMatX",
        unique_id=URL,
        version=2,
        data={
            "device": URL,
            CONF_MODULE_COUNT: MODULE_COUNT,
            CONF_ALL_OFF_ADDRESS: ALL_OFF_ADDRESS,
        },
    )


@pytest.fixture
def setup_integration(
    hass: HomeAssistant,
    fake_serial: FakeSerialLink,
    _fast_bus: None,
) -> Callable[[MockConfigEntry], Awaitable[MockConfigEntry]]:
    """Return a helper that adds an entry to hass and sets it up."""

    async def _setup(entry: MockConfigEntry) -> MockConfigEntry:
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup
