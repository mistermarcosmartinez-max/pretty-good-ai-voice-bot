# Evaluation Findings

These findings prioritize recurring or outcome-relevant behavior in the target
healthcare agent. Timestamps are approximate and point reviewers to the relevant
recording passages. Transcripts are machine-generated aids; the linked audio is
the stronger source for audible behavior.

## Transfers announce patient support but reach the test-line ending

- **Severity:** High
- **Affected calls and evidence:**
  - Call 2: [transcript](artifacts/iteration_evidence/CA0fec9ebd41abc05350c3ac7d778ee491/transcript.md), [audio](artifacts/iteration_evidence/CA0fec9ebd41abc05350c3ac7d778ee491/recording.mp3), approximately 01:49–02:13.
  - Call 3: [transcript](artifacts/calls/CA56ac6e8ef6ed4a5f5b06b5534f7211bd/transcript.md), [audio](artifacts/calls/CA56ac6e8ef6ed4a5f5b06b5534f7211bd/recording.mp3), approximately 02:03–02:14.
  - Call 4: [transcript](artifacts/iteration_evidence/CA5c5e01ec13d8244136947788855dcbe5/transcript.md), [audio](artifacts/iteration_evidence/CA5c5e01ec13d8244136947788855dcbe5/recording.mp3), approximately 01:46–02:14.
  - Call 7: [transcript](artifacts/calls/CA1c2496c52efa8940f09a35946d1e88fd/transcript.md), [audio](artifacts/calls/CA1c2496c52efa8940f09a35946d1e88fd/recording.mp3), approximately 01:35–01:49.
  - Call 8: [transcript](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/transcript.md), [audio](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/recording.mp3), approximately 01:36–01:54.
  - Call 9: [transcript](artifacts/calls/CAb90ccf5f0d1d310fdd9090587f72e5d5/transcript.md), [audio](artifacts/calls/CAb90ccf5f0d1d310fdd9090587f72e5d5/recording.mp3), approximately 02:02–02:14.
  - Call 10: [transcript](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/transcript.md), [audio](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/recording.mp3), approximately 02:11–02:30.
  - Call 13: [transcript](artifacts/calls/CA31327225d8949a4d82bad9dc36d3f0dd/transcript.md), [audio](artifacts/calls/CA31327225d8949a4d82bad9dc36d3f0dd/recording.mp3), approximately 02:07–02:28.
- **Observed behavior:** The agent repeatedly said it would connect the caller to a patient support or clinic support team. The next destination was instead a short Pretty Good AI test-line greeting followed by goodbye.
- **Why it matters:** The caller is told that a human or support workflow will continue, but the call ends without the promised help. This is especially consequential after the automated flow has already failed to resolve the request.
- **Expected behavior:** Route the caller to the announced support destination, or clearly state that live support is unavailable and provide an accurate next step.
- **Evidence limitations:** The recordings establish the spoken promise and audible destination, but do not expose transfer configuration or backend routing. Call 2 also uses older diarization labels, so its exact speaker attribution is less reliable than the later dual-channel transcripts.

## New-patient scheduling follows an existing-patient lookup path

- **Severity:** High
- **Affected calls and evidence:** Call 8 ([transcript](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/transcript.md), [audio](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/recording.mp3), approximately 00:13–01:54) and Call 9 ([transcript](artifacts/calls/CAb90ccf5f0d1d310fdd9090587f72e5d5/transcript.md), [audio](artifacts/calls/CAb90ccf5f0d1d310fdd9090587f72e5d5/recording.mp3), approximately 00:14–02:14).
- **Observed behavior:** The caller explicitly requested a new-patient primary-care visit and said they had not visited the clinic before. The agent nevertheless asked for a phone number on file, attempted record lookup, failed to find a record, and did not schedule the requested visit.
- **Why it matters:** A valid new-patient request reaches a predictable dead end because the workflow assumes an existing chart.
- **Expected behavior:** Recognize the caller as a new patient, collect only information needed for new-patient scheduling, and schedule the visit or give an accurate next step.
- **Evidence limitations:** These are two scripted evaluation calls to one test endpoint. They demonstrate repeatability in this setup but do not establish behavior for every new-patient path.

## Insurance question is not answered

