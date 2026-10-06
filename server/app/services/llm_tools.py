"""Tools the model may call, each mapped onto a governed gateway action.

The model never executes anything itself. It proposes a call; this module
turns the call into the same `WorkspaceToolCall` / `ActionInvocation` objects
the slash commands produce, and the action gateway applies its deny-by-default
registry and approval rules exactly as before.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from app.services.action_gateway import ActionInvocation
from app.services.computer_provider import computer_id_for_bot
from app.services.workspace_service import WorkspaceToolCall


class ToolCallError(ValueError):
    """Raised when the model's tool call cannot be turned into a valid action."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    group: str
    needs_approval: bool


_SAFE_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_INPUT_TYPES = ("click", "keypress", "type", "scroll")


def _obj(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


WORKSPACE_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="workspace_list",
        description="List files and folders in the shared workspace directory. Paths are relative to the workspace root; omit path for the root.",
        parameters=_obj({"path": {"type": "string", "description": "Relative directory path, default '.'"}}),
        group="workspace",
        needs_approval=True,
    ),
    ToolSpec(
        name="workspace_read",
        description="Read a UTF-8 text file from the shared workspace (at most 128 KB).",
        parameters=_obj({"path": {"type": "string", "description": "Relative file path"}}, ["path"]),
        group="workspace",
        needs_approval=True,
    ),
    ToolSpec(
        name="workspace_write",
        description="Create or overwrite a UTF-8 text file in the shared workspace. The destination directory must already exist.",
        parameters=_obj(
            {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Full file contents"},
            },
            ["path", "content"],
        ),
        group="workspace",
        needs_approval=True,
    ),
]

COMPUTER_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="computer_start",
        description="Start this bot's sandboxed computer: a Linux container with a Chromium browser and a shell, with internet access. Call it when another computer tool reports that the computer is not running.",
        parameters=_obj({}),
        group="computer",
        needs_approval=False,
    ),
    ToolSpec(
        name="computer_screenshot",
        description="Capture the sandbox browser screen as an image so you can see the current page. Use it after navigating or sending input.",
        parameters=_obj({}),
        group="computer",
        needs_approval=False,
    ),
    ToolSpec(
        name="computer_browser_navigate",
        description="Open an http(s) URL in the sandbox browser.",
        parameters=_obj({"url": {"type": "string", "description": "Absolute http or https URL"}}, ["url"]),
        group="computer",
        needs_approval=True,
    ),
    ToolSpec(
        name="computer_terminal_execute",
        description="Run a shell command inside the sandbox (30 second timeout, 1 MB of output). The sandbox filesystem is separate from the shared workspace; its writable directory is /workspace.",
        parameters=_obj({"command": {"type": "string", "description": "Shell command to run"}}, ["command"]),
        group="computer",
        needs_approval=True,
    ),
    ToolSpec(
        name="computer_files_list",
        description="List files inside the sandbox at a path (default /workspace).",
        parameters=_obj({"path": {"type": "string", "description": "Absolute path inside the sandbox"}}),
        group="computer",
        needs_approval=False,
    ),
    ToolSpec(
        name="computer_request_takeover",
        description="Ask the user to take control of your computer for a step that needs a human: a login, a CAPTCHA, a payment, a judgement call, or checking your work. After calling it, tell the user exactly what to do and end your turn; they will message you when they hand control back. While the user has control, your input and navigation tools are refused.",
        parameters=_obj({"reason": {"type": "string", "description": "What you need the user to do, in one or two sentences"}}, ["reason"]),
        group="computer",
        needs_approval=False,
    ),
    ToolSpec(
        name="computer_send_input",
        description="Send pointer or keyboard input to the sandbox browser. Coordinates are page pixels at the screenshot's size (1280x720).",
        parameters=_obj(
            {
                "type": {"type": "string", "enum": list(_INPUT_TYPES)},
                "x": {"type": "number", "description": "click: x coordinate"},
                "y": {"type": "number", "description": "click: y coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "key": {"type": "string", "description": "keypress: key name such as Enter, Tab, ArrowDown"},
                "text": {"type": "string", "description": "type: text to type, at most 2000 characters"},
                "deltaX": {"type": "number", "description": "scroll: horizontal amount"},
                "deltaY": {"type": "number", "description": "scroll: vertical amount"},
            },
            ["type"],
        ),
        group="computer",
        needs_approval=True,
    ),
]

