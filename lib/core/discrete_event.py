"""Low-level discrete-event stuff: `BroadcastPipe`, which delivers packets
to every node's receive store after their time on air.
"""
import simpy


class BroadcastPipe:
    def __init__(self, env, capacity=simpy.core.Infinity):
        self.env = env
        self.capacity = capacity
        self.pipes = []

    def latency(self, packet):
        # wait time that packet is on the air
        yield self.env.timeout(packet.timeOnAir)
        if not self.pipes:
            raise RuntimeError('There are no output pipes.')
        events = [store.put(packet) for store in self.pipes]
        return self.env.all_of(events)

    def put(self, packet):
        self.env.process(self.latency(packet))
        # this mimics start of reception
        if not self.pipes:
            raise RuntimeError('There are no output pipes.')
        events = [store.put(packet) for store in self.pipes]
        return self.env.all_of(events)

    def get_output_conn(self):
        pipe = simpy.Store(self.env, capacity=self.capacity)
        self.pipes.append(pipe)
        return pipe
