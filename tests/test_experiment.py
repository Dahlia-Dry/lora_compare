import copy
import csv
import math
import os
import subprocess
import sys
import tempfile
import unittest

import yaml
from pydantic import ValidationError

from lib.experiment.loader import (build_run, configure_radio, expand_sweep, place_deployment, plan_runs,
                                   set_dotted)
from lib.experiment.schema import Experiment

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = {
    "name": "tiny",
    "seeds": [1, 2],
    "simtime_s": 600,
    "phy": {"region": "EU_868", "modem_preset": "LONG_FAST"},
    "deployment": {
        "area_m": [1500, 1500],
        "sensors": {"count": 4},
        "gateway_sites": {"positions": [[0, 0]]},
        "responders": {"count": 2, "mobility": "walking"},
    },
    "traffic": {"reading_period_s": 120, "payload_bytes": 12},
    "architectures": {
        "B": {
            "sensor": {"type": "mesh_node", "role": "SENSOR", "device": "rak4631"},
            "gateway": [{"type": "mesh_node", "role": "ROUTER", "device": "rak4631", "gain_dbi": 3, "height_m": 2}],
            "responder": {"type": "mesh_node", "role": "CLIENT", "device": "t_echo"},
        },
    },
}


def variant(**changes):
    raw = copy.deepcopy(BASE)
    for path, value in changes.items():
        set_dotted(raw, path.replace("__", "."), value)
    return raw


class TestSchema(unittest.TestCase):
    def test_valid(self):
        exp = Experiment.model_validate(BASE)
        self.assertEqual(exp.deployment.sensors.size, 4)
        self.assertEqual(exp.phy.pathloss_model, 5)

    def test_rejects(self):
        cases = {
            "unknown key": dict(BASE, simtime=5),
            "planned node type": variant(architectures__B__sensor={"type": "lorawan_sensor"}),
            "unknown role": variant(architectures__B__sensor={"type": "mesh_node", "role": "BOSS"}),
            "count and positions": variant(deployment__sensors={"count": 1, "positions": [[0, 0]]}),
            "unknown region": variant(phy__region="MARS"),
            "moving gateway sites": variant(deployment__gateway_sites={"positions": [[0, 0]], "mobility": "walking"}),
            "gateway sites without gateway nodes": variant(architectures__B__gateway=[]),
        }
        for name, raw in cases.items():
            with self.subTest(name), self.assertRaises(ValidationError):
                Experiment.model_validate(raw)

    def test_responder_may_be_omitted(self):
        exp = Experiment.model_validate(variant(architectures__B__responder=None))
        self.assertIsNone(exp.architectures["B"].responder)


class TestSweepAndRuns(unittest.TestCase):
    def test_sweep_expansion(self):
        raw = dict(BASE, sweep={"deployment.sensors.count": [2, 3], "traffic.reading_period_s": [60, 120, 240]})
        variants = expand_sweep(raw)
        self.assertEqual(len(variants), 6)
        point, exp = variants[-1]
        self.assertEqual(point, {"deployment.sensors.count": 3, "traffic.reading_period_s": 240})
        self.assertEqual(exp.deployment.sensors.count, 3)
        self.assertEqual(exp.sweep, {})

        runs = plan_runs(raw)
        self.assertEqual(len(runs), 6 * 2, "variants x seeds")
        self.assertEqual([r.run_id for r in runs], list(range(12)))

    def test_bad_sweeps(self):
        with self.assertRaisesRegex(ValueError, "not in the experiment"):
            expand_sweep(dict(BASE, sweep={"deployment.nope.count": [1]}))
        with self.assertRaisesRegex(ValueError, "cannot be swept"):
            expand_sweep(dict(BASE, sweep={"seeds": [[1]]}))
        with self.assertRaisesRegex(ValueError, "sweep point"):
            expand_sweep(dict(BASE, sweep={"deployment.sensors.count": [-1]}))

    def test_unknown_device_and_architecture(self):
        with self.assertRaisesRegex(ValueError, "unknown device"):
            plan_runs(variant(architectures__B__sensor__device="nope"))
        with self.assertRaisesRegex(ValueError, "unknown architecture"):
            plan_runs(BASE, ["Z"])


