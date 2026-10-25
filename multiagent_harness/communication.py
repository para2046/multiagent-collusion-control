"""Public and private channels with per-agent inboxes; delivery happens at the
recipient's next turn so replay order is unambiguous."""
from __future__ import annotations

from .events import EventLog


class MessageBus:
    def __init__(self, agents: list[str], log: EventLog):
        self.agents = list(agents)
        self.log = log
        self.inbox: dict[str, list[dict]] = {a: [] for a in agents}
        self.messages: list[dict] = []

    def post(self, sender: str, content: str, *, channel: str = "public", recipients: list[str] | None = None,
             task_id: str | None = None, role: str | None = None) -> dict:
        if channel == "public":
            recipients, visibility = [a for a in self.agents if a != sender], "public"
        elif channel in ("private", "delegation"):
            if not recipients:
                raise ValueError("private messages need a recipient")
            unknown = [r for r in recipients if r not in self.agents]
            if unknown:
                raise ValueError(f"unknown recipient(s) {unknown}; agents are {self.agents}")
            visibility = "private"
        else:
            raise ValueError(f"unknown channel {channel}")
        msg = {"id": len(self.messages), "sender": sender, "recipients": list(recipients), "channel": channel,
               "content": content, "task_id": task_id, "round": self.log.round}
        self.messages.append(msg)
        for r in recipients:
            self.inbox[r].append(msg)
        self.log.emit("message", agent=sender, role=role, channel=channel, visibility=visibility, sender=sender,
                      recipients=list(recipients), task_id=task_id, content=content)
        return msg

    def drain(self, agent: str) -> list[dict]:
        msgs, self.inbox[agent] = self.inbox[agent], []
        return msgs

    def by_channel(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.messages:
            out[m["channel"]] = out.get(m["channel"], 0) + 1
        return out
