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

## Offline tests

Run from the project folder with the existing virtual environment:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The destination check accepts only the exact approved assessment destination
and raises `ValueError` for other inputs without correcting or substituting them.
These offline tests check destination validation in isolation and never dial
any number. Live calling is not implemented yet.

The same command runs the configuration tests with fictional values.
`config.py` provides `validate_settings(settings)`, which checks that a supplied
dictionary contains nonempty, non-whitespace strings for `OPENAI_API_KEY`,
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, and
`PUBLIC_BASE_URL`. It returns `None` on success or raises `ValueError` listing
only invalid or missing setting names, never their values.

This check runs in isolation. It does not validate phone-number or URL formats,
verify credentials with OpenAI or Twilio, or contact either provider.

## Configuration loading

`.env.example` is an empty configuration template for the planned Twilio and
OpenAI voice bot. Real credentials belong only in an ignored local `.env` file;
never put them in the template or commit them.

`TWILIO_FROM_NUMBER` is our originating caller-ID number in E.164 format.
`PUBLIC_BASE_URL` will be our server's public HTTPS address. The assessment
destination is defined in `call_safety.py` and is not configurable here.

Call `load_settings(env_path=None)` explicitly to load settings. By default it
reads only `.env` beside `config.py`, regardless of the current directory; it
does not search parent folders. An explicit path selects that file instead.
It uses python-dotenv's `dotenv_values` with variable expansion disabled, so
`${NAME}` references remain literal.

Existing environment variables override file values, including empty or
whitespace-only values, which fail validation. The loader collects only
`REQUIRED_SETTINGS`, calls `validate_settings`, and returns the validated
dictionary without changing `os.environ` or printing setting values. A missing
file can still succeed if the environment supplies every required setting.
Nothing is loaded automatically on import.

Offline loader tests use temporary files, fictional values, and an isolated
environment; they never read the real project `.env`. This is local validation
only. Provider credential authentication and live calling remain unimplemented.
The unchanged local health endpoint does not load settings or require credentials.

## Offline fictional-patient scenarios

`patient_scenarios.py` defines 10 fictional offline scenario types:

- Schedule a routine annual physical
- Reschedule an existing appointment
- Cancel an appointment
- Request a routine medication refill
- Ask about office hours
- Ask for the clinic location and parking information
- Ask whether a named insurance plan is accepted
- Schedule a new-patient primary-care appointment
- Clarify an initially unclear appointment request
- Recover after an interruption or misunderstanding

The prompt builder tells a future voice model to speak naturally, reveal only
known facts as they become relevant, adapt to the healthcare agent, follow each
scenario's conversation guidance, and avoid inventing missing personal or
medical details. It also prevents claims about a real emergency or a real
appointment.

The standard offline test command above checks the stored scenario, lookup
errors, and prompt instructions without loading configuration or contacting a
provider. These are fictional offline definitions. They are not completed calls,
recordings, or discovered bugs, and they have not been connected to a voice
model or live calling.