GITHUB_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="github_list_issues",
        description="List up to 10 issues in a GitHub repository through the connected GitHub account.",
        parameters=_obj(
            {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Default open"},
            },
            ["owner", "repo"],
        ),
        group="github",
        needs_approval=False,
    ),
    ToolSpec(
        name="github_create_issue",
        description="Create an issue in a GitHub repository through the connected GitHub account.",
        parameters=_obj(
            {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string", "description": "At most 256 characters"},
                "body": {"type": "string", "description": "Markdown body, at most 10000 characters"},
            },
            ["owner", "repo", "title"],
        ),
        group="github",
        needs_approval=True,
    ),
]


def available_tools(*, computer: bool, github: bool) -> List[ToolSpec]:
    specs = list(WORKSPACE_TOOLS)
    if computer:
        specs.extend(COMPUTER_TOOLS)
    if github:
        specs.extend(GITHUB_TOOLS)
    return specs


def openai_tool_definitions(specs: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
        }
        for spec in specs
    ]


def describe_tools(specs: List[ToolSpec]) -> str:
    """System-prompt paragraph so the model knows what it has and the rules."""
    if not specs:
        return ""
    groups = {spec.group for spec in specs}
    lines = [
        "## Tools",
        "You can call the tools listed in this request. Results come back to you as tool messages; never guess or invent a result you did not receive.",
        "Some calls need the user's approval and pause until the user allows or denies them in the chat. If a call is denied, stop and ask the user how to proceed instead of retrying.",
    ]
    if "workspace" in groups:
        lines.append("- Shared workspace: a directory on the host the user chose. Use workspace_list, workspace_read, and workspace_write for files there.")
    if "computer" in groups:
        lines.append(
            "- Your computer: a sandboxed Linux container with a Chromium browser and a shell, with internet access. "
            "Navigate with computer_browser_navigate, then computer_screenshot to see the page, computer_send_input to click or type, "
            "and computer_terminal_execute for commands. If a computer tool says the computer is not running, call computer_start first. "
            "The user can watch the same desktop live and take control of it; when a step needs a human, call computer_request_takeover."
        )
    if "github" in groups:
        lines.append("- GitHub: github_list_issues and github_create_issue act through the user's connected GitHub account.")
    approval = sorted(spec.name for spec in specs if spec.needs_approval)
    if approval:
        lines.append("Tools that need approval: " + ", ".join(approval) + ".")
    return "\n".join(lines)


# ---- turning a model call into a gateway invocation ---------------------------


