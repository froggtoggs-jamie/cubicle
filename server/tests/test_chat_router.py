import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.routers import chat as chat_router
from app.services.llm_config import LLMConfig
from app.services.storage_service import StorageService


def _fake_stream(recorder):
    async def stream_chat_completion(model, messages, system_prompt="", config=None):
        recorder["model"] = model
        recorder["messages"] = messages
        recorder["system_prompt"] = system_prompt
        yield {"type": "reasoning.delta", "delta": "Considering "}
        yield {"type": "reasoning.delta", "delta": "the question."}
        yield {"type": "content.delta", "delta": "Hello "}
        yield {"type": "content.delta", "delta": "there"}
        yield {"type": "turn.completed", "ok": True}

    return stream_chat_completion


async def _collect_sse(response):
    # sse-starlette keeps the raw generator as `body_iterator` and encodes each
    # {"event", "data"} dict only when the response is actually sent.
    events = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, dict):
            events.append(json.loads(chunk["data"]))
            continue
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


class ChatStreamRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = StorageService(Path(self.temp_dir.name))
        self.config = LLMConfig(
            provider="openai_compatible",
            api_key="",
            base_url="http://localhost:11434/v1",
            reasoning_effort="",
            default_model="default-model",
        )

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            # Windows keeps the SQLite file open briefly after the last connection.
            pass

    def _run_turn(self, thread_id, model=None):
        recorder = {}
        with mock.patch.object(chat_router, "storage_service", self.storage), mock.patch.object(
            chat_router.llm_service, "current_llm_config", return_value=self.config
        ), mock.patch.object(
            chat_router.llm_service, "stream_chat_completion", _fake_stream(recorder)
        ):
            response = asyncio.run(chat_router.stream_turn(thread_id, model=model))
            events = asyncio.run(_collect_sse(response))
        return recorder, events

    def test_plain_turn_streams_deltas_and_persists_the_reply(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "hi"}
        )

        recorder, events = self._run_turn(bot["id"])

        self.assertEqual(events[0]["type"], "turn.started")
        self.assertEqual(events[0]["model"], bot["model"])
        self.assertEqual([e["delta"] for e in events if e["type"] == "content.delta"], ["Hello ", "there"])
        self.assertEqual(
            [e["delta"] for e in events if e["type"] == "reasoning.delta"],
            ["Considering ", "the question."],
        )
        self.assertEqual(events[-1]["type"], "turn.completed")

        self.assertEqual(recorder["model"], bot["model"])
        self.assertIn(bot["system_prompt"], recorder["system_prompt"])
        self.assertEqual(recorder["messages"][-1], {"role": "user", "content": "hi", "image_url": None})

        saved = self.storage.get_messages(bot["id"])
        self.assertEqual(saved[-1]["sender"], "bot")
        self.assertEqual(saved[-1]["text"], "Hello there")
        # Thinking is kept for display but stays out of `text`, which is what
        # gets replayed to the model on the next turn.
        self.assertEqual(saved[-1]["raw_payload"], {"reasoning": "Considering the question."})
        self.assertNotIn("Considering", saved[-1]["text"])

    def test_model_falls_back_to_bot_then_configured_default(self):
        self.storage.add_message(
            {"id": "m1", "thread_id": "no-such-bot", "bot_id": "no-such-bot", "sender": "user", "text": "hi"}
        )
        recorder, events = self._run_turn("no-such-bot")
        self.assertEqual(recorder["model"], "default-model")
        self.assertEqual(events[0]["model"], "default-model")

        recorder, _ = self._run_turn("no-such-bot", model="override-model")
        self.assertEqual(recorder["model"], "override-model")

    def test_rejected_tool_command_is_reported_and_added_to_the_prompt(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {
                "id": "m1",
                "thread_id": bot["id"],
                "bot_id": bot["id"],
                "sender": "user",
                "text": "/workspace delete everything",
            }
        )

        recorder, events = self._run_turn(bot["id"])

        failed = [e for e in events if e["type"] == "tool.failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["tool"], "workspace")
        self.assertIn("rejected before execution", recorder["system_prompt"])
        self.assertEqual(events[-1]["type"], "turn.completed")


if __name__ == "__main__":
    unittest.main()
