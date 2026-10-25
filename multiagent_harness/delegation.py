"""Explicit delegation: a private message plus a tracked record
(open -> accepted | declined -> done). Declines are the refusal signal."""
from __future__ import annotations

from .communication import MessageBus
from .events import EventLog

STATUSES = ("open", "accepted", "declined", "done")


class DelegationBoard:
    def __init__(self, bus: MessageBus, log: EventLog):
        self.bus, self.log, self.tasks = bus, log, {}

    def delegate(self, sender: str, assignee: str, task_id: str, instructions: str, role: str | None = None) -> str:
        if assignee == sender:
            return "Error: you cannot delegate to yourself."
        if assignee not in self.bus.agents:
            return f"Error: unknown agent {assignee}. Agents: {self.bus.agents}"
        if task_id in self.tasks:
            return f"Error: task_id {task_id} already exists (status {self.tasks[task_id]['status']})."
        self.tasks[task_id] = {"task_id": task_id, "from": sender, "to": assignee, "instructions": instructions,
                               "status": "open", "history": [], "round": self.log.round}
        self.bus.post(sender, f"[DELEGATION {task_id}] {instructions}", channel="delegation", recipients=[assignee],
                      task_id=task_id, role=role)
        self.log.emit("delegation", agent=sender, role=role, channel="delegation", visibility="private", sender=sender,
                      recipients=[assignee], task_id=task_id, content=instructions, meta={"status": "open"})
        return f"Delegated {task_id} to {assignee}. They will see it on their next turn."

    def respond(self, agent: str, task_id: str, decision: str, note: str, role: str | None = None) -> str:
        rec = self.tasks.get(task_id)
        if rec is None:
            return f"Error: no delegation with task_id {task_id}."
        if rec["to"] != agent:
            return f"Error: {task_id} is assigned to {rec['to']}, not to you."
        decision = decision.strip().lower()
        if decision not in ("accepted", "declined", "done"):
            return "Error: decision must be one of accepted, declined, done."
        rec["status"] = decision
        rec["history"].append({"round": self.log.round, "decision": decision, "note": note})
        self.bus.post(agent, f"[DELEGATION {task_id} {decision.upper()}] {note}", channel="delegation",
                      recipients=[rec["from"]], task_id=task_id, role=role)
        self.log.emit("delegation", agent=agent, role=role, channel="delegation", visibility="private", sender=agent,
                      recipients=[rec["from"]], task_id=task_id, content=note, meta={"status": decision})
        return f"Recorded {task_id} as {decision}; {rec['from']} has been notified."

    def open_for(self, agent: str) -> list[dict]:
        return [t for t in self.tasks.values() if t["to"] == agent and t["status"] in ("open", "accepted")]

    def summary(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for t in self.tasks.values():
            out[t["status"]] += 1
        return out
