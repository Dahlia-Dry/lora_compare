"""The shared radio medium. `RadioMedium` decides who hears what: it caches
path loss between nodes, keeps the connectivity map used to skip unreachable
receivers, applies the asymmetric-link offsets, checks collisions when a
transmission starts, delivers transmissions to every node's receiver, and
answers channel activity detection (CAD) queries.
"""
import itertools
import logging
import random

from lib.core.discrete_event import BroadcastPipe
from lib.radio.phy import check_collision, estimate_path_loss, get_current_slot_time

logger = logging.getLogger(__name__)


class RadioMedium:
    def __init__(self, conf, env):
        """Create an empty medium. Nodes add themselves to `nodes`, indexed by node ID.

        Arguments:
        conf -- simulation Config
        env -- SimPy Environment
        """
        self.conf = conf
        self.env = env
        self.nodes = []
        self.bc_pipe = BroadcastPipe(env)
        self.packetsAtN = [[] for _ in range(conf.NR_NODES)]  # transmissions each node could receive, for collision checks
        self.connectivity_map = {}  # node_id -> set of node IDs which can (with margin) hear it
        self.baseline_pathloss_matrix = [[None for _ in range(conf.NR_NODES)] for _ in range(conf.NR_NODES)]
        self.asym_rng = random.Random(conf.SEED)
        self._transmission_ids = itertools.count(1)
        self._recent = []  # started transmissions that may still be on air, for CAD

    def next_transmission_id(self) -> int:
        return next(self._transmission_ids)

    def connectivity_threshold(self) -> float:
        """Weakest RSSI any receiver on the medium can sense, used for the connectivity map."""
        return self.conf.current_preset['sensitivity']

    def initialize(self, node_configs):
        """Compute the initial connectivity map and baseline path loss matrix
        from the node configs, before any nodes exist.

        Returns:
        (totalPairs, totalLinks, noLinks) -- link counts without the connectivity margin
        """
        totalPairs = totalLinks = noLinks = 0
        threshold = self.connectivity_threshold()
        for tx_node in node_configs:
            reachable_node_set = set()
            for rx_node in node_configs:
                if tx_node.node_id == rx_node.node_id:
                    continue

                totalPairs += 1
                (rssi, pl) = tx_node.compute_rssi_and_pathloss_to(rx_node, self.conf)

                # compare with extra margin (set based on 10-node standard test)
                if rssi + self.conf.CONNECTIVITY_MAP_RSSI_MARGIN > threshold:
                    reachable_node_set.add(rx_node.node_id)

                # compute total/no links without margin
                if rssi >= threshold:
                    totalLinks += 1
                else:
                    noLinks += 1

                # cache path loss (it is symmetric, and static until one of the nodes moves)
                self.baseline_pathloss_matrix[tx_node.node_id][rx_node.node_id] = pl
                self.baseline_pathloss_matrix[rx_node.node_id][tx_node.node_id] = pl

            self.connectivity_map[tx_node.node_id] = reachable_node_set
        return totalPairs, totalLinks, noLinks

    def update_connectivity(self, node):
        """Recompute path loss and connectivity for a node that has moved."""
        if not self.conf.ENABLE_CONNECTIVITY_MAP:
            return
        threshold = self.connectivity_threshold()
        # may need to deepcopy if we put more complex things in here
        old_reachable_set = self.connectivity_map[node.nodeid].copy()
        new_reachable_set = set()
        for rx_node in self.nodes:
            if rx_node.nodeid == node.nodeid:
                continue

            (rssi, pl) = node.node_conf.compute_rssi_and_pathloss_to(rx_node.node_conf, self.conf)

            if rssi + self.conf.CONNECTIVITY_MAP_RSSI_MARGIN > threshold:
                new_reachable_set.add(rx_node.nodeid)

            self.baseline_pathloss_matrix[node.nodeid][rx_node.nodeid] = pl
            self.baseline_pathloss_matrix[rx_node.nodeid][node.nodeid] = pl

        lost_nodes = old_reachable_set.difference(new_reachable_set)
        gained_nodes = new_reachable_set.difference(old_reachable_set)

        logger.debug(f"{self.env.now:.3f} node {node.nodeid} moved. Connectivity change: -{len(lost_nodes)}, +{len(gained_nodes)}.")
        # TODO: -0, +0 case is very common. Skip what we can in this case.
        self.connectivity_map[node.nodeid] = new_reachable_set
        for node_id in gained_nodes:
            self.connectivity_map[node_id].add(node.nodeid)
        for node_id in lost_nodes:
            self.connectivity_map[node_id].discard(node.nodeid)

    def compute_reception(self, tx):
        """Fill in the per-receiver path loss, RSSI, sensed and detected arrays of a transmission."""
        conf = self.conf
        for rx_node in self.nodes:
            if rx_node.nodeid == tx.txNodeId:
                continue

            # reduce calculations just to plausibly reachable nodes. This is initialized
            # before sim start, and updated whenever a moving node's position is updated,
            # so is always an accurate map of what nodes could (with extra margin) even
            # sense each other.
            if conf.ENABLE_CONNECTIVITY_MAP and rx_node.nodeid not in self.connectivity_map[tx.txNodeId]:
                logger.debug(f"{tx.now:.3f} skipping {tx.txNodeId} -> {rx_node.nodeid} computation. connectivity map: {self.connectivity_map[tx.txNodeId]}")
                if conf.MODEL_ASYMMETRIC_LINKS:
                    # Each tx -> rx computation we skip gets the asym_rng one call out
                    # of sync between simulations with and without the connectivity
                    # map optimization, which would make results depend on the
                    # optimization being on/off.
                    #
                    # Keep things balanced by 'unnecessarily' calling the rng here.
                    self.asym_rng.gauss(0, conf.MODEL_ASYMMETRIC_LINKS_STDDEV)
                continue

            if conf.ENABLE_CONNECTIVITY_MAP:
                # look up baseline path loss from matrix, since we've already computed it.
                baseline_pathloss = self.baseline_pathloss_matrix[tx.txNodeId][rx_node.nodeid]
            else:
                dist_3d = tx.tx_node.position.euclidean_distance(rx_node.position)
                baseline_pathloss = estimate_path_loss(conf, dist_3d, tx.freq, tx.tx_node.position.z, rx_node.position.z)

            if conf.MODEL_ASYMMETRIC_LINKS:
                offset = self.asym_rng.gauss(0, conf.MODEL_ASYMMETRIC_LINKS_STDDEV)
                logger.debug(f"{tx.now:.3f} transmission {tx.unique_packet_seq} has asym offset {offset} dB")
                if abs(offset) > conf.CONNECTIVITY_MAP_RSSI_MARGIN:
                    logger.warning(f"{tx.now:.3f} transmission {tx.unique_packet_seq} has asymmetric RSSI offset {offset} which is outside margin. This will lead to inconsistent results with the connectivity map optimization.")
            else:
                offset = 0
            tx.LplAtN[rx_node.nodeid] = baseline_pathloss + offset
            tx.rssiAtN[rx_node.nodeid] = tx.txpow + tx.tx_node.antennaGain + rx_node.antennaGain - tx.LplAtN[rx_node.nodeid]
            if tx.rssiAtN[rx_node.nodeid] >= rx_node.sensitivity(tx):
                tx.sensedByN[rx_node.nodeid] = True
            if tx.rssiAtN[rx_node.nodeid] >= rx_node.cad_threshold(tx):
                tx.detectedByN[rx_node.nodeid] = True

    def transmit(self, tx):
        """Put a transmission on the air now: mark collisions at every receiver
        that senses it, and deliver it to every node's receiver."""
        for rx_node in self.nodes:
            if tx.sensedByN[rx_node.nodeid]:
                if check_collision(self.conf, self.env, tx, rx_node.nodeid, self.packetsAtN) == 0:
                    self.packetsAtN[rx_node.nodeid].append(tx)
        # transmission's collidedAtN field is now computed/valid
        tx.startTime = self.env.now
        tx.endTime = self.env.now + tx.timeOnAir
        self._recent.append(tx)
        self.bc_pipe.put(tx)  # queue for nodes to receive the transmission

    def is_channel_active(self, node) -> bool:
        """Channel activity detection at a node: True if outside interference is
        present or a transmission the node can detect is on the air."""
        if random.randrange(10) <= self.conf.INTERFERENCE_LEVEL * 10:
            return True
        now = self.env.now
        self._recent = [p for p in self._recent if p.endTime >= now]
        slot_time = get_current_slot_time(self.conf)
        for p in self._recent:
            if p.detectedByN[node.nodeid]:
                # You will miss detecting a packet if it has just started before you could do CAD
                if p.startTime + slot_time <= now <= p.endTime:
                    return True
        return False
