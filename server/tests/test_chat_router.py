import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.routers import chat as chat_router
from app.services.action_gateway import ActionGateway
from app.services.llm_config import LLMConfig
from app.services.storage_service import StorageService
from app.services.workspace_service import WorkspaceService


class NoComposio:
    def get_api_key(self):
        return ""


class DecidingApprovalBroker:
    """Approval broker stand-in that answers every request the same way."""

    def __init__(self, decision="allow"):
        self.decision = decision
        self.opened = []

    def open(self, thread_id, bot_id, call, request_id=None):
        self.opened.append(request_id)
        return {
            "request_id": request_id,
            "thread_id": thread_id,
            "bot_id": bot_id,
            "tool": call.name,
            "summary": call.summary,
            "arguments": call.arguments_for_display,
            "status": "pending",
        }

    async def wait(self, request_id):
        return self.decision


def _scripted_stream(recorder, rounds):
    """Yield a different scripted event list on each call, recording inputs."""
    state = {"calls": 0}

    async def stream_chat_completion(model, messages, system_prompt="", config=None, tools=None):
        index = min(state["calls"], len(rounds) - 1)
        state["calls"] += 1
        recorder.setdefault("rounds", []).append(
            {"model": model, "messages": [dict(m) for m in messages], "system_prompt": system_prompt, "tools": tools}
        )
        for event in rounds[index]:
            yield event

    return stream_chat_completion


