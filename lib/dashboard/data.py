"""Load one experiment's results folder (written by runExperiment.py) and
select and aggregate runs for the dashboard."""
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yaml

from lib.core.config import Config
from lib.dashboard.style import KIND_OF

# two-sided 95% Student t quantiles by degrees of freedom
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
        10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042, 60: 2.000}


@dataclass(frozen=True)
class Metric:
    label: str
    short: str
    percent: bool = False  # a 0-1 ratio shown as a percentage
    higher_is_better: bool = True


METRICS = {
    "delivery_ratio_mean": Metric("Delivery ratio (mean over responders)", "Delivery (mean)", True),
    "delivery_ratio_min": Metric("Delivery ratio (worst responder)", "Worst responder", True),
    "delivery_ratio_any": Metric("Delivered to at least one responder", "Delivered to any", True),
    "latency_mean_s": Metric("Mean latency (s)", "Mean latency", higher_is_better=False),
    "latency_p95_s": Metric("95th percentile latency (s)", "p95 latency", higher_is_better=False),
    "collision_rate": Metric("Collision rate", "Collision rate", True, higher_is_better=False),
    "energy_per_delivered_J": Metric("Energy per delivered reading (J)", "Energy / delivered", higher_is_better=False),
    "energy_total_J": Metric("Total energy (J)", "Total energy", higher_is_better=False),
    "tx_airtime_s": Metric("Total airtime (s)", "Airtime", higher_is_better=False),
    "packets_sent": Metric("Packets sent", "Packets sent", higher_is_better=False),
}
KPI_METRICS = ["delivery_ratio_mean", "delivery_ratio_min", "latency_p95_s", "energy_per_delivered_J"]
COMPARE_METRICS = ["delivery_ratio_mean", "latency_p95_s", "energy_per_delivered_J", "collision_rate"]


@dataclass
class Results:
    dir: str
    name: str
    runs: pd.DataFrame
    nodes: pd.DataFrame
    readings: pd.DataFrame
    deliveries: pd.DataFrame
    architectures: list  # in experiment-file order
    sweep_keys: list
    sweep_values: dict  # key -> sorted list of values present
    seeds: list
    simtime_s: float
    region: str
    duty_cycle_percent: float
    energy_missing: list
    component_kind: dict = field(default_factory=dict)  # component name -> kind label

    @property
    def components(self):
        return [m.group(1) for c in self.nodes.columns if (m := re.fullmatch(r"energy_(.+)_J", c))]

    def default_point(self) -> dict:
        return {k: v[0] for k, v in self.sweep_values.items()}


def _read(path, required=False):
    if os.path.exists(path):
        return pd.read_csv(path)
    if required:
        raise FileNotFoundError(f"{path} not found. Is this a folder written by runExperiment.py?")
    return pd.DataFrame()


def _native(value):
    return value.item() if isinstance(value, np.generic) else value


def load_results(directory) -> Results:
    runs = _read(os.path.join(directory, "runs.csv"), required=True)
    with open(os.path.join(directory, "experiment.yaml"), encoding="utf-8") as f:
        exp = yaml.safe_load(f)

    sweep_keys = [k for k in (exp.get("sweep") or {}) if k in runs.columns]
    sweep_values = {k: sorted({_native(v) for v in runs[k].dropna()}, key=lambda v: (isinstance(v, str), v))
                    for k in sweep_keys}
    present = set(runs["architecture"])
    architectures = [a for a in exp["architectures"] if a in present] + sorted(present - set(exp["architectures"]))

    missing = sorted({m for cell in runs["energy_missing"].fillna("") for m in str(cell).split()})

    region = (exp.get("phy") or {}).get("region", "EU_868")
    regions = Config().regions
    duty = regions[region]["duty_cycle"] if region in regions else 100

    kinds = {}
    try:
        from lib.energy.profiles import load_hardware
        for name, comp in load_hardware(exp.get("hardware")).components.items():
            kinds[name] = KIND_OF.get(comp.kind, comp.kind)
    except (OSError, ValueError):
        pass

    return Results(
        dir=directory, name=exp.get("name", os.path.basename(os.path.normpath(directory))),
        runs=runs,
        nodes=_read(os.path.join(directory, "nodes.csv")),
        readings=_read(os.path.join(directory, "readings.csv")),
        deliveries=_read(os.path.join(directory, "deliveries.csv")),
        architectures=architectures, sweep_keys=sweep_keys, sweep_values=sweep_values,
        seeds=sorted({_native(s) for s in runs["seed"]}),
        simtime_s=float(exp.get("simtime_s", math.nan)), region=region, duty_cycle_percent=duty,
        energy_missing=missing, component_kind=kinds,
    )


@dataclass
class Selection:
    """What the dashboard filters select."""
    architectures: list
    point: dict  # sweep key -> value
    x_key: str = None  # sweep key on the x axis of comparison charts, or None
    seed_mode: str = "ci"  # "ci": mean with 95% confidence interval; "seeds": every seed


def select_runs(res: Results, sel: Selection, vary: str = None) -> pd.DataFrame:
    """Runs of the selected architectures at the selected sweep point. The key
    `vary` is left free (all its values are kept)."""
    df = res.runs[res.runs["architecture"].isin(sel.architectures)]
    for key, value in sel.point.items():
        if key != vary:
            df = df[df[key] == value]
    return df


def run_ids(res: Results, sel: Selection) -> list:
    return list(select_runs(res, sel)["run_id"])


def ci95(values) -> float:
    """Half-width of the 95% confidence interval of the mean."""
    v = np.asarray([x for x in values if not pd.isna(x)], dtype=float)
    if len(v) < 2:
        return math.nan
    dof = len(v) - 1
    t = _T95[max(k for k in _T95 if k <= dof)]
    return float(t * v.std(ddof=1) / math.sqrt(len(v)))


def aggregate(df: pd.DataFrame, metric: str, by: list) -> pd.DataFrame:
    """Mean, 95% CI half-width and seed count of a metric, grouped by `by`."""
    rows = []
    for keys, group in df.groupby(by, sort=False, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        values = group[metric].astype(float)
        rows.append({**dict(zip(by, keys)), "mean": values.mean(skipna=True) if values.notna().any() else math.nan,
                     "ci": ci95(values), "n": int(values.notna().sum())})
    return pd.DataFrame(rows, columns=[*by, "mean", "ci", "n"])


def summary_table(res: Results, sel: Selection) -> pd.DataFrame:
    """Mean and 95% CI of every metric, per architecture and sweep point."""
    df = res.runs[res.runs["architecture"].isin(sel.architectures)]
    by = ["architecture", *res.sweep_keys]
    out = None
    for metric in METRICS:
        agg = aggregate(df, metric, by).rename(columns={"mean": metric, "ci": f"{metric}_ci95"})
        agg = agg.drop(columns="n") if out is not None else agg.rename(columns={"n": "seeds"})
        out = agg if out is None else out.merge(agg, on=by)
    order = {a: i for i, a in enumerate(res.architectures)}
    return out.sort_values(by=by, key=lambda s: s.map(order) if s.name == "architecture" else s).reset_index(drop=True)
