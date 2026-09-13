from dataclasses import FrozenInstanceError
import json
import unittest

from media_protocol import (
    MediaProtocolSession,
    build_clear_message,
    build_mark_message,
    build_media_message,
    parse_connected_message,
    parse_inbound_media_message,
    parse_start_message,
    parse_stop_message,
)


class MediaProtocolTests(unittest.TestCase):
    account_sid = "ACfictionalaccount"
    stream_sid = "MZfictionalstream"
    call_sid = "CAfictionalcall"
    scenario_id = "schedule_routine_visit"
    payload = "AQIDBA=="

    def connected_message(self, **changes):
        message = {
            "event": "connected",
            "protocol": "Call",
            "version": "1.0.0",
        }
        message.update(changes)
        return json.dumps(message)

    def start_message(self, **start_changes):
        start = {
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
        }
        start.update(start_changes)
        return json.dumps(
            {"event": "start", "streamSid": self.stream_sid, "start": start}
        )

    def media_message(self, **media_changes):
        media = {"track": "inbound", "payload": self.payload}
        media.update(media_changes)
        return json.dumps(
            {"event": "media", "streamSid": self.stream_sid, "media": media}
        )

    def assert_private_error(self, function, *args):
        supplied_values = (
            self.account_sid,
            self.stream_sid,
            self.call_sid,
            self.scenario_id,
            self.payload,
            "submitted-message-sentinel",
            "unknown-scenario-sentinel",
            "ACwrong-account-sentinel",
            "MZwrong-stream-sentinel",
            "+12025550123",
            "fictional-secret-sentinel",
        )
        with self.assertRaises(ValueError) as raised:
            function(*args)
        message = str(raised.exception)
        for value in supplied_values:
            self.assertNotIn(value, message)

    def test_connected_message_is_accepted_and_result_is_frozen(self):
        result = parse_connected_message(self.connected_message())
        self.assertEqual(result.protocol, "Call")
        self.assertEqual(result.version, "1.0.0")
        with self.assertRaises(FrozenInstanceError):
            result.version = "changed"

    def test_connected_rejects_malformed_json_and_wrong_fields(self):
        invalid_messages = (
            "submitted-message-sentinel",
            "[]",
            self.connected_message(event="start"),
            self.connected_message(protocol="Message"),
            self.connected_message(version="2.0.0"),
        )
        for message in invalid_messages:
            with self.subTest(message=message):
                self.assert_private_error(parse_connected_message, message)

    def test_start_message_is_accepted_and_result_is_frozen(self):
        result = parse_start_message(self.start_message(), self.account_sid)
        self.assertEqual(result.stream_sid, self.stream_sid)
        self.assertEqual(result.call_sid, self.call_sid)
        self.assertEqual(result.scenario_id, self.scenario_id)
        with self.assertRaises(FrozenInstanceError):
            result.stream_sid = "changed"

    def test_start_rejects_wrong_event_order_and_missing_identifiers(self):
        wrong_event = json.loads(self.start_message())
        wrong_event["event"] = "connected"
        invalid_messages = (
            json.dumps(wrong_event),
            self.start_message(streamSid=""),
            self.start_message(callSid=""),
            "submitted-message-sentinel",
        )
        for message in invalid_messages:
            with self.subTest(message=message):
                self.assert_private_error(
                    parse_start_message, message, self.account_sid
                )

    def test_start_rejects_mismatched_stream_and_account_identifiers(self):
        mismatched_stream = json.loads(self.start_message())
        mismatched_stream["streamSid"] = "MZwrong-stream-sentinel"
        self.assert_private_error(
            parse_start_message,
            json.dumps(mismatched_stream),
            self.account_sid,
        )
        self.assert_private_error(
            parse_start_message,
            self.start_message(accountSid="ACwrong-account-sentinel"),
            self.account_sid,
        )
        self.assert_private_error(
            parse_start_message,
            self.start_message(accountSid=""),
            "",
        )

    def test_start_accepts_only_the_inbound_track(self):
        for tracks in ([], ["outbound"], ["inbound", "outbound"], "inbound"):
            with self.subTest(tracks=tracks):
                self.assert_private_error(
                    parse_start_message,
                    self.start_message(tracks=tracks),
                    self.account_sid,
                )

    def test_start_requires_exact_media_format(self):
        invalid_formats = (
            None,
            {"encoding": "audio/pcm", "sampleRate": 8000, "channels": 1},
            {"encoding": "audio/x-mulaw", "sampleRate": 16000, "channels": 1},
            {"encoding": "audio/x-mulaw", "sampleRate": "8000", "channels": 1},
            {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 2},
            {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": True},
        )
        for media_format in invalid_formats:
            with self.subTest(media_format=media_format):
                self.assert_private_error(
                    parse_start_message,
                    self.start_message(mediaFormat=media_format),
                    self.account_sid,
                )

    def test_start_gets_scenario_only_from_custom_parameters(self):
        top_level_only = json.loads(
            self.start_message(customParameters={})
        )
        top_level_only["scenario_id"] = self.scenario_id
        self.assert_private_error(
            parse_start_message,
            json.dumps(top_level_only),
            self.account_sid,
        )

        custom_wins = json.loads(self.start_message())
        custom_wins["scenario_id"] = "unknown-scenario-sentinel"
        result = parse_start_message(json.dumps(custom_wins), self.account_sid)
        self.assertEqual(result.scenario_id, self.scenario_id)

    def test_start_rejects_unknown_scenario_without_echoing_it(self):
        self.assert_private_error(
            parse_start_message,
            self.start_message(
                customParameters={"scenario_id": "unknown-scenario-sentinel"}
            ),
            self.account_sid,
        )

    def test_inbound_media_returns_only_validated_base64_text(self):
        result = parse_inbound_media_message(self.media_message(), self.stream_sid)
        self.assertEqual(result, self.payload)
        self.assertIsInstance(result, str)

    def test_inbound_media_rejects_wrong_event_stream_and_track(self):
        wrong_event = json.loads(self.media_message())
        wrong_event["event"] = "start"
        wrong_stream = json.loads(self.media_message())
        wrong_stream["streamSid"] = "MZwrong-stream-sentinel"
        invalid_messages = (
            json.dumps(wrong_event),
            json.dumps(wrong_stream),
            self.media_message(track="outbound"),
        )
        for message in invalid_messages:
            with self.subTest(message=message):
                self.assert_private_error(
                    parse_inbound_media_message, message, self.stream_sid
                )

    def test_inbound_media_rejects_empty_or_invalid_base64(self):
        invalid_payloads = (
            "",
            " ",
            "not*base64",
            "A===",
            "AAA",
            "AQIDBA==\n",
            None,
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assert_private_error(
                    parse_inbound_media_message,
                    self.media_message(payload=payload),
                    self.stream_sid,
                )

    def test_media_and_stop_reject_empty_expected_stream_identifiers(self):
        empty_media = json.dumps(
            {
                "event": "media",
                "streamSid": "",
                "media": {"track": "inbound", "payload": self.payload},
            }
        )
        empty_stop = json.dumps({"event": "stop", "streamSid": ""})
        self.assert_private_error(parse_inbound_media_message, empty_media, "")
        self.assert_private_error(parse_stop_message, empty_stop, "")

    def test_media_parser_rejects_malformed_json(self):
        self.assert_private_error(
            parse_inbound_media_message,
            "submitted-message-sentinel",
            self.stream_sid,
        )

    def test_stop_returns_none_and_rejects_wrong_event_or_stream(self):
        valid = json.dumps(
            {
                "event": "stop",
                "streamSid": self.stream_sid,
                "stop": {"private": "submitted-message-sentinel"},
            }
        )
        self.assertIsNone(parse_stop_message(valid, self.stream_sid))

        wrong_event = json.dumps({"event": "media", "streamSid": self.stream_sid})
        wrong_stream = json.dumps(
            {"event": "stop", "streamSid": "MZwrong-stream-sentinel"}
        )
        for message in (wrong_event, wrong_stream, "submitted-message-sentinel"):
            with self.subTest(message=message):
                self.assert_private_error(
                    parse_stop_message, message, self.stream_sid
                )

    def test_builders_return_exact_twilio_message_dictionaries(self):
        self.assertEqual(
            build_media_message(self.stream_sid, self.payload),
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": self.payload},
            },
        )
        self.assertEqual(
            build_mark_message(self.stream_sid, "response_1.done"),
            {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": "response_1.done"},
            },
        )
        self.assertEqual(
            build_clear_message(self.stream_sid),
            {"event": "clear", "streamSid": self.stream_sid},
        )

    def test_builders_reject_invalid_inputs_without_exposing_them(self):
        invalid_cases = (
            (build_media_message, "", self.payload),
            (build_media_message, self.stream_sid, "fictional-secret-sentinel"),
            (build_mark_message, "", "safe-name"),
            (build_mark_message, self.stream_sid, "unsafe name"),
            (build_mark_message, self.stream_sid, "unsafe/name"),
            (build_mark_message, self.stream_sid, "x" * 65),
            (build_clear_message, ""),
        )
        for function, *args in invalid_cases:
            with self.subTest(function=function.__name__, args=args):
                self.assert_private_error(function, *args)

    def test_errors_do_not_expose_identifiers_phone_numbers_or_secrets(self):
        submitted = json.dumps(
            {
                "event": "media",
                "streamSid": "MZwrong-stream-sentinel",
                "media": {
                    "track": "outbound",
                    "payload": "fictional-secret-sentinel",
                    "phone": "+12025550123",
                },
            }
        )
        self.assert_private_error(
            parse_inbound_media_message, submitted, self.stream_sid
        )


class MediaProtocolSessionTests(unittest.TestCase):
    account_sid = MediaProtocolTests.account_sid
    stream_sid = MediaProtocolTests.stream_sid
    call_sid = MediaProtocolTests.call_sid
    scenario_id = MediaProtocolTests.scenario_id
    payload = MediaProtocolTests.payload
    connected_message = MediaProtocolTests.connected_message
    start_message = MediaProtocolTests.start_message
    media_message = MediaProtocolTests.media_message
    assert_private_error = MediaProtocolTests.assert_private_error

    def new_session(self):
        return MediaProtocolSession(self.account_sid)

    def started_session(self):
        session = self.new_session()
        session.process_message(self.connected_message())
        session.process_message(self.start_message())
        return session

    def test_valid_connected_start_media_media_stop_transitions(self):
        session = self.new_session()

        connected = session.process_message(self.connected_message())
        self.assertEqual(connected.protocol, "Call")
        self.assertIsNone(session.stream_sid)

        started = session.process_message(self.start_message())
        self.assertEqual(started.stream_sid, self.stream_sid)
        self.assertEqual(session.stream_sid, self.stream_sid)
        self.assertEqual(session.call_sid, self.call_sid)
        self.assertEqual(session.scenario_id, self.scenario_id)

        self.assertEqual(session.process_message(self.media_message()), self.payload)
        self.assertEqual(session.process_message(self.media_message()), self.payload)
        self.assertIsNone(
            session.process_message(
                json.dumps({"event": "stop", "streamSid": self.stream_sid})
            )
        )

    def test_rejects_start_media_and_stop_before_connected(self):
        messages = (
            self.start_message(),
            self.media_message(),
            json.dumps({"event": "stop", "streamSid": self.stream_sid}),
        )
        for message in messages:
            with self.subTest(message=message):
                self.assert_private_error(
                    self.new_session().process_message,
                    message,
                )

    def test_rejects_second_connected_and_media_or_stop_before_start(self):
        messages = (
            self.connected_message(),
            self.media_message(),
            json.dumps({"event": "stop", "streamSid": self.stream_sid}),
        )
        for message in messages:
            with self.subTest(message=message):
                session = self.new_session()
                session.process_message(self.connected_message())
                self.assert_private_error(session.process_message, message)

    def test_rejects_second_connected_and_second_start_after_start(self):
        for message in (self.connected_message(), self.start_message()):
            with self.subTest(message=message):
                session = self.started_session()
                self.assert_private_error(session.process_message, message)

    def test_rejects_every_message_after_stop(self):
        messages = (
            self.connected_message(),
            self.start_message(),
            self.media_message(),
            json.dumps({"event": "stop", "streamSid": self.stream_sid}),
        )
        for message in messages:
            with self.subTest(message=message):
                session = self.started_session()
                session.process_message(
                    json.dumps({"event": "stop", "streamSid": self.stream_sid})
                )
                self.assert_private_error(session.process_message, message)

    def test_rejected_message_does_not_advance_session(self):
        session = self.new_session()
        self.assert_private_error(session.process_message, self.start_message())
        connected = session.process_message(self.connected_message())
        self.assertEqual(connected.protocol, "Call")

    def test_session_never_retains_media_payload(self):
        session = self.started_session()
        returned_payload = session.process_message(self.media_message())
        self.assertEqual(returned_payload, self.payload)
        stored_values = tuple(getattr(session, slot) for slot in session.__slots__)
        self.assertNotIn(self.payload, stored_values)
        self.assertFalse(any("payload" in slot for slot in session.__slots__))

    def test_session_errors_are_generic_and_private(self):
        session = self.started_session()
        submitted = json.dumps(
            {
                "event": "media",
                "streamSid": "MZwrong-stream-sentinel",
                "media": {
                    "track": "outbound",
                    "payload": "fictional-secret-sentinel",
                    "phone": "+12025550123",
                },
            }
        )
        self.assert_private_error(session.process_message, submitted)

    def test_constructor_rejects_empty_account_sid_without_echoing_it(self):
        self.assert_private_error(MediaProtocolSession, "")


if __name__ == "__main__":
    unittest.main()
