"""Schema of an experiment YAML file (see documentation/EXPERIMENTS.md).
Every model rejects unknown keys, so a typo in the YAML is an error rather
than a silently ignored setting.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.core.config import Config
from lib.mesh.node import MESHTASTIC_ROLE

_DEFAULTS = Config()

IMPLEMENTED_NODE_TYPES = {"mesh_node"}
PLANNED_NODE_TYPES = {"lorawan_sensor", "lorawan_gateway", "hybrid_concentrator", "hybrid_cohost"}

Mobility = Literal["static", "walking", "biking", "driving"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AsymmetricLinks(Strict):
    enabled: bool = True
    stddev_db: float = Field(2.0, ge=0)


class Phy(Strict):
    region: str = "EU_868"
    modem_preset: str = "LONG_FAST"
    frequency_slot: int = Field(0, ge=0, description="Meshtastic frequency slot (0-based) within the region")
    pathloss_model: int = Field(5, ge=0, le=6)
    asymmetric_links: AsymmetricLinks = AsymmetricLinks()
    legacy_collision_timing: bool = False

    @field_validator("region")
    @classmethod
    def known_region(cls, v):
        if v not in _DEFAULTS.regions:
            raise ValueError(f"unknown region {v!r}. Known: {sorted(_DEFAULTS.regions)}")
        return v

    @field_validator("modem_preset")
    @classmethod
    def known_preset(cls, v):
        if v not in _DEFAULTS.MODEM_PRESETS:
            raise ValueError(f"unknown modem preset {v!r}. Known: {sorted(_DEFAULTS.MODEM_PRESETS)}")
        return v


class Group(Strict):
    """A group of nodes: either `count` placed at random, or explicit `positions`."""
    count: Optional[int] = Field(None, ge=0)
    positions: Optional[list[tuple[float, float]]] = None
    mobility: Mobility = "static"
    gps: bool = False

    @model_validator(mode="after")
    def count_or_positions(self):
        if (self.count is None) == (self.positions is None):
            raise ValueError("give exactly one of 'count' or 'positions'")
        return self

    @property
    def size(self) -> int:
        return self.count if self.positions is None else len(self.positions)


class Deployment(Strict):
    area_m: tuple[float, float]
    min_dist_m: float = Field(10, ge=0)
    sensors: Group
    gateway_sites: Group = Group(count=0)
    responders: Group = Group(count=0)

    @model_validator(mode="after")
    def gateway_sites_static(self):
        if self.gateway_sites.mobility != "static":
            raise ValueError("gateway sites cannot move")
        return self


class Traffic(Strict):
    reading_period_s: float = Field(gt=0)
    payload_bytes: int = Field(12, ge=1, le=255)


class NodeSpec(Strict):
    type: str
    role: Optional[str] = None
    device: Optional[str] = None
    tx_dbm: Optional[float] = None
    gain_dbi: Optional[float] = None
    height_m: Optional[float] = Field(None, gt=0)
    hop_limit: int = Field(3, ge=0, le=7)

    @field_validator("type")
    @classmethod
    def known_type(cls, v):
        if v in PLANNED_NODE_TYPES:
            raise ValueError(f"node type {v!r} is not implemented yet")
        if v not in IMPLEMENTED_NODE_TYPES:
            raise ValueError(f"unknown node type {v!r}. Known: {sorted(IMPLEMENTED_NODE_TYPES | PLANNED_NODE_TYPES)}")
        return v

    @model_validator(mode="after")
    def mesh_role(self):
        if self.type == "mesh_node":
            if self.role is None:
                raise ValueError("mesh_node needs a role")
            if self.role not in MESHTASTIC_ROLE.__members__:
                raise ValueError(f"unknown Meshtastic role {self.role!r}. Known: {list(MESHTASTIC_ROLE.__members__)}")
        return self


class Architecture(Strict):
    sensor: NodeSpec
    gateway: list[NodeSpec] = []  # nodes placed together at every gateway site
    responder: Optional[NodeSpec] = None  # None: responders are not radio nodes in this architecture


class Output(Strict):
    dir: Optional[str] = None  # default: out/<experiment name>
    nodes_csv: bool = True
    readings_csv: bool = True
    deliveries_csv: bool = True


class Experiment(Strict):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    seeds: list[int] = Field([44], min_length=1)
    simtime_s: float = Field(gt=0)
    phy: Phy = Phy()
    hardware: Optional[list[str]] = None  # default: hardware/components.yaml and hardware/devices.yaml
    deployment: Deployment
    traffic: Traffic
    architectures: dict[str, Architecture] = Field(min_length=1)
    sweep: dict[str, list] = {}
    output: Output = Output()

    @model_validator(mode="after")
    def groups_have_specs(self):
        d = self.deployment
        for name, arch in self.architectures.items():
            if d.gateway_sites.size > 0 and not arch.gateway:
                raise ValueError(f"architecture {name}: the deployment has gateway sites but 'gateway' lists no nodes")
        for path, values in self.sweep.items():
            if not values:
                raise ValueError(f"sweep {path}: needs at least one value")
        return self
