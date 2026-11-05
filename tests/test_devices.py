"""Offline tests (no hardware): python3 -m unittest discover tests"""
import unittest

from logibuntu import devices as dv
from logibuntu import softeq


class FakeDev:
    def __init__(self, replies):
        self.replies = replies

    def call(self, feature, func, params=b""):
        return self.replies[(feature, func)]


class Tests(unittest.TestCase):
    def test_crc_ccitt(self):
        self.assertEqual(dv.crc16(b"123456789"), 0x29B1)

    def test_voltage_curve(self):
        self.assertEqual(dv.voltage_to_percent(4300), 100)
        self.assertEqual(dv.voltage_to_percent(3000), 0)
        self.assertEqual(dv.voltage_to_percent(3925), 74)

    def test_dpi_range_from_g305_reply(self):
        m = object.__new__(dv.Mouse)
        m.dev = FakeDev({(dv.DPI, 1): bytes.fromhex("0000c8e0322ee0000000000000000000")})
        self.assertEqual(m.dpi_range(), (200, 12000, 50))

    def test_set_profile_roundtrip(self):
        p = bytearray(b"\xff" * 255)
        buttons = [dv.ACTIONS["Voltar"], dv.ACTIONS["Play/Pause"]]
        dv.set_profile(p, 2, [400, 0, 1600], 1, buttons)
        self.assertEqual(dv.profile_rate(p), 2)
        self.assertEqual(dv.profile_dpis(p), [400, 1600, 0, 0, 0])
        self.assertEqual(dv.profile_default_dpi(p), 1)
        self.assertEqual(dv.action_name(dv.profile_button(p, 1)), "Play/Pause")

    def test_macro_roundtrip_and_opcodes(self):
        ops = dv.encode_macro("Ok\n")
        self.assertEqual(ops[:3], bytes([0x43, dv.SHIFT, 0x12]))  # press Shift+o
        self.assertEqual(ops[-1], 0xFF)
        self.assertEqual(dv.decode_macro(ops), "Ok\n")
        with self.assertRaises(ValueError):
            dv.encode_macro("ç")

    def test_softeq_render(self):
        conf = softeq.render("alsa_output.x", "G533", [32, 64, 125], [3, 0, -12])
        self.assertIn('# gains: [3, 0, -12]', conf)
        self.assertIn('"Freq" = 125.0', conf)
        self.assertIn('{ output = "eq_band_2:Out" input = "eq_band_3:In" }', conf)
        self.assertNotIn("eq_band_4", conf)
        self.assertIn('target.object = "alsa_output.x"', conf)


if __name__ == "__main__":
    unittest.main()
