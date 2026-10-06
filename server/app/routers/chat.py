import json
import uuid
import asyncio
from datetime import datetime
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.config import settings
from app.schemas.contracts import TurnRequest, Message
from app.services.storage_service import storage_service
from app.services import llm_service
from app.services.action_gateway import (
    ActionGatewayError,
    ActionPolicyError,
    action_gateway,
)
from app.services.composio_service import composio_service
from app.services.connector_actions import ConnectorCommandError, parse_connector_command
from app.services.llm_tools import (
    ToolCallError,
    available_tools,
    build_invocation,
    describe_tools,
    openai_tool_definitions,
    parse_arguments,
)
from app.services.openai_compatible_service import openai_compatible_service
from app.services.workspace_service import (
    WorkspaceToolError,
    parse_workspace_command,
)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# Tool results are fed back to the model as text. Keep them bounded so one
# large file does not blow the context window.
MAX_TOOL_RESULT_CHARS = 20_000


def _sse(payload: Dict[str, Any]) -> Dict[str, str]:
    return {"event": "message", "data": json.dumps(payload)}


def _compact_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Drop binary payloads (screenshots) and trim long text for SSE and the model."""
    compact: Dict[str, Any] = {}
    for key, value in result.items():
        if key == "data" and isinstance(value, str) and len(value) > 2000:
            compact[key] = f"<{len(value)} bytes of base64 image omitted>"
        elif isinstance(value, str) and len(value) > MAX_TOOL_RESULT_CHARS:
            compact[key] = value[:MAX_TOOL_RESULT_CHARS] + f"\n...[truncated {len(value) - MAX_TOOL_RESULT_CHARS} characters]"
        else:
            compact[key] = value
    return compact


def _tool_result_text(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "...[truncated]"
    return text


def _screenshot_message(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn a screenshot result into an image message the model can look at."""
    data = result.get("data")
    if not isinstance(data, str) or not data:
        return None
    image_format = str(result.get("format") or "jpeg").lower()
    frame = result.get("frame_id") or "latest"
    url = data if data.startswith("data:") else f"data:image/{image_format};base64,{data}"
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"[Screenshot from computer_screenshot, frame {frame}. This image is the tool result, not a new user message.]"},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


def _model_accepts_images(model: str, llm_config) -> bool:
    mode = (settings.LLM_SCREENSHOTS_TO_MODEL or "auto").lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    info = openai_compatible_service.cached_model_info(llm_config, model)
    return not (info is not None and info.supports_vision is False)


@router.get("/history/{thread_id}", response_model=List[Message])
async def get_history(thread_id: str):
    return storage_service.get_messages(thread_id=thread_id)


@router.post("/send")
async def send_message(req: TurnRequest):
    # Store user message
    user_msg = {
        "id": f"msg-{uuid.uuid4().hex[:6]}",
        "thread_id": req.thread_id,
        "bot_id": req.bot_id,
        "sender": "user",
        "text": req.user_text,
        "image_url": req.image_url,
        "created_at": datetime.now().isoformat(),
        "model": req.model or llm_service.current_llm_config().default_model,
        "item_type": "user_text"
    }

    storage_service.add_message(user_msg)
    return {"status": "ok", "message": user_msg}


