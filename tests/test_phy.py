import unittest

import lib.radio.phy

class TestPhy(unittest.TestCase):

    def test_path_loss_estimator(self):
        # make sure we reject invalid model selection integers
        from lib.core.config import CONFIG
        conf = CONFIG

        model = -1 # invalid model
        self.assertRaises(ValueError, lib.radio.phy.estimate_path_loss, conf, 50, 915, 3, 3, model)
        model = 7 # invalid model
        self.assertRaises(ValueError, lib.radio.phy.estimate_path_loss, conf, 50, 915, 3, 3, model)
        model = 10 # invalid model
        self.assertRaises(ValueError, lib.radio.phy.estimate_path_loss, conf, 50, 915, 3, 3, model)
        model = -10 # invalid model
        self.assertRaises(ValueError, lib.radio.phy.estimate_path_loss, conf, 50, 915, 3, 3, model)

        # TODO: hardcode some expected values for the calculations across different
        # models, to detect unintended changes. This also requires verifying
        # the calculations are correct.

    def test_rootFinder(self):
        # double-check we can find the roots of some polynomials
        message = "sanity-check Newton-Raphson root-finding implementation"
        tolerance = 0.0000001

        def poly1(x):
            ''' roots at x=-3, 0, 2.5 '''
            return (x+3)*(x-2.5)*x

        # should find -3
        res = lib.radio.phy.rootFinder(poly1, -3.5, tol=tolerance)
        diff = abs(res - -3)
        self.assertLess(diff, tolerance, message)

        # should find 0
        res = lib.radio.phy.rootFinder(poly1, -1, tol=tolerance)
        diff = abs(res - 0)
        self.assertLess(diff, tolerance, message)

        # should find 2.5
        res = lib.radio.phy.rootFinder(poly1, 3, tol=tolerance)
        diff = abs(res - 2.5)
        self.assertLess(diff, tolerance, message)


    def test_airtime(self):
        from types import SimpleNamespace
        conf = SimpleNamespace(NPREAM=8)

        # Semtech LoRa calculator: SF7, 125 kHz, CR 4/5, 8-symbol preamble,
        # explicit header, CRC on, 20 bytes -> 56.576 ms
        self.assertAlmostEqual(lib.radio.phy.airtime(conf, 7, 1, 20, 125e3, 0), 56.576, places=3)

        # the header length is added to the payload
        self.assertEqual(lib.radio.phy.airtime(conf, 9, 1, 10, 125e3, 10),
                         lib.radio.phy.airtime(conf, 9, 1, 20, 125e3, 0))

    def test_frequency_collision(self):
        from types import SimpleNamespace
        def tx(freq, bw=125e3):
            return SimpleNamespace(freq=freq, bw=bw)

        self.assertTrue(lib.radio.phy.frequency_collision(tx(868.1e6), tx(868.1e6)), "same channel")
        self.assertTrue(lib.radio.phy.frequency_collision(tx(868.1e6), tx(868.12e6)), "20 kHz apart at 125 kHz")
        self.assertFalse(lib.radio.phy.frequency_collision(tx(868.1e6), tx(868.3e6)), "adjacent EU868 channels")
        self.assertTrue(lib.radio.phy.frequency_collision(tx(869.5e6, 250e3), tx(869.55e6)), "50 kHz apart, one at 250 kHz")
        self.assertTrue(lib.radio.phy.frequency_collision(tx(869.5e6), tx(869.6e6, 500e3)), "100 kHz apart, one at 500 kHz")
        self.assertFalse(lib.radio.phy.frequency_collision(tx(868.5e6), tx(869.525e6, 250e3)), "LoRaWAN vs Meshtastic EU868")

    def test_timing_collision_preamble_window(self):
        from types import SimpleNamespace
        conf = SimpleNamespace(NPREAM=16, LEGACY_COLLISION_TIMING=False)
        env = SimpleNamespace(now=1000.0)
        # SF11 at 250 kHz: 8.192 ms symbols, so the new packet locks on after
        # (16 - 5) symbols = 90.1 ms
        new = SimpleNamespace(sf=11, bw=250e3)
        ends_within_window = SimpleNamespace(endTime=1050.0)
        ends_after_window = SimpleNamespace(endTime=1100.0)

        self.assertFalse(lib.radio.phy.timing_collision(conf, env, new, ends_within_window),
                         "old packet ends before the new one's preamble locks")
        self.assertTrue(lib.radio.phy.timing_collision(conf, env, new, ends_after_window))

        conf.LEGACY_COLLISION_TIMING = True
        self.assertTrue(lib.radio.phy.timing_collision(conf, env, new, ends_within_window),
                        "legacy timing treats the window as ~0.09 ms")


if __name__ == '__main__':
    unittest.main()
