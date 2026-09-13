import unittest
from unittest.mock import Mock, patch

from twilio.request_validator import RequestValidator

import twilio_webhooks
from twilio_webhooks import validate_twilio_websocket_handshake


class FakeHeaders:
    def __init__(self, values=()):
        self._values = {name.lower(): value for name, value in values}

    def get(self, name, default=None):
        return self._values.get(name.lower(), default)


class FakeWebSocket:
    def __init__(self, raw_path=b"/media", query_string=b"", signature=None):
        self.scope = {
            "type": "websocket",
            "raw_path": raw_path,
            "query_string": query_string,
        }
        headers = () if signature is None else (("X-Twilio-Signature", signature),)
        self.headers = FakeHeaders(headers)
        self.accept_count = 0
        self.close_count = 0

    async def accept(self):
        self.accept_count += 1

    async def close(self):
        self.close_count += 1


class TwilioWebSocketValidationTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-key",
            "TWILIO_ACCOUNT_SID": "ACfictional-account",
            "TWILIO_AUTH_TOKEN": "fictional-auth-token",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://hooks.example.invalid/base",
        }

    def signature(self, raw_path="/media", raw_query=""):
        url = f"wss://hooks.example.invalid/base{raw_path}"
        if raw_query:
            url = f"{url}?{raw_query}"
        return RequestValidator(self.settings["TWILIO_AUTH_TOKEN"]).compute_signature(
            url, {}
        )

    def websocket(self, raw_path=b"/media", raw_query=b"", signature=None):
        return FakeWebSocket(raw_path, raw_query, signature)

    def invoke(self, websocket):
        with patch(
            "twilio_webhooks.load_settings", return_value=self.settings.copy()
        ):
            return validate_twilio_websocket_handshake(websocket)

    def assert_private_error(self, function, *args):
        forbidden_values = (
            *self.settings.values(),
            "invalid-signature-sentinel",
            "submitted-query-sentinel",
            "submitted-path-sentinel",
            "validator-exception-sentinel",
        )
        with self.assertRaises(ValueError) as raised:
            function(*args)
        self.assertEqual(str(raised.exception), "WebSocket handshake rejected.")
        for value in forbidden_values:
            self.assertNotIn(value, str(raised.exception))

    def test_valid_exact_wss_signature_returns_only_future_route_settings(self):
        websocket = self.websocket(signature=self.signature())
        result = self.invoke(websocket)
        self.assertEqual(
            result,
            {
                "OPENAI_API_KEY": self.settings["OPENAI_API_KEY"],
                "TWILIO_ACCOUNT_SID": self.settings["TWILIO_ACCOUNT_SID"],
            },
        )
        self.assertNotIn("TWILIO_AUTH_TOKEN", result)
        self.assertNotIn("PUBLIC_BASE_URL", result)

    def test_base_path_and_https_to_wss_conversion_are_exact(self):
        validator = Mock()
        validator.validate.return_value = True
        websocket = self.websocket(
            raw_path=b"/media/subpath",
            raw_query=b"scenario=fictional%2Fencoded",
            signature="fictional-signature",
        )
        with (
            patch("twilio_webhooks.load_settings", return_value=self.settings.copy()),
            patch("twilio_webhooks.RequestValidator", return_value=validator) as factory,
        ):
            validate_twilio_websocket_handshake(websocket)
        factory.assert_called_once_with(self.settings["TWILIO_AUTH_TOKEN"])
        validator.validate.assert_called_once_with(
            "wss://hooks.example.invalid/base/media/subpath"
            "?scenario=fictional%2Fencoded",
            {},
            "fictional-signature",
        )

    def test_exact_raw_path_and_query_encoding_affect_validation(self):
        raw_path = "/media/submitted-path-sentinel%2Fsegment"
        raw_query = "value=submitted-query-sentinel%2Fencoded"
        signature = self.signature(raw_path, raw_query)

        valid = self.websocket(raw_path.encode(), raw_query.encode(), signature)
        self.invoke(valid)

        changed_path = self.websocket(
            raw_path.replace("%2F", "%2f").encode(), raw_query.encode(), signature
        )
        self.assert_private_error(self.invoke, changed_path)

        changed_query = self.websocket(
            raw_path.encode(), raw_query.replace("%2F", "%2f").encode(), signature
        )
        self.assert_private_error(self.invoke, changed_query)

    def test_missing_and_invalid_signatures_fail_privately(self):
        for signature in (None, "", " ", "invalid-signature-sentinel"):
            with self.subTest(signature=signature):
                websocket = self.websocket(signature=signature)
                self.assert_private_error(self.invoke, websocket)

    def test_malformed_settings_and_validator_exceptions_share_one_error(self):
        websocket = self.websocket(signature="invalid-signature-sentinel")
        malformed_settings = self.settings.copy()
        malformed_settings["PUBLIC_BASE_URL"] = "http://submitted-query-sentinel"
        with patch(
            "twilio_webhooks.load_settings", return_value=malformed_settings
        ):
            self.assert_private_error(validate_twilio_websocket_handshake, websocket)

        failures = (
            ValueError("fictional-auth-token"),
            KeyError("PUBLIC_BASE_URL"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("twilio_webhooks.load_settings", side_effect=failure):
                    self.assert_private_error(
                        validate_twilio_websocket_handshake, websocket
                    )

        with (
            patch("twilio_webhooks.load_settings", return_value=self.settings.copy()),
            patch(
                "twilio_webhooks.RequestValidator",
                side_effect=RuntimeError("validator-exception-sentinel"),
            ),
        ):
            self.assert_private_error(validate_twilio_websocket_handshake, websocket)

        validator = Mock()
        validator.validate.side_effect = RuntimeError("validator-exception-sentinel")
        with (
            patch("twilio_webhooks.load_settings", return_value=self.settings.copy()),
            patch("twilio_webhooks.RequestValidator", return_value=validator),
        ):
            self.assert_private_error(validate_twilio_websocket_handshake, websocket)

    def test_malformed_raw_paths_and_queries_fail_privately(self):
        malformed_scopes = (
            (None, b""),
            ("/media", b""),
            (b"media", b""),
            (b"/media%2", b""),
            (b"/media?value", b""),
            (b"/media", "value=one"),
            (b"/media", b"value=%GG"),
            (b"/media", b"value=one#fragment"),
        )
        for raw_path, raw_query in malformed_scopes:
            with self.subTest(raw_path=raw_path, raw_query=raw_query):
                websocket = self.websocket(
                    raw_path, raw_query, "invalid-signature-sentinel"
                )
                self.assert_private_error(self.invoke, websocket)

    def test_settings_load_only_when_helper_is_invoked(self):
        signature = self.signature()
        websocket = self.websocket(signature=signature)
        with patch(
            "twilio_webhooks.load_settings", return_value=self.settings.copy()
        ) as loader:
            loader.assert_not_called()
            validate_twilio_websocket_handshake(websocket)
            loader.assert_called_once_with()

    def test_helper_neither_accepts_nor_closes_websocket(self):
        websocket = self.websocket(signature=self.signature())
        self.invoke(websocket)
        self.assertEqual(websocket.accept_count, 0)
        self.assertEqual(websocket.close_count, 0)

    def test_handshake_data_is_not_retained(self):
        raw_query = b"value=submitted-query-sentinel%2Fencoded"
        signature = self.signature("/media", raw_query.decode())
        websocket = self.websocket(raw_query=raw_query, signature=signature)
        result = self.invoke(websocket)

        self.assertEqual(set(result), {"OPENAI_API_KEY", "TWILIO_ACCOUNT_SID"})
        module_values = tuple(vars(twilio_webhooks).values())
        self.assertNotIn(signature, module_values)
        self.assertNotIn(raw_query, module_values)
        self.assertNotIn(websocket, module_values)
        self.assertEqual(validate_twilio_websocket_handshake.__dict__, {})


if __name__ == "__main__":
    unittest.main()
