import asyncio
from contextlib import nullcontext
import importlib
import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from twilio.request_validator import RequestValidator

import app as app_module
import audio_relay
import openai_realtime_connection
import realtime_bridge
import twilio_webhooks


class FakeOpenAIConnection:
    def __init__(self, close_error=None, order=None):
        self.close_error = close_error
        self.close_count = 0
        self.order = order

    async def close(self):
        self.close_count += 1
        if self.order is not None:
            self.order.append("openai_close")
        if self.close_error is not None:
            raise self.close_error


class MediaWebSocketRouteTests(unittest.TestCase):
    account_sid = "ACfictionalaccount"
    api_key = "fictional-openai-secret"

    def setUp(self):
        self.settings = {
            "OPENAI_API_KEY": self.api_key,
            "TWILIO_ACCOUNT_SID": self.account_sid,
            "TWILIO_AUTH_TOKEN": "fictional-twilio-token",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://hooks.example.invalid/base",
        }

    def signature(self):
        return RequestValidator(
            self.settings["TWILIO_AUTH_TOKEN"]
        ).compute_signature("wss://hooks.example.invalid/base/media", {})

    def run_websocket(
        self,
        *,
        signature=None,
        handshake_result=None,
        connection=None,
        connection_error=None,
        bridge_result="stopped",
        bridge_error=None,
        bridge_observer=None,
        twilio_messages=(),
        patch_bridge=True,
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
            {"type": "websocket.receive", "text": message}
            for message in twilio_messages
        )
        sent = []
        received = []
        order = []
        if connection is None:
            connection = FakeOpenAIConnection(order=order)
        elif connection.order is None:
            connection.order = order

        async def receive():
            if incoming:
                message = incoming.pop(0)
            else:
                message = {"type": "websocket.disconnect", "code": 1000}
            received.append(message)
            return message

        async def send(message):
            sent.append(message)
            if message["type"] == "websocket.accept":
                order.append("twilio_accept")
            elif message["type"] == "websocket.close":
                order.append(f"twilio_close_{message['code']}")

        async def connect_side_effect(api_key):
            order.append("openai_connect")
            if connection_error is not None:
                raise connection_error
            return connection

        async def bridge_side_effect(*arguments):
            order.append("bridge")
            if bridge_observer is not None:
                bridge_observer(*arguments)
            if bridge_error is not None:
                raise bridge_error
            return bridge_result

        connector = AsyncMock(side_effect=connect_side_effect)
        bridge = AsyncMock(side_effect=bridge_side_effect)
        bridge_patch = (
            patch.object(
                app_module.realtime_bridge,
                "bridge_realtime_audio",
                bridge,
            )
            if patch_bridge
            else nullcontext()
        )
        if handshake_result is None:
            validation_patch = patch.object(
                twilio_webhooks,
                "load_settings",
                return_value=self.settings.copy(),
            )
        else:
            validation_patch = patch.object(
                app_module.twilio_webhooks,
                "validate_twilio_websocket_handshake",
                return_value=handshake_result,
            )

        with (
            validation_patch as validation_mock,
            patch.object(
                app_module.openai_realtime_connection,
                "create_openai_realtime_connection",
                connector,
            ),
            bridge_patch,
        ):
            asyncio.run(app_module.app(scope, receive, send))

        return {
            "sent": sent,
            "received": received,
            "order": order,
            "connection": connection,
            "connector": connector,
            "bridge": bridge,
            "validation": validation_mock,
        }

    def close_codes(self, result):
        return [
            message["code"]
            for message in result["sent"]
            if message["type"] == "websocket.close"
        ]

    def test_invalid_signatures_never_accept_or_open_openai(self):
        for signature in (None, "invalid-fictional-signature"):
            with self.subTest(signature=signature):
                result = self.run_websocket(signature=signature)
                self.assertEqual(self.close_codes(result), [1008])
                self.assertFalse(
                    any(
                        message["type"] == "websocket.accept"
                        for message in result["sent"]
                    )
                )
                result["connector"].assert_not_awaited()
                result["bridge"].assert_not_awaited()
                self.assertEqual(result["connection"].close_count, 0)

    def test_valid_handshake_accepts_opens_and_bridges_exactly_once(self):
        observations = []

        def observe_bridge(websocket, connection, account_sid):
            observations.append((websocket, connection, account_sid))

        result = self.run_websocket(
            signature=self.signature(), bridge_observer=observe_bridge
        )

        accepts = [
            message
            for message in result["sent"]
            if message["type"] == "websocket.accept"
        ]
        self.assertEqual(len(accepts), 1)
        result["connector"].assert_awaited_once_with(self.api_key)
        result["bridge"].assert_awaited_once()
        self.assertEqual(len(observations), 1)
        websocket, connection, account_sid = observations[0]
        self.assertIs(connection, result["connection"])
        self.assertEqual(account_sid, self.account_sid)
        self.assertEqual(websocket.url.path, "/media")
        self.assertEqual(
            result["order"],
            [
                "twilio_accept",
                "openai_connect",
                "bridge",
                "openai_close",
                "twilio_close_1000",
            ],
        )

    def test_api_key_and_settings_are_released_before_bridge_starts(self):
        handshake_result = {
            "OPENAI_API_KEY": "private-api-key-sentinel",
            "TWILIO_ACCOUNT_SID": self.account_sid,
        }
        observations = []

        def observe_bridge(*_):
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
                        route_locals is not None,
                        route_locals is not None and "settings" in route_locals,
                        route_locals is not None and "api_key" in route_locals,
                        route_locals is not None
                        and any(
                            value is handshake_result
                            for value in route_locals.values()
                        ),
                        route_locals is not None
                        and "private-api-key-sentinel" in route_locals.values(),
                    )
                )
            finally:
                del route_locals
                del frame

        result = self.run_websocket(
            handshake_result=handshake_result,
            bridge_observer=observe_bridge,
        )

        result["connector"].assert_awaited_once_with(
            "private-api-key-sentinel"
        )
        self.assertEqual(observations, [(True, False, False, False, False)])

    def test_clean_stop_closes_twilio_normally_and_openai_once(self):
        result = self.run_websocket(
            signature=self.signature(), bridge_result="stopped"
        )
        self.assertEqual(self.close_codes(result), [1000])
        self.assertEqual(result["connection"].close_count, 1)

    def test_peer_disconnect_returns_without_twilio_close(self):
        result = self.run_websocket(
            signature=self.signature(), bridge_result="disconnected"
        )
        self.assertEqual(self.close_codes(result), [])
        self.assertEqual(result["connection"].close_count, 1)

    def test_protocol_failure_closes_with_1008(self):
        result = self.run_websocket(
            signature=self.signature(),
            bridge_error=realtime_bridge.RealtimeBridgeProtocolError(
                "private-client-message"
            ),
        )
        self.assertEqual(self.close_codes(result), [1008])
        self.assertEqual(result["connection"].close_count, 1)
        self.assertNotIn("private-client-message", repr(result["sent"]))

    def test_unknown_scenario_closes_with_1008_without_retention(self):
        unknown_scenario_id = "unknown-scenario-private-sentinel"

        class ScenarioPassthroughSession:
            def __init__(self):
                self.message_count = 0

            def process_message(self, _message):
                self.message_count += 1
                if self.message_count == 1:
                    return realtime_bridge.media_protocol.ConnectedResult(
                        protocol="Call", version="1.0.0"
                    )
                return realtime_bridge.media_protocol.StartResult(
                    stream_sid="MZfictionalstream",
                    call_sid="CAfictionalcall",
                    scenario_id=unknown_scenario_id,
                )

        connected = json.dumps(
            {"event": "connected", "protocol": "Call", "version": "1.0.0"}
        )
        start = json.dumps(
            {
                "event": "start",
                "start": {
                    "customParameters": {
                        "scenario_id": unknown_scenario_id
                    }
                },
            }
        )
        with patch.object(
            realtime_bridge.media_protocol,
            "MediaProtocolSession",
            return_value=ScenarioPassthroughSession(),
        ):
            result = self.run_websocket(
                signature=self.signature(),
                twilio_messages=(connected, start),
                patch_bridge=False,
            )

        self.assertEqual(self.close_codes(result), [1008])
        close_message = next(
            message
            for message in result["sent"]
            if message["type"] == "websocket.close"
        )
        self.assertNotIn(unknown_scenario_id, close_message.get("reason", ""))
        self.assertEqual(result["connection"].close_count, 1)

        app_values = tuple(vars(app_module).values())
        bridge_values = tuple(vars(realtime_bridge).values())
        state_values = tuple(vars(app_module.app.state).get("_state", {}).values())
        self.assertNotIn(unknown_scenario_id, app_values)
        self.assertNotIn(unknown_scenario_id, bridge_values)
        self.assertNotIn(unknown_scenario_id, state_values)
        self.assertEqual(app_module.media.__dict__, {})
        self.assertEqual(realtime_bridge.bridge_realtime_audio.__dict__, {})

    def test_connection_failure_closes_with_1011_without_bridge_or_close(self):
        result = self.run_websocket(
            signature=self.signature(),
            connection_error=ConnectionError("private-provider-detail"),
        )
        self.assertEqual(self.close_codes(result), [1011])
        result["bridge"].assert_not_awaited()
        self.assertEqual(result["connection"].close_count, 0)
        self.assertNotIn("private-provider-detail", repr(result["sent"]))

    def test_bridge_failure_closes_with_1011_and_closes_openai_once(self):
        result = self.run_websocket(
            signature=self.signature(),
            bridge_error=realtime_bridge.RealtimeBridgeInternalError(
                "private-provider-detail"
            ),
        )
        self.assertEqual(self.close_codes(result), [1011])
        self.assertEqual(result["connection"].close_count, 1)
        self.assertNotIn("private-provider-detail", repr(result["sent"]))

    def test_openai_close_failure_does_not_mask_original_outcome(self):
        for bridge_result, bridge_error, expected_code in (
            ("stopped", None, 1000),
            (
                "stopped",
                realtime_bridge.RealtimeBridgeProtocolError(
                    "private-client-detail"
                ),
                1008,
            ),
            (
                "stopped",
                realtime_bridge.RealtimeBridgeInternalError(
                    "private-provider-detail"
                ),
                1011,
            ),
        ):
            with self.subTest(expected_code=expected_code):
                connection = FakeOpenAIConnection(
                    close_error=RuntimeError("private-close-detail")
                )
                result = self.run_websocket(
                    signature=self.signature(),
                    connection=connection,
                    bridge_result=bridge_result,
                    bridge_error=bridge_error,
                )
                self.assertEqual(self.close_codes(result), [expected_code])
                self.assertEqual(connection.close_count, 1)
                self.assertNotIn("private-close-detail", repr(result["sent"]))

    def test_created_openai_connection_is_closed_once_for_every_bridge_outcome(self):
        cases = (
            ("stopped", None),
            ("disconnected", None),
            (
                "stopped",
                realtime_bridge.RealtimeBridgeProtocolError("private"),
            ),
            (
                "stopped",
                realtime_bridge.RealtimeBridgeInternalError("private"),
            ),
        )
        for bridge_result, bridge_error in cases:
            with self.subTest(
                bridge_result=bridge_result,
                bridge_error=type(bridge_error).__name__,
            ):
                result = self.run_websocket(
                    signature=self.signature(),
                    bridge_result=bridge_result,
                    bridge_error=bridge_error,
                )
                self.assertEqual(result["connection"].close_count, 1)

    def test_route_uses_no_audio_adapter_when_boundaries_are_faked(self):
        with (
            patch.object(
                audio_relay,
                "twilio_media_to_openai_append",
                side_effect=AssertionError("audio must not be processed"),
            ) as inbound,
            patch.object(
                audio_relay,
                "openai_delta_to_twilio_media",
                side_effect=AssertionError("audio must not be processed"),
            ) as outbound,
            patch.object(
                audio_relay,
                "openai_speech_started_to_twilio_clear",
                side_effect=AssertionError("audio must not be processed"),
            ) as interruption,
        ):
            self.run_websocket(signature=self.signature())
        inbound.assert_not_called()
        outbound.assert_not_called()
        interruption.assert_not_called()

    def test_no_private_data_is_retained_in_app_globals_or_state(self):
        handshake_result = {
            "OPENAI_API_KEY": "private-api-key-sentinel",
            "TWILIO_ACCOUNT_SID": self.account_sid,
        }
        result = self.run_websocket(handshake_result=handshake_result)
        module_values = tuple(vars(app_module).values())
        state_values = tuple(vars(app_module.app.state).get("_state", {}).values())
        for value in (
            handshake_result,
            "private-api-key-sentinel",
            self.account_sid,
            result["connection"],
        ):
            self.assertFalse(any(item is value for item in module_values))
            self.assertNotIn(value, module_values)
            self.assertFalse(any(item is value for item in state_values))
            self.assertNotIn(value, state_values)

    def test_import_and_health_do_not_load_settings_or_connect(self):
        connector = AsyncMock(
            side_effect=AssertionError("OpenAI must not connect")
        )
        with (
            patch.object(
                twilio_webhooks,
                "load_settings",
                side_effect=AssertionError("settings must not load"),
            ) as loader,
            patch.object(
                openai_realtime_connection,
                "create_openai_realtime_connection",
                connector,
            ),
        ):
            imported = importlib.reload(app_module)
            self.assertEqual(imported.health(), {"status": "ok"})

        loader.assert_not_called()
        connector.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
