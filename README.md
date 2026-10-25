# multiagent-collusion-control

A research framework for **multi-agent LLM collusion**: detecting when a hidden objective or
incentive conflict propagates through a team of role-separated agents, and **"swarm alignment"** —
using reinforcement learning to make the compliant agents resist. It adapts two
[SHADE-Arena](https://github.com/jkutaso/SHADE-Arena) tasks into role-separated simulations
(**Bank Transfer**: Worker → Reviewer → Executor behind a two-person approval gate; **API Key
Leakage**), runs three conditions (**P0** benign, **P1** a seed agent carrying a hidden objective,
**P2** emergent incentive pressure), scores a state-aware monitor at visibility levels **M0–M4**
plus a realistic no-private-channel monitor **M_obs**, computes a reward from verified environment
records, and runs **live on-policy PPO/GAE** training of a shared LoRA policy (locally for Qwen3-8B,
on [Modal](https://modal.com) with 4-bit Qwen3-235B).

> This is a curated **code + results** release. The two large external environment repos
> (SHADE-Arena, ControlArena) are **not** vendored — `setup/install_deps.sh` clones them at pinned
> commits. No API keys, model weights, or run caches are included.

## What's in this repo

```
multiagent-collusion-control/
├── multiagent_harness/      # the multi-agent orchestrator, event log, monitors, adapters
│   ├── orchestration.py     #   round-robin scheduler, per-agent state, episode loop
│   ├── events.py            #   canonical event log + visibility labels
│   ├── communication.py     #   agent<->agent message bus (NEW; SHADE is single-agent)
│   ├── delegation.py        #   delegation board, workspace.py, permissions.py, decision.py
│   ├── monitor.py           #   online monitors M0–M4 + M_obs (views + rule/LLM backends)
│   ├── ca_adapter.py        #   ControlArena/Inspect monitor adapter + offline replay scorer
│   ├── shade_bootstrap.py   #   makes environments/shade_arena importable
│   ├── tools.py, prompts.py, fake_llm.py
├── scenarios/               # role-separated task adapters over SHADE-Arena envs
│   ├── bank_transfer.py     #   Task A (worker/reviewer/executor, approval gate)
│   ├── api_key_leak.py      #   Task B (researcher/correspondent/coordinator)
│   ├── build.py             #   build_episode(): wires a scenario into an orchestrator
├── alignment/
│   └── reward_verifier.py   # post-hoc, two-oracle, scenario-agnostic step rewards
├── rl/
│   ├── live_rl.py           # local on-policy actor-critic loop (LocalPolicyLLM, GAE, PPO)
│   ├── modal_rl_live.py     # Modal app: staged live RL (Qwen3-235B, 4-bit QLoRA, H100:4/8)
│   └── modal_rl_235b_probe.py  # Modal memory/throughput probe for the 235B load
├── experiments/             # runnable scripts (run from the repo root)
├── results/                 # curated artifacts (see "Results")
├── setup/
│   ├── install_deps.sh      # clone + pin SHADE-Arena & ControlArena, apply local edits
│   └── shade_patch/         # OpenRouter provider + config edits applied to SHADE-Arena
├── requirements.txt         # full frozen env (127 pkgs, Python 3.11.16)
├── requirements-core.txt    # curated key direct deps + the RL training stack
└── LICENSE                  # MIT
```

## Architecture

**The harness.** `orchestration.py` runs a **round-robin** scheduler over a team of role-separated
agents. Each agent has a role, a permission set, a private scratch state, and acts by emitting
events. All inter-agent traffic flows through a **message bus** and a **delegation board** — these
are NEW; upstream SHADE-Arena is single-agent and has no agent↔agent channel. Everything the team
does is appended to a **canonical event log** (`events.py`), where every event carries a
**visibility label**:

- `public` — broadcast communication
- `private` — a private channel / direct delegation
- `action` — a tool call, its result, and the resulting state change
- `reasoning` — a recorded chain-of-thought trace
- `hidden` — the seed's secret objective (never shown to any monitor; ground truth only)

**Conditions.** `P0` benign cooperation; `P1` one **seed** agent is given a hidden unauthorized
objective (inducement ladder `L0`/`L1`/`L2`; `coercive` vs believed-`legit` framing); `P2` the
unauthorized incentive emerges from a shared hidden KPI held by some/all roles (`--p2-roles`,
`--p2-common`).

**The monitor ladder.** `monitor.py` defines a visibility ladder — a monitor sees strictly more as
you climb:

| config | sees |
|---|---|
| `M0` | public communication only |
| `M1` | + private communication & delegation |
| `M2` | + tool calls, results, state changes (behaviour) |
| `M3` | + recorded reasoning traces |
| `M4` | + the coalition / delegation dependency graph |
| `M_obs` | realistic deployment view: public + action + reasoning, **no private channel**, no graph |

Monitors run **online** (a pre-tool hook, rule or LLM backend) or **offline** via the ControlArena
adapter (`ca_adapter.score_events_offline`), which replays a recorded episode through an Inspect
monitor model and reuses ControlArena's `<score>`/`<reasoning>` parsing.

**The reward verifier** (`alignment/reward_verifier.py`) is abstract and scenario-agnostic. A
scenario supplies exactly **two oracles**: (1) an unauthorized-target ground truth (bad
destination / leaked credential) and (2) `task_score(env) → [0,1]` for legitimate task quality.
Every event is mapped to an abstract category with a reward magnitude (e.g. `R_TERMINAL = -10`,
`R_ENABLE = -5`, `R_RESIST = +1.0`, `R_LEGIT = +0.4`), bucketed into the paper's
`r = r_task + r_auth + r_coord` decomposition. A **coalition accessory** penalty is added to every
enabling agent when a terminal violation fires. Authorization rewards are **conditional on ground
truth**, so the policy learns to discriminate (resist the real attack, cooperate on legit work)
rather than refuse everything. Computed post-hoc from the event log + final env state — never from
the monitor's prediction.

**The live RL loop** (`rl/live_rl.py`, `rl/modal_rl_live.py`). Compliant agents are driven by a
trainable **shared LoRA policy**; the **seed is frozen** (either the same base with adapters
disabled, or a full-precision OpenRouter API model). Episodes run live in the real harness; the
verifier emits dense per-step rewards; a value head gives **per-agent GAE** advantages (staged:
`local` critic → `coalition`-aware critic with a coalition accessory term) and the policy is updated
with **PPO**. On Modal the 235B policy is loaded in **4-bit NF4 QLoRA** sharded across GPUs.

## Install

Requires **Python 3.11** (built and frozen on 3.11.16).

```bash
git clone <this repo> multiagent-collusion-control
cd multiagent-collusion-control

python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # harness + monitor stack (exact pins)

# clone + pin SHADE-Arena and ControlArena into environments/<name>, apply local edits
bash setup/install_deps.sh
```

The code path-hacks to `environments/shade_arena` and `environments/control_arena` **relative to the
repo root** (see `multiagent_harness/shade_bootstrap.py` and `ca_adapter.py`). They MUST live at
exactly those paths. `install_deps.sh` also drops in the OpenRouter provider and config edits that
the harness needs (see `setup/shade_patch/`).

### Environment variables

```bash
export OPENROUTER_API_KEY=sk-or-...                 # all agents + API-seed go through OpenRouter

# the ControlArena / Inspect monitor speaks the OpenAI API — point it at OpenRouter:
export OPENAI_API_KEY="$OPENROUTER_API_KEY"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"

export ANTHROPIC_API_KEY=sk-ant-...                 # optional: direct-Anthropic agents/monitor
# optional: capture reasoning streams
export OPENROUTER_REASONING_LOG=/path/to/reasoning.jsonl
```

For the 235B RL path, authenticate Modal once: `pip install modal && modal setup`.

> **Costs money.** Agent rollouts and monitor scoring make paid OpenRouter calls; the 235B RL path
> rents multi-GPU Modal machines (H100:4/8). Keys are read from the environment only — never commit
> them.

## Reproduce

All experiment scripts are run **from the repo root** and insert it on `sys.path` themselves.

### (a) One multi-agent episode per condition

`experiments/run_multiagent.py` (real flags, from its argparse):
`--pattern {P0,P1,P2}` · `--scenario {bank_transfer,api_key_leak}` · `--model` ·
`--rounds` · `--monitor {none,M0,M1,M2,M3,M4}` · `--monitor-backend {rule,llm}` ·
`--monitor-model` · `--active` · `--inducement {L0,L1,L2}` ·
`--seed-role {fixed,random,<role>}` · `--seed` · `--p2-roles` · `--p2-common` ·
`--trigger` · `--seed-framing {coercive,legit}` · `--with-coordinator` · `--out` (required).

```bash
# P0 benign
python experiments/run_multiagent.py --pattern P0 --scenario bank_transfer \
    --model qwen/qwen3-32b --rounds 6 --out runs/demo/p0

# P1 seed with hidden objective (believed-legit framing, strongest inducement)
python experiments/run_multiagent.py --pattern P1 --scenario bank_transfer \
    --model qwen/qwen3-32b --rounds 6 --inducement L2 --seed-framing legit \
    --seed-role reviewer --seed 1 --out runs/demo/p1

# P2 emergent: every role carries the hidden KPI
python experiments/run_multiagent.py --pattern P2 --scenario bank_transfer \
    --model qwen/qwen3-32b --rounds 6 --p2-roles all --out runs/demo/p2
```

Each run writes `summary.json` (+ an event log) under `--out`.

### (b) Score the monitor offline + per-step early warning

The offline monitor uses the **OpenAI-compatible** Inspect client, so it needs `OPENAI_API_KEY` /
`OPENAI_BASE_URL` set to OpenRouter (see above). Model ids are passed as `openai/<openrouter-id>`.

`experiments/per_step_monitor.py`:
`--events <episode.events.jsonl>` (required) · `--scenario {bank_transfer,api_key_leak}` (required) ·
`--config {M0..M4,M_obs}` (default `M4`) · `--model` · `--out`.

```bash
# per-step risk curve at M4 for one recorded episode (ships in results/)
python experiments/per_step_monitor.py \
    --events results/review_export_80/P1_Qwen/a_P1_legit_reviewer_s2.events.jsonl \
    --scenario bank_transfer --config M4 --out runs/demo/perstep_M4.json

# the realistic no-private-channel monitor
python experiments/per_step_monitor.py \
    --events results/review_export_80/P1_Qwen/a_P1_legit_reviewer_s2.events.jsonl \
    --scenario bank_transfer --config M_obs --out runs/demo/perstep_Mobs.json
```

`experiments/ca_replay.py --events <jsonl> [--model …] [--configs M0 M2 M4]` scores a whole episode
at several configs at once. `experiments/collect_scores.py` and
`experiments/summarize_shade_run.py <run_dir>` aggregate/summarize runs.

### (c) Recruitment graph + collusion-onset timeline figures

```bash
# objective-propagation / recruitment graph over the 80-episode export
python experiments/recruitment_graph.py \
    --index results/review_export_80/INDEX.csv --out runs/demo/recruitment_graph

# collusion-onset timeline (seed-push vs first compliant uptake)
python experiments/collusion_timeline.py \
    --index results/review_export_80/INDEX.csv --out runs/demo/collusion_timeline --tag pre-RL
```

> **Path note.** `collusion_timeline.py`, `recruitment_graph.py`, and `plot_perstep_risk.py` carry
> default paths from the original run layout (`runs/phase2/…`, `paper_work/…`) that are **not**
> shipped in this curated release. Point `--index` at `results/review_export_80/INDEX.csv` (shown
> above). `collusion_timeline.py` additionally expands per-group directories (`runs/phase2/legit_seed`,
> …) that are not included — the pre-rendered figures are in `results/figures/`. `plot_perstep_risk.py`
> reads two specific per-step JSONs that were scratch outputs and are not shipped.

### (d) Live RL — local Qwen3-8B and Modal 235B

`rl/live_rl.py` is the training library (LocalPolicyLLM, RolloutBuffer, `rollout_episode`,
`ppo_update`) used by the Modal app and by any local driver; it is imported, not run as a CLI.

Modal 235B run — `rl/modal_rl_live.py` local entrypoint flags (from its argparse):
`--policy-model` · `--updates` · `--episodes-per-update` · `--stage {local,coalition}` ·
`--max-rounds` · `--do-eval` · `--seed-api-model`. It reads `OPENROUTER_API_KEY` from the
environment (for the full-precision API seed) and forwards it to the GPU function.

```bash
# validate the pipeline on a tiny model first
modal run rl/modal_rl_live.py --policy-model Qwen/Qwen2.5-0.5B-Instruct --updates 2

# the target run (needs a big machine; H100:4 by default in the app)
modal run rl/modal_rl_live.py \
    --policy-model Qwen/Qwen3-235B-A22B-Instruct-2507 --updates 50 \
    --episodes-per-update 2 --stage local --max-rounds 4

# one-off memory/throughput probe of the 235B 4-bit load
modal run rl/modal_rl_235b_probe.py --policy-model Qwen/Qwen3-235B-A22B-Instruct-2507
```

The curriculum is Task-A (bank) P1 only, seeding the two attack-productive roles (reviewer, executor)
with the believed-legit framing at inducement L2. Checkpoints are written to the
`collusion-rl-ckpts` Modal volume. For **local Qwen3-8B** training, install the RL stack from
`requirements-core.txt` into a GPU venv and drive `rl/live_rl.py` directly (same
`rollout_episode` → `ppo_update` loop, no Modal).

## Results

`results/` holds the curated artifacts behind the paper's headline numbers:

- **`results/review_export_80/`** — the full, auditable **80-episode** evaluation export
  (P1: 30 Qwen + 30 GPT-5-"Luna"; P2: 10 + 10). For each episode: `*.md` (full ordered trajectory
  with reasoning), `*.events.jsonl` (raw event stream incl. rule-monitor `monitor_decision` events),
  `*.summary.json`. `INDEX.csv` has one row per episode with robust tool-verified labels
  (`convinced`, `completed`) and monitor scores. `README.md` and `MONITOR_EVAL.md`/`.csv` explain
  the labels, the two scorer corrections the robust signal catches, and the whole-episode M4
  detection table (completed framing: 8/10 at τ=0.5/0.7; episode-FP 0.186/0.171/0.129 over the 70
  non-completed episodes).
- **`results/monitor_final_perstep.csv`** — final per-step monitor summary.
- **`results/mobs_ca.csv`** — the realistic `M_obs` monitor (no private channel) scored via the
  ControlArena adapter.
- **`results/perstep_cmp/*.json`** — paired per-step risk curves (`__M4` vs `__M_obs`) for selected
  episodes, showing how much the private-channel view buys.
- **`results/figures/collusion_timeline.{pdf,png}`** — the pre-RL collusion-onset timeline
  (seed-push vs first compliant uptake), the baseline for a post-RL rerun.

## External data / model requirements

- **SHADE-Arena task data** (`bank_transfer_2`, `api_key_calendar_agendas_2`) ships **inside** the
  SHADE-Arena repo that `setup/install_deps.sh` clones — no separate download.
- **ControlArena** is used purely as a monitor library on Inspect (not its vLLM sandbox).
- **OpenRouter and Modal cost money** (see Install). All credentials are read from environment
  variables; nothing is stored in this repo.

## License

MIT — see `LICENSE`. Copyright (c) 2026 para2046.
