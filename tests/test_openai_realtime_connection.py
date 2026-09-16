import asyncio
import builtins
import importlib
import sys
import types
import unittest
from unittest.mock import patch

import config
import openai_realtime_connection
from openai_realtime_connection import create_openai_realtime_connection


class OpenAIRealtimeConnectionTests(unittest.TestCase):
    api_key = "fictional-openai-key-sentinel"
    expected_url = "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_connector_receives_exact_url_and_authorization_header(self):
        calls = []
        fake_connection = object()

        async def fake_connector(url, **kwargs):
            calls.append((url, kwargs))
            return fake_connection

        result = self.run_async(
            create_openai_realtime_connection(self.api_key, fake_connector)
        )

        self.assertIs(result, fake_connection)
        self.assertEqual(
            calls,
            [
                (
                    self.expected_url,
                    {
                        "additional_headers": {
                            "Authorization": f"Bearer {self.api_key}"
                        }
                    },
                )
            ],
        )

    def test_invocation_lazily_imports_current_asyncio_connector(self):
        calls = []
        fake_connection = object()

        async def fake_connect(url, **kwargs):
            calls.append((url, kwargs))
            return fake_connection

        fake_websockets = types.ModuleType("websockets")
        fake_asyncio = types.ModuleType("websockets.asyncio")
        fake_client = types.ModuleType("websockets.asyncio.client")
        fake_client.connect = fake_connect

        with patch.dict(
            sys.modules,
            {
                "websockets": fake_websockets,
                "websockets.asyncio": fake_asyncio,
                "websockets.asyncio.client": fake_client,
            },
        ):
            result = self.run_async(
                create_openai_realtime_connection(self.api_key)
            )

        self.assertIs(result, fake_connection)
        self.assertEqual(
            calls,
            [
                (
                    self.expected_url,
                    {
                        "additional_headers": {
                            "Authorization": f"Bearer {self.api_key}"
                        }
                    },
                )
            ],
        )

    def test_invalid_keys_fail_before_connector_is_called(self):
        invalid_keys = (None, "", " ", "\t\r\n", 123, b"fictional-key", [])

        for api_key in invalid_keys:
            with self.subTest(api_key=api_key):
                calls = []

                async def fake_connector(*args, **kwargs):
                    calls.append((args, kwargs))
                    return object()

                with self.assertRaisesRegex(ValueError, "^Invalid OpenAI API key\\.$"):
                    self.run_async(
                        create_openai_realtime_connection(api_key, fake_connector)
                    )
                self.assertEqual(calls, [])

    def test_connector_failure_uses_generic_private_error(self):
        provider_detail = f"provider rejected {self.api_key} at {self.expected_url}"

        async def failing_connector(*args, **kwargs):
            raise RuntimeError(provider_detail)

        with self.assertRaises(ConnectionError) as raised:
            self.run_async(
                create_openai_realtime_connection(self.api_key, failing_connector)
            )

        self.assertEqual(
            str(raised.exception), "Unable to establish Realtime connection."
        )
        self.assertNotIn(self.api_key, str(raised.exception))
        self.assertNotIn(self.expected_url, str(raised.exception))
        self.assertNotIn(provider_detail, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_call_retains_no_secret_or_connection_in_module_globals(self):
        fake_connection = object()

        async def fake_connector(*args, **kwargs):
            return fake_connection

        result = self.run_async(
            create_openai_realtime_connection(self.api_key, fake_connector)
        )
        self.assertIs(result, fake_connection)

        module_values = tuple(vars(openai_realtime_connection).values())
        self.assertFalse(any(value is fake_connection for value in module_values))
        self.assertFalse(any(value is fake_connector for value in module_values))
        self.assertFalse(any(value == self.api_key for value in module_values))
        self.assertFalse(
            any(
                isinstance(value, dict)
                and value.get("Authorization") == f"Bearer {self.api_key}"
                for value in module_values
            )
        )
        self.assertEqual(create_openai_realtime_connection.__dict__, {})

    def test_import_makes_no_connection_and_loads_no_configuration(self):
        websocket_imports = []
        real_import = builtins.__import__

        def tracking_import(name, *args, **kwargs):
            if name.startswith("websockets"):
                websocket_imports.append(name)
            return real_import(name, *args, **kwargs)

        with patch.object(
            config,
            "load_settings",
            side_effect=AssertionError("configuration must not load on import"),
        ), patch("builtins.__import__", side_effect=tracking_import):
            imported = importlib.reload(openai_realtime_connection)

        self.assertIs(imported, openai_realtime_connection)
        self.assertEqual(websocket_imports, [])


if __name__ == "__main__":
    unittest.main()
