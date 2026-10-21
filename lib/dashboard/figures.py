"""Figures of the results dashboard. Every function takes the loaded Results,
the current Selection and a theme ("light" or "dark") and returns a Plotly
figure, so the Dash app and the static export draw exactly the same charts.
"""
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from lib.dashboard.data import METRICS, Results, Selection, aggregate, select_runs
from lib.dashboard.style import (COMPONENT_KINDS, GROUP_LABELS, GROUP_SYMBOLS, GROUPS, SEQUENTIAL, TOKENS,
                                 arch_color, group_color, kind_color, rgba, template)


def _base(theme, title, height=380, **layout):
    fig = go.Figure(layout=dict(template=template(theme), title=dict(text=title), height=height, **layout))
    return fig


def _empty(fig, theme, message):
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                       font=dict(color=TOKENS[theme]["muted"], size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def _no_energy_message(res):
    if res.energy_missing:
        return "No energy data: fill in hardware/components.yaml<br>(" + ", ".join(res.energy_missing[:4]) + \
               (", ..." if len(res.energy_missing) > 4 else "") + ")"
    return "No energy data: the nodes have no hardware device"


def _fmt(metric, value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if METRICS[metric].percent:
        return f"{value:.0%}" if abs(value) >= 0.1 else f"{value:.1%}"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:.3g}"


_KEY_LABELS = {
    "deployment.sensors.count": "Number of sensors",
    "deployment.responders.count": "Number of responders",
    "deployment.gateway_sites.count": "Number of gateway sites",
    "deployment.min_dist_m": "Minimum node distance (m)",
    "traffic.reading_period_s": "Reading period (s)",
    "traffic.payload_bytes": "Payload (bytes)",
    "simtime_s": "Simulated time (s)",
    "phy.modem_preset": "Modem preset",
    "phy.pathloss_model": "Path loss model",
}


def _key_label(key):
    """Readable name of a sweep key."""
    return _KEY_LABELS.get(key, key)


def _point_text(res, sel, skip=None):
    return ", ".join(f"{_key_label(k).lower()} = {v}" for k, v in sel.point.items() if k != skip)


def _legend(fig, n_series):
    fig.update_layout(showlegend=n_series >= 2)


def _numeric(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)


def metric_vs_sweep(res: Results, metric: str, sel: Selection, theme="light"):
    """One metric for every architecture: against the swept parameter (lines
    with a 95% CI band, or every seed), or per architecture without a sweep."""
    m = METRICS[metric]
    t = TOKENS[theme]
    fig = _base(theme, m.label)
    df = select_runs(res, sel, vary=sel.x_key)
    if df.empty or df[metric].isna().all():
        msg = _no_energy_message(res) if metric.startswith("energy") else "No data for this selection"
        return _empty(fig, theme, msg)

    archs = [a for a in res.architectures if a in sel.architectures]
    if sel.x_key is None:
        for arch in archs:
            d = df[df["architecture"] == arch]
            color = arch_color(res.architectures, arch, theme)
            if sel.seed_mode == "seeds":
                fig.add_trace(go.Scatter(x=[arch] * len(d), y=d[metric], mode="markers", name=arch,
                                         marker=dict(color=color, size=9, line=dict(width=2, color=t["surface"])),
                                         customdata=d["seed"], hovertemplate="%{x} · seed %{customdata}<br>%{y}<extra></extra>"))
            else:
                a = aggregate(d, metric, ["architecture"])
                up, down = a["ci"], np.minimum(a["ci"], a["mean"])
                if m.percent:  # a ratio cannot leave [0, 1]
                    up, down = np.minimum(a["ci"], 1 - a["mean"]), np.minimum(a["ci"], a["mean"])
                fig.add_trace(go.Scatter(x=a["architecture"], y=a["mean"], mode="markers", name=arch,
                                         error_y=dict(array=up, arrayminus=down, color=color, thickness=2, width=6),
                                         marker=dict(color=color, size=11, line=dict(width=2, color=t["surface"])),
                                         customdata=np.stack([a["ci"], a["n"]], axis=-1),
                                         hovertemplate="%{x}: %{y}<br>±%{customdata[0]:.3g} (95% CI, %{customdata[1]} seeds)<extra></extra>"))
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=archs, showspikes=False,
                         range=[-0.7, len(archs) - 0.3])
        fig.update_yaxes(showspikes=False)
    else:
        xs = res.sweep_values[sel.x_key]
        for arch in archs:
            d = df[df["architecture"] == arch]
            color = arch_color(res.architectures, arch, theme)
            a = aggregate(d, metric, [sel.x_key]).set_index(sel.x_key).reindex(xs).reset_index()
            if sel.seed_mode == "ci":
                upper, lower = a["mean"] + a["ci"].fillna(0), a["mean"] - a["ci"].fillna(0)
                lower = lower.clip(lower=0)  # no metric here can be negative
                if m.percent:  # nor can a ratio exceed 1
                    upper = upper.clip(upper=1)
                fig.add_trace(go.Scatter(x=a[sel.x_key], y=upper, mode="lines", line=dict(width=0), showlegend=False,
                                         hoverinfo="skip", legendgroup=arch))
                fig.add_trace(go.Scatter(x=a[sel.x_key], y=lower, mode="lines", line=dict(width=0), fill="tonexty",
                                         fillcolor=rgba(color, 0.12), showlegend=False, hoverinfo="skip", legendgroup=arch))
            else:
                fig.add_trace(go.Scatter(x=d[sel.x_key], y=d[metric], mode="markers", showlegend=False, legendgroup=arch,
                                         marker=dict(color=rgba(color, 0.55), size=8), customdata=d["seed"],
                                         hovertemplate=f"{arch} · seed %{{customdata}}: %{{y}}<extra></extra>"))
            fig.add_trace(go.Scatter(x=a[sel.x_key], y=a["mean"], mode="lines+markers", name=arch, legendgroup=arch,
                                     line=dict(color=color, width=2, shape="linear"),
                                     marker=dict(color=color, size=9, line=dict(width=2, color=t["surface"])),
                                     customdata=np.stack([a["ci"], a["n"]], axis=-1),
                                     hovertemplate=f"{arch}: %{{y}} ±%{{customdata[0]:.3g}} (%{{customdata[1]}} seeds)<extra></extra>"))
        fig.update_xaxes(title=dict(text=_key_label(sel.x_key)),
                         type="linear" if _numeric(xs) else "category")
        if _numeric(xs):
            fig.update_xaxes(tickvals=xs)
        fig.update_layout(hovermode="x unified")
    if m.percent:
        fig.update_yaxes(tickformat=".0%")
    if m.percent and m.higher_is_better:  # delivery ratios: always the full 0-100% scale
        fig.update_yaxes(range=[-0.02, 1.02])
    else:
        fig.update_yaxes(rangemode="tozero")
    rest = _point_text(res, sel, skip=sel.x_key)
    if rest:
        fig.add_annotation(text=f"at {rest}", x=1, y=1.02, xref="paper", yref="paper", xanchor="right",
                           yanchor="bottom", showarrow=False, font=dict(color=t["muted"], size=12))
    _legend(fig, len(archs))
    return fig


