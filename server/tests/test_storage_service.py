import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.services.storage_service import StorageService
from app.services.database import SCHEMA_MIGRATIONS
from app.schemas.contracts import AppSettingsSchema
from app.routers import settings as settings_router


class StorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = StorageService(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_and_encrypted_settings_survive_reopen(self):
        self.service.save_bots([{"id": "bot-test", "name": "Persistent bot"}])
        self.service.add_message(
            {
                "id": "message-test",
                "thread_id": "thread-test",
                "sender": "user",
                "text": "remember this",
            }
        )
        self.service.add_approval(
            {"request_id": "request-test", "thread_id": "thread-test", "status": "pending"}
        )
        self.service.add_audit_event({"event": "test.completed", "request_id": "request-test"})
        self.service.save_settings({"muapi_api_key": "super-secret", "theme": "light"})

        self.service.save_settings({"muapi_api_key": "", "theme": "dark"})
        reopened = StorageService(self.root)

        self.assertEqual(reopened.get_bots(), [{"id": "bot-test", "name": "Persistent bot"}])
        self.assertEqual(reopened.get_messages("thread-test")[0]["text"], "remember this")
        self.assertEqual(reopened.get_approvals("thread-test")[0]["request_id"], "request-test")
        self.assertEqual(reopened.get_audit_events(10)[-1]["event"], "test.completed")
        self.assertEqual(reopened.get_settings()["muapi_api_key"], "super-secret")
        self.assertEqual(reopened.get_settings()["theme"], "dark")

        public = reopened.get_public_settings()
        self.assertEqual(public["muapi_api_key"], "")
        self.assertTrue(public["muapi_api_key_configured"])
        self.assertNotIn(b"super-secret", self._database_bytes())

        with sqlite3.connect(reopened.db_path) as connection:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(version, 2)
        with sqlite3.connect(reopened.db_path) as connection:
            owner_id = connection.execute(
                "SELECT owner_id FROM bots WHERE id = ?", ("bot-test",)
            ).fetchone()[0]
        self.assertEqual(owner_id, "local-user")

    def test_legacy_json_is_imported_and_plaintext_keys_are_scrubbed(self):
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        legacy_secret = "legacy-secret-value"
        self._write_json(
            legacy_root,
            "bots.json",
            [{"id": "legacy-bot", "name": "Imported bot"}],
        )
        self._write_json(
            legacy_root,
            "messages.json",
            [
                {
                    "id": "legacy-message",
                    "thread_id": "legacy-thread",
                    "text": "Imported message",
                }
            ],
        )
        self._write_json(
            legacy_root,
            "settings.json",
            {
                "muapi_api_key": legacy_secret,
                "muapi_base_url": "https://example.test/api/v1",
                "theme": "light",
            },
        )
        self._write_json(
            legacy_root,
            "approvals.json",
            [{"request_id": "legacy-request", "thread_id": "legacy-thread"}],
        )
        self._write_json(
            legacy_root,
            "audit.json",
            [{"event": "legacy.imported", "created_at": "2026-08-20T00:00:00+00:00"}],
        )

        imported = StorageService(legacy_root)

        self.assertEqual(imported.get_bots()[0]["id"], "legacy-bot")
        self.assertEqual(imported.get_messages("legacy-thread")[0]["text"], "Imported message")
        self.assertEqual(imported.get_settings()["muapi_api_key"], legacy_secret)
        self.assertTrue(imported.get_public_settings()["muapi_api_key_configured"])
        self.assertEqual(imported.get_approvals()[0]["request_id"], "legacy-request")
        self.assertEqual(imported.get_audit_events()[0]["event"], "legacy.imported")

        scrubbed = (legacy_root / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn(legacy_secret, scrubbed)
        self.assertIn('"muapi_api_key": ""', scrubbed)
        self.assertEqual((legacy_root / ".encryption.key").stat().st_mode & 0o777, 0o600)

        reopened = StorageService(legacy_root)
        self.assertEqual(reopened.get_settings()["muapi_api_key"], legacy_secret)

    def test_settings_routes_never_return_provider_credentials(self):
        self.service.save_settings({"llm_api_key": "route-secret"})
        original_storage = settings_router.storage_service
        settings_router.storage_service = self.service
        try:
            fetched = asyncio.run(settings_router.get_settings())
            self.assertEqual(fetched.llm_api_key, "")
            self.assertTrue(fetched.llm_api_key_configured)

            saved = asyncio.run(
                settings_router.save_settings(
                    AppSettingsSchema(
                        llm_api_key="",
                        llm_base_url="http://localhost:11434/v1",
                        llm_reasoning_effort="high",
                    )
                )
            )
            self.assertEqual(saved.llm_api_key, "")
            self.assertTrue(saved.llm_api_key_configured)
            self.assertEqual(saved.llm_base_url, "http://localhost:11434/v1")
            self.assertEqual(saved.llm_reasoning_effort, "high")
            self.assertEqual(self.service.get_settings()["llm_api_key"], "route-secret")
        finally:
            settings_router.storage_service = original_storage

    def test_saved_muapi_key_is_migrated_to_provider_neutral_settings(self):
        # A database written before the provider abstraction only has the
        # muapi_* keys. The owner was clearly using MUAPI, so they stay on it.
        self.service.save_settings(
            {"muapi_api_key": "legacy-secret", "muapi_base_url": "https://example.test/api/v1"}
        )
        with self.service.database.connect() as connection:
            connection.execute(
                "DELETE FROM storage_meta WHERE key = 'llm_settings_migrated'"
            )

        migrated = StorageService(self.root).get_settings()
        self.assertEqual(migrated["llm_provider"], "muapi")
        self.assertEqual(migrated["llm_api_key"], "legacy-secret")
        self.assertEqual(migrated["llm_base_url"], "https://example.test/api/v1")

    def test_fresh_install_defaults_to_openai_compatible_and_does_not_migrate(self):
        fresh = StorageService(self.root / "fresh")
        public = fresh.get_public_settings()
        self.assertEqual(public["llm_provider"], "openai_compatible")
        self.assertFalse(public["llm_api_key_configured"])
        self.assertEqual(fresh.database.get_meta("llm_settings_migrated"), "1")

    def test_schema_one_is_upgraded_with_owner_columns(self):
        upgrade_root = self.root / "schema-one"
        upgrade_root.mkdir()
        db_path = upgrade_root / "open-grok-bot.sqlite3"
        with sqlite3.connect(db_path) as connection:
            connection.executescript(SCHEMA_MIGRATIONS[1])
            connection.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-08-20T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO bots(id, payload) VALUES (?, ?)",
                ("old-bot", json.dumps({"id": "old-bot", "name": "Old bot"})),
            )

        upgraded = StorageService(upgrade_root)
        self.assertEqual(upgraded.get_bots()[0]["id"], "old-bot")
        with sqlite3.connect(db_path) as connection:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            owner_id = connection.execute(
                "SELECT owner_id FROM bots WHERE id = 'old-bot'"
            ).fetchone()[0]
        self.assertEqual(version, 2)
        self.assertEqual(owner_id, "local-user")

    def _write_json(self, root: Path, name: str, value):
        (root / name).write_text(json.dumps(value), encoding="utf-8")

    def _database_bytes(self) -> bytes:
        paths = [
            self.root / "open-grok-bot.sqlite3",
            self.root / "open-grok-bot.sqlite3-wal",
            self.root / "open-grok-bot.sqlite3-shm",
        ]
        return b"".join(path.read_bytes() for path in paths if path.exists())


if __name__ == "__main__":
    unittest.main()
