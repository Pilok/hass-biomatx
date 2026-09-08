# AGENTS.md

Working contract for any coding agent (Claude Code, Codex, Cursor, Copilot) and any
human contributor on this repository. Read it before changing anything.

## What this repository is

`hass-biomatx` is a Home Assistant custom integration for **BioMatX 2110** lighting
modules (PSO, Belgium), driven through their RS485 inter-module bus with a
USB-RS485 or Ethernet-RS485 adapter. It is a fork of
[canatella/hass-biomatx](https://github.com/canatella/hass-biomatx) (Apache-2.0,
see `NOTICE`). It is distributed through HACS as a custom repository.

Only `custom_components/biomatx/` ships to users. Everything else is tooling,
tests and documentation.

## Layout

```
custom_components/biomatx/   the integration (the only directory HACS installs)
tests/                       pytest suite, runs without hardware (fake serial link)
scripts/setup|lint|test      the three commands below
.github/workflows/           lint (ruff), test (pytest), validate (hassfest + HACS)
hacs.json                    HACS repository manifest
CHANGELOG.md                 Keep a Changelog; every PR edits [Unreleased]
```

## Commands

```bash
scripts/setup          # venv (Python >= 3.14.2) + requirements_dev.txt + pre-commit hooks
scripts/lint           # ruff format + ruff check --fix   (scripts/lint --check = CI mode)
scripts/test           # pytest (extra args are forwarded, e.g. scripts/test -k hub)
```

CI runs the same three things plus `hassfest` and `hacs/action`. A pull request is
mergeable only when all of them are green.

## Non-negotiables

1. **Tests first.** No production code without a failing test that motivates it:
   red, green, refactor. Bug fix = regression test reproducing the bug first.
2. **ruff `select = ["ALL"]`** with the small ignore list in `pyproject.toml`.
   Never add a `# noqa` without a reason on the same line.
3. **One branch, one pull request, one concern.** Branch names `feat/...`,
   `fix/...`, `chore/...`, `docs/...`, `test/...`. Commits and PR titles follow
   Conventional Commits (`feat(hub): reconnect with backoff`). Squash-merge.
4. **Never push to `main` directly.** Never force-push a shared branch.
5. **Never deploy to a Home Assistant instance from a session.** Deployment goes
   through HACS releases and the staged validation checklist in the project plan.
6. **Never open a real serial port from tests.** Tests use `tests/fake_serial.py`.
7. **Never call `biomatx.Bus.connect`, `.loop`, `.send_packet` or `.stop`.** The
   integration owns the serial transport (`hub.py`); the `biomatx` package is used
   only as a data model (`Packet`, `Module`, `Relay`, `Switch`). Grep before merging.
8. **Nothing goes upstream automatically.** Generic fixes may be prepared on a
   branch cut from `upstream/main`; opening a pull request against canatella is a
   human decision.
9. `CHANGELOG.md` `[Unreleased]` is updated in the same PR as the change.

## Domain facts the code relies on

- Frame = 2 bytes at 19200 8N1. Byte 1 = `0x5e` or `0xAe`, `e` = **emitting**
  module (0-7). Byte 2 = bit 7 released (1) / pressed (0), bits 4-6 **target**
  module, bits 0-3 target relay or button (0-9). The target is always byte 2.
- Wall buttons and front-panel buttons emit frames with emitter == target.
  Detectors wired on one module and commanding a relay on another produce
  frames whose two module fields differ (`51 07` = emitted by module 1, targets
  module 0 relay 7). Handle them like any other frame.
- Modules never report state. Home Assistant infers relay state from observed
  presses and from the frames it sends. Lights are `assumed_state`.
- Relays in timer mode switch off by themselves without any bus frame.
  Collisions between two emitters produce garbage bytes (`switch >= 10`, wrong
  release bytes). Both are normal on this bus: log at DEBUG, never ERROR.
- The scenario module has address 7. The "all off" scenario, when configured, is
  the only way to resynchronise: `biomatx.reset` fires it, then re-presses every
  relay Home Assistant believes on.
- Internal addressing is 0-based like the frames (unique_ids, stored config).
  Every user-facing string is 1-based ("BioMatX module 2", "Relay 8", scenario
  numbers 1-10 in the config form).
- The reference adapter (DSD TECH SH-U11G) does not echo transmitted bytes.
  Do not rely on echo, do not assume its absence for other adapters.

## Home Assistant rules that bite custom integrations

- No `strings.json`. All user-facing text lives in `translations/en.json`
  (flat text, no `[%key:...%]`) with `fr.json` carrying the same keys.
- `services.yaml` holds structure only; names and descriptions live under
  `services` in the translation files; service icons in `icons.json`.
- Services are registered in `async_setup`, resolve their config entry through
  `homeassistant.helpers.service.async_get_config_entry`, and raise translated
  `ServiceValidationError` / `HomeAssistantError`.
- `entry.runtime_data` (typed `ConfigEntry[BiomatxHub]`), never `hass.data`.
  Platforms are forwarded with one awaited `async_forward_entry_setups`.
  Background work uses `entry.async_create_background_task`.
- Entities: `_attr_has_entity_name = True`, unique_id prefixed by the entry id,
  `DeviceInfo` for the hub, each module and the scenario module.
- `manifest.json` keys sorted (domain, name, then alphabetical); `version` must
  equal the release tag without the `v` prefix.

## Definition of done for a pull request

- Tests written first, passing, coverage not lower than before.
- `scripts/lint --check` clean; hassfest and HACS actions green.
- Code review by a fresh reader (agent or human) with findings addressed.
- Hardware-affecting changes carry a written verification protocol (frame sent,
  relay expected, observed result) and the owner's sign-off.
- `CHANGELOG.md` updated. Legacy module removed from `extend-exclude` in
  `pyproject.toml` when it has been rewritten.

## Release procedure

1. Release PR: date the `CHANGELOG.md` section, set `manifest.json` `version`.
2. Tag `vX.Y.Z` on `main` and push the tag; `release.yml` verifies that the
   manifest version equals the tag and publishes the GitHub Release with the
   changelog section as notes (pre-release when the tag contains `-`).
3. HACS picks up GitHub Releases only (bare tags are ignored).

## Roadmap

- **V1.0** parity with upstream, HA 2026 compatibility, tests, CI, HACS.
- **V1.1** protocol and transport in-house on `serialx` (the serial library
  Home Assistant core uses), no `pyserial-asyncio` dependency.
- **V2** per-relay profiles (on/off, detector + timer, on/off + timer) and an
  import of the owner's room/lighting table into the Home Assistant registries.
