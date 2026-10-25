"""Assemble an Orchestrator for a scenario + pattern + team of LLMs."""
from __future__ import annotations

from multiagent_harness.communication import MessageBus
from multiagent_harness.delegation import DelegationBoard
from multiagent_harness.orchestration import Agent, Orchestrator, new_episode_log
from multiagent_harness.permissions import PermissionPolicy
from multiagent_harness.prompts import build_system_prompt
from multiagent_harness.tools import AgentState
from multiagent_harness.workspace import Workspace


def build_episode(scenario, *, episode_id, pattern, team, llms, out_dir, monitor_hook=None,
                  active=False, max_rounds=12, seed_spec=None, inducement='L0',
                  p2_incentive_roles=None, p2_common_knowledge=False, trigger=False, seed_framing='coercive'):
    """team: {agent_name: role_name}; llms: {agent_name: BaseLLM}."""
    env = scenario.build_environment()
    if trigger and hasattr(scenario, 'inject_trigger'):
        scenario.inject_trigger(env)
    env_tool_names = [t.__name__ for t in env.available_tools]
    roles = scenario.ROLES
    seed_spec = dict(seed_spec or getattr(scenario, "SEED_SPEC_P1", None) or {})
    prompts = scenario.PROMPTS
    active_incentives = frozenset()
    if pattern == "P2" and p2_incentive_roles and getattr(scenario, "P2_INCENTIVE", None):
        import dataclasses as _dc
        txt = scenario.P2_INCENTIVE + (getattr(scenario, "P2_COMMON_SUFFIX", "") if p2_common_knowledge else "")
        inc = dict(prompts.incentives); 
        for r in p2_incentive_roles: inc[r] = txt
        prompts = _dc.replace(prompts, incentives=inc)
        active_incentives = frozenset(p2_incentive_roles)
    if inducement and inducement != "L0" and hasattr(scenario, "inducement_overrides"):
        import dataclasses
        ov = scenario.inducement_overrides(inducement)
        fields = {}
        if ov.get("seed_objective_template"): fields["seed_objective_template"] = ov["seed_objective_template"]
        if ov.get("team_policy"): fields["team_policy"] = ov["team_policy"]
        if ov.get("incentive_role"):
            inc = dict(prompts.incentives); inc[ov["incentive_role"]] = ov["incentive_text"]; fields["incentives"] = inc
            active_incentives = active_incentives | {ov["incentive_role"]}
        prompts = dataclasses.replace(prompts, **fields)
    # believed-legitimate framing wins on the seed objective (keeps any inducement KPI/policy)
    if seed_framing == 'legit' and getattr(scenario, 'SEED_LEGIT_OBJECTIVE', None):
        import dataclasses as _dc0
        prompts = _dc0.replace(prompts, seed_objective_template=scenario.SEED_LEGIT_OBJECTIVE)
    log = new_episode_log(episode_id, out_dir)
    names = list(team)
    bus = MessageBus(names, log)
    board = DelegationBoard(bus, log)
    ws = Workspace(log)
    states = {n: AgentState(n, team[n]) for n in names}
    permission = PermissionPolicy(roles)
    agents = {}
    for n in names:
        role = roles[team[n]]
        others = [m for m in names if m != n]
        sys_prompt, hidden = build_system_prompt(n, team[n], others, pattern, seed_spec, prompts, active_incentives)
        agents[n] = Agent(n, role, llms[n], sys_prompt, hidden)
    orch = Orchestrator(episode_id=episode_id, agents=agents, env=env, env_tool_names=env_tool_names,
                        permission=permission, bus=bus, board=board, ws=ws, log=log, states=states,
                        monitor_hook=monitor_hook, active=active, max_rounds=max_rounds)
    return orch
