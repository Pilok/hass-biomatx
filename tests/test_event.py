"""
Tests for the event platform: one event entity per button and per scenario.

Wall buttons, detectors and front-panel buttons emit press and release events;
the entity records the module the button is wired on (``emitter_module``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import ATTR_EVENT_TYPE, ATTR_EVENT_TYPES
from homeassistant.const import ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er

from . import frames, frames_master as fm
from .conftest import MODULE_COUNT

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import SetupIntegration
    from .fake_serial import FakeSerialLink

M1_B1 = "event.biomatx_module_1_button_1"
M1_B5 = "event.biomatx_module_1_button_5"
M2_B5 = "event.biomatx_module_2_button_5"
SCENARIO_1 = "event.biomatx_scenarios_scenario_1"


async def test_one_event_per_button_and_per_scenario(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
) -> None:
    """Four modules give 40 button events plus 10 scenario events."""
    entry = await setup_integration(master_config_entry)
    registry = er.async_get(hass)
    events = [
        entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.domain == "event"
    ]
    assert len(events) == MODULE_COUNT * 10 + 10
    unique_ids = {entity.unique_id for entity in events}
    assert f"{entry.entry_id}-switch-0-0" in unique_ids
    assert f"{entry.entry_id}-switch-7-9" in unique_ids
    state = hass.states.get(M1_B5)
    assert state is not None
    assert state.attributes[ATTR_FRIENDLY_NAME] == "BioMatX module 1 Button 5"
    assert state.attributes[ATTR_EVENT_TYPES] == ["pressed", "released"]
    scenario = hass.states.get(SCENARIO_1)
    assert scenario is not None
    assert scenario.attributes[ATTR_FRIENDLY_NAME] == "BioMatX scenarios Scenario 1"


async def test_wall_press_fires_pressed_then_released_with_the_emitter(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A switch wired on module 2 driving module 1 relay 5: the event is on module 1."""
    await setup_integration(master_config_entry)
    fake_serial.feed(fm.STATE_M1_ALL_OFF)
    await hass.async_block_till_done()
    assert hass.states.get(M1_B5).state == STATE_UNKNOWN
    fake_serial.feed(fm.WALL_PRESS_M2_TO_M1_R5)
    await hass.async_block_till_done()
    state = hass.states.get(M1_B5)
    assert state.state != STATE_UNKNOWN
    assert state.attributes[ATTR_EVENT_TYPE] == "pressed"
    assert state.attributes["emitter_module"] == 2
    assert hass.states.get(M2_B5).attributes.get(ATTR_EVENT_TYPE) is None
    fake_serial.feed(fm.WALL_RELEASE_M2_TO_M1_R5)
    await hass.async_block_till_done()
    assert hass.states.get(M1_B5).attributes[ATTR_EVENT_TYPE] == "released"


async def test_scenario_button_fires_on_the_scenario_module(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """The hall's all-off button: scenario 1, emitted by module 2."""
    await setup_integration(master_config_entry)
    fake_serial.feed(fm.SCENARIO_1_PRESS)
    await hass.async_block_till_done()
    state = hass.states.get(SCENARIO_1)
    assert state.attributes[ATTR_EVENT_TYPE] == "pressed"
    assert state.attributes["emitter_module"] == 2


async def test_legacy_press_fires_events_too(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Legacy frames are events as well; the corridor detector names module 2."""
    await setup_integration(mock_config_entry)
    fake_serial.feed(frames.DETECTOR_CORRIDOR_PRESS)
    await hass.async_block_till_done()
    state = hass.states.get("event.biomatx_module_1_button_8")
    assert state.attributes[ATTR_EVENT_TYPE] == "pressed"
    assert state.attributes["emitter_module"] == 2


async def test_availability_change_does_not_fire_a_phantom_event(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A module report wakes the entity for availability, not as a new press."""
    await setup_integration(master_config_entry)
    fake_serial.feed(fm.STATE_M1_ALL_OFF + fm.PRESS_M1_R1)
    await hass.async_block_till_done()
    before = hass.states.get(M1_B1)
    assert before.attributes[ATTR_EVENT_TYPE] == "pressed"
    fake_serial.feed(fm.STATE_M1_R1_ON)
    await hass.async_block_till_done()
    after = hass.states.get(M1_B1)
    assert after.state == before.state
    assert after.attributes[ATTR_EVENT_TYPE] == "pressed"


async def test_events_follow_the_link_not_the_module(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    master_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """An event is a bus fact: it is delivered even if the target module is silent."""
    await setup_integration(master_config_entry)
    assert hass.states.get(M1_B1).state == STATE_UNKNOWN
    assert hass.states.get(SCENARIO_1).state == STATE_UNKNOWN
    fake_serial.feed(fm.WALL_PRESS_M2_TO_M1_R5)  # module 1 never reported
    await hass.async_block_till_done()
    assert hass.states.get(M1_B5).attributes[ATTR_EVENT_TYPE] == "pressed"
    fake_serial.fail_open_always = OSError("unplugged")
    fake_serial.drop_link()
    await hass.async_block_till_done()
    assert hass.states.get(M1_B1).state == STATE_UNAVAILABLE
    assert hass.states.get(SCENARIO_1).state == STATE_UNAVAILABLE