- **Severity:** Medium/High
- **Affected call and evidence:** Call 7 ([transcript](artifacts/calls/CA1c2496c52efa8940f09a35946d1e88fd/transcript.md), [audio](artifacts/calls/CA1c2496c52efa8940f09a35946d1e88fd/recording.mp3), approximately 00:13–01:49).
- **Observed behavior:** The caller asked whether a named plan was accepted. The agent entered identity and record-lookup questions, never answered the coverage question, then announced a transfer that ended at the test line.
- **Why it matters:** A straightforward pre-service coverage question becomes an unnecessary identity workflow and remains unresolved.
- **Expected behavior:** Answer from available plan information, explain that acceptance cannot be confirmed, or direct the caller to the appropriate verification channel without implying the question was resolved.
- **Evidence limitations:** The evaluation cannot determine whether plan data was unavailable to the agent or whether identity collection was required by an internal policy.

## Unexplained phone number is stated without clear confirmation

- **Severity:** Medium
- **Affected calls and evidence:** Call 7 ([transcript](artifacts/calls/CA1c2496c52efa8940f09a35946d1e88fd/transcript.md), [audio](artifacts/calls/CA1c2496c52efa8940f09a35946d1e88fd/recording.mp3), approximately 01:09–01:28), Call 10 ([transcript](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/transcript.md), [audio](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/recording.mp3), approximately 01:54–02:07), and Call 13 ([transcript](artifacts/calls/CA31327225d8949a4d82bad9dc36d3f0dd/transcript.md), [audio](artifacts/calls/CA31327225d8949a4d82bad9dc36d3f0dd/recording.mp3), approximately 01:45–02:02).
- **Observed behavior:** The agent stated a phone number that the patient bot had not spoken, then included it among details to confirm. The bot explicitly said it could not verify or had not supplied the number.
- **Why it matters:** Presenting an unexplained identifier as established patient data can confuse callers and creates an avoidable privacy concern.
- **Expected behavior:** Explain the source at an appropriate level—such as “the number you are calling from”—and ask the caller to confirm before treating it as verified record data.
- **Evidence limitations:** The number may legitimately have come from caller ID. The recordings do not reveal the agent's data source, so this finding concerns the unexplained presentation and lack of clear confirmation, not unauthorized data access.

## Unclear appointment request is not clarified

- **Severity:** Medium
- **Affected call and evidence:** Call 10 ([transcript](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/transcript.md), [audio](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/recording.mp3), approximately 00:13–02:30).
- **Observed behavior:** After the caller said only that they needed to make an appointment, the agent moved directly into identity and record lookup instead of asking what kind of appointment was needed. The request was never clarified or completed.
- **Why it matters:** Missing the caller's intent early produces a longer conversation and a failed outcome.
- **Expected behavior:** Ask a concise clarifying question about the visit type or need before choosing a scheduling workflow.
- **Evidence limitations:** The evaluation observes only the spoken interaction and cannot determine whether an upstream workflow forced identity collection before intent clarification.

## Audible voice and audio instability

- **Severity:** Medium
- **Affected calls and evidence:** Call 1 ([transcript](artifacts/calls/CA66298dc6c86cd477454b8038d84537c5/transcript.md), [audio](artifacts/calls/CA66298dc6c86cd477454b8038d84537c5/recording.mp3), sharp voice change around 00:43 on “Jordan”); Call 8 ([transcript](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/transcript.md), [audio](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/recording.mp3), distortion around 01:18–01:23); and Call 10 ([transcript](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/transcript.md), [audio](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/recording.mp3), agent-audio dropout around 01:57–02:00).
- **Observed behavior:** The target-agent voice changes abruptly or becomes distorted, and one passage drops out audibly.
- **Why it matters:** Instability makes details harder to understand and reduces confidence in a healthcare phone interaction.
- **Expected behavior:** Maintain a consistent voice and intelligible, continuous audio throughout each turn.
- **Evidence limitations:** The recordings confirm the audible result but do not isolate whether it originated in synthesis, telephony transport, media streaming, or recording.

## Lower-severity speech intelligibility issues

