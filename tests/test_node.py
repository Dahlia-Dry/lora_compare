import unittest

import lib.mesh.node

class TestNodeConf(unittest.TestCase):
    def test_reject_rssi_and_pathloss_between_identical_nodes(self):

        from lib.core.config import CONFIG
        from lib.core.point import Point
        conf = CONFIG

        # reasonable values
        nodeconf = lib.mesh.node.NodeConfig(0, Point(0, 0, 0), 1, 30, 902e6)

        with self.assertRaises(ValueError, msg="cannot compute rssi/pathloss between the same nodes (by id)"):
            nodeconf.compute_rssi_and_pathloss_to(nodeconf, conf)
