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

## Protected outbound-call boundary

`outbound_call.py` defines an offline-tested boundary for constructing a Twilio
client and preparing one assessment call. It validates configuration, scenario
selection, strict E.164 caller-ID formatting, and the public HTTPS base URL. The
destination is not configurable: the boundary obtains and validates the fixed
assessment destination immediately before calling Twilio's create method.

The boundary requests POST callbacks for call progress and recording completion.
It also requests recording with `recording_channels="dual"` and
`recording_track="both"`. The generated `/voice`, `/calls/status`, and
`/recordings/status` webhook routes now exist and are protected with Twilio
signature validation. They load configuration only when requested. The
`/voice` route returns `<Connect><Stream>` TwiML with the scenario ID as a custom
parameter, while the two callback routes validate and discard their data.

The `/media` WebSocket route does not exist yet, so `create_assessment_call()`
must not be run yet. Dual-channel recording behavior has not been verified with
Twilio.

`media_protocol.py` is a pure offline parser and message builder for the Twilio
Media Streams protocol. It validates the connected, start, inbound media, and
stop messages and builds media, mark, and clear messages without opening a
WebSocket or processing audio. Twilio's μ-law/8000 audio format matches OpenAI's
`audio/pcmu`/8000 format, but no OpenAI connection or audio relay exists yet.
No audio has been received, generated, stored, or played.

`openai_realtime_protocol.py` is a pure offline builder and parser for the next
OpenAI Realtime protocol boundary. It builds an audio-only `session.update`
from an existing patient scenario, using PCMU at its 8000 Hz rate and semantic
VAD, and builds validated `input_audio_buffer.append` events. It also validates
`response.output_audio.delta` and `input_audio_buffer.speech_started` events
without decoding, transforming, storing, printing, or logging audio.

`audio_relay.py` is a pure offline adapter between the Twilio and OpenAI
protocol helpers. It converts validated inbound Twilio media into OpenAI audio
append dictionaries, validated OpenAI audio deltas into Twilio media
dictionaries, and OpenAI speech-started events into Twilio clear dictionaries
for future interruption handling. It reuses the protocol modules' public
parsers and builders and retains no payloads or event data.

These layers construct and check plain dictionaries and JSON only. They do not
connect to provider services or WebSockets, and no live audio relay loop
exists. The `/media` WebSocket route still does not exist, so
`create_assessment_call()` must not be run yet.

Offline tests use fictional values, generated signatures, mocks, and
a local ASGI harness. They make no network requests. No live call has been made,
and provider authentication and end-to-end calling remain unimplemented.
