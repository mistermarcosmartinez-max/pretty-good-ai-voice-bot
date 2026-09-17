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


def _is_text_delta(value):
    return isinstance(value, str) and value != ""


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


def build_response_cancel(response_id):
    """Build a targeted cancellation for one validated active response."""
    if not _is_nonempty_string(response_id):
        raise ValueError("Invalid Realtime response cancellation.")
    return {"type": "response.cancel", "response_id": response_id}


def parse_response_created(message):
    """Return the validated ID of a newly active response."""
    error = "Invalid Realtime response lifecycle event."
    parsed = _parse_object(message, error)
    response = parsed.get("response")
    if (
        parsed.get("type") != "response.created"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not isinstance(response, dict)
        or not _is_nonempty_string(response.get("id"))
        or response.get("status") != "in_progress"
    ):
        raise ValueError(error)
    return response["id"]


def parse_response_done(message):
    """Return the validated ID of a terminal response."""
    error = "Invalid Realtime response lifecycle event."
    parsed = _parse_object(message, error)
    response = parsed.get("response")
    if (
        parsed.get("type") != "response.done"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not isinstance(response, dict)
        or not _is_nonempty_string(response.get("id"))
        or response.get("status")
        not in ("completed", "cancelled", "failed", "incomplete")
    ):
        raise ValueError(error)
    return response["id"]


def parse_input_audio_transcription_completed(message):
    """Return only validated input transcript text for transient routing."""
    error = "Invalid Realtime input transcription event."
    parsed = _parse_object(message, error)
    content_index = parsed.get("content_index")
    if (
        parsed.get("type")
        != "conversation.item.input_audio_transcription.completed"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not _is_nonempty_string(parsed.get("item_id"))
        or type(content_index) is not int
        or content_index < 0
        or not _is_nonempty_string(parsed.get("transcript"))
    ):
        raise ValueError(error)
    return parsed["transcript"]


def parse_input_audio_transcription_delta(message):
    """Return an input item ID and validated streaming transcript delta."""
    error = "Invalid Realtime input transcription event."
    parsed = _parse_object(message, error)
    content_index = parsed.get("content_index")
    delta = parsed.get("delta")
    if (
        parsed.get("type")
        != "conversation.item.input_audio_transcription.delta"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not _is_nonempty_string(parsed.get("item_id"))
        or (
            content_index is not None
            and (type(content_index) is not int or content_index < 0)
        )
        or (delta is not None and not isinstance(delta, str))
    ):
        raise ValueError(error)
    return parsed["item_id"], delta


def parse_response_output_audio_transcript_delta(message):
    """Return validated response identity and streaming transcript text."""
    error = "Invalid Realtime output transcription event."
    parsed = _parse_object(message, error)
    output_index = parsed.get("output_index")
    content_index = parsed.get("content_index")
    if (
        parsed.get("type") != "response.output_audio_transcript.delta"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not _is_nonempty_string(parsed.get("response_id"))
        or not _is_nonempty_string(parsed.get("item_id"))
        or type(output_index) is not int
        or output_index < 0
        or type(content_index) is not int
        or content_index < 0
        or not _is_text_delta(parsed.get("delta"))
    ):
        raise ValueError(error)
    return parsed["response_id"], parsed["delta"]


def parse_response_output_audio_transcript_done(message):
    """Return validated response identity and completed transcript text."""
    error = "Invalid Realtime output transcription event."
    parsed = _parse_object(message, error)
    output_index = parsed.get("output_index")
    content_index = parsed.get("content_index")
    if (
        parsed.get("type") != "response.output_audio_transcript.done"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not _is_nonempty_string(parsed.get("response_id"))
        or not _is_nonempty_string(parsed.get("item_id"))
        or type(output_index) is not int
        or output_index < 0
        or type(content_index) is not int
        or content_index < 0
        or not _is_nonempty_string(parsed.get("transcript"))
    ):
        raise ValueError(error)
    return parsed["response_id"], parsed["transcript"]


def parse_response_output_audio_delta(message):
    """Return only a validated base64 delta from an output audio event."""
    error = "Invalid Realtime output audio event."
    parsed = _parse_object(message, error)
    output_index = parsed.get("output_index")
    content_index = parsed.get("content_index")
    if (
        parsed.get("type") != "response.output_audio.delta"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not _is_nonempty_string(parsed.get("response_id"))
        or not _is_nonempty_string(parsed.get("item_id"))
        or type(output_index) is not int
        or output_index < 0
        or type(content_index) is not int
        or content_index < 0
        or not _is_strict_base64(parsed.get("delta"))
    ):
        raise ValueError(error)
    return parsed["delta"]


def parse_response_output_audio_done(message):
    """Recognize completion of one output-audio part without retaining data."""
    error = "Invalid Realtime output audio event."
    parsed = _parse_object(message, error)
    output_index = parsed.get("output_index")
    content_index = parsed.get("content_index")
    if (
        parsed.get("type") != "response.output_audio.done"
        or not _is_nonempty_string(parsed.get("event_id"))
        or not _is_nonempty_string(parsed.get("response_id"))
        or not _is_nonempty_string(parsed.get("item_id"))
        or type(output_index) is not int
        or output_index < 0
        or type(content_index) is not int
        or content_index < 0
    ):
        raise ValueError(error)
    return True


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
