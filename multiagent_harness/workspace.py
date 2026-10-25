"""Shared workspace: payment ledger (propose -> review -> execute) and notes.
The ledger is the artifact the team policy refers to ("approved items only")."""
from __future__ import annotations

from .events import EventLog

PAY_STATUSES = ("proposed", "approved", "rejected", "executed")
METHODS = ("bank", "venmo", "zelle")


class Workspace:
    def __init__(self, log: EventLog):
        self.log, self.payments, self.notes = log, {}, []

    def propose(self, agent, payee, method, destination, amount, justification, role=None) -> str:
        method = str(method).strip().lower()
        if method not in METHODS:
            return f"Error: method must be one of {METHODS}."
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return "Error: amount must be a number."
        pid = f"PAY-{len(self.payments) + 1:03d}"
        self.payments[pid] = {"id": pid, "payee": payee, "method": method, "destination": str(destination).strip(),
                              "amount": amount, "justification": justification, "proposed_by": agent,
                              "status": "proposed", "reviewer": None, "review_note": None, "executed_by": None,
                              "reference": None, "round": self.log.round}
        self.log.emit("workspace", agent=agent, role=role, channel="workspace", visibility="public", sender=agent,
                      task_id=pid, content=f"propose {payee} {method} {destination} {amount}",
                      args={"payee": payee, "method": method, "destination": destination, "amount": amount,
                            "justification": justification}, meta={"status": "proposed"})
        return f"Recorded {pid}: {amount:.2f} USD to {payee} via {method} ({destination}), status proposed."

    def review(self, agent, payment_id, decision, note, role=None) -> str:
        rec = self.payments.get(payment_id)
        if rec is None:
            return f"Error: no ledger item {payment_id}."
        decision = str(decision).strip().lower()
        if decision not in ("approved", "rejected"):
            return "Error: decision must be approved or rejected."
        if rec["status"] == "executed":
            return f"Error: {payment_id} was already executed."
        rec.update(status=decision, reviewer=agent, review_note=note)
        self.log.emit("workspace", agent=agent, role=role, channel="workspace", visibility="public", sender=agent,
                      task_id=payment_id, content=f"{decision}: {note}", args={"decision": decision, "note": note},
                      meta={"status": decision})
        return f"{payment_id} is now {decision}."

    def mark_executed(self, agent, payment_id, reference, role=None) -> str:
        rec = self.payments.get(payment_id)
        if rec is None:
            return f"Error: no ledger item {payment_id}."
        prior = rec["status"]
        rec.update(status="executed", executed_by=agent, reference=reference)
        self.log.emit("workspace", agent=agent, role=role, channel="workspace", visibility="public", sender=agent,
                      task_id=payment_id, content=f"executed ({reference}); prior status {prior}",
                      meta={"status": "executed", "prior_status": prior})
        return f"{payment_id} marked executed (was {prior})."

    def add_note(self, agent, title, body, role=None) -> str:
        self.notes.append({"id": len(self.notes), "by": agent, "title": title, "body": body, "round": self.log.round})
        self.log.emit("workspace", agent=agent, role=role, channel="workspace", visibility="public", sender=agent,
                      content=f"note '{title}': {body}", meta={"note": True})
        return f"Note '{title}' added to the shared workspace."

    def render(self) -> str:
        lines = ["SHARED WORKSPACE", "Payment ledger:"] + (["  (empty)"] if not self.payments else [])
        for p in self.payments.values():
            lines.append(f"  {p['id']} | {p['payee']} | {p['method']} -> {p['destination']} | {p['amount']:.2f} USD | "
                         f"{p['status']} | proposed by {p['proposed_by']} | reviewer {p['reviewer']} "
                         f"({p['review_note'] or ''}) | justification: {p['justification']}")
        lines.append("Notes:" + ("" if self.notes else " (none)"))
        for n in self.notes:
            lines.append(f"  [{n['id']}] {n['title']} (by {n['by']}): {n['body']}")
        return "\n".join(lines)

    def status_counts(self) -> dict[str, int]:
        out = {s: 0 for s in PAY_STATUSES}
        for p in self.payments.values():
            out[p["status"]] += 1
        return out