def tradeoff(res: Results, sel: Selection, x_metric="energy_per_delivered_J", y_metric="delivery_ratio_mean", theme="light"):
    """Cost against delivery: one point per architecture and value of the swept
    parameter, joined in sweep order."""
    t = TOKENS[theme]
    fig = _base(theme, f"{METRICS[y_metric].short} against {METRICS[x_metric].short.lower()}", height=460)
    df = select_runs(res, sel, vary=sel.x_key)
    if df.empty or df[x_metric].isna().all():
        return _empty(fig, theme, _no_energy_message(res) if x_metric.startswith("energy") else "No data for this selection")
    by = [sel.x_key] if sel.x_key else []
    archs = [a for a in res.architectures if a in sel.architectures]
    for arch in archs:
        d = df[df["architecture"] == arch]
        color = arch_color(res.architectures, arch, theme)
        ax = aggregate(d, x_metric, by or ["architecture"])
        ay = aggregate(d, y_metric, by or ["architecture"])
        labels = [f"{_key_label(sel.x_key)} = {v}" for v in ax[sel.x_key]] if sel.x_key else [arch] * len(ax)
        text = [""] * len(labels)
        if labels:  # label the ends of the path only
            text[0], text[-1] = str(ax[sel.x_key].iloc[0]) if sel.x_key else "", str(ax[sel.x_key].iloc[-1]) if sel.x_key else ""
        fig.add_trace(go.Scatter(x=ax["mean"], y=ay["mean"], mode="lines+markers+text", name=arch,
                                 text=text, textposition="top center", textfont=dict(color=t["ink2"], size=11),
                                 line=dict(color=color, width=2),
                                 marker=dict(color=color, size=10, line=dict(width=2, color=t["surface"])),
                                 customdata=np.stack([labels, ax["ci"], ay["ci"]], axis=-1),
                                 hovertemplate=f"{arch} · %{{customdata[0]}}<br>{METRICS[x_metric].short}: %{{x:.3g}} "
                                               f"±%{{customdata[1]:.2g}}<br>{METRICS[y_metric].short}: %{{y:.1%}} "
                                               f"±%{{customdata[2]:.1%}}<br>(95% CI over seeds)<extra></extra>"))
    fig.update_xaxes(title=dict(text=METRICS[x_metric].label))
    fig.update_yaxes(title=dict(text=METRICS[y_metric].label), tickformat=".0%", range=[-0.02, 1.02])
    better = "↖ better" if not METRICS[x_metric].higher_is_better else "↗ better"
    fig.add_annotation(text=better, x=0 if better.startswith("↖") else 1, y=1, xref="paper", yref="paper",
                       xanchor="left" if better.startswith("↖") else "right", yanchor="top", showarrow=False,
                       font=dict(color=t["muted"], size=12))
    _legend(fig, len(archs))
    return fig


