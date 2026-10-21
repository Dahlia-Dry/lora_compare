#!/usr/bin/env python3
import argparse
import logging
import math
import os
import random

import yaml

from lib.core.config import CONFIG
from lib.mesh.node import NodeConfig, default_generate_node_list

conf = CONFIG
logger = logging.getLogger(__name__)
MIN_TIME_OVERRIDE_SECONDS = 0.01
CLI_DEFAULT_ATTR = "_lora_mesh_cli_defaults"


def configure_logging():
    """Apply CLI logging defaults without changing logging during module import."""
    logging.basicConfig(level=logging.INFO) # default log level


def get_cli_defaults(conf):
    """Remember the caller's initial CLI defaults across reusable parse calls."""
    if not hasattr(conf, CLI_DEFAULT_ATTR):
        setattr(
            conf,
            CLI_DEFAULT_ATTR,
            {
                "SIMTIME": conf.SIMTIME,
                "PERIOD": conf.PERIOD,
                "GUI_ENABLED": conf.GUI_ENABLED,
                "PLOT": conf.PLOT,
            },
        )
    return getattr(conf, CLI_DEFAULT_ATTR)


def parse_params(conf, args=None) -> [NodeConfig]:
    """parses command-line arguments, alters global simulation config, and returns
    a list of node configurations, or a list of None.
    """

    # previous cli behavior:
    # loraMesh.py [nr_nodes [router_type]] | [--from-file [file_name]]
    # we'll replicate the intent with argparse, but more strictly, so flags like '--never--from-file' will no longer be accepted
    parser = argparse.ArgumentParser(
        description='run a single interactive or discrete Meshtastic network simulation'
        )

    # only allow one of --from-file optional, or nr_nodes positional exclusively
    group = parser.add_mutually_exclusive_group()
    group.add_argument('nr_nodes', nargs='?', type=int, help='Number of nodes to generate. If unspecified, do interactive simulation')
    group.add_argument('--from-file', nargs='?', const='nodeConfig.yaml', type=str, metavar='filename', help='Name of yaml file storing node config under "out/" directory. If unspecified, defaults to "nodeConfig.yaml".')

    # the earlier behavior of specifying `router_type` as an optional positional arg with `nr_nodes` is difficult to exactly
    # replicate with argparse, especially since nesting groups was an unintended feature and deprecated.
    # Just implement as an optional argument, and manually treat it as incompatible with `--from-file`
    parser.add_argument('--router-type', type=conf.ROUTER_TYPE, choices=conf.ROUTER_TYPE, help='Router type to use, taken from ROUTER_TYPE enum. Omit the leading "ROUTER_TYPE". Incompatible with --from-file')
    parser.add_argument('--simtime-seconds', type=float, help='Override simulation duration in seconds')
    parser.add_argument('--period-seconds', type=float, help='Override mean message-generation period in seconds')
    parser.add_argument('--no-gui', action='store_true', help='Run without Tk/Matplotlib graphing or schedule plotting')
    parser.add_argument('--disable-connectivity-map', action='store_true', help='disable the connectivity map optimization. May be faster for some scenarios with many moving nodes and/or a densely connected network.')
    parser.add_argument('-v', '--verbose', action='store_true', help='enable verbose/debug output')

    parsed_arguments = parser.parse_args(args)

    cli_defaults = get_cli_defaults(conf)
    simtime = cli_defaults["SIMTIME"]
    period = cli_defaults["PERIOD"]
    gui_enabled = cli_defaults["GUI_ENABLED"]
    plot_enabled = cli_defaults["PLOT"]

    if parsed_arguments.simtime_seconds is not None:
        if not math.isfinite(parsed_arguments.simtime_seconds) or parsed_arguments.simtime_seconds < MIN_TIME_OVERRIDE_SECONDS:
            parser.error(f"--simtime-seconds must be at least {MIN_TIME_OVERRIDE_SECONDS} seconds")
        simtime = int(parsed_arguments.simtime_seconds * conf.ONE_SECOND_INTERVAL)

    if parsed_arguments.period_seconds is not None:
        if not math.isfinite(parsed_arguments.period_seconds) or parsed_arguments.period_seconds < MIN_TIME_OVERRIDE_SECONDS:
            parser.error(f"--period-seconds must be at least {MIN_TIME_OVERRIDE_SECONDS} seconds")
        period = int(parsed_arguments.period_seconds * conf.ONE_SECOND_INTERVAL)

    if parsed_arguments.no_gui:
        # Headless CI and smoke runs should not pay Tk startup, per-node
        # plt.pause(), or the final interactive schedule plot. Keep this as an
        # explicit flag so historical visual CLI behavior remains unchanged.
        gui_enabled = False
        plot_enabled = False

    # enforce defaulting to True
    if parsed_arguments.disable_connectivity_map:
        conf.ENABLE_CONNECTIVITY_MAP = False
    else:
        conf.ENABLE_CONNECTIVITY_MAP = True

    if parsed_arguments.from_file is not None and parsed_arguments.router_type is not None:
        parser.error("Incompatible argument selection. --from-file and --router-type can not be used together")

    seeded_for_scenario = False
    if parsed_arguments.from_file is not None:
        with open(os.path.join("out", parsed_arguments.from_file), 'r', encoding="utf-8") as file:
            raw_config = yaml.load(file, Loader=yaml.FullLoader)
        config = [
            # transmit power and frequency not previously saved. Use defaults from Config.
            NodeConfig.from_gen_scenario_output(node_id, node_config, period, conf.PTX, conf.FREQ)
            for node_id, node_config in raw_config.items()
        ]
        nr_nodes = len(config)
    elif parsed_arguments.nr_nodes is not None:
        if parsed_arguments.nr_nodes < 2:
            parser.error(f"Need at least two nodes. You specified {parsed_arguments.nr_nodes}")
        nr_nodes = parsed_arguments.nr_nodes
        if parsed_arguments.router_type is not None:
            routerType = parsed_arguments.router_type
            conf.SELECTED_ROUTER_TYPE = routerType
            conf.update_router_dependencies()
        # Generated node positions come from the global RNG. Seed immediately
        # before that generation, after every parser-only rejection path above.
        conf.NR_NODES = nr_nodes
        conf.PERIOD = period
        random.seed(conf.SEED)
        seeded_for_scenario = True
        config = default_generate_node_list(conf)
    else:
        if not gui_enabled:
            parser.error("--no-gui requires nr_nodes or --from-file")
        from lib.gui import gen_scenario

        config_dict = gen_scenario(conf)
        config = [NodeConfig.from_gen_scenario_output(node_id, cfg, period, conf.PTX, conf.FREQ) for node_id, cfg in config_dict.items()]
        nr_nodes = len(config)

    if nr_nodes < 2:
        parser.error(f"Need at least two nodes. You specified {nr_nodes}")
    if not seeded_for_scenario:
        # Loaded and interactive scenarios do not need random state for node
        # placement, but the later MAC/PHY simulation does. Seed only after
        # successful scenario loading so rejected inputs leave caller RNG state
        # alone.
        random.seed(conf.SEED)

    conf.SIMTIME = simtime
    conf.PERIOD = period
    conf.GUI_ENABLED = gui_enabled
    conf.PLOT = plot_enabled
    conf.NR_NODES = nr_nodes

    if parsed_arguments.verbose:
        # Set this logger and lib.* to DEBUG only after the command line has
        # resolved into a usable scenario. Failed parser inputs should not leave
        # imported callers with noisier logging.
        logger.setLevel(logging.DEBUG)
        lib_logger = logging.getLogger('lib')
        lib_logger.setLevel(logging.DEBUG)
        print("verbose output enabled")

    print("Number of nodes:", conf.NR_NODES)
    print("Modem:", conf.MODEM_PRESET)
    print("Simulation time (s):", conf.SIMTIME/1000)
    print("Period (s):", conf.PERIOD/1000)
    print("Interference level:", conf.INTERFERENCE_LEVEL)
    return config


