import json
import unittest
from unittest import mock

from app.services import context_builder
from app.services.context_builder import (
    OMITTED_NOTE,
    build_history_messages,
    cap_message_results,
    trim_result_text,
)


def _bot(text, calls=None):
    return {"sender": "bot", "text": text, "raw_payload": {"tool_calls": calls} if calls else None}


def _user(text):
    return {"sender": "user", "text": text}


class ContextBuilderTests(unittest.TestCase):
    def test_results_are_trimmed_and_capped_per_message(self):
        with mock.patch.object(context_builder.settings, "CONTEXT_TOOL_RESULT_CHARS", 10):
            self.assertEqual(trim_result_text("short"), "short")
            self.assertTrue(trim_result_text("x" * 50).startswith("x" * 10 + "\n...[trimmed 40 characters]"))
        records = [{"id": "a", "result": "1" * 30}, {"id": "b", "result": "2" * 30}, {"id": "c", "result": "3" * 30}]
        with mock.patch.object(context_builder.settings, "CONTEXT_TOOL_RESULTS_MESSAGE_CHARS", 65):
            cap_message_results(records)
        # The oldest result goes first; the newer two still fit.
        self.assertIsNone(records[0]["result"])
        self.assertTrue(records[0]["result_dropped"])
        self.assertEqual(records[1]["result"], "2" * 30)
        self.assertEqual(records[2]["result"], "3" * 30)

    def test_recent_turns_replay_full_results_and_older_turns_get_stubs(self):
        history = [
            _user("find it"),
            _bot("Found A.", [{"id": "c1", "name": "exa_search", "raw_arguments": '{"query": "A"}', "status": "completed", "summary": "Search A", "result": '{"hits": ["A"]}'}]),
            _user("and B?"),
            _bot("Found B.", [{"id": "c2", "name": "exa_search", "arguments": {"query": "B"}, "status": "completed", "summary": "Search B", "result": '{"hits": ["B"]}'},
                             {"id": "c3", "name": "gmail_send_email", "status": "denied", "summary": "Send mail", "error": "The user denied this action"}]),
            _user("thanks"),
            _bot("You're welcome."),
        ]
        messages = build_history_messages(history, replay_tools=True, replay_turns=1)

        roles = [(m["role"], m.get("content")[:12] if isinstance(m.get("content"), str) else "") for m in messages]
        self.assertEqual(
            roles,
            [("user", "find it"), ("assistant", ""), ("tool", '{"status": "'), ("assistant", "Found A."),
             ("user", "and B?"), ("assistant", ""), ("tool", '{"hits": ["B'), ("tool", '{"status": "'), ("assistant", "Found B."),
             ("user", "thanks"), ("assistant", "You're welco")],
        )
        # Older turn: tool calls kept, result replaced by a stub that still names the summary.
        older = json.loads(messages[2]["content"])
        self.assertEqual(older["note"], OMITTED_NOTE)
        self.assertEqual(older["summary"], "Search A")
        self.assertEqual(messages[1]["tool_calls"], [{"id": "c1", "name": "exa_search", "arguments": '{"query": "A"}'}])
        # Arguments stored as a dict are serialised; a denied call explains itself.
        self.assertEqual(messages[5]["tool_calls"][0]["arguments"], '{"query": "B"}')
        denied = json.loads(messages[7]["content"])
        self.assertEqual(denied["status"], "denied")
        self.assertIn("denied", denied["error"])

    def test_without_tool_replay_only_texts_are_used(self):
        history = [_user("hi"), _bot("Found A.", [{"id": "c1", "name": "t", "status": "completed", "result": "big"}])]
        messages = build_history_messages(history, replay_tools=False)
        self.assertEqual([(m["role"], m["content"]) for m in messages], [("user", "hi"), ("assistant", "Found A.")])

    def test_records_without_ids_or_dropped_results_degrade_gracefully(self):
        history = [
            _user("go"),
            _bot("Done.", [{"name": "no-id", "status": "completed", "result": "x"},
                           {"id": "c9", "name": "t", "status": "completed", "result": None, "result_dropped": True}]),
        ]
        messages = build_history_messages(history, replay_tools=True, replay_turns=5)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(json.loads(messages[2]["content"])["note"], "result not retained")
        self.assertEqual(len(messages[1]["tool_calls"]), 1)


if __name__ == "__main__":
    unittest.main()
