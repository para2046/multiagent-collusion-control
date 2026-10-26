"""Modal app: staged live-environment actor-critic for the compliant swarm policy.

Runs the real multi-agent harness on the GPU, with compliant agents driven by a trainable
Qwen+LoRA policy and the seed by a frozen model, then PPO+GAE on the dense step rewards.

Stages (set via `stage`): 'local' (local-history critic) then 'coalition' (coalition-aware critic).

Validate first on a small model:
    .modalenv/bin/modal run rl/modal_rl_live.py --policy-model Qwen/Qwen2.5-0.5B-Instruct --updates 2 --gpu A100-40GB
Then scale to the target (needs a big machine):
    .modalenv/bin/modal run rl/modal_rl_live.py --policy-model Qwen/Qwen3-235B-A22B --updates 50 --gpu "H100:8"
"""
import modal

app = modal.App("collusion-rl-live")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        # training stack
        "torch==2.5.1", "transformers==4.51.3", "peft==0.14.0", "accelerate==1.1.1",
        "bitsandbytes==0.45.0", "hf_transfer==0.1.8",
        "sentencepiece==0.2.0",
        # harness + scenario-env runtime deps (curated from environments/shade_arena/requirements.txt)
        "pydantic==2.10.4", "docstring_parser==0.16", "typing_extensions>=4.12",
        "tenacity==8.5.0", "openapi-pydantic==0.5.1", "pandas==2.2.3", "numpy==1.26.4",
        "python-dateutil==2.9.0", "email-validator==2.2.0", "faker==30.1.0", "pyyaml==6.0.2",
        # the harness imports the full provider+viz stack at module load even though the local-RL
        # path never calls them, so they must be present for import to succeed
        "anthropic==0.42.0", "openai==1.58.1", "tiktoken==0.8.0", "google-generativeai==0.8.3",
        "jsonref==1.1.0", "matplotlib==3.9.2", "seaborn==0.13.2", "rich==13.9.4", "psutil==6.0.0",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    # intermittent "bad marshal data" crashes came from corrupt/raced .pyc bytecode in the image;
    # delete all cached bytecode and disable writing it so imports always use source (robust, no race)
    .run_commands("find /usr/local/lib/python3.11 -name '*.pyc' -delete 2>/dev/null || true")
    # ship ONLY the code the harness needs (avoid volatile dirs like paper_work/runs that change mid-build)
    .add_local_dir("rl", remote_path="/root/proposal/rl", ignore=["**/__pycache__/**"])
    .add_local_dir("multiagent_harness", remote_path="/root/proposal/multiagent_harness", ignore=["**/__pycache__/**"])
    .add_local_dir("scenarios", remote_path="/root/proposal/scenarios", ignore=["**/__pycache__/**"])
    .add_local_dir("alignment", remote_path="/root/proposal/alignment", ignore=["**/__pycache__/**"])
    .add_local_dir("environments/shade_arena", remote_path="/root/proposal/environments/shade_arena",
                   ignore=[".venv/**", "**/__pycache__/**", "**/transcripts*/**", "**/*.zip", "**/logs/**"])
)

vol = modal.Volume.from_name("collusion-rl-ckpts", create_if_missing=True)
hf_cache = modal.Volume.from_name("hf-cache-235b", create_if_missing=True)  # reuse the probe's 235B weights


@app.function(image=image, gpu="H200:6", timeout=60 * 60 * 5,
              volumes={"/ckpt": vol, "/root/.cache/huggingface": hf_cache})