def run_simulation(conf, node_config):
    """Run one configured simulation and print the historical CLI summary."""
    # Keep the heavier simulation/GUI import out of module import. That makes
    # CLI parsing unit-testable and lets CI/tools import loraMesh without
    # starting Matplotlib/Tk plumbing as a side effect.
    from lib.core.sim import DiscreteEventSim

    conf.update_router_dependencies()
    if conf.GUI_ENABLED:
        from lib.gui import Graph

        graph = Graph(conf)
    else:
        graph = None

    # set up sim
    sim = DiscreteEventSim(conf, node_config, graph)

    # run sim
    print("\n====== START OF SIMULATION ======")
    sim.run_simulation()

    # collect, process & display results
    print("\n====== END OF SIMULATION ======")

    results = sim.get_results()

    packets = results["packets"]
    messageSeq = results["messageSeq"]
    messages = results["messages"]

    # collect second-order results from finalized results
    sent = results['sent']
    potentialReceivers = results['potentialReceivers']
    nrCollisions = results['nrCollisions']
    nrSensed = results['nrSensed']
    nrReceived = results['nrReceived']
    meanDelay = results['meanDelay']
    txAirUtilizationRate = results['txAirUtilizationRate']
    collisionRate = results['collisionRate']
    nodeReach = results['nodeReach']
    usefulness = results['usefulness']
    delayDropped = results['delayDropped']

    print("*******************************")
    print(f"\nRouter Type: {conf.SELECTED_ROUTER_TYPE}")
    print('Number of messages created:', messageSeq)
    print('Number of packets sent:', sent, 'to', potentialReceivers, 'potential receivers')
    print("Number of collisions:", nrCollisions)
    print("Number of packets sensed:", nrSensed)
    print("Number of packets received:", nrReceived)
    print('Delay average (ms):', round(meanDelay, 2))
    print('Average Tx air utilization:', round(txAirUtilizationRate * 100, 2), '%')
    print("Percentage of packets that collided:", round(collisionRate*100, 2))
    print("Average percentage of nodes reached:", round(nodeReach*100, 2))
    print("Percentage of received packets containing new message:", round(usefulness*100, 2))
    print("Number of packets dropped by delay/hop limit:", delayDropped)

    if conf.MODEL_ASYMMETRIC_LINKS:
        noLinkRate = results['noLinkRate']
        print("No links:", round(noLinkRate * 100, 2), '%')

    if conf.MOVEMENT_ENABLED:
        movingNodes = results['movingNodes']
        gpsEnabled = results['gpsEnabled']
        print("Number of moving nodes:", movingNodes)
        print("Number of moving nodes w/ GPS:", gpsEnabled)

    if graph is not None:
        graph.save()

    if conf.PLOT:
        from lib.gui import plot_schedule

        plot_schedule(conf, packets, messages)

    return results


def main(args=None):
    configure_logging()
    node_config = parse_params(conf, args)
    return run_simulation(conf, node_config)


if __name__ == "__main__":
    main()
