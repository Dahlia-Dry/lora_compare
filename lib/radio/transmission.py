"""A single LoRa transmission on the shared medium: its radio parameters and,
for every node in the simulation, whether that node senses, detects (CAD),
receives or loses it to a collision. Protocol-specific packet types
(Meshtastic packets, LoRaWAN frames) subclass `Transmission`.
"""
import logging

from lib.radio.phy import airtime

logger = logging.getLogger(__name__)


class Transmission:
    def __init__(self, conf, medium, txNodeId, sf, cr, bw, freq, txpow, payload_len, header_len, now):
        """Create a transmission and compute how it is received at every node.

        Arguments:
        conf -- simulation Config
        medium -- RadioMedium the transmission is sent on
        txNodeId -- ID of the transmitting node
        sf, cr, bw -- spreading factor, coding rate (4/(4+cr)) and bandwidth in Hz
        freq -- centre frequency in Hz
        txpow -- transmit power in dBm
        payload_len -- payload length in bytes, excluding the protocol header
        header_len -- protocol header length in bytes, added for airtime
        now -- current sim time when called, always `env.now`
        """
        self.unique_packet_seq = medium.next_transmission_id()
        self.conf = conf
        self.txNodeId = txNodeId
        self.now = now
        self.txpow = txpow
        self.sf = sf
        self.cr = cr
        self.bw = bw
        self.freq = freq

        nr_nodes = conf.NR_NODES
        self.LplAtN = [0 for _ in range(nr_nodes)]
        self.rssiAtN = [0 for _ in range(nr_nodes)]
        self.sensedByN = [False for _ in range(nr_nodes)]  # nodes which may possibly sense this transmission
        self.detectedByN = [False for _ in range(nr_nodes)]  # nodes whose CAD can detect this transmission
        self.collidedAtN = [False for _ in range(nr_nodes)]
        self.receivedAtN = [False for _ in range(nr_nodes)]
        self.onAirToN = [True for _ in range(nr_nodes)]

        self.tx_node = next(n for n in medium.nodes if n.nodeid == self.txNodeId)

        logger.debug(f"{self.now:.3f} Transmission {self.unique_packet_seq} created by node {self.txNodeId}")
        medium.compute_reception(self)

        self.packetLen = payload_len
        self.timeOnAir = airtime(conf, self.sf, self.cr, self.packetLen, self.bw, header_len)
        self.startTime = 0
        self.endTime = 0
