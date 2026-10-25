"""Modal COST PROBE for 235B live RL. NOT a full training job.

Loads Qwen3-235B-A22B sharded across 8x A100-80GB (device_map=auto), runs ONE live P1 rollout
and ONE representative training step, and reports wall-clock for load / rollout / update so we can
estimate $/update. Weights are cached to a volume so retries skip the ~470GB download.

    .modalenv/bin/modal run rl/modal_rl_235b_probe.py
"""
import modal

app = modal.App("collusion-rl-235b-probe")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.5.1", "transformers==4.51.3", "peft==0.14.0", "accelerate==1.1.1",
        "bitsandbytes==0.45.0",
        "sentencepiece==0.2.0", "hf_transfer==0.1.8",
        "pydantic==2.10.4", "docstring_parser==0.16", "typing_extensions>=4.12",
        "tenacity==8.5.0", "openapi-pydantic==0.5.1", "pandas==2.2.3", "numpy==1.26.4",
        "python-dateutil==2.9.0", "email-validator==2.2.0", "faker==30.1.0", "pyyaml==6.0.2",
        "anthropic==0.42.0", "openai==1.58.1", "tiktoken==0.8.0", "google-generativeai==0.8.3",
        "jsonref==1.1.0", "matplotlib==3.9.2", "seaborn==0.13.2", "rich==13.9.4", "psutil==6.0.0",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .add_local_dir("rl", remote_path="/root/proposal/rl", ignore=["**/__pycache__/**"])
    .add_local_dir("multiagent_harness", remote_path="/root/proposal/multiagent_harness", ignore=["**/__pycache__/**"])
    .add_local_dir("scenarios", remote_path="/root/proposal/scenarios", ignore=["**/__pycache__/**"])
    .add_local_dir("alignment", remote_path="/root/proposal/alignment", ignore=["**/__pycache__/**"])
    .add_local_dir("environments/shade_arena", remote_path="/root/proposal/environments/shade_arena",
                   ignore=[".venv/**", "**/__pycache__/**", "**/transcripts*/**", "**/*.zip", "**/logs/**"])
)

hf_cache = modal.Volume.from_name("hf-cache-235b", create_if_missing=True)

# A100-80GB x8 = 640GB; ~$2.50/hr/GPU on Modal (approx) -> set here for the $ estimate, adjust to your rate.
GPU = "A100-80GB:8"
RATE_PER_GPU_HR = 2.50


