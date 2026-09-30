# BioMatX for Home Assistant

Home Assistant integration for the **BioMatX 2110** lighting modules made by PSO (Belgium). It reads and drives the modules through their RS485 inter-module bus with a serial adapter. Everything stays local: there is no cloud and no polling, and the frames of the bus are read as they arrive.

It works with both firmware generations of the modules, **legacy** and **master**, and detects which one is on the bus. The two behave differently: with the master firmware Home Assistant shows the real state of every relay, with the legacy firmware it can only infer it. See [Which firmware do you have?](#which-firmware-do-you-have).

## What it provides

- One `light` per relay: ten per module, up to seven modules. The relays are on/off contacts, so the lights have no brightness.
- One `event` per button of every module, and one per scenario of the scenario module. Wall buttons, front-panel buttons and motion detectors that drive a relay all show up there, with `pressed` and `released` events and the module they are wired on.
- One device for the bus, one per module and one for the scenario module.
- On the master firmware: the real state of every relay, commands confirmed by the module, and availability per module.
- A `biomatx_invalid_frame` event for bus frames that the modules' format cannot carry.

The integration registers no service actions of its own. Lights use the standard `light.turn_on` and `light.turn_off` actions.

## Requirements

- Home Assistant 2026.9.0 or later.
- [HACS](https://hacs.xyz/) 2.0.5 or later, for the installation below.
- BioMatX 2110 modules on their RS485 inter-module bus, and a USB-RS485 adapter or a serial-to-Ethernet gateway connected to that bus.

## Hardware

**Modules.** BioMatX 2110, addressed 1 to 7 on their front panel. Each has ten relays and ten buttons. The scenario module is virtual: it has no relay and ten scenario buttons.

**Adapter.** The reference adapter is the DSD TECH SH-U11G (USB to RS485). It does not echo the bytes it transmits; the integration does not rely on echo, and does not assume its absence on other adapters. Other adapters and gateways have not been tested by this project. A serial-to-Ethernet gateway is reached through a `socket://host:port` address, opened as a plain TCP connection.

**Wiring.** The bus is RS485 at 19200 baud, 8 data bits, no parity, 1 stop bit. The integration sets these on a serial device; a serial-to-Ethernet gateway has to be set to them by you. Connect the adapter to the twisted pair on pins 4 and 5 of an RJ45 socket of the bus, the two middle pins. If no frame decodes, swap the two wires: manufacturers do not all label the A and B lines the same way, and a reversed pair is a known cause.

Only one program can read a serial port. Stop any serial monitor before Home Assistant opens the port. If the other program locks the port, Home Assistant cannot open it; if it does not, the two share the bytes and both see broken frames.

## Which firmware do you have?

A bus runs one of two firmwares. The frames tell them apart:

| | Legacy | Master |
|---|---|---|
| Frame | 2 bytes, the first one `5e` or `Ae` where `e` is the emitting module (0 to 7) | starts with `a5`: 6 bytes for a button event, 9 bytes for a state report, XOR checksum |
| Idle bus | silent until a button is pressed or a detector fires | every module sends a state report every 3 s, and usually within a second of a change |
| Relay state | never reported | reported by the modules |

To recognise yours, read the bytes of the bus at 19200 8N1 with a serial terminal, while the integration is not set up or is disabled:

- **Master:** within three seconds, 9-byte frames starting with `a5`, one per module every 3 s. For example `a5 1a 7f 40 81 01 00 00 00` is the state report of module 1 with every relay off.
- **Legacy:** nothing until someone presses a button, then 2-byte frames. For example `50 00` is button 1 of module 1 pressed and `50 80` is the same button released.

You do not have to tell the integration: it detects the firmware from the first valid frame it reads. A legacy bus is silent until a button is pressed, so the integration refuses commands until it has read that first frame. In Home Assistant, the lights of a legacy bus have separate on and off buttons, because their state is assumed, while the lights of a master bus show their real state.

The master firmware is not installed from this repository. Your installer reprograms the modules to obtain it. The integration reads one firmware per bus, so every module of a bus has to run the same one.

What changes for Home Assistant:

| | Legacy | Master |
|---|---|---|
| Light state | inferred from the button presses seen on the bus and from the commands Home Assistant sends; shown as assumed, and restored after a restart | the state the module reports |
| Command | the state flips as soon as the press is sent; nothing confirms it | sent as a press and a release, then confirmed by the module's report; pressed once more if the relay did not move; an error if it still did not |
| Timer expiry | invisible | visible within three seconds |
| Availability | every entity follows the serial link | every entity follows the link, and the lights of a module become unavailable when it is silent for 10 s |

Nothing is sent on the bus when Home Assistant starts.

## Installation

1. Install HACS if you have not yet, following its [documentation](https://hacs.xyz/docs/use/download/download/).
2. In HACS, open the menu at the top right, choose **Custom repositories**, enter `https://github.com/Pilok/hass-biomatx`, choose the type **Integration** and select **Add**.
3. Open **BioMatX** in HACS and download it. Every release before `1.0.0` is a pre-release, and while no stable release exists HACS downloads the default branch, which can be ahead of the last release. To install a release instead, choose it under **Need a different version?** in the download dialog. HACS announces the removal of that selector; when it is gone, call the `update.install` action on the update entity of the repository with the `version` of the release.
4. Restart Home Assistant.

After the restart, add the integration from **Settings** > **Devices & services** > **Add integration** and search for BioMatX.

HACS only announces updates to pre-releases when the "Pre-release" switch of the repository is on. HACS creates that switch entity for every repository it downloads, and it is disabled by default: enable it under **Settings** > **Devices & services** > **HACS**.

<!-- Setup walkthrough: written after the config flow pull request (1.0.0-beta.3). -->

## Entities and devices

| Device | Entities |
|---|---|
| BioMatX bus | none; the module devices are attached to it |
| BioMatX module 1 to 7 | lights "Relay 1" to "Relay 10", events "Button 1" to "Button 10" |
| BioMatX scenarios | events "Scenario 1" to "Scenario 10" |

Module, relay, button and scenario numbers are the ones printed on the front panels. Entity ids follow the names, for example `light.biomatx_module_1_relay_1`, `event.biomatx_module_1_button_1` and `event.biomatx_scenarios_scenario_1` in an English installation.

**Lights.** Turning a light on or off presses and releases the matching button on the bus, as a wall button would. Turning on a light that is already on sends nothing, so Home Assistant cannot restart a running timer.

**Events.** "Button N" of module M reports every press and release aimed at relay N of module M, whoever sent it: a wall button, the front panel, or a motion detector wired on another module. The event types are `pressed` and `released`. The attribute `emitter_module` is the module the button or detector is wired on. The example below ignores the transition out of `unavailable`: when the link comes back, the entity returns to its last event, which would otherwise fire the automation again.

```yaml
triggers:
  - trigger: state
    entity_id: event.biomatx_module_2_button_10
    not_from: unavailable
    not_to: [unknown, unavailable]
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'pressed' }}"
```

## The `biomatx_invalid_frame` event

On the master firmware, a frame can pass its checksum and still carry a field the format cannot: a module or an output that does not exist. The integration touches no entity for such a frame. It fires `biomatx_invalid_frame` on the Home Assistant event bus and logs a warning, at most once a minute, that counts the frames skipped meanwhile.

| Field | Content |
|---|---|
| `entry_id` | the configuration entry |
| `raw` | the frame in hex, bytes separated by spaces |
| `reason` | why the format cannot carry it |
| `target_module`, `emitter_module`, `output` | numbers as printed on the front panels; `null` when the byte that holds them is unreadable |
| `pressed` | `true` for a press, `false` for a release, `null` when unreadable |

Example: the frame `a5 e8 03 80 84 4a` gives `reason` "button out of range", `target_module` 4, `emitter_module` 1, `output` 11 and `pressed` true. A bus collision can also produce such a frame, rarely.

## Known limitations

- **Legacy firmware: the state is assumed.** The modules never report it. A relay in timer mode switches off without any frame on the bus, a frame lost in a collision is not seen, and a change made while Home Assistant is stopped is not seen either, so a light can show the wrong state. If an "all off" scenario is programmed on the modules and declared in the integration settings, every light is marked off in Home Assistant when that scenario is seen on the bus, which realigns the assumed state.
- **Master firmware: the detectors' "module 4, output 11" frame.** Motion detectors sometimes emit a frame addressed to output 11 of module 4, an output that does not exist. The master firmware executes it as a press on relay 1 of the module it addresses, which is module 4 in the captures this integration was built from. The integration reports the frame, through the event above and a warning, and cannot prevent it. Leave relay 1 of the addressed module free of any load.
- **Timer relays.** A relay in timer mode switches itself off. On the master firmware the next state report shows it, within three seconds; on the legacy firmware Home Assistant does not see it. Per-relay behaviours (timer, detector, contactor) are not modelled yet: every relay is treated as a latching relay.
- **No dimming.** The relays are on/off contacts. The lights have no brightness and nothing in the integration dims a light.
- **One reader per serial port.** See [Hardware](#hardware).
- **One command at a time.** Commands are sent one by one for the whole bus. On the master firmware each waits for its confirmation, from half a second to a few seconds, so a script that switches many relays takes that long per relay.

## Troubleshooting

To see what the integration does, raise its log level in `configuration.yaml` and restart Home Assistant, or call the `logger.set_level` action:

```yaml
logger:
  default: warning
  logs:
    custom_components.biomatx: debug
```

The debug lines show every decoded button frame. They count modules and buttons from 0, so "module 1 button 6" is module 2, button 7 on the front panel. Warnings count from 1, like the front panels and the entities.

**Setup fails with "The serial device could not be opened", or the integration stays in "Failed setup, will retry".** The log says `Cannot open the BioMatX serial device ...`. Check the path and that the adapter is plugged in. A stable path such as `/dev/serial/by-id/usb-...` survives a change of USB port. On Home Assistant OS and in a container the device has to be visible to Home Assistant. `Serial port ... is already locked by another process` means another program holds the port.

**Nothing decodes, or the frames look broken.** Swap the two bus wires, since a reversed polarity is a known cause. Then check that no other program reads the port.

**Every entity is unavailable.** The serial link is down. The log says `link to <device> lost (...), reconnecting`. The integration retries by itself after 1, 2, 5, 10 and 30 seconds, then every 60 seconds. On the master firmware the lights come back as each module sends its next report, within three seconds.

**The lights of one module are unavailable (master firmware).** The module sent no state report for 10 seconds, and the log says `module N sent no state report for 10 s, marking it unavailable`. Check the module's power and its bus cable. A module can also stay silent for a few seconds after a burst of commands. It comes back by itself at its next report.

**"BioMatX module N did not confirm the command; check the light."** After two presses the module still reported the old state. Look at the lamp and at the relay's LED. The state shown by Home Assistant is corrected by the module's next report.

**"BioMatX module N has not reported its state yet; the command was not sent."** The integration does not press a button of a master module that has not reported since startup or since the link came back. Wait a few seconds. If the message stays, the module is unavailable, see above. The same message follows a command to a module that stopped reporting while the command was pending.

**"The BioMatX bus is not connected; the command was not sent."** Either the serial link is down, see "Every entity is unavailable", or the link is up and the integration has not read a valid frame yet, so it does not know the firmware. That happens on a legacy bus, which is silent until a button is pressed: press a wall button once.

**A light turns on or off by itself (master firmware).** Look at the log for `bus frame ... the format cannot carry`. If it names output 11, see the known limitations.

To report a problem, open an [issue](https://github.com/Pilok/hass-biomatx/issues) with the firmware you have and the debug log around the incident.

## Removal

1. Go to **Settings** > **Devices & services** > **BioMatX**, open the three-dot menu and choose **Delete**.
2. Remove the repository from HACS, or delete `custom_components/biomatx` for a manual installation, then restart Home Assistant.

The integration never reprograms the modules, so removing it leaves them as they were.

## Contributing and license

The working rules of the repository, tests first and one pull request per concern, are in [`AGENTS.md`](AGENTS.md). Changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

The project is licensed under the Apache License 2.0, see [`LICENSE`](LICENSE). It is a fork of the upstream integration, which [`NOTICE`](NOTICE) credits. BioMatX is a product name of PSO; this project is independent of PSO.
