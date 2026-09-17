import re
from urllib.parse import urlencode, urlsplit

from twilio.rest import Client

import call_safety
from config import validate_settings
from patient_scenarios import get_scenario


_E164_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")
_FIXED_ASSESSMENT_DESTINATION = call_safety.ASSESSMENT_DESTINATION

# Twilio's dual-channel Calls recording layout is ordered by track: channel 1
# is inbound audio received by Twilio and channel 2 is outbound audio generated
# by Twilio. Because this project originates the call and generates the patient
# audio, those tracks are the remote side and patient bot, respectively.
RECORDING_CHANNELS = "dual"
RECORDING_TRACK = "both"
RECORDING_CHANNEL_ROLES = ("Remote side", "Patient bot")


def build_twilio_client(settings, client_factory=Client):
    """Build a Twilio client locally from validated credentials."""
    validate_settings(settings)
    return client_factory(
        settings["TWILIO_ACCOUNT_SID"],
        settings["TWILIO_AUTH_TOKEN"],
    )


def _validate_caller_id(number):
    if not _E164_PATTERN.fullmatch(number):
        raise ValueError("TWILIO_FROM_NUMBER must use strict E.164 format.")


def _validate_public_base_url(base_url):
    try:
        parsed = urlsplit(base_url)
        parsed.port
    except ValueError:
        raise ValueError("PUBLIC_BASE_URL must be a valid public HTTPS URL.") from None

    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in base_url)
    ):
        raise ValueError("PUBLIC_BASE_URL must be a valid public HTTPS URL.")


def create_assessment_call(client, settings, scenario_id):
    """Create one protected assessment call after local boundary validation."""
    validate_settings(settings)
    try:
        scenario = get_scenario(scenario_id)
    except ValueError:
        raise ValueError("Unknown scenario ID.") from None
    _validate_caller_id(settings["TWILIO_FROM_NUMBER"])
    _validate_public_base_url(settings["PUBLIC_BASE_URL"])

    base_url = settings["PUBLIC_BASE_URL"].rstrip("/")
    voice_url = f"{base_url}/voice?{urlencode({'scenario_id': scenario.scenario_id})}"
    call_status_url = f"{base_url}/calls/status"
    recording_status_url = f"{base_url}/recordings/status"

    destination = call_safety.ASSESSMENT_DESTINATION
    if destination != _FIXED_ASSESSMENT_DESTINATION:
        raise ValueError("Assessment destination safety check failed.")
    destination = call_safety.validate_destination(destination)
    created_call = client.calls.create(
        to=destination,
        from_=settings["TWILIO_FROM_NUMBER"],
        url=voice_url,
        method="POST",
        status_callback=call_status_url,
        status_callback_method="POST",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        record=True,
        recording_channels=RECORDING_CHANNELS,
        recording_track=RECORDING_TRACK,
        recording_status_callback=recording_status_url,
        recording_status_callback_method="POST",
        recording_status_callback_event=["completed"],
    )
    return created_call.sid
