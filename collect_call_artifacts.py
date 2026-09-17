import argparse
import base64
import io
import json
import math
import re
import shutil
import struct
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.request import Request, urlopen

import call_safety
from config import load_settings
from outbound_call import RECORDING_CHANNEL_ROLES, build_twilio_client
from patient_scenarios import get_scenario


DEFAULT_OUTPUT_ROOT = Path("artifacts/calls")
TRANSCRIPTION_MODEL = "gpt-4o-transcribe-diarize"
TRANSCRIPTION_RESPONSE_FORMAT = "diarized_json"
TRANSCRIPTION_CHUNKING_STRATEGY = "auto"
TRANSCRIPTION_LANGUAGE = "en"
RECORDING_CHANNEL_COUNT = len(RECORDING_CHANNEL_ROLES)
REMOTE_SIDE_LABEL, PATIENT_BOT_LABEL = RECORDING_CHANNEL_ROLES

_CALL_SID_PATTERN = re.compile(r"^CA[0-9a-fA-F]{32}$")
_RECORDING_SID_PATTERN = re.compile(r"^RE[0-9a-fA-F]{32}$")
_MISSING = object()
_ERROR_PREFIX = "CALL_ARTIFACT_ERROR stage="
_ERROR_STAGES = frozenset(
    (
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
)
_TRANSCRIPTION_PARSE_REASONS = frozenset(
    (
        "response_missing",
        "response_shape",
        "segments_missing",
        "segments_invalid",
        "timestamps_missing",
        "no_segments",
        "timestamp_invalid",
        "segment_bounds_invalid",
        "segment_order_invalid",
        "segment_text_invalid",
        "speaker_invalid",
        "unexpected_response",
    )
)


class ArtifactCollectionError(RuntimeError):
    """Report a private artifact-collection failure."""

    def __init__(self, stage, reason=None):
        if stage not in _ERROR_STAGES:
            stage = "artifact_validation"
        self.stage = stage
        if stage == "transcription_parse":
            if reason not in _TRANSCRIPTION_PARSE_REASONS:
                reason = "unexpected_response"
        else:
            reason = None
        self.reason = reason
        message = f"{_ERROR_PREFIX}{stage}"
        if reason is not None:
            message += f" reason={reason}"
        super().__init__(message)


def _field(value, name):
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _validate_identifier(value, pattern, stage="artifact_validation"):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ArtifactCollectionError(stage)
    return value


def _validate_duration(value):
    if isinstance(value, bool):
        raise ArtifactCollectionError("artifact_validation")
    try:
        duration = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ArtifactCollectionError("artifact_validation") from None
    if duration < 0 or str(duration) != str(value).strip():
        raise ArtifactCollectionError("artifact_validation")
    return duration


def _validate_seconds(value):
    if isinstance(value, bool):
        raise ArtifactCollectionError(
            "transcription_parse", "timestamp_invalid"
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ArtifactCollectionError(
            "transcription_parse", "timestamp_invalid"
        ) from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ArtifactCollectionError(
            "transcription_parse", "timestamp_invalid"
        )
    return seconds


def _format_timestamp(seconds):
    milliseconds = round(_validate_seconds(seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _normalized_speaker_label(index):
    """Return spreadsheet-style labels: A through Z, then AA, AB, and so on."""
    label = ""
    while True:
        index, remainder = divmod(index, 26)
        label = chr(ord("A") + remainder) + label
        if index == 0:
            return label
        index -= 1


def _response_field(value, name):
    """Return whether a response field exists and its possibly-None value."""
    if isinstance(value, Mapping):
        return name in value, value.get(name)
    if value is None:
        return False, None
    try:
        result = getattr(value, name, _MISSING)
    except Exception:
        raise ArtifactCollectionError(
            "transcription_parse", "response_shape"
        ) from None
    if result is _MISSING:
        return False, None
    return True, result


def _response_segments(transcription, *, allow_empty):
    """Classify timestamped segments without inventing missing timing data."""
    if transcription is None:
        raise ArtifactCollectionError(
            "transcription_parse", "response_missing"
        )
    segments_present, segments = _response_field(transcription, "segments")
    text_present, response_text = _response_field(transcription, "text")
    if text_present and response_text is not None and not isinstance(
        response_text, str
    ):
        raise ArtifactCollectionError(
            "transcription_parse", "response_shape"
        )
    has_response_text = isinstance(response_text, str) and bool(
        response_text.strip()
    )
    if not segments_present or segments is None:
        if has_response_text:
            raise ArtifactCollectionError(
                "transcription_parse", "timestamps_missing"
            )
        if text_present and allow_empty:
            return ()
        raise ArtifactCollectionError(
            "transcription_parse", "segments_missing"
        )
    if not isinstance(segments, Sequence) or isinstance(
        segments, (str, bytes, bytearray)
    ):
        raise ArtifactCollectionError(
            "transcription_parse", "segments_invalid"
        )
    try:
        segments = tuple(segments)
    except Exception:
        raise ArtifactCollectionError(
            "transcription_parse", "segments_invalid"
        ) from None
    if not segments:
        if has_response_text:
            raise ArtifactCollectionError(
                "transcription_parse", "timestamps_missing"
            )
        if allow_empty:
            return ()
        raise ArtifactCollectionError(
            "transcription_parse", "no_segments"
        )
    return segments


def _stable_chronological_segments(segments, key):
    """Return a deterministic timeline without changing segment contents."""
    try:
        return sorted(segments, key=key)
    except Exception:
        raise ArtifactCollectionError(
            "transcription_parse", "segment_order_invalid"
        ) from None


def format_diarized_transcript(transcription, scenario_id, call_sid):
    """Build Markdown with provider speaker IDs normalized by first appearance."""
    segments = _response_segments(transcription, allow_empty=False)

    lines = [
        "# Call Transcript",
        "",
        f"- Scenario ID: `{scenario_id}`",
        f"- Call SID: `{call_sid}`",
        "- Speaker labels are diarization labels only and require manual review.",
        "",
    ]
    validated = []
    for sequence, segment in enumerate(segments):
        start = _validate_seconds(_field(segment, "start"))
        end = _validate_seconds(_field(segment, "end"))
        speaker = _field(segment, "speaker")
        text = _field(segment, "text")
        if end < start:
            raise ArtifactCollectionError(
                "transcription_parse", "segment_bounds_invalid"
            )
        if not isinstance(speaker, str) or not speaker.strip():
            raise ArtifactCollectionError(
                "transcription_parse", "speaker_invalid"
            )
        if not isinstance(text, str) or not text.strip():
            raise ArtifactCollectionError(
                "transcription_parse", "segment_text_invalid"
            )
        clean_text = " ".join(text.split())
        validated.append((start, end, speaker, clean_text, sequence))

    speaker_labels = {}
    for start, end, speaker, clean_text, _sequence in (
        _stable_chronological_segments(validated, key=lambda item: item[0])
    ):
        if speaker not in speaker_labels:
            speaker_labels[speaker] = _normalized_speaker_label(
                len(speaker_labels)
            )
        lines.append(
            f"**[{_format_timestamp(start)} - {_format_timestamp(end)}] "
            f"Speaker {speaker_labels[speaker]}:** {clean_text}"
        )
        lines.append("")
    return "\n".join(lines)


def _validated_channel_segments(transcription, role):
    segments = _response_segments(transcription, allow_empty=True)

    validated = []
    for sequence, segment in enumerate(segments):
        start = _validate_seconds(_field(segment, "start"))
        end = _validate_seconds(_field(segment, "end"))
        text = _field(segment, "text")
        if end < start:
            raise ArtifactCollectionError(
                "transcription_parse", "segment_bounds_invalid"
            )
        if not isinstance(text, str) or not text.strip():
            raise ArtifactCollectionError(
                "transcription_parse", "segment_text_invalid"
            )
        validated.append(
            (start, end, role, " ".join(text.split()), sequence)
        )
    return _stable_chronological_segments(
        validated, key=lambda item: item[0]
    )


def format_channel_transcript(
    remote_transcription, patient_transcription, scenario_id, call_sid
):
    """Build Markdown whose roles come from the dual recording channels."""
    segments = _validated_channel_segments(
        remote_transcription, REMOTE_SIDE_LABEL
    )
    segments.extend(
        _validated_channel_segments(patient_transcription, PATIENT_BOT_LABEL)
    )
    if not segments:
        raise ArtifactCollectionError("transcription_parse", "no_segments")
    role_order = {REMOTE_SIDE_LABEL: 0, PATIENT_BOT_LABEL: 1}
    segments = _stable_chronological_segments(
        segments,
        key=lambda item: (item[0], role_order[item[2]], item[4]),
    )

    lines = [
        "# Call Transcript",
        "",
        f"- Scenario ID: `{scenario_id}`",
        f"- Call SID: `{call_sid}`",
        (
            "- Roles come from Twilio's dual recording channels: Remote side "
            "is inbound audio and Patient bot is outbound audio."
        ),
        (
            "- Separate voices sharing the Remote side channel are not "
            "identified individually."
        ),
        "",
    ]
    for start, end, role, text, _sequence in segments:
        lines.append(
            f"**[{_format_timestamp(start)} - {_format_timestamp(end)}] "
            f"{role}:** {text}"
        )
        lines.append("")
    return "\n".join(lines)


def _split_dual_channel_wav(audio):
    """Return inbound (channel 1) and outbound (channel 2) mono WAVs."""
    if not isinstance(audio, bytes) or not audio:
        raise ValueError
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ValueError
    if struct.unpack_from("<I", audio, 4)[0] + 8 != len(audio):
        raise ValueError

    chunks = []
    offset = 12
    while offset < len(audio):
        if offset + 8 > len(audio):
            raise ValueError
        chunk_id = audio[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", audio, offset + 4)[0]
        data_start = offset + 8
        data_end = data_start + chunk_size
        padded_end = data_end + (chunk_size % 2)
        if padded_end > len(audio):
            raise ValueError
        chunks.append((chunk_id, audio[data_start:data_end]))
        offset = padded_end
    if offset != len(audio):
        raise ValueError

    format_chunks = [payload for name, payload in chunks if name == b"fmt "]
    data_chunks = [payload for name, payload in chunks if name == b"data"]
    if len(format_chunks) != 1 or len(data_chunks) != 1:
        raise ValueError
    format_payload = format_chunks[0]
    if len(format_payload) < 16:
        raise ValueError
    (
        audio_format,
        channel_count,
        sample_rate,
        _byte_rate,
        block_alignment,
        bits_per_sample,
    ) = struct.unpack_from("<HHIIHH", format_payload)
    if audio_format not in (1, 6, 7):
        raise ValueError
    if sample_rate <= 0 or _byte_rate != sample_rate * block_alignment:
        raise ValueError
    if audio_format == 1 and bits_per_sample not in (8, 16, 24, 32):
        raise ValueError
    if audio_format in (6, 7) and bits_per_sample != 8:
        raise ValueError
    if channel_count != RECORDING_CHANNEL_COUNT or block_alignment % 2:
        raise ValueError
    mono_block_alignment = block_alignment // RECORDING_CHANNEL_COUNT
    expected_alignment = (bits_per_sample + 7) // 8
    if mono_block_alignment <= 0 or mono_block_alignment != expected_alignment:
        raise ValueError

    frames = data_chunks[0]
    if not frames or len(frames) % block_alignment:
        raise ValueError
    channel_frames = (bytearray(), bytearray())
    for frame_offset in range(0, len(frames), block_alignment):
        second_channel = frame_offset + mono_block_alignment
        channel_frames[0].extend(frames[frame_offset:second_channel])
        channel_frames[1].extend(
            frames[second_channel : frame_offset + block_alignment]
        )

    silence_values = {
        1: ({128} if bits_per_sample == 8 else {0}),
        6: {0x55, 0xD5},
        7: {0x7F, 0xFF},
    }[audio_format]
    if any(set(channel).issubset(silence_values) for channel in channel_frames):
        raise ValueError

    mono_wavs = []
    for mono_frames in channel_frames:
        mono_format = bytearray(format_payload)
        struct.pack_into("<H", mono_format, 2, 1)
        struct.pack_into(
            "<I", mono_format, 8, sample_rate * mono_block_alignment
        )
        struct.pack_into("<H", mono_format, 12, mono_block_alignment)

        body = bytearray(b"WAVE")
        for chunk_id, payload in chunks:
            if chunk_id == b"fmt ":
                payload = bytes(mono_format)
            elif chunk_id == b"data":
                payload = bytes(mono_frames)
            body.extend(chunk_id)
            body.extend(struct.pack("<I", len(payload)))
            body.extend(payload)
            if len(payload) % 2:
                body.append(0)
        mono_wavs.append(
            b"RIFF" + struct.pack("<I", len(body)) + bytes(body)
        )
    return tuple(mono_wavs)


def _default_openai_client_builder(api_key):
    from openai import OpenAI

    return OpenAI(api_key=api_key)


def _download_recording_media(
    recording_sid, account_sid, auth_token, extension, accept, query=""
):
    media_url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{account_sid}/Recordings/{recording_sid}.{extension}{query}"
    )
    authorization = base64.b64encode(
        f"{account_sid}:{auth_token}".encode("utf-8")
    ).decode("ascii")
    request = Request(
        media_url,
        headers={
            "Authorization": f"Basic {authorization}",
            "Accept": accept,
        },
    )
    with urlopen(request, timeout=30) as response:
        if response.getcode() != 200:
            raise RuntimeError
        content = response.read()
    if not isinstance(content, bytes) or not content:
        raise RuntimeError
    return content


def _default_recording_downloader(recording_sid, account_sid, auth_token):
    return _download_recording_media(
        recording_sid,
        account_sid,
        auth_token,
        "mp3",
        "audio/mpeg",
        "?RequestedChannels=2",
    )


def _default_channel_recording_downloader(
    recording_sid, account_sid, auth_token
):
    return _download_recording_media(
        recording_sid,
        account_sid,
        auth_token,
        "wav",
        "audio/wav",
        "?RequestedChannels=2",
    )


def _find_completed_recording(twilio_client, call_sid):
    try:
        recordings = twilio_client.recordings.list(
            call_sid=call_sid, limit=50
        )
    except Exception:
        raise ArtifactCollectionError("twilio_recording_lookup") from None
    for recording in recordings:
        if (
            _field(recording, "call_sid") == call_sid
            and _field(recording, "status") == "completed"
        ):
            _validate_identifier(
                _field(recording, "sid"),
                _RECORDING_SID_PATTERN,
                "twilio_recording_lookup",
            )
            return recording
    raise ArtifactCollectionError("twilio_recording_lookup")


def collect_call_artifacts(
    call_sid,
    scenario_id,
    output_root=DEFAULT_OUTPUT_ROOT,
    *,
    settings_loader=load_settings,
    twilio_client_builder=build_twilio_client,
    recording_downloader=None,
    channel_recording_downloader=None,
    openai_client_builder=None,
):
    """Collect one call's recording, channel-role transcript, and metadata."""
    _validate_identifier(call_sid, _CALL_SID_PATTERN)
    if not isinstance(scenario_id, str):
        raise ArtifactCollectionError("artifact_validation")
    try:
        scenario = get_scenario(scenario_id)
    except ValueError:
        raise ArtifactCollectionError("artifact_validation") from None

    try:
        root = Path(output_root)
        target = root / call_sid
        if target.exists():
            raise ArtifactCollectionError("artifact_validation")
        if root.exists() and not root.is_dir():
            raise ArtifactCollectionError("artifact_validation")
    except ArtifactCollectionError:
        raise
    except Exception:
        raise ArtifactCollectionError("artifact_validation") from None
    try:
        root.mkdir(parents=True, exist_ok=True)
        temp_path = Path(tempfile.mkdtemp(prefix=".collect-", dir=root))
    except Exception:
        raise ArtifactCollectionError("artifact_write") from None

    published = False
    try:
        try:
            settings = settings_loader()
            api_key = settings["OPENAI_API_KEY"]
            account_sid = settings["TWILIO_ACCOUNT_SID"]
            auth_token = settings["TWILIO_AUTH_TOKEN"]
        except Exception:
            raise ArtifactCollectionError("settings_load") from None

        try:
            twilio_client = twilio_client_builder(settings)
            del settings
            call = twilio_client.calls(call_sid).fetch()
        except Exception:
            raise ArtifactCollectionError("twilio_call_fetch") from None

        try:
            if _field(call, "sid") != call_sid:
                raise ArtifactCollectionError("artifact_validation")
            call_safety.validate_destination(_field(call, "to"))
            call_status = _field(call, "status")
            if not isinstance(call_status, str) or not call_status.strip():
                raise ArtifactCollectionError("artifact_validation")
            call_duration = _validate_duration(_field(call, "duration"))
        except ArtifactCollectionError:
            raise
        except Exception:
            raise ArtifactCollectionError("artifact_validation") from None

        try:
            recording = _find_completed_recording(twilio_client, call_sid)
        except ArtifactCollectionError:
            raise
        except Exception:
            raise ArtifactCollectionError(
                "twilio_recording_lookup"
            ) from None
        try:
            recording_sid = _field(recording, "sid")
            recording_duration = _validate_duration(
                _field(recording, "duration")
            )
            recording_channels = _field(recording, "channels")
            if (
                type(recording_channels) is not int
                or recording_channels != RECORDING_CHANNEL_COUNT
            ):
                raise ArtifactCollectionError("channel_audio_validation")
        except ArtifactCollectionError:
            raise
        except Exception:
            raise ArtifactCollectionError("artifact_validation") from None

        try:
            downloader = recording_downloader or _default_recording_downloader
            audio = downloader(
                recording_sid,
                account_sid,
                auth_token,
            )
            if not isinstance(audio, bytes) or not audio:
                raise TypeError
            channel_downloader = (
                channel_recording_downloader
                or _default_channel_recording_downloader
            )
            channel_audio = channel_downloader(
                recording_sid,
                account_sid,
                auth_token,
            )
        except Exception:
            raise ArtifactCollectionError("recording_download") from None
        finally:
            del account_sid
            del auth_token

        try:
            remote_audio, patient_audio = _split_dual_channel_wav(
                channel_audio
            )
        except Exception:
            raise ArtifactCollectionError(
                "channel_audio_validation"
            ) from None

        try:
            recording_path = temp_path / "recording.mp3"
            recording_path.write_bytes(audio)
        except Exception:
            raise ArtifactCollectionError("artifact_write") from None
        finally:
            del audio
            del channel_audio

        try:
            client_builder = (
                openai_client_builder or _default_openai_client_builder
            )
            openai_client = client_builder(api_key)
        except Exception:
            raise ArtifactCollectionError("openai_transcription") from None
        finally:
            del api_key

        try:
            transcriptions = []
            for file_name, channel_bytes in (
                ("remote-side.wav", remote_audio),
                ("patient-bot.wav", patient_audio),
            ):
                audio_file = io.BytesIO(channel_bytes)
                audio_file.name = file_name
                transcriptions.append(
                    openai_client.audio.transcriptions.create(
                        file=audio_file,
                        model=TRANSCRIPTION_MODEL,
                        response_format=TRANSCRIPTION_RESPONSE_FORMAT,
                        chunking_strategy=TRANSCRIPTION_CHUNKING_STRATEGY,
                        language=TRANSCRIPTION_LANGUAGE,
                    )
                )
        except Exception:
            raise ArtifactCollectionError("openai_transcription") from None
        finally:
            del remote_audio
            del patient_audio

        try:
            transcript = format_channel_transcript(
                transcriptions[0],
                transcriptions[1],
                scenario.scenario_id,
                call_sid,
            )
        except ArtifactCollectionError:
            raise
        except Exception:
            raise ArtifactCollectionError(
                "transcription_parse", "unexpected_response"
            ) from None

        try:
            (temp_path / "transcript.md").write_text(
                transcript, encoding="utf-8", newline="\n"
            )
        except Exception:
            raise ArtifactCollectionError("artifact_write") from None
        finally:
            del transcript
            del transcriptions

        try:
            metadata = {
                "scenario_id": scenario.scenario_id,
                "call_sid": call_sid,
                "recording_sid": recording_sid,
                "call_duration_seconds": call_duration,
                "recording_duration_seconds": recording_duration,
                "status": call_status,
                "recording_status": "completed",
                "recording_channels": recording_channels,
                "artifact_files": [
                    "recording.mp3",
                    "transcript.md",
                    "metadata.json",
                ],
            }
            (temp_path / "metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            del metadata
        except Exception:
            raise ArtifactCollectionError("artifact_write") from None

        try:
            if target.exists():
                raise ArtifactCollectionError("artifact_validation")
            temp_path.rename(target)
        except ArtifactCollectionError:
            raise
        except Exception:
            raise ArtifactCollectionError("artifact_write") from None
        published = True
        return target
    finally:
        if not published:
            try:
                if temp_path.exists():
                    shutil.rmtree(temp_path, ignore_errors=True)
            except Exception:
                pass


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Collect recording and transcription artifacts for one call."
    )
    parser.add_argument("--call-sid", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser


def main(argv=None):
    arguments = _build_parser().parse_args(argv)
    try:
        artifact_folder = collect_call_artifacts(
            call_sid=arguments.call_sid,
            scenario_id=arguments.scenario_id,
            output_root=arguments.output_root,
        )
    except ArtifactCollectionError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Artifacts saved to {artifact_folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
