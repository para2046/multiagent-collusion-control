"""Role definitions and tool allow-lists, enforced at call time by the orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Role:
    name: str
    title: str
    description: str
    env_tools: frozenset
    harness_tools: frozenset
    extra: dict = field(default_factory=dict)

    def allows(self, tool: str) -> bool:
        return tool in self.env_tools or tool in self.harness_tools


class PermissionPolicy:
    def __init__(self, roles: dict[str, Role]):
        self.roles, self.denials = roles, []

    def check(self, agent: str, role: Role, tool: str) -> str | None:
        if role.allows(tool):
            return None
        self.denials.append({"agent": agent, "tool": tool})
        return (f"PermissionError: tool '{tool}' is not available to the {role.title} ({agent}). "
                f"Ask the responsible teammate through the team channels or the delegation tool.")
