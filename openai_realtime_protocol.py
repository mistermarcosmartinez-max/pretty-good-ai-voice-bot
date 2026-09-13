import json
import re

from patient_scenarios import build_patient_prompt, get_scenario


SUPPORTED_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)

_BASE64_PAYLOAD_PATTERN = re.compile(
    r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_strict_base64(payload):
    return (
        _is_nonempty_string(payload)
        and payload.isascii()
        and len(payload) % 4 == 0
        and _BASE64_PAYLOAD_PATTERN.fullmatch(payload) is not None
    )


def _parse_object(message, error_message):
    try:
        parsed = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        raise ValueError(error_message) from None
    if not isinstance(parsed, dict):
        raise ValueError(error_message)
    return parsed


def build_session_update(scenario_id, model, voice):
    """Build a Realtime session update for one existing patient scenario."""
    error = "Invalid Realtime session settings."
    if (
        not _is_nonempty_string(scenario_id)
        or not _is_nonempty_string(model)
        or voice not in SUPPORTED_VOICES
    ):
        raise ValueError(error)
    try:
        scenario = get_scenario(scenario_id)
    except ValueError:
        raise ValueError(error) from None

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": model,
            "output_modalities": ["audio"],
            "instructions": build_patient_prompt(scenario),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu", "rate": 8000},
                    "turn_detection": {"type": "semantic_vad"},
                },
                "output": {
                    "format": {"type": "audio/pcmu", "rate": 8000},
                    "voice": voice,
                },
            },
        },
    }


def build_input_audio_buffer_append(payload):
    """Build an audio append event after validating base64 syntax."""
    if not _is_strict_base64(payload):
        raise ValueError("Invalid Realtime input audio event.")
    return {"type": "input_audio_buffer.append", "audio": payload}


def parse_response_output_audio_delta(message):
    """Return only a validated base64 delta from an output audio event."""
    error = "Invalid Realtime output audio event."
    parsed = _parse_object(message, error)
    if (
        parsed.get("type") != "response.output_audio.delta"
        or not _is_strict_base64(parsed.get("delta"))
    ):
        raise ValueError(error)
    return parsed["delta"]


def parse_input_audio_buffer_speech_started(message):
    """Recognize a speech-started event without retaining submitted data."""
    error = "Invalid Realtime speech event."
    parsed = _parse_object(message, error)
    audio_start_ms = parsed.get("audio_start_ms")
    if (
        parsed.get("type") != "input_audio_buffer.speech_started"
        or not _is_nonempty_string(parsed.get("event_id"))
        or type(audio_start_ms) is not int
        or audio_start_ms < 0
        or not _is_nonempty_string(parsed.get("item_id"))
    ):
        raise ValueError(error)
    return True
