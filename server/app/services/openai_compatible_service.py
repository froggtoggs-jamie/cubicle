"""Chat streaming and model listing against any OpenAI-compatible server.

Works with OpenRouter, Ollama (`/v1`), LM Studio, llama.cpp's server, vLLM and
the OpenAI API itself. Only the `/chat/completions` and `/models` routes are
used, and the request body sticks to the widely implemented subset.
"""

import json
import time
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

import httpx

from app.schemas.contracts import ModelInfo
from app.services.llm_config import LLMConfig

# Local servers can take a long time before the first token while a model
# loads, so the read timeout is generous. The connect timeout stays short so
# a wrong base URL fails fast.
DEFAULT_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
MODELS_TIMEOUT = httpx.Timeout(30.0, connect=15.0)
MODEL_CACHE_TTL_SECONDS = 60.0

ClientFactory = Callable[[httpx.Timeout], httpx.AsyncClient]


def _default_client_factory(timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


def build_headers(config: LLMConfig) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    if config.is_openrouter:
        # Optional attribution headers OpenRouter uses for its rankings page.
        headers["HTTP-Referer"] = "https://github.com/Anil-matcha/open-grok-bot"
        headers["X-Title"] = "Open Grok Bot"
    return headers


def build_messages(messages: List[Dict[str, Any]], system_prompt: str = "") -> List[Dict[str, Any]]:
    """Convert the stored transcript into an OpenAI `messages` array.

    User turns with an attached image become multimodal content parts. Data
    URLs and hosted URLs are both accepted by OpenAI-style vision endpoints.
    """
    result: List[Dict[str, Any]] = []
    if system_prompt and system_prompt.strip():
        result.append({"role": "system", "content": system_prompt.strip()})

    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = str(message.get("content") or "")
        image_url = message.get("image_url") if role == "user" else None
        if image_url:
            parts: List[Dict[str, Any]] = []
            if text.strip():
                parts.append({"type": "text", "text": text})
            parts.append({"type": "image_url", "image_url": {"url": image_url}})
            result.append({"role": role, "content": parts})
        elif text.strip():
            result.append({"role": role, "content": text})
    return result


def build_request_body(
    model: str,
    messages: List[Dict[str, Any]],
    system_prompt: str,
    config: LLMConfig,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": model,
        "messages": build_messages(messages, system_prompt),
        "stream": True,
    }
    if config.reasoning_effort:
        body["reasoning_effort"] = config.reasoning_effort
    return body


def _error_detail(status_code: int, raw_body: bytes, url: str) -> str:
    text = raw_body.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            text = str(error.get("message") or error)
        elif error:
            text = str(error)
    except (json.JSONDecodeError, AttributeError):
        pass
    return f"Error: {url} returned HTTP {status_code}: {text[:1000]}"


def _display_provider(model_id: str, raw: Dict[str, Any], config: LLMConfig) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    owned_by = str(raw.get("owned_by") or "").strip()
    if owned_by and owned_by.lower() not in {"organization-owner", "library", "openai", "user"}:
        return owned_by
    host = httpx.URL(config.base_url).host or "local"
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return "Local"
    return host


def model_info_from_remote(raw: Dict[str, Any], config: LLMConfig) -> Optional[ModelInfo]:
    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        return None

    supported = raw.get("supported_parameters")
    supports_reasoning: Optional[bool] = None
    if isinstance(supported, list):
        supports_reasoning = "reasoning" in supported or "reasoning_effort" in supported

    supports_vision: Optional[bool] = None
    architecture = raw.get("architecture")
    if isinstance(architecture, dict):
        modalities = architecture.get("input_modalities")
        if isinstance(modalities, list):
            supports_vision = "image" in modalities
        elif isinstance(architecture.get("modality"), str):
            supports_vision = "image" in architecture["modality"].split("->")[0]

    context_length = raw.get("context_length")
    if not isinstance(context_length, int):
        meta = raw.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("n_ctx_train"), int):
            context_length = meta["n_ctx_train"]
        else:
            context_length = None

    description = str(raw.get("description") or "").strip()
    if len(description) > 240:
        description = description[:237].rstrip() + "..."

    return ModelInfo(
        id=model_id,
        name=str(raw.get("name") or model_id),
        provider=_display_provider(model_id, raw, config),
        description=description,
        recommended=model_id == config.default_model,
        supports_reasoning=supports_reasoning,
        supports_vision=supports_vision,
        context_length=context_length,
    )


