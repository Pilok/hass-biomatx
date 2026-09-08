"""Tests for the light platform: one assumed-state light per relay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.light import ATTR_SUPPORTED_COLOR_MODES, ColorMode
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import async_get_platforms
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from . import frames
from .conftest import MODULE_COUNT
from .fake_serial import settle

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .conftest import SetupIntegration
    from .fake_serial import FakeSerialLink


M1_R1 = "light.biomatx_module_1_relay_1"
M1_R8 = "light.biomatx_module_1_relay_8"
M2_R8 = "light.biomatx_module_2_relay_8"


async def turn(hass: HomeAssistant, service: str, entity_id: str) -> None:
    """Call light.turn_on or light.turn_off and wait for the bus to settle."""
    await hass.services.async_call(
        "light", service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def test_one_light_per_relay_with_namespaced_unique_ids(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Four modules give forty lights, identified by entry, module and relay."""
    entry = await setup_integration(mock_config_entry)
    registry = er.async_get(hass)
    lights = [
        entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.domain == "light"
    ]
    assert len(lights) == MODULE_COUNT * 10
    unique_ids = {entity.unique_id for entity in lights}
    assert f"{entry.entry_id}-relay-0-0" in unique_ids
    assert f"{entry.entry_id}-relay-3-9" in unique_ids
    assert hass.states.get(M1_R1) is not None
    assert hass.states.get("light.biomatx_module_4_relay_10") is not None


async def test_light_names_are_one_based(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Frame index (1, 7) is shown as module 2, relay 8, like the front panel."""
    await setup_integration(mock_config_entry)
    state = hass.states.get(M2_R8)
    assert state is not None
    assert state.attributes[ATTR_FRIENDLY_NAME] == "BioMatX module 2 Relay 8"


async def test_light_is_assumed_state_and_off_by_default(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The bus never reports state, so lights declare their state as assumed."""
    await setup_integration(mock_config_entry)
    state = hass.states.get(M1_R1)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_ASSUMED_STATE] is True
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.ONOFF]


async def test_turn_on_sends_press_release_and_reports_on(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Turning on simulates one wall press for that relay."""
    await setup_integration(mock_config_entry)
    await turn(hass, "turn_on", M1_R1)
    assert fake_serial.frames_written() == [frames.PRESS_M1_R1, frames.RELEASE_M1_R1]
    assert hass.states.get(M1_R1).state == STATE_ON


async def test_turn_on_when_already_on_sends_nothing(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A second turn_on must not toggle the relay back off."""
    await setup_integration(mock_config_entry)
    await turn(hass, "turn_on", M1_R1)
    fake_serial.clear()
    await turn(hass, "turn_on", M1_R1)
    assert fake_serial.frames_written() == []
    assert hass.states.get(M1_R1).state == STATE_ON


async def test_turn_off_sends_frames_and_reports_off(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Turning off a light believed on presses its button once."""
    await setup_integration(mock_config_entry)
    await turn(hass, "turn_on", M1_R1)
    fake_serial.clear()
    await turn(hass, "turn_off", M1_R1)
    assert fake_serial.frames_written() == [frames.PRESS_M1_R1, frames.RELEASE_M1_R1]
    assert hass.states.get(M1_R1).state == STATE_OFF
    fake_serial.clear()
    await turn(hass, "turn_off", M1_R1)
    assert fake_serial.frames_written() == []


async def test_wall_press_frame_toggles_light(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """A press seen on the bus flips the light without any command from HA."""
    await setup_integration(mock_config_entry)
    fake_serial.feed(frames.PRESS_M1_R1 + frames.RELEASE_M1_R1)
    await hass.async_block_till_done()
    assert hass.states.get(M1_R1).state == STATE_ON
    fake_serial.feed(frames.PRESS_M1_R1 + frames.RELEASE_M1_R1)
    await hass.async_block_till_done()
    assert hass.states.get(M1_R1).state == STATE_OFF


async def test_detector_cross_module_frame_toggles_target_light(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """The corridor detector frame lights module 1 relay 8, not module 2."""
    await setup_integration(mock_config_entry)
    fake_serial.feed(frames.DETECTOR_CORRIDOR_PRESS + frames.DETECTOR_CORRIDOR_RELEASE)
    await hass.async_block_till_done()
    assert hass.states.get(M1_R8).state == STATE_ON
    assert hass.states.get(M2_R8).state == STATE_OFF


async def test_collision_garbage_does_not_change_any_light(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """Bytes produced by a bus collision leave every light untouched."""
    entry = await setup_integration(mock_config_entry)
    fake_serial.feed(frames.COLLISION_INVALID_SWITCH + frames.COLLISION_WRONG_RELEASE)
    await hass.async_block_till_done()
    lights = [
        state
        for state in hass.states.async_all("light")
        if state.entity_id.startswith("light.biomatx_")
    ]
    assert len(lights) == MODULE_COUNT * 10
    assert all(state.state == STATE_OFF for state in lights)
    assert entry.runtime_data.hub.frames_dropped >= 1


async def test_light_restores_last_state_after_restart(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Assumed states survive a Home Assistant restart."""
    mock_restore_cache(hass, [State(M1_R1, STATE_ON), State(M2_R8, STATE_ON)])
    await setup_integration(mock_config_entry)
    assert hass.states.get(M1_R1).state == STATE_ON
    assert hass.states.get(M2_R8).state == STATE_ON
    assert hass.states.get(M1_R8).state == STATE_OFF


async def test_lights_unavailable_on_link_loss_and_back_after_reconnect(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """An unplugged adapter shows as unavailable, then recovers by itself."""
    await setup_integration(mock_config_entry)
    await turn(hass, "turn_on", M1_R1)
    fake_serial.drop_link()
    await hass.async_block_till_done()
    assert hass.states.get(M1_R1).state == STATE_UNAVAILABLE
    await settle(50)
    await hass.async_block_till_done()
    assert len(fake_serial.opens) == 2
    assert hass.states.get(M1_R1).state == STATE_ON


async def test_turn_on_while_link_down_raises_translated_error(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    mock_config_entry: MockConfigEntry,
    fake_serial: FakeSerialLink,
) -> None:
    """
    A command that cannot reach the bus fails with a user-facing error.

    Home Assistant does not call services on unavailable entities, so the
    entity is driven directly: this is the race where the link drops between
    the availability check and the command.
    """
    await setup_integration(mock_config_entry)
    fake_serial.fail_open_always = OSError("no such device")
    fake_serial.drop_link()
    await settle(30)
    await hass.async_block_till_done()
    assert hass.states.get(M1_R1).state == STATE_UNAVAILABLE
    platform = next(
        p for p in async_get_platforms(hass, "biomatx") if p.domain == "light"
    )
    light = platform.entities[M1_R1]
    with pytest.raises(HomeAssistantError) as excinfo:
        await light.async_turn_on()
    assert excinfo.value.translation_key == "link_down"
    assert fake_serial.frames_written() == []
