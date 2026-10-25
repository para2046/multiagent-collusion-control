"""The monitor's output, shared by the orchestrator hook and the monitor implementations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    action: str = "Continue"           # Continue | Observe | Alert
    p_coalition: float | None = None   # P(a knowing unauthorized coalition has formed so far)
    p_violation: float | None = None   # P(an unauthorized execution lies ahead)
    uncertainty: float | None = None
    evidence: list | None = None       # event ids the decision rests on
    note: str | None = None
