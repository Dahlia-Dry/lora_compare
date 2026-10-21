# Experiments

An experiment is a YAML file that describes one deployment and the network architectures to simulate on it. `runExperiment.py` runs every combination of architecture, sweep value and seed, and writes the results as CSV files.

```sh
python3 runExperiment.py experiments/mesh_b.yaml --dry-run   # check the file and list the runs
python3 runExperiment.py experiments/mesh_b.yaml --jobs 4    # run them, 4 in parallel
python3 runExperiment.py experiments/mesh_b.yaml --arch B    # only architecture B
```

`--gui` shows the node placement graph while a run is simulated. It needs an experiment with exactly one run: one seed, no sweep, and one architecture (use `--arch`).

Node placement depends only on the deployment and the seed. Every architecture in a file is therefore simulated on the same sensor, gateway site and responder positions.

## File format

Unknown keys are errors, so a typo is reported instead of being silently ignored. See `experiments/mesh_b.yaml` for a complete example.

```yaml
name: mesh_b                  # letters, digits, "_", "-", "."; names the default output folder
seeds: [1, 2, 3]              # one run per seed. Default [44]
simtime_s: 3600               # simulated time per run

phy:                          # all optional
  region: EU_868              # region from lib/core/config.py. Default EU_868
  modem_preset: LONG_FAST     # Meshtastic modem preset. Default LONG_FAST
  frequency_slot: 0           # 0-based slot within the region; the frequency is the slot's centre. Default 0
  pathloss_model: 5           # 0 log-distance, 1-4 Okumura-Hata, 5-6 3GPP. Default 5
  asymmetric_links: {enabled: true, stddev_db: 2}
  legacy_collision_timing: false   # true reproduces upstream Meshtasticator's preamble timing bug

hardware: [hardware/components.yaml, hardware/devices.yaml]   # optional; these are the defaults

deployment:
  area_m: [6000, 6000]        # width and height, centred on (0, 0)
  min_dist_m: 10              # minimum distance between randomly placed nodes. Default 10
  sensors:       {count: 30}                          # random positions...
  gateway_sites: {positions: [[0, 0], [-1800, 1200]]} # ...or explicit ones
  responders:    {count: 3, mobility: walking, gps: false}

traffic:
  reading_period_s: 900       # mean time between readings per sensor (exponential)
  payload_bytes: 12           # reading size. Default 12

architectures:                # one or more; the names are free
  B:
    sensor:    {type: mesh_node, role: SENSOR, device: rak4631}
    gateway:   [{type: mesh_node, role: ROUTER, device: rak4631, gain_dbi: 3, height_m: 2}]
    responder: {type: mesh_node, role: CLIENT, device: t_echo}

sweep:                        # optional: dotted paths into this file, and the values to try
  deployment.sensors.count: [10, 30]
  traffic.reading_period_s: [300, 900]

output:                       # optional
  dir: out/mesh_b             # default out/<name>
  nodes_csv: true
  deliveries_csv: true
```

### Deployment groups

`sensors`, `gateway_sites` and `responders` each take either `count` (placed uniformly at random) or `positions` (a list of `[x, y]` in metres). Random positions keep at least `min_dist_m` from every node placed before them. Sensors are placed first, then gateway sites, then responders.

`mobility` is `static` (the default), `walking`, `biking` or `driving`. A moving node does a random walk with the step size from `lib/core/config.py`. With `gps: true`, a moving Meshtastic node also broadcasts its position after moving far enough. Gateway sites cannot move.

### Architectures

`deployment` says *where* nodes are; each architecture says *what* is placed there:

- `sensor`: one node at every sensor position. Only sensors generate readings.
- `gateway`: a list of nodes placed together at every gateway site (one mast). LoRaWAN lists one gateway, Meshtastic one router, and the hybrid will list two (concentrator and co-host).
- `responder`: one node at every responder position. Responders log every reading they receive. Set `responder: null` when responders are not radio nodes in that architecture (for example, the cloud application in LoRaWAN).

A node spec has these fields:

| Field | Meaning |
|---|---|
| `type` | Node type. Implemented: `mesh_node`. Planned: `lorawan_sensor`, `lorawan_gateway`, `hybrid_concentrator`, `hybrid_cohost` |
| `role` | Meshtastic role for `mesh_node` (`CLIENT`, `CLIENT_MUTE`, `ROUTER`, `REPEATER`, `SENSOR`, ...) |
| `device` | Device from the hardware files. Gives energy accounting and default `tx_dbm`, `gain_dbi`, `height_m` |
| `tx_dbm`, `gain_dbi`, `height_m` | Override the device values. Without a device: region power limit, 0 dBi, 1 m |
| `hop_limit` | Meshtastic hop limit. Default 3 |

Transmit power is capped at the region's power limit, as the firmware does.

### Sweeps

Each `sweep` key is a dotted path through mappings in the file, for example `deployment.sensors.count` or `architectures.B.sensor.hop_limit`. Paths cannot index into lists, so a value inside `gateway: [...]` cannot be swept. The runs are every combination of the sweep values, times every architecture, times every seed. `name`, `seeds`, `sweep` and `output` cannot be swept.

## Output

`runExperiment.py` writes to the output folder:

- **`runs.csv`**: one row per run, with the following columns.
  - Identification: `run_id`, `architecture`, `seed`, then one column per sweep path.
  - Node counts: `sensors`, `gateway_nodes`, `responders`.
  - Delivery: `readings` (the number generated), `delivery_ratio_mean` and `delivery_ratio_min` (readings received at each responder, divided by readings generated, averaged over responders and at the worst responder), and `delivery_ratio_any` (received by at least one responder).
  - Latency: `latency_mean_s`, `latency_p50_s`, `latency_p95_s`, from generation to first reception at a responder.
  - Radio: `packets_sent`, `collisions`, `collision_rate`, `tx_airtime_s`.
  - Energy: `energy_total_J`, `energy_sensors_J`, `energy_gateways_J`, `energy_responders_J`, `energy_per_delivered_J` (total energy divided by readings delivered to any responder), and `energy_missing`.
- **`nodes.csv`**: one row per node per run, with its group (`sensor`, `gateway`, `responder`), gateway site index, role, device, position, power, packets and airtime sent, readings delivered (responders), and energy in total and per component.
- **`deliveries.csv`**: one row per reading received at a responder: `seq`, `sensor`, `responder`, generation and delivery time, and latency.
- **`experiment.yaml`**: a copy of the experiment file.

Energy is `NaN` while values in `hardware/components.yaml` are `null`. `energy_missing` lists which ones.

## Randomness

Each run's seed sets the following:
- **Placement:** where nodes are placed.
- **Link offsets:** the asymmetric link offsets.
- **Node timing:** each node's own RNG, which decides message timing and movement.
- **Global RNG:** used for MAC delays and interference.

A run is reproducible from its seed, and running with `--jobs` gives the same results as running serially.
