import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from twilio.request_validator import RequestValidator

import app as app_module
import twilio_webhooks


@dataclass(frozen=True)
class AsgiResponse:
    status_code: int
    headers: dict
    body: bytes

    @property
    def text(self):
        return self.body.decode("utf-8")


class SignatureParameters:
    """Independent multi-value mapping for generating test signatures."""

    def __init__(self, pairs):
        self._values = {}
        for name, value in pairs:
            self._values.setdefault(name, []).append(value)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def getall(self, name):
        return self._values[name]


async def asgi_request(method, path, query_string="", body="", headers=None):
    request_headers = {
        "content-type": "application/x-www-form-urlencoded",
        **(headers or {}),
    }
    body_bytes = body.encode("utf-8")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string.encode("ascii"),
        "root_path": "",
        "headers": [
            (name.lower().encode("ascii"), value.encode("latin-1"))
            for name, value in request_headers.items()
        ],
        "client": ("offline-test", 1),
        "server": ("internal-proxy", 80),
    }
    received = False
    messages = []

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app_module.app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in start["headers"]
    }
    return AsgiResponse(start["status"], response_headers, response_body)


class TwilioWebhookRouteTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-secret",
            "TWILIO_ACCOUNT_SID": "fictional-account-secret",
            "TWILIO_AUTH_TOKEN": "fictional-webhook-token",
            "TWILIO_FROM_NUMBER": "+12025550123",
            "PUBLIC_BASE_URL": "https://hooks.example.invalid/base",
        }
        self.settings_patch = patch(
            "twilio_webhooks.load_settings",
            return_value=self.settings.copy(),
        )
        self.load_settings = self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def request(self, method, path, query_string="", pairs=(), signature=None, headers=None):
        body = urlencode(list(pairs))
        request_headers = dict(headers or {})
        if signature is not None:
            request_headers["x-twilio-signature"] = signature
        return asyncio.run(
            asgi_request(
                method,
                path,
                query_string=query_string,
                body=body,
                headers=request_headers,
            )
        )

    def signature(self, path, query_string="", pairs=()):
        public_url = f"{self.settings['PUBLIC_BASE_URL']}{path}"
        if query_string:
            public_url = f"{public_url}?{query_string}"
        return RequestValidator(self.settings["TWILIO_AUTH_TOKEN"]).compute_signature(
            public_url,
            SignatureParameters(pairs),
        )

    def assert_private_error(self, response, expected_status):
        self.assertEqual(response.status_code, expected_status)
        forbidden_values = (
            *self.settings.values(),
            "submitted-body-sentinel",
            "submitted-query-sentinel",
            "invalid-signature-sentinel",
        )
        for value in forbidden_values:
            self.assertNotIn(value, response.text)

    def test_health_is_unchanged_and_does_not_load_settings(self):
        self.load_settings.side_effect = AssertionError("Settings must not load")
        response = asyncio.run(asgi_request("GET", "/health", headers={}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, '{"status":"ok"}')
        self.load_settings.assert_not_called()

    def test_all_routes_reject_missing_and_invalid_signatures(self):
        routes = (
            ("/voice", "scenario_id=schedule_routine_visit"),
            ("/calls/status", ""),
            ("/recordings/status", ""),
        )
        pairs = (("CallSid", "submitted-body-sentinel"),)
        for path, query in routes:
            for signature in (None, "invalid-signature-sentinel"):
                with self.subTest(path=path, signature=signature):
                    response = self.request(
                        "POST", path, query, pairs, signature=signature
                    )
                    self.assert_private_error(response, 403)

    def test_voice_accepts_exact_raw_query_and_all_form_parameters(self):
        query = (
            "scenario_id=schedule_routine_visit"
            "&marker=submitted-query-sentinel%2Fwith%20encoding"
        )
        pairs = (
            ("CallSid", "CAfictional"),
            ("FutureField", "submitted-body-sentinel"),
            ("Repeated", "first"),
            ("Repeated", "second"),
        )
        signature = self.signature("/voice", query, pairs)
        response = self.request("POST", "/voice", query, pairs, signature)
        self.assertEqual(response.status_code, 200)

        changed_pairs = pairs[:-1]
        rejected = self.request("POST", "/voice", query, changed_pairs, signature)
        self.assert_private_error(rejected, 403)

        reencoded_query = query.replace("%20", "+")
        rejected = self.request(
            "POST", "/voice", reencoded_query, pairs, signature
        )
        self.assert_private_error(rejected, 403)

    def test_voice_returns_bidirectional_stream_twiml(self):
        query = "scenario_id=schedule_routine_visit"
        pairs = (("CallSid", "CAfictional"),)
        response = self.request(
            "POST",
            "/voice",
            query,
            pairs,
            self.signature("/voice", query, pairs),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/xml"))
        root = ElementTree.fromstring(response.text)
        connect = root.find("Connect")
        self.assertIsNotNone(connect)
        stream = connect.find("Stream")
        self.assertIsNotNone(stream)
        self.assertEqual(
            stream.attrib["url"],
            "wss://hooks.example.invalid/base/media",
        )
        self.assertNotIn("scenario_id", stream.attrib["url"])
        parameter = stream.find("Parameter")
        self.assertEqual(parameter.attrib["name"], "scenario_id")
        self.assertEqual(parameter.attrib["value"], "schedule_routine_visit")

    def test_unknown_scenario_is_checked_after_signature(self):
        query = "scenario_id=unknown-scenario"
        pairs = (("CallSid", "CAfictional"),)
        invalid = self.request(
            "POST", "/voice", query, pairs, "invalid-signature-sentinel"
        )
        self.assert_private_error(invalid, 403)

        valid = self.request(
            "POST",
            "/voice",
            query,
            pairs,
            self.signature("/voice", query, pairs),
        )
        self.assert_private_error(valid, 400)

    def test_callback_routes_accept_signatures_return_204_and_retain_nothing(self):
        pairs = (
            ("CallSid", "submitted-body-sentinel"),
            ("FutureField", "future-value"),
        )
        for path in ("/calls/status", "/recordings/status"):
            with self.subTest(path=path):
                response = self.request(
                    "POST",
                    path,
                    pairs=pairs,
                    signature=self.signature(path, pairs=pairs),
                )
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.body, b"")
        self.assertFalse(hasattr(app_module.app.state, "webhook_data"))
        self.assertFalse(hasattr(twilio_webhooks, "webhook_data"))

    def test_configuration_failures_are_generic(self):
        self.load_settings.side_effect = ValueError("fictional-webhook-token")
        response = self.request(
            "POST",
            "/calls/status",
            pairs=(("CallSid", "submitted-body-sentinel"),),
            signature="invalid-signature-sentinel",
        )
        self.assert_private_error(response, 403)

    def test_only_form_encoded_requests_are_accepted(self):
        response = self.request(
            "POST",
            "/calls/status",
            signature="invalid-signature-sentinel",
            headers={"content-type": "application/json"},
        )
        self.assert_private_error(response, 415)


if __name__ == "__main__":
    unittest.main()
