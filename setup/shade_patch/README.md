# SHADE-Arena local additions

The harness drives all API-backed agents and monitors through OpenRouter by
calling `get_appropriate_llm(model, org="openrouter")`. The pinned upstream
SHADE-Arena commit (`6614e77…`) has no OpenRouter provider, so these files are
applied on top of the fresh clone by `setup/install_deps.sh`:

- `llms/openrouter_llm.py` — the OpenRouter provider (this project's addition).
  SHADE's `llms/__init__.py` auto-imports every module in `llms/`, so dropping
  this file in is enough to register the `"openrouter"` provider. Reads
  `OPENROUTER_API_KEY` from the environment; contains no secrets. Optional:
  set `OPENROUTER_REASONING_LOG` to a JSONL path to capture reasoning streams.

- `shade_arena_local_edits.patch` — two tracked-file edits:
  1. `config.yaml`: adds an `openrouter:` block under `org_model_mapping`
     listing the model ids used in the experiments.
  2. `llms/together_llm.py`: tolerate a transient empty/blocked completion
     (substitute an empty assistant turn) instead of crashing the episode.

Apply manually (if not using install_deps.sh), from `environments/shade_arena`:

    cp <repo>/setup/shade_patch/llms/openrouter_llm.py llms/openrouter_llm.py
    git apply <repo>/setup/shade_patch/shade_arena_local_edits.patch
