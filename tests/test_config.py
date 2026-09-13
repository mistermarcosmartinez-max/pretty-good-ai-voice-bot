import unittest

from config import validate_settings


class ValidateSettingsTests(unittest.TestCase):
    def setUp(self):
        # Fictional placeholders only; these tests never contact providers.
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-secret",
            "TWILIO_ACCOUNT_SID": "fictional-twilio-account",
            "TWILIO_AUTH_TOKEN": "fictional-twilio-secret",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://example.invalid",
        }

    def test_valid_placeholders_return_none(self):
        self.assertIsNone(validate_settings(self.settings))

    def test_each_missing_setting_is_rejected(self):
        for name in self.settings:
            with self.subTest(setting=name):
                settings = self.settings.copy()
                del settings[name]
                with self.assertRaises(ValueError) as error:
                    validate_settings(settings)
                self.assertEqual(str(error.exception), name)

    def test_all_missing_settings_are_listed(self):
        with self.assertRaises(ValueError) as error:
            validate_settings({})
        self.assertEqual(str(error.exception), ", ".join(self.settings))

    def test_empty_values_are_rejected(self):
        self.assert_invalid_value("")

    def test_whitespace_only_values_are_rejected(self):
        self.assert_invalid_value(" \t\r\n")

    def test_none_values_are_rejected(self):
        self.assert_invalid_value(None)

    def test_other_non_string_values_are_rejected(self):
        for value in (123, False, ["fictional-secret"], {"secret": "fictional-secret"}):
            self.assert_invalid_value(value)

    def assert_invalid_value(self, value):
        for name in self.settings:
            with self.subTest(setting=name):
                settings = self.settings.copy()
                settings[name] = value
                with self.assertRaises(ValueError) as error:
                    validate_settings(settings)
                self.assertEqual(str(error.exception), name)

    def test_error_messages_do_not_expose_supplied_secrets(self):
        settings = self.settings.copy()
        settings["TWILIO_AUTH_TOKEN"] = ["fictional-invalid-secret"]
        del settings["PUBLIC_BASE_URL"]
        with self.assertRaises(ValueError) as error:
            validate_settings(settings)
        message = str(error.exception)
        self.assertEqual(message, "TWILIO_AUTH_TOKEN, PUBLIC_BASE_URL")
        for secret in (*self.settings.values(), "fictional-invalid-secret"):
            self.assertNotIn(secret, message)


if __name__ == "__main__":
    unittest.main()
