"""OpenRouter provider for SHADE-Arena (NEW, sprint addition).

Reuses TogetherLLM's OpenAI-compatible request/response handling and only
changes the endpoint, the key, and adds reasoning capture:

* key:  OPENROUTER_API_KEY
* url:  https://openrouter.ai/api/v1
* for models that expose reasoning (Qwen3, DeepSeek, Gemini 2.5) the request
  asks OpenRouter for the reasoning stream and the returned ``message.reasoning``
  is appended to OPENROUTER_REASONING_LOG (a JSONL path, if set).  Anthropic
  models are deliberately NOT run with extended thinking here, because the
  SHADE-Arena pipeline does not echo thinking blocks back on tool-use turns.
"""
import json
import os
import time

from openai import AsyncOpenAI

from .base import LLMFactory
from .together_llm import TogetherLLM

REASONING_MODEL_PREFIXES = ("qwen/qwen3", "deepseek/", "google/gemini-2.5")
RECENT_REASONING: list = []  # in-process queue read by the multi-agent harness


def pop_reasoning():
    """Return and clear the reasoning records captured since the last call."""
    out = list(RECENT_REASONING)
    RECENT_REASONING.clear()
    return out



def _wants_reasoning(model: str) -> bool:
    return any(model.startswith(p) for p in REASONING_MODEL_PREFIXES)


def _wrap_create(client: AsyncOpenAI) -> AsyncOpenAI:
    original = client.chat.completions.create
    log_path = os.environ.get("OPENROUTER_REASONING_LOG")

    async def create(*args, **kwargs):
        model = kwargs.get("model", "")
        if _wants_reasoning(model):
            extra = dict(kwargs.get("extra_body") or {})
            extra["reasoning"] = {"enabled": True}
            kwargs["extra_body"] = extra
        response = await original(*args, **kwargs)
        if log_path and getattr(response, "choices", None):
            msg = response.choices[0].message
            reasoning = getattr(msg, "reasoning", None)
            record = {
                "ts": time.time(),
                "model": model,
                "reasoning": reasoning,
                "content": msg.content,
                "tool_calls": [
                    {"name": tc.function.name, "arguments": tc.function.arguments}
                    for tc in (msg.tool_calls or [])
                ],
                "usage": getattr(response, "usage", None) and response.usage.model_dump(),
            }
            with open(log_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        if getattr(response, "choices", None):
            m = response.choices[0].message
            RECENT_REASONING.append({"model": model, "reasoning": getattr(m, "reasoning", None),
                                     "content": m.content})
        return response

    client.chat.completions.create = create  # type: ignore[method-assign]
    return client


class OpenRouterLLM(TogetherLLM):
    @property
    def provider(self) -> str:
        return "openrouter"

    @classmethod
    def create_client(cls) -> AsyncOpenAI:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required")
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={"X-Title": "collusion-sprint-phase1"},
            timeout=180.0,
            max_retries=3,
        )
        return _wrap_create(client)


LLMFactory.register_provider("openrouter", OpenRouterLLM)
