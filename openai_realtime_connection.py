OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
)


async def create_openai_realtime_connection(api_key, connector=None):
    """Open and return an authenticated OpenAI Realtime WebSocket connection."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Invalid OpenAI API key.")

    setup_failed = False
    if connector is None:
        try:
            from websockets.asyncio.client import connect as selected_connector
        except Exception:
            setup_failed = True
    else:
        selected_connector = connector

    if not setup_failed:
        try:
            connection = await selected_connector(
                OPENAI_REALTIME_URL,
                additional_headers={"Authorization": f"Bearer {api_key}"},
            )
        except Exception:
            setup_failed = True

    if setup_failed:
        raise ConnectionError("Unable to establish Realtime connection.") from None

    return connection
