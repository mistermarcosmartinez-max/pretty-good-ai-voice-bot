import asyncio
import json

from starlette.websockets import WebSocketDisconnect

import audio_relay
import media_protocol
import openai_realtime_protocol
import patient_scenarios


_REALTIME_MODEL = "gpt-realtime-2.1"
_REALTIME_VOICE = "marin"
_BRIDGE_ERROR = "Realtime audio bridge failed."


class RealtimeBridgeProtocolError(ValueError):
    """Indicate a private Twilio client-protocol failure."""


class RealtimeBridgeInternalError(RuntimeError):
    """Indicate a private provider or internal bridge failure."""


async def bridge_realtime_audio(
    twilio_websocket, openai_connection, twilio_account_sid
):
    """Relay audio and return the clean Twilio termination reason."""
    stream_ready = asyncio.get_running_loop().create_future()

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
                    event = audio_relay.twilio_media_to_openai_append(
                        message, session.stream_sid
                    )
                    try:
                        await openai_connection.send(json.dumps(event))
                    finally:
                        del event
                elif result is None:
                    return "stopped"
            finally:
                del result
                del message

    async def relay_openai_to_twilio():
        stream_sid = await stream_ready

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

                if event_type == "response.output_audio.delta":
                    event = audio_relay.openai_delta_to_twilio_media(
                        message, stream_sid
                    )
                elif event_type == "input_audio_buffer.speech_started":
                    event = audio_relay.openai_speech_started_to_twilio_clear(
                        message, stream_sid
                    )
                else:
                    continue

                await twilio_websocket.send_text(json.dumps(event))
            finally:
                del parsed
                del event
                del message

        raise RealtimeBridgeInternalError(_BRIDGE_ERROR)

    twilio_task = asyncio.create_task(relay_twilio_to_openai())
    openai_task = asyncio.create_task(relay_openai_to_twilio())
    tasks = (twilio_task, openai_task)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
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
