"""Tests for the config entry lifecycle: setup, unload, devices, migration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biomatx.const import CONF_MODULE_COUNT, DOMAIN

from .conftest import MODULE_COUNT, URL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .conftest import SetupIntegration
    from .fake_serial import FakeSerialLink


async def test_setup_entry_loads_and_opens_port(
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A loadable entry opens the configured port exactly once."""
    entry = await setup_integration(mock_config_entry)
    assert entry.state is ConfigEntryState.LOADED
    assert len(fake_serial.opens) == 1
    assert fake_serial.opens[0]["url"] == URL
    assert entry.runtime_data.hub.connected is True


async def test_setup_entry_sends_no_frames(
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Setting up must not touch the lights: it enables an observe-only stage."""
    await setup_integration(mock_config_entry)
    assert fake_serial.frames_written() == []


async def test_setup_entry_retries_when_port_cannot_open(
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A missing adapter is a temporary condition: Home Assistant must retry."""
    fake_serial.fail_open = OSError("no such device")
    entry = await setup_integration(mock_config_entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry_closes_link(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Unloading closes the port and stops the reader; no task is left behind."""
    entry = await setup_integration(mock_config_entry)
    hub = entry.runtime_data.hub
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hub.connected is False
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is True


async def test_devices_hub_and_modules_with_via_device(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
) -> None:
    """One hub device, then one device per module linked to the hub."""
    entry = await setup_integration(mock_config_entry)
    registry = dr.async_get(hass)
    hub_device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert hub_device is not None
    assert hub_device.name == "BioMatX bus"
    assert hub_device.manufacturer == "PSO"
    for index in range(MODULE_COUNT):
        module_device = registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}-module-{index}"), entry.entry_id
        )
        assert module_device is not None
        assert module_device.name == f"BioMatX module {index + 1}"
        assert module_device.model == "BioMatX 2110"
        assert module_device.via_device_id == hub_device.id
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}-module-4"), entry.entry_id
        )
        is None
    )


async def test_migrate_v1_drops_serial_wait_and_sets_unique_id(
    setup_integration: SetupIntegration,
) -> None:
    """Upstream version 1 entries carried an inert serial_wait and no unique id."""
    legacy = MockConfigEntry(
        domain=DOMAIN,
        title="biomatx",
        version=1,
        data={"device": URL, CONF_MODULE_COUNT: MODULE_COUNT, "serial_wait": 0.5},
    )
    entry = await setup_integration(legacy)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.unique_id == URL
    assert "serial_wait" not in entry.data
    assert entry.data[CONF_MODULE_COUNT] == MODULE_COUNT


async def test_migrate_refuses_future_version(
    setup_integration: SetupIntegration,
) -> None:
    """An entry written by a newer release is not downgraded silently."""
    future = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id=URL,
        data={"device": URL, CONF_MODULE_COUNT: MODULE_COUNT},
    )
    entry = await setup_integration(future)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_unload_keeps_the_link_when_a_platform_fails_to_unload(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A failed unload leaves the entry loaded, so the bus must stay open."""
    entry = await setup_integration(mock_config_entry)
    hub = entry.runtime_data.hub
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hub.connected is True


async def test_migrate_v1_renames_legacy_unique_ids_and_removes_relay_devices(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
) -> None:
    """Upstream entities keep their entity ids; upstream per-relay devices go."""
    legacy = MockConfigEntry(
        domain=DOMAIN,
        title="biomatx",
        version=1,
        data={"device": URL, CONF_MODULE_COUNT: MODULE_COUNT, "serial_wait": 0.5},
    )
    legacy.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "light", DOMAIN, "1_7", suggested_object_id="1_7", config_entry=legacy
    )
    entity_registry.async_get_or_create(
        "binary_sensor", DOMAIN, "1_7", suggested_object_id="1_7", config_entry=legacy
    )
    entity_registry.async_get_or_create(
        "light",
        DOMAIN,
        "already-migrated",
        suggested_object_id="other",
        config_entry=legacy,
    )
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=legacy.entry_id, identifiers={(DOMAIN, "1_7")}, name="1_7"
    )
    entry = await setup_integration(legacy)
    assert entry.state is ConfigEntryState.LOADED
    light = entity_registry.async_get("light.1_7")
    assert light is not None
    assert light.unique_id == f"{entry.entry_id}-relay-1-7"
    button = entity_registry.async_get("binary_sensor.1_7")
    assert button is not None
    assert button.unique_id == f"{entry.entry_id}-switch-1-7"
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, "1_7"), entry.entry_id)
        is None
    )
    assert hass.states.get("light.1_7") is not None
    assert hass.states.get("light.biomatx_module_2_relay_8") is None
    assert entity_registry.async_get("light.other").unique_id == "already-migrated"
