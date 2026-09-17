import contextlib
import importlib
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import sys
import tempfile
import unittest
import wave
from unittest import mock

import collect_call_artifacts as collector
from openai.types.audio import (
    Transcription,
    TranscriptionDiarized,
    TranscriptionDiarizedSegment,
    TranscriptionVerbose,
)


CALL_SID = "CA" + "1" * 32
RECORDING_SID = "RE" + "2" * 32
SCENARIO_ID = "schedule_routine_visit"
DESTINATION = "+18054398008"
MP3_BYTES = b"ID3-fictional-audio"


def make_dual_wav(inbound_samples=b"\x01\x02", outbound_samples=b"\x11\x12"):
    frames = bytes(
        sample
        for pair in zip(inbound_samples, outbound_samples)
        for sample in pair
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(2)
        destination.setsampwidth(1)
        destination.setframerate(8000)
        destination.writeframes(frames)
    return output.getvalue()


DUAL_WAV_BYTES = make_dual_wav()


def make_g711_dual_wav(audio_format=7):
    frames = b"\x01\x11\x02\x12"
    format_payload = struct.pack("<HHIIHH", audio_format, 2, 8000, 16000, 2, 8)
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(format_payload))
        + format_payload
        + b"fact"
        + struct.pack("<I", 4)
        + struct.pack("<I", 2)
        + b"data"
        + struct.pack("<I", len(frames))
        + frames
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wav_chunk(audio, expected_name):
    offset = 12
    while offset < len(audio):
        name = audio[offset : offset + 4]
        size = struct.unpack_from("<I", audio, offset + 4)[0]
        payload = audio[offset + 8 : offset + 8 + size]
        if name == expected_name:
            return payload
        offset += 8 + size + (size % 2)
    raise AssertionError(f"Missing WAV chunk {expected_name!r}")


class FakeCalls:
    def __init__(self, call):
        self.call = call
        self.requested_sids = []

    def __call__(self, call_sid):
        self.requested_sids.append(call_sid)
        return self

    def fetch(self):
        return self.call


class FakeRecordings:
    def __init__(self, recordings):
        self.recordings = recordings
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.recordings


class FakeTwilioClient:
    def __init__(self, call=None, recordings=None):
        self.calls = FakeCalls(
            call
            or SimpleNamespace(
                sid=CALL_SID,
                to=DESTINATION,
                status="completed",
                duration="82",
            )
        )
        self.recordings = FakeRecordings(
            recordings
            if recordings is not None
            else [
                SimpleNamespace(
                    sid=RECORDING_SID,
                    call_sid=CALL_SID,
                    status="completed",
                    duration="80",
                    channels=2,
                )
            ]
        )


class FakeTranscriptions:
    def __init__(self, response=None, error=None, error_on_call=None):
        self.response = response or self._default_response
        self.error = error
        self.error_on_call = error_on_call
        self.calls = []

    @staticmethod
    def _default_response(file_name):
        if file_name == "remote-side.wav":
            return {
                "segments": [
                    {
                        "start": 0,
                        "end": 1.25,
                        "speaker": "speaker_0",
                        "text": " Hello from the clinic. ",
                    }
                ]
            }
        return {
            "segments": [
                {
                    "start": 61.2,
                    "end": 62.005,
                    "speaker": "speaker_0",
                    "text": "Tuesday works well.",
                }
            ]
        }

    def create(self, **kwargs):
        captured = dict(kwargs)
        file_name = kwargs["file"].name
        captured["file_name"] = file_name
        captured["file_bytes"] = kwargs["file"].read()
        captured.pop("file")
        self.calls.append(captured)
        if self.error is not None or len(self.calls) == self.error_on_call:
            raise self.error or RuntimeError("fictional transcription failure")
        if callable(self.response):
            return self.response(file_name)
        return self.response


