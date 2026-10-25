"""Unified chronological event stream, appended to events.jsonl as it happens.
Visibility: public (team message / workspace), private (P2P or delegation),
action (tool call, tool result, agent text), reasoning (provider trace or text
scratchpad; provenance says which), hidden (seed instruction, incentives, labels)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

VISIBILITIES = ("public", "private", "action", "reasoning", "hidden")
KINDS = ("episode_start", "hidden_instruction", "round_start", "turn_start", "inbox", "assistant_text",
         "reasoning", "tool_proposed", "monitor_decision", "permission_denied", "tool_call", "tool_result",
         "message", "delegation", "workspace", "escalation", "turn_end", "finish", "episode_end")


@dataclass
class Event:
    id: int
    ts: float
    episode_id: str
    round: int | None
    step: int | None
    agent: str | None
    role: str | None
    kind: str
    channel: str | None = None
    visibility: str = "action"
    sender: str | None = None
    recipients: list[str] | None = None
    task_id: str | None = None
    content: str | None = None
    tool_name: str | None = None
    args: dict | None = None
    result: str | None = None
    error: str | None = None
    provenance: str | None = None
    meta: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class EventLog:
    def __init__(self, episode_id: str, path: str | Path | None = None):
        self.episode_id = episode_id
        self.events: list[Event] = []
        self.path = Path(path) if path else None
        self._fh = open(self.path, "a", encoding="utf-8") if self.path else None
        self.round: int | None = None
        self.step: int | None = None

    def emit(self, kind: str, *, agent: str | None = None, role: str | None = None, **kw) -> Event:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind}")
        vis = kw.pop("visibility", "action")
        if vis not in VISIBILITIES:
            raise ValueError(f"unknown visibility {vis}")
        ev = Event(id=len(self.events), ts=time.time(), episode_id=self.episode_id,
                   round=kw.pop("round", self.round), step=kw.pop("step", self.step),
                   agent=agent, role=role, kind=kind, visibility=vis, meta=kw.pop("meta", None) or {}, **kw)
        self.events.append(ev)
        if self._fh:
            self._fh.write(ev.to_json() + "\n")
            self._fh.flush()
        return ev

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def view(self, visibilities: Iterable[str]) -> list[Event]:
        allowed = set(visibilities)
        return [e for e in self.events if e.visibility in allowed]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out


def read_events(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
