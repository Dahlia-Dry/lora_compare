#!/usr/bin/env python3
"""Explore one experiment's results in a dashboard, or export its figures.

    python3 viewResults.py out/mesh_b                 # open the dashboard at http://127.0.0.1:8050
    python3 viewResults.py out/mesh_b --export        # write SVG + PDF figures and summary.csv, then exit
    python3 viewResults.py out/mesh_b --export --point deployment.sensors.count=30 --formats png
"""
import argparse
import sys

import yaml

from lib.dashboard.data import Selection, load_results


def parse_point(res, items, parser):
    point = res.default_point()
    for item in items or []:
        key, sep, raw = item.partition("=")
        if not sep or key not in res.sweep_values:
            parser.error(f"--point {item}: expected KEY=VALUE with KEY one of {res.sweep_keys}")
        value = yaml.safe_load(raw)
        if value not in res.sweep_values[key]:
            parser.error(f"--point {item}: {key} has values {res.sweep_values[key]}")
        point[key] = value
    return point


def main(args=None):
    parser = argparse.ArgumentParser(description="Dashboard for the results of one experiment (a folder written by runExperiment.py)")
    parser.add_argument("results", help="results folder, e.g. out/mesh_b")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true", help="Dash debug mode (reloads on code changes)")
    parser.add_argument("--tab", default="compare", help="tab to open on: compare, tradeoff, delivery, energy, "
                                                        "channel, map, timeline or table")
    parser.add_argument("--theme", choices=["light", "dark"], default="light", help="theme to open with")
    parser.add_argument("--export", action="store_true", help="export figures instead of starting the dashboard")
    parser.add_argument("--formats", default="svg,pdf", help="export formats, comma separated (svg, pdf, png). Default svg,pdf")
    parser.add_argument("--point", action="append", metavar="KEY=VALUE",
                        help="sweep point for --export (repeatable). Default: the first value of every sweep key")
    parser.add_argument("--x", metavar="KEY", help="sweep key on the x axis for --export. Default: the first sweep key")
    a = parser.parse_args(args)

    try:
        res = load_results(a.results)
    except (OSError, KeyError) as e:
        parser.exit(2, f"{a.results}: {e}\n")

    if a.export:
        from lib.dashboard.export import export_figures
        if a.x is not None and a.x not in res.sweep_keys:
            parser.error(f"--x {a.x}: not a sweep key. Sweep keys: {res.sweep_keys}")
        x_key = a.x or (res.sweep_keys[0] if res.sweep_keys else None)
        sel = Selection(list(res.architectures), parse_point(res, a.point, parser), x_key, "ci")
        out = export_figures(res, sel, [f.strip() for f in a.formats.split(",") if f.strip()])
        print(f"Figures written to {out}/")
        return 0

    from lib.dashboard.app import TABS, create_app
    if a.tab not in TABS:
        parser.error(f"--tab {a.tab}: choose from {', '.join(TABS)}")
    create_app(res, a.tab, a.theme).run(host=a.host, port=a.port, debug=a.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
