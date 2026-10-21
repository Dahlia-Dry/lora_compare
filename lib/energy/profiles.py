"""Hardware profiles for energy accounting. A `Component` is one chip or module
with its current draw per state; a `Device` combines components into the
hardware of one node type. Both are loaded from YAML (see hardware/).
"""
import bisect
import os
from dataclasses import dataclass, field

import yaml

COMPONENT_KINDS = {"radio", "concentrator", "mcu", "mcu_radio", "host", "modem", "sensor"}
RADIO_KINDS = {"radio", "concentrator", "mcu_radio"}

_HARDWARE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "hardware")
DEFAULT_HARDWARE_FILES = [
    os.path.normpath(os.path.join(_HARDWARE_DIR, "components.yaml")),
    os.path.normpath(os.path.join(_HARDWARE_DIR, "devices.yaml")),
]


@dataclass(frozen=True)
class Component:
    name: str
    kind: str
    voltage_v: float
    current_ma: dict = field(default_factory=dict)  # state -> mA (None if unknown)
    tx_current_ma: dict = field(default_factory=dict)  # dBm -> mA (None if unknown)
    event_energy_mj: dict = field(default_factory=dict)  # event -> mJ (None if unknown)
    source: str = ""

    def has_state(self, state: str) -> bool:
        if state == "tx":
            return bool(self.tx_current_ma)
        return state in self.current_ma

    def state_current_ma(self, state: str, dbm: float = None):
        """Current in mA in `state` (at output power `dbm` for "tx"), or None if unknown."""
        if state == "tx":
            return self._tx_current_ma(dbm)
        if state not in self.current_ma:
            raise KeyError(f"component {self.name} has no state '{state}'")
        return self.current_ma[state]

    def _tx_current_ma(self, dbm):
        if not self.tx_current_ma:
            raise KeyError(f"component {self.name} cannot transmit (no tx_current_ma)")
        if dbm is None:
            raise ValueError(f"transmit power needed for tx current of {self.name}")
        powers = sorted(self.tx_current_ma)
        if dbm > powers[-1]:
            raise ValueError(f"{self.name} transmits at most {powers[-1]} dBm, asked for {dbm} dBm")
        if dbm <= powers[0]:
            return self.tx_current_ma[powers[0]]
        i = bisect.bisect_left(powers, dbm)
        hi = powers[i]
        if hi == dbm:
            return self.tx_current_ma[hi]
        lo = powers[i - 1]
        i_lo, i_hi = self.tx_current_ma[lo], self.tx_current_ma[hi]
        if i_lo is None or i_hi is None:
            return None
        return i_lo + (i_hi - i_lo) * (dbm - lo) / (hi - lo)


@dataclass(frozen=True)
class Device:
    name: str
    radio: str  # name of the component whose state follows the node's radio
    baseline: dict  # other component name -> constant state
    components: dict  # component name -> Component
    tx_dbm: float
    gain_dbi: float
    height_m: float
    description: str = ""


@dataclass
class HardwareCatalog:
    components: dict = field(default_factory=dict)
    devices: dict = field(default_factory=dict)

    def device(self, name: str) -> Device:
        if name not in self.devices:
            raise KeyError(f"unknown device '{name}'. Known: {sorted(self.devices)}")
        return self.devices[name]


def _number_or_none(value, where):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: expected a number or null, got {value!r}")
    return float(value)


def _parse_component(name, raw):
    where = f"component {name}"
    kind = raw.get("kind")
    if kind not in COMPONENT_KINDS:
        raise ValueError(f"{where}: kind must be one of {sorted(COMPONENT_KINDS)}, got {kind!r}")
    if "voltage_v" not in raw:
        raise ValueError(f"{where}: voltage_v is required")
    current = {str(s): _number_or_none(v, f"{where} current_ma.{s}") for s, v in (raw.get("current_ma") or {}).items()}
    tx = {float(p): _number_or_none(v, f"{where} tx_current_ma.{p}") for p, v in (raw.get("tx_current_ma") or {}).items()}
    events = {str(e): _number_or_none(v, f"{where} event_energy_mj.{e}") for e, v in (raw.get("event_energy_mj") or {}).items()}
    if kind in RADIO_KINDS:
        if "rx" not in current:
            raise ValueError(f"{where}: a {kind} needs an 'rx' state in current_ma")
        if not tx:
            raise ValueError(f"{where}: a {kind} needs tx_current_ma")
    elif tx:
        raise ValueError(f"{where}: only radios have tx_current_ma")
    return Component(name, kind, float(raw["voltage_v"]), current, tx, events, str(raw.get("source", "")))


def _parse_device(name, raw, components):
    where = f"device {name}"
    radio = raw.get("radio")
    if radio not in components:
        raise ValueError(f"{where}: radio '{radio}' is not a known component")
    if components[radio].kind not in RADIO_KINDS:
        raise ValueError(f"{where}: radio '{radio}' is a {components[radio].kind}, not a radio")
    baseline = dict(raw.get("baseline") or {})
    for comp, state in baseline.items():
        if comp not in components:
            raise ValueError(f"{where}: baseline component '{comp}' is not a known component")
        if comp == radio:
            raise ValueError(f"{where}: radio '{radio}' must not also be in baseline")
        if not components[comp].has_state(state):
            raise ValueError(f"{where}: component '{comp}' has no state '{state}'")
    for key in ("tx_dbm", "gain_dbi", "height_m"):
        if not isinstance(raw.get(key), (int, float)):
            raise ValueError(f"{where}: {key} is required and must be a number")
    used = {radio: components[radio], **{c: components[c] for c in baseline}}
    return Device(name, radio, baseline, used, float(raw["tx_dbm"]), float(raw["gain_dbi"]),
                  float(raw["height_m"]), str(raw.get("description", "")))


def load_hardware(paths=None) -> HardwareCatalog:
    """Load components and devices from YAML files. Each file may hold a
    `components:` mapping, a `devices:` mapping, or both. Devices may refer to
    components from any of the files."""
    raw_components, raw_devices = {}, {}
    for path in paths or DEFAULT_HARDWARE_FILES:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for section, target in (("components", raw_components), ("devices", raw_devices)):
            for name, raw in (data.get(section) or {}).items():
                if name in target:
                    raise ValueError(f"{section[:-1]} '{name}' defined twice")
                target[name] = raw
    components = {name: _parse_component(name, raw) for name, raw in raw_components.items()}
    devices = {name: _parse_device(name, raw, components) for name, raw in raw_devices.items()}
    return HardwareCatalog(components, devices)
