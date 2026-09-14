"""
Data model of an installation: modules, their relays and their buttons.

Ported from ``pybiomatx`` (MIT, Damien Merenne) without its transport. Every
address is 0-based like the frames; user-facing strings add one.
"""

from __future__ import annotations

SCENARIO_MODULE_ADDRESS = 7
"""Virtual module whose ten buttons trigger the programmed scenarios."""
BUTTONS_PER_MODULE = 10
MAX_MODULE_ADDRESS = 7


class Switch:
    """A button of a module: a relay button or a scenario button."""

    __slots__ = ("address", "emitter", "events", "module", "pressed")

    def __init__(self, module: Module, address: int) -> None:
        """Create the released button ``address`` of ``module``."""
        self.module = module
        self.address = address
        self.pressed = False
        self.emitter = module.address
        """0-based module the last press or release was emitted by; its own at first."""
        self.events = 0
        """Number of press and release events seen, so a refresh is not an event."""

    @property
    def released(self) -> bool:
        """Return whether the button is not pressed."""
        return not self.pressed

    def __repr__(self) -> str:
        """Return a debugging view: ``<Switch module=0 address=3 pressed=False>``."""
        return (
            f"<Switch module={self.module.address} address={self.address} "
            f"pressed={self.pressed}>"
        )


class Relay:
    """A relay of a module, driven by the button with the same address."""

    __slots__ = ("address", "module", "on")

    def __init__(self, module: Module, address: int) -> None:
        """Create the relay ``address`` of ``module``, off."""
        self.module = module
        self.address = address
        self.on = False

    def __repr__(self) -> str:
        """Return a debugging view, e.g. ``<Relay module=0 address=3 on=False>``."""
        return (
            f"<Relay module={self.module.address} address={self.address} on={self.on}>"
        )


class Module:
    """A BioMatX module (ten relays, ten buttons) or the scenario module."""

    __slots__ = ("address", "relays", "switches")

    def __init__(self, address: int) -> None:
        """Create module ``address`` with its buttons, and relays unless virtual."""
        self.address = address
        self.switches = [Switch(self, i) for i in range(BUTTONS_PER_MODULE)]
        self.relays = (
            []
            if self.is_scenario
            else [Relay(self, i) for i in range(BUTTONS_PER_MODULE)]
        )

    @property
    def is_scenario(self) -> bool:
        """Return whether this is the virtual scenario module."""
        return self.address == SCENARIO_MODULE_ADDRESS

    def __repr__(self) -> str:
        """Return a debugging view, e.g. ``<Module address=0>``."""
        return f"<Module address={self.address}>"


class Installation:
    """The modules of one bus: ``module_count`` real ones plus the scenario module."""

    def __init__(self, module_count: int) -> None:
        """Model modules ``0`` to ``module_count - 1`` and the scenario module."""
        self._modules: dict[int, Module] = {
            address: Module(address) for address in range(module_count)
        }
        self._modules[SCENARIO_MODULE_ADDRESS] = Module(SCENARIO_MODULE_ADDRESS)

    @property
    def modules(self) -> list[Module]:
        """Return the real modules, scenario module excluded."""
        return [module for module in self._modules.values() if not module.is_scenario]

    @property
    def scenario_module(self) -> Module:
        """Return the virtual module that carries the scenarios."""
        return self._modules[SCENARIO_MODULE_ADDRESS]

    @property
    def relays(self) -> list[Relay]:
        """Return every relay of the real modules."""
        return [relay for module in self.modules for relay in module.relays]

    @property
    def switches(self) -> list[Switch]:
        """Return every button, scenario buttons included."""
        return [
            switch for module in self._modules.values() for switch in module.switches
        ]

    def module(self, address: int) -> Module:
        """Return one module by 0-based address; ``KeyError`` if not modelled."""
        return self._modules[address]

    def relay(self, module: int, address: int) -> Relay:
        """Return one relay by 0-based module and relay address."""
        return self._modules[module].relays[address]

    def switch(self, module: int, address: int) -> Switch:
        """Return one button by 0-based module and button address."""
        return self._modules[module].switches[address]
