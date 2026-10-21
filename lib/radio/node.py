"""Protocol-agnostic radio node. `RadioNodeConfig` holds the radio part of a
node's configuration (position, power, frequency, antenna gain), and
`RadioNode` holds the radio state shared by every node type: half-duplex
transmit/receive bookkeeping, airtime accounting and random mobility.
Protocol node types subclass both and implement `on_receive`.
"""
import logging
import math
import random

from lib.core.point import Point
from lib.energy.meter import EnergyMeter
from lib.radio.phy import estimate_path_loss

logger = logging.getLogger(__name__)


class RadioNodeConfig:
    """Radio configuration of a node."""
    def __init__(self, node_id: int, position: Point, tx_power: int, freq: float, antenna_gain: float = 0, device=None,
                 speed_m_per_min: float = None, gps: bool = False):
        """Arguments:
        node_id -- unique integer id of node (used as list index)
        position -- beginning Point(x, y, z) location of node
        tx_power -- transmit power in dBm
        freq -- frequency in Hz
        antenna_gain -- antenna gain in dBi. Default 0
        device -- hardware Device for energy accounting. Default None: no energy metering
        speed_m_per_min -- random-walk step size; 0 for a static node. Default None: decided
                           at random from Config.APPROX_RATIO_NODES_MOVING (legacy behaviour)
        gps -- whether a moving node has GPS. Only used when speed_m_per_min is set
        """
        self.node_id = node_id
        self.position = position.copy()  # make sure we keep our own point
        self.tx_power = tx_power
        self.freq = freq
        self.antenna_gain = antenna_gain
        self.device = device
        self.speed_m_per_min = speed_m_per_min
        self.gps = gps

    def compute_rssi_and_pathloss_to(self, rx_nodeconf, conf) -> (float, float):
        """Compute RSSI and pathloss from this node config as the transmitting node
        to a receiving node, using a given config for various physical parameters.

        Arguments:
        rx_nodeconf -- config of node we are transmitting to
        conf -- Config object specifying various physical parameters

        Returns:
        (rssi, pathloss) -- rssi at rx_nodeconf, and pathloss along the path
        """
        if self.node_id == rx_nodeconf.node_id:
            raise ValueError(f"Calculating rssi/pathloss between identical nodes is invalid. Node ID {self.node_id}")

        dist = self.position.euclidean_distance(rx_nodeconf.position)
        pl = estimate_path_loss(conf, dist, self.freq, self.position.z, rx_nodeconf.position.z)
        rssi = self.tx_power + self.antenna_gain + rx_nodeconf.antenna_gain - pl

        return (rssi, pl)


