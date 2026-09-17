from dataclasses import dataclass
import json
import re

from patient_scenarios import get_scenario


_BASE64_PAYLOAD_PATTERN = re.compile(
    r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)
_SAFE_MARK_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")


@dataclass(frozen=True)
class ConnectedResult:
    protocol: str
    version: str


@dataclass(frozen=True)
class StartResult:
    stream_sid: str
    call_sid: str
    scenario_id: str


@dataclass(frozen=True)
class MarkResult:
    name: str


def _parse_object(message, error_message):
    try:
        parsed = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        raise ValueError(error_message) from None
    if not isinstance(parsed, dict):
        raise ValueError(error_message)
    return parsed


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_strict_base64(payload):
    return (
        _is_nonempty_string(payload)
        and payload.isascii()
        and len(payload) % 4 == 0
        and _BASE64_PAYLOAD_PATTERN.fullmatch(payload) is not None
    )


def parse_connected_message(message):
    """Validate Twilio's initial connected message."""
    error = "Invalid connected message."
    parsed = _parse_object(message, error)
    if (
        parsed.get("event") != "connected"
        or parsed.get("protocol") != "Call"
        or parsed.get("version") != "1.0.0"
    ):
        raise ValueError(error)
    return ConnectedResult(protocol="Call", version="1.0.0")


def parse_start_message(message, expected_account_sid):
    """Validate Twilio's start metadata and return its safe routing fields."""
    error = "Invalid start message."
    parsed = _parse_object(message, error)
    start = parsed.get("start")
    if parsed.get("event") != "start" or not isinstance(start, dict):
        raise ValueError(error)

    stream_sid = parsed.get("streamSid")
    nested_stream_sid = start.get("streamSid")
    call_sid = start.get("callSid")
    if (
        not _is_nonempty_string(stream_sid)
        or not _is_nonempty_string(nested_stream_sid)
        or stream_sid != nested_stream_sid
        or not _is_nonempty_string(call_sid)
        or not _is_nonempty_string(expected_account_sid)
        or not _is_nonempty_string(start.get("accountSid"))
        or start.get("accountSid") != expected_account_sid
        or start.get("tracks") != ["inbound"]
    ):
        raise ValueError(error)

    media_format = start.get("mediaFormat")
    if (
        not isinstance(media_format, dict)
        or media_format.get("encoding") != "audio/x-mulaw"
        or type(media_format.get("sampleRate")) is not int
        or media_format["sampleRate"] != 8000
        or type(media_format.get("channels")) is not int
        or media_format["channels"] != 1
    ):
        raise ValueError(error)

    custom_parameters = start.get("customParameters")
    if not isinstance(custom_parameters, dict):
        raise ValueError(error)
    scenario_id = custom_parameters.get("scenario_id")
    try:
        scenario = get_scenario(scenario_id)
    except ValueError:
        raise ValueError(error) from None

    return StartResult(
        stream_sid=stream_sid,
        call_sid=call_sid,
        scenario_id=scenario.scenario_id,
    )


def parse_inbound_media_message(message, expected_stream_sid):
    """Validate inbound media metadata and base64 syntax without decoding audio."""
    error = "Invalid media message."
    parsed = _parse_object(message, error)
    media = parsed.get("media")
    if (
        parsed.get("event") != "media"
        or not _is_nonempty_string(expected_stream_sid)
        or not _is_nonempty_string(parsed.get("streamSid"))
        or parsed.get("streamSid") != expected_stream_sid
        or not isinstance(media, dict)
        or media.get("track") != "inbound"
        or not _is_strict_base64(media.get("payload"))
    ):
        raise ValueError(error)
    return media["payload"]


def parse_stop_message(message, expected_stream_sid):
    """Validate a stop event and intentionally retain none of its payload data."""
    error = "Invalid stop message."
    parsed = _parse_object(message, error)
    if (
        parsed.get("event") != "stop"
        or not _is_nonempty_string(expected_stream_sid)
        or not _is_nonempty_string(parsed.get("streamSid"))
        or parsed.get("streamSid") != expected_stream_sid
    ):
        raise ValueError(error)
    return None


def parse_mark_message(message, expected_stream_sid):
    """Validate a Twilio playback mark and return only its local label."""
    error = "Invalid mark message."
    parsed = _parse_object(message, error)
    mark = parsed.get("mark")
    if (
        parsed.get("event") != "mark"
        or not _is_nonempty_string(expected_stream_sid)
        or parsed.get("streamSid") != expected_stream_sid
        or not isinstance(mark, dict)
        or not isinstance(mark.get("name"), str)
        or _SAFE_MARK_NAME_PATTERN.fullmatch(mark["name"]) is None
    ):
        raise ValueError(error)
    return MarkResult(name=mark["name"])


def build_media_message(stream_sid, payload):
    """Build a Twilio media message after syntax-only payload validation."""
    if not _is_nonempty_string(stream_sid) or not _is_strict_base64(payload):
        raise ValueError("Invalid outbound media message.")
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": payload},
    }


def build_mark_message(stream_sid, name):
    """Build a Twilio mark message with a constrained local label."""
    if (
        not _is_nonempty_string(stream_sid)
        or not isinstance(name, str)
        or _SAFE_MARK_NAME_PATTERN.fullmatch(name) is None
    ):
        raise ValueError("Invalid outbound mark message.")
    return {
        "event": "mark",
        "streamSid": stream_sid,
        "mark": {"name": name},
    }


def build_clear_message(stream_sid):
    """Build a Twilio clear message for one validated stream identifier."""
    if not _is_nonempty_string(stream_sid):
        raise ValueError("Invalid outbound clear message.")
    return {"event": "clear", "streamSid": stream_sid}


class MediaProtocolSession:
    """Enforce the connected, start, media, and stop event sequence."""

    __slots__ = (
        "_expected_account_sid",
        "_state",
        "_stream_sid",
        "_call_sid",
        "_scenario_id",
    )

    def __init__(self, expected_account_sid):
        if not _is_nonempty_string(expected_account_sid):
            raise ValueError("Invalid media protocol session.")
        self._expected_account_sid = expected_account_sid
        self._state = "awaiting_connected"
        self._stream_sid = None
        self._call_sid = None
        self._scenario_id = None

    @property
    def stream_sid(self):
        return self._stream_sid

    @property
    def call_sid(self):
        return self._call_sid

    @property
    def scenario_id(self):
        return self._scenario_id

    def process_message(self, message):
        """Validate one correctly ordered message and return its immediate result."""
        error = "Invalid media protocol sequence."
        try:
            event = _parse_object(message, error).get("event")

            if self._state == "awaiting_connected":
                if event != "connected":
                    raise ValueError(error)
                result = parse_connected_message(message)
                self._state = "awaiting_start"
                return result

            if self._state == "awaiting_start":
                if event != "start":
                    raise ValueError(error)
                result = parse_start_message(message, self._expected_account_sid)
                self._stream_sid = result.stream_sid
                self._call_sid = result.call_sid
                self._scenario_id = result.scenario_id
                self._state = "streaming"
                return result

            if self._state == "streaming":
                if event == "media":
                    return parse_inbound_media_message(message, self._stream_sid)
                if event == "mark":
                    return parse_mark_message(message, self._stream_sid)
                if event == "stop":
                    parse_stop_message(message, self._stream_sid)
                    self._state = "stopped"
                    return None
                raise ValueError(error)

            raise ValueError(error)
        except ValueError:
            raise ValueError(error) from None
