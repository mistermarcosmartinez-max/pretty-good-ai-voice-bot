import asyncio
import importlib
import json
import unittest
from unittest.mock import patch

from starlette.websockets import WebSocketDisconnect

import config
import realtime_bridge
from realtime_bridge import bridge_realtime_audio


class FakeTwilioWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.receive_count = 0
        self.accept_count = 0
        self.close_count = 0
        self._blocked = asyncio.Event()

    async def receive_text(self):
        self.receive_count += 1
        if self.messages:
            message = self.messages.pop(0)
            if message is WebSocketDisconnect:
                raise WebSocketDisconnect(code=1000)
            return message
        await self._blocked.wait()

    async def send_text(self, message):
        self.sent.append(message)

    async def accept(self):
        self.accept_count += 1

    async def close(self):
        self.close_count += 1


class FakeOpenAIConnection:
    def __init__(self, messages=(), end_when_empty=False, send_error=None):
        self.messages = list(messages)
        self.sent = []
        self.end_when_empty = end_when_empty
        self.send_error = send_error
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

    def delta(self, payload=None):
        return json.dumps(
            {
                "type": "response.output_audio.delta",
                "delta": self.payload_one if payload is None else payload,
            }
        )

    def speech_started(self):
        return json.dumps(
            {
                "type": "input_audio_buffer.speech_started",
                "event_id": "event-fictional",
                "audio_start_ms": 120,
                "item_id": "item-fictional",
            }
        )

    def run_bridge(self, twilio, openai):
        asyncio.run(bridge_realtime_audio(twilio, openai, self.account_sid))

    def test_start_sends_exactly_one_session_update(self):
        twilio = FakeTwilioWebSocket(
            (self.connected(), self.start(), self.stop())
        )
        openai = FakeOpenAIConnection()

        self.run_bridge(twilio, openai)

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
        openai = FakeOpenAIConnection((self.delta(),), end_when_empty=True)

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

    def test_speech_started_sends_twilio_clear(self):
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (self.speech_started(),), end_when_empty=True
        )

        self.run_bridge(twilio, openai)

        self.assertEqual(
            [json.loads(message) for message in twilio.sent],
            [{"event": "clear", "streamSid": self.stream_sid}],
        )

    def test_well_formed_unused_openai_event_is_ignored(self):
        unused = json.dumps({"type": "response.created", "response": {}})
        twilio = FakeTwilioWebSocket((self.connected(), self.start()))
        openai = FakeOpenAIConnection(
            (unused, self.delta()), end_when_empty=True
        )

        self.run_bridge(twilio, openai)

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

        self.run_bridge(twilio, openai)

        self.assertEqual(twilio.receive_count, 3)
        self.assertEqual(len(twilio.messages), 1)

    def test_twilio_disconnect_finishes_cleanly(self):
        twilio = FakeTwilioWebSocket(
            (self.connected(), self.start(), WebSocketDisconnect)
        )
        openai = FakeOpenAIConnection()

        self.run_bridge(twilio, openai)

        self.assertEqual(twilio.receive_count, 3)

    def assert_private_failure(self, twilio, openai):
        with self.assertRaises(ValueError) as raised:
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
            FakeTwilioWebSocket((self.start(),)), FakeOpenAIConnection()
        )

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
        )
        for message in malformed_events:
            with self.subTest(message=message):
                self.assert_private_failure(
                    FakeTwilioWebSocket((self.connected(), self.start())),
                    FakeOpenAIConnection((message,), end_when_empty=True),
                )

    def test_unexpected_send_failure_fails_privately(self):
        self.assert_private_failure(
            FakeTwilioWebSocket((self.connected(), self.start())),
            FakeOpenAIConnection(
                send_error=RuntimeError("private-provider-detail")
            ),
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
            (delta, speech_started), end_when_empty=True
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
        ):
            self.run_bridge(twilio, openai)

        audio_relay_adapter.assert_called_once_with(delta, self.stream_sid)
        interruption_adapter.assert_called_once_with(
            speech_started, self.stream_sid
        )

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
