import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

import config
from config import load_settings, validate_settings


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


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "fictional.env"
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-secret",
            "TWILIO_ACCOUNT_SID": "fictional-twilio-account",
            "TWILIO_AUTH_TOKEN": "fictional-twilio-secret",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://example.invalid",
        }
        self.write_settings(self.path)

    def write_settings(self, path):
        path.write_text(
            "".join(f"{name}={value}\n" for name, value in self.settings.items()),
            encoding="utf-8",
        )

    def test_file_loading_returns_only_required_settings(self):
        with self.path.open("a", encoding="utf-8") as file:
            file.write("UNRELATED=fictional-extra\n")
        os.environ["OTHER_SETTING"] = "fictional-environment-extra"
        self.assertEqual(load_settings(self.path), self.settings)

    def test_environment_takes_precedence(self):
        for name in self.settings:
            os.environ[name] = "fictional-environment-override"
        self.assertEqual(load_settings(self.path), dict(os.environ))

    def test_empty_and_whitespace_environment_overrides_fail(self):
        for name in self.settings:
            for value in ("", " \t"):
                with self.subTest(setting=name), patch.dict(os.environ, {name: value}):
                    with self.assertRaises(ValueError) as error:
                        load_settings(self.path)
                    self.assertEqual(str(error.exception), name)

    def test_missing_settings_fail_without_exposing_secrets(self):
        self.path.write_text("OPENAI_API_KEY=fictional-secret\n", encoding="utf-8")
        with self.assertRaises(ValueError) as error:
            load_settings(self.path)
        self.assertEqual(str(error.exception), ", ".join(list(self.settings)[1:]))
        self.assertNotIn("fictional-secret", str(error.exception))

    def test_environment_is_unchanged_on_success_and_failure(self):
        os.environ["UNRELATED"] = "fictional-untouched"
        before = dict(os.environ)
        load_settings(self.path)
        self.assertEqual(dict(os.environ), before)
        self.path.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_settings(self.path)
        self.assertEqual(dict(os.environ), before)

    def test_default_file_is_beside_module_not_current_directory(self):
        module_dir = self.root / "module"
        module_dir.mkdir()
        self.write_settings(module_dir / ".env")
        # Redirect the module location so the real project .env is never read.
        with patch.object(config, "__file__", str(module_dir / "config.py")):
            self.assertEqual(load_settings(), self.settings)

    def test_missing_default_file_does_not_search_parent_folders(self):
        module_dir = self.root / "module"
        module_dir.mkdir()
        self.write_settings(self.root / ".env")
        with patch.object(config, "__file__", str(module_dir / "config.py")):
            with self.assertRaises(ValueError) as error:
                load_settings()
        self.assertEqual(str(error.exception), ", ".join(self.settings))

    def test_missing_explicit_file_can_use_environment_only(self):
        os.environ.update(self.settings)
        self.assertEqual(load_settings(self.root / "absent.env"), self.settings)

    def test_variable_expansion_is_disabled(self):
        self.settings["OPENAI_API_KEY"] = "${FICTIONAL_SECRET}"
        self.write_settings(self.path)
        os.environ["FICTIONAL_SECRET"] = "fictional-expanded-value"
        self.assertEqual(load_settings(self.path), self.settings)

    def test_import_does_not_read_settings(self):
        with patch("dotenv.dotenv_values", side_effect=AssertionError("Unexpected file read")):
            runpy.run_path(config.__file__, run_name="offline_config_import_test")


if __name__ == "__main__":
    unittest.main()
