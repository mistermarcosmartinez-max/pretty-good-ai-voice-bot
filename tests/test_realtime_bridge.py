import asyncio
import importlib
import inspect
import json
import unittest
from unittest.mock import patch

from starlette.websockets import WebSocketDisconnect

import config
import realtime_bridge
from realtime_bridge import bridge_realtime_audio


class FakeTwilioWebSocket:
    def __init__(self, messages, receive_observer=None):
        self.messages = list(messages)
        self.sent = []
        self.receive_count = 0
        self.accept_count = 0
        self.close_count = 0
        self.receive_observer = receive_observer
        self._available = asyncio.Event()
        if self.messages:
            self._available.set()

    async def receive_text(self):
        self.receive_count += 1
        if self.receive_observer is not None:
            self.receive_observer(self.receive_count)
        while not self.messages:
            self._available.clear()
            await self._available.wait()
        message = self.messages.pop(0)
        if message is WebSocketDisconnect:
            raise WebSocketDisconnect(code=1000)
        return message

    def add_message(self, message):
        self.messages.append(message)
        self._available.set()

    async def send_text(self, message):
        self.sent.append(message)

    async def accept(self):
        self.accept_count += 1

    async def close(self):
        self.close_count += 1


class FakeOpenAIConnection:
    def __init__(
        self,
        messages=(),
        end_when_empty=False,
        send_error=None,
        on_empty=None,
    ):
        self.messages = list(messages)
        self.sent = []
        self.end_when_empty = end_when_empty
        self.send_error = send_error
        self.on_empty = on_empty
        self.close_count = 0
        self._blocked = asyncio.Event()

    async def send(self, message):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return self.messages.pop(0)
        if self.end_when_empty:
            raise StopAsyncIteration
        if self.on_empty is not None:
            on_empty = self.on_empty
            self.on_empty = None
            on_empty()
        await self._blocked.wait()

    async def close(self):
        self.close_count += 1


