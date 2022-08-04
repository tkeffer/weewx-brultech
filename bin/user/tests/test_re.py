import unittest

from brultech import energy2_re, denergy2_re, power_re


class RETest(unittest.TestCase):

    def test_energy2(self):
        self.assertTrue(energy2_re.match("ch2_a_energy2"))
        self.assertTrue(energy2_re.match("ch2_p_energy2"))
        self.assertTrue(energy2_re.match("ch12_a_energy2"))
        self.assertTrue(energy2_re.match("ch12_p_energy2"))
        self.assertFalse(energy2_re.match("ch2_ad_energy2"))
        self.assertFalse(energy2_re.match("ch2_pd_energy2"))

    def test_denergy2(self):
        self.assertTrue(denergy2_re.match("ch2_ad_energy2"))
        self.assertTrue(denergy2_re.match("ch2_pd_energy2"))
        self.assertTrue(denergy2_re.match("ch12_ad_energy2"))
        self.assertTrue(denergy2_re.match("ch12_pd_energy2"))
        self.assertFalse(denergy2_re.match("ch2_a_energy2"))
        self.assertFalse(denergy2_re.match("ch2_p_energy2"))

    def test_power(self):
        self.assertTrue(power_re.match("ch2_a_power"))
        self.assertTrue(power_re.match("ch2_p_power"))
        self.assertTrue(power_re.match("ch12_a_power"))
        self.assertTrue(power_re.match("ch12_p_power"))


if __name__ == '__main__':
    unittest.main()