def train(policy_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507", updates: int = 3,
          episodes_per_update: int = 4, stage: str = "local", seed_model: str | None = None,
          max_rounds: int = 6, do_eval: bool = True, quant: str = "bf16", eval_baseline: bool = False,
          seed_api_model: str = "qwen/qwen3-235b-a22b-2507", openrouter_key: str = "",
          infer_only: bool = False, infer_episodes: int = 4):
    import sys, tempfile, asyncio, torch, torch.nn as nn
    sys.path.insert(0, "/root/proposal")
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    import scenarios.bank_transfer as A
    from rl.live_rl import LocalPolicyLLM, RolloutBuffer, rollout_episode, ppo_update
    # CURRICULUM = Task A (bank) P1 ONLY, per scope decision 2026-10-24. The two attack-productive
    # bank seed placements from the Qwen 6/30 sweep (believed-legit framing, inducement L2): the
    # seed role is the step that enables the unauthorized payment, so the frozen seed really
    # attempts recruitment and the reward verifier emits signal on the compliant agents:
    #   bank A: reviewer (3/5) + executor (2/5).  (api-key B excluded: RL is bank-P1 only.)
    SCENARIOS = [(A, "reviewer"), (A, "executor")]

    def make_llms(scn, seed_role, buf, trainable_compliant):
        """Build the per-agent LLM map: compliant agents on the (maybe trainable) LoRA policy,
        the seed on the SAME base model with adapters disabled (frozen, one 8B copy total)."""
        team = dict(scn.DEFAULT_TEAM)
        spec = dict(scn.SEED_SPEC_P1); spec["role"] = seed_role
        seed_name = next(n for n in team if team[n] == seed_role)
        llms = {n: LocalPolicyLLM(policy_model, policy, tok, buf, n, team[n],
                                  trainable=(trainable_compliant and team[n] != seed_role), device=dev)
                for n in team if team[n] != seed_role}
        if seed_api_model:
            # frozen seed via full-precision OpenRouter API (OFF the GPU): a stronger, eval-matched
            # recruiter; only the trainable compliant agents occupy the GPU LoRA policy.
            from utils.llm_utils import get_appropriate_llm
            llms[seed_name] = get_appropriate_llm(seed_api_model, org="openrouter", temperature=0.7)
        else:
            llms[seed_name] = LocalPolicyLLM(policy_model, policy, tok, buf, seed_name, team[seed_name],
                                             trainable=False, device=dev, disable_adapter=True)
        return team, spec, llms

    def evaluate(n_per_config=1, tag=""):
        """Run the current policy against each collusion config (no grad) and report the rate at
        which the seed still realises the unauthorized outcome -- the headline 'performance'."""
        import tempfile as _tmp
        rows, leaks = [], 0
        for scn, seed_role in SCENARIOS:
            for _ in range(n_per_config):
                evbuf = RolloutBuffer()
                team, spec, llms = make_llms(scn, seed_role, evbuf, trainable_compliant=False)
                _, outcome = asyncio.run(rollout_episode(scn, team=team, policy_llms=llms, seed_spec=spec,
                                         buffer=evbuf, out_dir=_tmp.mkdtemp(), pattern="P1",
                                         inducement="L2", seed_framing="legit", max_rounds=max_rounds))
                hit = bool(outcome.get("leaked"))
                leaks += int(hit)
                rows.append((scn.__name__.split('.')[-1], seed_role, hit))
        rate = leaks / max(1, len(rows))
        print(f"[EVAL {tag}] collusion {leaks}/{len(rows)} = {rate:.2f} | " +
              " ".join(f"{s}:{r}:{'LEAK' if h else 'ok'}" for s, r, h in rows))
        return rate, rows

    dev = "cuda"
    tok = AutoTokenizer.from_pretrained(policy_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    import os
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    if openrouter_key:
        os.environ["OPENROUTER_API_KEY"] = openrouter_key  # for the full-precision API seed
    # Precision is switchable. Default bf16 FULL precision (for H200:4, ~470GB weights fit in 564GB):
    # the compliant agents then match the full-precision API seed -- no quantization degradation.
    # 8bit = int8 (fits H200:4 comfortably, minor degradation); 4bit = NF4 (fits H100:4 but degrades
    # the compliant agents -- this was the stalling/zero-signal cause). The frozen seed SHARES this base.
    if quant in ("4bit", "8bit"):
        if quant == "4bit":
            bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                     bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
        else:
            bnb = BitsAndBytesConfig(load_in_8bit=True)
        base = AutoModelForCausalLM.from_pretrained(policy_model, quantization_config=bnb,
                                                    torch_dtype=torch.bfloat16, device_map="auto")
        base.config.use_cache = False
        base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=True)
    else:  # bf16 full precision
        base = AutoModelForCausalLM.from_pretrained(policy_model, torch_dtype=torch.bfloat16,
                                                    device_map="auto")
        base.config.use_cache = False
        base.gradient_checkpointing_enable()
        base.enable_input_require_grads()   # so grad-checkpointing backprops into the LoRA adapters
    policy = get_peft_model(base, LoraConfig(r=16, lora_alpha=32, target_modules=["q_proj", "v_proj"],
                                             task_type="CAUSAL_LM"))
    dev = str(base.get_input_embeddings().weight.device)  # inputs -> embedding device; accelerate dispatches rest
    print(f"[train] {torch.cuda.device_count()} GPUs | input device {dev}", flush=True)
    policy.print_trainable_parameters()

    # INFERENCE-ONLY fallback (--infer-only): no PPO, no critic, no grad. Just run the 235B through
    # the harness like the local/API experiments and watch behaviour. rollout_episode (via evaluate,
    # trainable_compliant=False) still prints every [step]/[act] action and the [episode reward] line,
    # so this yields clean behavioural traces + collusion outcome with none of the training machinery.
    if infer_only:
        print(f"[INFER-ONLY] no training; {infer_episodes} inference episode(s) per collusion config", flush=True)
        rate, rows = evaluate(n_per_config=infer_episodes, tag="infer")
        print(f"[INFER-ONLY DONE] collusion-rate {rate:.2f} over {len(rows)} episodes")
        return {"mode": "infer_only", "collusion_rate": rate, "rows": rows}
    # the frozen seed SHARES the policy's base model, run with LoRA adapters disabled (no 2nd 8B copy)

    # staged critic: a small value head on top of the backbone hidden state
    hidden = policy.config.hidden_size
    class Critic(nn.Module):
        def __init__(self):
            super().__init__(); self.v = nn.Sequential(nn.Linear(hidden, hidden), nn.Tanh(), nn.Linear(hidden, 1)).to(dev).bfloat16()
        def forward(self, ids):
            with torch.no_grad():
                h = base.model(ids.unsqueeze(0).to(dev)).last_hidden_state[0, -1]
            return self.v(h.to(dev).bfloat16())  # h may land on a later shard under device_map=auto
    critic = Critic()
    opt = torch.optim.AdamW(list(policy.parameters()) + list(critic.parameters()), lr=3e-6)  # lowered for PPO stability (was 1e-5)

    # PRE-TRAINING eval: collusion rate of the (untrained) policy against the 6/30 configs
    # Optional t=0 baseline (default off): otherwise all episodes go to training, with one eval after
    # EACH update -> a learning-curve trajectory (eval@u0, eval@u1, ...) instead of a single pre/post.
    pre_rate, pre_rows = (evaluate(n_per_config=1, tag="pre") if (do_eval and eval_baseline) else (None, []))

    hist = []
    for u in range(updates):
        # accumulate a batch of live episodes (cycling the collusion curriculum) into one buffer
        batch = RolloutBuffer()
        u_leaks = 0
        for i in range(episodes_per_update):
            scn, seed_role = SCENARIOS[i % len(SCENARIOS)]
            ep_buf = RolloutBuffer()   # per-episode buffer: rollout_episode.clear() is scoped to it, and
            team, spec, llms = make_llms(scn, seed_role, ep_buf, trainable_compliant=True)   # its rewards
            _, outcome = asyncio.run(rollout_episode(scn, team=team, policy_llms=llms, seed_spec=spec,
                                     buffer=ep_buf, out_dir=tempfile.mkdtemp(), pattern="P1",
                                     inducement="L2", seed_framing="legit", max_rounds=max_rounds))
            batch.turns.extend(ep_buf.turns)   # ...are attributed per-episode; accumulate ALL into the batch
            u_leaks += int(bool(outcome.get("leaked")))
            print(f"  [u{u} ep{i}] {scn.__name__.split('.')[-1]} seed={seed_role} "
                  f"leak={bool(outcome.get('leaked'))} turns_so_far={len(batch.turns)}")
        stats = ppo_update(policy, critic, opt, batch, stage=stage, device=dev)
        stats["rollout_leaks"] = u_leaks
        # one eval at the END of this batch (fixed config) -> trajectory point for "does RL help?"
        if do_eval:
            ev_rate, ev_rows = evaluate(n_per_config=1, tag=f"u{u}")
            stats["eval_rate"] = ev_rate; stats["eval_rows"] = ev_rows
        hist.append(stats); print(f"[update {u}] stage={stage} {stats}")
        import gc; gc.collect(); torch.cuda.empty_cache()

    # the post-training point is the last update's eval (no separate post pass needed)
    post_rate = hist[-1].get("eval_rate") if hist else None
    post_rows = hist[-1].get("eval_rows", []) if hist else []
    policy.save_pretrained(f"/ckpt/{policy_model.split('/')[-1]}-{stage}")
    vol.commit()
    if do_eval:
        traj = " -> ".join(f"u{i}:{h['eval_rate']:.2f}" for i, h in enumerate(hist) if h.get("eval_rate") is not None)
        base = f"baseline:{pre_rate:.2f} -> " if pre_rate is not None else ""
        print(f"[PERFORMANCE] collusion-rate trajectory: {base}{traj}")
    return {"updates": updates, "stage": stage, "history": hist,
            "pre_rate": pre_rate, "post_rate": post_rate,
            "pre_rows": pre_rows, "post_rows": post_rows}


@app.local_entrypoint()
def main(policy_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507", updates: int = 3,
         episodes_per_update: int = 4, stage: str = "local", max_rounds: int = 6,
         do_eval: bool = True, quant: str = "bf16", eval_baseline: bool = False,
         seed_api_model: str = "qwen/qwen3-235b-a22b-2507",
         infer_only: bool = False, infer_episodes: int = 4):
    import os as _os
    key = _os.environ.get("OPENROUTER_API_KEY", "")
    assert key, "OPENROUTER_API_KEY not in local env -- source or.env before launching (needed for the API seed)"
    print(train.remote(policy_model=policy_model, updates=updates,
                       episodes_per_update=episodes_per_update, stage=stage,
                       max_rounds=max_rounds, do_eval=do_eval, quant=quant, eval_baseline=eval_baseline,
                       seed_api_model=seed_api_model, openrouter_key=key,
                       infer_only=infer_only, infer_episodes=infer_episodes))
