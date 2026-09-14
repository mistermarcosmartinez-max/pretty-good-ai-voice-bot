import asyncio
import importlib
import inspect
import json
import unittest
from unittest.mock import patch

from twilio.request_validator import RequestValidator

import app as app_module
import audio_relay
import twilio_webhooks


class MediaWebSocketRouteTests(unittest.TestCase):
    account_sid = "ACfictionalaccount"
    stream_sid = "MZfictionalstream"
    call_sid = "CAfictionalcall"
    scenario_id = "schedule_routine_visit"
    payload = "AQIDBA=="

    def setUp(self):
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-secret",
            "TWILIO_ACCOUNT_SID": self.account_sid,
            "TWILIO_AUTH_TOKEN": "fictional-twilio-token",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://hooks.example.invalid/base",
        }

    def signature(self, path="/media", query=""):
        url = f"wss://hooks.example.invalid/base{path}"
        if query:
            url = f"{url}?{query}"
        return RequestValidator(self.settings["TWILIO_AUTH_TOKEN"]).compute_signature(
            url, {}
        )

    def connected(self):
        return json.dumps(
            {"event": "connected", "protocol": "Call", "version": "1.0.0"}
        )

    def start(self, *, stream_sid=None):
        selected_stream_sid = self.stream_sid if stream_sid is None else stream_sid
        return json.dumps(
            {
                "event": "start",
                "streamSid": selected_stream_sid,
                "start": {
                    "accountSid": self.account_sid,
                    "streamSid": selected_stream_sid,
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

    def media(self, *, stream_sid=None, payload=None):
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

    def stop(self, *, stream_sid=None):
        return json.dumps(
            {
                "event": "stop",
                "streamSid": self.stream_sid if stream_sid is None else stream_sid,
            }
        )

    def run_websocket(
        self,
        events=(),
        signature=None,
        disconnect=False,
        handshake_result=None,
        receive_observer=None,
    ):
        headers = []
        if signature is not None:
            headers.append((b"x-twilio-signature", signature.encode("ascii")))
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/media",
            "raw_path": b"/media",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("offline-test", 1),
            "server": ("internal-proxy", 80),
            "subprotocols": [],
        }
        incoming = [{"type": "websocket.connect"}]
        incoming.extend(
            {"type": "websocket.receive", "text": event} for event in events
        )
        if disconnect:
            incoming.append({"type": "websocket.disconnect", "code": 1000})
        sent = []
        received = []

        async def receive():
            if receive_observer is not None:
                receive_observer(len(received))
            if incoming:
                message = incoming.pop(0)
            else:
                message = {"type": "websocket.disconnect", "code": 1000}
            received.append(message)
            return message

        async def send(message):
            sent.append(message)

        if handshake_result is None:
            validation_patch = patch(
                "twilio_webhooks.load_settings", return_value=self.settings.copy()
            )
        else:
            validation_patch = patch(
                "app.twilio_webhooks.validate_twilio_websocket_handshake",
                return_value=handshake_result,
            )
        with validation_patch as validation_mock:
            asyncio.run(app_module.app(scope, receive, send))
        return sent, received, validation_mock

    def close_codes(self, sent):
        return [message["code"] for message in sent if message["type"] == "websocket.close"]

    def test_missing_and_invalid_signatures_reject_before_accept(self):
        for signature in (None, "invalid-fictional-signature"):
            with self.subTest(signature=signature):
                sent, _, loader = self.run_websocket(signature=signature)
                self.assertEqual(sent, [{"type": "websocket.close", "code": 1008, "reason": ""}])
                self.assertFalse(
                    any(message["type"] == "websocket.accept" for message in sent)
                )
                loader.assert_called_once_with()

    def test_valid_signature_accepts_exactly_once(self):
        sent, _, _ = self.run_websocket(
            events=(self.connected(),), signature=self.signature(), disconnect=True
        )
        accepts = [message for message in sent if message["type"] == "websocket.accept"]
        self.assertEqual(len(accepts), 1)

    def test_valid_sequence_with_two_media_events_closes_normally(self):
        events = (
            self.connected(),
            self.start(),
            self.media(),
            self.media(),
            self.stop(),
        )
        sent, received, _ = self.run_websocket(events, self.signature())
        self.assertEqual(self.close_codes(sent), [1000])
        self.assertEqual(
            sum(message["type"] == "websocket.receive" for message in received),
            len(events),
        )

    def test_events_before_connected_are_rejected(self):
        for event in (self.start(), self.media(), self.stop()):
            with self.subTest(event=event):
                sent, _, _ = self.run_websocket((event,), self.signature())
                self.assertEqual(self.close_codes(sent), [1008])

    def test_invalid_order_and_duplicate_events_are_rejected(self):
        cases = (
            (self.connected(), self.media()),
            (self.connected(), self.connected()),
            (self.connected(), self.start(), self.connected()),
            (self.connected(), self.start(), self.start()),
        )
        for events in cases:
            with self.subTest(events=events):
                sent, _, _ = self.run_websocket(events, self.signature())
                self.assertEqual(self.close_codes(sent), [1008])

    def test_malformed_messages_wrong_stream_and_invalid_audio_are_rejected(self):
        cases = (
            ("not-json",),
            (self.connected(), self.start(), self.media(stream_sid="MZwrong")),
            (self.connected(), self.start(), self.media(payload="not*base64")),
            (self.connected(), self.start(), self.media(payload="")),
        )
        for events in cases:
            with self.subTest(events=events):
                sent, _, _ = self.run_websocket(events, self.signature())
                self.assertEqual(self.close_codes(sent), [1008])

    def test_stop_closes_before_a_post_stop_message_is_processed(self):
        events = (
            self.connected(),
            self.start(),
            self.stop(),
            self.media(),
        )
        sent, received, _ = self.run_websocket(events, self.signature())
        self.assertEqual(self.close_codes(sent), [1000])
        self.assertEqual(
            sum(message["type"] == "websocket.receive" for message in received), 3
        )

    def test_peer_disconnect_returns_cleanly(self):
        sent, _, _ = self.run_websocket(
            (self.connected(), self.start()), self.signature(), disconnect=True
        )
        self.assertEqual(
            [message["type"] for message in sent], ["websocket.accept"]
        )

    def test_route_sends_no_application_messages(self):
        sent, _, _ = self.run_websocket(
            (self.connected(), self.start(), self.media(), self.stop()),
            self.signature(),
        )
        self.assertFalse(any(message["type"] == "websocket.send" for message in sent))

    def test_route_never_invokes_audio_relay(self):
        with (
            patch.object(
                audio_relay,
                "twilio_media_to_openai_append",
                side_effect=AssertionError("relay must not run"),
            ) as inbound,
            patch.object(
                audio_relay,
                "openai_delta_to_twilio_media",
                side_effect=AssertionError("relay must not run"),
            ) as outbound,
            patch.object(
                audio_relay,
                "openai_speech_started_to_twilio_clear",
                side_effect=AssertionError("relay must not run"),
            ) as interruption,
        ):
            self.run_websocket(
                (self.connected(), self.start(), self.media(), self.stop()),
                self.signature(),
            )
        inbound.assert_not_called()
        outbound.assert_not_called()
        interruption.assert_not_called()

    def test_unexpected_internal_failure_closes_with_1011(self):
        with patch(
            "app.media_protocol.MediaProtocolSession",
            side_effect=RuntimeError("private-internal-error"),
        ):
            sent, _, _ = self.run_websocket(signature=self.signature())
        self.assertEqual(self.close_codes(sent), [1011])
        self.assertNotIn("private-internal-error", repr(sent))

    def test_import_and_health_do_not_load_settings(self):
        with patch(
            "twilio_webhooks.load_settings",
            side_effect=AssertionError("settings must not load"),
        ) as loader:
            importlib.reload(app_module)
            self.assertEqual(app_module.health(), {"status": "ok"})
            loader.assert_not_called()

    def test_no_data_is_retained_in_app_globals_or_state(self):
        events = (
            self.connected(),
            self.start(),
            self.media(),
            self.stop(),
        )
        self.run_websocket(events, self.signature())
        forbidden_values = (
            self.account_sid,
            self.stream_sid,
            self.call_sid,
            self.scenario_id,
            self.payload,
            *self.settings.values(),
            *events,
        )
        module_values = tuple(vars(app_module).values())
        state_values = tuple(vars(app_module.app.state).get("_state", {}).values())
        for value in forbidden_values:
            self.assertNotIn(value, module_values)
            self.assertNotIn(value, state_values)

    def test_settings_mapping_and_openai_key_are_released_before_accept(self):
        openai_key = "unused-openai-key-sentinel"
        handshake_result = {
            "OPENAI_API_KEY": openai_key,
            "TWILIO_ACCOUNT_SID": self.account_sid,
        }
        observations = []

        def observe_receive(receive_index):
            frame = inspect.currentframe()
            route_locals = None
            try:
                while frame is not None:
                    if frame.f_code is app_module.media.__code__:
                        route_locals = frame.f_locals
                        break
                    frame = frame.f_back
                observations.append(
                    (
                        receive_index,
                        route_locals is not None,
                        route_locals is not None and "settings" in route_locals,
                        route_locals is not None
                        and any(
                            value is handshake_result for value in route_locals.values()
                        ),
                        route_locals is not None
                        and openai_key in route_locals.values(),
                    )
                )
            finally:
                del route_locals
                del frame

        self.run_websocket(
            events=(self.connected(), self.start(), self.stop()),
            handshake_result=handshake_result,
            receive_observer=observe_receive,
        )

        self.assertTrue(observations)
        for _, found_route, has_settings, has_mapping, has_openai_key in observations:
            self.assertTrue(found_route)
            self.assertFalse(has_settings)
            self.assertFalse(has_mapping)
            self.assertFalse(has_openai_key)

    def test_processed_media_message_is_released_before_next_receive(self):
        raw_media_message = self.media()
        observations = []

        def observe_receive(receive_index):
            frame = inspect.currentframe()
            route_locals = None
            try:
                while frame is not None:
                    if frame.f_code is app_module.media.__code__:
                        route_locals = frame.f_locals
                        break
                    frame = frame.f_back
                observations.append(
                    (
                        receive_index,
                        route_locals is not None,
                        route_locals is not None and "message" in route_locals,
                        route_locals is not None
                        and raw_media_message in route_locals.values(),
                        route_locals is not None
                        and self.payload in route_locals.values(),
                    )
                )
            finally:
                del route_locals
                del frame

        self.run_websocket(
            events=(
                self.connected(),
                self.start(),
                raw_media_message,
                self.stop(),
            ),
            signature=self.signature(),
            receive_observer=observe_receive,
        )

        before_stop = next(item for item in observations if item[0] == 4)
        _, found_route, has_message, has_raw_media, has_payload = before_stop
        self.assertTrue(found_route)
        self.assertFalse(has_message)
        self.assertFalse(has_raw_media)
        self.assertFalse(has_payload)


if __name__ == "__main__":
    unittest.main()
