"""Tests for the config flow: bus discovery, user and reconfigure steps, validation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biomatx import config_flow
from custom_components.biomatx.const import (
    CONF_ALL_OFF_ADDRESS,
    CONF_ALL_OFF_SCENARIO,
    CONF_MODULE_COUNT,
    CONF_PROTOCOL,
    DOMAIN,
)
from custom_components.biomatx.protocol.master import xor_checksum

from . import frames_master as fm
from .conftest import MODULE_COUNT, URL
from .fake_serial import settle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant
    import voluptuous as vol

    from .conftest import SetupIntegration
    from .fake_serial import FakeSerialLink

OTHER_URL = "socket://192.168.1.50:8899"
REAL_PROBE_SECONDS = config_flow.PROBE_SECONDS
"""The listening window as shipped, read before any fixture shortens it."""
FAST_PROBE_SECONDS = 0.05
SLOWEST_REPORT_PERIOD = 4.0
"""Report period of the slowest module of the Enersol hall capture (3.95 to 4.14 s)."""
HOUSE_REPORTS = (
    f"{fm.STATE_HOUSE_M1} {fm.STATE_HOUSE_M2} {fm.STATE_HOUSE_M3} {fm.STATE_HOUSE_M4}"
)
"""One state report from each of the owner's four modules (addresses 0 to 3)."""
LEGACY_PRESS = f"{fm.LEGACY_PRESS_M1_R1} {fm.LEGACY_RELEASE_M1_R1}"


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the listening window: the fake bus has said everything at once."""
    monkeypatch.setattr(config_flow, "PROBE_SECONDS", FAST_PROBE_SECONDS)


@pytest.fixture(autouse=True)
def _no_setup(request: pytest.FixtureRequest) -> Iterator[None]:
    """
    Keep the flow tests about the flow: do not set up created entries.

    A test that asks for ``setup_integration`` wants the real integration.
    """
    if "setup_integration" in request.fixturenames:
        yield
        return
    with patch("custom_components.biomatx.async_setup_entry", return_value=True):
        yield


def state_report(module: int) -> str:
    """Return a valid state report of ``module`` (0-based) with every relay off."""
    body = bytes((0xA5, 0, 0x7F, 0x40 | module, 0x81, 0x01, 0x00, 0x00, 0x00))
    return (body[:1] + bytes((xor_checksum(body),)) + body[2:]).hex(" ")


def suggested(schema: vol.Schema, key: str) -> Any:
    """Return the value the form proposes for ``key``, ``None`` for no proposal."""
    for marker in schema.schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    msg = f"{key} not in schema"
    raise KeyError(msg)


async def configure(
    hass: HomeAssistant, result: dict[str, Any], user_input: dict[str, Any]
) -> dict[str, Any]:
    """Submit ``user_input`` to the step ``result`` shows."""
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    """Open the user step and return its form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def hear_bus(
    hass: HomeAssistant, fake_serial: FakeSerialLink, frames: str = ""
) -> dict[str, Any]:
    """Enter ``URL`` while ``frames`` are on the bus and return the modules form."""
    fake_serial.preload(frames)
    result = await start_user_flow(hass)
    result = await configure(hass, result, {CONF_DEVICE: URL})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "modules"
    assert result["last_step"] is True
    return result


def test_listening_window_spans_two_periods_of_the_slowest_module() -> None:
    """One lost report must not hide a module: the window holds two of its reports."""
    assert REAL_PROBE_SECONDS >= 2 * SLOWEST_REPORT_PERIOD


