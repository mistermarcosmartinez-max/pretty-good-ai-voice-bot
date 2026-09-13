import json
import unittest
from unittest.mock import patch

import audio_relay
from audio_relay import (
    openai_delta_to_twilio_media,
    openai_speech_started_to_twilio_clear,
    twilio_media_to_openai_append,
)


class AudioRelayTests(unittest.TestCase):
    stream_sid = "MZfictionalstream"
    payload = "AQIDBA=="

    def twilio_media_message(self, payload=None, stream_sid=None):
        return json.dumps(
            {
                "event": "media",
                "streamSid": self.stream_sid if stream_sid is None else stream_sid,
                "media": {
                    "track": "inbound",
                    "payload": self.payload if payload is None else payload,
                },
            }
        )

    def openai_delta_message(self, payload=None):
        return json.dumps(
            {
                "type": "response.output_audio.delta",
                "delta": self.payload if payload is None else payload,
            }
        )

    def speech_started_message(self, **changes):
        event = {
            "type": "input_audio_buffer.speech_started",
            "event_id": "event-fictional",
            "audio_start_ms": 120,
            "item_id": "item-fictional",
        }
        event.update(changes)
        return json.dumps(event)

    def assert_private_error(self, expected_message, function, *args):
        supplied_values = (
            self.stream_sid,
            self.payload,
            "MZwrong-stream-sentinel",
            "submitted-event-sentinel",
            "private-prompt-sentinel",
            "fictional-model-sentinel",
            "fictional-secret-sentinel",
            "+12025550123",
        )
        with self.assertRaises(ValueError) as raised:
            function(*args)
        self.assertEqual(str(raised.exception), expected_message)
        for value in supplied_values:
            self.assertNotIn(value, str(raised.exception))

    def test_twilio_media_converts_to_exact_openai_append(self):
        self.assertEqual(
            twilio_media_to_openai_append(
                self.twilio_media_message(), self.stream_sid
            ),
            {"type": "input_audio_buffer.append", "audio": self.payload},
        )

    def test_openai_delta_converts_to_exact_twilio_media(self):
        self.assertEqual(
            openai_delta_to_twilio_media(
                self.openai_delta_message(), self.stream_sid
            ),
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": self.payload},
            },
        )

    def test_speech_started_converts_to_exact_twilio_clear(self):
        self.assertEqual(
            openai_speech_started_to_twilio_clear(
                self.speech_started_message(), self.stream_sid
            ),
            {"event": "clear", "streamSid": self.stream_sid},
        )

    def test_wrong_twilio_stream_sid_is_rejected(self):
        self.assert_private_error(
            "Invalid Twilio-to-OpenAI audio relay.",
            twilio_media_to_openai_append,
            self.twilio_media_message(stream_sid="MZwrong-stream-sentinel"),
            self.stream_sid,
        )

    def test_malformed_twilio_and_openai_json_is_rejected(self):
        cases = (
            (
                "Invalid Twilio-to-OpenAI audio relay.",
                twilio_media_to_openai_append,
                "submitted-event-sentinel",
                self.stream_sid,
            ),
            (
                "Invalid OpenAI-to-Twilio audio relay.",
                openai_delta_to_twilio_media,
                "submitted-event-sentinel",
                self.stream_sid,
            ),
            (
                "Invalid OpenAI interruption relay.",
                openai_speech_started_to_twilio_clear,
                "submitted-event-sentinel",
                self.stream_sid,
            ),
        )
        for expected, function, *arguments in cases:
            with self.subTest(function=function.__name__):
                self.assert_private_error(expected, function, *arguments)

    def test_empty_and_invalid_base64_is_rejected_in_both_directions(self):
        for payload in ("", " ", "not*base64", "AAA", "AQIDBA==\n"):
            with self.subTest(direction="Twilio to OpenAI", payload=payload):
                self.assert_private_error(
                    "Invalid Twilio-to-OpenAI audio relay.",
                    twilio_media_to_openai_append,
                    self.twilio_media_message(payload=payload),
                    self.stream_sid,
                )
            with self.subTest(direction="OpenAI to Twilio", payload=payload):
                self.assert_private_error(
                    "Invalid OpenAI-to-Twilio audio relay.",
                    openai_delta_to_twilio_media,
                    self.openai_delta_message(payload=payload),
                    self.stream_sid,
                )

    def test_malformed_speech_started_events_are_rejected(self):
        messages = (
            self.speech_started_message(type="input_audio_buffer.speech_stopped"),
            self.speech_started_message(event_id=""),
            self.speech_started_message(audio_start_ms=-1),
            self.speech_started_message(item_id=" "),
        )
        for message in messages:
            with self.subTest(message=message):
                self.assert_private_error(
                    "Invalid OpenAI interruption relay.",
                    openai_speech_started_to_twilio_clear,
                    message,
                    self.stream_sid,
                )

    def test_empty_and_malformed_stream_identifiers_are_rejected(self):
        for stream_sid in ("", " ", None, [], {}):
            with self.subTest(operation="inbound", stream_sid=stream_sid):
                self.assert_private_error(
                    "Invalid Twilio-to-OpenAI audio relay.",
                    twilio_media_to_openai_append,
                    self.twilio_media_message(),
                    stream_sid,
                )
            with self.subTest(operation="outbound", stream_sid=stream_sid):
                self.assert_private_error(
                    "Invalid OpenAI-to-Twilio audio relay.",
                    openai_delta_to_twilio_media,
                    self.openai_delta_message(),
                    stream_sid,
                )
            with self.subTest(operation="clear", stream_sid=stream_sid):
                self.assert_private_error(
                    "Invalid OpenAI interruption relay.",
                    openai_speech_started_to_twilio_clear,
                    self.speech_started_message(),
                    stream_sid,
                )

    def test_errors_never_expose_submitted_values(self):
        private_message = json.dumps(
            {
                "type": "wrong-event",
                "payload": self.payload,
                "streamSid": self.stream_sid,
                "prompt": "private-prompt-sentinel",
                "model": "fictional-model-sentinel",
                "secret": "fictional-secret-sentinel",
                "phone": "+12025550123",
            }
        )
        self.assert_private_error(
            "Invalid OpenAI-to-Twilio audio relay.",
            openai_delta_to_twilio_media,
            private_message,
            self.stream_sid,
        )

    def test_adapter_retains_no_audio_or_submitted_event_data(self):
        twilio_media_to_openai_append(self.twilio_media_message(), self.stream_sid)
        openai_delta_to_twilio_media(self.openai_delta_message(), self.stream_sid)
        openai_speech_started_to_twilio_clear(
            self.speech_started_message(), self.stream_sid
        )
        module_values = vars(audio_relay).values()
        self.assertFalse(any(value == self.payload for value in module_values))
        self.assertFalse(any(value == self.stream_sid for value in module_values))
        for function in (
            twilio_media_to_openai_append,
            openai_delta_to_twilio_media,
            openai_speech_started_to_twilio_clear,
        ):
            self.assertEqual(function.__dict__, {})

    def test_twilio_to_openai_reuses_public_parser_and_builder(self):
        expected = {"type": "input_audio_buffer.append", "audio": self.payload}
        with (
            patch.object(
                audio_relay.media_protocol,
                "parse_inbound_media_message",
                return_value=self.payload,
            ) as parser,
            patch.object(
                audio_relay.openai_realtime_protocol,
                "build_input_audio_buffer_append",
                return_value=expected,
            ) as builder,
        ):
            result = twilio_media_to_openai_append(
                "submitted-event-sentinel", self.stream_sid
            )
        parser.assert_called_once_with("submitted-event-sentinel", self.stream_sid)
        builder.assert_called_once_with(self.payload)
        self.assertIs(result, expected)

    def test_openai_to_twilio_reuses_public_parser_and_builder(self):
        expected = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": self.payload},
        }
        with (
            patch.object(
                audio_relay.openai_realtime_protocol,
                "parse_response_output_audio_delta",
                return_value=self.payload,
            ) as parser,
            patch.object(
                audio_relay.media_protocol,
                "build_media_message",
                return_value=expected,
            ) as builder,
        ):
            result = openai_delta_to_twilio_media(
                "submitted-event-sentinel", self.stream_sid
            )
        parser.assert_called_once_with("submitted-event-sentinel")
        builder.assert_called_once_with(self.stream_sid, self.payload)
        self.assertIs(result, expected)

    def test_interruption_reuses_public_parser_and_clear_builder(self):
        expected = {"event": "clear", "streamSid": self.stream_sid}
        with (
            patch.object(
                audio_relay.openai_realtime_protocol,
                "parse_input_audio_buffer_speech_started",
                return_value=True,
            ) as parser,
            patch.object(
                audio_relay.media_protocol,
                "build_clear_message",
                return_value=expected,
            ) as builder,
        ):
            result = openai_speech_started_to_twilio_clear(
                "submitted-event-sentinel", self.stream_sid
            )
        parser.assert_called_once_with("submitted-event-sentinel")
        builder.assert_called_once_with(self.stream_sid)
        self.assertIs(result, expected)


if __name__ == "__main__":
    unittest.main()