- **Severity:** Low
- **Affected calls and evidence:** Call 6 ([transcript](artifacts/calls/CAbb29ea2bd992136871398857cf75be7c/transcript.md), [audio](artifacts/calls/CAbb29ea2bd992136871398857cf75be7c/recording.mp3), approximately 00:26–00:30, heard/transcribed as “freecation parking”); Call 8 ([transcript](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/transcript.md), [audio](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/recording.mp3), approximately 01:36–01:40, “falls up”); and Call 10 ([transcript](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/transcript.md), [audio](artifacts/calls/CA5ccc27450be532f5b4a568088b7fd2b6/recording.mp3), approximately 00:40–00:45 and 02:09–02:11, “Jania” and “perceive”).
- **Observed behavior:** Several isolated words or short phrases were difficult to understand or sounded like the examples above.
- **Why it matters:** Even brief pronunciation errors can alter names, instructions, or the perceived meaning of a response.
- **Expected behavior:** Render names and common workflow phrases consistently and intelligibly; ask for or use spelling when needed.
- **Evidence limitations:** The quoted spellings describe what was heard or produced by transcription, not a definitive diagnosis of the intended generated text.

## Bot iteration evidence

The items below are issues found in this repository's patient simulator or
artifact pipeline. They are retained to show testing and correction history and
must not be attributed to Pretty Good AI's healthcare agent.

- Early validation exposed an opening cutoff and long patient-response pauses. The opening was protected during disclosures, and server-VAD timing plus concise prompt guidance were refined. See the [preliminary connectivity call](artifacts/iteration_evidence/CA1fb581d80c0a1db25cfa6ea5618f5387/transcript.md) and [initial full validation call](artifacts/iteration_evidence/CA54f7993c3270d706891a98f6796418d3/transcript.md).
- Call 2 repeated “Sure. Take your time while you pull that up” at approximately 01:38–01:44 ([transcript](artifacts/iteration_evidence/CA0fec9ebd41abc05350c3ac7d778ee491/transcript.md), [audio](artifacts/iteration_evidence/CA0fec9ebd41abc05350c3ac7d778ee491/recording.mp3)). Per-call event-ID deduplication was added and bounded.
- Call 4 reproduced patient audio continuing over a confirmed transfer at approximately 02:05–02:09 ([transcript](artifacts/iteration_evidence/CA5c5e01ec13d8244136947788855dcbe5/transcript.md), [audio](artifacts/iteration_evidence/CA5c5e01ec13d8244136947788855dcbe5/recording.mp3)). Confirmed remote-transfer handling now cancels generation, clears best-effort buffered audio, and suppresses subsequent patient output without enabling ordinary interruption globally.
- Call 8 cut a patient transfer acceptance off at “Please connect me to” around 01:45 ([transcript](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/transcript.md), [audio](artifacts/iteration_evidence/CA11da9e9198d28de99ea8acd40d751b67/recording.mp3)). Patient-output transcript cancellation was removed so concise transfer requests can finish; confirmed remote transfers still stop genuine overlap.
- Call 3 exposed unreliable diarization speaker attribution ([transcript](artifacts/calls/CA56ac6e8ef6ed4a5f5b06b5534f7211bd/transcript.md), [audio](artifacts/calls/CA56ac6e8ef6ed4a5f5b06b5534f7211bd/recording.mp3)). Later collection splits the dual-channel recording and uses honest `Remote side` and `Patient bot` call-leg labels. It does not claim to separate voices sharing the remote channel.
- Call 11 artifact collection first lacked a safe parser detail and then reported `timestamp_order`. The parser now supplies non-sensitive reason codes, validates bounds independently, preserves legitimate overlap, and stably normalizes valid out-of-order segments without altering timestamps or text. The final [Call 11 artifacts](artifacts/calls/CAf2f8a6db01100db529f111c93b642c72/) were collected afterward. Although its scenario ID references interruption recovery, no interruption was audible; it is evidence only of a clean general-information exchange.
- Calls 12 ([transcript](artifacts/calls/CA5b408aa5d1176b94474d63b8a4813438/transcript.md), [audio](artifacts/calls/CA5b408aa5d1176b94474d63b8a4813438/recording.mp3)) and 13 ([transcript](artifacts/calls/CA31327225d8949a4d82bad9dc36d3f0dd/transcript.md), [audio](artifacts/calls/CA31327225d8949a4d82bad9dc36d3f0dd/recording.mp3)) were user-verified as clean with respect to the simulator's cutoff, duplication, and transfer-overlap regressions. This does not mean the target agent had no findings; Call 13 still contributes evidence above.
