"""Turn an experiment YAML file into simulation runs: validate it, expand the
sweep into variants, plan one run per variant x architecture x seed, and
build the Config and node list of a run.

Node placement depends only on the deployment and the seed, never on the
architecture, so every architecture is simulated on the same positions.
"""
import copy
import itertools
import math
import random
from dataclasses import dataclass

import yaml

from lib.core.config import Config
from lib.core.point import Point
from lib.energy.profiles import load_hardware
from lib.experiment.schema import Experiment
from lib.mesh.node import MESHTASTIC_ROLE, NodeConfig

GROUPS = ("sensor", "gateway", "responder")
UNSWEEPABLE = {"name", "seeds", "sweep", "output"}


@dataclass
class RunSpec:
    run_id: int
    architecture: str
    seed: int
    sweep_point: dict  # dotted path -> value for this variant
    experiment: Experiment  # the variant, with sweep values applied


@dataclass
class NodeInfo:
    """What a simulated node stands for in the experiment."""
    node_id: int
    group: str  # "sensor", "gateway" or "responder"
    type: str
    role: str
    device: str = None
    gateway_site: int = None  # index of the gateway site the node belongs to


def load_experiment_file(path) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return raw


def set_dotted(data: dict, path: str, value):
    """Set `data["a"]["b"]["c"] = value` for path "a.b.c". Every parent must already exist."""
    keys = path.split(".")
    if keys[0] in UNSWEEPABLE:
        raise ValueError(f"sweep {path}: '{keys[0]}' cannot be swept")
    node = data
    for i, key in enumerate(keys[:-1]):
        if not isinstance(node, dict) or key not in node:
            raise ValueError(f"sweep {path}: '{'.'.join(keys[:i + 1])}' is not in the experiment")
        node = node[key]
    if not isinstance(node, dict):
        raise ValueError(f"sweep {path}: '{'.'.join(keys[:-1])}' is not a mapping")
    node[keys[-1]] = value


def expand_sweep(raw: dict) -> list:
    """All (sweep_point, variant) pairs: the cartesian product of the sweep values,
    each applied to a copy of the raw experiment and validated."""
    base = Experiment.model_validate(raw)
    paths = list(base.sweep)
    variants = []
    for values in itertools.product(*(base.sweep[p] for p in paths)):
        variant = copy.deepcopy(raw)
        variant.pop("sweep", None)
        point = dict(zip(paths, values))
        for path, value in point.items():
            set_dotted(variant, path, value)
        try:
            variants.append((point, Experiment.model_validate(variant)))
        except ValueError as e:
            raise ValueError(f"sweep point {point}: {e}") from e
    return variants


def plan_runs(raw: dict, architectures=None) -> list:
    """One RunSpec per sweep variant x architecture x seed, numbered from 0."""
    runs = []
    for point, exp in expand_sweep(raw):
        names = list(exp.architectures)
        if architectures:
            unknown = set(architectures) - set(names)
            if unknown:
                raise ValueError(f"unknown architecture(s) {sorted(unknown)}. Known: {names}")
            names = [n for n in names if n in architectures]
        check_devices(exp)
        for name in names:
            for seed in exp.seeds:
                runs.append(RunSpec(len(runs), name, seed, point, exp))
    return runs


def check_devices(exp: Experiment):
    catalog = load_hardware(exp.hardware)
    for name, arch in exp.architectures.items():
        for spec in [arch.sensor, *arch.gateway, arch.responder]:
            if spec is not None and spec.device is not None and spec.device not in catalog.devices:
                raise ValueError(f"architecture {name}: unknown device {spec.device!r}. Known: {sorted(catalog.devices)}")


def configure_radio(conf: Config, phy):
    """Set region, modem preset, frequency, power limit and PHY options on a Config.
    The frequency follows the Meshtastic firmware: the centre of slot
    `frequency_slot`, counting `bw`-wide slots up from the region's start."""
    conf.REGION = conf.regions[phy.region]
    conf.MODEM_PRESET = phy.modem_preset
    bw = conf.current_preset["bw"]
    span = conf.REGION["freq_end"] - conf.REGION["freq_start"]
    slots = math.floor(span / bw + 1e-9)
    if phy.frequency_slot >= slots:
        raise ValueError(f"frequency_slot {phy.frequency_slot} out of range: {phy.region} has {slots} slot(s) at {bw / 1e3:g} kHz")
    conf.CHANNEL_NUM = phy.frequency_slot
    conf.FREQ = conf.REGION["freq_start"] + bw / 2 + phy.frequency_slot * bw
    conf.PTX = conf.REGION["power_limit"]
    conf.MODEL = phy.pathloss_model
    conf.MODEL_ASYMMETRIC_LINKS = phy.asymmetric_links.enabled
    conf.MODEL_ASYMMETRIC_LINKS_STDDEV = phy.asymmetric_links.stddev_db
    # the connectivity map skips links that are out of reach by this margin; keep
    # it at 5 sigma of the link offsets so skipping almost never changes a result
    conf.CONNECTIVITY_MAP_RSSI_MARGIN = max(conf.CONNECTIVITY_MAP_RSSI_MARGIN, 5 * phy.asymmetric_links.stddev_db)
    conf.LEGACY_COLLISION_TIMING = phy.legacy_collision_timing


