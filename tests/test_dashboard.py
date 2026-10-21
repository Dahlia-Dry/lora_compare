import copy
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from lib.dashboard import figures as F
from lib.dashboard.data import Selection, aggregate, ci95, load_results, select_runs, summary_table

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# placeholder currents so energy is a number (not the real hardware values)
FAKE_COMPONENTS = {"components": {
    "sx1262": {"kind": "radio", "voltage_v": 3.3, "current_ma": {"sleep": 0.001, "rx": 5.0},
               "tx_current_ma": {14: 45, 22: 118}},
    "nrf52840": {"kind": "mcu", "voltage_v": 3.3, "current_ma": {"sleep": 0.003, "idle": 0.5}},
}}
FAKE_DEVICES = {"devices": {
    "node": {"radio": "sx1262", "baseline": {"nrf52840": "idle"}, "tx_dbm": 22, "gain_dbi": 0, "height_m": 1},
}}
EXPERIMENT = {
    "name": "dash_test",
    "seeds": [1, 2],
    "simtime_s": 600,
    "deployment": {
        "area_m": [1500, 1500],
        "sensors": {"count": 4},
        "gateway_sites": {"positions": [[0, 0]]},
        "responders": {"count": 2, "mobility": "walking"},
    },
    "traffic": {"reading_period_s": 120},
    "architectures": {
        "B": {"sensor": {"type": "mesh_node", "role": "SENSOR", "device": "node"},
              "gateway": [{"type": "mesh_node", "role": "ROUTER", "device": "node"}],
              "responder": {"type": "mesh_node", "role": "CLIENT", "device": "node"}},
        "B_mute": {"sensor": {"type": "mesh_node", "role": "SENSOR", "device": "node"},
                   "gateway": [{"type": "mesh_node", "role": "ROUTER", "device": "node"}],
                   "responder": {"type": "mesh_node", "role": "CLIENT_MUTE", "device": "node"}},
    },
    "sweep": {"deployment.sensors.count": [3, 5]},
}


def write_yaml(path, data):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
    return path


class DashboardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lib.experiment.runner import run_experiment
        cls.tmp = tempfile.mkdtemp()
        hw = [write_yaml(os.path.join(cls.tmp, "c.yaml"), FAKE_COMPONENTS),
              write_yaml(os.path.join(cls.tmp, "d.yaml"), FAKE_DEVICES)]
        exp = dict(copy.deepcopy(EXPERIMENT), hardware=hw, output={"dir": os.path.join(cls.tmp, "out")})
        cls.out, _ = run_experiment(write_yaml(os.path.join(cls.tmp, "exp.yaml"), exp), jobs=1)
        cls.res = load_results(cls.out)

        # the same experiment without hardware values: energy is NaN
        hw_null = copy.deepcopy(FAKE_COMPONENTS)
        hw_null["components"]["sx1262"]["current_ma"]["rx"] = None
        write_yaml(os.path.join(cls.tmp, "c_null.yaml"), hw_null)
        exp_null = dict(exp, hardware=[os.path.join(cls.tmp, "c_null.yaml"), hw[1]],
                        output={"dir": os.path.join(cls.tmp, "out_null")}, seeds=[1], sweep={})
        out_null, _ = run_experiment(write_yaml(os.path.join(cls.tmp, "exp_null.yaml"), exp_null), jobs=1)
        cls.res_null = load_results(out_null)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def sel(self, **kw):
        base = dict(architectures=list(self.res.architectures), point=self.res.default_point(),
                    x_key="deployment.sensors.count", seed_mode="ci")
        return Selection(**{**base, **kw})


class TestData(DashboardTestCase):
    def test_load(self):
        res = self.res
        self.assertEqual(res.architectures, ["B", "B_mute"])
        self.assertEqual(res.sweep_keys, ["deployment.sensors.count"])
        self.assertEqual(res.sweep_values, {"deployment.sensors.count": [3, 5]})
        self.assertEqual(res.seeds, [1, 2])
        self.assertEqual(res.energy_missing, [])
        self.assertEqual(set(res.components), {"sx1262", "nrf52840"})
        self.assertEqual(res.component_kind["sx1262"], "Radio")
        self.assertEqual((res.region, res.duty_cycle_percent), ("EU_868", 10))
        self.assertEqual(len(res.runs), 2 * 2 * 2)
        self.assertFalse(res.readings.empty)
        self.assertEqual(self.res_null.energy_missing, ["sx1262.rx"])

    def test_missing_results_folder(self):
        with self.assertRaises(FileNotFoundError):
            load_results(self.tmp)

    def test_selection(self):
        sel = self.sel(architectures=["B"], point={"deployment.sensors.count": 5})
        self.assertEqual(set(select_runs(self.res, sel)["deployment.sensors.count"]), {5})
        self.assertEqual(set(select_runs(self.res, sel, vary="deployment.sensors.count")["deployment.sensors.count"]), {3, 5})
        self.assertEqual(set(select_runs(self.res, sel)["architecture"]), {"B"})

    def test_statistics(self):
        self.assertTrue(math.isnan(ci95([1.0])))
        self.assertAlmostEqual(ci95([1.0, 2.0, 3.0]), 4.303 * 1.0 / math.sqrt(3), places=3)
        import pandas as pd
        a = aggregate(pd.DataFrame({"g": ["x", "x", "y"], "v": [1.0, 3.0, math.nan]}), "v", ["g"])
        self.assertEqual(list(a["mean"].fillna(-1)), [2.0, -1])
        self.assertEqual(list(a["n"]), [2, 0])

    def test_summary_table(self):
        table = summary_table(self.res, self.sel())
        self.assertEqual(len(table), 4, "architectures x sweep values")
        self.assertIn("delivery_ratio_mean_ci95", table.columns)
        self.assertEqual(list(table["seeds"]), [2, 2, 2, 2])


