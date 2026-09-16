from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response as ContentResponse
from twilio.twiml.voice_response import VoiceResponse

import openai_realtime_connection
import realtime_bridge
import twilio_webhooks
from patient_scenarios import get_scenario

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/voice")
async def voice(request: Request):
    validated = await twilio_webhooks.validate_twilio_webhook(request)
    try:
        scenario = get_scenario(request.query_params.get("scenario_id"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scenario.") from None

    twiml = VoiceResponse()
    stream = twiml.connect().stream(
        url=twilio_webhooks.build_media_url(validated.settings["PUBLIC_BASE_URL"])
    )
    stream.parameter(name="scenario_id", value=scenario.scenario_id)
    return ContentResponse(content=str(twiml), media_type="application/xml")


@app.post("/calls/status", status_code=204)
async def call_status(request: Request):
    await twilio_webhooks.validate_twilio_webhook(request)
    return Response(status_code=204)


@app.post("/recordings/status", status_code=204)
async def recording_status(request: Request):
    await twilio_webhooks.validate_twilio_webhook(request)
    return Response(status_code=204)


@app.websocket("/media")
async def media(websocket: WebSocket):
    try:
        settings = twilio_webhooks.validate_twilio_websocket_handshake(websocket)
    except ValueError:
        await websocket.close(code=1008)
        return

    try:
        api_key, account_sid = (
            settings["OPENAI_API_KEY"],
            settings["TWILIO_ACCOUNT_SID"],
        )
    except Exception:
        del settings
        await websocket.close(code=1011)
        return

    try:
        await websocket.accept()
    except WebSocketDisconnect:
        del api_key
        del account_sid
        del settings
        return
    except Exception:
        del api_key
        del account_sid
        del settings
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
        return

    openai_connection = None
    setup_failed = False
    try:
        openai_connection = (
            await openai_realtime_connection.create_openai_realtime_connection(
                api_key
            )
        )
    except Exception:
        setup_failed = True
    finally:
        del api_key
        del settings

    if setup_failed:
        del account_sid
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
        return

    close_code = None
    peer_disconnected = False
    bridge_operation = realtime_bridge.bridge_realtime_audio(
        websocket, openai_connection, account_sid
    )
    del account_sid
    try:
        try:
            bridge_result = await bridge_operation
        except realtime_bridge.RealtimeBridgeProtocolError:
            close_code = 1008
        except Exception:
            close_code = 1011
        else:
            if bridge_result == "stopped":
                close_code = 1000
            elif bridge_result == "disconnected":
                peer_disconnected = True
            else:
                close_code = 1011
            del bridge_result
    finally:
        del bridge_operation
        try:
            await openai_connection.close()
        except Exception:
            pass
        del openai_connection

    if peer_disconnected:
        return
    try:
        await websocket.close(code=close_code)
    except WebSocketDisconnect:
        return
    except Exception:
        return
