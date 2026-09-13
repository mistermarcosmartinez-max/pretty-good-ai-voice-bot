import unittest
from unittest.mock import MagicMock, patch

from call_safety import ASSESSMENT_DESTINATION
from outbound_call import build_twilio_client, create_assessment_call


class OutboundCallTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-secret",
            "TWILIO_ACCOUNT_SID": "fictional-account-sid",
            "TWILIO_AUTH_TOKEN": "fictional-auth-token",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://voice.example.invalid",
        }
        self.client = MagicMock()
        self.client.calls.create.return_value.sid = "CAfictionalcallsid"

    def test_exact_create_arguments_and_returned_sid(self):
        result = create_assessment_call(
            self.client,
            self.settings,
            "schedule_routine_visit",
        )

        self.assertEqual(result, "CAfictionalcallsid")
        self.client.calls.create.assert_called_once_with(
            to=ASSESSMENT_DESTINATION,
            from_="+12025550123",
            url=(
                "https://voice.example.invalid/voice"
                "?scenario_id=schedule_routine_visit"
            ),
            method="POST",
            status_callback="https://voice.example.invalid/calls/status",
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            record=True,
            recording_channels="dual",
            recording_track="both",
            recording_status_callback=(
                "https://voice.example.invalid/recordings/status"
            ),
            recording_status_callback_method="POST",
            recording_status_callback_event=["completed"],
        )

    def test_only_fixed_destination_is_validated_and_used(self):
        with patch(
            "outbound_call.call_safety.validate_destination",
            return_value=ASSESSMENT_DESTINATION,
        ) as validate_destination:
            create_assessment_call(
                self.client,
                self.settings,
                "schedule_routine_visit",
            )

        validate_destination.assert_called_once_with(ASSESSMENT_DESTINATION)
        self.assertEqual(
            self.client.calls.create.call_args.kwargs["to"],
            ASSESSMENT_DESTINATION,
        )

    def test_patched_destination_is_rejected_before_create(self):
        with patch(
            "outbound_call.call_safety.ASSESSMENT_DESTINATION",
            "+13105550199",
        ):
            with self.assertRaisesRegex(ValueError, "destination safety check"):
                create_assessment_call(
                    self.client,
                    self.settings,
                    "schedule_routine_visit",
                )

        self.client.calls.create.assert_not_called()

    def test_destination_validator_failure_prevents_create(self):
        with patch(
            "outbound_call.call_safety.validate_destination",
            side_effect=ValueError("Destination rejected."),
        ):
            with self.assertRaisesRegex(ValueError, "Destination rejected"):
                create_assessment_call(
                    self.client,
                    self.settings,
                    "schedule_routine_visit",
                )

        self.client.calls.create.assert_not_called()

    def test_unknown_scenario_is_rejected_before_create(self):
        unknown_id = "unknown_fictional_scenario"
        with self.assertRaises(ValueError) as error:
            create_assessment_call(
                self.client,
                self.settings,
                unknown_id,
            )
        self.assertNotIn(unknown_id, str(error.exception))
        self.client.calls.create.assert_not_called()

    def test_invalid_required_settings_are_rejected_before_create(self):
        for name in self.settings:
            with self.subTest(setting=name):
                invalid_settings = self.settings.copy()
                invalid_settings[name] = ""
                with self.assertRaises(ValueError) as error:
                    create_assessment_call(
                        self.client,
                        invalid_settings,
                        "schedule_routine_visit",
                    )
                self.assertEqual(str(error.exception), name)
                self.client.calls.create.assert_not_called()

    def test_invalid_caller_ids_are_rejected_without_exposing_values(self):
        invalid_numbers = (
            "fictional-caller-secret",
            "12025550123",
            "+1-202-555-0123",
            "+02025550123",
            "+1234567890123456",
        )
        for number in invalid_numbers:
            with self.subTest(number=number):
                settings = self.settings.copy()
                settings["TWILIO_FROM_NUMBER"] = number
                with self.assertRaises(ValueError) as error:
                    create_assessment_call(
                        self.client,
                        settings,
                        "schedule_routine_visit",
                    )
                self.assertNotIn(number, str(error.exception))
                self.client.calls.create.assert_not_called()

    def test_invalid_public_urls_are_rejected_without_exposing_values(self):
        invalid_urls = (
            "http://voice.example.invalid",
            "https:///missing-host",
            "https://user:password@voice.example.invalid",
            "https://voice.example.invalid?secret=query-value",
            "https://voice.example.invalid#secret-fragment",
            "https://voice.example.invalid:invalid-port",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                settings = self.settings.copy()
                settings["PUBLIC_BASE_URL"] = url
                with self.assertRaises(ValueError) as error:
                    create_assessment_call(
                        self.client,
                        settings,
                        "schedule_routine_visit",
                    )
                self.assertNotIn(url, str(error.exception))
                self.client.calls.create.assert_not_called()

    def test_error_messages_do_not_expose_credentials(self):
        settings = self.settings.copy()
        settings["TWILIO_FROM_NUMBER"] = "invalid-caller"
        with self.assertRaises(ValueError) as error:
            create_assessment_call(
                self.client,
                settings,
                "schedule_routine_visit",
            )
        message = str(error.exception)
        for value in self.settings.values():
            self.assertNotIn(value, message)

    def test_client_construction_uses_only_credentials_without_request(self):
        client_factory = MagicMock()
        result = build_twilio_client(self.settings, client_factory=client_factory)

        client_factory.assert_called_once_with(
            "fictional-account-sid",
            "fictional-auth-token",
        )
        self.assertIs(result, client_factory.return_value)
        client_factory.return_value.calls.create.assert_not_called()

    def test_invalid_settings_prevent_client_construction(self):
        client_factory = MagicMock()
        settings = self.settings.copy()
        settings["TWILIO_AUTH_TOKEN"] = ""
        with self.assertRaisesRegex(ValueError, "^TWILIO_AUTH_TOKEN$"):
            build_twilio_client(settings, client_factory=client_factory)
        client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
