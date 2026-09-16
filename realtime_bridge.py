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


async def bridge_realtime_audio(
    twilio_websocket, openai_connection, twilio_account_sid
):
    """Relay validated audio events across two already-open connections."""
    stream_ready = asyncio.get_running_loop().create_future()

    async def relay_twilio_to_openai():
        session = media_protocol.MediaProtocolSession(twilio_account_sid)

        while True:
            try:
                message = await twilio_websocket.receive_text()
            except WebSocketDisconnect:
                return

            result = None
            try:
                result = session.process_message(message)

                if isinstance(result, media_protocol.StartResult):
                    scenario_id = patient_scenarios.get_scenario(
                        result.scenario_id
                    ).scenario_id
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
                    return
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

    tasks = (
        asyncio.create_task(relay_twilio_to_openai()),
        asyncio.create_task(relay_openai_to_twilio()),
    )
    failed = False
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.CancelledError:
        raise
    except Exception:
        failed = True
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if any(isinstance(outcome, Exception) for outcome in outcomes):
            failed = True
        del outcomes

    if failed:
        raise ValueError(_BRIDGE_ERROR) from None
