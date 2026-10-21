"""The results dashboard: a Dash app over one experiment's results folder.

Filters in one row apply to every tab: architectures, the sweep parameter on
the x axis, the sweep point (a value for every swept parameter), whether to
show the mean with a 95% CI or every seed, and the theme. Comparison charts
vary the x-axis parameter and hold the others at the sweep point; everything
else is drawn at the sweep point.
"""
import math
import os

from dash import ALL, Dash, Input, Output, State, dash_table, dcc, html, no_update

from lib.dashboard import figures as F
from lib.dashboard.data import (COMPARE_METRICS, KPI_METRICS, METRICS, Results, Selection, aggregate,
                                select_runs, summary_table)
from lib.dashboard.export import export_figures
from lib.dashboard.style import FONT, arch_color

GRAPH_CONFIG = {"displaylogo": False, "toImageButtonOptions": {"format": "svg", "width": 800, "scale": 1},
                "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


def _graph(graph_id, **kwargs):
    return dcc.Graph(id=graph_id, config={**GRAPH_CONFIG, "toImageButtonOptions": {
        **GRAPH_CONFIG["toImageButtonOptions"], "filename": graph_id}}, className="chart", **kwargs)


def _control(label, child, cls=""):
    return html.Div([html.Label(label, className="control-label"), child], className=f"control {cls}")


def _selection(res, archs, x_key, point_values, point_ids, seed_mode):
    point = {pid["key"]: value for pid, value in zip(point_ids or [], point_values or [])}
    x = None if x_key in (None, "", "__architecture__") else x_key
    return Selection([a for a in res.architectures if a in (archs or [])], point, x, seed_mode or "ci")


def _arch_options(res, theme):
    return [{"label": html.Span([html.Span(className="swatch",
                                           style={"background": arch_color(res.architectures, a, theme)}), a]),
             "value": a} for a in res.architectures]


def _kpi_tiles(res: Results, sel: Selection, theme):
    runs = select_runs(res, sel)
    point = ", ".join(f"{F._key_label(k).lower()} = {v}" for k, v in sel.point.items())
    tiles = []
    for metric in KPI_METRICS:
        rows = []
        for arch in sel.architectures:
            a = aggregate(runs[runs["architecture"] == arch], metric, ["architecture"])
            mean = a["mean"].iloc[0] if not a.empty else math.nan
            ci = a["ci"].iloc[0] if not a.empty else math.nan
            rows.append(html.Div([
                html.Span(className="swatch", style={"background": arch_color(res.architectures, arch, theme)}),
                html.Span(arch, className="kpi-arch"),
                html.Span(F._fmt(metric, mean), className="kpi-value"),
                html.Span(f"±{F._fmt(metric, ci)}" if not math.isnan(ci) else "", className="kpi-ci"),
            ], className="kpi-row"))
        tiles.append(html.Div([html.Div(METRICS[metric].short, className="kpi-label"), *rows], className="kpi"))
    note = f"At {point}. " if point else ""
    note += "Mean over seeds, ± 95% confidence interval."
    return [html.Div(tiles, className="kpi-grid"), html.Div(note, className="note")]


TABS = ["compare", "tradeoff", "delivery", "energy", "channel", "map", "timeline", "table"]


def create_app(res: Results, tab="compare", theme="light") -> Dash:
    app = Dash(__name__, title=f"{res.name} · results", assets_folder=os.path.join(os.path.dirname(__file__), "assets"))

    x_options = [{"label": F._key_label(k), "value": k} for k in res.sweep_keys]
    x_options.append({"label": "Architecture (no sweep axis)", "value": "__architecture__"})
    default = res.default_point()

    filters = [
        _control("Architectures", dcc.Checklist(
            id="archs", value=list(res.architectures), inline=True, className="checklist",
            options=_arch_options(res, theme))),
        _control("X axis", dcc.Dropdown(id="x-key", options=x_options, clearable=False,
                                        value=res.sweep_keys[0] if res.sweep_keys else "__architecture__"), "narrow"),
        *[_control(F._key_label(k), dcc.Dropdown(id={"type": "point", "key": k}, clearable=False, value=default[k],
                                                 options=[{"label": str(v), "value": v} for v in res.sweep_values[k]]),
                   "narrow") for k in res.sweep_keys],
        _control("Seeds", dcc.RadioItems(id="seed-mode", inline=True, value="ci", className="radio",
                                         options=[{"label": "Mean ± 95% CI", "value": "ci"},
                                                  {"label": "Every seed", "value": "seeds"}])),
        _control("Theme", dcc.RadioItems(id="theme", inline=True, value=theme, className="radio",
                                         options=[{"label": "Light", "value": "light"}, {"label": "Dark", "value": "dark"}])),
        _control("Export", html.Div([html.Button("Export figures", id="export", className="button"),
                                     html.Span(id="export-status", className="note")], className="export")),
    ]

    banner = None
    if res.energy_missing:
        banner = html.Div(["Energy charts are empty until these values are filled in hardware/components.yaml: ",
                           html.Code(", ".join(res.energy_missing))], className="banner")

    run_picker = html.Div([
        _control("Architecture", dcc.Dropdown(id="run-arch", options=res.architectures, value=res.architectures[0],
                                              clearable=False), "narrow"),
        _control("Seed", dcc.Dropdown(id="run-seed", options=res.seeds, value=res.seeds[0], clearable=False), "narrow"),
    ], className="filters inner")

    tabs = dcc.Tabs(id="tabs", value=tab, className="tabs", children=[
        dcc.Tab(label="Compare", value="compare", children=[
            html.Div([_graph(f"compare-{m}") for m in COMPARE_METRICS], className="grid-2")]),
        dcc.Tab(label="Trade-offs", value="tradeoff", children=[
            html.Div([_control("Cost on the x axis", dcc.RadioItems(
                id="tradeoff-x", inline=True, value="energy_per_delivered_J", className="radio",
                options=[{"label": METRICS[m].short, "value": m}
                         for m in ["energy_per_delivered_J", "tx_airtime_s", "packets_sent", "latency_p95_s"]]))],
                className="filters inner"),
            _graph("tradeoff")]),
        dcc.Tab(label="Delivery & latency", value="delivery", children=[
            html.Div([_graph("latency-ecdf"), _graph("responder-delivery")], className="grid-2")]),
        dcc.Tab(label="Energy", value="energy", children=[
            html.Div([
                _control("Measure", dcc.RadioItems(id="energy-measure", inline=True, value="energy", className="radio",
                                                   options=[{"label": "Energy", "value": "energy"},
                                                            {"label": "Radio transmit time", "value": "tx_time"}])),
                _control("Split by", dcc.RadioItems(id="energy-split", inline=True, value="group", className="radio",
                                                    options=[{"label": "Node group", "value": "group"},
                                                             {"label": "Component", "value": "component"}])),
            ], className="filters inner"),
            html.Div([_graph("energy-breakdown"), _graph("energy-per-node")], className="grid-2")]),
        dcc.Tab(label="Channel", value="channel", children=[
            html.Div([_graph("airtime-share"), _graph("compare-collision-rate"), _graph("compare-airtime")],
                     className="grid-2")]),
        dcc.Tab(label="Map", value="map", children=[run_picker, _graph("node-map")]),
        dcc.Tab(label="Timeline", value="timeline", children=[
            html.Div("Uses the run chosen on the Map tab.", className="note inner"), _graph("timeline")]),
        dcc.Tab(label="Table", value="table", children=[html.Div(id="table", className="table-wrap")]),
    ])

    subtitle = (f"{len(res.runs)} runs · architectures {', '.join(res.architectures)} · {len(res.seeds)} seed(s)"
                + (f" · {res.simtime_s / 60:g} min simulated" if not math.isnan(res.simtime_s) else "")
                + f" · {res.region}")
    app.layout = html.Div([
        html.Header([html.H1(res.name), html.Div(subtitle, className="subtitle")]),
        banner,
        html.Div(filters, className="filters"),
        html.Div(id="kpis"),
        tabs,
        html.Div(id="theme-sink", hidden=True),
    ], id="root", className="page")

    sel_inputs = [Input("archs", "value"), Input("x-key", "value"), Input({"type": "point", "key": ALL}, "value"),
                  State({"type": "point", "key": ALL}, "id"), Input("seed-mode", "value"), Input("theme", "value")]

    def sel_of(archs, x_key, values, ids, seed_mode):
        return _selection(res, archs, x_key, values, ids, seed_mode)

    app.clientside_callback(
        "function(theme) { document.documentElement.dataset.theme = theme; return ''; }",
        Output("theme-sink", "children"), Input("theme", "value"))

    @app.callback(Output("archs", "options"), Input("theme", "value"), prevent_initial_call=True)
    def swatches(theme):
        return _arch_options(res, theme)

    @app.callback(Output("kpis", "children"), *sel_inputs)
    def kpis(archs, x_key, values, ids, seed_mode, theme):
        return _kpi_tiles(res, sel_of(archs, x_key, values, ids, seed_mode), theme)

    @app.callback([Output(f"compare-{m}", "figure") for m in COMPARE_METRICS]
                  + [Output("compare-collision-rate", "figure"), Output("compare-airtime", "figure")], *sel_inputs)
    def compare(archs, x_key, values, ids, seed_mode, theme):
        sel = sel_of(archs, x_key, values, ids, seed_mode)
        return ([F.metric_vs_sweep(res, m, sel, theme) for m in COMPARE_METRICS]
                + [F.metric_vs_sweep(res, "collision_rate", sel, theme), F.metric_vs_sweep(res, "tx_airtime_s", sel, theme)])

    @app.callback(Output("tradeoff", "figure"), *sel_inputs, Input("tradeoff-x", "value"))
    def tradeoff(archs, x_key, values, ids, seed_mode, theme, x_metric):
        return F.tradeoff(res, sel_of(archs, x_key, values, ids, seed_mode), x_metric, theme=theme)

    @app.callback(Output("latency-ecdf", "figure"), Output("responder-delivery", "figure"),
                  Output("airtime-share", "figure"), *sel_inputs)
    def delivery(archs, x_key, values, ids, seed_mode, theme):
        sel = sel_of(archs, x_key, values, ids, seed_mode)
        return F.latency_ecdf(res, sel, theme), F.responder_delivery(res, sel, theme), F.airtime_share(res, sel, theme)

    @app.callback(Output("energy-breakdown", "figure"), Output("energy-per-node", "figure"),
                  *sel_inputs, Input("energy-measure", "value"), Input("energy-split", "value"))
    def energy(archs, x_key, values, ids, seed_mode, theme, measure, split):
        sel = sel_of(archs, x_key, values, ids, seed_mode)
        return (F.energy_breakdown(res, sel, measure, split, theme),
                F.per_node_distribution(res, sel, measure, theme))

    @app.callback(Output("node-map", "figure"), Output("timeline", "figure"),
                  Input({"type": "point", "key": ALL}, "value"), State({"type": "point", "key": ALL}, "id"),
                  Input("run-arch", "value"), Input("run-seed", "value"), Input("theme", "value"))
    def single_run(values, ids, arch, seed, theme):
        sel = _selection(res, [arch], None, values, ids, "ci")
        runs = select_runs(res, sel)
        runs = runs[runs["seed"] == seed]
        if runs.empty:
            empty = F._empty(F._base(theme, "No run for this selection"), theme, "No run for this selection")
            return empty, empty
        run_id = int(runs["run_id"].iloc[0])
        return F.node_map(res, run_id, theme), F.timeline(res, run_id, theme)

    @app.callback(Output("table", "children"), *sel_inputs)
    def table(archs, x_key, values, ids, seed_mode, theme):
        df = summary_table(res, sel_of(archs, x_key, values, ids, seed_mode))
        columns = []
        for c in df.columns:
            name = METRICS[c].short if c in METRICS else (F._key_label(c) if c in res.sweep_keys else c.capitalize())
            fmt = {"specifier": ".1%"} if (c in METRICS and METRICS[c].percent) or \
                (c.endswith("_ci95") and METRICS.get(c[:-5]) and METRICS[c[:-5]].percent) else {"specifier": ".3~g"}
            columns.append({"name": name if not c.endswith("_ci95") else "± 95% CI", "id": c, "type": "numeric",
                            "format": fmt} if df[c].dtype.kind in "fi" and c not in res.sweep_keys and c != "seeds"
                           else {"name": name, "id": c})
        return [html.Div("Mean over seeds per architecture and sweep point; each metric is followed by its 95% CI.",
                         className="note"),
                dash_table.DataTable(data=df.to_dict("records"), columns=columns, sort_action="native",
                                     style_as_list_view=True, style_table={"overflowX": "auto"},
                                     style_cell={"fontFamily": FONT, "fontSize": "13px", "padding": "6px 10px",
                                                 "textAlign": "right"},
                                     style_cell_conditional=[{"if": {"column_id": "architecture"}, "textAlign": "left"}],
                                     style_header={"fontWeight": "600", "fontFamily": FONT}, export_format="csv")]

    @app.callback(Output("export-status", "children"), Input("export", "n_clicks"),
                  State("archs", "value"), State("x-key", "value"), State({"type": "point", "key": ALL}, "value"),
                  State({"type": "point", "key": ALL}, "id"), State("seed-mode", "value"), prevent_initial_call=True)
    def export(n_clicks, archs, x_key, values, ids, seed_mode):
        if not n_clicks:
            return no_update
        try:
            out = export_figures(res, sel_of(archs, x_key, values, ids, seed_mode))
        except Exception as e:  # report export problems (e.g. no Chrome for Kaleido) in the page
            return f"Export failed: {e}"
        return f"Saved to {os.path.relpath(out)}"

    return app
