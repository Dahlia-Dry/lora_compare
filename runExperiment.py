#!/usr/bin/env python3
"""Run an experiment defined in YAML. See documentation/EXPERIMENTS.md.

    python3 runExperiment.py experiments/mesh_b.yaml
    python3 runExperiment.py experiments/mesh_b.yaml --jobs 4
    python3 runExperiment.py experiments/mesh_b.yaml --dry-run
"""
import argparse
import logging
import math
import sys

import pandas as pd

from lib.experiment.loader import load_experiment_file, plan_runs


def fmt(value, digits=3):
    if isinstance(value, float):
        return "nan" if math.isnan(value) else f"{value:.{digits}g}"
    return str(value)


def describe(run):
    point = " ".join(f"{path}={value}" for path, value in run.sweep_point.items())
    return f"{run.architecture} seed={run.seed}" + (f" {point}" if point else "")


def print_progress(result, done, total, sweep_keys):
    r = result.row
    sweep = " ".join(f"{k}={r[k]}" for k in sweep_keys)
    print(f"[{done}/{total}] {r['architecture']} seed={r['seed']}{' ' + sweep if sweep else ''}: "
          f"delivery {fmt(r['delivery_ratio_mean'])}, latency {fmt(r['latency_mean_s'])} s, "
          f"energy {fmt(r['energy_total_J'])} J", flush=True)


def print_summary(rows, sweep_keys):
    df = pd.DataFrame(rows)
    keys = ["architecture"] + list(sweep_keys)
    metrics = ["delivery_ratio_mean", "delivery_ratio_min", "latency_mean_s", "latency_p95_s",
               "collision_rate", "energy_total_J", "energy_per_delivered_J"]
    summary = df.groupby(keys, dropna=False)[metrics].mean()
    print("\nMean over seeds:")
    with pd.option_context("display.width", 200, "display.max_columns", None, "display.float_format", "{:.3g}".format):
        print(summary)
    missing = sorted({m for cell in df["energy_missing"] for m in cell.split()})
    if missing:
        print("\nEnergy is NaN because these hardware values are not filled in yet:", ", ".join(missing))


def main(args=None):
    parser = argparse.ArgumentParser(description="Run an experiment defined in YAML (see documentation/EXPERIMENTS.md)")
    parser.add_argument("experiment", help="experiment YAML file")
    parser.add_argument("--arch", action="append", metavar="NAME", help="only run this architecture (repeatable)")
    parser.add_argument("--jobs", type=int, default=1, help="runs to simulate in parallel (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="validate the file and list the runs without simulating")
    parser.add_argument("--gui", action="store_true", help="show the node placement graph while simulating. Only for an experiment with exactly one run")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    a = parser.parse_args(args)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING)

    try:
        runs = plan_runs(load_experiment_file(a.experiment), a.arch)
    except (OSError, ValueError) as e:
        parser.exit(2, f"{a.experiment}: {e}\n")
    if not runs:
        parser.exit(2, "no runs to simulate\n")

    sweep_keys = list(runs[0].sweep_point)

    if a.dry_run:
        print(f"{len(runs)} run(s):")
        for run in runs:
            print(f"  {run.run_id}: {describe(run)}")
        return 0

    if a.gui:
        if len(runs) != 1:
            parser.exit(2, f"--gui needs exactly one run, the experiment has {len(runs)}. Use --arch and a single seed\n")
        from lib.experiment.runner import run_one
        from lib.experiment.loader import build_run
        from lib.gui import Graph
        conf, _, _ = build_run(runs[0])
        graph = Graph(conf)
        result = run_one(runs[0], graph)
        print_progress(result, 1, 1, sweep_keys)
        graph.save()
        print_summary([result.row], sweep_keys)
        return 0

    from lib.experiment.runner import run_experiment
    out, results = run_experiment(a.experiment, a.arch, a.jobs,
                                  lambda result, done, total: print_progress(result, done, total, sweep_keys))
    print_summary([r.row for r in results], sweep_keys)
    print(f"\nResults written to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