def latency_ecdf(res: Results, sel: Selection, theme="light"):
    """Share of (reading, responder) pairs received within time t. Each curve
    levels off at that architecture's mean delivery ratio."""
    t = TOKENS[theme]
    fig = _base(theme, "Readings received within time t", height=420)
    runs = select_runs(res, sel)
    if runs.empty or res.deliveries.empty or runs["responders"].sum() == 0:
        return _empty(fig, theme, "No responders in this selection")
    archs = [a for a in res.architectures if a in sel.architectures]
    for arch in archs:
        r = runs[runs["architecture"] == arch]
        possible = float((r["readings"] * r["responders"]).sum())
        lat = np.sort(res.deliveries[res.deliveries["run_id"].isin(r["run_id"])]["latency_s"].to_numpy())
        if possible == 0:
            continue
        color = arch_color(res.architectures, arch, theme)
        y = np.arange(1, len(lat) + 1) / possible
        fig.add_trace(go.Scatter(x=np.concatenate([[0], lat]), y=np.concatenate([[0], y]), mode="lines", name=arch,
                                 line=dict(color=color, width=2, shape="hv"),
                                 hovertemplate=f"{arch}: %{{y:.1%}} within %{{x:.2f}} s<extra></extra>"))
    fig.update_xaxes(title=dict(text="Latency t (s)"), rangemode="tozero")
    fig.update_yaxes(title=dict(text="Share of readings × responders"), tickformat=".0%", range=[0, 1.02])
    fig.add_annotation(text=f"at {_point_text(res, sel)}, all seeds" if sel.point else "all seeds", x=1, y=1.02,
                       xref="paper", yref="paper", xanchor="right", yanchor="bottom", showarrow=False,
                       font=dict(color=t["muted"], size=12))
    _legend(fig, len(archs))
    return fig


def responder_delivery(res: Results, sel: Selection, theme="light"):
    """Delivery ratio of every responder in every seed, per architecture."""
    t = TOKENS[theme]
    fig = _base(theme, "Delivery ratio per responder", height=420)
    runs = select_runs(res, sel)
    nodes = res.nodes[res.nodes["run_id"].isin(runs["run_id"]) & (res.nodes["group"] == "responder")] \
        if not res.nodes.empty else pd.DataFrame()
    if nodes.empty:
        return _empty(fig, theme, "No responders in this selection")
    nodes = nodes.merge(runs[["run_id", "architecture", "seed", "readings"]].rename(columns={"readings": "run_readings"}),
                        on="run_id")
    nodes["ratio"] = nodes["delivered"].astype(float) / nodes["run_readings"].where(nodes["run_readings"] > 0)
    archs = [a for a in res.architectures if a in sel.architectures]
    rng = np.random.default_rng(0)
    for i, arch in enumerate(archs):
        d = nodes[nodes["architecture"] == arch]
        color = arch_color(res.architectures, arch, theme)
        jitter = rng.uniform(-0.18, 0.18, len(d))
        fig.add_trace(go.Scatter(x=i + jitter, y=d["ratio"], mode="markers", name=arch,
                                 marker=dict(color=color, size=9, line=dict(width=2, color=t["surface"])),
                                 customdata=np.stack([d["node_id"], d["seed"]], axis=-1),
                                 hovertemplate=f"{arch} · responder %{{customdata[0]}} · seed %{{customdata[1]}}"
                                               f"<br>%{{y:.0%}}<extra></extra>"))
        mean = d["ratio"].mean()
        fig.add_shape(type="line", x0=i - 0.3, x1=i + 0.3, y0=mean, y1=mean, line=dict(color=t["ink2"], width=2))
    fig.update_xaxes(tickvals=list(range(len(archs))), ticktext=archs, range=[-0.6, len(archs) - 0.4],
                     showspikes=False, showgrid=False)
    fig.update_yaxes(title=dict(text="Readings received / readings generated"), tickformat=".0%",
                     range=[-0.02, 1.04], showspikes=False)
    fig.add_annotation(text="line: mean", x=1, y=1.02, xref="paper", yref="paper", xanchor="right",
                       yanchor="bottom", showarrow=False, font=dict(color=t["muted"], size=12))
    _legend(fig, len(archs))
    return fig


