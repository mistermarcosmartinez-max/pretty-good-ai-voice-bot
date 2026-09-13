from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator

from config import load_settings


_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_GENERIC_ERROR = "Request rejected."
_GENERIC_WEBSOCKET_ERROR = "WebSocket handshake rejected."
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class _FormParameters:
    """Minimal multi-value mapping supported by Twilio's RequestValidator."""

    def __init__(self, pairs):
        self._values = {}
        for name, value in pairs:
            self._values.setdefault(name, []).append(value)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def getall(self, name):
        return self._values[name]


@dataclass(frozen=True)
class ValidatedWebhook:
    settings: dict
    form_parameters: _FormParameters


def _public_url_parts(base_url):
    try:
        parsed = urlsplit(base_url)
        parsed.port
    except (TypeError, ValueError):
        raise ValueError from None

    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in base_url)
    ):
        raise ValueError
    return parsed


def _raw_request_path(request):
    raw_path = request.scope.get("raw_path")
    if raw_path is None:
        raw_path = request.scope["path"].encode("utf-8")
    return raw_path.decode("latin-1")


def _signature_url(base_url, request):
    path = _raw_request_path(request)
    query = request.scope.get("query_string", b"").decode("latin-1")
    url = f"{base_url.rstrip('/')}{path}"
    return f"{url}?{query}" if query else url


def _decode_raw_url_component(value, *, require_path=False):
    if not isinstance(value, bytes):
        raise ValueError
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError from None
    if require_path and (not decoded.startswith("/") or not decoded):
        raise ValueError
    if any(character.isspace() or ord(character) < 32 for character in decoded):
        raise ValueError
    if "#" in decoded or (require_path and "?" in decoded):
        raise ValueError
    for index, character in enumerate(decoded):
        if character == "%" and (
            index + 2 >= len(decoded)
            or decoded[index + 1] not in _HEX_DIGITS
            or decoded[index + 2] not in _HEX_DIGITS
        ):
            raise ValueError
    return decoded


def _websocket_signature_url(base_url, websocket):
    parsed = _public_url_parts(base_url)
    websocket_base = urlunsplit(parsed._replace(scheme="wss"))
    raw_path = _decode_raw_url_component(
        websocket.scope.get("raw_path"), require_path=True
    )
    raw_query = _decode_raw_url_component(
        websocket.scope.get("query_string", b"")
    )
    url = f"{websocket_base.rstrip('/')}{raw_path}"
    return f"{url}?{raw_query}" if raw_query else url


def build_media_url(base_url):
    """Derive the media WSS URL while retaining the configured base path."""
    parsed = _public_url_parts(base_url)
    websocket_base = urlunsplit(parsed._replace(scheme="wss"))
    return f"{websocket_base.rstrip('/')}/media"


def validate_twilio_websocket_handshake(websocket):
    """Validate a Twilio WebSocket handshake without changing its lifecycle."""
    try:
        settings = load_settings()
        signature = websocket.headers.get("x-twilio-signature")
        if not isinstance(signature, str) or not signature.strip():
            raise ValueError
        signature_url = _websocket_signature_url(
            settings["PUBLIC_BASE_URL"], websocket
        )
        valid = RequestValidator(settings["TWILIO_AUTH_TOKEN"]).validate(
            signature_url,
            {},
            signature,
        )
        if not valid:
            raise ValueError
        return {
            "OPENAI_API_KEY": settings["OPENAI_API_KEY"],
            "TWILIO_ACCOUNT_SID": settings["TWILIO_ACCOUNT_SID"],
        }
    except Exception:
        raise ValueError(_GENERIC_WEBSOCKET_ERROR) from None


async def validate_twilio_webhook(request):
    """Validate one form-encoded Twilio webhook without retaining its data."""
    content_type = request.headers.get("content-type", "").partition(";")[0]
    if content_type.strip().lower() != _FORM_CONTENT_TYPE:
        raise HTTPException(status_code=415, detail=_GENERIC_ERROR)

    try:
        settings = load_settings()
        _public_url_parts(settings["PUBLIC_BASE_URL"])
    except Exception:
        raise HTTPException(status_code=403, detail=_GENERIC_ERROR) from None

    signature = request.headers.get("x-twilio-signature")
    if not signature:
        raise HTTPException(status_code=403, detail=_GENERIC_ERROR)

    try:
        body = (await request.body()).decode("utf-8")
        form_parameters = _FormParameters(
            parse_qsl(body, keep_blank_values=True, strict_parsing=False)
        )
        valid = RequestValidator(settings["TWILIO_AUTH_TOKEN"]).validate(
            _signature_url(settings["PUBLIC_BASE_URL"], request),
            form_parameters,
            signature,
        )
    except Exception:
        raise HTTPException(status_code=403, detail=_GENERIC_ERROR) from None

    if not valid:
        raise HTTPException(status_code=403, detail=_GENERIC_ERROR)
    return ValidatedWebhook(settings=settings, form_parameters=form_parameters)
