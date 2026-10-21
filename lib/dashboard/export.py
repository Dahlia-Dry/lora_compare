"""Export the dashboard's figures for a selection as static files (SVG, PDF,
PNG) for reports, plus the summary table as CSV. Uses the light theme."""
import os
import re

import plotly.io as pio

from lib.dashboard import figures as F
from lib.dashboard.data import COMPARE_METRICS, Results, Selection, select_runs, summary_table

WIDTH = 800


def selection_slug(sel: Selection) -> str:
    parts = [f"x-{sel.x_key}" if sel.x_key else "x-architecture"]
    parts += [f"{k}-{v}" for k, v in sel.point.items()]  # single-run charts use every key, x axis included
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", "__".join(parts))


def figure_set(res: Results, sel: Selection, theme="light") -> dict:
    """Every figure of the dashboard for this selection, by file name."""
    figs = {f"compare_{m}": F.metric_vs_sweep(res, m, sel, theme) for m in COMPARE_METRICS}
    figs["tradeoff_energy"] = F.tradeoff(res, sel, "energy_per_delivered_J", theme=theme)
    figs["tradeoff_airtime"] = F.tradeoff(res, sel, "tx_airtime_s", theme=theme)
    figs["latency_ecdf"] = F.latency_ecdf(res, sel, theme)
    figs["delivery_per_responder"] = F.responder_delivery(res, sel, theme)
    figs["energy_by_group"] = F.energy_breakdown(res, sel, "energy", "group", theme)
    figs["energy_by_component"] = F.energy_breakdown(res, sel, "energy", "component", theme)
    figs["energy_per_node"] = F.per_node_distribution(res, sel, "energy", theme)
    figs["tx_time_by_group"] = F.energy_breakdown(res, sel, "tx_time", "group", theme)
    figs["airtime_share"] = F.airtime_share(res, sel, theme)
    runs = select_runs(res, sel)
    for arch in sel.architectures:
        r = runs[runs["architecture"] == arch].sort_values("seed")
        if not r.empty:
            run_id = int(r["run_id"].iloc[0])
            figs[f"map_{arch}"] = F.node_map(res, run_id, theme)
            figs[f"timeline_{arch}"] = F.timeline(res, run_id, theme)
    return figs


def export_figures(res: Results, sel: Selection, formats=("svg", "pdf"), out_dir=None) -> str:
    """Write every figure in each format, plus summary.csv, to
    <results>/figures/<selection>/ (or `out_dir`). Returns the folder."""
    out = out_dir or os.path.join(res.dir, "figures", selection_slug(sel))
    os.makedirs(out, exist_ok=True)
    figs, files, heights = [], [], []
    for name, fig in figure_set(res, sel).items():
        for fmt in formats:
            figs.append(fig)
            files.append(os.path.join(out, f"{name}.{fmt}"))
            heights.append(fig.layout.height or 400)
    pio.write_images(figs, files, width=WIDTH, height=heights)  # one browser for every file: much faster
    summary_table(res, sel).to_csv(os.path.join(out, "summary.csv"), index=False)
    return out
