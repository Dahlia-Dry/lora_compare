"""Shared helpers used across the simulator: random node placement within
radio range of existing nodes, and distance calculation.
"""
import random
import os

import numpy as np

from lib.radio import phy
from lib.core.point import Point

def find_random_position(conf, node_configs) -> (float, float):
    """Given a simulation config and list of existing node configs/nodes, find a
    randomly chosen position for the next node such that it is within the
    simulation bounds, within radio range of at least one existing node, and
    not within the minimum distance of any existing node.

    Arguments:
    conf -- Config object defining simulation
    node_configs -- list of NodeConfig objects, or node objects, defining pre-existing nodes

    Returns:
    (x, y) - x and y coordinates of location for next node
    """
    foundMin = True
    foundMax = False
    tries = 0
    position = None
    while not (foundMin and foundMax):
        a = random.random()
        b = random.random()
        posx = a*conf.XSIZE+conf.OX-conf.XSIZE/2
        posy = b*conf.YSIZE+conf.OY-conf.YSIZE/2
        pos_candidate = Point(posx, posy, conf.HM)
        if len(node_configs) > 0:
            for n in node_configs:
                dist = n.position.euclidean_distance(pos_candidate)
                if dist < conf.MINDIST:
                    foundMin = False
                    break
                pathLoss = phy.estimate_path_loss(conf, dist, conf.FREQ)
                rssi = conf.PTX + 2*conf.GL - pathLoss
                # At least one node should be able to reach it
                if rssi >= conf.current_preset["sensitivity"]:
                    foundMax = True
            if foundMin and foundMax:
                position = pos_candidate
        else:
            position = pos_candidate
            foundMin = True
            foundMax = True
        tries += 1
        if tries > 1000:
            print('Could not find a location to place the node. Try increasing XSIZE/YSIZE or decreasing MINDIST.')
            break
    return max(-conf.XSIZE/2, position.x), max(-conf.YSIZE/2, position.y)

# TODO: once lib/mesh/interactive no longer uses this, we can remove this and put all distance calculation in Point
def calc_dist(x0, x1, y0, y1, z0=0, z1=0):
    return np.sqrt(((abs(x0-x1))**2)+((abs(y0-y1))**2)+((abs(z0-z1)**2)))