class RealtimeBridgeTests(unittest.TestCase):
    account_sid = "ACfictionalaccount"
    stream_sid = "MZfictionalstream"
    call_sid = "CAfictionalcall"
    scenario_id = "schedule_routine_visit"
    payload_one = "AQIDBA=="
    payload_two = "BQYHCA=="

    def connected(self):
        return json.dumps(
            {"event": "connected", "protocol": "Call", "version": "1.0.0"}
        )

    def start(self):
        return json.dumps(
            {
                "event": "start",
                "streamSid": self.stream_sid,
                "start": {
                    "accountSid": self.account_sid,
                    "streamSid": self.stream_sid,
                    "callSid": self.call_sid,
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1,
                    },
                    "customParameters": {"scenario_id": self.scenario_id},
                },
            }
        )

    def media(self, payload):
        return json.dumps(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"track": "inbound", "payload": payload},
            }
        )

    def stop(self):
        return json.dumps({"event": "stop", "streamSid": self.stream_sid})

    def delta(
        self,
        payload=None,
        event_id="event-fictional-output",
        response_id="response-fictional",
    ):
        return json.dumps(
            {
                "type": "response.output_audio.delta",
                "event_id": event_id,
                "response_id": response_id,
                "item_id": "item-fictional-output",
                "output_index": 0,
                "content_index": 0,
                "delta": self.payload_one if payload is None else payload,
            }
        )

    def response_created(self, response_id="response-fictional"):
        return json.dumps(
            {
                "type": "response.created",
                "event_id": f"event-created-{response_id}",
                "response": {"id": response_id, "status": "in_progress"},
            }
        )

    def response_done(self, response_id="response-fictional"):
        return json.dumps(
            {
                "type": "response.done",
                "event_id": f"event-done-{response_id}",
                "response": {"id": response_id, "status": "completed"},
            }
        )

    def input_transcript(
        self,
        transcript,
        event_id="event-transcript",
        item_id="item-fictional-input",
    ):
        return json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "event_id": event_id,
                "item_id": item_id,
                "content_index": 0,
                "transcript": transcript,
            }
        )

    def input_transcript_delta(
        self,
        delta,
        event_id="event-input-transcript-delta",
        item_id="item-fictional-input",
    ):
        return json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "event_id": event_id,
                "item_id": item_id,
                "content_index": 0,
                "delta": delta,
            }
        )

    def output_transcript_delta(
        self,
        transcript,
        event_id="event-output-transcript-delta",
        response_id="response-fictional",
    ):
        return json.dumps(
            {
                "type": "response.output_audio_transcript.delta",
                "event_id": event_id,
                "response_id": response_id,
                "item_id": "item-fictional-output",
                "output_index": 0,
                "content_index": 0,
                "delta": transcript,
            }
        )

    def output_transcript_done(
        self,
        transcript,
        event_id="event-output-transcript-done",
        response_id="response-fictional",
    ):
        return json.dumps(
            {
                "type": "response.output_audio_transcript.done",
                "event_id": event_id,
                "response_id": response_id,
                "item_id": "item-fictional-output",
                "output_index": 0,
                "content_index": 0,
                "transcript": transcript,
            }
        )

    def speech_started(self, event_id="event-fictional"):
        return json.dumps(
            {
                "type": "input_audio_buffer.speech_started",
                "event_id": event_id,
                "audio_start_ms": 120,
                "item_id": "item-fictional",
            }
        )

    def audio_done(self):
        return json.dumps(
            {
                "type": "response.output_audio.done",
                "event_id": "event-fictional-audio-done",
                "response_id": "response-fictional",
                "item_id": "item-fictional-output",
                "output_index": 0,
                "content_index": 0,
            }
        )

    def mark(self, name="response_1_played"):
        return json.dumps(
            {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": name},
            }
        )

    def run_bridge(self, twilio, openai):
        return asyncio.run(
            bridge_realtime_audio(twilio, openai, self.account_sid)
        )

    def test_start_sends_exactly_one_session_update(self):
        twilio = FakeTwilioWebSocket(
            (self.connected(), self.start(), self.stop())
        )
        openai = FakeOpenAIConnection()

        result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "stopped")
        self.assertEqual(len(openai.sent), 1)
        event = json.loads(openai.sent[0])
        self.assertEqual(event["type"], "session.update")
        self.assertEqual(event["session"]["model"], "gpt-realtime-2.1")
        self.assertEqual(event["session"]["audio"]["output"]["voice"], "marin")
        self.assertIn("Jordan Lee", event["session"]["instructions"])
        self.assertEqual(twilio.accept_count, 0)
        self.assertEqual(twilio.close_count, 0)
        self.assertEqual(openai.close_count, 0)

    def test_two_inbound_media_events_send_two_openai_appends(self):
        twilio = FakeTwilioWebSocket(
            (
                self.connected(),
                self.start(),
                self.media(self.payload_one),
                self.media(self.payload_two),
                self.stop(),
            )
        )
        openai = FakeOpenAIConnection()

        self.run_bridge(twilio, openai)

        events = [json.loads(message) for message in openai.sent]
        self.assertEqual(
            events[1:],
            [
                {"type": "input_audio_buffer.append", "audio": self.payload_one},
                {"type": "input_audio_buffer.append", "audio": self.payload_two},
            ],
        )

    def test_openai_audio_delta_sends_twilio_media(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (self.delta(),), on_empty=lambda: twilio.add_message(self.stop())
        )

        self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message) for message in twilio.sent],
            [
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": self.payload_one},
                }
            ],
        )

    def test_duplicate_openai_audio_event_is_relayed_only_once(self):
        duplicate = self.delta()
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (duplicate, duplicate),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        sent = [json.loads(message) for message in twilio.sent]
        self.assertEqual([event["event"] for event in sent], ["media"])
        self.assertIn("openai_duplicate_events_ignored=1", logs.output[0])

    def test_transfer_cancels_and_clears_output_then_suppresses_new_line(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))

        def begin_transferred_line():
            twilio.add_message(self.media(self.payload_two))
            twilio.add_message(self.stop())

        openai = FakeOpenAIConnection(
            (
                self.response_created(),
                self.delta(),
                self.input_transcript(
                    "Please hold while I connect you with the other team."
                ),
                self.speech_started("event-transfer-speech-1"),
                self.speech_started("event-transfer-speech-2"),
            ),
            on_empty=begin_transferred_line,
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "stopped")
        self.assertEqual(
            [json.loads(message) for message in twilio.sent],
            [
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": self.payload_one},
                },
                {"event": "clear", "streamSid": self.stream_sid},
            ],
        )
        openai_events = [json.loads(message) for message in openai.sent]
        self.assertEqual(openai_events[0]["type"], "session.update")
        self.assertEqual(
            openai_events[1],
            {"type": "response.cancel", "response_id": "response-fictional"},
        )
        self.assertNotIn("input_audio_buffer.append", {
            event["type"] for event in openai_events[1:]
        })
        diagnostic = logs.output[0]
        for expected in (
            "transfer_transitions_detected=1",
            "openai_responses_cancelled=1",
            "twilio_clear_messages_sent=1",
            "openai_input_audio_frames_suppressed=1",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, diagnostic)

    def test_streaming_confirmation_cancels_and_clears_active_queued_audio(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.response_created(),
                self.delta(event_id="event-audio-before-request"),
                self.output_transcript_delta("Please transfer me."),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-audio-after-request",
                ),
                self.input_transcript_delta(
                    "Transferring ", event_id="event-transfer-prefix"
                ),
                self.input_transcript_delta(
                    "you now.", event_id="event-transfer-confirmed"
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-audio-after-confirmation",
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message) for message in twilio.sent],
            [
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": self.payload_one},
                },
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": self.payload_two},
                },
                {"event": "clear", "streamSid": self.stream_sid},
            ],
        )
        openai_events = [json.loads(message) for message in openai.sent]
        self.assertEqual(
            [event for event in openai_events if event["type"] == "response.cancel"],
            [{"type": "response.cancel", "response_id": "response-fictional"}],
        )
        diagnostic = logs.output[0]
        self.assertIn("transfer_transitions_detected=1", diagnostic)
        self.assertIn("openai_output_audio_frames_suppressed=1", diagnostic)
        self.assertIn("twilio_clear_messages_sent=1", diagnostic)

    def test_call_four_order_clears_audio_when_confirmation_interrupts_output(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.response_created(),
                self.delta(event_id="event-request-audio"),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-follow-up-audio-1",
                ),
                self.input_transcript_delta(
                    "Transferring ", event_id="event-confirmation-fragment-1"
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-follow-up-audio-2",
                ),
                self.input_transcript_delta(
                    "you now.", event_id="event-confirmation-fragment-2"
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-audio-after-confirmation",
                ),
                self.input_transcript(
                    "Transferring you now. Thank you.",
                    event_id="event-confirmation-completed",
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "media", "media", "clear"],
        )
        sent = [json.loads(message) for message in openai.sent]
        self.assertEqual(
            [event for event in sent if event["type"] == "response.cancel"],
            [{"type": "response.cancel", "response_id": "response-fictional"}],
        )
        self.assertIn("transfer_transitions_detected=1", logs.output[0])
        self.assertIn("twilio_clear_messages_sent=1", logs.output[0])
        self.assertIn("openai_output_audio_frames_suppressed=1", logs.output[0])

    def test_repeated_and_cumulative_input_deltas_merge_and_cancel_once(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.response_created(),
                self.delta(event_id="event-queued-audio"),
                self.input_transcript_delta("Trans", event_id="event-fragment-1"),
                self.input_transcript_delta("Trans", event_id="event-fragment-2"),
                self.input_transcript_delta(
                    "Transferring ", event_id="event-cumulative-1"
                ),
                self.input_transcript_delta(
                    "Transferring you now.", event_id="event-cumulative-2"
                ),
                self.input_transcript_delta(
                    "Transferring you now.", event_id="event-cumulative-repeat"
                ),
                self.input_transcript(
                    "Transferring you now.",
                    event_id="event-cumulative-completed",
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "clear"],
        )
        sent = [json.loads(message) for message in openai.sent]
        self.assertEqual(
            len([event for event in sent if event["type"] == "response.cancel"]),
            1,
        )
        self.assertIn("twilio_clear_messages_sent=1", logs.output[0])

    def test_confirmed_transfer_clears_audio_waiting_behind_playback_mark(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.response_created(),
                self.delta(),
                self.audio_done(),
                self.input_transcript_delta("Transferring you now."),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "mark", "clear"],
        )
        diagnostic = logs.output[0]
        self.assertIn("openai_responses_cancelled=1", diagnostic)
        self.assertIn("twilio_playback_marks_sent=1", diagnostic)
        self.assertIn("twilio_clear_messages_sent=1", diagnostic)

    def test_transfer_offer_and_complete_acceptance_finish_without_cancellation(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.input_transcript("Would you like me to transfer you?"),
                self.response_created(),
                self.delta(event_id="event-acceptance-audio"),
                self.output_transcript_delta("Yes, please."),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-unnecessary-follow-up",
                ),
                self.speech_started("event-ordinary-speech-after-acceptance"),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "media"],
        )
        sent_to_openai = [json.loads(message) for message in openai.sent]
        self.assertFalse(
            any(event["type"] == "response.cancel" for event in sent_to_openai)
        )
        diagnostic = logs.output[0]
        self.assertIn("transfer_transitions_detected=0", diagnostic)
        self.assertIn("twilio_clear_messages_sent=0", diagnostic)
        self.assertIn("openai_output_audio_frames_suppressed=0", diagnostic)

    def test_completed_acceptance_without_punctuation_finishes_normally(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.input_transcript("Would you like to be transferred?"),
                self.response_created(),
                self.delta(event_id="event-short-acceptance"),
                self.output_transcript_done("Yes please"),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-follow-up-after-completed-acceptance",
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "media"],
        )
        self.assertFalse(
            any(
                json.loads(message)["type"] == "response.cancel"
                for message in openai.sent
            )
        )

    def test_repeated_and_cumulative_patient_transcript_never_truncates_audio(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.input_transcript("Would you like me to transfer you?"),
                self.response_created(),
                self.delta(event_id="event-acceptance-audio"),
                self.output_transcript_delta(
                    "Yes", event_id="event-output-fragment-1"
                ),
                self.output_transcript_delta(
                    "Yes", event_id="event-output-fragment-repeat"
                ),
                self.output_transcript_delta(
                    "Yes, ", event_id="event-output-cumulative-1"
                ),
                self.output_transcript_delta(
                    "Yes, please.", event_id="event-output-cumulative-2"
                ),
                self.output_transcript_delta(
                    "Yes, please.", event_id="event-output-cumulative-repeat"
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-output-after-acceptance",
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        self.run_bridge(twilio, openai)

        sent = [json.loads(message) for message in openai.sent]
        self.assertFalse(
            any(event["type"] == "response.cancel" for event in sent)
        )
        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "media"],
        )

    def test_punctuated_incomplete_transfer_clauses_never_cancel_output(self):
        incomplete_clauses = (
            "Please connect me to.",
            "Please transfer me to.",
            "Please put me through to.",
            "I'd like to speak with.",
        )
        for index, clause in enumerate(incomplete_clauses):
            with self.subTest(clause=clause):
                twilio = FakeTwilioWebSocket((self.connected(), self.start()))
                openai = FakeOpenAIConnection(
                    (
                        self.input_transcript(
                            "Would you like me to transfer you?",
                            event_id=f"event-offer-{index}",
                        ),
                        self.response_created(),
                        self.delta(event_id=f"event-before-clause-{index}"),
                        self.output_transcript_delta(
                            clause, event_id=f"event-clause-{index}"
                        ),
                        self.delta(
                            payload=self.payload_two,
                            event_id=f"event-after-clause-{index}",
                        ),
                    ),
                    on_empty=lambda: twilio.add_message(self.stop()),
                )

                self.run_bridge(twilio, openai)

                self.assertEqual(
                    [json.loads(message)["event"] for message in twilio.sent],
                    ["media", "media"],
                )
                self.assertFalse(
                    any(
                        json.loads(message)["type"] == "response.cancel"
                        for message in openai.sent
                    )
                )

    def test_multi_sentence_transfer_response_is_not_runtime_truncated(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.input_transcript("Would you like me to transfer you?"),
                self.response_created(),
                self.delta(event_id="event-multi-sentence-audio-1"),
                self.output_transcript_delta(
                    "Yes, please transfer me. I will wait for the next team."
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-multi-sentence-audio-2",
                ),
                self.output_transcript_done(
                    "Yes, please transfer me. I will wait for the next team."
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "media"],
        )
        self.assertFalse(
            any(
                json.loads(message)["type"] == "response.cancel"
                for message in openai.sent
            )
        )

    def test_call_eight_order_finishes_acceptance_until_remote_confirmation(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.input_transcript(
                    "Would you like me to connect you with that team?",
                    event_id="event-call-eight-offer",
                ),
                self.response_created(),
                self.delta(event_id="event-call-eight-audio-1"),
                self.output_transcript_delta(
                    "Please connect me to.",
                    event_id="event-call-eight-incomplete-punctuation",
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-call-eight-audio-2",
                ),
                self.output_transcript_done(
                    "Please connect me to the new-patient team.",
                    event_id="event-call-eight-complete-sentence",
                ),
                self.delta(event_id="event-call-eight-audio-3"),
                self.input_transcript_delta(
                    "Transferring ", event_id="event-call-eight-confirmation-1"
                ),
                self.input_transcript_delta(
                    "you now.", event_id="event-call-eight-confirmation-2"
                ),
                self.delta(
                    payload=self.payload_two,
                    event_id="event-call-eight-audio-after-confirmation",
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "media", "media", "clear"],
        )
        sent = [json.loads(message) for message in openai.sent]
        self.assertEqual(
            [event for event in sent if event["type"] == "response.cancel"],
            [{"type": "response.cancel", "response_id": "response-fictional"}],
        )
        self.assertIn("openai_output_audio_frames_suppressed=1", logs.output[0])
        self.assertIn("twilio_clear_messages_sent=1", logs.output[0])

    def test_transfer_detection_is_general_without_treating_normal_wait_as_transfer(self):
        transfer_announcements = (
            "I am transferring the call now.",
            "Let me put you through to that department.",
            "Please hold while I get the other line.",
            "I will connect you with another team.",
            "I\u2019M TRANSFERRING YOU NOW!",
            "Not a problem. I\u2019m transferring you now.",
            "I’M TRANSFERRING YOU NOW!",
            "One moment — I'll put you through to billing.",
            "Your call is being transferred now.",
            "I'm going to transfer your call now.",
            "You'll be connected to the new line now.",
            "Transferring you now. Thank you.",
        )
        for announcement in transfer_announcements:
            with self.subTest(announcement=announcement):
                self.assertTrue(
                    realtime_bridge._is_transfer_announcement(announcement)
                )
        self.assertTrue(
            realtime_bridge._is_streaming_transfer_announcement(
                "Transferring you now."
            )
        )
        self.assertFalse(
            realtime_bridge._is_streaming_transfer_announcement(
                "I will transfer you"
            )
        )
        non_actions = (
            "Would you like me to transfer you?",
            "Would you like me to transfer you",
            "Do you want me to connect you with billing?",
            "Can I transfer the call?",
            "Should I transfer you now?",
            "We can transfer you if you'd like.",
            "I will transfer you if you want.",
            "I will transfer you after I finish this update.",
            "I will transfer you later.",
            "I will transfer you tomorrow.",
            "I will transfer you in 10 minutes.",
            "We'll connect you once you confirm the department.",
            "If I transfer you, the other team can help.",
            "I'm not transferring you now.",
            "I don't think I'm transferring you now.",
            "I won't be transferring you now.",
            "Please hold while I transfer your prescription.",
            "Please hold while I transfer the pharmacy request.",
            "Please hold while I transfer your medical records.",
            "Please hold while I transfer the data.",
            "I'm transferring your prescription to the pharmacy.",
            "I'm transferring your records to the new clinic.",
            "I'm transferring the data now.",
            "I'm transferring your call data now.",
            "I'm routing the call records now.",
            "The transfer team can answer that question.",
            "Take your time while I pull up the appointment.",
        )
        for statement in non_actions:
            with self.subTest(statement=statement):
                self.assertFalse(
                    realtime_bridge._is_transfer_announcement(statement)
                )

    def test_offers_refusals_and_transfer_discussion_are_nonterminal(self):
        nonterminal_statements = (
            "Would you like me to transfer you?",
            "Would you like me to transfer you",
            "Would you like to be transferred?",
            "Can I connect you with support?",
            "Would it help if I transferred you?",
            "No, thank you. I don't want to be transferred.",
            "I do not want you to transfer me.",
            "I would not like you to transfer me.",
            "If you transfer me, will I need to repeat that?",
            "The transfer team might be able to help.",
            "My prescription was transferred last month.",
            "Could you transfer my prescription to another pharmacy?",
            "I need to transfer my medical records.",
        )
        for statement in nonterminal_statements:
            with self.subTest(statement=statement):
                self.assertFalse(
                    realtime_bridge._is_transfer_announcement(statement)
                )

    def test_only_healthcare_input_transcript_can_activate_transfer(self):
        patient_output_transcript = json.dumps(
            {
                "type": "response.output_audio_transcript.done",
                "event_id": "event-patient-output-transcript",
                "response_id": "response-fictional",
                "item_id": "item-fictional-output",
                "output_index": 0,
                "content_index": 0,
                "transcript": "I am transferring you now.",
            }
        )
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (patient_output_transcript, self.delta()),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media"],
        )
        self.assertIn("transfer_transitions_detected=0", logs.output[0])

    def test_stale_patient_transcript_cannot_cancel_or_suppress_new_response(self):
        old_response = "response-old"
        new_response = "response-new"
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.response_created(old_response),
                self.response_done(old_response),
                self.response_created(new_response),
                self.output_transcript_delta(
                    "Please transfer me.",
                    event_id="event-stale-output-delta",
                    response_id=old_response,
                ),
                self.output_transcript_done(
                    "Please transfer me.",
                    event_id="event-stale-output-done",
                    response_id=old_response,
                ),
                self.delta(
                    event_id="event-new-response-audio",
                    response_id=new_response,
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media"],
        )
        sent = [json.loads(message) for message in openai.sent]
        self.assertFalse(
            any(event["type"] == "response.cancel" for event in sent)
        )
        self.assertIn("openai_responses_cancelled=0", logs.output[0])

    def test_nonterminal_completed_text_recovers_provisional_stream_match(self):
        item_id = "item-corrected-input"
        new_response = "response-after-correction"
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (
                self.response_created(),
                self.delta(event_id="event-before-correction"),
                self.input_transcript_delta(
                    "Transferring you now.",
                    event_id="event-provisional-transfer",
                    item_id=item_id,
                ),
                self.input_transcript(
                    "I'm not transferring you now.",
                    event_id="event-corrected-completion",
                    item_id=item_id,
                ),
                self.response_created(new_response),
                self.delta(
                    event_id="event-after-correction",
                    response_id=new_response,
                ),
            ),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in twilio.sent],
            ["media", "clear", "media"],
        )

    def test_transfer_and_event_id_state_are_isolated_per_call(self):
        shared_event_id = "event-shared-across-calls"
        first_twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        first_openai = FakeOpenAIConnection(
            (
                self.input_transcript(
                    "I am transferring you now.", event_id=shared_event_id
                ),
            ),
            on_empty=lambda: first_twilio.add_message(self.stop()),
        )
        self.run_bridge(first_twilio, first_openai)

        second_twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        second_openai = FakeOpenAIConnection(
            (self.delta(event_id=shared_event_id),),
            on_empty=lambda: second_twilio.add_message(self.stop()),
        )
        self.run_bridge(second_twilio, second_openai)

        self.assertEqual(
            [json.loads(message)["event"] for message in second_twilio.sent],
            ["media"],
        )

    def test_event_id_window_is_bounded_and_retains_recent_duplicates(self):
        event_ids = realtime_bridge._BoundedEventIds(3)

        self.assertTrue(event_ids.remember("event-1"))
        self.assertTrue(event_ids.remember("event-2"))
        self.assertTrue(event_ids.remember("event-3"))
        self.assertFalse(event_ids.remember("event-2"))
        self.assertTrue(event_ids.remember("event-4"))
        self.assertEqual(len(event_ids), 3)
        self.assertTrue(event_ids.remember("event-1"))
        self.assertEqual(len(event_ids), 3)
        with self.assertRaisesRegex(ValueError, "Realtime audio bridge failed"):
            event_ids.remember("x" * 257)

    def test_transcript_delta_merge_handles_fragment_repeat_and_cumulative_text(self):
        self.assertEqual(
            realtime_bridge._merge_transcript_delta("Transferring ", "you now."),
            "Transferring you now.",
        )
        self.assertEqual(
            realtime_bridge._merge_transcript_delta("Trans", "Trans"),
            "Trans",
        )
        self.assertEqual(
            realtime_bridge._merge_transcript_delta("Trans", "Transferring "),
            "Transferring ",
        )
        self.assertEqual(
            realtime_bridge._merge_transcript_delta(
                "Transferring y", "you now."
            ),
            "Transferring you now.",
        )
        with self.assertRaisesRegex(ValueError, "Realtime audio bridge failed"):
            realtime_bridge._merge_transcript_delta(
                "x" * realtime_bridge._MAX_TRANSIENT_TRANSCRIPT_LENGTH,
                "y",
            )

    def test_missing_event_id_fails_privately(self):
        event = json.loads(self.delta())
        del event["event_id"]
        self.assert_private_failure(
            FakeTwilioWebSocket((self.connected(), self.start())),
            FakeOpenAIConnection((json.dumps(event),), end_when_empty=True),
            realtime_bridge.RealtimeBridgeInternalError,
        )

    def test_ga_audio_event_relays_end_to_end_with_counter_only_diagnostics(self):
        twilio = FakeTwilioWebSocket(
            (
                self.connected(),
                self.start(),
                self.media(self.payload_one),
                self.media(self.payload_two),
            )
        )
        openai = FakeOpenAIConnection(
            (self.delta(),), on_empty=lambda: twilio.add_message(self.stop())
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "stopped")
        self.assertEqual(
            [json.loads(message) for message in twilio.sent],
            [
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": self.payload_one},
                }
            ],
        )
        diagnostic = logs.output[0]
        for expected in (
            "twilio_inbound_audio_frames=2",
            "twilio_inbound_audio_bytes=8",
            "openai_input_audio_appends=2",
            "openai_output_audio_delta_frames=1",
            "openai_output_audio_delta_bytes=4",
            "twilio_outbound_media_frames=1",
            "twilio_outbound_media_bytes=4",
            "twilio_clear_messages_sent=0",
            "provider_error_event_types=none",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, diagnostic)
        for private_value in (
            self.payload_one,
            self.payload_two,
            self.account_sid,
            self.stream_sid,
            self.call_sid,
        ):
            self.assertNotIn(private_value, diagnostic)

    def test_provider_error_is_counted_privately_and_fails_bridge(self):
        private_detail = "private-provider-detail"
        provider_error = json.dumps(
            {
                "type": "error",
                "event_id": "event-fictional-error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_value",
                    "message": private_detail,
                    "param": "session.audio.output.format.rate",
                },
            }
        )
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection((provider_error,), end_when_empty=True)

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            with self.assertRaises(
                realtime_bridge.RealtimeBridgeInternalError
            ) as raised:
                self.run_bridge(twilio, openai)

        self.assertEqual(str(raised.exception), "Realtime audio bridge failed.")
        diagnostic = logs.output[0]
        self.assertIn(
            "provider_error_event_types=invalid_request_error:1", diagnostic
        )
        self.assertNotIn(private_detail, diagnostic)
        self.assertNotIn("session.audio.output.format.rate", diagnostic)

    def test_speech_started_does_not_clear_when_interruption_is_disabled(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (self.speech_started(),),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            self.run_bridge(twilio, openai)

        self.assertEqual(twilio.sent, [])
        self.assertIn("openai_speech_started_events=1", logs.output[0])
        self.assertIn("twilio_clear_messages_sent=0", logs.output[0])

    def test_output_audio_is_marked_and_not_truncated_before_playback(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))

        def finish_playback():
            twilio.add_message(self.mark())
            twilio.add_message(self.stop())

        openai = FakeOpenAIConnection(
            (self.delta(), self.speech_started(), self.audio_done()),
            on_empty=finish_playback,
        )

        with self.assertLogs("realtime_bridge", level="INFO") as logs:
            result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "stopped")
        self.assertEqual(
            [json.loads(message) for message in twilio.sent],
            [
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": self.payload_one},
                },
                {
                    "event": "mark",
                    "streamSid": self.stream_sid,
                    "mark": {"name": "response_1_played"},
                },
            ],
        )
        diagnostic = logs.output[0]
        for expected in (
            "openai_output_audio_done_events=1",
            "twilio_playback_marks_sent=1",
            "twilio_playback_marks_acknowledged=1",
            "twilio_playback_marks_pending=0",
            "twilio_clear_messages_sent=0",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, diagnostic)

    def test_well_formed_unused_openai_event_is_ignored(self):
        unused = json.dumps({"type": "rate_limits.updated", "rate_limits": []})
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (unused, self.delta()),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "stopped")
        self.assertEqual(len(twilio.sent), 1)
        self.assertEqual(json.loads(twilio.sent[0])["event"], "media")

    def test_stop_finishes_without_reading_later_message(self):
        twilio = FakeTwilioWebSocket(
            (
                self.connected(),
                self.start(),
                self.stop(),
                self.media(self.payload_one),
            )
        )
        openai = FakeOpenAIConnection()

        result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "stopped")
        self.assertEqual(twilio.receive_count, 3)
        self.assertEqual(len(twilio.messages), 1)

    def test_raw_media_is_released_before_next_receive(self):
        raw_media = self.media(self.payload_one)
        observations = []

        def observe_receive(receive_count):
            frame = inspect.currentframe()
            relay_locals = None
            try:
                while frame is not None:
                    if frame.f_code.co_name == "relay_twilio_to_openai":
                        relay_locals = frame.f_locals
                        break
                    frame = frame.f_back
                observations.append(
                    (
                        receive_count,
                        relay_locals is not None,
                        relay_locals is not None and "message" in relay_locals,
                        relay_locals is not None
                        and raw_media in relay_locals.values(),
                        relay_locals is not None
                        and self.payload_one in relay_locals.values(),
                    )
                )
            finally:
                del relay_locals
                del frame

        twilio = FakeTwilioWebSocket(
            (self.connected(), self.start(), raw_media, self.stop()),
            receive_observer=observe_receive,
        )
        self.run_bridge(twilio, FakeOpenAIConnection())

        before_stop = next(item for item in observations if item[0] == 4)
        self.assertEqual(before_stop, (4, True, False, False, False))

    def test_twilio_disconnect_finishes_cleanly(self):
        twilio = FakeTwilioWebSocket(
            (self.connected(), self.start(), WebSocketDisconnect)
        )
        openai = FakeOpenAIConnection()

        result = self.run_bridge(twilio, openai)

        self.assertEqual(result, "disconnected")
        self.assertEqual(twilio.receive_count, 3)

    def assert_private_failure(self, twilio, openai, exception_type):
        with self.assertRaises(exception_type) as raised:
            self.run_bridge(twilio, openai)
        self.assertEqual(str(raised.exception), "Realtime audio bridge failed.")
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        for private_value in (
            self.account_sid,
            self.stream_sid,
            self.call_sid,
            self.payload_one,
            "private-provider-detail",
        ):
            self.assertNotIn(private_value, str(raised.exception))

    def test_invalid_twilio_order_fails_privately(self):
        self.assert_private_failure(
            FakeTwilioWebSocket((self.start(),)),
            FakeOpenAIConnection(),
            realtime_bridge.RealtimeBridgeProtocolError,
        )

    def test_unknown_scenario_lookup_fails_privately_as_protocol_error(self):
        unknown_scenario_id = "unknown-scenario-private-sentinel"

        class ScenarioPassthroughSession:
            def __init__(self, stream_sid, call_sid):
                self.stream_sid = stream_sid
                self.call_sid = call_sid

            def process_message(self, _message):
                return realtime_bridge.media_protocol.StartResult(
                    stream_sid=self.stream_sid,
                    call_sid=self.call_sid,
                    scenario_id=unknown_scenario_id,
                )

        with patch.object(
            realtime_bridge.media_protocol,
            "MediaProtocolSession",
            return_value=ScenarioPassthroughSession(
                self.stream_sid, self.call_sid
            ),
        ):
            with self.assertRaises(
                realtime_bridge.RealtimeBridgeProtocolError
            ) as raised:
                self.run_bridge(
                    FakeTwilioWebSocket((self.start(),)),
                    FakeOpenAIConnection(),
                )

        self.assertEqual(str(raised.exception), "Realtime audio bridge failed.")
        self.assertNotIn(unknown_scenario_id, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn(unknown_scenario_id, vars(realtime_bridge).values())
        self.assertEqual(bridge_realtime_audio.__dict__, {})

    def test_malformed_supported_openai_events_fail_privately(self):
        malformed_events = (
            "not-json-private-provider-detail",
            json.dumps({"type": "response.output_audio.delta"}),
            json.dumps(
                {
                    "type": "input_audio_buffer.speech_started",
                    "event_id": "private-provider-detail",
                    "audio_start_ms": -1,
                    "item_id": "item-fictional",
                }
            ),
            json.dumps(
                {
                    "type": "response.output_audio.done",
                    "event_id": "private-provider-detail",
                }
            ),
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "event_id": "private-provider-detail",
                    "delta": "private-provider-detail",
                }
            ),
            json.dumps(
                {
                    "type": "response.output_audio_transcript.delta",
                    "event_id": "private-provider-detail",
                    "delta": "private-provider-detail",
                }
            ),
            json.dumps(
                {
                    "type": "response.output_audio_transcript.done",
                    "event_id": "private-provider-detail",
                    "transcript": "private-provider-detail",
                }
            ),
        )
        for message in malformed_events:
            with self.subTest(message=message):
                self.assert_private_failure(
                    FakeTwilioWebSocket((self.connected(), self.start())),
                    FakeOpenAIConnection((message,), end_when_empty=True),
                    realtime_bridge.RealtimeBridgeInternalError,
                )

    def test_unexpected_send_failure_fails_privately(self):
        self.assert_private_failure(
            FakeTwilioWebSocket((self.connected(), self.start())),
            FakeOpenAIConnection(
                send_error=RuntimeError("private-provider-detail")
            ),
            realtime_bridge.RealtimeBridgeInternalError,
        )

    def test_internal_tasks_are_cleaned_up(self):
        twilio = FakeTwilioWebSocket(
            (self.connected(), self.start(), self.stop())
        )
        openai = FakeOpenAIConnection()

        async def observe_tasks():
            current = asyncio.current_task()
            before = {task for task in asyncio.all_tasks() if task is not current}
            await bridge_realtime_audio(twilio, openai, self.account_sid)
            await asyncio.sleep(0)
            after = {task for task in asyncio.all_tasks() if task is not current}
            return before, after

        before, after = asyncio.run(observe_tasks())
        self.assertEqual(after, before)

    def test_bridge_retains_no_payload_or_connection(self):
        twilio = FakeTwilioWebSocket(
            (
                self.connected(),
                self.start(),
                self.media(self.payload_one),
                self.stop(),
            )
        )
        openai = FakeOpenAIConnection()

        self.run_bridge(twilio, openai)

        module_values = tuple(vars(realtime_bridge).values())
        for forbidden in (
            twilio,
            openai,
            self.account_sid,
            self.stream_sid,
            self.call_sid,
            self.scenario_id,
            self.payload_one,
        ):
            self.assertFalse(any(value is forbidden for value in module_values))
            self.assertNotIn(forbidden, module_values)
        self.assertEqual(bridge_realtime_audio.__dict__, {})

    def test_public_helpers_are_reused(self):
        twilio = FakeTwilioWebSocket(
            (
                self.connected(),
                self.start(),
                self.media(self.payload_one),
                self.stop(),
            )
        )
        openai = FakeOpenAIConnection()

        with (
            patch.object(
                realtime_bridge.media_protocol,
                "MediaProtocolSession",
                wraps=realtime_bridge.media_protocol.MediaProtocolSession,
            ) as session_class,
            patch.object(
                realtime_bridge.patient_scenarios,
                "get_scenario",
                wraps=realtime_bridge.patient_scenarios.get_scenario,
            ) as scenario_lookup,
            patch.object(
                realtime_bridge.openai_realtime_protocol,
                "build_session_update",
                wraps=realtime_bridge.openai_realtime_protocol.build_session_update,
            ) as session_builder,
            patch.object(
                realtime_bridge.audio_relay,
                "twilio_media_to_openai_append",
                wraps=realtime_bridge.audio_relay.twilio_media_to_openai_append,
            ) as inbound_relay,
        ):
            self.run_bridge(twilio, openai)

        session_class.assert_called_once_with(self.account_sid)
        scenario_lookup.assert_called_once_with(self.scenario_id)
        session_builder.assert_called_once_with(
            self.scenario_id, "gpt-realtime-2.1", "marin"
        )
        inbound_relay.assert_called_once_with(
            self.media(self.payload_one), self.stream_sid
        )

    def test_outbound_audio_relay_helpers_are_reused(self):
        delta = self.delta()
        speech_started = self.speech_started()
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (delta, speech_started),
            on_empty=lambda: twilio.add_message(self.stop()),
        )

        with (
            patch.object(
                realtime_bridge.audio_relay,
                "openai_delta_to_twilio_media",
                wraps=realtime_bridge.audio_relay.openai_delta_to_twilio_media,
            ) as audio_relay_adapter,
            patch.object(
                realtime_bridge.audio_relay,
                "openai_speech_started_to_twilio_clear",
                wraps=(
                    realtime_bridge.audio_relay
                    .openai_speech_started_to_twilio_clear
                ),
            ) as interruption_adapter,
            patch.object(
                realtime_bridge.openai_realtime_protocol,
                "parse_input_audio_buffer_speech_started",
                wraps=(
                    realtime_bridge.openai_realtime_protocol
                    .parse_input_audio_buffer_speech_started
                ),
            ) as speech_parser,
        ):
            self.run_bridge(twilio, openai)

        audio_relay_adapter.assert_called_once_with(delta, self.stream_sid)
        speech_parser.assert_called_once_with(speech_started)
        interruption_adapter.assert_not_called()

    def test_import_has_no_configuration_connection_or_task_side_effect(self):
        with (
            patch.object(
                config,
                "load_settings",
                side_effect=AssertionError("configuration must not load"),
            ) as loader,
            patch(
                "asyncio.create_task",
                side_effect=AssertionError("task must not be created"),
            ) as task_creator,
        ):
            imported = importlib.reload(realtime_bridge)

        self.assertIs(imported, realtime_bridge)
        loader.assert_not_called()
        task_creator.assert_not_called()
        self.assertNotIn("openai_realtime_connection", vars(realtime_bridge))


if __name__ == "__main__":
    unittest.main()
