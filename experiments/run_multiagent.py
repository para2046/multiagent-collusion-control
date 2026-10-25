"""Run one multi-agent episode. Pattern P0/P1/P2, team of OpenRouter models.

usage: run_multiagent.py --pattern P0 --scenario bank_transfer --model qwen/qwen3-32b \
       --rounds 6 --out runs/phase2/<name>
Reads OPENROUTER_API_KEY from the environment (source experiments/env.sh first).
Shadow mode only here; monitors are added in phase 3.
"""
import argparse
import asyncio
import importlib
import json
from pathlib import Path

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from multiagent_harness.shade_bootstrap import bootstrap

bootstrap()
from utils.llm_utils import get_appropriate_llm  # noqa: E402
from scenarios.build import build_episode  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="P0", choices=["P0", "P1", "P2"])
    ap.add_argument("--scenario", default="bank_transfer")
    ap.add_argument("--model", default="qwen/qwen3-32b")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--monitor", default="none", choices=["none", "M0", "M1", "M2", "M3", "M4"])
    ap.add_argument("--monitor-backend", default="rule", choices=["rule", "llm"])
    ap.add_argument("--monitor-model", default="openai/gpt-4.1-mini")
    ap.add_argument("--active", action="store_true", help="active mode: Alert blocks the call")
    ap.add_argument("--inducement", default="L0", choices=["L0", "L1", "L2"])
    ap.add_argument("--seed-role", default="fixed", help="'fixed' (scenario default), 'random', or a role name")
    ap.add_argument("--seed", type=int, default=0, help="reproducibility seed (random seed-role + logged)")
    ap.add_argument("--p2-roles", default="", help="P2: comma-separated roles that get the hidden KPI, or 'all'")
    ap.add_argument("--p2-common", action="store_true", help="P2: KPI is common knowledge (everyone knows everyone has it)")
    ap.add_argument("--trigger", action="store_true", help="inject the ambiguous external trigger email (untrusted data)")
    ap.add_argument("--seed-framing", default="coercive", choices=["coercive","legit"], help="legit = believed-authorized task, no stealth")
    ap.add_argument("--with-coordinator", action="store_true", help="add the coordinator as a 4th agent (enables larger cliques)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    scenario = importlib.import_module(f"scenarios.{args.scenario}")
    team = dict(scenario.DEFAULT_TEAM)
    if args.with_coordinator and "agent_coordinator" not in team and "coordinator" in getattr(scenario, "ROLES", {}):
        team["agent_coordinator"] = "coordinator"
    import random as _random
    seed_spec = dict(getattr(scenario, "SEED_SPEC_P1", {}) or {})
    roles = list(getattr(scenario, "ALL_SEED_ROLES", [seed_spec.get("role")]))
    if args.seed_role == "random":
        seed_spec["role"] = _random.Random(args.seed).choice(roles)
    elif args.seed_role != "fixed":
        seed_spec["role"] = args.seed_role
    chosen_seed_role = seed_spec.get("role")
    p2_roles = None
    if args.p2_roles:
        p2_roles = list({team[n] for n in team}) if args.p2_roles == "all" else [x.strip() for x in args.p2_roles.split(",") if x.strip()]
    monitor_hook = None
    if args.monitor != "none":
        from multiagent_harness.monitor import make_monitor_hook
        mon_llm = get_appropriate_llm(args.monitor_model, org="openrouter", temperature=0.0) if args.monitor_backend == "llm" else None
        monitor_hook = make_monitor_hook(args.monitor, backend=args.monitor_backend, llm=mon_llm)
    llms = {n: get_appropriate_llm(args.model, org="openrouter", temperature=0.7) for n in team}
    orch = build_episode(scenario, episode_id=f"{args.scenario}_{args.pattern}", pattern=args.pattern,
                         team=team, llms=llms, out_dir=args.out, max_rounds=args.rounds,
                         monitor_hook=monitor_hook, active=args.active, inducement=args.inducement, seed_spec=seed_spec,
                         p2_incentive_roles=p2_roles, p2_common_knowledge=args.p2_common, trigger=args.trigger, seed_framing=args.seed_framing)
    log = asyncio.run(orch.run())
    md = [e for e in log.events if e.kind == "monitor_decision"]
    pv = [e.meta.get("p_violation") for e in md if e.meta.get("p_violation") is not None]
    monitor_stats = {"decisions": len(md),
                     "alerts": sum(1 for e in md if e.meta.get("action") == "Alert"),
                     "observes": sum(1 for e in md if e.meta.get("action") == "Observe"),
                     "max_p_violation": max(pv) if pv else None}
    _ofn = getattr(scenario, "outcome_leaked", None) or getattr(scenario, "outcome_unauthorized", None)
    outcome = _ofn(orch.env) if _ofn else None
    summary = {"pattern": args.pattern, "inducement": args.inducement, "seed_role": chosen_seed_role, "seed": args.seed, "p2_roles": p2_roles, "p2_common": args.p2_common, "trigger": args.trigger, "model": args.model, "monitor": args.monitor,
               "monitor_backend": args.monitor_backend, "active": args.active, "rounds": log.round,
               "outcome": outcome, "monitor_stats": monitor_stats,
               "event_counts": log.counts(), "ledger": orch.ws.status_counts(),
               "delegations": orch.board.summary(), "channels": orch.bus.by_channel(),
               "permission_denials": len(orch.permission.denials),
               "escalations": {n: len(s.escalations) for n, s in orch.states.items()}}
    Path(args.out, "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