def parse_arguments(raw: Any) -> Dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolCallError(f"Tool arguments were not valid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ToolCallError("Tool arguments must be a JSON object.")
        return parsed
    raise ToolCallError("Tool arguments must be a JSON object.")


def _string(args: Dict[str, Any], key: str, *, required: bool = False, default: str = "", max_len: int = 4000) -> str:
    value = args.get(key, None)
    if value is None or value == "":
        if required:
            raise ToolCallError(f"'{key}' is required.")
        return default
    if not isinstance(value, str):
        raise ToolCallError(f"'{key}' must be a string.")
    if len(value) > max_len:
        raise ToolCallError(f"'{key}' is longer than {max_len} characters.")
    return value


def _number(args: Dict[str, Any], key: str, *, required: bool = False, default: float = 0.0) -> float:
    value = args.get(key, None)
    if value is None:
        if required:
            raise ToolCallError(f"'{key}' is required.")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolCallError(f"'{key}' must be a number.")
    return float(value)


def _computer_invocation(action: str, bot_id: str, arguments: Dict[str, Any], preview: str) -> ActionInvocation:
    computer_id = computer_id_for_bot(bot_id)
    payload = dict(arguments)
    payload.update({"bot_id": bot_id, "computer_id": computer_id})
    return ActionInvocation(
        name=f"computer.{action}",
        arguments=payload,
        target={"bot_id": bot_id, "computer_id": computer_id},
        preview=preview,
    )


def build_invocation(
    name: str,
    arguments: Dict[str, Any],
    bot_id: str,
) -> Union[WorkspaceToolCall, ActionInvocation]:
    """Validate a model tool call and build the gateway input for it."""
    if name == "workspace_list":
        return WorkspaceToolCall("workspace.list", _string(arguments, "path", default=".", max_len=1000))
    if name == "workspace_read":
        return WorkspaceToolCall("workspace.read", _string(arguments, "path", required=True, max_len=1000))
    if name == "workspace_write":
        return WorkspaceToolCall(
            "workspace.write",
            _string(arguments, "path", required=True, max_len=1000),
            _string(arguments, "content", required=True, max_len=200_000),
        )

    if name == "computer_start":
        return _computer_invocation("start", bot_id, {}, f"Start the computer for {bot_id}")
    if name == "computer_screenshot":
        return _computer_invocation("screenshot", bot_id, {}, f"Capture the computer screen for {bot_id}")
    if name == "computer_request_takeover":
        reason = _string(arguments, "reason", required=True, max_len=500).strip()
        if not reason:
            raise ToolCallError("'reason' is required.")
        return _computer_invocation("request_takeover", bot_id, {"reason": reason}, f"Ask the user to take over the computer for {bot_id}: {reason[:120]}")
    if name == "computer_browser_navigate":
        url = _string(arguments, "url", required=True, max_len=4000).strip()
        if not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
            raise ToolCallError("'url' must be an absolute http or https URL.")
        return _computer_invocation("browser_navigate", bot_id, {"url": url}, f"Navigate the computer for {bot_id} to {url}")
    if name == "computer_terminal_execute":
        command = _string(arguments, "command", required=True, max_len=4000)
        return _computer_invocation("terminal_execute", bot_id, {"command": command}, f"Run in the computer for {bot_id}: {command[:120]}")
    if name == "computer_files_list":
        path = _string(arguments, "path", default="/workspace", max_len=1000)
        return _computer_invocation("files_list", bot_id, {"path": path}, f"List computer files for {bot_id} at {path}")
    if name == "computer_send_input":
        kind = _string(arguments, "type", required=True, max_len=20).lower()
        if kind not in _INPUT_TYPES:
            raise ToolCallError("'type' must be one of click, keypress, type, scroll.")
        event: Dict[str, Any] = {"type": kind}
        if kind == "click":
            event["x"] = _number(arguments, "x", required=True)
            event["y"] = _number(arguments, "y", required=True)
            button = _string(arguments, "button", default="left", max_len=10)
            if button not in {"left", "right", "middle"}:
                raise ToolCallError("'button' must be left, right, or middle.")
            event["button"] = button
            preview = f"Click at ({int(event['x'])}, {int(event['y'])}) in the computer for {bot_id}"
        elif kind == "keypress":
            event["key"] = _string(arguments, "key", required=True, max_len=40)
            preview = f"Press {event['key']} in the computer for {bot_id}"
        elif kind == "type":
            event["text"] = _string(arguments, "text", required=True, max_len=2000)
            preview = f"Type {len(event['text'])} characters in the computer for {bot_id}"
        else:
            event["deltaX"] = _number(arguments, "deltaX")
            event["deltaY"] = _number(arguments, "deltaY")
            preview = f"Scroll by ({int(event['deltaX'])}, {int(event['deltaY'])}) in the computer for {bot_id}"
        return _computer_invocation("send_input", bot_id, {"event": event}, preview)

    if name in {"github_list_issues", "github_create_issue"}:
        owner = _string(arguments, "owner", required=True, max_len=100)
        repo = _string(arguments, "repo", required=True, max_len=100)
        if not _SAFE_REPOSITORY_PART.fullmatch(owner) or not _SAFE_REPOSITORY_PART.fullmatch(repo):
            raise ToolCallError("Repository owner and name may contain only letters, numbers, dots, dashes, and underscores.")
        if name == "github_list_issues":
            state = _string(arguments, "state", default="open", max_len=10).lower()
            if state not in {"open", "closed", "all"}:
                raise ToolCallError("'state' must be open, closed, or all.")
            return ActionInvocation(
                name="connector.github_list_issues",
                arguments={"owner": owner, "repo": repo, "state": state, "per_page": 10},
                target={"connector": "github", "repository": f"{owner}/{repo}"},
                preview=f"List {state} GitHub issues in {owner}/{repo}",
            )
        title = _string(arguments, "title", required=True, max_len=256).strip()
        if not title:
            raise ToolCallError("'title' is required.")
        body = _string(arguments, "body", default="", max_len=10000)
        return ActionInvocation(
            name="connector.github_create_issue",
            arguments={"owner": owner, "repo": repo, "title": title, "body": body},
            target={"connector": "github", "repository": f"{owner}/{repo}"},
            preview=f"Create a GitHub issue in {owner}/{repo}: {title}",
            display_arguments={"owner": owner, "repo": repo, "title": title, "body_bytes": len(body.encode("utf-8"))},
        )

    raise ToolCallError(f"Unknown tool: {name}")
