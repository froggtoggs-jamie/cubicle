import asyncio
import unittest
from unittest import mock

import httpx

from app.routers import connectors


class KeyedStorage:
    def get_settings(self):
        return {"composio_api_key": "ak_test"}


class EmptyStorage:
    def get_settings(self):
        return {}


def _client_factory(handler):
    def factory(*args, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    return factory


class ConnectorCatalogTests(unittest.TestCase):
    def setUp(self):
        connectors._toolkit_cache = None
        connectors._toolkit_cache_at = 0

    def tearDown(self):
        connectors._toolkit_cache = None
        connectors._toolkit_cache_at = 0

    def test_live_toolkits_become_cards_and_are_cached(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"slug": "github", "name": "GitHub", "meta": {"description": "Issues and code", "logo": "https://logo/github.png"}},
                        {"slug": "slack", "name": "Slack", "meta": {}},
                        {"name": "", "slug": ""},
                    ]
                },
            )

        with mock.patch.object(connectors, "storage_service", KeyedStorage()), mock.patch.object(
            connectors.httpx, "AsyncClient", _client_factory(handler)
        ):
            first = asyncio.run(connectors.catalog())
            second = asyncio.run(connectors.catalog())

        self.assertEqual(first["source"], "api")
        self.assertTrue(first["configured"])
        self.assertEqual([c["slug"] for c in first["cards"]], ["github", "slack"])
        self.assertEqual(first["cards"][0]["logo"], "https://logo/github.png")
        self.assertEqual(calls[0].headers["x-api-key"], "ak_test")
        self.assertEqual(calls[0].url.params["limit"], "200")
        self.assertEqual(second["source"], "api")
        self.assertEqual(len(calls), 1)

    def test_failed_listing_falls_back_to_curated_with_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "nope"}})

        with mock.patch.object(connectors, "storage_service", KeyedStorage()), mock.patch.object(
            connectors.httpx, "AsyncClient", _client_factory(handler)
        ):
            result = asyncio.run(connectors.catalog())

        self.assertEqual(result["source"], "curated")
        self.assertTrue(result["configured"])
        self.assertIn("HTTP 401", result["error"])
        self.assertEqual(result["cards"], connectors.CURATED)

    def test_without_a_key_the_curated_list_is_served_without_calling_composio(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("Composio must not be called without a key")

        with mock.patch.object(connectors, "storage_service", EmptyStorage()), mock.patch.object(
            connectors.httpx, "AsyncClient", _client_factory(handler)
        ):
            result = asyncio.run(connectors.catalog())

        self.assertEqual(result["source"], "curated")
        self.assertFalse(result["configured"])
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
