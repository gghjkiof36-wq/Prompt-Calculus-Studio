import math
import unittest
from prompt_calculus_studio.parameter_display import display_value


class ParameterDisplayTests(unittest.TestCase):
    def test_binary_tails_are_compact_without_step_rounding(self):
        for value, expected in [(0.15000000000000002, '0.15'), (0.30000000000000004, '0.3'),
                                (-0.30000000000000004, '-0.3'), (0.35000000000000003, '0.35')]:
            with self.subTest(value=value):
                self.assertEqual(display_value(value, 'FLOAT'), expected)

    def test_precision_extremes_and_non_numeric_text_stay_exact(self):
        for value in [0.12345678901234567, math.pi, 1.2345678901234567e-210,
                      1.7976931348623157e308, 5e-324, -0.0, 0.0, 2.0, float('inf')]:
            with self.subTest(value=value):
                self.assertEqual(display_value(value, 'FLOAT'), str(value))
        for value, kind in [(2**53-1, 'INT'), ('0.15000000000000002', 'STRING'),
                            ('0.15000000000000002', 'FLOAT'), ('-', 'FLOAT'),
                            ('1e-', 'FLOAT'), (True, 'BOOLEAN')]:
            self.assertEqual(display_value(value, kind), str(value))


if __name__ == '__main__':
    unittest.main()