class RadioNode:
    """Radio state and processes shared by all node types. Subclasses start
    their own processes, call `start_receiving` and `maybe_start_mobility`,
    and implement `on_receive`.
    """
    def __init__(self, conf, sim_state, node_config: RadioNodeConfig):
        """Arguments:
        conf -- Config object of various sim parameters
        sim_state -- SimulationState holding the env and the radio medium
        node_config -- initial configuration of node
        """
        self.conf = conf
        self.node_conf = node_config
        self.nodeid = node_config.node_id
        self.position = node_config.position  # moves together with node_conf.position
        self.antennaGain = node_config.antenna_gain

        self.env = sim_state.env
        self.medium = sim_state.medium
        self.nodes = self.medium.nodes
        self.packetsAtN = self.medium.packetsAtN

        # per-node RNG seed: the node ID, salted with the run's seed when Config.NODE_RNG_SEED is set
        self.rng_seed = self.nodeid if conf.NODE_RNG_SEED is None else f"{conf.NODE_RNG_SEED}:{self.nodeid}"
        self.moveRng = random.Random(self.rng_seed)

        self.nrPacketsSent = 0
        self.isReceiving = []
        self.isTransmitting = False
        self.txAirUtilization = 0
        self.airUtilization = 0
        self.isMoving = False
        self.gpsEnabled = False

        # radio state whenever the node is not transmitting
        self.idle_radio_state = "rx"
        device = getattr(node_config, "device", None)
        self.meter = EnergyMeter(device, self.env.now, self.idle_radio_state) if device is not None else None

    def set_radio_state(self, state: str, dbm: float = None):
        """Record a radio state change for energy accounting."""
        if self.meter is not None:
            self.meter.set_radio_state(state, self.env.now, dbm)

    def sensitivity(self, tx) -> float:
        """Weakest RSSI (dBm) at which this node can receive `tx`."""
        return self.conf.current_preset["sensitivity"]

    def cad_threshold(self, tx) -> float:
        """Weakest RSSI (dBm) at which this node's CAD detects `tx`."""
        return self.conf.current_preset["cad_threshold"]

    def start_receiving(self):
        """Connect this node's receiver to the medium."""
        self.env.process(self.receive(self.medium.bc_pipe.get_output_conn()))

    def radio_transmit(self, tx):
        """Process: send a transmission now and stay busy until it ends."""
        self.nrPacketsSent += 1
        self.medium.transmit(tx)
        self.txAirUtilization += tx.timeOnAir
        self.airUtilization += tx.timeOnAir
        self.isTransmitting = True
        self.set_radio_state("tx", tx.txpow)
        yield self.env.timeout(tx.timeOnAir)
        self.isTransmitting = False
        self.set_radio_state(self.idle_radio_state)

    def receive(self, in_pipe):
        """Process: track the start and end of every transmission on the medium,
        and pass each one received intact to `on_receive`."""
        while True:
            p = yield in_pipe.get()

            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} fetches transmission {p.unique_packet_seq} from {p.txNodeId}: sensed: {p.sensedByN[self.nodeid]} collided: {p.collidedAtN[self.nodeid]} on air: {p.onAirToN[self.nodeid]}")
            if p.sensedByN[self.nodeid] and p.onAirToN[self.nodeid]:  # start of reception
                if p.collidedAtN[self.nodeid]:
                    # this transmission collided, so we can sense it but not decode it.
                    # Mark it as no-longer on air and leave further processing to
                    # the 'end of transmission' branch
                    p.onAirToN[self.nodeid] = False
                elif not self.isTransmitting:
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} started receiving transmission {p.unique_packet_seq} from {p.txNodeId}")
                    p.onAirToN[self.nodeid] = False
                    self.isReceiving.append(True)
                else:  # if you were currently transmitting, you could not have sensed it
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} was transmitting, so could not receive transmission {p.unique_packet_seq}")
                    p.sensedByN[self.nodeid] = False
                    p.onAirToN[self.nodeid] = False
            elif p.sensedByN[self.nodeid]:  # end of reception
                try:
                    self.isReceiving[self.isReceiving.index(True)] = False
                except Exception:
                    pass
                self.airUtilization += p.timeOnAir
                # begin receiving fine, but a collision begins before we finish receiving.
                if p.collidedAtN[self.nodeid]:
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} could not decode transmission {p.unique_packet_seq}.")
                    continue
                p.receivedAtN[self.nodeid] = True
                self.on_receive(p)

    def on_receive(self, p):
        """Handle a transmission received intact. Implemented by each node type."""
        raise NotImplementedError

    def maybe_start_mobility(self):
        """Start moving if the node config says so. Without an explicit speed in
        the config, decide at random (from this node's RNG) whether it moves and has GPS."""
        speed = getattr(self.node_conf, "speed_m_per_min", None)
        if speed is not None:
            if speed > 0:
                self.isMoving = True
                self.gpsEnabled = self.node_conf.gps
                self.movementStepSize = speed
                self.env.process(self.move_node())
            return

        if self.conf.MOVEMENT_ENABLED and self.moveRng.random() <= self.conf.APPROX_RATIO_NODES_MOVING:
            self.isMoving = True
            if self.moveRng.random() <= self.conf.APPROX_RATIO_OF_NODES_MOVING_W_GPS_ENABLED:
                self.gpsEnabled = True

            possibleSpeeds = [
                self.conf.WALKING_METERS_PER_MIN,  # e.g.,  96 m/min
                self.conf.BIKING_METERS_PER_MIN,   # e.g., 390 m/min
                self.conf.DRIVING_METERS_PER_MIN   # e.g., 1500 m/min
            ]
            self.movementStepSize = self.moveRng.choice(possibleSpeeds)

            self.env.process(self.move_node())

    def move_node(self):
        """Process: random walk within the simulation area."""
        while True:
            # Pick a random direction and distance
            angle = 2 * math.pi * self.moveRng.random()
            distance = self.movementStepSize * self.moveRng.random()

            dx = distance * math.cos(angle)
            dy = distance * math.sin(angle)

            leftBound = self.conf.OX - self.conf.XSIZE / 2
            rightBound = self.conf.OX + self.conf.XSIZE / 2
            bottomBound = self.conf.OY - self.conf.YSIZE / 2
            topBound = self.conf.OY + self.conf.YSIZE / 2

            new_x = min(max(self.position.x + dx, leftBound), rightBound)
            new_y = min(max(self.position.y + dy, bottomBound), topBound)

            self.position.update_xy(new_x, new_y)
            self.medium.update_connectivity(self)
            self.on_moved()

            nextMove = self.next_move_delay()
            if nextMove >= 0:
                yield self.env.timeout(nextMove)
            else:
                break

    def on_moved(self):
        """Hook called after every move. Default: nothing."""
        pass

    def next_move_delay(self) -> float:
        """Time until the next move in ms, or -1 to stop moving. Default:
        exponential with a one-minute mean, stopping before the sim ends."""
        nextMove = self.moveRng.expovariate(1.0 / self.conf.ONE_MIN_INTERVAL)
        if self.env.now + nextMove < self.conf.SIMTIME:
            return nextMove
        return -1
