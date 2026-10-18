from unittest import TestCase

from collectors.common.value_converter import normalize_value


class NormalizeValueTests(TestCase):
    def test_scaled_integer_register_preserves_fractional_engineering_value(self):
        self.assertEqual(normalize_value(13556, "uint16", "0.001"), 13.556)

    def test_scaled_signed_register_preserves_fractional_engineering_value(self):
        self.assertEqual(normalize_value(-123, "int16", "0.1"), -12.3)

    def test_scaled_integer_that_remains_integral_stays_integer(self):
        self.assertEqual(normalize_value(120, "uint16", "0.1"), 12)
