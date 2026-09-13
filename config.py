import os
from pathlib import Path

from dotenv import dotenv_values


REQUIRED_SETTINGS = (
    "OPENAI_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "PUBLIC_BASE_URL",
)


def validate_settings(settings):
    """Check required dictionary values locally, without authenticating them."""
    invalid_names = []
    for name in REQUIRED_SETTINGS:
        value = settings.get(name)
        if not isinstance(value, str) or not value.strip():
            invalid_names.append(name)
    if invalid_names:
        raise ValueError(", ".join(invalid_names))
    return None


def load_settings(env_path=None):
    """Read one settings file, apply environment overrides, and validate locally."""
    path = Path(__file__).resolve().with_name(".env") if env_path is None else Path(env_path)
    file_settings = dotenv_values(dotenv_path=path, interpolate=False)
    settings = {
        name: os.environ[name] if name in os.environ else file_settings.get(name)
        for name in REQUIRED_SETTINGS
    }
    validate_settings(settings)
    return settings
