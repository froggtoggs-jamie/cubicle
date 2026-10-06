import asyncio
import unittest
from unittest import mock

from app.services import connector_tools
from app.services.composio_service import ConnectorServiceError
from app.services.connector_tools import (
    READ_ACTION,
    WRITE_ACTION,
    connector_tool_specs,
    sanitize_schema,
    spec_from_composio_tool,
)
from app.services.llm_tools import (
    ToolCallError,
    available_tools,
    build_invocation,
    describe_tools,
    openai_tool_definitions,
)

GMAIL_SEND = {
    "slug": "GMAIL_SEND_EMAIL",
    "name": "Send Email",
    "description": "Sends an email via Gmail.",
    "toolkit": {"slug": "gmail", "name": "Gmail"},
    "tags": ["openWorldHint"],
    "input_parameters": {
        "type": "object",
        "title": "SendEmailRequest",
        "required": ["recipient_email", "body"],
        "properties": {
            "recipient_email": {"type": "string", "title": "Recipient", "examples": ["a@b.c"]},
            "subject": {"type": "string", "default": ""},
            "body": {"type": "string"},
            "cc": {"type": "array", "items": {"type": "string", "properties": {}}},
            "user_id": {"type": "string", "default": "me"},
        },
    },
}

GMAIL_FETCH = {
    "slug": "GMAIL_FETCH_EMAILS",
    "name": "Fetch Emails",
    "description": "Lists messages.",
    "toolkit": {"slug": "gmail", "name": "Gmail"},
    "tags": ["readOnlyHint"],
    "input_parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}},
}


class FakeComposio:
    def __init__(self, key="k", toolkits=("gmail", "github"), fail_list=False):
        self._key = key
        self._toolkits = list(toolkits)
        self._fail_list = fail_list
        self.listed = []

    def get_api_key(self):
        return self._key

    async def list_connected_toolkits(self):
        return list(self._toolkits)

    async def list_tools(self, toolkit, *, important_only=True, limit=40):
        self.listed.append((toolkit, important_only, limit))
        if self._fail_list and toolkit == "github":
            raise ConnectorServiceError("scoped key")
        if toolkit == "gmail":
            return [GMAIL_SEND, GMAIL_FETCH, {"slug": "GMAIL_OLD", "is_deprecated": True, "toolkit": {"slug": "gmail"}}]
        return [{"slug": "GITHUB_" + "X" * 70, "toolkit": {"slug": "github"}}, {"slug": "GITHUB_LIST_BRANCHES", "toolkit": {"slug": "github"}, "tags": ["readOnlyHint"]}]