def _measure_column(res, measure):
    return "energy_J" if measure == "energy" else "radio_tx_s"


def _measure_label(measure):
    return "Energy (J)" if measure == "energy" else "Radio transmit time (s)"


def energy_breakdown(res: Results, sel: Selection, measure="energy", split="group", theme="light"):
    """Stacked bar per architecture: mean over seeds of the run total, split by
    node group or (for energy) by component kind."""
    t = TOKENS[theme]
    what = "Energy" if measure == "energy" else "Radio transmit time"
    fig = _base(theme, f"{what} per run, by {'node group' if split == 'group' or measure != 'energy' else 'component'}",
                height=340)
    runs = select_runs(res, sel)
    col = _measure_column(res, measure)
    nodes = res.nodes[res.nodes["run_id"].isin(runs["run_id"])] if not res.nodes.empty else pd.DataFrame()
    if nodes.empty or col not in nodes or nodes[col].isna().all():
        return _empty(fig, theme, _no_energy_message(res) if measure == "energy" else "No radio time data")
    nodes = nodes.merge(runs[["run_id", "architecture"]], on="run_id")
    archs = [a for a in res.architectures if a in sel.architectures and a in set(nodes["architecture"])]
    n_seeds = runs.groupby("architecture")["run_id"].nunique()

    if split == "component" and measure == "energy":
        segments = []
        for kind in COMPONENT_KINDS:
            cols = [f"energy_{c}_J" for c in res.components if res.component_kind.get(c, "Other") == kind]
            if cols:
                segments.append((kind, nodes[cols].sum(axis=1, min_count=1), kind_color(kind, theme)))
    else:
        segments = [(GROUP_LABELS[g], nodes[col].where(nodes["group"] == g), group_color(g, theme)) for g in GROUPS
                    if (nodes["group"] == g).any()]

    bar_width = min(0.6, 0.24 * 3 / max(len(archs), 1))
    for label, values, color in segments:
        per_arch = values.groupby(nodes["architecture"]).sum(min_count=1)
        x = [per_arch.get(a, math.nan) / n_seeds.get(a, 1) for a in archs]
        fig.add_trace(go.Bar(y=archs, x=x, orientation="h", name=label, width=bar_width,
                             marker=dict(color=color, line=dict(width=2, color=t["surface"])),
                             hovertemplate=f"%{{y}} · {label}: %{{x:.3g}}<extra></extra>"))
    fig.update_layout(barmode="stack", legend=dict(traceorder="normal"))
    fig.update_xaxes(title=dict(text=_measure_label(measure) + " per run (mean over seeds)"), showspikes=False)
    fig.update_yaxes(type="category", categoryorder="array", categoryarray=archs[::-1], showspikes=False,
                     showgrid=False)
    _legend(fig, len(segments))
    return fig