def place_deployment(deployment, seed: int) -> dict:
    """Positions (x, y) for every group: "sensor", "gateway", "responder". Random
    groups are placed uniformly in the area, at least min_dist_m from every
    node placed before, in the order sensors, gateway sites, responders."""
    rng = random.Random(seed)
    half_x, half_y = deployment.area_m[0] / 2, deployment.area_m[1] / 2
    placed = []
    positions = {}
    for group_name, group in (("sensor", deployment.sensors), ("gateway", deployment.gateway_sites), ("responder", deployment.responders)):
        if group.positions is not None:
            pts = [tuple(p) for p in group.positions]
        else:
            pts = []
            for i in range(group.count):
                for _ in range(10000):
                    p = (rng.uniform(-half_x, half_x), rng.uniform(-half_y, half_y))
                    if all(math.dist(p, q) >= deployment.min_dist_m for q in placed):
                        break
                else:
                    raise ValueError(f"could not place {group_name} {i} at least {deployment.min_dist_m} m from the others; "
                                     "enlarge area_m or lower min_dist_m")
                pts.append(p)
                placed.append(p)
        positions[group_name] = pts
        if group.positions is not None:
            placed.extend(pts)
    return positions


def _speed(conf: Config, mobility: str) -> float:
    return {"static": 0, "walking": conf.WALKING_METERS_PER_MIN, "biking": conf.BIKING_METERS_PER_MIN,
            "driving": conf.DRIVING_METERS_PER_MIN}[mobility]


def build_run(run: RunSpec, catalog=None):
    """Build the Config, node configs and node descriptions of one run.

    Returns:
    (conf, node_configs, infos) -- infos[i] describes node_configs[i]
    """
    exp = run.experiment
    arch = exp.architectures[run.architecture]
    catalog = catalog or load_hardware(exp.hardware)
    d = exp.deployment

    conf = Config()
    configure_radio(conf, exp.phy)
    conf.XSIZE, conf.YSIZE = d.area_m
    conf.OX = conf.OY = 0.0
    conf.MINDIST = d.min_dist_m
    conf.SIMTIME = exp.simtime_s * conf.ONE_SECOND_INTERVAL
    conf.PERIOD = exp.traffic.reading_period_s * conf.ONE_SECOND_INTERVAL
    conf.PACKETLENGTH = exp.traffic.payload_bytes
    conf.SEED = run.seed
    conf.NODE_RNG_SEED = run.seed
    conf.MOVEMENT_ENABLED = True  # every node gets an explicit speed, so no random movers
    conf.DMs = False
    conf.HARDWARE_FILES = exp.hardware

    positions = place_deployment(d, run.seed)
    groups = {"sensor": d.sensors, "gateway": d.gateway_sites, "responder": d.responders}

    placements = []  # (group, gateway site index, spec, (x, y))
    placements += [("sensor", None, arch.sensor, p) for p in positions["sensor"]]
    for site_index, p in enumerate(positions["gateway"]):
        placements += [("gateway", site_index, spec, p) for spec in arch.gateway]
    if arch.responder is not None:
        placements += [("responder", None, arch.responder, p) for p in positions["responder"]]

    conf.NR_NODES = len(placements)
    node_configs, infos = [], []
    for node_id, (group_name, site_index, spec, (x, y)) in enumerate(placements):
        device = catalog.device(spec.device) if spec.device is not None else None
        tx = spec.tx_dbm if spec.tx_dbm is not None else (device.tx_dbm if device else conf.PTX)
        tx = min(tx, conf.PTX)  # the firmware caps power at the region limit
        gain = spec.gain_dbi if spec.gain_dbi is not None else (device.gain_dbi if device else 0)
        height = spec.height_m if spec.height_m is not None else (device.height_m if device else conf.HM)
        group = groups[group_name]

        if spec.type == "mesh_node":
            node_configs.append(NodeConfig(
                node_id, Point(x, y, height),
                period=conf.PERIOD if group_name == "sensor" else None,
                tx_power=tx, freq=conf.FREQ, role=MESHTASTIC_ROLE[spec.role],
                antenna_gain=gain, hop_limit=spec.hop_limit, device=device,
                speed_m_per_min=_speed(conf, group.mobility), gps=group.gps,
                is_sink=(group_name == "responder")))
        else:
            raise ValueError(f"node type {spec.type!r} is not implemented yet")
        infos.append(NodeInfo(node_id, group_name, spec.type, spec.role, spec.device, site_index))

    return conf, node_configs, infos
