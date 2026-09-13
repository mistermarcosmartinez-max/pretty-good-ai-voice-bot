import unittest

from call_safety import validate_destination


class ValidateDestinationTests(unittest.TestCase):
    def test_approved_destination_is_accepted(self):
        self.assertEqual(validate_destination("+18054398008"), "+18054398008")

    def test_different_destination_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_destination("+12025550123")

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_destination("")

    def test_none_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_destination(None)

    def test_altered_format_is_rejected(self):
        for number in (" +18054398008", "+18054398008 ", "18054398008", "+1-805-439-8008"):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    validate_destination(number)


if __name__ == "__main__":
    unittest.main()