class OpenAICompatibleService:
    def __init__(self, client_factory: Optional[ClientFactory] = None):
        self._client_factory = client_factory or _default_client_factory
        self._model_cache: Dict[str, Any] = {}

    async def stream_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        config: LLMConfig,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield `content.delta` / `reasoning.delta` events, then `turn.completed`.

        `reasoning.delta` carries the model's thinking (`reasoning_content`
        from llama.cpp-style servers, `reasoning` from OpenRouter). Errors are
        reported the same way the rest of the app expects: the error text
        arrives as a content delta and the turn completes with ok=False.
        """
        target_model = (model or config.default_model).strip()
        url = f"{config.base_url}/chat/completions"
        body = build_request_body(target_model, messages, system_prompt, config)
        headers = build_headers(config)

        try:
            async with self._client_factory(DEFAULT_TIMEOUT) as client:
                async with client.stream("POST", url, json=body, headers=headers) as response:
                    if response.status_code != 200:
                        raw = await response.aread()
                        yield {"type": "content.delta", "delta": _error_detail(response.status_code, raw, url)}
                        yield {"type": "turn.completed", "ok": False}
                        return

                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type:
                        # The server ignored `stream: true` and answered in one piece.
                        raw = await response.aread()
                        async for event in self._emit_non_streaming(raw, url):
                            yield event
                        return

                    produced_output = False
                    async for line in response.aiter_lines():
                        line = line.strip()
                        # OpenRouter emits ": OPENROUTER PROCESSING" keep-alive comments.
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue

                        error = chunk.get("error")
                        if error:
                            message = error.get("message") if isinstance(error, dict) else str(error)
                            yield {"type": "content.delta", "delta": f"Error: {message}"}
                            yield {"type": "turn.completed", "ok": False}
                            return

                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if isinstance(reasoning, str) and reasoning:
                                yield {"type": "reasoning.delta", "delta": reasoning}
                            content = delta.get("content")
                            if content:
                                produced_output = True
                                yield {"type": "content.delta", "delta": content}

                    if not produced_output:
                        yield {
                            "type": "content.delta",
                            "delta": "Error: the model returned an empty response.",
                        }
                        yield {"type": "turn.completed", "ok": False}
                        return
                    yield {"type": "turn.completed", "ok": True}
        except httpx.HTTPError as exc:
            yield {"type": "content.delta", "delta": f"Error: could not reach {url}: {exc}"}
            yield {"type": "turn.completed", "ok": False}

    async def _emit_non_streaming(self, raw: bytes, url: str) -> AsyncGenerator[Dict[str, Any], None]:
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            yield {"type": "content.delta", "delta": f"Error: {url} returned a non-JSON response."}
            yield {"type": "turn.completed", "ok": False}
            return

        error = data.get("error") if isinstance(data, dict) else None
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            yield {"type": "content.delta", "delta": f"Error: {message}"}
            yield {"type": "turn.completed", "ok": False}
            return

        text = ""
        reasoning = ""
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            raw_reasoning = message.get("reasoning_content") or message.get("reasoning")
            if isinstance(raw_reasoning, str):
                reasoning = raw_reasoning
            content = message.get("content")
            if isinstance(content, list):
                text = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            elif content:
                text = str(content)
        if not text:
            yield {"type": "content.delta", "delta": "Error: the model returned an empty response."}
            yield {"type": "turn.completed", "ok": False}
            return
        if reasoning:
            yield {"type": "reasoning.delta", "delta": reasoning}
        yield {"type": "content.delta", "delta": text}
        yield {"type": "turn.completed", "ok": True}

    async def list_models(self, config: LLMConfig, refresh: bool = False) -> List[ModelInfo]:
        """Fetch `/models` and normalise it. Raises httpx.HTTPError or ValueError."""
        cache_key = f"{config.base_url}|{config.api_key[-6:]}|{config.default_model}"
        cached = self._model_cache.get(cache_key)
        if cached and not refresh and time.monotonic() - cached["at"] < MODEL_CACHE_TTL_SECONDS:
            return cached["models"]

        url = f"{config.base_url}/models"
        async with self._client_factory(MODELS_TIMEOUT) as client:
            response = await client.get(url, headers=build_headers(config))
        if response.status_code != 200:
            raise ValueError(_error_detail(response.status_code, response.content, url))

        payload = response.json()
        raw_models = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw_models, list):
            raise ValueError(f"Error: {url} did not return a model list.")

        models: List[ModelInfo] = []
        seen = set()
        for raw in raw_models:
            if not isinstance(raw, dict):
                continue
            info = model_info_from_remote(raw, config)
            if info and info.id not in seen:
                seen.add(info.id)
                models.append(info)
        models.sort(key=lambda m: (m.provider.lower(), m.name.lower()))

        self._model_cache[cache_key] = {"at": time.monotonic(), "models": models}
        return models


openai_compatible_service = OpenAICompatibleService()