class FakeOpenAIClient:
    def __init__(self, transcriptions=None):
        self.transcriptions = transcriptions or FakeTranscriptions()
        self.audio = SimpleNamespace(transcriptions=self.transcriptions)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.output_root = Path(self.temporary_directory.name) / "calls"
        self.settings = {
            "OPENAI_API_KEY": "fictional-openai-secret",
            "TWILIO_ACCOUNT_SID": "AC" + "3" * 32,
            "TWILIO_AUTH_TOKEN": "fictional-twilio-secret",
        }
        self.twilio_client = FakeTwilioClient()
        self.openai_client = FakeOpenAIClient()
        self.settings_calls = 0
        self.twilio_builder_settings = []
        self.openai_builder_keys = []
        self.download_calls = []
        self.channel_download_calls = []

    def tearDown(self):
        self.temporary_directory.cleanup()

    def settings_loader(self):
        self.settings_calls += 1
        return dict(self.settings)

    def twilio_builder(self, settings):
        self.twilio_builder_settings.append(dict(settings))
        return self.twilio_client

    def downloader(self, recording_sid, account_sid, auth_token):
        self.download_calls.append((recording_sid, account_sid, auth_token))
        return MP3_BYTES

    def channel_downloader(self, recording_sid, account_sid, auth_token):
        self.channel_download_calls.append(
            (recording_sid, account_sid, auth_token)
        )
        return DUAL_WAV_BYTES

    def openai_builder(self, api_key):
        self.openai_builder_keys.append(api_key)
        return self.openai_client

    def collect(self, **overrides):
        arguments = {
            "call_sid": CALL_SID,
            "scenario_id": SCENARIO_ID,
            "output_root": self.output_root,
            "settings_loader": self.settings_loader,
            "twilio_client_builder": self.twilio_builder,
            "recording_downloader": self.downloader,
            "channel_recording_downloader": self.channel_downloader,
            "openai_client_builder": self.openai_builder,
        }
        arguments.update(overrides)
        return collector.collect_call_artifacts(**arguments)

    def test_collects_exact_call_recording_and_transcription_request(self):
        target = self.collect()

        self.assertEqual(target, self.output_root / CALL_SID)
        self.assertEqual(self.settings_calls, 1)
        self.assertEqual(self.twilio_client.calls.requested_sids, [CALL_SID])
        self.assertEqual(
            self.twilio_client.recordings.list_calls,
            [{"call_sid": CALL_SID, "limit": 50}],
        )
        self.assertEqual(
            self.download_calls,
            [
                (
                    RECORDING_SID,
                    self.settings["TWILIO_ACCOUNT_SID"],
                    self.settings["TWILIO_AUTH_TOKEN"],
                )
            ],
        )
        self.assertEqual(self.channel_download_calls, self.download_calls)
        self.assertEqual(self.openai_builder_keys, [self.settings["OPENAI_API_KEY"]])
        self.assertEqual(
            self.openai_client.transcriptions.calls,
            [
                {
                    "model": "gpt-4o-transcribe-diarize",
                    "response_format": "diarized_json",
                    "chunking_strategy": "auto",
                    "language": "en",
                    "file_name": "remote-side.wav",
                    "file_bytes": mock.ANY,
                },
                {
                    "model": "gpt-4o-transcribe-diarize",
                    "response_format": "diarized_json",
                    "chunking_strategy": "auto",
                    "language": "en",
                    "file_name": "patient-bot.wav",
                    "file_bytes": mock.ANY,
                },
            ],
        )

    def test_writes_mp3_transcript_and_metadata(self):
        target = self.collect()

        self.assertEqual((target / "recording.mp3").read_bytes(), MP3_BYTES)
        transcript = (target / "transcript.md").read_text(encoding="utf-8")
        self.assertIn(
            "[00:00:00.000 - 00:00:01.250] Remote side", transcript
        )
        self.assertIn(
            "[00:01:01.200 - 00:01:02.005] Patient bot", transcript
        )
        self.assertIn("Separate voices sharing the Remote side", transcript)

        metadata = json.loads(
            (target / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata,
            {
                "artifact_files": [
                    "recording.mp3",
                    "transcript.md",
                    "metadata.json",
                ],
                "call_duration_seconds": 82,
                "call_sid": CALL_SID,
                "recording_duration_seconds": 80,
                "recording_channels": 2,
                "recording_sid": RECORDING_SID,
                "recording_status": "completed",
                "scenario_id": SCENARIO_ID,
                "status": "completed",
            },
        )

    def test_existing_folder_is_refused_before_loading_settings(self):
        target = self.output_root / CALL_SID
        target.mkdir(parents=True)
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(
            collector.ArtifactCollectionError,
            "CALL_ARTIFACT_ERROR stage=artifact_validation",
        ):
            self.collect()

        self.assertEqual(self.settings_calls, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_invalid_request_is_rejected_before_settings_or_providers(self):
        for arguments in (
            {"call_sid": "CA-not-a-sid"},
            {"scenario_id": "unknown-scenario"},
            {"scenario_id": None},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=artifact_validation",
                ):
                    self.collect(**arguments)
        self.assertEqual(self.settings_calls, 0)
        self.assertEqual(self.twilio_client.calls.requested_sids, [])

    def test_call_must_match_sid_and_protected_destination(self):
        invalid_calls = (
            SimpleNamespace(
                sid="CA" + "9" * 32,
                to=DESTINATION,
                status="completed",
                duration="82",
            ),
            SimpleNamespace(
                sid=CALL_SID,
                to="+15555550123",
                status="completed",
                duration="82",
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                self.twilio_client = FakeTwilioClient(call=call)
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=artifact_validation",
                ) as raised:
                    self.collect()
                self.assertNotIn(str(call.to), str(raised.exception))
                self.assertFalse((self.output_root / CALL_SID).exists())

    def test_recording_must_be_completed_and_belong_to_exact_call(self):
        recordings = [
            SimpleNamespace(
                sid=RECORDING_SID,
                call_sid="CA" + "9" * 32,
                status="completed",
                duration="80",
            ),
            SimpleNamespace(
                sid="RE" + "4" * 32,
                call_sid=CALL_SID,
                status="processing",
                duration="80",
            ),
        ]
        self.twilio_client = FakeTwilioClient(recordings=recordings)

        with self.assertRaisesRegex(
            collector.ArtifactCollectionError,
            "CALL_ARTIFACT_ERROR stage=twilio_recording_lookup",
        ):
            self.collect()

        self.assertEqual(self.download_calls, [])
        self.assertFalse((self.output_root / CALL_SID).exists())

    def test_download_must_return_nonempty_bytes(self):
        for invalid_audio in (b"", "not-bytes", None):
            with self.subTest(invalid_audio=invalid_audio):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=recording_download",
                ):
                    self.collect(
                        recording_downloader=lambda *_: invalid_audio,
                    )
                self.assertFalse((self.output_root / CALL_SID).exists())

    def test_missing_or_non_dual_channel_metadata_fails_before_download(self):
        for channels in (None, 1, "2", 2.0, True):
            with self.subTest(channels=channels):
                self.twilio_client = FakeTwilioClient(
                    recordings=[
                        SimpleNamespace(
                            sid=RECORDING_SID,
                            call_sid=CALL_SID,
                            status="completed",
                            duration="80",
                            channels=channels,
                        )
                    ]
                )
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=channel_audio_validation",
                ):
                    self.collect()

        self.assertEqual(self.download_calls, [])

    def test_splits_stereo_wav_into_inbound_and_outbound_mono_files(self):
        inbound, outbound = collector._split_dual_channel_wav(
            make_dual_wav(b"\x01\x02\x03", b"\x11\x12\x13")
        )

        decoded = []
        for channel in (inbound, outbound):
            with wave.open(io.BytesIO(channel), "rb") as source:
                decoded.append(
                    (
                        source.getnchannels(),
                        source.getsampwidth(),
                        source.getframerate(),
                        source.readframes(source.getnframes()),
                    )
                )
        self.assertEqual(decoded[0], (1, 1, 8000, b"\x01\x02\x03"))
        self.assertEqual(decoded[1], (1, 1, 8000, b"\x11\x12\x13"))

    def test_split_preserves_leading_silence_frame_count_and_timeline(self):
        inbound, outbound = collector._split_dual_channel_wav(
            make_dual_wav(
                b"\x80\x80\x01\x02", b"\x80\x03\x04\x05"
            )
        )

        for channel, expected_frames in (
            (inbound, b"\x80\x80\x01\x02"),
            (outbound, b"\x80\x03\x04\x05"),
        ):
            with wave.open(io.BytesIO(channel), "rb") as source:
                self.assertEqual(source.getnframes(), 4)
                self.assertEqual(source.getframerate(), 8000)
                self.assertEqual(source.readframes(4), expected_frames)

    def test_split_preserves_g711_encoding_and_non_audio_chunks(self):
        for audio_format in (6, 7):
            with self.subTest(audio_format=audio_format):
                inbound, outbound = collector._split_dual_channel_wav(
                    make_g711_dual_wav(audio_format)
                )
                for channel, expected_frames in (
                    (inbound, b"\x01\x02"),
                    (outbound, b"\x11\x12"),
                ):
                    format_payload = wav_chunk(channel, b"fmt ")
                    self.assertEqual(
                        struct.unpack_from("<HHIIHH", format_payload),
                        (audio_format, 1, 8000, 8000, 1, 8),
                    )
                    self.assertEqual(wav_chunk(channel, b"fact"), struct.pack("<I", 2))
                    self.assertEqual(wav_chunk(channel, b"data"), expected_frames)

    def test_channel_transcript_uses_call_leg_not_diarization_speaker(self):
        remote = {
            "segments": [
                {
                    "start": 0,
                    "end": 1,
                    "speaker": "same-model-label",
                    "text": "How can I help?",
                },
                {
                    "start": 4,
                    "end": 5,
                    "speaker": "different-remote-voice",
                    "text": "You have reached the transferred line.",
                },
            ]
        }
        patient = SimpleNamespace(
            segments=[
                SimpleNamespace(
                    start=2,
                    end=3,
                    speaker="same-model-label",
                    text="Please cancel my appointment.",
                )
            ]
        )

        transcript = collector.format_channel_transcript(
            remote, patient, SCENARIO_ID, CALL_SID
        )

        transcript_lines = [
            line for line in transcript.splitlines() if line.startswith("**[")
        ]
        self.assertIn("[00:00:00.000", transcript_lines[0])
        self.assertIn("Remote side:** How can I help?", transcript_lines[0])
        self.assertIn("[00:00:02.000", transcript_lines[1])
        self.assertIn("Patient bot:", transcript_lines[1])
        self.assertIn("[00:00:04.000", transcript_lines[2])
        self.assertIn("Remote side:** How can I help?", transcript)
        self.assertIn(
            "Remote side:** You have reached the transferred line.", transcript
        )
        self.assertIn(
            "Patient bot:** Please cancel my appointment.", transcript
        )
        self.assertNotIn("same-model-label", transcript)
        self.assertNotIn("different-remote-voice", transcript)

    def test_adjacent_and_overlapping_segments_are_preserved(self):
        remote = {
            "segments": [
                {"start": 0, "end": 2, "text": "First."},
                {"start": 1, "end": 3, "text": "Overlapping."},
                {"start": 3, "end": 4, "text": "Adjacent."},
            ]
        }

        transcript = collector.format_channel_transcript(
            remote, {"segments": []}, SCENARIO_ID, CALL_SID
        )
        lines = [
            line for line in transcript.splitlines() if line.startswith("**[")
        ]

        self.assertEqual(
            lines,
            [
                "**[00:00:00.000 - 00:00:02.000] Remote side:** First.",
                "**[00:00:01.000 - 00:00:03.000] Remote side:** Overlapping.",
                "**[00:00:03.000 - 00:00:04.000] Remote side:** Adjacent.",
            ],
        )

    def test_unsorted_and_equal_start_segments_are_stably_normalized(self):
        remote = {
            "segments": [
                {"start": 4, "end": 5, "text": "Late."},
                {"start": 1, "end": 2, "text": "Equal first."},
                {"start": 1, "end": 1.5, "text": "Equal second."},
                {"start": 3, "end": 4, "text": "Middle."},
            ]
        }

        transcript = collector.format_channel_transcript(
            remote, {"segments": []}, SCENARIO_ID, CALL_SID
        )
        lines = [
            line for line in transcript.splitlines() if line.startswith("**[")
        ]

        self.assertEqual(
            lines,
            [
                "**[00:00:01.000 - 00:00:02.000] Remote side:** Equal first.",
                "**[00:00:01.000 - 00:00:01.500] Remote side:** Equal second.",
                "**[00:00:03.000 - 00:00:04.000] Remote side:** Middle.",
                "**[00:00:04.000 - 00:00:05.000] Remote side:** Late.",
            ],
        )

    def test_cross_channel_merge_is_chronological_and_deterministic(self):
        remote = {
            "segments": [
                {"start": 2, "end": 4, "text": "Remote equal."},
                {"start": 0.5, "end": 3, "text": "Remote first."},
            ]
        }
        patient = {
            "segments": [
                {"start": 2, "end": 2.5, "text": "Patient equal."},
                {"start": 1, "end": 5, "text": "Patient overlap."},
            ]
        }

        transcript = collector.format_channel_transcript(
            remote, patient, SCENARIO_ID, CALL_SID
        )
        lines = [
            line for line in transcript.splitlines() if line.startswith("**[")
        ]

        self.assertEqual(
            lines,
            [
                "**[00:00:00.500 - 00:00:03.000] Remote side:** Remote first.",
                "**[00:00:01.000 - 00:00:05.000] Patient bot:** Patient overlap.",
                "**[00:00:02.000 - 00:00:04.000] Remote side:** Remote equal.",
                "**[00:00:02.000 - 00:00:02.500] Patient bot:** Patient equal.",
            ],
        )

    def test_channel_audio_and_segments_are_strictly_validated(self):
        mono = io.BytesIO()
        with wave.open(mono, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(1)
            destination.setframerate(8000)
            destination.writeframes(b"\x01")
        with self.assertRaises(ValueError):
            collector._split_dual_channel_wav(mono.getvalue())

        valid = {"segments": [{"start": 0, "end": 1, "text": "Hi"}]}
        invalid_responses = (
            {"segments": [{"start": 2, "end": 1, "text": "Hi"}]},
            {"segments": [{"start": 0, "end": 1, "text": "   "}]},
        )
        for invalid in invalid_responses:
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=transcription_parse",
                ):
                    collector.format_channel_transcript(
                        invalid, valid, SCENARIO_ID, CALL_SID
                    )

    def test_empty_channel_variants_are_allowed_when_other_channel_has_segments(self):
        populated = {
            "segments": [{"start": 1, "end": 2, "text": "Hello."}]
        }
        empty_channels = (
            {"segments": []},
            {"segments": [], "text": ""},
            {"text": ""},
            {"text": None},
            {"segments": None, "text": None},
            Transcription(text=""),
            TranscriptionVerbose(
                duration=2.0,
                language="en",
                text="",
                segments=None,
            ),
        )
        for empty_channel in empty_channels:
            with self.subTest(response_type=type(empty_channel).__name__):
                transcript = collector.format_channel_transcript(
                    empty_channel, populated, SCENARIO_ID, CALL_SID
                )
                self.assertNotIn("Remote side:**", transcript)
                self.assertIn("Patient bot:** Hello.", transcript)

    def test_installed_sdk_diarized_response_objects_are_supported(self):
        response = TranscriptionDiarized(
            duration=2.0,
            task="transcribe",
            text="Hello.",
            segments=[
                TranscriptionDiarizedSegment(
                    id="segment-1",
                    start=0.25,
                    end=1.5,
                    speaker="A",
                    text="Hello.",
                    type="transcript.text.segment",
                )
            ],
        )
        transcript = collector.format_channel_transcript(
            response, {"segments": []}, SCENARIO_ID, CALL_SID
        )
        self.assertIn("Remote side:** Hello.", transcript)
        self.assertNotIn("Patient bot:**", transcript)

    def test_missing_timestamps_never_publish_untimed_response_text(self):
        populated = {
            "segments": [{"start": 1, "end": 2, "text": "Hello."}]
        }
        responses = (
            {"segments": None, "text": "Spoken words."},
            {"segments": [], "text": "Spoken words."},
            Transcription(text="Spoken words."),
            TranscriptionVerbose(
                duration=2.0,
                language="en",
                text="Spoken words.",
                segments=None,
            ),
        )
        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "reason=timestamps_missing",
                ):
                    collector.format_channel_transcript(
                        response, populated, SCENARIO_ID, CALL_SID
                    )

    def test_response_and_segment_shape_failures_have_specific_safe_reasons(self):
        class BrokenResponse:
            @property
            def segments(self):
                raise RuntimeError("private response detail")

        populated = {
            "segments": [{"start": 1, "end": 2, "text": "Hello."}]
        }
        invalid_cases = (
            (None, "response_missing"),
            ({}, "segments_missing"),
            ({"segments": None}, "segments_missing"),
            ({"segments": 7}, "segments_invalid"),
            (BrokenResponse(), "response_shape"),
            (
                {"segments": [{"start": None, "end": 1, "text": "Hi"}]},
                "timestamp_invalid",
            ),
            (
                {"segments": [{"start": float("nan"), "end": 1, "text": "Hi"}]},
                "timestamp_invalid",
            ),
            (
                {"segments": [{"start": 0, "end": float("inf"), "text": "Hi"}]},
                "timestamp_invalid",
            ),
            (
                {"segments": [{"start": -0.1, "end": 1, "text": "Hi"}]},
                "timestamp_invalid",
            ),
            (
                {"segments": [{"start": 2, "end": 1, "text": "Hi"}]},
                "segment_bounds_invalid",
            ),
            (
                {"segments": [{"start": 0, "end": 1, "text": None}]},
                "segment_text_invalid",
            ),
        )
        for response, reason in invalid_cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError, f"reason={reason}"
                ):
                    collector.format_channel_transcript(
                        response, populated, SCENARIO_ID, CALL_SID
                    )

    def test_unusable_ordering_has_a_distinct_safe_reason(self):
        with self.assertRaisesRegex(
            collector.ArtifactCollectionError,
            "reason=segment_order_invalid",
        ):
            collector._stable_chronological_segments(
                [(0, 1)], key=lambda _item: 1 / 0
            )

    def test_both_empty_channels_fail_instead_of_publishing_empty_transcript(self):
        with self.assertRaisesRegex(
            collector.ArtifactCollectionError, "reason=no_segments"
        ):
            collector.format_channel_transcript(
                {"segments": []},
                {"segments": None, "text": None},
                SCENARIO_ID,
                CALL_SID,
            )

    def test_malformed_mono_and_silent_channel_audio_fail_before_openai(self):
        mono = io.BytesIO()
        with wave.open(mono, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(1)
            destination.setframerate(8000)
            destination.writeframes(b"\x01\x02")
        invalid_channel_audio = (
            b"not-a-wave",
            DUAL_WAV_BYTES[:-1],
            mono.getvalue(),
            make_g711_dual_wav(99),
            make_dual_wav(b"\x80\x80", b"\x01\x02"),
            make_dual_wav(b"\x01\x02", b"\x80\x80"),
        )

        for audio in invalid_channel_audio:
            with self.subTest(audio_length=len(audio)):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=channel_audio_validation",
                ):
                    self.collect(
                        channel_recording_downloader=lambda *_args: audio
                    )
                self.assertEqual(self.openai_builder_keys, [])
                self.assertFalse((self.output_root / CALL_SID).exists())
                self.assertEqual(list(self.output_root.iterdir()), [])

    def test_first_transcription_failure_skips_second_request(self):
        transcriptions = FakeTranscriptions(error_on_call=1)
        self.openai_client = FakeOpenAIClient(transcriptions)

        with self.assertRaisesRegex(
            collector.ArtifactCollectionError,
            "CALL_ARTIFACT_ERROR stage=openai_transcription",
        ):
            self.collect()

        self.assertEqual(len(transcriptions.calls), 1)
        self.assertEqual(transcriptions.calls[0]["file_name"], "remote-side.wav")
        self.assertEqual(list(self.output_root.iterdir()), [])

    def test_partial_transcription_failure_is_atomic_and_not_retried(self):
        transcriptions = FakeTranscriptions(error_on_call=2)
        self.openai_client = FakeOpenAIClient(transcriptions)

        with self.assertRaisesRegex(
            collector.ArtifactCollectionError,
            "CALL_ARTIFACT_ERROR stage=openai_transcription",
        ):
            self.collect()

        self.assertEqual(len(transcriptions.calls), 2)
        self.assertEqual(
            [call["file_name"] for call in transcriptions.calls],
            ["remote-side.wav", "patient-bot.wav"],
        )
        self.assertFalse((self.output_root / CALL_SID).exists())
        self.assertEqual(list(self.output_root.iterdir()), [])

    def test_success_publishes_no_temporary_or_channel_wav_files(self):
        target = self.collect()

        self.assertEqual(
            sorted(path.name for path in target.iterdir()),
            ["metadata.json", "recording.mp3", "transcript.md"],
        )
        self.assertEqual(
            sorted(path.name for path in self.output_root.iterdir()),
            [CALL_SID],
        )

    def test_normalizes_three_source_speakers_in_first_seen_order(self):
        transcription = {
            "segments": [
                {
                    "start": 0,
                    "end": 1,
                    "speaker": "disclosure_voice",
                    "text": "This call may be recorded.",
                },
                {
                    "start": 1,
                    "end": 2,
                    "speaker": "agent-17",
                    "text": "How can I help?",
                },
                {
                    "start": 2,
                    "end": 3,
                    "speaker": "caller_9",
                    "text": "I need an appointment.",
                },
                {
                    "start": 3,
                    "end": 4,
                    "speaker": "agent-17",
                    "text": "Certainly.",
                },
            ]
        }

        transcript = collector.format_diarized_transcript(
            transcription, SCENARIO_ID, CALL_SID
        )

        speaker_lines = [
            line for line in transcript.splitlines() if line.startswith("**[")
        ]
        self.assertIn("Speaker A:** This call may be recorded.", speaker_lines[0])
        self.assertIn("Speaker B:** How can I help?", speaker_lines[1])
        self.assertIn("Speaker C:** I need an appointment.", speaker_lines[2])
        self.assertIn("Speaker B:** Certainly.", speaker_lines[3])
        for source_label in ("disclosure_voice", "agent-17", "caller_9"):
            self.assertNotIn(source_label, transcript)

    def test_sdk_response_objects_and_existing_ab_labels_are_supported(self):
        transcription = SimpleNamespace(
            segments=[
                SimpleNamespace(start=0, end=1, speaker="A", text="Hello."),
                SimpleNamespace(start=1, end=2, speaker="B", text="Hi."),
                SimpleNamespace(start=2, end=3, speaker="A", text="Thanks."),
            ]
        )

        transcript = collector.format_diarized_transcript(
            transcription, SCENARIO_ID, CALL_SID
        )

        self.assertEqual(transcript.count("Speaker A:"), 2)
        self.assertEqual(transcript.count("Speaker B:"), 1)

    def test_unsorted_diarization_is_stable_and_labeled_chronologically(self):
        transcription = {
            "segments": [
                {
                    "start": 3,
                    "end": 4,
                    "speaker": "later-voice",
                    "text": "Later.",
                },
                {
                    "start": 1,
                    "end": 2,
                    "speaker": "earlier-voice",
                    "text": "Equal first.",
                },
                {
                    "start": 1,
                    "end": 1.5,
                    "speaker": "later-voice",
                    "text": "Equal second.",
                },
            ]
        }

        transcript = collector.format_diarized_transcript(
            transcription, SCENARIO_ID, CALL_SID
        )
        lines = [
            line for line in transcript.splitlines() if line.startswith("**[")
        ]

        self.assertEqual(
            lines,
            [
                "**[00:00:01.000 - 00:00:02.000] Speaker A:** Equal first.",
                "**[00:00:01.000 - 00:00:01.500] Speaker B:** Equal second.",
                "**[00:00:03.000 - 00:00:04.000] Speaker B:** Later.",
            ],
        )

    def test_malformed_diarization_is_rejected_without_publishing(self):
        invalid_segments = (
            (
                [{"start": 2, "end": 1, "speaker": "A", "text": "Hi"}],
                "segment_bounds_invalid",
            ),
            (
                [{"start": 0, "end": 1, "speaker": "A", "text": "   "}],
                "segment_text_invalid",
            ),
            ([], "no_segments"),
        )
        for segments, reason in invalid_segments:
            with self.subTest(reason=reason):
                transcriptions = FakeTranscriptions(response={"segments": segments})
                self.openai_client = FakeOpenAIClient(transcriptions)
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    f"stage=transcription_parse reason={reason}",
                ):
                    self.collect()
                self.assertFalse((self.output_root / CALL_SID).exists())
                self.assertEqual(list(self.output_root.iterdir()), [])

    def test_malformed_speaker_labels_are_rejected(self):
        for speaker in (None, "", "   ", 7, b"A"):
            with self.subTest(speaker=speaker):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    "CALL_ARTIFACT_ERROR stage=transcription_parse",
                ):
                    collector.format_diarized_transcript(
                        {
                            "segments": [
                                {
                                    "start": 0,
                                    "end": 1,
                                    "speaker": speaker,
                                    "text": "Hello.",
                                }
                            ]
                        },
                        SCENARIO_ID,
                        CALL_SID,
                    )

    def test_provider_failures_are_sanitized_and_leave_no_artifacts(self):
        private_detail = "private-provider-detail-987"
        failures = (
            (
                "twilio_call_fetch",
                {
                    "twilio_client_builder": lambda _settings: (
                        _ for _ in ()
                    ).throw(RuntimeError(private_detail))
                },
            ),
            (
                "recording_download",
                {
                    "recording_downloader": lambda *_args: (
                        _ for _ in ()
                    ).throw(RuntimeError(private_detail))
                },
            ),
            (
                "openai_transcription",
                {
                    "openai_client_builder": lambda _key: (
                        _ for _ in ()
                    ).throw(RuntimeError(private_detail))
                },
            ),
        )
        for stage, overrides in failures:
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(
                    collector.ArtifactCollectionError,
                    f"CALL_ARTIFACT_ERROR stage={stage}",
                ) as raised:
                    self.collect(**overrides)
                self.assertNotIn(private_detail, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertFalse((self.output_root / CALL_SID).exists())

        self.openai_client = FakeOpenAIClient(
            FakeTranscriptions(error=RuntimeError(private_detail))
        )
        with self.assertRaises(collector.ArtifactCollectionError) as raised:
            self.collect()
        self.assertEqual(
            str(raised.exception),
            "CALL_ARTIFACT_ERROR stage=openai_transcription",
        )
        self.assertNotIn(private_detail, str(raised.exception))
        self.assertFalse((self.output_root / CALL_SID).exists())

    def test_every_failure_stage_is_stable_private_and_unpublished(self):
        private_detail = (
            "Bearer private-api-key Basic private-token "
            "https://signed.example.invalid/private?signature=secret"
        )

        def fail(*_args, **_kwargs):
            raise RuntimeError(private_detail)

        cases = (
            ("settings_load", {"settings_loader": fail}),
            ("twilio_call_fetch", {"twilio_client_builder": fail}),
            ("recording_download", {"recording_downloader": fail}),
            ("openai_transcription", {"openai_client_builder": fail}),
        )
        for stage, overrides in cases:
            with self.subTest(stage=stage):
                with self.assertRaises(collector.ArtifactCollectionError) as raised:
                    self.collect(**overrides)
                self.assertEqual(
                    str(raised.exception),
                    f"CALL_ARTIFACT_ERROR stage={stage}",
                )
                self.assertNotIn(private_detail, str(raised.exception))
                self.assertFalse((self.output_root / CALL_SID).exists())

        self.twilio_client = FakeTwilioClient()
        self.twilio_client.recordings.list = fail
        with self.assertRaises(collector.ArtifactCollectionError) as raised:
            self.collect()
        self.assertEqual(
            str(raised.exception),
            "CALL_ARTIFACT_ERROR stage=twilio_recording_lookup",
        )

        self.twilio_client = FakeTwilioClient(
            call=SimpleNamespace(
                sid=CALL_SID,
                to="+15555550123",
                status="completed",
                duration="82",
            )
        )
        with self.assertRaises(collector.ArtifactCollectionError) as raised:
            self.collect()
        self.assertEqual(
            str(raised.exception),
            "CALL_ARTIFACT_ERROR stage=artifact_validation",
        )

        self.twilio_client = FakeTwilioClient()
        self.openai_client = FakeOpenAIClient(
            FakeTranscriptions(response={"segments": []})
        )
        with self.assertRaises(collector.ArtifactCollectionError) as raised:
            self.collect()
        self.assertEqual(
            str(raised.exception),
            "CALL_ARTIFACT_ERROR stage=transcription_parse reason=no_segments",
        )

        self.openai_client = FakeOpenAIClient()
        with mock.patch.object(
            Path, "write_bytes", side_effect=RuntimeError(private_detail)
        ):
            with self.assertRaises(collector.ArtifactCollectionError) as raised:
                self.collect()
        self.assertEqual(
            str(raised.exception),
            "CALL_ARTIFACT_ERROR stage=artifact_write",
        )
        self.assertFalse((self.output_root / CALL_SID).exists())

    def test_existing_artifacts_cannot_be_overwritten(self):
        target = self.output_root / CALL_SID
        target.mkdir(parents=True)
        recording = target / "recording.mp3"
        transcript = target / "transcript.md"
        recording.write_bytes(b"original recording")
        transcript.write_text("original transcript", encoding="utf-8")

        with self.assertRaises(collector.ArtifactCollectionError):
            self.collect()

        self.assertEqual(recording.read_bytes(), b"original recording")
        self.assertEqual(
            transcript.read_text(encoding="utf-8"), "original transcript"
        )
        self.assertEqual(self.settings_calls, 0)

    def test_publish_race_cannot_replace_existing_artifacts(self):
        target = self.output_root / CALL_SID

        def create_collision_and_fail(_target):
            target.mkdir()
            (target / "recording.mp3").write_bytes(b"racing recording")
            (target / "transcript.md").write_text(
                "racing transcript", encoding="utf-8"
            )
            raise FileExistsError

        with mock.patch.object(
            Path, "rename", side_effect=create_collision_and_fail
        ):
            with self.assertRaisesRegex(
                collector.ArtifactCollectionError,
                "CALL_ARTIFACT_ERROR stage=artifact_write",
            ):
                self.collect()

        self.assertEqual(
            (target / "recording.mp3").read_bytes(), b"racing recording"
        )
        self.assertEqual(
            (target / "transcript.md").read_text(encoding="utf-8"),
            "racing transcript",
        )

    def test_credentials_are_not_written_or_retained_in_module_globals(self):
        target = self.collect()
        artifact_text = "\n".join(
            path.read_bytes().decode("utf-8", errors="ignore")
            for path in target.iterdir()
        )
        module_values = tuple(vars(collector).values())
        for secret in self.settings.values():
            self.assertNotIn(secret, artifact_text)
            self.assertNotIn(secret, module_values)

    def test_default_downloader_requests_direct_mp3_with_basic_auth(self):
        captured = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return MP3_BYTES

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse()

        with mock.patch.object(collector, "urlopen", side_effect=fake_urlopen):
            result = collector._default_recording_downloader(
                RECORDING_SID,
                self.settings["TWILIO_ACCOUNT_SID"],
                self.settings["TWILIO_AUTH_TOKEN"],
            )

        self.assertEqual(result, MP3_BYTES)
        request, timeout = captured[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(
            request.full_url,
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self.settings['TWILIO_ACCOUNT_SID']}/Recordings/"
            f"{RECORDING_SID}.mp3?RequestedChannels=2",
        )
        self.assertEqual(request.get_header("Accept"), "audio/mpeg")
        self.assertTrue(request.get_header("Authorization").startswith("Basic "))

    def test_default_channel_downloader_requests_dual_channel_wav(self):
        captured = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return DUAL_WAV_BYTES

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse()

        with mock.patch.object(collector, "urlopen", side_effect=fake_urlopen):
            result = collector._default_channel_recording_downloader(
                RECORDING_SID,
                self.settings["TWILIO_ACCOUNT_SID"],
                self.settings["TWILIO_AUTH_TOKEN"],
            )

        self.assertEqual(result, DUAL_WAV_BYTES)
        request, timeout = captured[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(
            request.full_url,
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self.settings['TWILIO_ACCOUNT_SID']}/Recordings/"
            f"{RECORDING_SID}.wav?RequestedChannels=2",
        )
        self.assertEqual(request.get_header("Accept"), "audio/wav")

    def test_import_has_no_configuration_or_network_side_effects(self):
        with mock.patch("config.load_settings") as settings_loader, mock.patch(
            "outbound_call.build_twilio_client"
        ) as twilio_builder, mock.patch("urllib.request.urlopen") as network:
            sys.modules.pop("collect_call_artifacts", None)
            imported = importlib.import_module("collect_call_artifacts")

        self.assertEqual(imported.DEFAULT_OUTPUT_ROOT, Path("artifacts/calls"))
        settings_loader.assert_not_called()
        twilio_builder.assert_not_called()
        network.assert_not_called()

    def test_cli_has_no_overwrite_option(self):
        with mock.patch.object(
            collector,
            "collect_call_artifacts",
            return_value=Path("artifacts/calls") / CALL_SID,
        ) as collect, contextlib.redirect_stdout(io.StringIO()):
            result = collector.main(
                [
                    "--call-sid",
                    CALL_SID,
                    "--scenario-id",
                    SCENARIO_ID,
                ]
            )

        self.assertEqual(result, 0)
        collect.assert_called_once_with(
            call_sid=CALL_SID,
            scenario_id=SCENARIO_ID,
            output_root=str(collector.DEFAULT_OUTPUT_ROOT),
        )

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                collector._build_parser().parse_args(
                    [
                        "--call-sid",
                        CALL_SID,
                        "--scenario-id",
                        SCENARIO_ID,
                        "--overwrite",
                    ]
                )

    def test_cli_reports_only_sanitized_collection_error(self):
        stages = (
            "settings_load",
            "twilio_call_fetch",
            "twilio_recording_lookup",
            "recording_download",
            "channel_audio_validation",
            "openai_transcription",
            "transcription_parse",
            "artifact_validation",
            "artifact_write",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                stderr = io.StringIO()
                with mock.patch.object(
                    collector,
                    "collect_call_artifacts",
                    side_effect=collector.ArtifactCollectionError(stage),
                ) as collect, contextlib.redirect_stderr(stderr):
                    result = collector.main(
                        [
                            "--call-sid",
                            CALL_SID,
                            "--scenario-id",
                            SCENARIO_ID,
                        ]
                    )

                self.assertEqual(result, 1)
                collect.assert_called_once()
                expected = f"CALL_ARTIFACT_ERROR stage={stage}"
                if stage == "transcription_parse":
                    expected += " reason=unexpected_response"
                self.assertEqual(stderr.getvalue(), expected + "\n")
                for private_value in (
                    "Bearer private-api-key",
                    "Basic private-token",
                    "private-provider-response",
                    "https://signed.example.invalid",
                    *self.settings.values(),
                ):
                    self.assertNotIn(private_value, stderr.getvalue())

        stderr = io.StringIO()
        with mock.patch.object(
            collector,
            "collect_call_artifacts",
            side_effect=collector.ArtifactCollectionError(
                "transcription_parse", "timestamps_missing"
            ),
        ), contextlib.redirect_stderr(stderr):
            result = collector.main(
                ["--call-sid", CALL_SID, "--scenario-id", SCENARIO_ID]
            )
        self.assertEqual(result, 1)
        self.assertEqual(
            stderr.getvalue(),
            "CALL_ARTIFACT_ERROR stage=transcription_parse "
            "reason=timestamps_missing\n",
        )


if __name__ == "__main__":
    unittest.main()
