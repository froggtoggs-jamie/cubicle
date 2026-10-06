import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from app.routers import bots as bots_router
from app.services.computer_provider import ComputerProviderError
from app.services.llm_config import LLMConfig
from app.services.storage_service import StorageService
from app.services.turn_manager import TurnManager


class FakeComputers:
    def __init__(self, running=True):
        self.running = running
        self.stopped = []

    async def stop(self, computer_id):
        self.stopped.append(computer_id)
        if not self.running:
            raise ComputerProviderError("No computer")
        return {"state": "stopped"}


class BotsRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = StorageService(Path(self.temp_dir.name))
        self.computers = FakeComputers()
        self.turns = TurnManager()
        config = LLMConfig(provider="openai_compatible", api_key="", base_url="http://x/v1", reasoning_effort="", default_model="local-default")
        self.patches = [
            mock.patch.object(bots_router, "storage_service", self.storage),
            mock.patch.object(bots_router, "computer_provider", self.computers),
            mock.patch.object(bots_router, "turn_manager", self.turns),
            mock.patch.object(bots_router.llm_service, "current_llm_config", return_value=config),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass  # Windows keeps the SQLite file open a moment longer.

    def test_create_fills_defaults_and_requires_a_name(self):
        bot = asyncio.run(bots_router.create_bot(bots_router.BotInput(name="  Helper  ")))
        self.assertEqual(bot["name"], "Helper")
        self.assertEqual(bot["model"], "local-default")
        self.assertEqual(bot["role"], "AI Assistant")
        self.assertIn("Helper", bot["system_prompt"])
        self.assertFalse(bot["archived"])
        self.assertIn(bot["id"], [b["id"] for b in self.storage.get_bots()])

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(bots_router.create_bot(bots_router.BotInput(name="   ")))
        self.assertEqual(caught.exception.status_code, 422)

    def test_update_only_touches_editable_fields_and_archives(self):
        bot = asyncio.run(bots_router.create_bot(bots_router.BotInput(name="Helper", model="m1")))
        updated = asyncio.run(bots_router.update_bot(bot["id"], bots_router.BotUpdate(name="Renamed", system_prompt="Be terse.", model="")))
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["system_prompt"], "Be terse.")
        self.assertEqual(updated["model"], "m1")  # a blank model keeps the old one
        self.assertEqual(updated["id"], bot["id"])

        archived = asyncio.run(bots_router.update_bot(bot["id"], bots_router.BotUpdate(archived=True)))
        self.assertTrue(archived["archived"])
        self.assertTrue(archived["archived_at"])
        restored = asyncio.run(bots_router.update_bot(bot["id"], bots_router.BotUpdate(archived=False)))
        self.assertFalse(restored["archived"])
        self.assertIsNone(restored["archived_at"])

        with self.assertRaises(HTTPException):
            asyncio.run(bots_router.update_bot("missing", bots_router.BotUpdate(name="x")))

    def test_archiving_cancels_a_running_turn(self):
        bot = asyncio.run(bots_router.create_bot(bots_router.BotInput(name="Busy")))

        async def scenario():
            gate = asyncio.Event()

            async def forever(turn):
                await gate.wait()

            turn = self.turns.start(bot["id"], bot["id"], "m", forever)
            await asyncio.sleep(0.01)
            await bots_router.update_bot(bot["id"], bots_router.BotUpdate(archived=True))
            await asyncio.wait_for(turn.task, 2)
            self.assertEqual(turn.status, "cancelled")

        asyncio.run(scenario())

    def test_delete_removes_transcript_and_stops_the_computer(self):
        bot = asyncio.run(bots_router.create_bot(bots_router.BotInput(name="Gone")))
        self.storage.add_message({"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "hi"})
        self.storage.add_message({"id": "m2", "thread_id": "other", "bot_id": "other", "sender": "user", "text": "keep"})

        result = asyncio.run(bots_router.delete_bot(bot["id"]))
        self.assertEqual(result["deleted_messages"], 1)
        self.assertNotIn(bot["id"], [b["id"] for b in self.storage.get_bots()])
        self.assertEqual(self.storage.get_messages(bot["id"]), [])
        self.assertEqual(len(self.storage.get_messages("other")), 1)
        self.assertEqual(len(self.computers.stopped), 1)

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(bots_router.delete_bot(bot["id"]))
        self.assertEqual(caught.exception.status_code, 404)

    def test_delete_succeeds_when_there_is_no_computer(self):
        self.computers.running = False
        bot = asyncio.run(bots_router.create_bot(bots_router.BotInput(name="Plain")))
        result = asyncio.run(bots_router.delete_bot(bot["id"]))
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