class SchemaTests(unittest.TestCase):
    def test_schema_is_cleaned_for_strict_servers(self):
        schema = sanitize_schema(GMAIL_SEND["input_parameters"])
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("title", schema)
        self.assertNotIn("examples", schema["properties"]["recipient_email"])
        self.assertNotIn("properties", schema["properties"]["cc"]["items"])
        self.assertEqual(schema["required"], ["recipient_email", "body"])
        self.assertEqual(schema["properties"]["user_id"]["default"], "me")

    def test_missing_or_odd_schema_becomes_an_empty_object(self):
        self.assertEqual(sanitize_schema(None), {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertNotIn("required", sanitize_schema({"type": "object", "required": ["ghost"]}))


class SpecTests(unittest.TestCase):
    def test_tools_become_specs_with_approval_from_the_read_only_hint(self):
        send = spec_from_composio_tool(GMAIL_SEND)
        self.assertEqual(send.name, "gmail_send_email")
        self.assertTrue(send.needs_approval)
        self.assertEqual(send.group, "connector")
        self.assertTrue(send.description.startswith("[Gmail]"))
        fetch = spec_from_composio_tool(GMAIL_FETCH)
        self.assertFalse(fetch.needs_approval)
        self.assertTrue(fetch.connector["read_only"])

    def test_deprecated_and_unnameable_tools_are_skipped(self):
        self.assertIsNone(spec_from_composio_tool({"slug": "GMAIL_OLD", "is_deprecated": True}))
        self.assertIsNone(spec_from_composio_tool({"slug": "GITHUB_" + "X" * 70}))
        self.assertIsNone(spec_from_composio_tool({}))

    def test_catalog_covers_every_connected_toolkit(self):
        service = FakeComposio()
        specs = asyncio.run(connector_tool_specs(service))
        self.assertEqual([s.name for s in specs], ["github_list_branches", "gmail_send_email", "gmail_fetch_emails"])
        self.assertEqual([t for t, _, _ in service.listed], ["github", "gmail"])

    def test_no_key_or_failures_degrade_to_no_connector_tools(self):
        self.assertEqual(asyncio.run(connector_tool_specs(FakeComposio(key=""))), [])
        specs = asyncio.run(connector_tool_specs(FakeComposio(fail_list=True)))
        self.assertEqual([s.name for s in specs], ["gmail_send_email", "gmail_fetch_emails"])

        class Unreachable(FakeComposio):
            async def list_connected_toolkits(self):
                raise ConnectorServiceError("down")

        self.assertEqual(asyncio.run(connector_tool_specs(Unreachable())), [])

    def test_connector_specs_join_the_catalog_and_replace_the_github_fallback(self):
        connectors = asyncio.run(connector_tool_specs(FakeComposio()))
        everything = available_tools(computer=False, github=True, connectors=connectors)
        names = [s.name for s in everything]
        self.assertIn("gmail_send_email", names)
        self.assertNotIn("github_list_issues", names)
        text = describe_tools(everything)
        self.assertIn("Connected apps (github, Gmail)", text)
        self.assertIn("github_list_branches", text)
        for definition in openai_tool_definitions(everything):
            self.assertRegex(definition["function"]["name"], r"^[a-zA-Z0-9_-]{1,64}$")
            self.assertEqual(definition["function"]["parameters"]["type"], "object")


class InvocationTests(unittest.TestCase):
    def setUp(self):
        self.specs = asyncio.run(connector_tool_specs(FakeComposio()))

    def test_schema_violations_are_rejected_before_the_gateway(self):
        with self.assertRaisesRegex(ToolCallError, "Unknown argument"):
            build_invocation("gmail_send_email", {"recipient_email": "a@b.c", "body": "x", "password": "p"}, "bot-1", specs=self.specs)
        with self.assertRaisesRegex(ToolCallError, "Missing required"):
            build_invocation("gmail_send_email", {"recipient_email": "a@b.c"}, "bot-1", specs=self.specs)
        with self.assertRaisesRegex(ToolCallError, "Unknown tool"):
            build_invocation("gmail_nope", {}, "bot-1", specs=self.specs)

    def test_invocations_carry_the_tool_slug_and_a_safe_preview(self):
        call = build_invocation(
            "gmail_send_email", {"recipient_email": "a@b.c", "subject": "Hi", "body": "x" * 100}, "bot-1", specs=self.specs
        )
        self.assertEqual(call.name, WRITE_ACTION)
        self.assertEqual(call.arguments["tool"], "GMAIL_SEND_EMAIL")
        self.assertEqual(call.arguments["arguments"]["subject"], "Hi")
        self.assertEqual(call.target, {"connector": "gmail", "tool": "GMAIL_SEND_EMAIL"})
        self.assertTrue(call.preview.startswith("Gmail: Send Email (recipient_email=a@b.c, subject=Hi, body="))
        self.assertIn("...", call.preview)

        read = build_invocation("gmail_fetch_emails", {"query": "is:unread"}, "bot-1", specs=self.specs)
        self.assertEqual(read.name, READ_ACTION)

    def test_executor_unwraps_composio_results_and_surfaces_failures(self):
        class Service:
            async def execute_tool(self, slug, arguments):
                if slug == "GMAIL_FETCH_EMAILS":
                    return {"tool": slug, "data": {"messages": [1, 2]}}
                raise ConnectorServiceError("quota")

        with mock.patch.object(connector_tools, "composio_service", Service()):
            read = build_invocation("gmail_fetch_emails", {"query": "x"}, "bot-1", specs=self.specs)
            result = asyncio.run(connector_tools.execute_composio_tool(read))
            self.assertEqual(result["data"]["messages"], [1, 2])
            send = build_invocation("gmail_send_email", {"recipient_email": "a@b.c", "body": "x"}, "bot-1", specs=self.specs)
            with self.assertRaisesRegex(ConnectorServiceError, "quota"):
                asyncio.run(connector_tools.execute_composio_tool(send))


if __name__ == "__main__":
    unittest.main()
