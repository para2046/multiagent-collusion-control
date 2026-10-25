"""Scripted LLM for offline tests. Each agent gets a queue of steps; a step is a
list of (tool_name, args) tuples, or a plain string for a text-only reply."""
from __future__ import annotations

from .shade_bootstrap import bootstrap

bootstrap()
from llms.base import BaseLLM  # noqa: E402
from utils.functions_runtime import FunctionCall  # noqa: E402
from utils.pipeline_elements import QueryResult  # noqa: E402
from utils.types import ChatAssistantMessage  # noqa: E402


class FakeLLM(BaseLLM):
    def __init__(self, model: str, script: list, temperature: float | None = 0.0):
        super().__init__(model, temperature)
        self.script = list(script)
        self.calls = 0

    @property
    def provider(self) -> str:
        return "fake"

    @classmethod
    def create_client(cls):
        return None

    async def query(self, query, runtime, env=None, messages=(), extra_args=None):
        extra_args = extra_args if extra_args is not None else {}
        self.calls += 1
        step = self.script.pop(0) if self.script else []
        if isinstance(step, str):
            out = ChatAssistantMessage(role="assistant", content=step, tool_calls=None)
        else:
            calls = [FunctionCall(function=n, args=a, id=f"call_{self.calls}_{i}") for i, (n, a) in enumerate(step)]
            out = ChatAssistantMessage(role="assistant", content="", tool_calls=calls)
        return QueryResult(query=query, runtime=runtime, env=env, messages=[*messages, out], extra_args=extra_args)