async def test_user_form_asks_for_the_device_and_announces_the_listen(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first step takes the device only and says how long the bus is heard."""
    monkeypatch.setattr(config_flow, "PROBE_SECONDS", REAL_PROBE_SECONDS)
    result = await start_user_flow(hass)
    assert [str(key) for key in result["data_schema"].schema] == [CONF_DEVICE]
    assert result["description_placeholders"] == {"seconds": f"{REAL_PROBE_SECONDS:g}"}
    assert result["last_step"] is False


async def test_user_flow_hears_a_master_bus_and_stores_its_protocol(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """Four reporting modules: four are proposed and the entry keeps ``master``."""
    result = await hear_bus(hass, fake_serial, HOUSE_REPORTS)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 4
    assert suggested(result["data_schema"], CONF_ALL_OFF_SCENARIO) is None
    assert result["description_placeholders"] == {
        "protocol": "master",
        "modules": "1, 2, 3, 4",
    }
    result = await configure(
        hass, result, {CONF_MODULE_COUNT: 4, CONF_ALL_OFF_SCENARIO: 6}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "BioMatX"
    assert entry.unique_id == URL
    assert entry.version == 3
    assert entry.data == {
        CONF_DEVICE: URL,
        CONF_MODULE_COUNT: 4,
        CONF_ALL_OFF_ADDRESS: 5,
        CONF_PROTOCOL: "master",
    }
    assert len(fake_serial.opens) == 1
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is True


@pytest.mark.parametrize(
    ("frames", "heard", "proposed"),
    [
        pytest.param(fm.STATE_HOUSE_M1, "1", 1, id="one module"),
        pytest.param(
            f"{fm.STATE_HOUSE_M1} {fm.STATE_HOUSE_M3}",
            "1, 3",
            3,
            id="addresses 0 and 2 give 3, not the 2 modules counted",
        ),
        pytest.param(HOUSE_REPORTS, "1, 2, 3, 4", 4, id="the owner's four modules"),
        pytest.param(state_report(6), "7", 7, id="the last address is not dropped"),
    ],
)
async def test_user_flow_proposes_the_highest_address_heard_plus_one(
    hass: HomeAssistant,
    fake_serial: FakeSerialLink,
    frames: str,
    heard: str,
    proposed: int,
) -> None:
    """A missed report must not under-count: the top address decides."""
    result = await hear_bus(hass, fake_serial, frames)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == proposed
    assert result["description_placeholders"]["modules"] == heard


async def test_user_flow_keeps_listening_for_the_whole_window(
    hass: HomeAssistant, fake_serial: FakeSerialLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Modules do not report together: a late report still counts."""
    monkeypatch.setattr(config_flow, "PROBE_SECONDS", 0.5)
    fake_serial.preload(fm.STATE_HOUSE_M1)

    async def late_report() -> None:
        await asyncio.sleep(0.2)  # the port is open, the window is not over
        fake_serial.feed(fm.STATE_HOUSE_M4)

    result = await start_user_flow(hass)
    late = asyncio.create_task(late_report())
    result = await configure(hass, result, {CONF_DEVICE: URL})
    await late
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 4
    assert result["description_placeholders"]["modules"] == "1, 4"


async def test_user_flow_hears_a_slow_module_whose_report_is_lost(
    hass: HomeAssistant, fake_serial: FakeSerialLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A module reporting every 4 s is heard although one of its reports is lost.

    The slowest module of the Enersol hall capture reports every 3.95 to 4.14 s, so
    a window of one period holds one report and a collision can hide it. The window
    as shipped is replayed at a twentieth of its length, against a period scaled
    the same way: the first report (at 0.1 s) is damaged and the next one, one
    period later, is valid. Two periods hear it; one period would not.
    """
    scale = 20
    monkeypatch.setattr(config_flow, "PROBE_SECONDS", REAL_PROBE_SECONDS / scale)

    async def reports() -> None:
        await asyncio.sleep(0.1)
        fake_serial.feed(fm.STATE_M1_BAD_CHECKSUM)  # lost to a collision
        await asyncio.sleep(SLOWEST_REPORT_PERIOD / scale)
        fake_serial.feed(fm.STATE_HOUSE_M1)

    result = await start_user_flow(hass)
    later = asyncio.create_task(reports())
    result = await configure(hass, result, {CONF_DEVICE: URL})
    await later
    assert result["description_placeholders"]["modules"] == "1"
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 1


async def test_user_flow_silent_bus_asks_for_the_count_by_hand(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """Nothing heard: no proposal, no stored protocol, the next start detects it."""
    result = await hear_bus(hass, fake_serial)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) is None
    assert result["description_placeholders"] == {"protocol": "-", "modules": "-"}
    result = await configure(hass, result, {CONF_MODULE_COUNT: 3})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data == {CONF_DEVICE: URL, CONF_MODULE_COUNT: 3}


async def test_user_flow_legacy_frames_store_no_protocol(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """
    Two-byte frames prove nothing the flow keeps: only master is stored.

    Legacy modules speak on a press only and never report, and a legacy verdict
    can come from one stray frame. The entry has no protocol, and the detection
    at the first load takes over.
    """
    result = await hear_bus(hass, fake_serial, LEGACY_PRESS)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) is None
    assert result["description_placeholders"] == {"protocol": "-", "modules": "-"}
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["result"].data == {CONF_DEVICE: URL, CONF_MODULE_COUNT: 4}


async def test_user_flow_phantom_frame_alone_proves_no_protocol(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """The detectors' phantom frame reads as a legacy press: it proves nothing."""
    result = await hear_bus(hass, fake_serial, fm.PHANTOM_PRESS_M4_OUT11)
    assert result["description_placeholders"] == {"protocol": "-", "modules": "-"}
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert CONF_PROTOCOL not in result["result"].data


async def test_user_flow_phantom_frame_first_does_not_hide_a_master_bus(
    hass: HomeAssistant, fake_serial: FakeSerialLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The phantom frame alone in the first read, the reports in a later one.

    Decided on that first read, the frame would read as a legacy press and the
    modules would go unheard. The entry must get ``master`` and the four modules.
    """
    monkeypatch.setattr(config_flow, "PROBE_SECONDS", 0.3)
    fake_serial.preload(fm.PHANTOM_PRESS_M4_OUT11)

    async def reports() -> None:
        await asyncio.sleep(0.1)
        fake_serial.feed(HOUSE_REPORTS)

    result = await start_user_flow(hass)
    later = asyncio.create_task(reports())
    result = await configure(hass, result, {CONF_DEVICE: URL})
    await later
    assert result["description_placeholders"] == {
        "protocol": "master",
        "modules": "1, 2, 3, 4",
    }
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["result"].data[CONF_PROTOCOL] == "master"


async def test_user_flow_scenario_is_optional(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """Without an all-off scenario the key is simply absent from the entry."""
    result = await hear_bus(hass, fake_serial, HOUSE_REPORTS)
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_ALL_OFF_ADDRESS not in result["result"].data


async def test_the_probe_writes_nothing_on_the_bus(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """A busy bus is only listened to: not one byte goes out, the port is closed."""
    chatter = (
        f"{HOUSE_REPORTS} {fm.PRESS_M1_R1} {fm.RELEASE_M1_R1} "
        f"{fm.WALL_PRESS_M2_TO_M1_R5} {fm.PHANTOM_PRESS_M4_OUT11}"
    )
    await hear_bus(hass, fake_serial, chatter)
    assert fake_serial.writes == 0
    assert bytes(fake_serial.written) == b""
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is True


async def test_abandoning_the_flow_during_the_listen_closes_the_port_and_its_reader(
    hass: HomeAssistant, fake_serial: FakeSerialLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flow cancelled while it listens leaves no open port and no reader task."""
    monkeypatch.setattr(config_flow, "PROBE_SECONDS", 30)
    result = await start_user_flow(hass)
    listening = asyncio.create_task(configure(hass, result, {CONF_DEVICE: URL}))
    await asyncio.sleep(0.1)
    assert fake_serial.writer is not None
    assert fake_serial.writer.closed is False
    listening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await listening
    assert fake_serial.writer.closed is True
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith(f"{DOMAIN} probe")
    ]


async def test_user_flow_cannot_connect_shows_error_then_recovers(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """A port that cannot be opened keeps the device form up with an error."""
    fake_serial.fail_open = OSError("no such device")
    result = await start_user_flow(hass)
    result = await configure(hass, result, {CONF_DEVICE: URL})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    result = await configure(hass, result, {CONF_DEVICE: URL})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "modules"
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(fake_serial.opens) == 2


async def test_user_flow_aborts_when_device_already_configured(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """One serial device is one bus: a second entry for it is refused, unopened."""
    mock_config_entry.add_to_hass(hass)
    result = await start_user_flow(hass)
    result = await configure(hass, result, {CONF_DEVICE: URL})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert fake_serial.opens == []


async def test_user_flow_aborts_when_the_device_is_already_being_configured(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """A second flow for a device another flow holds does not listen to the bus."""
    await hear_bus(hass, fake_serial, HOUSE_REPORTS)
    opened = len(fake_serial.opens)
    second = await start_user_flow(hass)
    second = await configure(hass, second, {CONF_DEVICE: URL})
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    assert len(fake_serial.opens) == opened


@pytest.mark.parametrize(
    ("field", "value"),
    [(CONF_MODULE_COUNT, 0), (CONF_MODULE_COUNT, 8), (CONF_ALL_OFF_SCENARIO, 11)],
)
async def test_modules_step_rejects_values_out_of_range(
    hass: HomeAssistant, fake_serial: FakeSerialLink, field: str, value: int
) -> None:
    """Module counts are 1-7 and scenario numbers 1-10, like the front panels."""
    result = await hear_bus(hass, fake_serial, HOUSE_REPORTS)
    with pytest.raises(InvalidData):
        await configure(hass, result, {CONF_MODULE_COUNT: 4, field: value})


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


async def reconfigure_to_modules(
    hass: HomeAssistant, entry: MockConfigEntry, device: str = URL
) -> dict[str, Any]:
    """Submit ``device`` in the reconfigure step and return the modules form."""
    result = await start_reconfigure(hass, entry)
    result = await configure(hass, result, {CONF_DEVICE: device})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "modules"
    assert result["last_step"] is True
    return result


async def test_reconfigure_form_asks_for_the_device_only(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """The first page holds the device, prefilled; the modules come on the next."""
    mock_config_entry.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    schema = result["data_schema"]
    assert [str(key) for key in schema.schema] == [CONF_DEVICE]
    assert suggested(schema, CONF_DEVICE) == URL
    assert result["last_step"] is False
    assert fake_serial.opens == []


async def test_reconfigure_modules_form_is_prefilled_one_based(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """The stored 0-based scenario address 5 is shown as scenario 6."""
    mock_config_entry.add_to_hass(hass)
    result = await reconfigure_to_modules(hass, mock_config_entry)
    schema = result["data_schema"]
    assert suggested(schema, CONF_MODULE_COUNT) == MODULE_COUNT
    assert suggested(schema, CONF_ALL_OFF_SCENARIO) == 6


async def test_reconfigure_unloaded_entry_listens_and_proposes_the_modules_heard(
    hass: HomeAssistant, fake_serial: FakeSerialLink
) -> None:
    """Nothing runs on the bus, so the flow may listen: a fifth module gets noticed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=URL,
        version=3,
        data={CONF_DEVICE: URL, CONF_MODULE_COUNT: 2, CONF_PROTOCOL: "master"},
    )
    entry.add_to_hass(hass)
    fake_serial.preload(f"{HOUSE_REPORTS} {state_report(4)}")  # addresses 0 to 4
    result = await reconfigure_to_modules(hass, entry)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 5
    assert result["description_placeholders"] == {
        "protocol": "master",
        "modules": "1, 2, 3, 4, 5",
    }
    assert len(fake_serial.opens) == 1
    assert fake_serial.writes == 0


async def test_reconfigure_never_proposes_fewer_modules_than_configured(
    hass: HomeAssistant,
    fake_serial: FakeSerialLink,
    master_config_entry: MockConfigEntry,
) -> None:
    """Two of four modules reported during the listen: the four stay proposed."""
    master_config_entry.add_to_hass(hass)
    fake_serial.preload(f"{fm.STATE_HOUSE_M1} {fm.STATE_HOUSE_M2}")
    result = await reconfigure_to_modules(hass, master_config_entry)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == MODULE_COUNT
    assert result["description_placeholders"]["modules"] == "1, 2"


async def test_reconfigure_silent_bus_keeps_a_stored_master_protocol(
    hass: HomeAssistant,
    fake_serial: FakeSerialLink,
    master_config_entry: MockConfigEntry,
) -> None:
    """A master verdict was proved once by a frame: a silent listen does not undo it."""
    master_config_entry.add_to_hass(hass)
    result = await reconfigure_to_modules(hass, master_config_entry)
    assert result["description_placeholders"] == {"protocol": "master", "modules": "-"}
    result = await configure(hass, result, {CONF_MODULE_COUNT: MODULE_COUNT})
    assert result["type"] is FlowResultType.ABORT
    assert master_config_entry.data == {
        CONF_DEVICE: URL,
        CONF_MODULE_COUNT: MODULE_COUNT,
        CONF_PROTOCOL: "master",
    }


async def test_reconfigure_silent_bus_drops_a_stored_legacy_protocol(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """The flow never stores legacy: the next load detects the protocol again."""
    mock_config_entry.add_to_hass(hass)
    result = await reconfigure_to_modules(hass, mock_config_entry)
    assert result["description_placeholders"] == {"protocol": "legacy", "modules": "-"}
    result = await configure(
        hass, result, {CONF_MODULE_COUNT: 4, CONF_ALL_OFF_SCENARIO: 6}
    )
    assert result["type"] is FlowResultType.ABORT
    assert mock_config_entry.data == {
        CONF_DEVICE: URL,
        CONF_MODULE_COUNT: 4,
        CONF_ALL_OFF_ADDRESS: 5,
    }


async def test_reconfigure_hearing_another_protocol_replaces_the_stored_one(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Modules reprogrammed from legacy to master: the listen corrects the entry."""
    mock_config_entry.add_to_hass(hass)
    fake_serial.preload(HOUSE_REPORTS)
    result = await reconfigure_to_modules(hass, mock_config_entry)
    assert result["description_placeholders"]["protocol"] == "master"
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["type"] is FlowResultType.ABORT
    assert mock_config_entry.data[CONF_PROTOCOL] == "master"


async def test_reconfigure_updates_data_and_reloads(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Changing the module count and the scenario updates the entry in place."""
    mock_config_entry.add_to_hass(hass)
    result = await reconfigure_to_modules(hass, mock_config_entry)
    result = await configure(
        hass, result, {CONF_MODULE_COUNT: 3, CONF_ALL_OFF_SCENARIO: 2}
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
    result = await reconfigure_to_modules(hass, mock_config_entry)
    result = await configure(hass, result, {CONF_MODULE_COUNT: MODULE_COUNT})
    assert result["type"] is FlowResultType.ABORT
    assert CONF_ALL_OFF_ADDRESS not in mock_config_entry.data


async def test_reconfigure_new_device_listens_and_the_protocol_follows(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Moving the bus to another adapter keeps the entry, and reads the new bus."""
    mock_config_entry.add_to_hass(hass)
    fake_serial.preload(HOUSE_REPORTS)
    result = await reconfigure_to_modules(hass, mock_config_entry, OTHER_URL)
    assert fake_serial.opens[0]["url"] == OTHER_URL
    assert result["description_placeholders"]["protocol"] == "master"
    result = await configure(hass, result, {CONF_MODULE_COUNT: MODULE_COUNT})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == OTHER_URL
    assert mock_config_entry.data[CONF_DEVICE] == OTHER_URL
    assert mock_config_entry.data[CONF_PROTOCOL] == "master"


async def test_reconfigure_new_device_proposes_what_it_hears_not_the_configured_count(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """The count of the entry says nothing about another bus: two modules, two."""
    mock_config_entry.add_to_hass(hass)
    fake_serial.preload(f"{fm.STATE_HOUSE_M1} {fm.STATE_HOUSE_M2}")
    result = await reconfigure_to_modules(hass, mock_config_entry, OTHER_URL)
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 2
    assert suggested(result["data_schema"], CONF_ALL_OFF_SCENARIO) == 6


async def test_reconfigure_new_silent_device_proposes_nothing_and_drops_the_protocol(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """The old protocol and count say nothing about another bus: by hand, detected."""
    mock_config_entry.add_to_hass(hass)
    result = await reconfigure_to_modules(hass, mock_config_entry, OTHER_URL)
    assert result["description_placeholders"] == {"protocol": "-", "modules": "-"}
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) is None
    result = await configure(hass, result, {CONF_MODULE_COUNT: MODULE_COUNT})
    assert result["type"] is FlowResultType.ABORT
    assert CONF_PROTOCOL not in mock_config_entry.data


async def test_reconfigure_rejects_device_owned_by_other_entry(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """Two entries cannot share one serial device."""
    mock_config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_URL,
        version=3,
        data={CONF_DEVICE: OTHER_URL, CONF_MODULE_COUNT: 2},
    )
    other.add_to_hass(hass)
    result = await start_reconfigure(hass, mock_config_entry)
    result = await configure(hass, result, {CONF_DEVICE: OTHER_URL})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_DEVICE] == URL
    assert fake_serial.opens == []


async def test_reconfigure_cannot_connect_shows_error(
    hass: HomeAssistant, fake_serial: FakeSerialLink, mock_config_entry: MockConfigEntry
) -> None:
    """A new device that cannot be opened is not stored."""
    mock_config_entry.add_to_hass(hass)
    fake_serial.fail_open = OSError("no such device")
    result = await start_reconfigure(hass, mock_config_entry)
    result = await configure(hass, result, {CONF_DEVICE: OTHER_URL})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_config_entry.data[CONF_DEVICE] == URL


async def test_reconfigure_loaded_entry_reads_the_running_hub_and_opens_no_port(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """The hub in service holds the bus: a second reader would share its bytes."""
    entry = await setup_integration(master_config_entry)
    fake_serial.feed(HOUSE_REPORTS)
    await settle()
    await hass.async_block_till_done()
    assert len(fake_serial.opens) == 1
    result = await reconfigure_to_modules(hass, entry)
    assert len(fake_serial.opens) == 1
    assert fake_serial.writes == 0
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 4
    assert result["description_placeholders"] == {
        "protocol": "master",
        "modules": "1, 2, 3, 4",
    }
    result = await configure(hass, result, {CONF_MODULE_COUNT: 3})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {
        CONF_DEVICE: URL,
        CONF_MODULE_COUNT: 3,
        CONF_PROTOCOL: "master",
    }
    assert len(fake_serial.opens) == 2  # the change reloads the entry, not the flow


async def test_reconfigure_without_a_change_does_not_reload_the_entry(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A review that changes nothing must not cut the 40 lights for a reload."""
    entry = await setup_integration(master_config_entry)
    fake_serial.feed(HOUSE_REPORTS)
    await settle()
    await hass.async_block_till_done()
    result = await reconfigure_to_modules(hass, entry)
    result = await configure(hass, result, {CONF_MODULE_COUNT: MODULE_COUNT})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(fake_serial.opens) == 1
    assert entry.runtime_data.hub.connected is True


async def test_reconfigure_loaded_entry_on_a_silent_bus_keeps_the_configured_count(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """No module has reported yet: the entry's protocol and count are proposed."""
    entry = await setup_integration(master_config_entry)
    result = await reconfigure_to_modules(hass, entry)
    assert len(fake_serial.opens) == 1
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == MODULE_COUNT
    assert result["description_placeholders"] == {"protocol": "master", "modules": "-"}


async def test_reconfigure_loaded_entry_proposes_a_module_its_hub_drops_as_unconfigured(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A fifth module added to the bus is dropped by the hub and found by the flow."""
    entry = await setup_integration(master_config_entry)  # four modules configured
    fake_serial.feed(f"{HOUSE_REPORTS} {state_report(4)}")
    await settle()
    await hass.async_block_till_done()
    result = await reconfigure_to_modules(hass, entry)
    assert len(fake_serial.opens) == 1
    assert suggested(result["data_schema"], CONF_MODULE_COUNT) == 5
    assert result["description_placeholders"]["modules"] == "1, 2, 3, 4, 5"


async def test_reconfigure_loaded_entry_with_a_legacy_hub_drops_the_protocol_key(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """The flow stores master only: the entry reloads and detects its bus again."""
    entry = await setup_integration(mock_config_entry)  # stored protocol: legacy
    result = await reconfigure_to_modules(hass, entry)
    assert result["description_placeholders"]["protocol"] == "legacy"
    result = await configure(
        hass, result, {CONF_MODULE_COUNT: 4, CONF_ALL_OFF_SCENARIO: 6}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert CONF_PROTOCOL not in entry.data
    assert entry.data[CONF_ALL_OFF_ADDRESS] == 5
    assert len(fake_serial.opens) == 2  # the dropped key is a change: one reload


async def test_reconfigure_loaded_entry_takes_the_protocol_the_hub_detected(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    undetected_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """An entry without a stored protocol still names the one its hub works with."""
    entry = await setup_integration(undetected_config_entry)
    fake_serial.feed(LEGACY_PRESS)
    await settle()
    await hass.async_block_till_done()
    assert entry.data[CONF_PROTOCOL] == "legacy"  # stored at run time, as in beta.2
    result = await reconfigure_to_modules(hass, entry)
    assert len(fake_serial.opens) == 1
    assert result["description_placeholders"]["protocol"] == "legacy"
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert CONF_PROTOCOL not in entry.data


async def test_reconfigure_loaded_entry_moved_to_a_new_device_listens_to_it(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Only the device in service is protected: another adapter is free to open."""
    entry = await setup_integration(master_config_entry)
    fake_serial.preload(HOUSE_REPORTS)
    result = await reconfigure_to_modules(hass, entry, OTHER_URL)
    assert [opened["url"] for opened in fake_serial.opens] == [URL, OTHER_URL]
    assert result["description_placeholders"]["modules"] == "1, 2, 3, 4"
    result = await configure(hass, result, {CONF_MODULE_COUNT: 4})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert entry.unique_id == OTHER_URL