class TestBuildRun(unittest.TestCase):
    def test_radio_settings(self):
        from lib.core.config import Config
        conf = Config()
        configure_radio(conf, Experiment.model_validate(BASE).phy)
        self.assertAlmostEqual(conf.FREQ, 869.525e6, msg="Meshtastic EU_868 LongFast frequency")
        self.assertEqual(conf.PTX, 27)

        configure_radio(conf, Experiment.model_validate(variant(phy__region="US", phy__frequency_slot=19)).phy)
        self.assertAlmostEqual(conf.FREQ, 906.875e6, msg="Meshtastic US LongFast default slot")

        with self.assertRaisesRegex(ValueError, "out of range"):
            configure_radio(conf, Experiment.model_validate(variant(phy__frequency_slot=1)).phy)

    def test_placement(self):
        d = Experiment.model_validate(variant(deployment__min_dist_m=100)).deployment
        a = place_deployment(d, 7)
        self.assertEqual(a, place_deployment(d, 7), "same seed, same positions")
        self.assertNotEqual(a["sensor"], place_deployment(d, 8)["sensor"])
        self.assertEqual(a["gateway"], [(0, 0)])
        points = a["sensor"] + a["gateway"] + a["responder"]
        for i, p in enumerate(points):
            self.assertTrue(-750 <= p[0] <= 750 and -750 <= p[1] <= 750)
            for q in points[i + 1:]:
                self.assertGreaterEqual(math.dist(p, q), 100)

    def test_nodes(self):
        raw = variant(architectures__B__gateway=[
            {"type": "mesh_node", "role": "ROUTER", "device": "rak4631", "gain_dbi": 3, "height_m": 2, "tx_dbm": 30},
            {"type": "mesh_node", "role": "CLIENT_MUTE"},
        ])
        run = plan_runs(raw)[0]
        conf, node_configs, infos = build_run(run)

        self.assertEqual(conf.NR_NODES, 4 + 2 + 2, "sensors, two nodes at the one gateway site, responders")
        self.assertEqual([i.group for i in infos], ["sensor"] * 4 + ["gateway"] * 2 + ["responder"] * 2)
        sensors = node_configs[:4]
        site_a, site_b = node_configs[4:6]
        responders = node_configs[6:]

        self.assertTrue(all(n.period == 120000 for n in sensors), "only sensors generate readings")
        self.assertTrue(all(n.period is None for n in node_configs[4:]))
        self.assertTrue(all(n.is_sink for n in responders) and not any(n.is_sink for n in node_configs[:6]))
        self.assertEqual((site_a.position.x, site_a.position.y), (site_b.position.x, site_b.position.y), "co-located")
        self.assertEqual(site_a.tx_power, 27, "capped at the EU_868 limit")
        self.assertEqual((site_a.antenna_gain, site_a.position.z), (3, 2))
        self.assertEqual(sensors[0].tx_power, 22, "from the rak4631 device")
        self.assertIsNone(site_b.device)
        self.assertEqual(responders[0].speed_m_per_min, conf.WALKING_METERS_PER_MIN)
        self.assertEqual(sensors[0].speed_m_per_min, 0)

    def test_same_positions_across_architectures(self):
        raw = copy.deepcopy(BASE)
        raw["architectures"]["B2"] = copy.deepcopy(raw["architectures"]["B"])
        raw["architectures"]["B2"]["responder"] = None
        runs = plan_runs(raw)
        b = next(r for r in runs if r.architecture == "B" and r.seed == 1)
        b2 = next(r for r in runs if r.architecture == "B2" and r.seed == 1)
        pos = lambda run: [(n.position.x, n.position.y) for n in build_run(run)[1]]
        self.assertEqual(pos(b)[:5], pos(b2), "sensors and gateways match; B2 has no responders")


class TestRunExperiment(unittest.TestCase):
    def write(self, directory, raw):
        path = os.path.join(directory, "exp.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f)
        return path

    def test_end_to_end(self):
        from lib.experiment.runner import run_experiment
        with tempfile.TemporaryDirectory() as d:
            raw = dict(BASE, output={"dir": os.path.join(d, "out")})
            out, results = run_experiment(self.write(d, raw), jobs=1)
            self.assertEqual(sorted(os.listdir(out)), ["deliveries.csv", "experiment.yaml", "nodes.csv", "runs.csv"])
            with open(os.path.join(out, "runs.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            with open(os.path.join(out, "deliveries.csv"), encoding="utf-8") as f:
                deliveries = list(csv.DictReader(f))

        self.assertEqual(len(rows), 2)
        for r in results:
            self.assertGreater(r.row["readings"], 0)
            self.assertGreater(r.row["delivery_ratio_mean"], 0)
            self.assertLessEqual(r.row["delivery_ratio_min"], r.row["delivery_ratio_mean"])
            self.assertTrue(math.isnan(r.row["energy_total_J"]), "hardware values are not filled in")
            self.assertIn("sx1262.rx", r.row["energy_missing"])
            self.assertEqual(len(r.nodes), 7)
        self.assertGreater(len(deliveries), 0)

        gen_times = [sorted(d["gen_time_s"] for d in r.deliveries) for r in results]
        self.assertNotEqual(gen_times[0], gen_times[1], "seeds change message timing, not only placement")

    def test_parallel_matches_serial(self):
        from lib.experiment.runner import run_experiment
        with tempfile.TemporaryDirectory() as d:
            raw = dict(BASE, output={"dir": os.path.join(d, "out")})
            path = self.write(d, raw)
            _, serial = run_experiment(path, jobs=1)
            _, parallel = run_experiment(path, jobs=2)
        self.assertEqual([r.row["readings"] for r in serial], [r.row["readings"] for r in parallel])
        self.assertEqual([r.row["packets_sent"] for r in serial], [r.row["packets_sent"] for r in parallel])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            raw = dict(BASE, seeds=[1], output={"dir": os.path.join(d, "out")})
            path = self.write(d, raw)
            dry = subprocess.run([sys.executable, "runExperiment.py", path, "--dry-run"],
                                 cwd=REPO, capture_output=True, text=True, check=True)
            self.assertIn("1 run(s)", dry.stdout)

            # a headless run must not import the Tk GUI
            script = ("import builtins\n"
                      "real_import = builtins.__import__\n"
                      "def guarded(name, *a, **k):\n"
                      "    if name == 'lib.gui':\n"
                      "        raise AssertionError('headless run imported lib.gui')\n"
                      "    return real_import(name, *a, **k)\n"
                      "builtins.__import__ = guarded\n"
                      "import runExperiment, sys\n"
                      f"sys.exit(runExperiment.main([{path!r}]))\n")
            run = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Results written to", run.stdout)

            bad = subprocess.run([sys.executable, "runExperiment.py", path, "--arch", "Z"],
                                 cwd=REPO, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("unknown architecture", bad.stderr)


if __name__ == '__main__':
    unittest.main()
