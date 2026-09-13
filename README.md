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
