"""Build the model's context from the stored transcript.

Phase one of context management: tool results are kept with the message
that produced them (trimmed), and recent bot turns are replayed in the
provider's native shape (assistant tool_calls, tool results, final text)
so the model remembers what it found instead of searching again. Older
turns keep their tool calls but get a one-line stub per result.
"""

import json
from typing import Any, Dict, List, Optional

from app.config import settings

OMITTED_NOTE = "result omitted from context (older turn)"


def trim_result_text(text: str, limit: Optional[int] = None) -> str:
    """Bound one tool result for storage, keeping the start which is usually the useful part."""
    limit = limit or settings.CONTEXT_TOOL_RESULT_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[trimmed {len(text) - limit} characters]"


def cap_message_results(records: List[Dict[str, Any]], limit: Optional[int] = None) -> None:
    """Keep one message's stored results under a total size, dropping the oldest first."""
    limit = limit or settings.CONTEXT_TOOL_RESULTS_MESSAGE_CHARS
    total = sum(len(r.get("result") or "") for r in records)
    for record in records:
        if total <= limit:
            break
        text = record.get("result")
        if not text:
            continue
        total -= len(text)
        record["result"] = None
        record["result_dropped"] = True


def _stub_for(record: Dict[str, Any], note: str) -> str:
    stub: Dict[str, Any] = {"status": record.get("status") or "unknown", "note": note}
    if record.get("summary"):
        stub["summary"] = record["summary"]
    if record.get("error"):
        stub["error"] = record["error"]
    return json.dumps(stub, ensure_ascii=False)


def _tool_message(record: Dict[str, Any], full: bool) -> Dict[str, Any]:
    if record.get("status") == "completed":
        if full and record.get("result"):
            content = record["result"]
        elif record.get("result_dropped") or not record.get("result"):
            content = _stub_for(record, "result not retained")
        else:
            content = _stub_for(record, OMITTED_NOTE)
    else:
        content = _stub_for(record, "the call did not complete")
    return {"role": "tool", "tool_call_id": str(record["id"]), "content": content}


def _call_entries(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries = []
    for record in records:
        arguments = record.get("raw_arguments")
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(record.get("arguments") or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                arguments = "{}"
        entries.append({"id": str(record["id"]), "name": str(record.get("name") or "tool"), "arguments": arguments or "{}"})
    return entries


def build_history_messages(
    history: List[Dict[str, Any]],
    *,
    replay_tools: bool,
    replay_turns: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Transcript -> provider-neutral messages.

    With `replay_tools` (OpenAI-compatible providers), bot turns that used
    tools become assistant(tool_calls) + tool results + assistant(text).
    The most recent `replay_turns` tool-using bot turns carry full results;
    older ones carry stubs. Without it (MUAPI), only the texts are used, as before.
    """
    replay_turns = settings.CONTEXT_REPLAY_TURNS if replay_turns is None else replay_turns
    # Count only replies that used tools, so plain exchanges in between do
    # not push real results out of the window.
    tool_turns = [
        i for i, m in enumerate(history)
        if m.get("sender") == "bot" and any(
            isinstance(r, dict) and r.get("id") for r in ((m.get("raw_payload") or {}).get("tool_calls") or [])
        )
    ]
    full_from = set(tool_turns[-replay_turns:]) if replay_turns > 0 else set()

    messages: List[Dict[str, Any]] = []
    for index, message in enumerate(history):
        sender = message.get("sender")
        if sender == "user":
            messages.append({"role": "user", "content": message.get("text", ""), "image_url": message.get("image_url")})
            continue
        if sender != "bot":
            continue
        text = message.get("text", "") or ""
        records = [
            r for r in ((message.get("raw_payload") or {}).get("tool_calls") or [])
            if isinstance(r, dict) and r.get("id")
        ]
        if not replay_tools or not records:
            messages.append({"role": "assistant", "content": text, "image_url": None})
            continue
        full = index in full_from
        messages.append({"role": "assistant", "content": "", "tool_calls": _call_entries(records)})
        messages.extend(_tool_message(record, full) for record in records)
        if text.strip():
            messages.append({"role": "assistant", "content": text, "image_url": None})
    return messages
