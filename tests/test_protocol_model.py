"""Tests for the data model of an installation: modules, relays, buttons."""

from __future__ import annotations

import pytest

from custom_components.biomatx.protocol.model import (
    BUTTONS_PER_MODULE,
    SCENARIO_MODULE_ADDRESS,
    Installation,
)


def test_installation_models_configured_modules_and_scenarios() -> None:
    """Four modules give 40 relays, 40 buttons and 10 scenario buttons."""
    installation = Installation(4)
    assert [module.address for module in installation.modules] == [0, 1, 2, 3]
    assert installation.scenario_module.address == SCENARIO_MODULE_ADDRESS
    assert len(installation.relays) == 4 * BUTTONS_PER_MODULE
    assert len(installation.switches) == 5 * BUTTONS_PER_MODULE


def test_scenario_module_has_buttons_but_no_relays() -> None:
    """Module 7 is virtual: scenarios are buttons without a relay behind them."""
    scenarios = Installation(1).scenario_module
    assert scenarios.is_scenario is True
    assert len(scenarios.switches) == BUTTONS_PER_MODULE
    assert scenarios.relays == []


def test_relay_and_switch_lookup_by_address() -> None:
    """Relays and buttons are addressed 0-based like the frames."""
    installation = Installation(2)
    relay = installation.relay(1, 7)
    assert relay.module.address == 1
    assert relay.address == 7
    assert relay.on is False
    switch = installation.switch(SCENARIO_MODULE_ADDRESS, 5)
    assert switch.module is installation.scenario_module
    assert switch.address == 5
    assert switch.pressed is False
    assert switch.released is True


def test_switch_remembers_its_last_emitter_and_counts_events() -> None:
    """Event entities need the emitter and a way to tell a new event from a refresh."""
    switch = Installation(1).switch(0, 0)
    assert switch.emitter is None
    assert switch.events == 0
    switch.emitter = 1
    switch.events += 1
    assert repr(switch) == "<Switch module=0 address=0 pressed=False>"


def test_module_lookup_covers_configured_and_scenario_modules_only() -> None:
    """Module 4 of a 2-module bus does not exist; module 7 always does."""
    installation = Installation(2)
    assert installation.module(1).address == 1
    assert installation.module(SCENARIO_MODULE_ADDRESS).is_scenario is True
    with pytest.raises(KeyError):
        installation.module(4)


def test_relay_and_switch_share_their_address_and_module() -> None:
    """Relay n of a module is driven by button n of the same module."""
    module = Installation(1).module(0)
    assert module.is_scenario is False
    for address, (relay, switch) in enumerate(
        zip(module.relays, module.switches, strict=True)
    ):
        assert relay.address == switch.address == address
        assert relay.module is switch.module is module


def test_states_are_mutable_and_independent() -> None:
    """Flipping one relay or button leaves every other one untouched."""
    installation = Installation(2)
    installation.relay(0, 0).on = True
    installation.switch(0, 0).pressed = True
    assert installation.relay(0, 1).on is False
    assert installation.relay(1, 0).on is False
    assert installation.switch(0, 1).pressed is False
    assert installation.switch(0, 0).released is False


def test_repr_names_module_and_address() -> None:
    """Log lines show where a relay or button lives."""
    installation = Installation(1)
    assert repr(installation.relay(0, 3)) == "<Relay module=0 address=3 on=False>"
    assert (
        repr(installation.switch(0, 3)) == "<Switch module=0 address=3 pressed=False>"
    )
    assert repr(installation.module(0)) == "<Module address=0>"