def per_node_distribution(res: Results, sel: Selection, measure="energy", theme="light"):
    """Box per node group and architecture of the per-node energy or transmit
    time, over all nodes and seeds."""
    t = TOKENS[theme]
    what = "Energy" if measure == "energy" else "Radio transmit time"
    fig = _base(theme, f"{what} per node", height=400)
    runs = select_runs(res, sel)
    col = _measure_column(res, measure)
    nodes = res.nodes[res.nodes["run_id"].isin(runs["run_id"])] if not res.nodes.empty else pd.DataFrame()
    if nodes.empty or col not in nodes or nodes[col].isna().all():
        return _empty(fig, theme, _no_energy_message(res) if measure == "energy" else "No radio time data")
    nodes = nodes.merge(runs[["run_id", "architecture"]], on="run_id")
    archs = [a for a in res.architectures if a in sel.architectures]
    for arch in archs:
        d = nodes[nodes["architecture"] == arch]
        color = arch_color(res.architectures, arch, theme)
        fig.add_trace(go.Box(x=d["group"].map(GROUP_LABELS), y=d[col], name=arch, marker=dict(color=color, size=5),
                             line=dict(color=color, width=2), fillcolor=rgba(color, 0.12), boxpoints="outliers",
                             hovertemplate=f"{arch} · %{{x}}: %{{y:.3g}}<extra></extra>"))
    fig.update_layout(boxmode="group")
    fig.update_xaxes(categoryorder="array", categoryarray=[GROUP_LABELS[g] for g in GROUPS], showspikes=False)
    fig.update_yaxes(title=dict(text=_measure_label(measure)), showspikes=False)
    _legend(fig, len(archs))
    return fig


def airtime_share(res: Results, sel: Selection, theme="light"):
    """Share of the run each node spends transmitting, against the region's
    duty-cycle limit."""
    t = TOKENS[theme]
    fig = _base(theme, "Time spent transmitting, per node", height=400)
    runs = select_runs(res, sel)
    nodes = res.nodes[res.nodes["run_id"].isin(runs["run_id"])] if not res.nodes.empty else pd.DataFrame()
    if nodes.empty or not res.simtime_s or math.isnan(res.simtime_s):
        return _empty(fig, theme, "No data for this selection")
    nodes = nodes.merge(runs[["run_id", "architecture"]], on="run_id")
    nodes["share"] = nodes["tx_airtime_s"] / res.simtime_s
    archs = [a for a in res.architectures if a in sel.architectures]
    for arch in archs:
        d = nodes[nodes["architecture"] == arch]
        color = arch_color(res.architectures, arch, theme)
        fig.add_trace(go.Box(x=d["group"].map(GROUP_LABELS), y=d["share"], name=arch, marker=dict(color=color, size=5),
                             line=dict(color=color, width=2), fillcolor=rgba(color, 0.12), boxpoints="outliers",
                             hovertemplate=f"{arch} · %{{x}}: %{{y:.2%}}<extra></extra>"))
    if res.duty_cycle_percent < 100:
        limit = res.duty_cycle_percent / 100
        label = f"{res.region} duty-cycle limit {res.duty_cycle_percent:g}%"
        if nodes["share"].max() >= limit / 2:
            fig.add_hline(y=limit, line=dict(color=t["ink2"], width=1))
            fig.add_annotation(text=label, x=1, y=limit, xref="paper", yref="y", xanchor="right", yanchor="bottom",
                               showarrow=False, font=dict(color=t["ink2"], size=11))
        else:  # far below the limit: name it rather than squash the boxes under it
            fig.add_annotation(text=f"{label}, off scale", x=1, y=1.02, xref="paper", yref="paper",
                               xanchor="right", yanchor="bottom", showarrow=False, font=dict(color=t["muted"], size=12))
    fig.update_layout(boxmode="group")
    fig.update_xaxes(categoryorder="array", categoryarray=[GROUP_LABELS[g] for g in GROUPS], showspikes=False)
    fig.update_yaxes(title=dict(text="Transmitting, share of run"), tickformat=".1%", rangemode="tozero",
                     showspikes=False)
    _legend(fig, len(archs))
    return fig


def run_label(res: Results, run_id: int) -> str:
    r = res.runs[res.runs["run_id"] == run_id].iloc[0]
    point = ", ".join(f"{_key_label(k)} = {r[k]}" for k in res.sweep_keys)
    return f"{r['architecture']} · seed {r['seed']}" + (f" · {point}" if point else "")


