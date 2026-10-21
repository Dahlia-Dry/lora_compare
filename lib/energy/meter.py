"""Per-node energy meter. `EnergyMeter` records how long each component of a
node's device spends in each state, and how many one-off events (such as an
LTE-M uplink) it performs. `report` turns that into energy using the
device's hardware profile.
"""
import math
from collections import defaultdict

from lib.energy.profiles import Device


class EnergyMeter:
    def __init__(self, device: Device, now: float, radio_state: str):
        """Start metering at sim time `now` (ms), with the radio in `radio_state`
        and every other component in its baseline state."""
        self.device = device
        self._current = {}  # component -> (state, dbm, since_ms)
        self._time_ms = defaultdict(float)  # (component, state, dbm) -> ms
        self._events = defaultdict(int)  # (component, event) -> count
        self.set_state(device.radio, radio_state, now)
        for comp, state in device.baseline.items():
            self.set_state(comp, state, now)

    def set_state(self, component: str, state: str, now: float, dbm: float = None):
        """Switch a component to `state` at sim time `now` (ms). `dbm` is the
        output power, needed only for "tx"."""
        comp = self.device.components[component]
        if not comp.has_state(state):
            raise KeyError(f"{self.device.name}: component {component} has no state '{state}'")
        if state == "tx" and dbm is None:
            raise ValueError(f"{self.device.name}: tx state needs a transmit power")
        self._accrue(component, now)
        self._current[component] = (state, dbm if state == "tx" else None, now)

    def set_radio_state(self, state: str, now: float, dbm: float = None):
        self.set_state(self.device.radio, state, now, dbm)

    def add_event(self, component: str, event: str, count: int = 1):
        if event not in self.device.components[component].event_energy_mj:
            raise KeyError(f"{self.device.name}: component {component} has no event '{event}'")
        self._events[(component, event)] += count

    def _accrue(self, component, now):
        if component in self._current:
            state, dbm, since = self._current[component]
            if now < since:
                raise ValueError(f"time went backwards for {component}: {now} < {since}")
            self._time_ms[(component, state, dbm)] += now - since

    def time_in_state_ms(self, now: float) -> dict:
        """(component, state, dbm) -> ms spent in that state up to `now`, including the current state."""
        times = defaultdict(float, self._time_ms)
        for component, (state, dbm, since) in self._current.items():
            times[(component, state, dbm)] += now - since
        return dict(times)

    def report(self, now: float) -> dict:
        """Energy used up to sim time `now` (ms), without changing the meter.

        Returns a dict:
          total_mJ   -- sum over all components (NaN if any value is missing)
          components -- per component: total_mJ, and per state ("tx@22dBm" for
                        transmit) time_s and energy_mJ, and per event count and energy_mJ
          missing    -- "component.state" values that are null in the profile
        """
        components = {name: {"total_mJ": 0.0, "states": {}, "events": {}} for name in self.device.components}
        missing = set()
        for (name, state, dbm), ms in sorted(self.time_in_state_ms(now).items(), key=lambda kv: str(kv[0])):
            comp = self.device.components[name]
            current = comp.state_current_ma(state, dbm)
            label = f"tx@{dbm:g}dBm" if state == "tx" else state
            if current is None:
                missing.add(f"{name}.{label}")
                energy = math.nan
            else:
                energy = current * comp.voltage_v * ms / 1000  # mA * V * s = mJ
            components[name]["states"][label] = {"time_s": ms / 1000, "energy_mJ": energy}
            components[name]["total_mJ"] += energy
        for (name, event), count in sorted(self._events.items()):
            per_event = self.device.components[name].event_energy_mj[event]
            if per_event is None:
                missing.add(f"{name}.{event}")
                energy = math.nan
            else:
                energy = per_event * count
            components[name]["events"][event] = {"count": count, "energy_mJ": energy}
            components[name]["total_mJ"] += energy
        total = sum(c["total_mJ"] for c in components.values())
        return {"total_mJ": total, "components": components, "missing": sorted(missing)}
