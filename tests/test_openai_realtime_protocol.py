import json
import unittest

import openai_realtime_protocol
from openai_realtime_protocol import (
    SUPPORTED_VOICES,
    build_input_audio_buffer_append,
    build_session_update,
    parse_input_audio_buffer_speech_started,
    parse_response_output_audio_delta,
)
from patient_scenarios import build_patient_prompt, get_scenario


class OpenAIRealtimeProtocolTests(unittest.TestCase):
    scenario_id = "schedule_routine_visit"
    model = "gpt-realtime-fictional-model"
    voice = "marin"
    payload = "AQIDBA=="

    def assert_private_error(self, function, *args):
        supplied_values = (
            self.scenario_id,
            self.model,
            self.payload,
            "unknown-scenario-sentinel",
            "unsupported-voice-sentinel",
            "private-prompt-sentinel",
            "fictional-secret-sentinel",
            "+12025550123",
            "event-fictional-sentinel",
            "item-fictional-sentinel",
        )
        with self.assertRaises(ValueError) as raised:
            function(*args)
        message = str(raised.exception)
        for value in supplied_values:
            self.assertNotIn(value, message)

    def test_build_session_update_returns_exact_dictionary(self):
        expected_prompt = build_patient_prompt(get_scenario(self.scenario_id))
        self.assertEqual(
            build_session_update(self.scenario_id, self.model, self.voice),
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": self.model,
                    "output_modalities": ["audio"],
                    "instructions": expected_prompt,
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu", "rate": 8000},
                            "turn_detection": {"type": "semantic_vad"},
                        },
                        "output": {
                            "format": {"type": "audio/pcmu", "rate": 8000},
                            "voice": self.voice,
                        },
                    },
                },
            },
        )

    def test_session_uses_existing_prompt_without_duplicate_scenario_content(self):
        scenario = get_scenario(self.scenario_id)
        event = build_session_update(self.scenario_id, self.model, self.voice)
        instructions = event["session"]["instructions"]
        self.assertEqual(instructions, build_patient_prompt(scenario))
        self.assertEqual(instructions.count(scenario.opening_line), 1)
        self.assertNotIn("scenario_id", event["session"])

    def test_all_supported_voices_are_accepted(self):
        self.assertEqual(
            SUPPORTED_VOICES,
            (
                "alloy", "ash", "ballad", "coral", "echo", "sage",
                "shimmer", "verse", "marin", "cedar",
            ),
        )
        for voice in SUPPORTED_VOICES:
            with self.subTest(voice=voice):
                event = build_session_update(self.scenario_id, self.model, voice)
                self.assertEqual(event["session"]["audio"]["output"]["voice"], voice)

    def test_session_update_rejects_invalid_inputs(self):
        invalid_cases = (
            (self.scenario_id, "", self.voice),
            (self.scenario_id, "   ", self.voice),
            (self.scenario_id, None, self.voice),
            (self.scenario_id, self.model, ""),
            (self.scenario_id, self.model, "unsupported-voice-sentinel"),
            ("unknown-scenario-sentinel", self.model, self.voice),
            (None, self.model, self.voice),
            (["unknown-scenario-sentinel"], self.model, self.voice),
            ({"scenario": "unknown-scenario-sentinel"}, self.model, self.voice),
        )
        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                self.assert_private_error(build_session_update, *arguments)

    def test_input_audio_append_returns_exact_dictionary(self):
        self.assertEqual(
            build_input_audio_buffer_append(self.payload),
            {"type": "input_audio_buffer.append", "audio": self.payload},
        )

    def test_input_audio_append_requires_strict_nonempty_base64(self):
        for payload in ("", " ", "not*base64", "A===", "AAA", "AQIDBA==\n", None):
            with self.subTest(payload=payload):
                self.assert_private_error(build_input_audio_buffer_append, payload)

    def test_output_audio_delta_returns_only_validated_base64(self):
        message = json.dumps(
            {
                "type": "response.output_audio.delta",
                "delta": self.payload,
                "event_id": "event-fictional-sentinel",
                "response_id": "private-prompt-sentinel",
            }
        )
        result = parse_response_output_audio_delta(message)
        self.assertEqual(result, self.payload)
        self.assertIsInstance(result, str)

    def test_output_audio_delta_rejects_malformed_events_and_base64(self):
        invalid_messages = (
            "fictional-secret-sentinel",
            "[]",
            json.dumps({"type": "response.output_audio.done", "delta": self.payload}),
            json.dumps({"type": "response.output_audio.delta"}),
            json.dumps({"type": "response.output_audio.delta", "delta": ""}),
            json.dumps({"type": "response.output_audio.delta", "delta": "AAA"}),
            json.dumps({"type": "response.output_audio.delta", "delta": "not*base64"}),
        )
        for message in invalid_messages:
            with self.subTest(message=message):
                self.assert_private_error(parse_response_output_audio_delta, message)

    def test_speech_started_is_recognized_without_returning_event_data(self):
        message = json.dumps(
            {
                "type": "input_audio_buffer.speech_started",
                "event_id": "event-fictional-sentinel",
                "audio_start_ms": 120,
                "item_id": "item-fictional-sentinel",
                "private": "private-prompt-sentinel",
            }
        )
        self.assertIs(parse_input_audio_buffer_speech_started(message), True)

    def test_speech_started_rejects_malformed_or_incomplete_events(self):
        valid = {
            "type": "input_audio_buffer.speech_started",
            "event_id": "event-fictional-sentinel",
            "audio_start_ms": 0,
            "item_id": "item-fictional-sentinel",
        }
        invalid_messages = ["fictional-secret-sentinel", "[]"]
        for field, value in (
            ("type", "input_audio_buffer.speech_stopped"),
            ("event_id", ""),
            ("audio_start_ms", -1),
            ("audio_start_ms", True),
            ("audio_start_ms", "0"),
            ("item_id", " "),
        ):
            changed = dict(valid)
            changed[field] = value
            invalid_messages.append(json.dumps(changed))
        for message in invalid_messages:
            with self.subTest(message=message):
                self.assert_private_error(parse_input_audio_buffer_speech_started, message)

    def test_errors_never_expose_supplied_values(self):
        message = json.dumps(
            {
                "type": "wrong-event",
                "delta": "fictional-secret-sentinel",
                "scenario_id": "unknown-scenario-sentinel",
                "model": self.model,
                "phone": "+12025550123",
            }
        )
        self.assert_private_error(parse_response_output_audio_delta, message)

    def test_functions_retain_no_audio_payload(self):
        build_input_audio_buffer_append(self.payload)
        parse_response_output_audio_delta(
            json.dumps({"type": "response.output_audio.delta", "delta": self.payload})
        )
        self.assertFalse(
            any(value == self.payload for value in vars(openai_realtime_protocol).values())
        )
        self.assertEqual(build_input_audio_buffer_append.__dict__, {})
        self.assertEqual(parse_response_output_audio_delta.__dict__, {})


if __name__ == "__main__":
    unittest.main()