def node_map(res: Results, run_id: int, theme="light"):
    """Positions of one run's nodes. Sensors are shaded by delivery ratio;
    moving responders show where they started and ended."""
    t = TOKENS[theme]
    fig = _base(theme, f"Node map · {run_label(res, run_id)}", height=560)
    nodes = res.nodes[res.nodes["run_id"] == run_id]
    if nodes.empty:
        return _empty(fig, theme, "No node data for this run")

    moving = nodes[(nodes["x"] != nodes["end_x"]) | (nodes["y"] != nodes["end_y"])]
    for _, n in moving.iterrows():
        fig.add_trace(go.Scatter(x=[n["x"], n["end_x"]], y=[n["y"], n["end_y"]], mode="lines", showlegend=False,
                                 line=dict(color=t["axis"], width=1), hoverinfo="skip"))

    sensors = nodes[nodes["group"] == "sensor"]
    if not sensors.empty:
        ratio = pd.to_numeric(sensors["delivery_ratio"], errors="coerce")
        scale = [[i / (len(SEQUENTIAL[3:]) - 1), c] for i, c in enumerate(SEQUENTIAL[3:])]
        fig.add_trace(go.Scatter(
            x=sensors["x"], y=sensors["y"], mode="markers", name="Sensors",
            marker=dict(symbol="circle", size=11, color=ratio, colorscale=scale, cmin=0, cmax=1,
                        line=dict(width=2, color=t["surface"]),
                        colorbar=dict(title=dict(text="Delivery<br>ratio", font=dict(color=t["ink2"])), tickformat=".0%",
                                      thickness=10, len=0.6, outlinewidth=0, tickfont=dict(color=t["muted"]))),
            customdata=np.stack([sensors["node_id"], ratio.fillna(-1), sensors["packets_sent"]], axis=-1),
            hovertemplate="Sensor %{customdata[0]}<br>delivery %{customdata[1]:.0%}<br>"
                          "packets sent %{customdata[2]}<extra></extra>"))
    for group, color, size in (("gateway", t["ink"], 14), ("responder", t["ink2"], 13)):
        g = nodes[nodes["group"] == group]
        if g.empty:
            continue
        x, y = (g["end_x"], g["end_y"]) if group == "responder" else (g["x"], g["y"])
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name=GROUP_LABELS[group] + (" (end position)" if group == "responder" and not moving.empty else ""),
            marker=dict(symbol=GROUP_SYMBOLS[group], size=size, color=color, line=dict(width=2, color=t["surface"])),
            customdata=np.stack([g["node_id"], g["role"], g["device"].fillna("")], axis=-1),
            hovertemplate=f"{GROUP_LABELS[group][:-1]} %{{customdata[0]}}<br>%{{customdata[1]}} %{{customdata[2]}}<extra></extra>"))
    fig.update_xaxes(title=dict(text="x (m)"), showspikes=False, constrain="domain")
    fig.update_yaxes(title=dict(text="y (m)"), showspikes=False, scaleanchor="x", scaleratio=1)
    return fig


def timeline(res: Results, run_id: int, theme="light"):
    """Every reading of one run, by sensor and time, marked by how many
    responders received it."""
    t = TOKENS[theme]
    fig = _base(theme, f"Readings over time · {run_label(res, run_id)}", height=520)
    readings = res.readings[res.readings["run_id"] == run_id] if not res.readings.empty else pd.DataFrame()
    run = res.runs[res.runs["run_id"] == run_id].iloc[0]
    if readings.empty:
        return _empty(fig, theme, "No readings in this run")
    n = int(run["responders"])
    if n == 0:
        states = [("Generated", readings, dict(symbol="circle", color=SEQUENTIAL[8]))]
    else:
        states = [
            ("Reached every responder", readings[readings["responders_reached"] >= n], dict(symbol="circle", color=SEQUENTIAL[9])),
            ("Reached some", readings[(readings["responders_reached"] > 0) & (readings["responders_reached"] < n)],
             dict(symbol="circle", color=SEQUENTIAL[4])),
            ("Reached none", readings[readings["responders_reached"] == 0],
             dict(symbol="circle-open", color=t["muted"], line=dict(width=2, color=t["muted"]))),
        ]
    sensors = sorted(readings["sensor"].unique())
    for label, d, marker in states:
        if d.empty:
            continue
        fig.add_trace(go.Scatter(x=d["gen_time_s"] / 60, y=d["sensor"].astype(str), mode="markers", name=label,
                                 marker=dict(size=9, **({"line": dict(width=2, color=t["surface"])} | marker)),
                                 customdata=np.stack([d["seq"], d["responders_reached"]], axis=-1),
                                 hovertemplate=f"Sensor %{{y}} · %{{x:.1f}} min<br>reading %{{customdata[0]}}: "
                                               f"%{{customdata[1]}} of {n} responders<extra></extra>"))
    fig.update_xaxes(title=dict(text="Time (min)"), range=[0, res.simtime_s / 60], showspikes=False)
    fig.update_yaxes(title=dict(text="Sensor"), type="category", categoryorder="array",
                     categoryarray=[str(s) for s in sensors], showspikes=False)
    return fig