def _fake_stream(recorder):
    async def stream_chat_completion(model, messages, system_prompt="", config=None, tools=None):
        recorder["model"] = model
        recorder["messages"] = messages
        recorder["system_prompt"] = system_prompt
        recorder["tools"] = tools
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

    def _run_turn(self, thread_id, model=None, stream=None, gateway=None, config=None):
        recorder = {}
        patches = [
            mock.patch.object(chat_router, "storage_service", self.storage),
            mock.patch.object(chat_router.llm_service, "current_llm_config", return_value=config or self.config),
            mock.patch.object(chat_router.llm_service, "stream_chat_completion", stream or _fake_stream(recorder)),
            mock.patch.object(chat_router, "composio_service", NoComposio()),
        ]
        if gateway is not None:
            patches.append(mock.patch.object(chat_router, "action_gateway", gateway))
        for p in patches:
            p.start()
        try:
            response = asyncio.run(chat_router.stream_turn(thread_id, model=model))
            events = asyncio.run(_collect_sse(response))
        finally:
            for p in reversed(patches):
                p.stop()
        return recorder, events

    def _gateway(self, decision="allow"):
        workspace_root = Path(self.temp_dir.name) / "workspace"
        workspace_root.mkdir(exist_ok=True)
        (workspace_root / "hello.txt").write_text("hello", encoding="utf-8")
        broker = DecidingApprovalBroker(decision)
        return ActionGateway(workspace=WorkspaceService(workspace_root), approvals=broker, audit=self.storage), broker

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
        self.assertIn("## Tools", recorder["system_prompt"])
        self.assertEqual([t["function"]["name"] for t in recorder["tools"]], ["workspace_list", "workspace_read", "workspace_write"])
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

    def test_model_tool_call_runs_through_the_gateway_and_feeds_back(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "what is in the workspace?"}
        )
        gateway, broker = self._gateway("allow")
        recorder = {}
        stream = _scripted_stream(
            recorder,
            [
                [
                    {"type": "content.delta", "delta": "Let me look."},
                    {"type": "tool_calls", "calls": [{"id": "call_1", "name": "workspace_list", "arguments": "{}"}]},
                    {"type": "turn.completed", "ok": True},
                ],
                [
                    {"type": "content.delta", "delta": "There is hello.txt."},
                    {"type": "turn.completed", "ok": True},
                ],
            ],
        )

        _, events = self._run_turn(bot["id"], stream=stream, gateway=gateway)

        types = [e["type"] for e in events]
        self.assertEqual(types.count("request.opened"), 1)
        self.assertIn("tool.started", types)
        completed = next(e for e in events if e["type"] == "tool.completed")
        self.assertEqual(completed["callName"], "workspace_list")
        self.assertEqual(completed["tool"], "workspace.list")
        self.assertEqual([entry["name"] for entry in completed["result"]["entries"]], ["hello.txt"])
        self.assertEqual(len(broker.opened), 1)

        self.assertEqual(len(recorder["rounds"]), 2)
        second = recorder["rounds"][1]["messages"]
        self.assertEqual(second[-2]["role"], "assistant")
        self.assertEqual(second[-2]["tool_calls"][0]["name"], "workspace_list")
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-1]["tool_call_id"], "call_1")
        self.assertIn("hello.txt", second[-1]["content"])

        saved = self.storage.get_messages(bot["id"])[-1]
        self.assertEqual(saved["text"], "Let me look.\n\nThere is hello.txt.")
        self.assertEqual(saved["raw_payload"]["tool_calls"][0]["status"], "completed")
        self.assertEqual(saved["raw_payload"]["tool_calls"][0]["name"], "workspace_list")

    def test_denied_tool_call_is_reported_to_the_model_and_not_executed(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "write a file"}
        )
        gateway, _ = self._gateway("deny")
        recorder = {}
        stream = _scripted_stream(
            recorder,
            [
                [
                    {"type": "tool_calls", "calls": [{"id": "call_1", "name": "workspace_write", "arguments": "{\"path\": \"new.txt\", \"content\": \"x\"}"}]},
                    {"type": "turn.completed", "ok": True},
                ],
                [{"type": "content.delta", "delta": "Understood, I will not write it."}, {"type": "turn.completed", "ok": True}],
            ],
        )

        _, events = self._run_turn(bot["id"], stream=stream, gateway=gateway)

        self.assertIn("tool.denied", [e["type"] for e in events])
        self.assertNotIn("tool.started", [e["type"] for e in events])
        self.assertFalse((Path(self.temp_dir.name) / "workspace" / "new.txt").exists())
        tool_message = recorder["rounds"][1]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("denied", tool_message["content"])
        saved = self.storage.get_messages(bot["id"])[-1]
        self.assertEqual(saved["raw_payload"]["tool_calls"][0]["status"], "denied")
        self.assertEqual(saved["text"], "Understood, I will not write it.")

    def test_invalid_tool_arguments_never_reach_the_gateway(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "read it"}
        )
        gateway, broker = self._gateway("allow")
        recorder = {}
        stream = _scripted_stream(
            recorder,
            [
                [
                    {"type": "tool_calls", "calls": [{"id": "call_1", "name": "workspace_read", "arguments": "{}"}]},
                    {"type": "turn.completed", "ok": True},
                ],
                [{"type": "content.delta", "delta": "Which file?"}, {"type": "turn.completed", "ok": True}],
            ],
        )

        _, events = self._run_turn(bot["id"], stream=stream, gateway=gateway)

        failed = next(e for e in events if e["type"] == "tool.failed")
        self.assertIn("'path' is required", failed["error"])
        self.assertEqual(broker.opened, [])
        self.assertIn("'path' is required", recorder["rounds"][1]["messages"][-1]["content"])

    def test_tool_rounds_are_capped(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "loop"}
        )
        gateway, _ = self._gateway("allow")
        recorder = {}
        forever = [
            {"type": "tool_calls", "calls": [{"id": "c", "name": "workspace_list", "arguments": "{}"}]},
            {"type": "turn.completed", "ok": True},
        ]
        with mock.patch.object(chat_router.settings, "LLM_MAX_TOOL_ROUNDS", 2):
            _, events = self._run_turn(bot["id"], stream=_scripted_stream(recorder, [forever]), gateway=gateway)

        # Two tool rounds, then one forced text-only round without tools.
        self.assertEqual(len(recorder["rounds"]), 3)
        self.assertIsNotNone(recorder["rounds"][1]["tools"])
        self.assertIsNone(recorder["rounds"][2]["tools"])
        self.assertIn("tool budget for this turn is used up", recorder["rounds"][2]["system_prompt"])
        # The second round's calls were answered as skipped, never executed.
        last_tool_message = recorder["rounds"][2]["messages"][-1]
        self.assertEqual(last_tool_message["role"], "tool")
        self.assertIn("skipped", last_tool_message["content"])
        self.assertEqual([e["type"] for e in events].count("tool.started"), 1)
        # The scripted model still produced no text, so the fallback note is shown.
        self.assertIn("Stopped after 2 tool rounds", "".join(e.get("delta", "") for e in events if e["type"] == "content.delta"))
        saved = self.storage.get_messages(bot["id"])[-1]
        self.assertEqual([r["status"] for r in saved["raw_payload"]["tool_calls"]], ["completed", "skipped"])

    def test_forced_final_round_answer_is_used_when_the_model_gives_one(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "loop"}
        )
        gateway, _ = self._gateway("allow")
        recorder = {}
        tool_round = [
            {"type": "tool_calls", "calls": [{"id": "c", "name": "workspace_list", "arguments": "{}"}]},
            {"type": "turn.completed", "ok": True},
        ]
        final_round = [{"type": "content.delta", "delta": "Here is what I found so far."}, {"type": "turn.completed", "ok": True}]
        with mock.patch.object(chat_router.settings, "LLM_MAX_TOOL_ROUNDS", 1):
            _, events = self._run_turn(bot["id"], stream=_scripted_stream(recorder, [tool_round, final_round]), gateway=gateway)

        self.assertEqual(len(recorder["rounds"]), 2)
        text = "".join(e.get("delta", "") for e in events if e["type"] == "content.delta")
        self.assertEqual(text, "Here is what I found so far.")
        self.assertNotIn("Stopped after", text)

    def test_muapi_provider_gets_no_tools(self):
        bot = self.storage.get_bots()[0]
        self.storage.add_message(
            {"id": "m1", "thread_id": bot["id"], "bot_id": bot["id"], "sender": "user", "text": "hi"}
        )
        muapi = LLMConfig(provider="muapi", api_key="k", base_url="https://api.muapi.ai/api/v1", reasoning_effort="", default_model="grok-4-5")
        recorder, _ = self._run_turn(bot["id"], config=muapi)
        self.assertIsNone(recorder["tools"])
        self.assertNotIn("## Tools", recorder["system_prompt"])

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