async def _perform_action(
    thread_id: str,
    action_call: Any,
    call_name: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one governed action through the gateway, yielding SSE events.

    The last item yielded is {"_outcome": {...}} describing what happened, so
    the caller can tell the model (or the system prompt) about it.
    """
    try:
        action_request, approval = action_gateway.open(thread_id, thread_id, action_call)
    except ActionPolicyError as exc:
        yield _sse({
            "type": "tool.failed",
            "tool": getattr(action_call, "name", "unknown"),
            "callName": call_name,
            "requestId": exc.request_id,
            "error": str(exc),
        })
        yield {"_outcome": {"status": "rejected", "error": f"Action rejected by policy: {exc}", "request_id": exc.request_id}}
        return

    action_name = f"{action_request.tool}.{action_request.action}"
    base = {"tool": action_name, "callName": call_name, "requestId": action_request.request_id}

    if approval:
        yield _sse({
            "type": "request.opened",
            "requestType": "permission",
            "requestId": action_request.request_id,
            "tool": approval["tool"],
            "callName": call_name,
            "summary": approval["summary"],
            "arguments": approval["arguments"],
            "action": action_request.model_dump(),
        })

    decision = await action_gateway.wait_for_decision(action_request)
    if decision == "deny":
        yield _sse({"type": "tool.denied", **base})
        yield {"_outcome": {"status": "denied", "error": f"The user denied this action: {action_request.preview}", "request": action_request}}
        return
    if decision != "allow":
        yield _sse({"type": "tool.expired", **base})
        yield {"_outcome": {"status": "expired", "error": f"The approval request expired before the user answered: {action_request.preview}", "request": action_request}}
        return

    yield _sse({"type": "tool.started", **base, "action": action_request.model_dump()})
    try:
        action_result = await action_gateway.execute(action_request)
    except ActionGatewayError as exc:
        yield _sse({"type": "tool.failed", **base, "error": str(exc)})
        yield {"_outcome": {"status": "failed", "error": f"Action could not execute ({action_name}): {exc}", "request": action_request}}
        return

    if action_result.status == "completed":
        result = action_result.result or {}
        yield _sse({"type": "tool.completed", **base, "result": _compact_result(result)})
        yield {"_outcome": {"status": "completed", "result": result, "request": action_request}}
        return

    error = action_result.error or "The action failed."
    yield _sse({"type": "tool.failed", **base, "error": error})
    yield {"_outcome": {"status": "failed", "error": f"Action failed ({action_name}): {error}", "request": action_request}}


@router.get("/stream/{thread_id}")
async def stream_turn(thread_id: str, model: Optional[str] = Query(None)):
    """
    SSE stream endpoint broadcasting real-time tokens & tool events for a given thread.
    """
    history = storage_service.get_messages(thread_id=thread_id)
    bots = storage_service.get_bots()
    current_bot = next((b for b in bots if b["id"] == thread_id), None)

    raw_prompt = current_bot["system_prompt"] if current_bot else "You are a helpful AI assistant."
    current_time_str = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    system_prompt = f"Current Date & Time: {current_time_str}.\n\n{raw_prompt}"
    llm_config = llm_service.current_llm_config()
    selected_model = (
        model
        or (current_bot.get("model") if current_bot else None)
        or llm_config.default_model
    )

    formatted_history = []
    for m in history:
        if m["sender"] in ["user", "bot"]:
            formatted_history.append({
                "role": "user" if m["sender"] == "user" else "assistant",
                "content": m.get("text", ""),
                "image_url": m.get("image_url")
            })

    # Model tool calling is only possible on OpenAI-compatible servers. MUAPI
    # takes a single prompt, so there the slash commands remain the only tools.
    tools_enabled = settings.LLM_TOOLS_ENABLED and llm_config.provider != "muapi"
    tool_specs = (
        available_tools(
            computer=settings.COMPUTER_PROVIDER == "docker",
            github=bool(composio_service.get_api_key()),
        )
        if tools_enabled
        else []
    )
    tool_definitions = openai_tool_definitions(tool_specs) if tool_specs else None
    tools_prompt = describe_tools(tool_specs)

    async def event_generator():
        bot_msg_id = f"msg-{uuid.uuid4().hex[:6]}"
        text_pieces: List[str] = []
        # Thinking is shown to the user and kept with the message for display,
        # but it is never part of `text`, so it is not replayed to the model.
        accumulated_reasoning = ""
        tool_records: List[Dict[str, Any]] = []
        tool_context = ""

        # Emit turn started
        yield _sse({"type": "turn.started", "botMsgId": bot_msg_id, "model": selected_model})

        # --- explicit slash commands typed by the user ------------------------
        last_user_text = formatted_history[-1]["content"] if formatted_history else ""
        try:
            action_call = parse_workspace_command(last_user_text)
            if action_call is None:
                action_call = parse_connector_command(last_user_text)
        except (WorkspaceToolError, ConnectorCommandError) as exc:
            action_call = None
            command_tool = "connector" if last_user_text.lower().startswith("/connector") else "workspace"
            tool_context = f"A {command_tool} request was rejected before execution: {exc}"
            yield _sse({"type": "tool.failed", "tool": command_tool, "error": str(exc)})

        if action_call:
            outcome: Dict[str, Any] = {}
            async for item in _perform_action(thread_id, action_call):
                if "_outcome" in item:
                    outcome = item["_outcome"]
                else:
                    yield item
            if outcome.get("status") == "completed":
                tool_context = f"Action result ({action_call.name}): {_tool_result_text(_compact_result(outcome['result']))}"
            else:
                tool_context = outcome.get("error") or "The action did not complete."

        # Use a new name here. Assigning to `system_prompt` inside this nested
        # generator would make it local to the whole function and raise
        # UnboundLocalError on the first read above.
        turn_system_prompt = system_prompt
        if tools_prompt:
            turn_system_prompt = f"{turn_system_prompt}\n\n{tools_prompt}"
        if tool_context:
            turn_system_prompt = f"{turn_system_prompt}\n\n{tool_context}"

        # --- model turn, looping while it calls tools ------------------------
        messages: List[Dict[str, Any]] = list(formatted_history)
        max_rounds = max(1, settings.LLM_MAX_TOOL_ROUNDS)
        rounds = 0
        # Once the tool budget is spent the model gets one last call without
        # tools so the user always receives an answer rather than a cut-off.
        final_answer_only = False
        try:
            while True:
                rounds += 1
                round_text = ""
                pending_calls: List[Dict[str, Any]] = []
                round_ok = True

                round_prompt = turn_system_prompt
                round_tools = tool_definitions
                if final_answer_only:
                    round_tools = None
                    round_prompt = (
                        f"{turn_system_prompt}\n\nThe tool budget for this turn is used up. "
                        "Do not call tools. Reply to the user now with what you found, and say what is still unverified."
                    )

                async for event in llm_service.stream_chat_completion(
                    model=selected_model,
                    messages=messages,
                    system_prompt=round_prompt,
                    config=llm_config,
                    tools=round_tools,
                ):
                    if event["type"] == "content.delta":
                        round_text += event["delta"]
                        yield _sse({"type": "content.delta", "botMsgId": bot_msg_id, "delta": event["delta"]})
                    elif event["type"] == "reasoning.delta":
                        accumulated_reasoning += event["delta"]
                        yield _sse({"type": "reasoning.delta", "botMsgId": bot_msg_id, "delta": event["delta"]})
                    elif event["type"] == "tool_calls":
                        pending_calls = list(event.get("calls") or [])
                    elif event["type"] == "turn.completed":
                        round_ok = bool(event.get("ok", True))

                if round_text.strip():
                    text_pieces.append(round_text)

                if final_answer_only:
                    if not round_text.strip():
                        note = f"[Stopped after {max_rounds} tool rounds without a final answer.]"
                        text_pieces.append(note)
                        yield _sse({"type": "content.delta", "botMsgId": bot_msg_id, "delta": note})
                    break
                if not pending_calls or not round_ok:
                    break
                if round_text.strip():
                    # Visually separate a "let me check" preamble from the answer.
                    yield _sse({"type": "content.delta", "botMsgId": bot_msg_id, "delta": "\n\n"})

                messages.append({
                    "role": "assistant",
                    "content": round_text,
                    "tool_calls": [
                        {"id": call["id"], "name": call["name"], "arguments": call.get("arguments") or "{}"}
                        for call in pending_calls
                    ],
                })

                if rounds >= max_rounds:
                    # Budget spent: answer the pending calls without running
                    # them, then ask for a text-only reply.
                    for call in pending_calls:
                        tool_records.append({"id": call["id"], "name": call["name"], "status": "skipped",
                                             "error": "Tool budget for this turn was used up."})
                        yield _sse({"type": "tool.failed", "tool": call["name"], "callName": call["name"],
                                    "error": "Not run: the tool budget for this turn was used up."})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": _tool_result_text({"status": "skipped", "error": "Tool budget used up; answer the user now."}),
                        })
                    final_answer_only = True
                    continue

                image_messages: List[Dict[str, Any]] = []
                for call in pending_calls:
                    call_id = call["id"]
                    call_name = call["name"]
                    record: Dict[str, Any] = {"id": call_id, "name": call_name, "status": "running"}
                    tool_records.append(record)

                    try:
                        arguments = parse_arguments(call.get("arguments"))
                        invocation = build_invocation(call_name, arguments, thread_id)
                    except ToolCallError as exc:
                        record.update({"status": "failed", "error": str(exc)})
                        yield _sse({"type": "tool.failed", "tool": call_name, "callName": call_name, "error": str(exc)})
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_result_text({"error": str(exc)})})
                        continue

                    record["summary"] = getattr(invocation, "summary", None) or getattr(invocation, "preview", call_name)
                    outcome = {}
                    async for item in _perform_action(thread_id, invocation, call_name=call_name):
                        if "_outcome" in item:
                            outcome = item["_outcome"]
                        else:
                            yield item

                    status = outcome.get("status", "failed")
                    record["status"] = status
                    request_obj = outcome.get("request")
                    if request_obj is not None:
                        record["requestId"] = request_obj.request_id
                        record["arguments"] = request_obj.arguments
                    if status == "completed":
                        result = outcome.get("result") or {}
                        payload = _compact_result(result)
                        if call_name == "computer_request_takeover":
                            yield _sse({
                                "type": "computer.takeover_requested",
                                "botMsgId": bot_msg_id,
                                "botId": thread_id,
                                "reason": str(arguments.get("reason") or ""),
                            })
                        screenshot = _screenshot_message(result)
                        if screenshot is not None:
                            if _model_accepts_images(selected_model, llm_config):
                                image_messages.append(screenshot)
                                payload["image"] = "attached as the next message"
                            else:
                                payload["image"] = "omitted: the selected model does not accept images"
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_result_text(payload)})
                    else:
                        record["error"] = outcome.get("error")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": _tool_result_text({"status": status, "error": outcome.get("error")}),
                        })
                messages.extend(image_messages)
        except asyncio.CancelledError:
            raise

        raw_payload: Dict[str, Any] = {}
        if accumulated_reasoning:
            raw_payload["reasoning"] = accumulated_reasoning
        if tool_records:
            raw_payload["tool_calls"] = tool_records

        bot_msg = {
            "id": bot_msg_id,
            "thread_id": thread_id,
            "bot_id": thread_id,
            "sender": "bot",
            "text": "\n\n".join(piece.strip("\n") for piece in text_pieces if piece.strip()),
            "created_at": datetime.now().isoformat(),
            "model": selected_model,
            "item_type": "assistant_text",
            "raw_payload": raw_payload or None,
        }
        storage_service.add_message(bot_msg)
        yield _sse({"type": "turn.completed", "ok": True, "botMsgId": bot_msg_id})

    return EventSourceResponse(event_generator())
