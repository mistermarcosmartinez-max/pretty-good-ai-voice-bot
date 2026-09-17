import asyncio
from collections import deque
import json
import logging
import re

from starlette.websockets import WebSocketDisconnect

import audio_relay
import media_protocol
import openai_realtime_protocol
import patient_scenarios


_REALTIME_MODEL = "gpt-realtime-2.1"
_REALTIME_VOICE = "marin"
_BRIDGE_ERROR = "Realtime audio bridge failed."
_LOGGER = logging.getLogger(__name__)
_KNOWN_PROVIDER_ERROR_TYPES = frozenset(
    ("invalid_request_error", "server_error")
)
_MAX_SEEN_SERVER_EVENT_IDS = 16_384
_MAX_SERVER_EVENT_ID_LENGTH = 256
_MAX_TRANSIENT_TRANSCRIPT_LENGTH = 8_192
_MAX_SUPPRESSED_RESPONSE_IDS = 32
_TRANSFER_ANNOUNCEMENT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:i'm|i am|we're|we are)\s+(?:now\s+)?(?:transferring\s+(?:you|the call|your call)|connecting\s+(?:you|the call|your call)|putting\s+you\s+through|sending\s+you\s+(?:over|through|to)|routing\s+(?:you|the call|your call)|getting\s+you\s+(?:connected|over|through|to)|bringing\s+you\s+(?:over|through|to))\b",
        r"\b(?:i'll|i will|we'll|we will|i'm going to|i am going to|we're going to|we are going to|i'm about to|i am about to|we're about to|we are about to)\s+(?:now\s+)?(?:transfer\s+(?:you|the call|your call)|connect\s+(?:you|the call|your call)|put\s+you\s+through|send\s+you\s+(?:over|through|to)|route\s+(?:you|the call|your call)|get\s+you\s+(?:connected|over|through|to)|bring\s+you\s+(?:over|through|to))\b",
        r"\blet me\s+(?:now\s+)?(?:transfer\s+(?:you|the call)|connect\s+(?:you|the call)|put\s+you\s+through|send\s+you\s+(?:over|through|to)|route\s+(?:you|the call)|get\s+you\s+(?:connected|over|through|to)|bring\s+you\s+(?:over|through|to))\b",
        r"\b(?:please\s+)?hold(?:\s+on)?\s+while\s+(?:i|we)\s+(?:(?:transfer|connect|send|route)\s+(?:you|the call|your call)|put\s+you\s+through|get\s+(?:you\s+(?:connected|over|through)|the other line)|bring\s+you\s+(?:over|through|to))\b",
        r"\b(?:you are|you're|the call is|your call is)\s+(?:now\s+)?being\s+(?:transferred|connected|routed)\b",
        r"\b(?:you'll|you will|the call will|your call will)\s+(?:now\s+)?be\s+(?:transferred|connected|routed)\b",
        r"\b(?:transferring|connecting|routing)\s+(?:you|the call|your call)\s+now\b",
    )
)
_TRANSFER_DEFERRED_PATTERN = re.compile(
    r"\b(?:if|after|once|when|before|unless)\b"
)
_TRANSFER_NEGATED_PATTERN = re.compile(
    r"\b(?:(?:not|never)\s+(?:currently\s+)?"
    r"(?:transferring|connecting|routing|transfer|connect|route)|"
    r"(?:don't|do not|won't|will not|can't|cannot|aren't|are not|"
    r"isn't|is not)(?:\s+[\w']+){0,4}\s+"
    r"(?:transferring|connecting|routing|transferred|connected|routed|"
    r"transfer|connect|route))\b"
)
_TRANSFER_NONCURRENT_PATTERN = re.compile(
    r"\b(?:later|tomorrow|eventually|next\s+(?:week|month|time)|"
    r"in\s+\d+\s+(?:minute|minutes|hour|hours|day|days))\b"
)
_TRANSFER_NON_CALL_OBJECT_PATTERN = re.compile(
    r"\b(?:the|your)\s+call\s+"
    r"(?:data|record|records|information|notes|file|files)\b"
)
class RealtimeBridgeProtocolError(ValueError):
    """Indicate a private Twilio client-protocol failure."""


class RealtimeBridgeInternalError(RuntimeError):
    """Indicate a private provider or internal bridge failure."""


