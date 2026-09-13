# pretty-good-ai-voice-bot
AI Engineering Challenge - automated patient voice bot for testing a healthcare AI agent

This first step provides a local FastAPI server with a health endpoint.
Voice calling is not implemented yet. The server needs no API keys and makes
no external API calls.

## Run locally with Windows PowerShell

Run these commands from the project folder using the existing `.venv`.
You do not need to activate the environment.

1. Install the requirements:

   ```powershell
   & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Start the server:

   ```powershell
   & .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
   ```

3. Keep the server terminal open. In a second PowerShell terminal, open the
   health endpoint in your browser:

   ```powershell
   Start-Process "http://127.0.0.1:8000/health"
   ```

   You can also open <http://127.0.0.1:8000/health> directly.
   The expected response is `{"status":"ok"}`.

Press `Ctrl+C` in the server terminal to stop it.

## Local verification

The local `GET /health` test returned HTTP 200 with `{"status":"ok"}`
using FastAPI 0.141.1 and Uvicorn 0.52.4, with a 3-second request timeout.
The test server was stopped and its exit confirmed. Voice calling is not
implemented yet and has not been tested.

## Offline destination-safety tests

Run from the project folder with the existing virtual environment:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The destination check accepts only the exact approved assessment destination
and raises `ValueError` for other inputs without correcting or substituting them.
These offline tests check destination validation in isolation and never dial
any number. Live calling is not implemented yet.

## Planned configuration

`.env.example` is an empty configuration template for the planned Twilio and
OpenAI voice bot. Real credentials belong only in an ignored local `.env` file;
never put them in the template or commit them.

`TWILIO_FROM_NUMBER` is our originating caller-ID number in E.164 format.
`PUBLIC_BASE_URL` will be our server's public HTTPS address. The assessment
destination is defined in `call_safety.py` and is not configurable here.

Configuration loading and live calling are not implemented yet. The current
local health endpoint does not require these settings.
