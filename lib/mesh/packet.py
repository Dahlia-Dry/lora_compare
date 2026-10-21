"""Packet and message models. `MeshPacket` is a Meshtastic packet on the air
(one `Transmission`), and `MeshMessage` represents an application-level
message that may be carried by several packets.
"""
from lib.radio.transmission import Transmission

NODENUM_BROADCAST = 0xFFFFFFFF


class MeshPacket(Transmission):
    def __init__(self, conf, medium, origTxNodeId, destId, txNodeId, plen, seq, genTime, wantAck, isAck, requestId, now):
        """Create a new packet and calculate which nodes sense and receive it

        Arguments:
        conf -- simulation Config
        medium -- RadioMedium the packet is sent on
        origTxNodeId -- ID of originating node
        destId -- ID of destination node (specific node or broadcast)
        txNodeId -- ID of currently transmitting node
        plen -- packet length
        seq -- message sequence number
        genTime -- sim time of original packet generation (different from acks/rebroadcasts)
        wantAck -- this packet wants an ACK
        isAck -- this packet is an ACK packet
        requestId -- ID of packet requesting ACK (only valid for ACK packets)
        now -- current sim time when called, always `env.now`
        """
        self.origTxNodeId = origTxNodeId
        self.destId = destId
        self.wantAck = wantAck
        self.isAck = isAck
        self.seq = seq
        self.requestId = requestId
        self.genTime = genTime

        preset = conf.current_preset
        super().__init__(conf, medium, txNodeId, preset["sf"], preset["cr"], preset["bw"], conf.FREQ,
                         conf.PTX, plen, conf.HEADERLENGTH, now)

        # Routing
        self.retransmissions = self.conf.maxRetransmission
        self.ackReceived = False
        self.hopLimit = self.tx_node.hopLimit


class MeshMessage:
    def __init__(self, origTxNodeId, destId, genTime, seq):
        self.origTxNodeId = origTxNodeId
        self.destId = destId
        self.genTime = genTime
        self.seq = seq
        self.endTime = 0