class _BoundedEventIds:
    """Remember recent server event IDs without unbounded per-call growth."""

    __slots__ = ("_limit", "_order", "_values")

    def __init__(self, limit):
        if type(limit) is not int or limit < 1:
            raise ValueError(_BRIDGE_ERROR)
        self._limit = limit
        self._order = deque()
        self._values = set()

    def remember(self, event_id):
        if (
            not isinstance(event_id, str)
            or not event_id.strip()
            or len(event_id) > _MAX_SERVER_EVENT_ID_LENGTH
        ):
            raise ValueError(_BRIDGE_ERROR)
        if event_id in self._values:
            return False
        if len(self._order) == self._limit:
            self._values.remove(self._order.popleft())
        self._order.append(event_id)
        self._values.add(event_id)
        return True

    def __len__(self):
        return len(self._order)

    def __contains__(self, event_id):
        return event_id in self._values

    def clear(self):
        self._order.clear()
        self._values.clear()


def _decoded_base64_size(payload):
    padding = (
        2 if payload.endswith("==") else 1 if payload.endswith("=") else 0
    )
    return len(payload) // 4 * 3 - padding


def _provider_error_type(event):
    error = event.get("error")
    supplied_type = error.get("type") if isinstance(error, dict) else None
    if supplied_type in _KNOWN_PROVIDER_ERROR_TYPES:
        return supplied_type
    return "unknown"


def _is_transfer_announcement(transcript):
    if not isinstance(transcript, str) or "?" in transcript:
        return False
    normalized = re.sub(
        r"[^\w']+", " ", transcript.casefold().replace("’", "'")
    ).strip()
    if (
        _TRANSFER_DEFERRED_PATTERN.search(normalized)
        or _TRANSFER_NEGATED_PATTERN.search(normalized)
        or _TRANSFER_NONCURRENT_PATTERN.search(normalized)
        or _TRANSFER_NON_CALL_OBJECT_PATTERN.search(normalized)
    ):
        return False
    return any(
        pattern.search(normalized)
        for pattern in _TRANSFER_ANNOUNCEMENT_PATTERNS
    )


def _normalized_transcript(transcript):
    if not isinstance(transcript, str):
        return ""
    transcript = transcript.replace("\u2019", "'")
    return re.sub(
        r"[^\w']+", " ", transcript.casefold().replace("’", "'")
    ).strip()


def _is_streaming_transfer_announcement(transcript):
    """Recognize only prefixes that already establish an active transfer."""
    if not _is_transfer_announcement(transcript):
        return False
    normalized = _normalized_transcript(transcript)
    return bool(
        re.search(r"\bnow\b", normalized)
        or re.search(r"\bhold(?:\s+on)?\s+while\b", normalized)
    )


def _merge_transcript_delta(existing, delta):
    """Merge append-only, repeated, overlapping, or cumulative text."""
    if not isinstance(existing, str) or not isinstance(delta, str) or not delta:
        raise ValueError(_BRIDGE_ERROR)
    if delta.startswith(existing):
        combined = delta
    elif existing.startswith(delta) or existing.endswith(delta):
        combined = existing
    else:
        overlap = min(len(existing), len(delta))
        while overlap and not existing.endswith(delta[:overlap]):
            overlap -= 1
        combined = existing + delta[overlap:]
    if len(combined) > _MAX_TRANSIENT_TRANSCRIPT_LENGTH:
        raise ValueError(_BRIDGE_ERROR)
    return combined


def _log_diagnostics(diagnostics):
    provider_errors = ",".join(
        f"{event_type}:{count}"
        for event_type, count in sorted(
            diagnostics["provider_error_event_types"].items()
        )
    ) or "none"
    _LOGGER.info(
        "Realtime bridge diagnostics: "
        "twilio_inbound_audio_frames=%d "
        "twilio_inbound_audio_bytes=%d "
        "openai_input_audio_appends=%d "
        "openai_input_audio_frames_suppressed=%d "
        "openai_output_audio_delta_frames=%d "
        "openai_output_audio_delta_bytes=%d "
        "openai_output_audio_frames_suppressed=%d "
        "twilio_outbound_media_frames=%d "
        "twilio_outbound_media_bytes=%d "
        "openai_speech_started_events=%d "
        "openai_duplicate_events_ignored=%d "
        "openai_input_transcripts_checked=%d "
        "transfer_transitions_detected=%d "
        "openai_responses_cancelled=%d "
        "twilio_clear_messages_sent=%d "
        "openai_output_audio_done_events=%d "
        "twilio_playback_marks_sent=%d "
        "twilio_playback_marks_acknowledged=%d "
        "twilio_playback_marks_pending=%d "
        "provider_error_event_types=%s",
        diagnostics["twilio_inbound_audio_frames"],
        diagnostics["twilio_inbound_audio_bytes"],
        diagnostics["openai_input_audio_appends"],
        diagnostics["openai_input_audio_frames_suppressed"],
        diagnostics["openai_output_audio_delta_frames"],
        diagnostics["openai_output_audio_delta_bytes"],
        diagnostics["openai_output_audio_frames_suppressed"],
        diagnostics["twilio_outbound_media_frames"],
        diagnostics["twilio_outbound_media_bytes"],
        diagnostics["openai_speech_started_events"],
        diagnostics["openai_duplicate_events_ignored"],
        diagnostics["openai_input_transcripts_checked"],
        diagnostics["transfer_transitions_detected"],
        diagnostics["openai_responses_cancelled"],
        diagnostics["twilio_clear_messages_sent"],
        diagnostics["openai_output_audio_done_events"],
        diagnostics["twilio_playback_marks_sent"],
        diagnostics["twilio_playback_marks_acknowledged"],
        diagnostics["twilio_playback_marks_pending"],
        provider_errors,
    )


