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

import media_protocol
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
        account_sid = settings["TWILIO_ACCOUNT_SID"]
    except Exception:
        del settings
        await websocket.close(code=1011)
        return
    del settings

    await websocket.accept()
    try:
        session = media_protocol.MediaProtocolSession(account_sid)
    except Exception:
        await websocket.close(code=1011)
        return
    del account_sid

    while True:
        try:
            message = await websocket.receive_text()
        except WebSocketDisconnect:
            return
        except Exception:
            await websocket.close(code=1011)
            return

        try:
            try:
                is_stop = session.process_message(message) is None
            finally:
                del message
        except ValueError:
            await websocket.close(code=1008)
            return
        except Exception:
            await websocket.close(code=1011)
            return

        if is_stop:
            await websocket.close(code=1000)
            return