class TestFigures(DashboardTestCase):
    def figures(self, res, sel, theme):
        run_id = int(select_runs(res, sel)["run_id"].iloc[0])
        return {
            "compare": F.metric_vs_sweep(res, "delivery_ratio_mean", sel, theme),
            "compare_energy": F.metric_vs_sweep(res, "energy_per_delivered_J", sel, theme),
            "tradeoff": F.tradeoff(res, sel, theme=theme),
            "ecdf": F.latency_ecdf(res, sel, theme),
            "responders": F.responder_delivery(res, sel, theme),
            "energy_group": F.energy_breakdown(res, sel, "energy", "group", theme),
            "energy_component": F.energy_breakdown(res, sel, "energy", "component", theme),
            "tx_time": F.energy_breakdown(res, sel, "tx_time", "group", theme),
            "per_node": F.per_node_distribution(res, sel, "energy", theme),
            "airtime": F.airtime_share(res, sel, theme),
            "map": F.node_map(res, run_id, theme),
            "timeline": F.timeline(res, run_id, theme),
        }

    def test_every_figure_draws(self):
        for theme in ("light", "dark"):
            for seed_mode in ("ci", "seeds"):
                for x_key in ("deployment.sensors.count", None):
                    sel = self.sel(x_key=x_key, seed_mode=seed_mode)
                    for name, fig in self.figures(self.res, sel, theme).items():
                        with self.subTest(theme=theme, seed_mode=seed_mode, x_key=x_key, figure=name):
                            self.assertGreater(len(fig.data), 0)

    def test_architecture_colour_follows_entity(self):
        both = F.metric_vs_sweep(self.res, "delivery_ratio_mean", self.sel(seed_mode="seeds"))
        only_second = F.metric_vs_sweep(self.res, "delivery_ratio_mean", self.sel(architectures=["B_mute"], seed_mode="seeds"))
        colour = lambda fig: {t.name: t.line.color for t in fig.data if t.name}
        self.assertEqual(colour(both)["B_mute"], colour(only_second)["B_mute"])

    def test_missing_energy_shows_a_message(self):
        res = self.res_null
        sel = Selection(list(res.architectures), {}, None, "ci")
        for fig in (F.metric_vs_sweep(res, "energy_per_delivered_J", sel), F.energy_breakdown(res, sel),
                    F.per_node_distribution(res, sel), F.tradeoff(res, sel)):
            self.assertEqual(len(fig.data), 0)
            self.assertIn("sx1262.rx", fig.layout.annotations[0].text)
        # transmit time does not need hardware values
        self.assertGreater(len(F.energy_breakdown(res, sel, "tx_time").data), 0)


class TestAppAndExport(DashboardTestCase):
    def test_app_builds(self):
        from lib.dashboard.app import TABS, _kpi_tiles, create_app
        app = create_app(self.res, tab="energy", theme="dark")
        self.assertGreater(len(app.callback_map), 5)
        layout = str(app.layout)
        for tab in TABS:
            self.assertIn(f"value='{tab}'", layout)
        tiles = _kpi_tiles(self.res, self.sel(), "light")
        self.assertIn("Delivery (mean)", str(tiles))

    def test_export(self):
        from lib.dashboard.export import export_figures, selection_slug
        sel = self.sel(point={"deployment.sensors.count": 5})
        self.assertEqual(selection_slug(sel), "x-deployment.sensors.count__deployment.sensors.count-5")
        out = os.path.join(self.tmp, "figs")
        try:
            export_figures(self.res, sel, formats=["svg"], out_dir=out)
        except Exception as e:  # Kaleido needs Chrome; skip where it is not installed
            self.skipTest(f"static export unavailable: {e}")
        files = os.listdir(out)
        self.assertIn("summary.csv", files)
        self.assertIn("compare_delivery_ratio_mean.svg", files)
        self.assertIn("map_B.svg", files)

    def test_cli_rejects_bad_point(self):
        run = subprocess.run([sys.executable, "viewResults.py", self.out, "--export", "--point",
                              "deployment.sensors.count=99"], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("has values [3, 5]", run.stderr)


if __name__ == '__main__':
    unittest.main()
