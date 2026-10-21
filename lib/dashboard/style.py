"""Colours, fonts and the Plotly template for the results dashboard, in light
and dark themes. Colours come from a validated categorical palette:

- architectures use slots 1-3 (blue, orange, aqua), which stay distinguishable
  in every pair, including for colour-blind readers, in both themes;
- node groups and component kinds use slots 4-8 (yellow, magenta, green,
  violet, red), a contiguous run of the validated order, so they never reuse
  an architecture's colour.

Colours follow the entity: an architecture keeps its colour when others are
hidden.
"""
import plotly.graph_objects as go

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

TOKENS = {
    "light": {
        "surface": "#fcfcfb", "page": "#f9f9f7", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "border": "rgba(11,11,11,0.10)",
    },
    "dark": {
        "surface": "#1a1a19", "page": "#0d0d0d", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
    },
}

_CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}

# single-hue blue ramp for magnitudes (delivery ratio on the map, timeline)
SEQUENTIAL = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
              "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

GROUPS = ["sensor", "gateway", "responder"]
GROUP_LABELS = {"sensor": "Sensors", "gateway": "Gateways", "responder": "Responders"}
GROUP_SYMBOLS = {"sensor": "circle", "gateway": "triangle-up", "responder": "diamond"}

COMPONENT_KINDS = ["Radio", "MCU", "Host", "Backhaul modem", "Sensor"]
KIND_OF = {"radio": "Radio", "concentrator": "Radio", "mcu_radio": "Radio", "mcu": "MCU",
           "host": "Host", "modem": "Backhaul modem", "sensor": "Sensor"}


def arch_color(architectures, arch, theme):
    """Colour of an architecture, by its position in the experiment file.
    Only the first three are validated against each other; more reuse slots 4+."""
    return _CATEGORICAL[theme][architectures.index(arch) % 8]


def group_color(group, theme):
    return _CATEGORICAL[theme][3 + GROUPS.index(group)]


def kind_color(kind, theme):
    return _CATEGORICAL[theme][3 + COMPONENT_KINDS.index(kind) % 5]


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def template(theme) -> go.layout.Template:
    t = TOKENS[theme]
    axis = dict(gridcolor=t["grid"], gridwidth=1, linecolor=t["axis"], linewidth=1, zeroline=False,
                ticks="outside", tickcolor=t["axis"], ticklen=4, tickfont=dict(color=t["muted"]),
                title=dict(font=dict(color=t["ink2"])), showspikes=True, spikecolor=t["axis"],
                spikethickness=1, spikedash="solid", spikemode="across", automargin=True)
    return go.layout.Template(layout=dict(
        font=dict(family=FONT, size=13, color=t["ink"]),
        paper_bgcolor=t["surface"], plot_bgcolor=t["surface"],
        title=dict(font=dict(size=15, color=t["ink"]), x=0, xanchor="left", xref="paper", y=0.98, yanchor="top"),
        xaxis=axis, yaxis=axis,
        legend=dict(font=dict(color=t["ink2"]), bgcolor="rgba(0,0,0,0)", orientation="h",
                    x=0, xanchor="left", y=1.02, yanchor="bottom", title=dict(text="")),
        hoverlabel=dict(bgcolor=t["surface"], bordercolor=t["axis"], font=dict(family=FONT, color=t["ink"])),
        margin=dict(l=64, r=24, t=72, b=64),
        colorway=_CATEGORICAL[theme],
        barcornerradius=4,
    ))
