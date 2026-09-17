import json
import unittest

from openai.types import realtime as realtime_types
from pydantic import TypeAdapter

import openai_realtime_protocol
from openai_realtime_protocol import (
    SUPPORTED_VOICES,
    build_input_audio_buffer_append,
    build_response_cancel,
    build_session_update,
    parse_input_audio_transcription_delta,
    parse_input_audio_transcription_completed,
    parse_input_audio_buffer_speech_started,
    parse_response_created,
    parse_response_done,
    parse_response_output_audio_delta,
    parse_response_output_audio_done,
    parse_response_output_audio_transcript_delta,
    parse_response_output_audio_transcript_done,
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

    def test_events_match_installed_openai_realtime_type_contracts(self):
        server_events = (
            (
                realtime_types.ResponseCreatedEvent,
                {
                    "type": "response.created",
                    "event_id": "event-created",
                    "response": {"id": "response-1", "status": "in_progress"},
                },
            ),
            (
                realtime_types.ResponseDoneEvent,
                {
                    "type": "response.done",
                    "event_id": "event-done",
                    "response": {"id": "response-1", "status": "cancelled"},
                },
            ),
            (
                realtime_types.ConversationItemInputAudioTranscriptionDeltaEvent,
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "event_id": "event-input-delta",
                    "item_id": "item-input",
                    "content_index": 0,
                    "delta": "Transferring you now.",
                },
            ),
            (
                realtime_types.ConversationItemInputAudioTranscriptionCompletedEvent,
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "event_id": "event-input-completed",
                    "item_id": "item-input",
                    "content_index": 0,
                    "transcript": "Transferring you now.",
                    "usage": {"type": "duration", "seconds": 1.0},
                },
            ),
            (
                realtime_types.ResponseAudioTranscriptDeltaEvent,
                {
                    "type": "response.output_audio_transcript.delta",
                    "event_id": "event-output-transcript-delta",
                    "response_id": "response-1",
                    "item_id": "item-output",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "Yes, please.",
                },
            ),
            (
                realtime_types.ResponseAudioTranscriptDoneEvent,
                {
                    "type": "response.output_audio_transcript.done",
                    "event_id": "event-output-transcript-done",
                    "response_id": "response-1",
                    "item_id": "item-output",
                    "output_index": 0,
                    "content_index": 0,
                    "transcript": "Yes, please.",
                },
            ),
            (
                realtime_types.ResponseAudioDeltaEvent,
                {
                    "type": "response.output_audio.delta",
                    "event_id": "event-audio-delta",
                    "response_id": "response-1",
                    "item_id": "item-output",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": self.payload,
                },
            ),
            (
                realtime_types.ResponseAudioDoneEvent,
                {
                    "type": "response.output_audio.done",
                    "event_id": "event-audio-done",
                    "response_id": "response-1",
                    "item_id": "item-output",
                    "output_index": 0,
                    "content_index": 0,
                },
            ),
            (
                realtime_types.InputAudioBufferSpeechStartedEvent,
                {
                    "type": "input_audio_buffer.speech_started",
                    "event_id": "event-speech-started",
                    "item_id": "item-input",
                    "audio_start_ms": 100,
                },
            ),
        )
        for event_type, event in server_events:
            with self.subTest(event_type=event_type.__name__):
                self.assertEqual(event_type.model_validate(event).type, event["type"])

        for event_type, event in (
            (
                realtime_types.SessionUpdateEventParam,
                build_session_update(self.scenario_id, self.model, self.voice),
            ),
            (
                realtime_types.ResponseCancelEventParam,
                build_response_cancel("response-1"),
            ),
            (
                realtime_types.InputAudioBufferAppendEventParam,
                build_input_audio_buffer_append(self.payload),
            ),
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    TypeAdapter(event_type).validate_python(event)["type"],
                    event["type"],
                )

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
                            "format": {"type": "audio/pcmu"},
                            "transcription": {
                                "model": "gpt-4o-mini-transcribe",
                                "language": "en",
                            },
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 900,
                                "create_response": True,
                                "interrupt_response": False,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},
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

    def test_pcmu_uses_exact_ga_server_vad_configuration(self):
        session = build_session_update(
            self.scenario_id, self.model, self.voice
        )["session"]

        self.assertEqual(
            session["audio"]["input"]["format"],
            {"type": "audio/pcmu"},
        )
        self.assertEqual(
            session["audio"]["input"]["transcription"],
            {"model": "gpt-4o-mini-transcribe", "language": "en"},
        )
        self.assertEqual(
            session["audio"]["output"]["format"],
            {"type": "audio/pcmu"},
        )
        self.assertEqual(
            session["audio"]["input"]["turn_detection"],
            {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 900,
                "create_response": True,
                "interrupt_response": False,
            },
        )

    def test_session_includes_concise_completion_instructions(self):
        instructions = build_session_update(
            self.scenario_id, self.model, self.voice
        )["session"]["instructions"]
        normalized = " ".join(instructions.split())
        self.assertIn("normally in one or two sentences", normalized)
        self.assertIn("current question directly", normalized)
        self.assertIn("Ask at most one follow-up question", normalized)
        self.assertIn("only when necessary to achieve the scenario outcome", normalized)
        self.assertIn("Do not repeat already-confirmed details", normalized)
        self.assertIn(
            "Once the outcome and next step are clearly confirmed, briefly "
            "acknowledge them and end the call naturally",
            normalized,
        )
        self.assertNotIn("90 to 150 seconds", instructions)
        self.assertIn("Preserve natural turn-taking pauses", instructions)
        self.assertIn("never interrupt or talk over it", instructions)
        self.assertIn("Silence while listening is acceptable", normalized)
        self.assertIn(
            "Remain silent during any recording disclosure", instructions
        )
        self.assertIn(
            "first substantive question or invitation\nto speak", instructions
        )

    def test_output_audio_done_is_recognized_without_retaining_event_data(self):
        message = json.dumps(
            {
                "type": "response.output_audio.done",
                "event_id": "event-fictional-sentinel",
                "response_id": "response-fictional",
                "item_id": "item-fictional-sentinel",
                "output_index": 0,
                "content_index": 0,
            }
        )
        self.assertIs(parse_response_output_audio_done(message), True)

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

    def test_response_lifecycle_and_transfer_transcript_events(self):
        created = json.dumps(
            {
                "type": "response.created",
                "event_id": "event-created",
                "response": {"id": "response-fictional", "status": "in_progress"},
            }
        )
        done = json.dumps(
            {
                "type": "response.done",
                "event_id": "event-done",
                "response": {"id": "response-fictional", "status": "cancelled"},
            }
        )
        transcript = json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "event_id": "event-transcript",
                "item_id": "item-fictional-sentinel",
                "content_index": 0,
                "transcript": "I will connect the call now.",
            }
        )

        self.assertEqual(parse_response_created(created), "response-fictional")
        self.assertEqual(parse_response_done(done), "response-fictional")
        self.assertEqual(
            parse_input_audio_transcription_completed(transcript),
            "I will connect the call now.",
        )
        self.assertEqual(
            build_response_cancel("response-fictional"),
            {"type": "response.cancel", "response_id": "response-fictional"},
        )

    def test_streaming_input_and_output_transcript_events(self):
        common = {
            "event_id": "event-transcript",
            "item_id": "item-fictional-sentinel",
            "content_index": 0,
        }
        input_delta = json.dumps(
            {
                **common,
                "type": "conversation.item.input_audio_transcription.delta",
                "delta": "Transferring you now.",
            }
        )
        output_common = {
            **common,
            "response_id": "response-fictional",
            "output_index": 0,
        }
        output_delta = json.dumps(
            {
                **output_common,
                "type": "response.output_audio_transcript.delta",
                "delta": "Yes, please transfer me.",
            }
        )
        output_done = json.dumps(
            {
                **output_common,
                "type": "response.output_audio_transcript.done",
                "transcript": "Yes, please transfer me.",
            }
        )

        self.assertEqual(
            parse_input_audio_transcription_delta(input_delta),
            ("item-fictional-sentinel", "Transferring you now."),
        )
        self.assertEqual(
            parse_response_output_audio_transcript_delta(output_delta),
            ("response-fictional", "Yes, please transfer me."),
        )
        self.assertEqual(
            parse_response_output_audio_transcript_done(output_done),
            ("response-fictional", "Yes, please transfer me."),
        )

        whitespace_delta = json.dumps(
            {
                **common,
                "type": "conversation.item.input_audio_transcription.delta",
                "delta": " ",
            }
        )
        self.assertEqual(
            parse_input_audio_transcription_delta(whitespace_delta),
            ("item-fictional-sentinel", " "),
        )
        optional_input_delta = json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "event_id": "event-optional-delta",
                "item_id": "item-fictional-sentinel",
            }
        )
        self.assertEqual(
            parse_input_audio_transcription_delta(optional_input_delta),
            ("item-fictional-sentinel", None),
        )

    def test_response_lifecycle_helpers_reject_malformed_events_privately(self):
        malformed_cases = (
            (build_response_cancel, ("",)),
            (parse_response_created, (json.dumps({"type": "response.created"}),)),
            (
                parse_response_done,
                (
                    json.dumps(
                        {
                            "type": "response.done",
                            "event_id": "event-done",
                            "response": {
                                "id": "response-fictional",
                                "status": "in_progress",
                            },
                        }
                    ),
                ),
            ),
            (
                parse_input_audio_transcription_completed,
                (
                    json.dumps(
                        {
                            "type": "conversation.item.input_audio_transcription.completed",
                            "event_id": "event-transcript",
                            "item_id": "item-fictional-sentinel",
                            "content_index": 0,
                            "transcript": " ",
                        }
                    ),
                ),
            ),
            (
                parse_input_audio_transcription_delta,
                (json.dumps({"type": "conversation.item.input_audio_transcription.delta"}),),
            ),
            (
                parse_response_output_audio_transcript_delta,
                (json.dumps({"type": "response.output_audio_transcript.delta"}),),
            ),
            (
                parse_response_output_audio_transcript_done,
                (json.dumps({"type": "response.output_audio_transcript.done"}),),
            ),
        )
        for function, arguments in malformed_cases:
            with self.subTest(function=function.__name__):
                self.assert_private_error(function, *arguments)

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
                "item_id": "item-fictional-sentinel",
                "output_index": 0,
                "content_index": 0,
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

    def test_output_audio_delta_requires_all_identity_and_index_fields(self):
        valid = {
            "type": "response.output_audio.delta",
            "event_id": "event-fictional-sentinel",
            "response_id": "response-fictional-sentinel",
            "item_id": "item-fictional-sentinel",
            "output_index": 0,
            "content_index": 0,
            "delta": self.payload,
        }
        for field in (
            "event_id",
            "response_id",
            "item_id",
            "output_index",
            "content_index",
        ):
            malformed = dict(valid)
            del malformed[field]
            with self.subTest(field=field):
                self.assert_private_error(
                    parse_response_output_audio_delta, json.dumps(malformed)
                )

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
            json.dumps(
                {
                    "type": "response.output_audio.delta",
                    "event_id": "event-fictional-sentinel",
                    "response_id": "response-fictional-sentinel",
                    "item_id": "item-fictional-sentinel",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": self.payload,
                }
            )
        )
        self.assertFalse(
            any(value == self.payload for value in vars(openai_realtime_protocol).values())
        )
        self.assertEqual(build_input_audio_buffer_append.__dict__, {})
        self.assertEqual(parse_response_output_audio_delta.__dict__, {})


if __name__ == "__main__":
    unittest.main()
