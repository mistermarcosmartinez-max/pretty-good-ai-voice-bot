import media_protocol
import openai_realtime_protocol


def twilio_media_to_openai_append(message, expected_stream_sid):
    """Convert one validated Twilio media event to an OpenAI audio append."""
    try:
        payload = media_protocol.parse_inbound_media_message(
            message, expected_stream_sid
        )
        return openai_realtime_protocol.build_input_audio_buffer_append(payload)
    except ValueError:
        raise ValueError("Invalid Twilio-to-OpenAI audio relay.") from None


def openai_delta_to_twilio_media(message, stream_sid):
    """Convert one validated OpenAI audio delta to a Twilio media event."""
    try:
        payload = openai_realtime_protocol.parse_response_output_audio_delta(message)
        return media_protocol.build_media_message(stream_sid, payload)
    except ValueError:
        raise ValueError("Invalid OpenAI-to-Twilio audio relay.") from None


def openai_speech_started_to_twilio_clear(message, stream_sid):
    """Convert one validated OpenAI speech-started event to a Twilio clear."""
    try:
        openai_realtime_protocol.parse_input_audio_buffer_speech_started(message)
        return media_protocol.build_clear_message(stream_sid)
    except ValueError:
        raise ValueError("Invalid OpenAI interruption relay.") from None