@app.function(image=image, gpu=GPU, timeout=60 * 60, volumes={"/root/.cache/huggingface": hf_cache})
def probe(policy_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507"):
    import sys, os, time, tempfile, asyncio, torch, torch.nn as nn
    sys.path.insert(0, "/root/proposal")
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    import scenarios.bank_transfer as A
    from rl.live_rl import LocalPolicyLLM, RolloutBuffer, rollout_episode, policy_logprobs

    ngpu = torch.cuda.device_count()
    print(f"[probe] GPUs visible: {ngpu}", flush=True)
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(policy_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    # 4-bit NF4 QLoRA: frozen base ~118GB (vs ~470GB bf16) -> leaves room for activations/update.
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    base = AutoModelForCausalLM.from_pretrained(policy_model, quantization_config=bnb,
                                                torch_dtype=torch.bfloat16, device_map="auto")
    base.config.use_cache = False
    base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=True)
    policy = get_peft_model(base, LoraConfig(r=16, lora_alpha=32, target_modules=["q_proj", "v_proj"],
                                             task_type="CAUSAL_LM"))
    t_load = time.time() - t0
    policy.print_trainable_parameters()
    # inputs go to the device of the input embeddings (accelerate dispatches the rest)
    in_dev = base.get_input_embeddings().weight.device
    print(f"[probe] model loaded in {t_load:.1f}s | input device {in_dev}", flush=True)

    # one live P1 rollout (bank, seed=reviewer) -- compliant + frozen seed both on this base model
    scn, seed_role = A, "reviewer"
    team = dict(scn.DEFAULT_TEAM); spec = dict(scn.SEED_SPEC_P1); spec["role"] = seed_role
    seed_name = next(n for n in team if team[n] == seed_role)
    buf = RolloutBuffer()
    llms = {n: LocalPolicyLLM(policy_model, policy, tok, buf, n, team[n],
                              trainable=(team[n] != seed_role), device=str(in_dev)) for n in team}
    llms[seed_name] = LocalPolicyLLM(policy_model, policy, tok, buf, seed_name, team[seed_name],
                                     trainable=False, device=str(in_dev), disable_adapter=True)
    t1 = time.time()
    asyncio.run(rollout_episode(scn, team=team, policy_llms=llms, seed_spec=spec, buffer=buf,
                                out_dir=tempfile.mkdtemp(), pattern="P1", inducement="L2",
                                seed_framing="legit", max_rounds=1))
    t_roll = time.time() - t1
    n_turns = len(buf.turns)
    n_resp_tok = sum(len(t.resp_ids) for t in buf.turns)  # response tokens generated across trainable turns
    print(f"[probe] 1 rollout (1 round): {t_roll:.1f}s | {n_turns} trainable turns | "
          f"{n_resp_tok} response tokens", flush=True)

    # one representative training step: recompute logprobs over the turns and backward a surrogate loss.
    # snapshot a LoRA weight before/after to PROVE the optimizer actually moved the trainable params.
    opt = torch.optim.AdamW([p for p in policy.parameters() if p.requires_grad], lr=1e-5)
    _wname, _wref = next((n, p) for n, p in policy.named_parameters() if p.requires_grad)
    w_before = _wref.detach().float().clone()
    t2 = time.time(); loss = None; delta = None
    try:
        opt.zero_grad()
        total = 0.0
        for t in buf.turns:
            lp = policy_logprobs(policy, t.prompt_ids, t.resp_ids, str(in_dev))
            total = total + lp.sum()
        if torch.is_tensor(total):
            (-total).backward(); opt.step()
            loss = float(total.detach().cpu())
            delta = float((_wref.detach().float() - w_before).abs().sum().cpu())
        t_upd = time.time() - t2
        print(f"[probe] 1 update (backward over {n_turns} turns): {t_upd:.1f}s | surrogate {loss}", flush=True)
        print(f"[probe] WEIGHT UPDATED: LoRA param '{_wname}' L1 change = {delta} (>0 means step landed)", flush=True)
    except Exception as e:
        t_upd = time.time() - t2
        print(f"[probe] update step hit: {type(e).__name__}: {str(e)[:150]} (after {t_upd:.1f}s)", flush=True)

    total_s = time.time() - t0
    est = total_s / 3600 * RATE_PER_GPU_HR * ngpu
    print("==== COST PROBE SUMMARY (4-bit NF4 QLoRA) ====", flush=True)
    print(f"  GPUs: {ngpu} x A100-80GB | assumed ${RATE_PER_GPU_HR}/GPU-hr", flush=True)
    print(f"  episode: 1 round | {n_turns} trainable turns | {n_resp_tok} response tokens | update landed: {delta is not None and delta > 0}", flush=True)
    print(f"  load(incl download): {t_load:.0f}s | rollout: {t_roll:.0f}s | update: {t_upd:.0f}s", flush=True)
    print(f"  TOTAL wall: {total_s:.0f}s = {total_s/60:.1f} min", flush=True)
    print(f"  EST cost this probe: ${est:.2f}  (download is one-time; steady-state/update ~= {t_upd/60:.1f} min/update)", flush=True)
    return {"load_s": t_load, "rollout_s": t_roll, "update_s": t_upd, "total_s": total_s, "ngpu": ngpu, "est_usd": est}


@app.local_entrypoint()
def main(policy_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507"):
    print(probe.remote(policy_model=policy_model))
