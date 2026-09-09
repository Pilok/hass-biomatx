"""Tests for the device model shared by the platforms."""

from custom_components.biomatx.entity import module_device_info


def test_module_device_is_named_one_based_and_linked_to_the_hub() -> None:
    """Module index 1 is shown as module 2 and hangs under the hub device."""
    info = module_device_info("entry", 1, "hub-device-id")
    assert info["identifiers"] == {("biomatx", "entry-module-1")}
    assert info["name"] == "BioMatX module 2"
    assert info["model"] == "BioMatX 2110"
    assert info["manufacturer"] == "PSO"
    assert info["via_device_id"] == "hub-device-id"


def test_scenario_module_has_its_own_device() -> None:
    """Module 7 carries the scenarios, not relays: it gets a distinct device."""
    info = module_device_info("entry", 7, "hub-device-id")
    assert info["identifiers"] == {("biomatx", "entry-module-7")}
    assert info["name"] == "BioMatX scenarios"
    assert info["model"] == "Scenario module"
