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
