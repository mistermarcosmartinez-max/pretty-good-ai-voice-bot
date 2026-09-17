# Architecture

The system is a Python/FastAPI webhook and WebSocket service that starts a
Twilio outbound call only to a fixed, validated assessment destination. Twilio
creates a dual-channel recording and connects a bidirectional Media Stream to
the service; the bridge relays PCMU audio to and from an OpenAI Realtime session
that acts as a fictional patient. Each evaluation scenario supplies a focused
patient prompt with its own facts and outcome. Realtime was chosen because a
live, adaptive spoken exchange is more representative than prerecorded prompts
or a turn-by-turn batch pipeline, while the protected destination and strict
protocol validation keep the call boundary narrow.

After an authorized call, the collector preserves the MP3, splits the temporary
dual-channel WAV by call leg, transcribes each channel, and publishes a transcript
and metadata atomically. Call-leg attribution was chosen over diarization because
it can honestly distinguish remote audio from patient-bot audio; it cannot and
does not distinguish the healthcare agent, disclosures, or transferred greetings
when they share the remote channel. Tradeoffs remain: provider transcript timing
can overlap or arrive out of order, Realtime cancellation cannot retract audio
already played, transfer detection must be conservative enough to avoid muting
ordinary discussion, and Twilio buffer clearing is best effort. Local development
also requires a public HTTPS/WSS tunnel for provider callbacks. This is a focused
evaluation harness, not production-grade telephony infrastructure.
