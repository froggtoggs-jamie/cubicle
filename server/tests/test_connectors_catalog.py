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


# `connectors.httpx` is the httpx module itself, so patching AsyncClient on it
# replaces the class globally for the duration of the test. Keep the real one.
RealAsyncClient = httpx.AsyncClient


def _client_factory(handler):
    def factory(*args, **kwargs):
        return RealAsyncClient(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    return factory


class StubComposio:
    def __init__(self, slugs):
        self.slugs = slugs

    async def list_auth_config_slugs(self, api_key=None):
        return set(self.slugs)


class ConnectorCatalogTests(unittest.TestCase):
    def setUp(self):
        connectors._toolkit_cache = None
        connectors._toolkit_cache_at = 0
        self._composio = mock.patch.object(connectors, "composio_service", StubComposio({"klaviyo"}))
        self._composio.start()

    def tearDown(self):
        self._composio.stop()

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
                        {"slug": "github", "name": "GitHub", "composio_managed_auth_schemes": ["OAUTH2"], "auth_schemes": ["OAUTH2"], "meta": {"description": "Issues and code", "logo": "https://logo/github.png"}},
                        {"slug": "slack", "name": "Slack", "composio_managed_auth_schemes": ["OAUTH2"], "auth_schemes": ["OAUTH2"], "meta": {}},
                        {"slug": "klaviyo", "name": "Klaviyo", "composio_managed_auth_schemes": [], "auth_schemes": ["OAUTH2", "API_KEY"]},
                        {"slug": "weather", "name": "Weather", "composio_managed_auth_schemes": [], "auth_schemes": ["NO_AUTH"]},
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
        self.assertEqual([c["slug"] for c in first["cards"]], ["github", "slack", "klaviyo", "weather"])
        self.assertEqual(first["cards"][0]["logo"], "https://logo/github.png")
        by_slug = {c["slug"]: c for c in first["cards"]}
        self.assertTrue(by_slug["github"]["managed_auth"])
        self.assertFalse(by_slug["github"]["needs_setup"])
        # Klaviyo has no managed credentials but the project has its own config.
        self.assertFalse(by_slug["klaviyo"]["managed_auth"])
        self.assertTrue(by_slug["klaviyo"]["has_auth_config"])
        self.assertFalse(by_slug["klaviyo"]["needs_setup"])
        self.assertTrue(by_slug["weather"]["needs_setup"])
        self.assertEqual(by_slug["weather"]["auth_schemes"], ["NO_AUTH"])
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
