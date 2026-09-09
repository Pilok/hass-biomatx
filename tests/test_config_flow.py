"""Tests for the config flow: user step, reconfigure step, validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_DEVICE
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biomatx.const import (
    CONF_ALL_OFF_ADDRESS,
    CONF_ALL_OFF_SCENARIO,
    CONF_MODULE_COUNT,
    DOMAIN,
)

from .conftest import MODULE_COUNT, URL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    import voluptuous as vol

    from .fake_serial import FakeSerialLink

OTHER_URL = "socket://192.168.1.50:8899"


@pytest.fixture(autouse=True)
def _no_setup() -> Any:
    """Keep the flow tests about the flow: do not set up created entries."""
    with patch("custom_components.biomatx.async_setup_entry", return_value=True):
        yield


async def start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    """Open the user step and return its form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_creates_entry_with_unique_id_and_version_2(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """The device is probed, then stored with the scenario converted to 0-based."""
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4, CONF_ALL_OFF_SCENARIO: 6},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "BioMatX"
    assert entry.unique_id == URL
    assert entry.version == 2
    assert entry.data == {
        CONF_DEVICE: URL,
        CONF_MODULE_COUNT: 4,
        CONF_ALL_OFF_ADDRESS: 5,
    }
    assert len(fake_serial.opens) == 1
    assert fake_serial.frames_written() == []
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is True


async def test_user_flow_scenario_is_optional(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """Without an all-off scenario the key is simply absent from the entry."""
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_ALL_OFF_ADDRESS not in result["result"].data


async def test_user_flow_cannot_connect_shows_error_then_recovers(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """A port that cannot be opened keeps the form up with a translated error."""
    fake_serial.fail_open = OSError("no such device")
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_aborts_when_device_already_configured(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """One serial device is one bus: a second entry for it is refused."""
    mock_config_entry.add_to_hass(hass)
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert fake_serial.opens == []


@pytest.mark.parametrize(
    ("field", "value"),
    [(CONF_MODULE_COUNT, 0), (CONF_MODULE_COUNT, 8), (CONF_ALL_OFF_SCENARIO, 11)],
)
async def test_user_flow_rejects_values_out_of_range(
    hass: HomeAssistant, fake_serial: FakeSerialLink, field: str, value: int
) -> None:
    """Module counts are 1-7 and scenario numbers 1-10, like the front panels."""
    result = await start_user_flow(hass)
    data = {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4, field: value}
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def start_reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dict[str, Any]:
    """Open the reconfigure step of ``entry`` and return its form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return result


def suggested(schema: vol.Schema, key: str) -> Any:
    """Return the value the form proposes for ``key``."""
    for marker in schema.schema:
        if marker == key:
            if marker.description and "suggested_value" in marker.description:
                return marker.description["suggested_value"]
            return marker.default()
    msg = f"{key} not in schema"
    raise KeyError(msg)


async def test_reconfigure_form_is_prefilled_one_based(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """The stored 0-based scenario address 5 is shown as scenario 6."""
    mock_config_entry.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    schema = result["data_schema"]
    assert suggested(schema, CONF_DEVICE) == URL
    assert suggested(schema, CONF_MODULE_COUNT) == MODULE_COUNT
    assert suggested(schema, CONF_ALL_OFF_SCENARIO) == 6


async def test_reconfigure_updates_data_and_reloads(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Changing the module count and the scenario updates the entry in place."""
    mock_config_entry.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: URL, CONF_MODULE_COUNT: 3, CONF_ALL_OFF_SCENARIO: 2},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {
        CONF_DEVICE: URL,
        CONF_MODULE_COUNT: 3,
        CONF_ALL_OFF_ADDRESS: 1,
    }
    assert mock_config_entry.unique_id == URL


async def test_reconfigure_blank_scenario_removes_key(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Clearing the scenario field forgets the stored address."""
    mock_config_entry.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: URL, CONF_MODULE_COUNT: MODULE_COUNT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert CONF_ALL_OFF_ADDRESS not in mock_config_entry.data


async def test_reconfigure_new_device_updates_unique_id(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Moving the bus to another adapter keeps the entry and its entities."""
    mock_config_entry.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: OTHER_URL, CONF_MODULE_COUNT: MODULE_COUNT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == OTHER_URL
    assert mock_config_entry.data[CONF_DEVICE] == OTHER_URL


async def test_reconfigure_rejects_device_owned_by_other_entry(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Two entries cannot share one serial device."""
    mock_config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_URL,
        version=2,
        data={CONF_DEVICE: OTHER_URL, CONF_MODULE_COUNT: 2},
    )
    other.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: OTHER_URL, CONF_MODULE_COUNT: MODULE_COUNT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_DEVICE] == URL


async def test_reconfigure_cannot_connect_shows_error(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """A new device that cannot be opened is not stored."""
    mock_config_entry.add_to_hass(hass)
    fake_serial.fail_open = OSError("no such device")
    result = await start_reconfigure(hass, mock_config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: OTHER_URL, CONF_MODULE_COUNT: MODULE_COUNT}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_config_entry.data[CONF_DEVICE] == URL
