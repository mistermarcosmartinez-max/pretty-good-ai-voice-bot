# pretty-good-ai-voice-bot
AI Engineering Challenge - automated patient voice bot for testing a healthcare AI agent

## Completed evaluation

The evaluation collected **15 complete artifact sets**, each containing an MP3
recording, transcript, and metadata. `artifacts/calls` contains the **10
submitted evaluation calls**. `artifacts/iteration_evidence` contains **5
development calls retained only to show iteration**; these five development
calls are **not counted toward the required final call set**. The final complete
offline suite ran **226 tests with no failures or errors**.

- [Call inventory](CALL_INVENTORY.md)
- [Evaluation findings and bot iteration evidence](BUG_REPORT.md)
- [Architecture and tradeoffs](ARCHITECTURE.md)
- [Submitted evaluation calls — 10](artifacts/calls/)
- [Development iteration evidence — 5](artifacts/iteration_evidence/)

## Loom Videos

Both recordings use the creator's webcam and voice.

- [Project walkthrough](https://www.loom.com/share/9c6c1f7c08cc4d54b1b3bea7d2e063a0)
- [AI debugging — Transfer Overlap Follow-up](https://www.loom.com/share/c85d30dcba5541c2ab5d357c561fc4f3)

The sections below retain the repository's setup, run, safety, and incremental
implementation details. The documents above summarize the final evaluated
system and supersede earlier progress-status statements about features still
being planned.

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

The prompt builder tells the patient to answer the current question directly in
one or two sentences, ask at most one necessary follow-up, avoid repeating
confirmed details, and end naturally once the outcome and next step are clear.
It preserves silence during disclosures and introductory announcements, reveals
only known facts as relevant, and avoids inventing missing personal or medical
details. The routine-visit scenario does not volunteer preparation topics unless
the healthcare agent asks about them.

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

`twilio_webhooks.py` also provides an offline-tested validation boundary for a
Twilio Media Streams WebSocket handshake. When explicitly called, it
loads settings, reconstructs the externally visible WSS URL from the configured
public HTTPS base URL plus the exact raw request path and query string, and
validates `X-Twilio-Signature` with no form parameters. It returns only the
OpenAI API key and Twilio account SID needed by the media route and does not
accept or close the WebSocket.

The `/media` WebSocket route is now wired in code to the OpenAI Realtime
connection boundary and bidirectional bridge. It validates the signed Twilio
handshake before acceptance, opens the OpenAI connection through the existing
factory, delegates protocol and audio handling to the bridge, and closes the
OpenAI connection when the bridge ends. It uses private close-code mapping for
invalid Twilio messages, provider or internal failures, valid stop events, and
peer disconnects. Local route tests replace both boundaries with fakes, so the
wiring has not opened either provider connection or relayed real audio.

`create_assessment_call()` must not be run yet. Dual-channel recording behavior
has not been verified with Twilio, and end-to-end calling remains unimplemented.

`media_protocol.py` is a pure offline parser and message builder for the Twilio
Media Streams protocol. It validates connected, start, inbound media, playback
mark, and stop messages and builds media, mark, and clear messages without
opening a WebSocket or processing audio. Twilio's μ-law/8000 audio format matches OpenAI's
`audio/pcmu`/8000 format, but no OpenAI connection or live audio relay exists yet.
No audio has been received, generated, stored, or played.

`openai_realtime_protocol.py` is a pure offline builder and parser for the next
OpenAI Realtime protocol boundary. It builds an audio-only `session.update`
from an existing patient scenario, using PCMU at its 8000 Hz rate and GA server
VAD with a 0.5 threshold, 300 ms prefix padding, and 900 ms silence duration.
The VAD automatically creates responses but does not interrupt them. The module
also enables English input transcription for transient transfer detection,
builds validated `input_audio_buffer.append` and targeted `response.cancel`
events, and validates
`response.output_audio.delta`, `response.output_audio.done`, and
`input_audio_buffer.speech_started` events without storing, printing, or logging
audio.

`audio_relay.py` is a pure offline adapter between the Twilio and OpenAI
protocol helpers. It converts validated inbound Twilio media into OpenAI audio
append dictionaries, validated OpenAI audio deltas into Twilio media
dictionaries, and OpenAI speech-started events into Twilio clear dictionaries
for future interruption handling. It reuses the protocol modules' public
parsers and builders and retains no payloads or event data.

`openai_realtime_connection.py` is an explicitly invoked, offline-tested
connection boundary. Given an API key, it lazily loads the current asyncio
WebSocket connector and opens the fixed OpenAI Realtime URL with an
`Authorization: Bearer` header. It only returns the resulting connection; it
does not send or receive WebSocket messages. Tests inject fictional connectors,
so this boundary has not contacted OpenAI and provider authentication remains
unverified. The route now invokes this boundary in code, but route tests patch
it before invocation. The pinned `websockets==17.0.1` dependency is installed
in the existing project virtual environment.

The protocol and relay helpers still only construct and check plain
dictionaries and JSON. `realtime_bridge.py` adds an offline-tested,
bidirectional relay for two already-open, caller-owned WebSockets. It uses the
existing protocol session, scenario lookup, session-event builder, and audio
adapters to coordinate both directions. With interruption disabled it validates
speech-start events without clearing Twilio's buffered patient audio, and it
uses playback marks to distinguish generated audio from audio Twilio has played.
It ignores repeated provider events using a bounded, per-call event-ID window.
When an input transcript confirms that a transfer is actively starting (rather
than merely asking or discussing whether to transfer), the bridge cancels the
active patient response, clears queued patient audio, suppresses subsequent
transferred-line input, and therefore remains silent during the new greeting.
It checks streaming input-transcription deltas for unambiguously active wording
such as "Transferring you now" so it does not have to wait for the completed
transcript event. Fragmented, repeated, overlapping, and cumulative transcript
deltas are merged into one bounded per-turn buffer. The completed transcript
confirms the transition; if it corrects a provisional streaming match, later
responses in that call are allowed again. Transfer offers remain ordinary
questions that the patient can answer. Patient-output transcripts are validated
but never used to cancel or truncate that response, because transcript
punctuation can arrive before a grammatically complete audible sentence. The
prompt instead requires one short, complete transfer request or acceptance.
Ordinary remote speech-start events still do not clear patient audio. Remote
input transcript text is used transiently for transfer routing, has a strict
per-turn size limit, and is not logged or retained. Audio that Twilio has
already played cannot be retracted; clearing applies to audio that remains
buffered when confirmation is detected.
It cleans up its internal tasks when either direction ends. The bridge does not
create, authenticate, accept, or close either connection.

The `/media` route now invokes the bridge in code after the fake-tested
connection step. This integration is covered only with a local ASGI harness,
fake OpenAI connections, and a patched bridge. No provider authentication,
live OpenAI connection, real WebSocket relay, or call has occurred.

Offline tests use fictional values, generated signatures, mocks, and
a local ASGI harness. They make no network requests. No live call has been made,
and OpenAI provider authentication has not been attempted. End-to-end calling
remains unimplemented.

## Call artifact collector

`collect_call_artifacts.py` prepares challenge artifacts for one explicitly
selected completed call. It validates that the fetched call used the protected
assessment destination, selects a completed two-channel recording belonging to
the exact Call SID, and downloads both the preserved MP3 and a temporary
dual-channel WAV. It splits the WAV into inbound and outbound mono audio and
transcribes each channel separately with OpenAI's
`gpt-4o-transcribe-diarize` model. It writes the results beneath
`artifacts/calls/<CALL_SID>/` as `recording.mp3`, `transcript.md`, and
`metadata.json`. Transcript roles come from the recording channels, not model
diarization: inbound audio is labeled `Remote side`, and outbound audio is
labeled `Patient bot`. Disclosures, the healthcare agent, and transferred-line
recordings can all share the remote channel, so the collector intentionally
does not claim to distinguish those voices from one another.

For these outbound calls, `recording_channels="dual"` and
`recording_track="both"` use Twilio's track order: channel 1 is inbound audio
received from the remote side, while channel 2 is outbound patient-bot audio
generated through Twilio. The splitter preserves every audio frame, including
leading and internal silence, so each channel retains the original recording
timeline before timestamped segments are merged.

Install the pinned dependencies with the project virtual environment before a
separately authorized collection run:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then invoke the collector with an exact Call SID and existing fictional
scenario ID:

```powershell
& .\.venv\Scripts\python.exe .\collect_call_artifacts.py `
  --call-sid CA00000000000000000000000000000000 `
  --scenario-id schedule_routine_visit
```

Use `--output-root <folder>` to choose a different root. Existing call folders
are always rejected; the collector has no overwrite option. Running this
command loads `.env`, contacts both Twilio and OpenAI, downloads the selected
recording, and makes two sequential transcription requests. Compared with one
mixed-audio request, this can roughly double transcription cost and adds the
latency of both requests, but it makes call-leg attribution deterministic. If
either request fails, the collector does not retry or publish partial
artifacts; an already completed first request may still be billable. The
temporary dual-channel and split-channel WAV data remains in memory and is
discarded on success or failure. The command must only be run with explicit
authorization. Unit tests inject fictional provider boundaries and do not make
network requests.

Failures print only a stable processing category, for example
`CALL_ARTIFACT_ERROR stage=openai_transcription`. Provider details, response
bodies, signed URLs, credentials, and local secret values are never included.
Transcription parsing also prints a non-sensitive reason code, such as
`CALL_ARTIFACT_ERROR stage=transcription_parse reason=timestamps_missing`.
An explicitly empty transcribed channel is allowed when the other channel has
timestamped speech. Text without timestamped segments, malformed segments, and
two empty channels fail instead of producing an inaccurate or empty transcript.
Valid timestamped segments are stably sorted by start time before the two
channels are merged. Overlap is preserved, equal-start segments retain a
deterministic order, and no timestamps or segment text are changed to force a
non-overlapping timeline. Invalid numeric timestamps and reversed segment
bounds have distinct safe reason codes; an ordering that cannot be normalized
fails as `segment_order_invalid` rather than publishing a questionable result.
The collector does not retry automatically and publishes the call folder only
after every artifact has been validated and written successfully. Missing or
mono channel metadata, malformed WAV data, and digitally silent channels fail
as `CALL_ARTIFACT_ERROR stage=channel_audio_validation`.