async def bridge_realtime_audio(
    twilio_websocket, openai_connection, twilio_account_sid
):
    """Relay audio and return the clean Twilio termination reason."""
    stream_ready = asyncio.get_running_loop().create_future()
    diagnostics = {
        "twilio_inbound_audio_frames": 0,
        "twilio_inbound_audio_bytes": 0,
        "openai_input_audio_appends": 0,
        "openai_input_audio_frames_suppressed": 0,
        "openai_output_audio_delta_frames": 0,
        "openai_output_audio_delta_bytes": 0,
        "openai_output_audio_frames_suppressed": 0,
        "twilio_outbound_media_frames": 0,
        "twilio_outbound_media_bytes": 0,
        "openai_speech_started_events": 0,
        "openai_duplicate_events_ignored": 0,
        "openai_input_transcripts_checked": 0,
        "transfer_transitions_detected": 0,
        "openai_responses_cancelled": 0,
        "twilio_clear_messages_sent": 0,
        "openai_output_audio_done_events": 0,
        "twilio_playback_marks_sent": 0,
        "twilio_playback_marks_acknowledged": 0,
        "twilio_playback_marks_pending": 0,
        "provider_error_event_types": {},
    }
    pending_playback_marks = set()
    transfer_state = {
        "active": False,
        "provisional_item_id": None,
    }
    seen_server_event_ids = _BoundedEventIds(_MAX_SEEN_SERVER_EVENT_IDS)

    async def relay_twilio_to_openai():
        try:
            session = media_protocol.MediaProtocolSession(twilio_account_sid)
        except Exception:
            raise RealtimeBridgeInternalError(_BRIDGE_ERROR) from None

        while True:
            try:
                message = await twilio_websocket.receive_text()
            except WebSocketDisconnect:
                return "disconnected"

            result = None
            try:
                try:
                    result = session.process_message(message)
                except ValueError:
                    raise RealtimeBridgeProtocolError(_BRIDGE_ERROR) from None

                if isinstance(result, media_protocol.StartResult):
                    try:
                        scenario_id = patient_scenarios.get_scenario(
                            result.scenario_id
                        ).scenario_id
                    except ValueError:
                        raise RealtimeBridgeProtocolError(
                            _BRIDGE_ERROR
                        ) from None
                    event = openai_realtime_protocol.build_session_update(
                        scenario_id, _REALTIME_MODEL, _REALTIME_VOICE
                    )
                    try:
                        await openai_connection.send(json.dumps(event))
                    finally:
                        del event
                        del scenario_id
                    stream_ready.set_result(result.stream_sid)
                elif isinstance(result, str):
                    diagnostics["twilio_inbound_audio_frames"] += 1
                    diagnostics["twilio_inbound_audio_bytes"] += (
                        _decoded_base64_size(result)
                    )
                    if transfer_state["active"]:
                        diagnostics[
                            "openai_input_audio_frames_suppressed"
                        ] += 1
                        continue
                    event = audio_relay.twilio_media_to_openai_append(
                        message, session.stream_sid
                    )
                    try:
                        await openai_connection.send(json.dumps(event))
                        diagnostics["openai_input_audio_appends"] += 1
                    finally:
                        del event
                elif isinstance(result, media_protocol.MarkResult):
                    if result.name not in pending_playback_marks:
                        raise RealtimeBridgeProtocolError(
                            _BRIDGE_ERROR
                        ) from None
                    pending_playback_marks.remove(result.name)
                    diagnostics["twilio_playback_marks_acknowledged"] += 1
                elif result is None:
                    return "stopped"
            finally:
                del result
                del message

    async def relay_openai_to_twilio():
        stream_sid = await stream_ready
        output_part_has_audio = False
        playback_mark_number = 0
        active_response_id = None
        transfer_audio_cleared = False
        suppressed_response_ids = _BoundedEventIds(
            _MAX_SUPPRESSED_RESPONSE_IDS
        )
        input_transcript_item_id = None
        input_transcript = ""

        def is_duplicate_event(parsed_event):
            event_id = parsed_event.get("event_id")
            if not seen_server_event_ids.remember(event_id):
                diagnostics["openai_duplicate_events_ignored"] += 1
                return True
            return False

        async def cancel_response(response_id):
            nonlocal active_response_id
            suppressed_response_ids.remember(response_id)
            if response_id != active_response_id:
                return
            cancel_event = openai_realtime_protocol.build_response_cancel(
                response_id
            )
            await openai_connection.send(json.dumps(cancel_event))
            diagnostics["openai_responses_cancelled"] += 1
            active_response_id = None

        async def stop_for_transfer(item_id=None, completed=False):
            nonlocal active_response_id, output_part_has_audio
            nonlocal transfer_audio_cleared
            if not transfer_state["active"]:
                transfer_state["active"] = True
                diagnostics["transfer_transitions_detected"] += 1
            if completed:
                transfer_state["provisional_item_id"] = None
            elif item_id is not None:
                transfer_state["provisional_item_id"] = item_id
            if active_response_id is not None:
                await cancel_response(active_response_id)
            if (
                not transfer_audio_cleared
                and (output_part_has_audio or pending_playback_marks)
            ):
                clear_event = media_protocol.build_clear_message(stream_sid)
                await twilio_websocket.send_text(json.dumps(clear_event))
                diagnostics["twilio_clear_messages_sent"] += 1
                output_part_has_audio = False
                transfer_audio_cleared = True

        async for message in openai_connection:
            event = None
            parsed = None
            try:
                if not isinstance(message, str):
                    raise ValueError(_BRIDGE_ERROR)
                try:
                    parsed = json.loads(message)
                except json.JSONDecodeError:
                    raise ValueError(_BRIDGE_ERROR) from None
                if not isinstance(parsed, dict):
                    raise ValueError(_BRIDGE_ERROR)

                event_type = parsed.get("type")
                if not isinstance(event_type, str) or not event_type.strip():
                    raise ValueError(_BRIDGE_ERROR)

                if event_type == "response.created":
                    response_id = openai_realtime_protocol.parse_response_created(
                        message
                    )
                    if is_duplicate_event(parsed):
                        continue
                    active_response_id = response_id
                    if transfer_state["active"]:
                        await stop_for_transfer()
                    continue
                if event_type == "response.done":
                    response_id = openai_realtime_protocol.parse_response_done(
                        message
                    )
                    if is_duplicate_event(parsed):
                        continue
                    if response_id == active_response_id:
                        active_response_id = None
                    continue
                if event_type == "response.output_audio.delta":
                    event = audio_relay.openai_delta_to_twilio_media(
                        message, stream_sid
                    )
                    if is_duplicate_event(parsed):
                        continue
                    response_id = parsed.get("response_id")
                    if not isinstance(response_id, str) or not response_id.strip():
                        raise ValueError(_BRIDGE_ERROR)
                    if (
                        transfer_state["active"]
                        or response_id in suppressed_response_ids
                    ):
                        diagnostics[
                            "openai_output_audio_frames_suppressed"
                        ] += 1
                        continue
                    output_size = _decoded_base64_size(
                        event["media"]["payload"]
                    )
                    diagnostics["openai_output_audio_delta_frames"] += 1
                    diagnostics["openai_output_audio_delta_bytes"] += output_size
                elif event_type == "input_audio_buffer.speech_started":
                    openai_realtime_protocol.parse_input_audio_buffer_speech_started(
                        message
                    )
                    if is_duplicate_event(parsed):
                        continue
                    diagnostics["openai_speech_started_events"] += 1
                    if transfer_state["active"]:
                        await stop_for_transfer()
                    continue
                elif (
                    event_type
                    == "conversation.item.input_audio_transcription.delta"
                ):
                    item_id, transcript_delta = (
                        openai_realtime_protocol
                        .parse_input_audio_transcription_delta(message)
                    )
                    try:
                        if is_duplicate_event(parsed):
                            continue
                        diagnostics["openai_input_transcripts_checked"] += 1
                        if item_id != input_transcript_item_id:
                            input_transcript_item_id = item_id
                            input_transcript = ""
                        if not transcript_delta:
                            continue
                        input_transcript = _merge_transcript_delta(
                            input_transcript, transcript_delta
                        )
                        if _is_streaming_transfer_announcement(
                            input_transcript
                        ):
                            await stop_for_transfer(item_id=item_id)
                    finally:
                        del transcript_delta
                        del item_id
                    continue
                elif (
                    event_type
                    == "conversation.item.input_audio_transcription.completed"
                ):
                    transcript = (
                        openai_realtime_protocol
                        .parse_input_audio_transcription_completed(message)
                    )
                    try:
                        if is_duplicate_event(parsed):
                            continue
                        diagnostics["openai_input_transcripts_checked"] += 1
                        is_announcement = _is_transfer_announcement(transcript)
                        if is_announcement:
                            await stop_for_transfer(
                                item_id=parsed.get("item_id"), completed=True
                            )
                        elif (
                            transfer_state["active"]
                            and transfer_state["provisional_item_id"]
                            == parsed.get("item_id")
                        ):
                            transfer_state["active"] = False
                            transfer_state["provisional_item_id"] = None
                            transfer_audio_cleared = False
                        if parsed.get("item_id") == input_transcript_item_id:
                            input_transcript_item_id = None
                            input_transcript = ""
                    finally:
                        del transcript
                    continue
                elif event_type == "response.output_audio_transcript.delta":
                    openai_realtime_protocol.parse_response_output_audio_transcript_delta(
                        message
                    )
                    if is_duplicate_event(parsed):
                        continue
                    continue
                elif event_type == "response.output_audio_transcript.done":
                    openai_realtime_protocol.parse_response_output_audio_transcript_done(
                        message
                    )
                    if is_duplicate_event(parsed):
                        continue
                    continue
                elif event_type == "response.output_audio.done":
                    openai_realtime_protocol.parse_response_output_audio_done(
                        message
                    )
                    if is_duplicate_event(parsed):
                        continue
                    if transfer_state["active"]:
                        continue
                    diagnostics["openai_output_audio_done_events"] += 1
                    if not output_part_has_audio:
                        continue
                    playback_mark_number += 1
                    mark_name = f"response_{playback_mark_number}_played"
                    event = media_protocol.build_mark_message(
                        stream_sid, mark_name
                    )
                    pending_playback_marks.add(mark_name)
                    try:
                        await twilio_websocket.send_text(json.dumps(event))
                    except BaseException:
                        pending_playback_marks.discard(mark_name)
                        raise
                    diagnostics["twilio_playback_marks_sent"] += 1
                    output_part_has_audio = False
                    continue
                elif event_type == "error":
                    provider_type = _provider_error_type(parsed)
                    error_counts = diagnostics["provider_error_event_types"]
                    error_counts[provider_type] = (
                        error_counts.get(provider_type, 0) + 1
                    )
                    raise RealtimeBridgeInternalError(_BRIDGE_ERROR)
                else:
                    continue

                await twilio_websocket.send_text(json.dumps(event))
                if event_type == "response.output_audio.delta":
                    diagnostics["twilio_outbound_media_frames"] += 1
                    diagnostics["twilio_outbound_media_bytes"] += output_size
                    output_part_has_audio = True
            finally:
                del parsed
                del event
                del message

        raise RealtimeBridgeInternalError(_BRIDGE_ERROR)

    try:
        twilio_task = asyncio.create_task(relay_twilio_to_openai())
        openai_task = asyncio.create_task(relay_openai_to_twilio())
        tasks = (twilio_task, openai_task)
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        openai_finished = openai_task in done
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        twilio_outcome = outcomes[0]

        protocol_failed = isinstance(
            twilio_outcome, RealtimeBridgeProtocolError
        )
        internal_failed = openai_finished or any(
            isinstance(outcome, BaseException)
            and not isinstance(
                outcome, (asyncio.CancelledError, RealtimeBridgeProtocolError)
            )
            for outcome in outcomes
        )
        clean_outcome = (
            twilio_outcome
            if twilio_outcome in ("stopped", "disconnected")
            else None
        )
        del twilio_outcome
        del outcomes
        del done

        if protocol_failed:
            raise RealtimeBridgeProtocolError(_BRIDGE_ERROR) from None
        if internal_failed or clean_outcome is None:
            raise RealtimeBridgeInternalError(_BRIDGE_ERROR) from None
        return clean_outcome
    finally:
        diagnostics["twilio_playback_marks_pending"] = len(
            pending_playback_marks
        )
        _log_diagnostics(diagnostics)
        pending_playback_marks.clear()
        seen_server_event_ids.clear()
        transfer_state.clear()
        del diagnostics
