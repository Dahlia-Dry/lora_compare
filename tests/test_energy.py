import math
import os
import random
import tempfile
import unittest

import yaml

from lib.energy.meter import EnergyMeter
from lib.energy.profiles import load_hardware

COMPONENTS = {
    "radio_a": {
        "kind": "radio", "voltage_v": 2.0,
        "current_ma": {"sleep": 0.001, "rx": 5.0, "cad": None},
        "tx_current_ma": {10: 20.0, 20: 100.0},
    },
    "mcu_a": {
        "kind": "mcu", "voltage_v": 3.0,
        "current_ma": {"sleep": 0.002, "idle": 1.0},
    },
    "modem_a": {
        "kind": "modem", "voltage_v": 4.0,
        "current_ma": {"psm": 0.01},
        "event_energy_mj": {"uplink": 50.0},
    },
}
DEVICES = {
    "dev_a": {"radio": "radio_a", "baseline": {"mcu_a": "idle", "modem_a": "psm"},
              "tx_dbm": 20, "gain_dbi": 2, "height_m": 1.5},
}


def write_yaml(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
    return path


class TestHardwareProfiles(unittest.TestCase):
    def load(self, components=COMPONENTS, devices=DEVICES):
        with tempfile.TemporaryDirectory() as d:
            paths = [write_yaml(d, "c.yaml", {"components": components}),
                     write_yaml(d, "d.yaml", {"devices": devices})]
            return load_hardware(paths)

    def test_repo_hardware_files_load(self):
        catalog = load_hardware()
        for name in ["wio_e5", "rak4631", "t_echo", "sx1302_gw", "cohost"]:
            self.assertIn(name, catalog.devices)

    def test_device_parsed(self):
        dev = self.load().device("dev_a")
        self.assertEqual(dev.radio, "radio_a")
        self.assertEqual(set(dev.components), {"radio_a", "mcu_a", "modem_a"})
        self.assertEqual((dev.tx_dbm, dev.gain_dbi, dev.height_m), (20, 2, 1.5))

    def test_tx_current_interpolation(self):
        radio = self.load().components["radio_a"]
        self.assertEqual(radio.state_current_ma("tx", 10), 20.0)
        self.assertEqual(radio.state_current_ma("tx", 15), 60.0)
        self.assertEqual(radio.state_current_ma("tx", 5), 20.0, "below the table uses the lowest point")
        with self.assertRaises(ValueError):
            radio.state_current_ma("tx", 21)

    def test_validation(self):
        bad_radio = dict(COMPONENTS, radio_a={"kind": "radio", "voltage_v": 3.3, "current_ma": {"sleep": 1}, "tx_current_ma": {14: 1}})
        with self.assertRaisesRegex(ValueError, "'rx' state"):
            self.load(components=bad_radio)
        with self.assertRaisesRegex(ValueError, "not a known component"):
            self.load(devices={"d": dict(DEVICES["dev_a"], radio="nope")})
        with self.assertRaisesRegex(ValueError, "has no state"):
            self.load(devices={"d": dict(DEVICES["dev_a"], baseline={"mcu_a": "turbo"})})
        with self.assertRaisesRegex(ValueError, "not a radio"):
            self.load(devices={"d": dict(DEVICES["dev_a"], radio="mcu_a", baseline={})})
        with self.assertRaisesRegex(KeyError, "unknown device"):
            self.load().device("missing")


class TestEnergyMeter(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as d:
            self.device = load_hardware([write_yaml(d, "c.yaml", {"components": COMPONENTS}),
                                         write_yaml(d, "d.yaml", {"devices": DEVICES})]).device("dev_a")

    def test_energy_by_state(self):
        meter = EnergyMeter(self.device, 0, "rx")
        meter.set_radio_state("tx", 1000, dbm=15)   # 1 s rx
        meter.set_radio_state("rx", 1500)           # 0.5 s tx at 60 mA
        meter.add_event("modem_a", "uplink", 2)
        report = meter.report(4000)                 # 2.5 s more rx

        radio = report["components"]["radio_a"]
        self.assertAlmostEqual(radio["states"]["rx"]["time_s"], 3.5)
        self.assertAlmostEqual(radio["states"]["rx"]["energy_mJ"], 5.0 * 2.0 * 3.5)
        self.assertAlmostEqual(radio["states"]["tx@15dBm"]["energy_mJ"], 60.0 * 2.0 * 0.5)
        self.assertAlmostEqual(report["components"]["mcu_a"]["total_mJ"], 1.0 * 3.0 * 4.0)
        self.assertAlmostEqual(report["components"]["modem_a"]["events"]["uplink"]["energy_mJ"], 100.0)
        expected = 35 + 60 + 12 + 0.01 * 4.0 * 4.0 + 100
        self.assertAlmostEqual(report["total_mJ"], expected)
        self.assertEqual(report["missing"], [])

        # reporting does not change the meter
        self.assertAlmostEqual(meter.report(4000)["total_mJ"], expected)

    def test_missing_values_are_nan(self):
        meter = EnergyMeter(self.device, 0, "cad")
        report = meter.report(1000)
        self.assertTrue(math.isnan(report["total_mJ"]))
        self.assertEqual(report["missing"], ["radio_a.cad"])
        self.assertAlmostEqual(report["components"]["radio_a"]["states"]["cad"]["time_s"], 1.0)

    def test_rejects_bad_transitions(self):
        meter = EnergyMeter(self.device, 0, "rx")
        with self.assertRaises(KeyError):
            meter.set_radio_state("standby", 10)
        with self.assertRaises(ValueError):
            meter.set_radio_state("tx", 10)
        meter.set_radio_state("sleep", 100)
        with self.assertRaises(ValueError):
            meter.set_radio_state("rx", 50)


class TestEnergyInSimulation(unittest.TestCase):
    def test_mesh_nodes_metered(self):
        from lib.core.config import Config
        from lib.core.sim import DiscreteEventSim
        from lib.mesh.node import default_generate_node_list

        with tempfile.TemporaryDirectory() as d:
            conf = Config()
            conf.HARDWARE_FILES = [write_yaml(d, "c.yaml", {"components": COMPONENTS}),
                                   write_yaml(d, "d.yaml", {"devices": DEVICES})]
            conf.DEFAULT_DEVICE = "dev_a"
            conf.NR_NODES = 4
            conf.SIMTIME = 10 * conf.ONE_MIN_INTERVAL
            random.seed(conf.SEED)
            node_configs = default_generate_node_list(conf)

        for nc in node_configs:
            self.assertEqual(nc.tx_power, 20, "device transmit power is used")
            self.assertEqual(nc.antenna_gain, 2)
            self.assertEqual(nc.position.z, 1.5)

        sim = DiscreteEventSim(conf, node_configs)
        sim.run_simulation()
        results = sim.get_results()

        self.assertEqual(set(results["energy"]), {0, 1, 2, 3})
        total = 0
        for node in results["nodes"]:
            radio = results["energy"][node.nodeid]["components"]["radio_a"]["states"]
            tx_s = radio.get("tx@20dBm", {"time_s": 0})["time_s"]
            self.assertAlmostEqual(radio["rx"]["time_s"] + tx_s, conf.SIMTIME / 1000, msg="radio is always in rx or tx")
            self.assertAlmostEqual(tx_s, node.txAirUtilization / 1000, msg="tx time matches airtime sent")
            total += results["energy"][node.nodeid]["total_mJ"]
        self.assertGreater(results["sent"], 0)
        self.assertAlmostEqual(results["energyTotal_mJ"], total)
        self.assertEqual(results["energyMissing"], [])

    def test_no_device_no_energy(self):
        from lib.core.config import Config
        from lib.core.sim import DiscreteEventSim
        from lib.mesh.node import default_generate_node_list

        conf = Config()
        conf.NR_NODES = 3
        conf.SIMTIME = conf.ONE_MIN_INTERVAL
        random.seed(conf.SEED)
        sim = DiscreteEventSim(conf, default_generate_node_list(conf))
        sim.run_simulation()
        results = sim.get_results()
        self.assertEqual(results["energy"], {})
        self.assertNotIn("energyTotal_mJ", results.results)


if __name__ == '__main__':
    unittest.main()
